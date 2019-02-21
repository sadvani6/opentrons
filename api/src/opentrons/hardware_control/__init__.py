"""
hardware_control: The sole authority for controlling the hardware of an OT2.

The hardware_control module presents a unified api for the lowest level of
hardware command that takes into account the robot as a whole. For instance,
it presents an API for moving a specific pipette mount (not a specific motor
or axis)  to a deck-absolute point (not a Smoothie-coordinate point).

This module is not for use outside the opentrons api module. Higher-level
functions are available elsewhere.
"""

import asyncio
from collections import OrderedDict
import contextlib
import functools
import logging
from typing import Any, Dict, Union, List, Optional, Tuple
from opentrons import types as top_types
from opentrons.util import linal
from .simulator import Simulator
from opentrons.config import robot_configs
from .pipette import Pipette
try:
    from .controller import Controller
except ModuleNotFoundError:
    # implies windows
    Controller = None  # type: ignore
from . import modules
from .types import Axis, HardwareAPILike, CriticalPoint


mod_log = logging.getLogger(__name__)
PICK_UP_SPEED = 30


def _log_call(func):
    @functools.wraps(func)
    def _log_call_inner(*args, **kwargs):
        args[0]._log.debug(func.__name__)
        return func(*args, **kwargs)
    return _log_call_inner


class MustHomeError(RuntimeError):
    pass


class NoTipAttachedError(RuntimeError):
    pass


_Backend = Union[Controller, Simulator]
Instruments = Dict[top_types.Mount, Optional[Pipette]]
SHAKE_OFF_TIPS_SPEED = 50
SHAKE_OFF_TIPS_DISTANCE = 2.25
DROP_TIP_RELEASE_DISTANCE = 20


