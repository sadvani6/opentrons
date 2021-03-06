import logging
from copy import copy

from opentrons.util import calibration_functions
from opentrons.config import feature_flags as ff
from opentrons.broker import Broker
from opentrons.types import Point, Mount
from opentrons.protocol_api import labware
from opentrons.hardware_control import CriticalPoint

from .models import Container

log = logging.getLogger(__name__)

VALID_STATES = {'probing', 'moving', 'ready'}


class CalibrationManager:
    """
    Serves endpoints that are primarily used in
    opentrons/app/ui/robot/api-client/client.js
    """
    TOPIC = 'calibration'

    def __init__(self, hardware, loop=None, broker=None):
        self._broker = broker or Broker()
        self._hardware = hardware
        self._loop = loop
        self.state = None

    def _set_state(self, state):
        if state not in VALID_STATES:
            raise ValueError(
                'State {0} not in {1}'.format(state, VALID_STATES))
        self.state = state
        self._on_state_changed()

    def tip_probe(self, instrument):
        inst = instrument._instrument
        log.info('Probing tip with {}'.format(instrument.name))
        self._set_state('probing')

        if ff.use_protocol_api_v2():
            mount = Mount[instrument._instrument.mount.upper()]
            assert instrument.tip_racks,\
                'No known tipracks for {}'.format(instrument)
            tip_length = instrument.tip_racks[0]._container.tip_length
            measured_center = self._hardware.locate_tip_probe_center(
                mount, tip_length)
        else:
            measured_center = calibration_functions.probe_instrument(
                instrument=inst,
                robot=inst.robot)

        log.info('Measured probe top center: {0}'.format(measured_center))

        if ff.use_protocol_api_v2():
            self._hardware.update_instrument_offset(
                Mount[instrument._instrument.mount.upper()],
                from_tip_probe=measured_center)
            config = self._hardware.config
        else:
            config = calibration_functions.update_instrument_config(
                instrument=inst,
                measured_center=measured_center)

        log.info('New config: {0}'.format(config))

        self.move_to_front(instrument)
        self._set_state('ready')

    def pick_up_tip(self, instrument, container):
        if not isinstance(container, Container):
            raise ValueError(
                'Invalid object type {0}. Expected models.Container'
                .format(type(container)))

        inst = instrument._instrument
        log.info('Picking up tip from {} in {} with {}'.format(
            container.name, container.slot, instrument.name))
        self._set_state('moving')
        if ff.use_protocol_api_v2():
            with instrument._context.temp_connect(self._hardware):
                instrument._context.location_cache = None
                inst.pick_up_tip(container._container.wells()[0])
        else:
            inst.pick_up_tip(container._container.wells()[0])
        self._set_state('ready')

    def drop_tip(self, instrument, container):
        if not isinstance(container, Container):
            raise ValueError(
                'Invalid object type {0}. Expected models.Container'
                .format(type(container)))

        inst = instrument._instrument
        log.info('Dropping tip from {} in {} with {}'.format(
            container.name, container.slot, instrument.name))
        self._set_state('moving')
        if ff.use_protocol_api_v2():
            with instrument._context.temp_connect(self._hardware):
                instrument._context.location_cache = None
                inst.drop_tip(container._container.wells()[0])
        else:
            inst.drop_tip(container._container.wells()[0])
        self._set_state('ready')

    def return_tip(self, instrument):
        inst = instrument._instrument
        log.info('Returning tip from {}'.format(instrument.name))
        self._set_state('moving')
        if ff.use_protocol_api_v2():
            with instrument._context.temp_connect(self._hardware):
                instrument._context.location_cache = None
                inst.return_tip()
        else:
            inst.return_tip()
        self._set_state('ready')

    def move_to_front(self, instrument):
        inst = instrument._instrument
        log.info('Moving {}'.format(instrument.name))
        self._set_state('moving')
        if ff.use_protocol_api_v2():
            current = self._hardware.gantry_position(
                Mount[inst.mount.upper()],
                critical_point=CriticalPoint.NOZZLE)
            dest = instrument._context.deck.position_for(5)\
                                           .point._replace(z=150)
            self._hardware.move_to(Mount[inst.mount.upper()],
                                   current,
                                   critical_point=CriticalPoint.NOZZLE)
            self._hardware.move_to(Mount[inst.mount.upper()],
                                   dest._replace(z=current.z),
                                   critical_point=CriticalPoint.NOZZLE)
            self._hardware.move_to(Mount[inst.mount.upper()],
                                   dest, critical_point=CriticalPoint.NOZZLE)
        else:
            calibration_functions.move_instrument_for_probing_prep(
                inst, inst.robot)
        self._set_state('ready')

    def move_to(self, instrument, container):
        if not isinstance(container, Container):
            raise ValueError(
                'Invalid object type {0}. Expected models.Container'
                .format(type(container)))

        inst = instrument._instrument
        cont = container._container

        target = cont.wells()[0].top()

        log.info('Moving {} to {} in {}'.format(
            instrument.name, container.name, container.slot))
        self._set_state('moving')

        if ff.use_protocol_api_v2():
            with instrument._context.temp_connect(self._hardware):
                instrument._context.location_cache = None
                inst.move_to(target)
        else:
            inst.move_to(target)

        self._set_state('ready')

    def jog(self, instrument, distance, axis):
        inst = instrument._instrument
        log.info('Jogging {} by {} in {}'.format(
            instrument.name, distance, axis))
        self._set_state('moving')
        if ff.use_protocol_api_v2():
            self._hardware.move_rel(
                Mount[inst.mount.upper()], Point(**{axis: distance}))
        else:
            calibration_functions.jog_instrument(
                instrument=inst,
                distance=distance,
                axis=axis,
                robot=inst.robot)
        self._set_state('ready')

    def home(self, instrument):
        inst = instrument._instrument
        log.info('Homing {}'.format(instrument.name))
        self._set_state('moving')
        if ff.use_protocol_api_v2():
            with instrument._context.temp_connect(self._hardware):
                instrument._context.location_cache = None
                inst.home()
        else:
            inst.home()
        self._set_state('ready')

    def update_container_offset(self, container, instrument):
        inst = instrument._instrument
        log.info('Updating {} in {}'.format(container.name, container.slot))
        if ff.use_protocol_api_v2():
            if 'centerMultichannelOnWells' in container._container.quirks:
                cp = CriticalPoint.XY_CENTER
            else:
                cp = None
            here = self._hardware.gantry_position(Mount[inst.mount.upper()],
                                                  critical_point=cp)
            # Reset calibration so we don’t actually calibrate the offset
            # relative to the old calibration
            container._container.set_calibration(Point(0, 0, 0))
            if ff.calibrate_to_bottom():
                orig = container._container.wells()[0].bottom().point
            else:
                orig = container._container.wells()[0].top().point
            delta = here - orig
            labware.save_calibration(container._container, delta)
        else:
            inst.robot.calibrate_container_with_instrument(
                container=container._container,
                instrument=inst,
                save=True
            )

    def _snapshot(self):
        return {
            'topic': CalibrationManager.TOPIC,
            'name': 'state',
            'payload': copy(self)
        }

    def _on_state_changed(self):
        self._hardware._use_safest_height = (self.state in
                                             ['probing', 'moving'])
        self._broker.publish(CalibrationManager.TOPIC, self._snapshot())
