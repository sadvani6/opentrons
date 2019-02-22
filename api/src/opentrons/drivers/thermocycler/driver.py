import logging
import os
import threading
from queue import Queue
try:
    import select
except ModuleNotFoundError:
    select = None  # type: ignore
from time import sleep
from typing import Optional
from serial.serialutil import SerialException
from opentrons.drivers import serial_communication
from opentrons.drivers.serial_communication import SerialNoResponse


log = logging.getLogger(__name__)

GCODES = {
    'OPEN_LID': 'M126',
    'CLOSE_LID': 'M127',
    'GET_LID_STATUS': 'M119',
    'SET_LID_TEMP': 'M140',
    'DEACTIVATE_LID_HEATING': 'M108',
    'EDIT_PID_PARAMS': 'M301',
    'SET_PLATE_TEMP': 'M104 S',
    'GET_PLATE_TEMP': 'M105',
    'SET_RAMP_RATE': 'M566',
    'SET_HOLD_TIME': 'H',
    'PAUSE': '',
    'DEACTIVATE': 'M18'
}

TC_BAUDRATE = 115200

SERIAL_ACK = '\r\n'
TC_COMMAND_TERMINATOR = SERIAL_ACK + SERIAL_ACK
TC_ACK = 'ok' + SERIAL_ACK + 'ok' + SERIAL_ACK
ERROR_KEYWORD = 'error'
DEFAULT_TC_TIMEOUT = 1
DEFAULT_COMMAND_RETRIES = 3
DEFAULT_STABILIZE_DELAY = 0.1
POLLING_FREQUENCY_MS = 1000
TEMP_THRESHOLD = 0.5


class ThermocyclerError(Exception):
    pass


class ParseError(Exception):
    pass


class TCPoller(threading.Thread):
    def __init__(self, port, interrupt_callback, status_callback):
        self._port = port
        self._connection = self._connect_to_port()
        self._interrupt_callback = interrupt_callback
        self._status_callback = status_callback
        self._lock = threading.Lock()
        self._command_queue = Queue()

        # Note: the options and order of operations for opening file
        # descriptors is very specific. For more info, see:
        # http://pubs.opengroup.org/onlinepubs/007908799/xsh/open.html
        self._send_path = '/var/run/tc_send_fifo_{}'.format(hash(self))
        os.mkfifo(self._send_path)
        send_read_fd = os.open(
            self._send_path, flags=os.O_RDONLY | os.O_NONBLOCK)
        self._send_read_file = os.fdopen(send_read_fd, 'rb')
        self._send_write_fd = open(self._send_path, 'wb', buffering=0)

        self._halt_path = '/var/run/tc_halt_fifo_{}'.format(hash(self))
        os.mkfifo(self._halt_path)
        halt_read_fd = os.open(
            self._send_path, flags=os.O_RDONLY | os.O_NONBLOCK)
        self._halt_read_file = os.fdopen(halt_read_fd, 'rb')
        self._halt_write_fd = open(self._send_path, 'wb', buffering=0)

        self._poller = select.poll()
        self._poller.register(self._send_read_file, eventmask=select.POLLIN)
        self._poller.register(self._halt_read_file, eventmask=select.POLLIN)
        self._poller.register(self._connection, eventmask=select.POLLIN)

        super().__init__(target=self._serial_poller, name='tc_serial_poller')
        super().start()

    @property
    def port(self):
        return self._port

    def _serial_poller(self):
        while True:
            _next = dict(self._poller.poll(POLLING_FREQUENCY_MS))
            if self._halt_read_file.fileno() in _next:
                self._halt_read_file.read()
                # Note: this is discarded because we send a set message to halt
                # the thread--don't currently need to parse it
                break

            elif self._connection.fileno() in _next:
                # Lid-open interrupt
                res = self._connection.read_until(SERIAL_ACK)
                self._interrupt_callback(res)

            elif self._send_read_file.fileno() in _next:
                self._send_read_file.read(1)
                command, callback = self._command_queue.get()
                res = self._send_command(command)
                callback(res)
            else:
                # Nothing else to do--update device status
                res = self._send_command(GCODES['GET_PLATE_TEMP'])
                self._status_callback(res)

    def _wait_for_ack(self):
        """
        This method writes a sequence of newline characters, which will
        guarantee the device responds with 'ok\r\nok\r\n' within 1 second
        """
        self._send_command(SERIAL_ACK, timeout=DEFAULT_TC_TIMEOUT)

    def _send_command(self, command, timeout=DEFAULT_TC_TIMEOUT):
        command_line = command + ' ' + TC_COMMAND_TERMINATOR
        ret_code = self._recursive_write_and_return(
            command_line, timeout, DEFAULT_COMMAND_RETRIES)
        if ERROR_KEYWORD in ret_code.lower():
            log.error('Received error message from Thermocycler: {}'.format(
                    ret_code))
            raise ThermocyclerError(ret_code)
        return ret_code.strip()

    def _recursive_write_and_return(self, cmd, timeout, retries):
        try:
            return serial_communication.write_and_return(
                cmd, TC_ACK, self._connection, timeout)
        except SerialNoResponse as e:
            retries -= 1
            if retries <= 0:
                raise e
            sleep(DEFAULT_STABILIZE_DELAY)
            if self._connection:
                self._connection.close()
                self._connection.open()
            return self._recursive_write_and_return(
                cmd, timeout, retries)

    def _connect_to_port(self):
        try:
            return serial_communication.connect(port=self._port,
                                                baudrate=TC_BAUDRATE)
        except SerialException:
            raise SerialException("Thermocycler device not found")

    def send(self, command, callback):
        with self._lock:
            self._command_queue.put((command, callback))
            self._send_write_fd.write(b'c')

    def close(self):
        self._halt_write_fd.write(b'q')

    def __del__(self):
        """ Clean up thread fifos"""
        try:
            os.unlink(self._send_path)
        except NameError:
            pass
        try:
            os.unlink(self._halt_path)
        except NameError:
            pass