class API(HardwareAPILike):
    """ This API is the primary interface to the hardware controller.

    Because the hardware manager controls access to the system's hardware
    as a whole, it is designed as a class of which only one should be
    instantiated at a time. This class's methods should be the only method
    of external access to the hardware. Each method may be minimal - it may
    only delegate the call to another submodule of the hardware manager -
    but its purpose is to be gathered here to provide a single interface.
    """

    CLS_LOG = mod_log.getChild('API')

    def __init__(self,
                 backend: _Backend,
                 config: robot_configs.robot_config = None,
                 loop: asyncio.AbstractEventLoop = None) -> None:
        """ Initialize an API instance.

        This should rarely be explicitly invoked by an external user; instead,
        one of the factory methods build_hardware_controller or
        build_hardware_simulator should be used.
        """
        self._log = self.CLS_LOG.getChild(str(id(self)))
        self._config = config or robot_configs.load()
        self._backend = backend
        if None is loop:
            self._loop = asyncio.get_event_loop()
        else:
            self._loop = loop
        self._callbacks: set = set()
        # {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'A': 0.0, 'B': 0.0, 'C': 0.0}
        self._current_position: Dict[Axis, float] = {}

        self._attached_instruments: Instruments = {
            top_types.Mount.LEFT: None,
            top_types.Mount.RIGHT: None
        }
        self._attached_modules: Dict[str, Any] = {}
        self._last_moved_mount: Optional[top_types.Mount] = None
        # The motion lock synchronizes calls to long-running physical tasks
        # involved in motion. This fixes issue where for instance a move()
        # or home() call is in flight and something else calls
        # current_position(), which will not be updated until the move() or
        # home() call succeeds or fails.
        self._motion_lock = asyncio.Lock(loop=self._loop)

    @classmethod
    async def build_hardware_controller(
            cls, config: robot_configs.robot_config = None,
            port: str = None,
            loop: asyncio.AbstractEventLoop = None,
            force: bool = False) -> 'API':
        """ Build a hardware controller that will actually talk to hardware.

        This method should not be used outside of a real robot, and on a
        real robot only one true hardware controller may be active at one
        time.

        :param config: A config to preload. If not specified, load the default.
        :param port: A port to connect to. If not specified, the default port
                     (found by scanning for connected FT232Rs).
        :param loop: An event loop to use. If not specified, use the result of
                     :py:meth:`asyncio.get_event_loop`.
        :param force: If `True`, connect even if a lockfile is present. See
                      :py:meth:`Controller.__init__`.
        """
        if None is Controller:
            raise RuntimeError(
                'The hardware controller may only be instantiated on a robot')
        checked_loop = loop or asyncio.get_event_loop()
        backend = Controller(config, checked_loop, force=force)
        await backend.connect(port)
        return cls(backend, config=config, loop=checked_loop)

    @classmethod
    def build_hardware_simulator(
            cls,
            attached_instruments: Dict[top_types.Mount, Dict[str, Optional[str]]] = None,  # noqa E501
            attached_modules: List[str] = None,
            config: robot_configs.robot_config = None,
            loop: asyncio.AbstractEventLoop = None,
            strict_attached_instruments: bool = True) -> 'API':
        """ Build a simulating hardware controller.

        This method may be used both on a real robot and on dev machines.
        Multiple simulating hardware controllers may be active at one time.
        """

        if None is attached_instruments:
            attached_instruments = {}

        if None is attached_modules:
            attached_modules = []
        return cls(Simulator(attached_instruments,
                             attached_modules,
                             config, loop,
                             strict_attached_instruments),
                   config=config, loop=loop)

    def __repr__(self):
        return '<{} using backend {}>'.format(type(self),
                                              type(self._backend))

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """ The event loop used by this instance. """
        return self._loop

    @loop.setter
    def loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._lock = asyncio.Lock(loop=loop)

    @property
    def is_simulator(self) -> bool:
        """ `True` if this is a simulator; `False` otherwise. """
        return isinstance(self._backend, Simulator)

    async def register_callback(self, cb):
        """ Allows the caller to register a callback, and returns a closure
        that can be used to unregister the provided callback
        """
        self._callbacks.add(cb)

        def unregister():
            self._callbacks.remove(cb)

        return unregister

    # Query API
    @property
    def fw_version(self) -> str:
        """ Return the firmware version of the connected hardware.

        The version is a string retrieved directly from the attached hardware
        (or possibly simulator).
        """
        from_backend = self._backend.fw_version
        if from_backend is None:
            return 'unknown'
        else:
            return from_backend

    # Incidentals (i.e. not motion) API
    @_log_call
    def set_lights(self, button: bool = None, rails: bool = None):
        """ Control the robot lights.

        :param button: If specified, turn the button light on (`True`) or
                       off (`False`). If not specified, do not change the
                       button light.
        :param rails: If specified, turn the rail lights on (`True`) or
                      off (`False`). If not specified, do not change the
                      rail lights.
        """
        self._backend.set_lights(button, rails)

    @_log_call
    def get_lights(self) -> Dict[str, bool]:
        """ Return the current status of the robot lights.

        :returns: A dict of the lights: `{'button': bool, 'rails': bool}`
        """
        return self._backend.get_lights()

    @_log_call
    async def identify(self, duration_s: int = 5):
        """ Blink the button light to identify the robot.

        :param int duration_s: The duration to blink for, in seconds.
        """
        count = duration_s * 4
        on = False
        for sec in range(count):
            then = self._loop.time()
            self.set_lights(button=on)
            on = not on
            now = self._loop.time()
            await asyncio.sleep(max(0, 0.25-(now-then)))
        self.set_lights(button=True)

    @_log_call
    async def cache_instruments(self,
                                require: Dict[top_types.Mount, str] = None):
        """
         - Get the attached instrument on each mount and
         - Cache their pipette configs from pipette-config.json

        If specified, the require element should be a dict of mounts to
        instrument models describing the instruments expected to be present.
        This can save a subsequent of :py:attr:`attached_instruments` and also
        serves as the hook for the hardware simulator to decide what is
        attached.
        """
        checked_require = require or {}
        self._log.info("Updating instrument model cache")
        found = self._backend.get_attached_instruments(checked_require)
        for mount, instrument_data in found.items():
            model = instrument_data.get('model')
            if model is not None:
                p = Pipette(model,
                            self._config.instrument_offset[mount.name.lower()],
                            instrument_data['id'])
                self._attached_instruments[mount] = p
            else:
                self._attached_instruments[mount] = None
        mod_log.info("Instruments found: {}".format(
            self._attached_instruments))

    @property
    def attached_instruments(self):
        configs = ['name', 'min_volume', 'max_volume', 'channels',
                   'aspirate_flow_rate', 'dispense_flow_rate',
                   'pipette_id', 'current_volume', 'display_name',
                   'tip_length']
        instruments = {top_types.Mount.LEFT: {},
                       top_types.Mount.RIGHT: {}}
        for mount in top_types.Mount:
            instr = self._attached_instruments[mount]
            if not instr:
                continue
            instr_dict = instr.as_dict()
            for key in configs:
                instruments[mount][key] = instr_dict[key]
            instruments[mount]['has_tip'] = instr.has_tip
        return instruments

    @property
    def attached_modules(self):
        return self._attached_modules

    @_log_call
    async def update_firmware(
            self,
            firmware_file: str,
            loop: asyncio.AbstractEventLoop = None,
            explicit_modeset: bool = True) -> str:
        """ Update the firmware on the Smoothie board.

        :param firmware_file: The path to the firmware file.
        :param explicit_modeset: `True` to force the smoothie into programming
                                 mode; `False` to assume it is already in
                                 programming mode.
        :param loop: An asyncio event loop to use; if not specified, the one
                     associated with this instance will be used.
        :returns: The stdout of the tool used to update the smoothie
        """
        if None is loop:
            checked_loop = self._loop
        else:
            checked_loop = loop
        return await self._backend.update_firmware(firmware_file,
                                                   checked_loop,
                                                   explicit_modeset)

    # Global actions API
    @_log_call
    def pause(self):
        """
        Pause motion of the robot after a current motion concludes.

        Individual calls to :py:meth:`move`
        (which :py:meth:`aspirate` and :py:meth:`dispense` and other
        calls may depend on) are considered atomic and will always complete if
        they have been called prior to a call to this method. However,
        subsequent calls to :py:meth:`move` that occur when the system
        is paused will not proceed until the system is resumed with
        :py:meth:`resume`.
        """
        self._backend.pause()

    def pause_with_message(self, message):
        self._log.warning('Pause with message: {}'.format(message))
        for cb in self._callbacks:
            cb(message)
        self.pause()

    @_log_call
    def resume(self):
        """
        Resume motion after a call to :py:meth:`pause`.
        """
        self._backend.resume()

    @_log_call
    async def halt(self):
        """ Immediately stop motion, reset, and home.

        This will cancel motion (after the current call to :py:meth:`move`;
        see :py:meth:`pause` for more detail), then home and reset the
        robot.
        """
        self._backend.halt()
        await self.reset()
        await self.home()

    @_log_call
    async def reset(self):
        """ Reset the stored state of the system.

        This will re-scan instruments and models, clearing any cached
        information about their presence or state.
        """
        await self.cache_instruments()
        await self.discover_modules()

    # Gantry/frame (i.e. not pipette) action API
    @_log_call
    async def home_z(self, mount: top_types.Mount = None):
        """ Home the two z-axes """
        if not mount:
            axes = [Axis.Z, Axis.A]
        else:
            axes = [Axis.by_mount(mount)]
        await self.home(axes)

    @_log_call
    async def home_plunger(self, mount: top_types.Mount):
        """
        Home the plunger motor for a mount, and then return it to the 'bottom'
        position.

        :param mount: the mount associated with the target plunger
        :type mount: :py:class:`.top_types.Mount`
        """
        instr = self._attached_instruments[mount]
        if instr:
            await self.home([Axis.of_plunger(mount)])
            await self._move_plunger(mount,
                                     instr.config.plunger_positions['bottom'])

    @_log_call
    async def home(self, axes: List[Axis] = None):
        """ Home the entire robot and initialize current position.
        :param axes: A list of axes to home. Default is `None`, which will
                     home everything.
        """
        # Initialize/update current_position
        checked_axes = axes or [ax for ax in Axis]
        gantry = [ax for ax in checked_axes if ax in Axis.gantry_axes()]
        smoothie_gantry = [ax.name.upper() for ax in gantry]
        smoothie_pos = {}
        plungers = [ax for ax in checked_axes
                    if ax not in Axis.gantry_axes()]
        smoothie_plungers = [ax.name.upper() for ax in plungers]
        async with self._motion_lock:
            if smoothie_gantry:
                smoothie_pos.update(self._backend.home(smoothie_gantry))
            if smoothie_plungers:
                smoothie_pos.update(self._backend.home(smoothie_plungers))
            self._current_position = self._deck_from_smoothie(smoothie_pos)

    def add_tip(
            self,
            mount: top_types.Mount,
            tip_length: float):
        instr = self._attached_instruments[mount]
        instr_dict = self.attached_instruments[mount]
        if instr and not instr.has_tip:
            instr.add_tip(tip_length=tip_length)
            instr_dict['has_tip'] = True
            instr_dict['tip_length'] = tip_length
        else:
            mod_log.warning('attach tip called while tip already attached')

    def remove_tip(self, mount: top_types.Mount):
        instr = self._attached_instruments[mount]
        instr_dict = self.attached_instruments[mount]
        if instr and instr.has_tip:
            instr.remove_tip()
            instr_dict['has_tip'] = False
            instr_dict['tip_length'] = 0.0
        else:
            mod_log.warning('detach tip called with no tip')

    def _deck_from_smoothie(
            self, smoothie_pos: Dict[str, float]) -> Dict[Axis, float]:
        """ Build a deck-abs position store from the smoothie's position

        This should take the smoothie style position {'X': float, etc}
        and turn it into the position dict used here {Axis.X: float} in
        deck-absolute coordinates. It runs the reverse deck transformation
        for the axes that require it.

        One piece of complexity is that if the gantry transformation includes
        a transition between non parallel planes, the z position of the left
        mount would depend on its actual position in deck frame, so we have
        to apply the mount offset.

        TODO: Figure out which frame the mount offset is measured in, because
              if it's measured in the deck frame (e.g. by touching off points
              on the deck) it has to go through the reverse transform to be
              added to the smoothie coordinates here.
        """
        with_enum = {Axis[k]: v for k, v in smoothie_pos.items()}
        plunger_axes = {k: v for k, v in with_enum.items()
                        if k not in Axis.gantry_axes()}
        right = (with_enum[Axis.X], with_enum[Axis.Y],
                 with_enum[Axis.by_mount(top_types.Mount.RIGHT)])
        # Tell apply_transform to just do the change of base part of the
        # transform rather than the full affine transform, because this is
        # an offset
        left = (with_enum[Axis.X],
                with_enum[Axis.Y],
                with_enum[Axis.by_mount(top_types.Mount.LEFT)])
        right_deck = linal.apply_reverse(self.config.gantry_calibration,
                                         right)
        left_deck = linal.apply_reverse(self.config.gantry_calibration,
                                        left)
        deck_pos = {Axis.X: right_deck[0],
                    Axis.Y: right_deck[1],
                    Axis.by_mount(top_types.Mount.RIGHT): right_deck[2],
                    Axis.by_mount(top_types.Mount.LEFT): left_deck[2]}
        deck_pos.update(plunger_axes)
        return deck_pos

    async def current_position(
            self,
            mount: top_types.Mount,
            critical_point: CriticalPoint = None) -> Dict[Axis, float]:
        """ Return the postion (in deck coords) of the critical point of the
        specified mount.

        This returns cached position to avoid hitting the smoothie driver
        unless ``refresh`` is ``True``.

        If `critical_point` is specified, that critical point will be applied
        instead of the default one. For instance, if
        `critical_point=CriticalPoints.MOUNT` then the position of the mount
        will be returned. If the critical point specified does not exist, then
        the next one down is returned - for instance, if there is no tip on the
        specified mount but `CriticalPoint.TIP` was specified, the position of
        the nozzle will be returned.
        """
        if not self._current_position:
            raise MustHomeError
        async with self._motion_lock:
            if mount == mount.RIGHT:
                offset = top_types.Point(0, 0, 0)
            else:
                offset = top_types.Point(*self.config.mount_offset)
            z_ax = Axis.by_mount(mount)
            plunger_ax = Axis.of_plunger(mount)
            cp = self._critical_point_for(mount, critical_point)
            return {
                Axis.X: self._current_position[Axis.X] + offset[0] + cp.x,
                Axis.Y: self._current_position[Axis.Y] + offset[1] + cp.y,
                z_ax: self._current_position[z_ax] + offset[2] + cp.z,
                plunger_ax: self._current_position[plunger_ax]
            }

    async def gantry_position(
            self,
            mount: top_types.Mount,
            critical_point: CriticalPoint = None) -> top_types.Point:
        """ Return the position of the critical point as pertains to the gantry

        This ignores the plunger position and gives the Z-axis a predictable
        name (as :py:attr:`.Point.z`).

        `critical_point` specifies an override to the current critical point to
        use (see :py:meth:`current_position`).
        """
        cur_pos = await self.current_position(mount, critical_point)
        return top_types.Point(x=cur_pos[Axis.X],
                               y=cur_pos[Axis.Y],
                               z=cur_pos[Axis.by_mount(mount)])

    @_log_call
    async def move_to(
            self, mount: top_types.Mount, abs_position: top_types.Point,
            speed: float = None,
            critical_point: CriticalPoint = None):
        """ Move the critical point of the specified mount to a location
        relative to the deck, at the specified speed. 'speed' sets the speed
        of all robot axes to the given value. So, if multiple axes are to be
        moved, they will do so at the same speed

        The critical point of the mount depends on the current status of
        the mount:
        - If the mount does not have anything attached, its critical point is
          the bottom of the mount attach bracket.
        - If the mount has a pipette attached and it is not known to have a
          pipette tip, the critical point is the end of the nozzle of a single
          pipette or the end of the backmost nozzle of a multipipette
        - If the mount has a pipette attached and it is known to have a
          pipette tip, the critical point is the end of the pipette tip for
          a single pipette or the end of the tip of the backmost nozzle of a
          multipipette

        :param mount: The mount to move
        :param abs_position: The target absolute position in
                             :ref:`protocol-api-deck-coords` to move the
                             critical point to
        :param speed: An overall head speed to use during the move
        :param critical_point: The critical point to move. In most situations
                               this is not needed. If not specified, the
                               current critical point will be moved. If
                               specified, the critical point must be one that
                               actually exists - that is, specifying
                               :py:attr:`.CriticalPoint.NOZZLE` when no pipette
                               is attached or :py:attr:`.CriticalPoint.TIP`
                               when no tip is applied will result in an error.
        """
        if not self._current_position:
            raise MustHomeError

        await self._cache_and_maybe_retract_mount(mount)
        z_axis = Axis.by_mount(mount)
        if mount == top_types.Mount.LEFT:
            offset = top_types.Point(*self.config.mount_offset)
        else:
            offset = top_types.Point(0, 0, 0)
        cp = self._critical_point_for(mount, critical_point)
        target_position = OrderedDict(
            ((Axis.X, abs_position.x - offset.x - cp.x),
             (Axis.Y, abs_position.y - offset.y - cp.y),
             (z_axis, abs_position.z - offset.z - cp.z))
        )

        await self._move(target_position, speed=speed)

    @_log_call
    async def move_rel(self, mount: top_types.Mount, delta: top_types.Point,
                       speed: float = None):
        """ Move the critical point of the specified mount by a specified
        displacement in a specified direction, at the specified speed.
        'speed' sets the speed of all axes to the given value. So, if multiple
        axes are to be moved, they will do so at the same speed
        """
        if not self._current_position:
            raise MustHomeError

        await self._cache_and_maybe_retract_mount(mount)

        z_axis = Axis.by_mount(mount)
        try:
            target_position = OrderedDict(
                ((Axis.X,
                  self._current_position[Axis.X] + delta.x),
                 (Axis.Y,
                  self._current_position[Axis.Y] + delta.y),
                 (z_axis,
                  self._current_position[z_axis] + delta.z))
                )
        except KeyError:
            raise MustHomeError
        await self._move(target_position, speed=speed)

    async def _cache_and_maybe_retract_mount(self, mount: top_types.Mount):
        """ Retract the 'other' mount if necessary

        If `mount` does not match the value in :py:attr:`_last_moved_mount`
        (and :py:attr:`_last_moved_mount` exists) then retract the mount
        in :py:attr:`_last_moved_mount`. Also unconditionally update
        :py:attr:`_last_moved_mount` to contain `mount`.
        """
        if mount != self._last_moved_mount and self._last_moved_mount:
            await self.retract(self._last_moved_mount, 10)
        self._last_moved_mount = mount

    async def _move_plunger(self, mount: top_types.Mount, dist: float,
                            speed: float = None):
        z_axis = Axis.by_mount(mount)
        pl_axis = Axis.of_plunger(mount)
        all_axes_pos = OrderedDict(
            ((Axis.X,
              self._current_position[Axis.X]),
             (Axis.Y,
              self._current_position[Axis.Y]),
             (z_axis,
              self._current_position[z_axis]),
             (pl_axis, dist))
        )
        try:
            await self._move(all_axes_pos, speed, False)
        except KeyError:
            raise MustHomeError

    async def _move(self, target_position: 'OrderedDict[Axis, float]',
                    speed: float = None, home_flagged_axes: bool = True):
        """ Worker function to apply robot motion.

        Robot motion means the kind of motions that are relevant to the robot,
        i.e. only one pipette plunger and mount move at the same time, and an
        XYZ move in the coordinate frame of one of the pipettes.

        ``target_position`` should be an ordered dict (ordered by XYZABC)
        of deck calibrated values, containing any specified XY motion and
        at most one of a ZA or BC components. The frame in which to move
        is identified by the presence of (ZA) or (BC).
        """
        # Transform only the x, y, and (z or a) axes specified since this could
        # get the b or c axes as well
        to_transform = tuple((tp
                              for ax, tp in target_position.items()
                              if ax in Axis.gantry_axes()))

        # Pre-fill the dict we’ll send to the backend with the axes we don’t
        # need to transform
        smoothie_pos = {ax.name: pos for ax, pos in target_position.items()
                        if ax not in Axis.gantry_axes()}

        # We’d better have all of (x, y, (z or a)) or none of them since the
        # gantry transform requires them all
        if len(to_transform) != 3:
            self._log.error("Move derived {} axes to transform from {}"
                            .format(len(to_transform), target_position))
            raise ValueError("Moves must specify either exactly an x, y, and "
                             "(z or a) or none of them")

        # Type ignored below because linal.apply_transform (rightly) specifies
        # Tuple[float, float, float] and the implied type from
        # target_position.items() is (rightly) Tuple[float, ...] with unbounded
        # size; unfortunately, mypy can’t quite figure out the length check
        # above that makes this OK
        transformed = linal.apply_transform(  # type: ignore
            self.config.gantry_calibration, to_transform)

        # Since target_position is an OrderedDict with the axes ordered by
        # (x, y, z, a, b, c), and we’ll only have one of a or z (as checked
        # by the len(to_transform) check above) we can use an enumerate to
        # fuse the specified axes and the transformed values back together.
        # While we do this iteration, we’ll also check axis bounds.
        bounds = self._backend.axis_bounds
        for idx, ax in enumerate(target_position.keys()):
            if ax in Axis.gantry_axes():
                smoothie_pos[ax.name] = transformed[idx]
                if smoothie_pos[ax.name] < bounds[ax.name][0]\
                   or smoothie_pos[ax.name] > bounds[ax.name][1]:
                    deck_mins = self._deck_from_smoothie({ax: bound[0]
                                                          for ax, bound
                                                          in bounds.items()})
                    deck_max = self._deck_from_smoothie({ax: bound[1]
                                                         for ax, bound
                                                         in bounds.items()})
                    self._log.warning(
                        "Out of bounds move: {}={} (transformed: {}) not in"
                        "limits ({}, {}) (transformed: ({}, {})"
                        .format(ax.name,
                                target_position[ax],
                                smoothie_pos[ax.name],
                                deck_mins[ax], deck_max[ax],
                                bounds[ax.name][0], bounds[ax.name][1]))
        async with self._motion_lock:
            try:
                self._backend.move(smoothie_pos, speed=speed,
                                   home_flagged_axes=home_flagged_axes)
            except Exception:
                self._log.exception('Move failed')
                self._current_position.clear()
                raise
            else:
                self._current_position.update(target_position)

    @property
    def engaged_axes(self) -> Dict[Axis, bool]:
        """ Which axes are engaged and holding. """
        return {Axis[ax]: eng
                for ax, eng in self._backend.engaged_axes().items()}

    async def disengage_axes(self, which: List[Axis]):
        self._backend.disengage_axes([ax.name for ax in which])

    @_log_call
    async def retract(self, mount: top_types.Mount, margin: float):
        """ Pull the specified mount up to its home position.

        Works regardless of critical point or home status.
        """
        smoothie_ax = Axis.by_mount(mount).name.upper()
        async with self._motion_lock:
            smoothie_pos = self._backend.fast_home(smoothie_ax, margin)
            self._current_position = self._deck_from_smoothie(smoothie_pos)

    def _critical_point_for(
            self, mount: top_types.Mount,
            cp_override: CriticalPoint = None) -> top_types.Point:
        """ Return the current critical point of the specified mount.

        The mount's critical point is the position of the mount itself, if no
        pipette is attached, or the pipette's critical point (which depends on
        tip status).

        If `cp_override` is specified, and that critical point actually exists,
        it will be used instead. Invalid `cp_override`s are ignored.
        """
        pip = self._attached_instruments[mount]
        if pip is not None and cp_override != CriticalPoint.MOUNT:
            return pip.critical_point(cp_override)
        else:
            # TODO: The smoothie’s z/a home position is calculated to provide
            # the offset for a P300 single. Here we should decide whether we
            # implicitly accept this as correct (by returning a null offset)
            # or not (by returning an offset calculated to move back up the
            # length of the P300 single).
            return top_types.Point(0, 0, 0)

    # Gantry/frame (i.e. not pipette) config API
    @property
    def config(self) -> robot_configs.robot_config:
        """ Get the robot's configuration object.

        :returns .robot_config: The object.
        """
        return self._config

    @config.setter
    def config(self, config):
        self._config = config

    def update_config(self, **kwargs):
        """ Update values of the robot's configuration.

        `kwargs` should contain keys of the robot's configuration. For
        instance, `update_config(log_level='debug)` would change the API
        server log level to :py:attr:`logging.DEBUG`.

        Documentation on keys can be found in the documentation for
        :py:class:`.robot_config`.
        """
        self._config = self._config._replace(**kwargs)

    async def update_deck_calibration(self, new_transform):
        pass

    @_log_call
    async def head_speed(self, combined_speed=None,
                         x=None, y=None, z=None, a=None, b=None, c=None):
        pass

    # Pipette action API
    @_log_call
    async def aspirate(self, mount: top_types.Mount, volume: float = None,
                       rate: float = 1.0):
        """
        Aspirate a volume of liquid (in microliters/uL) using this pipette
        from the *current location*. If no volume is passed, `aspirate` will
        default to max available volume (after taking into account the volume
        already present in the tip).

        mount : Mount.LEFT or Mount.RIGHT
        volume : [float] The number of microliters to aspirate
        rate : [float] Set plunger speed for this aspirate, where
            speed = rate * aspirate_speed
        """
        this_pipette = self._attached_instruments[mount]
        if not this_pipette:
            raise top_types.PipetteNotAttachedError(
                "No pipette attached to {} mount".format(mount.name))
        if volume is None:
            asp_vol = this_pipette.available_volume
            mod_log.debug(
                "No aspirate volume defined. Aspirating up to pipette "
                "max_volume ({}uL)".format(this_pipette.config.max_volume))
        else:
            asp_vol = volume

        assert this_pipette.ok_to_add_volume(asp_vol), \
            "Cannot aspirate more than pipette max volume"
        if asp_vol == 0:
            return

        self._backend.set_active_current(
             Axis.of_plunger(mount), this_pipette.config.plunger_current)
        dist = self._plunger_position(
                this_pipette,
                this_pipette.current_volume + asp_vol,
                'aspirate')
        speed = this_pipette.config.aspirate_flow_rate * rate
        try:
            await self._move_plunger(mount, dist, speed=speed)
        except Exception:
            self._log.exception('Aspirate failed')
            this_pipette.set_current_volume(0)
            raise
        else:
            this_pipette.add_current_volume(asp_vol)

    @_log_call
    async def dispense(self, mount: top_types.Mount, volume: float = None,
                       rate: float = 1.0):
        """
        Dispense a volume of liquid in microliters(uL) using this pipette
        at the current location. If no volume is specified, `dispense` will
        dispense all volume currently present in pipette

        mount : Mount.LEFT or Mount.RIGHT
        volume : [float] The number of microliters to dispense
        rate : [float] Set plunger speed for this dispense, where
            speed = rate * dispense_speed
        """
        this_pipette = self._attached_instruments[mount]
        if not this_pipette:
            raise top_types.PipetteNotAttachedError(
                "No pipette attached to {} mount".format(mount.name))
        if volume is None:
            disp_vol = this_pipette.current_volume
            mod_log.debug("No dispense volume specified. Dispensing all "
                          "remaining liquid ({}uL) from pipette".format
                          (disp_vol))
        else:
            disp_vol = volume
        # Ensure we don't dispense more than the current volume
        disp_vol = min(this_pipette.current_volume, disp_vol)

        if disp_vol == 0:
            return

        self._backend.set_active_current(
            Axis.of_plunger(mount), this_pipette.config.plunger_current)
        dist = self._plunger_position(
                this_pipette,
                this_pipette.current_volume - disp_vol,
                'dispense')
        speed = this_pipette.config.dispense_flow_rate * rate
        try:
            await self._move_plunger(mount, dist, speed)
        except Exception:
            self._log.exception('Dispense failed')
            this_pipette.set_current_volume(0)
            raise
        else:
            this_pipette.remove_current_volume(disp_vol)

    def _plunger_position(self, instr: Pipette, ul: float,
                          action: str) -> float:
        mm = ul / instr.ul_per_mm(ul, action)
        position = mm + instr.config.plunger_positions['bottom']
        return round(position, 6)

    @_log_call
    async def blow_out(self, mount):
        """
        Force any remaining liquid to dispense. The liquid will be dispensed at
        the current location of pipette
        """
        this_pipette = self._attached_instruments[mount]
        if not this_pipette:
            raise top_types.PipetteNotAttachedError(
                "No pipette attached to {} mount".format(mount.name))

        self._backend.set_active_current(Axis.of_plunger(mount),
                                         this_pipette.config.plunger_current)
        try:
            await self._move_plunger(
                mount, this_pipette.config.plunger_positions['blow_out'])
        except Exception:
            self._log.exception('Blow out failed')
            raise
        finally:
            this_pipette.set_current_volume(0)

    @_log_call
    async def pick_up_tip(self,
                          mount,
                          tip_length: float,
                          presses: int = 3,
                          increment: float = 1):
        """
        Pick up tip from current location
        """
        instr = self._attached_instruments[mount]
        assert instr
        assert not instr.has_tip, 'Tip already attached'
        instr_ax = Axis.by_mount(mount)
        plunger_ax = Axis.of_plunger(mount)
        self._log.info('Picking up tip on {}'.format(instr.name))
        # Initialize plunger to bottom position
        self._backend.set_active_current(plunger_ax,
                                         instr.config.plunger_current)
        await self._move_plunger(
            mount, instr.config.plunger_positions['bottom'])

        # Press the nozzle into the tip <presses> number of times,
        # moving further by <increment> mm after each press
        for i in range(presses):
            # move nozzle down into the tip
            with self._backend.save_current():
                self._backend.set_active_current(instr_ax,
                                                 instr.config.pick_up_current)
                dist = -1 * instr.config.pick_up_distance + -1 * increment * i
                target_pos = top_types.Point(0, 0, dist)
                await self.move_rel(mount, target_pos, PICK_UP_SPEED)
            # move nozzle back up
            backup_pos = top_types.Point(0, 0, -dist)
            await self.move_rel(mount, backup_pos)
        instr.add_tip(tip_length=tip_length)
        instr.set_current_volume(0)

        # neighboring tips tend to get stuck in the space between
        # the volume chamber and the drop-tip sleeve on p1000.
        # This extra shake ensures those tips are removed
        if 'needs-pickup-shake' in instr.config.quirks:
            await self._shake_off_tips(mount)
            await self._shake_off_tips(mount)

        await self.retract(mount, instr.config.pick_up_distance)

    @_log_call
    async def drop_tip(self, mount, home_after=True):
        """
        Drop tip at the current location

        :param Mount mount: The mount to drop a tip from
        :param bool home_after: Home the plunger motor after dropping tip. This
                                is used in case the plunger motor skipped while
                                dropping the tip, and is also used to recover
                                the ejector shroud after a drop.
        """
        instr = self._attached_instruments[mount]
        assert instr
        assert instr.has_tip, 'Cannot drop tip without a tip attached'
        self._log.info("Dropping tip off from {}".format(instr.name))
        plunger_ax = Axis.of_plunger(mount)
        droptip = instr.config.plunger_positions['drop_tip']
        bottom = instr.config.plunger_positions['bottom']
        self._backend.set_active_current(plunger_ax,
                                         instr.config.plunger_current)
        await self._move_plunger(mount, bottom)
        self._backend.set_active_current(plunger_ax,
                                         instr.config.drop_tip_current)
        await self._move_plunger(
            mount, droptip, speed=instr.config.drop_tip_speed)
        await self._shake_off_tips(mount)
        self._backend.set_active_current(plunger_ax,
                                         instr.config.plunger_current)
        instr.set_current_volume(0)
        instr.remove_tip()
        if home_after:
            safety_margin = abs(bottom-droptip)
            async with self._motion_lock:
                smoothie_pos = self._backend.fast_home(
                    plunger_ax.name.upper(), safety_margin)
                self._current_position = self._deck_from_smoothie(smoothie_pos)
            await self._move_plunger(mount, safety_margin)

    async def _shake_off_tips(self, mount):
        # tips don't always fall off, especially if resting against
        # tiprack or other tips below it. To ensure the tip has fallen
        # first, shake the pipette to dislodge partially-sealed tips,
        # then second, raise the pipette so loosened tips have room to fall
        shake_off_dist = SHAKE_OFF_TIPS_DISTANCE
        # TODO: ensure the distance is not >25% the diameter of placeable
        shake_pos = top_types.Point(-shake_off_dist, 0, 0)  # move left
        await self.move_rel(mount, shake_pos, speed=SHAKE_OFF_TIPS_SPEED)
        shake_pos = top_types.Point(2*shake_off_dist, 0, 0)    # move right
        await self.move_rel(mount, shake_pos, speed=SHAKE_OFF_TIPS_SPEED)
        shake_pos = top_types.Point(-shake_off_dist, 0, 0)  # original position
        await self.move_rel(mount, shake_pos, speed=SHAKE_OFF_TIPS_SPEED)
        # raise the pipette upwards so we are sure tip has fallen off
        up_pos = top_types.Point(0, 0, DROP_TIP_RELEASE_DISTANCE)
        await self.move_rel(mount, up_pos)

    # Pipette config api
    @_log_call
    def calibrate_plunger(self,
                          mount: top_types.Mount,
                          top: float = None, bottom: float = None,
                          blow_out: float = None, drop_tip: float = None):
        """
        Set calibration values for the pipette plunger.
        This can be called multiple times as the user sets each value,
        or you can set them all at once.
        :param top: Touching but not engaging the plunger.
        :param bottom: Must be above the pipette's physical hard-stop, while
        still leaving enough room for 'blow_out'
        :param blow_out: Plunger is pushed down enough to expel all liquids.
        :param drop_tip: Position that causes the tip to be released from the
        pipette
        """
        instr = self._attached_instruments[mount]
        if not instr:
            raise top_types.PipetteNotAttachedError(
                "No pipette attached to {} mount".format(mount.name))

        pos_dict: Dict = instr.config.plunger_positions
        if top is not None:
            pos_dict['top'] = top
        if bottom is not None:
            pos_dict['bottom'] = bottom
        if blow_out is not None:
            pos_dict['blow_out'] = blow_out
        if bottom is not None:
            pos_dict['drop_tip'] = drop_tip
        instr.update_config_item('plunger_positions', pos_dict)

    @_log_call
    def set_flow_rate(self, mount, aspirate=None, dispense=None):
        this_pipette = self._attached_instruments[mount]
        if not this_pipette:
            raise top_types.PipetteNotAttachedError(
                "No pipette attached to {} mount".format(mount))
        if aspirate:
            this_pipette.update_config_item('aspirate_flow_rate', aspirate)
        if dispense:
            this_pipette.update_config_item('dispense_flow_rate', dispense)

    @_log_call
    async def discover_modules(self):
        discovered = {port + model: (port, model)
                      for port, model in self._backend.get_attached_modules()}
        these = set(discovered.keys())
        known = set(self._attached_modules.keys())
        new = these - known
        gone = known - these
        for mod in gone:
            self._attached_modules.pop(mod)
        for mod in new:
            self._attached_modules[mod]\
                = self._backend.build_module(discovered[mod][0],
                                             discovered[mod][1],
                                             self.pause_with_message)
        return list(self._attached_modules.values())

    @_log_call
    async def update_module(
            self, module: modules.AbstractModule,
            firmware_file: str,
            loop: asyncio.AbstractEventLoop = None) -> Tuple[bool, str]:
        """ Update a module's firmware.

        Returns (ok, message) where ok is True if the update succeeded and
        message is a human readable message.
        """
        details = (module.port, module.name())
        mod = self._attached_modules.pop(details[0] + details[1])
        try:
            new_mod = await self._backend.update_module(
                mod, firmware_file, loop)
        except modules.UpdateError as e:
            return False, e.msg
        else:
            new_details = new_mod.port + new_mod.device_info['model']
            self._attached_modules[new_details] = new_mod
            return True, 'firmware update successful'

    async def _do_tp(self, pip, mount) -> top_types.Point:
        """ Execute the work of tip probe.

        This is a separate function so that it can be encapsulated in
        a context manager that ensures the state of the pipette tip tracking
        is reset properly. It should not be called outside of
        :py:meth:`locate_tip_probe_center`.

        :param pip: The pipette to use
        :type pip: opentrons.hardware_control.pipette.Pipette
        :param mount: The mount on which the pipette is attached
        :type mount: opentrons.types.Mount
        """
        # Clear the old offset during calibration
        pip.update_instrument_offset(top_types.Point())
        # Hotspots based on our expectation of tip length and config
        hotspots = robot_configs.calculate_tip_probe_hotspots(
            pip.current_tip_length, self._config.tip_probe)
        new_pos: Dict[Axis, List[float]] = {
            ax: [] for ax in Axis.gantry_axes() if ax != Axis.A}
        safe_z = self._config.tip_probe.z_clearance.crossover + \
            self._config.tip_probe.center[2]
        for hs in hotspots:
            ax_en = Axis[hs.axis.upper()]
            overridden_center = {
                ax: sum(vals)/len(vals)
                if len(vals) == 2
                else self._config.tip_probe.center[ax.value]
                for ax, vals in new_pos.items()
            }
            x0 = overridden_center[Axis.X] + hs.x_start_offs
            y0 = overridden_center[Axis.Y] + hs.y_start_offs
            z0 = hs.z_start_abs
            pos = await self.current_position(mount)

            # Move safely to the setup point for the probe
            await self.move_to(mount,
                               top_types.Point(pos[Axis.X],
                                               pos[Axis.Y],
                                               safe_z))
            await self.move_to(mount,
                               top_types.Point(x0, y0, safe_z))
            await self.move_to(mount,
                               top_types.Point(x0, y0, z0))
            if ax_en == Axis.Z:
                to_probe = Axis.by_mount(mount)
            else:
                to_probe = ax_en
            # Probe and retrieve the position afterwards
            async with self._motion_lock:
                self._current_position = self._deck_from_smoothie(
                    self._backend.probe(
                        to_probe.name.lower(), hs.probe_distance))
            xyz = await self.gantry_position(mount)
            # Store the upated position.
            self._log.debug(
                "tip probe: hs {}: start: ({} {} {}) status {} will add {}"
                .format(hs, x0, y0, z0, new_pos, xyz[ax_en.value]))
            new_pos[ax_en].append(xyz[ax_en.value])
            # Before moving up, move back to clear the switches
            bounce = self._config.tip_probe.bounce_distance\
                * (-1.0 if hs.probe_distance > 0 else 1.0)
            await self.move_rel(mount,
                                top_types.Point(
                                    **{hs.axis: bounce}))
            await self.move_to(mount, xyz._replace(z=safe_z))

        to_ret = top_types.Point(**{ax.name.lower(): sum(vals)/len(vals)
                                    for ax, vals in new_pos.items()})
        self._log.info("Tip probe complete with {} {} on {}. "
                       "New position: {} (default {}), averaged from {}"
                       .format(pip.name, pip.pipette_id, mount.name,
                               to_ret, self._config.tip_probe.center,
                               new_pos))
        return to_ret

    @_log_call
    async def locate_tip_probe_center(
            self, mount, tip_length=None) -> top_types.Point:
        """ Use the specified mount (which should have a tip) to find the
        position of the tip probe target center relative to its definition

        :param mount: The mount to use for the probe
        :param tip_length: If specified (it should usually be specified),
                           the length of the tip assumed to be attached.

        The tip length specification is for the use case during protocol
        calibration, when the machine cannot yet pick up a tip on its own.
        For that reason, it is not universally necessary. Instead, there
        are several cases:

        1. A tip has previously been picked up with :py:meth:`pick_up_tip`.
           ``tip_length`` should not be specified since the tip length is
           known. If ``tip_length`` is not ``None``, this function asserts.
        2. A tip has not previously been picked up, and ``tip_length`` is
           specified. The pipette will internally have a tip added of the
           specified length.
        3. A tip has not previously been picked up, and ``tip_length`` is
           not specified. The pipette will use the tip length from its
           config.

        The return value is a dict containing the updated position, in deck
        coordinates, of the tip probe center.
        """
        opt_pip = self._attached_instruments[mount]
        assert opt_pip, '{} has no pipette'.format(mount.name.lower())
        pip = opt_pip

        if pip.has_tip and tip_length:
            pip.remove_tip()

        if not tip_length:
            assert pip.has_tip,\
                'If pipette has no tip a tip length must be specified'
            tip_length = pip._current_tip_length

        # assure_tip lets us make sure we don’t pollute the pipette
        # state even if there’s an exception in tip probe
        @contextlib.contextmanager
        def _assure_tip():
            if pip.has_tip:
                old_tip = pip._current_tip_length
                pip.remove_tip()
            else:
                old_tip = None
            pip.add_tip(tip_length)
            try:
                yield
            finally:
                pip.remove_tip()
                if old_tip:
                    pip.add_tip(old_tip)

        with _assure_tip():
            return await self._do_tp(pip, mount)

    def update_instrument_offset(self, mount,
                                 new_offset: top_types.Point = None,
                                 from_tip_probe: top_types.Point = None):
        """ Update the instrument offset for a pipette on the specified mount.

        This will update both the stored value in the robot settings and
        the live value in the currently-loaded pipette.

        This can be specified either directly by using the new_offset arg
        or using the result of a previous call to
        :py:meth:`locate_tip_probe_center` with the same mount.

        :note: Z differences in the instrument offset cannot be
               disambiguated between differences in the position of the
               nozzle and differences in the length of the nozzle/tip
               interface (assuming that tips are of reasonably uniform
               length). For this reason, they are saved as adjustments
               to the nozzle interface length and only applied when a
               tip is present.
        """
        if from_tip_probe:
            new_offset = (top_types.Point(*self._config.tip_probe.center)
                          - from_tip_probe)
        elif not new_offset:
            raise ValueError(
                "Either from_tip_probe or new_offset must be specified")
        opt_pip = self._attached_instruments[mount]
        assert opt_pip, '{} has no pipette'.format(mount.name.lower())
        pip = opt_pip
        inst_offs = self._config.instrument_offset
        pip_type = 'multi' if pip.config.channels > 1 else 'single'
        inst_offs[mount.name.lower()][pip_type] = [new_offset.x,
                                                   new_offset.y,
                                                   new_offset.z]
        self.update_config(instrument_offset=inst_offs)
        pip.update_instrument_offset(new_offset)
        robot_configs.save_robot_settings(self._config)