class Thermocycler:
    def __init__(self, interrupt_callback):
        self._poller = None
        self._update_thread = None
        self._current_temp = None
        self._target_temp = None
        self._ramp_rate = None
        self._hold_time = None
        self._lid_status = None
        self._interrupt_cb = interrupt_callback

    def connect(self, port: str) -> 'Thermocycler':
        self.disconnect()
        self._poller = TCPoller(
            port, self._interrupt_callback, self._temp_status_update_callback)
        _lid_status_res = self._write_and_wait(GCODES['GET_LID_STATUS'])
        self._lid_status = _lid_status_res.split()[-1].lower()
        return self

    def disconnect(self) -> 'Thermocycler':
        if self.is_connected():
            self._poller.close()
            self._poller.join()
        self._poller = None
        return self

    def deactivate(self):
        raise NotImplementedError

    def is_connected(self) -> bool:
        if not self._poller:
            return False
        return self._poller.is_alive()

    def open(self):
        # TODO: add temperature protection for >70C
        if self.target:
            raise ThermocyclerError(
                'Cannot open Thermocycler while it is active')
        self._write_and_wait(GCODES['OPEN_LID'])
        self._lid_status = 'open'

    def close(self):
        self._write_and_wait(GCODES['CLOSE_LID'])
        self._lid_status = 'closed'

    def _temp_status_update_callback(self, temperature_response):
        # Payload is shaped like `T:95.0 C:77.4 H:600` where T is the
        # target temperature, C is the current temperature, and H is the
        # hold time (remaining?)
        val_dict = {}
        data = [d.split(':') for d in temperature_response.split()]
        for datum in data:
            val_dict[datum[0]] = datum[1]

        self._current_temp = val_dict['C']
        self._target_temp = val_dict['T']
        self._hold_time_remaining = val_dict['H']

    def _interrupt_callback(self, interrupt_response):
        # TODO sanitize response and then call the callback
        parsed_response = interrupt_response
        self._interrupt_cb(parsed_response)

    @property
    def temperature(self):
        return self._current_temp

    @property
    def target(self):
        return self._target_temp

    @property
    def hold_time(self):
        return self._hold_time

    @property
    def ramp_rate(self):
        return self._ramp_rate

    @property
    def status(self):
        if self.target is None:
            _status = 'idle'
        elif abs(self.target - self.temperature) < TEMP_THRESHOLD:
            _status = 'holding at target'
        else:
            _status = 'ramping'
        return _status

    @property
    def port(self) -> Optional[str]:
        if not self._poller:
            return None
        return self._poller.port

    @property
    def lid_status(self):
        return self._lid_status

    def get_device_info(self):
        raise NotImplementedError

    def _write_and_wait(self, command):
        ret = None

        def cb(cmd):
            nonlocal ret
            ret = cmd

        self._poller.send(command, cb)

        while None is ret:
            pass
        return ret

    def __del__(self):
        try:
            self._poller.close()
        except Exception:
            log.exception('Exception while cleaning up Thermocycler:')
