""" A backwards-compatibility shim for the new protocol API.

This should not be imported directly; it is used to provide backwards
compatible singletons in opentrons/__init__.py.
"""

import importlib.util
from typing import Any, List

import opentrons.hardware_control as hc
from opentrons.config.pipette_config import configs
from opentrons.types import Mount
from .labware import Labware
from .contexts import ProtocolContext, InstrumentContext


def run(protocol_bytes: bytes, context: ProtocolContext):
    source = importlib.util.decode_source(protocol_bytes)
    exec(source)


class BCRobot:
    """ A backwards-compatibility shim for the `New Protocol API`_.

    This class is for providing a global instance of a
    :py:class:`.ProtocolContext` in an object called :py:attr:`.robot`. It
    should not be instantiated by user code, and use of its methods should
    be replaced with methods of :py:class:`.ProtocolContext`. For more
    information on how to replace its methods, see the method documentation.

    Attribute accesses to attributes not defined here will fall back to the
    global :py:class:`.ProtocolContext`; for instance, calling `robot.reset()`
    will fall back to calling :py:meth:`.ProtocolContext.reset`.

    For more information see :py:class:`.ProtocolContext`.
    """
    def __init__(self,
                 hardware: hc.adapters.SingletonAdapter,
                 protocol_ctx: ProtocolContext) -> None:
        self._hardware = hardware
        self._ctx = protocol_ctx

    def connect(self, port: str = None,
                options: Any = None):
        """ Connect to the robot hardware.

        This function is provided for backwards compatibility. In most cases
        it need not be called.

        Calls to this method should be replaced with calls to
        :py:meth:`.ProtocolContext.connect` (notice the difference in
        arguments) if necessary; however, since the context of protocols
        executed by an OT2 is automatically connected to either the hardware or
        a simulator (depending on whether the protocol is being simulated or
        run) this should be unnecessary.

        :param port: The port to connect to the smoothie board or the magic
                     string ``"Virtual Smoothie"``, which will initialize and
                     connect to a simulator
        :param options: Ignored.
        """
        self._hardware.connect(port)

    @property
    def fw_version(self):
        return self._hardware.fw_version

    def __getattr__(self, name):
        """ Provide transparent access to the protocol context """
        return getattr(self._ctx, name)


class AddInstrumentCtors(type):

    @staticmethod
    def _build_initializer(model, proper_name):
        """ Build an initializer function for a given pipette.

        This is a separate function so that the `proper_name` is correctly
        caught in the closure that it returns.
        """
        def initializer(
                self,
                mount: str,
                trash_container: Labware = None,
                tip_racks: List[Labware] = None,
                aspirate_flow_rate: float = None,
                dispense_flow_rate: float = None,
                min_volume: float = None,
                max_volume: float = None) -> InstrumentContext:
            return AddInstrumentCtors._load_instr(self._ctx,
                                                  model,
                                                  mount)
        initializer.__name__ = proper_name
        initializer.__qualname__ = '.'.join([AddInstrumentCtors.__qualname__,
                                             proper_name])
        initializer.__doc__ = \
            """Build a {} in a backwards-compatible way.

            :param mount: The mount to load the instrument. One of
                          `'left'` or `'right'`.
            :param trash_container: If specified, a :py:class:`.Labware` to
                                    use for trash.
            :param tip_racks: If specified, a list of :py:class:`.Labware`
                              containing tips.
            :param aspirate_flow_rate: If specified, a flow rate (in uL/s)
                                       to use when aspirating. By default,
                                       the value from the pipette's
                                       configuration.
            :param dispense_flow_rate: If specified, a flow rate (in uL/s)
                                       to use when dispensing. By default,
                                       the value from the pipette's
                                       configuration.
            :param min_volume: The minimum volume that can be aspirated at
                               once. By default, the pipette's minimum
                               volume.
            :param max_volume: The maximum volume that can be aspirated at
                               once. By default, the pipette's maximum
                               volume.

            :returns: An :py:class:`.InstrumentContext`, which represents
                      the newly-loaded pipette.
            """.format(' '.join(proper_name.split('_')))
        return initializer

    @staticmethod
    def _load_instr(ctx,
                    name: str,
                    mount: str,
                    *args, **kwargs) -> InstrumentContext:
        """ Build an instrument in a backwards-compatible way.

        You should almost certainly not be calling this function from a
        protocol; if you want to create a pipette on a lower level, use
        :py:meth:`.ProtocolContext.load_instrument` directly, and if you
        want to create an instrument easily use one of the partials below.
        """
        return ctx.load_instrument(name, Mount[mount.upper()])

    def __new__(cls, name, bases, namespace, **kwds):
        """ Add the pipette initializer functions to the class. """
        res = type.__new__(cls, name, bases, namespace)
        for config in configs:
            # Split the long name with the version
            comps = config.split('_')
            # To get the name without the version
            generic_model = '_'.join(comps[:2])
            number = comps[0].upper()
            ptype_0 = comps[1][0].upper()
            # And a nicely formatted version to name the function
            ptype = ptype_0 + comps[1][1:]
            proper_name = number + '_' + ptype
            if hasattr(res, proper_name):
                # Only build one initializer function for each versionless
                # model (i.e. don’t make a P10_Single for both p10_single_v1
                # and p10_single_v1.3)
                continue

            initializer = cls._build_initializer(generic_model, proper_name)
            setattr(res, proper_name, initializer)

        return res


class BCInstruments(metaclass=AddInstrumentCtors):
    """ A backwards-compatibility shim for the `New Protocol API`_.

    This class is provides a replacement for the `opentrons.instrument`
    global instance. Like that global instance, it shims object creation
    functions for ease of use. It should not be instantiated by user code, and
    use of its methods should be replaced with use of the corresponding methods
    of :py:class:`.ProtocolContext`. For information on how to replace calls to
    methods of this class, see the method documentation.
    """
    def __init__(self, ctx: ProtocolContext) -> None:
        self._ctx = ctx


class BCLabware:
    """ A backwards-compatibility shim for the `New Protocol API`_.

    This class provides a replacement for the `opentrons.labware` and
    `opentrons.containers` global instances. Like those global instances,
    this class shims labware load functions for ease of use. This class should
    not be instantiated by user code, and use of its methods should be
    replaced with use of the corresponding functions of
    :py:class:`.ProtocolContext`. For information on how to replace calls to
    methods of this class, see the method documentation.
    """
    def __init__(self, ctx: ProtocolContext) -> None:
        self._ctx = ctx

    LW_NO_EQUIVALENT = {'24-vial-plate', '48-vial-plate', '5ml-3x4',
                        '96-PCR-tall', '96-deep-well', '96-well-plate-20mm',
                        'MALDI-plate', 'PCR-strip-tall', 'T25-flask',
                        'T75-flask', 'alum-block-pcr-strips', 'e-gelgol',
                        'hampton-1ml-deep-block', 'point',
                        'opentrons-aluminum-block-PCR-strips-200ul',
                        'rigaku-compact-crystallization-plate',
                        'small_vial_rack_16x45', 'temperature-plate',
                        'tiprack-10ul-H', 'tiprack-200ul',
                        'trough-12row-short', 'trough-1row-25ml',
                        'trough-1row-test', 'tube-rack-.75ml',
                        'tube-rack-2ml-9x9', 'tube-rack-5ml-96',
                        'tube-rack-80well', 'wheaton_vial_rack',
                        'tube-rack-15_50ml', 'tube-rack-2ml'}
    """ Labwares that are no longer supported in this version """

    LW_TRANSLATION = {
        '12-well-plate': 'corning_12_wellPlate_6.9_mL',
        '24-well-plate': 'corning_24_wellPlate_3.4_mL',
        '384-plate': 'corning_384_wellPlate_112_uL',
        '48-well-plate': 'corning_48_wellPlate_1.6_mL',
        '6-well-plate': 'corning_6_wellPlate_16.8_mL',
        '96-PCR-flat': 'biorad_96_wellPlate_pcr_200_uL',
        '96-flat': 'generic_96_wellPlate_380_uL',
        'biorad-hardshell-96-PCR': 'biorad_96_wellPlate_pcr_200_uL',
        'opentrons-aluminum-block-2ml-eppendorf': 'opentrons_24_aluminum_tuberack_2_mL',       # noqa(E501)
        'opentrons-aluminum-block-2ml-screwcap': 'opentrons_24_aluminum_tuberack_2_mL',        # noqa(E501)
        'opentrons-aluminum-block-96-PCR-plate': 'opentrons_96_aluminum_biorad_plate_200_uL',  # noqa(E501)
        'opentrons-tiprack-300ul': 'opentrons_96_tiprack_300_uL',
        'opentrons-tuberack-1.5ml-eppendorf': 'opentrons_24_tuberack_1.5_mL_eppendorf',        # noqa(E501)
        'opentrons-tuberack-15_50ml': 'opentrons_6x15_mL_4x50_mL_tuberack',
        'opentrons-tuberack-15ml': 'opentrons_15_tuberack_15_mL_falcon',
        'opentrons-tuberack-2ml-eppendorf': 'opentrons_24_tuberack_2_mL_eppendorf',            # noqa(E501)
        'opentrons-tuberack-2ml-screwcap': 'opentrons_24_tuberack_2_mL_screwcap',              # noqa(E501)
        'opentrons-tuberack-50ml': 'opentrons_6_tuberack_50_mL_falcon',
        'tiprack-1000ul': 'opentrons_96_tiprack_1000_uL',
        'tiprack-10ul': 'opentrons_96_tiprack_10_uL',
        'trough-12row': 'usa_scientific_12_trough_22_mL'
    }
    """ A table mapping old labware names to new labware names"""

    def load(self, container_name, slot, label=None, share=False):
        """ Load a piece of labware by specifying its name and position.

        This method calls :py:meth:`.ProtocolContext.load_labware_by_name`;
        see that documentation for more information on arguments and return
        values. Calls to this function should be replaced with calls to
        :py:meth:`.Protocolcontext.load_labware_by_name`.

        In addition, this function contains translations between old
        labware names and new labware names.
        """
        if share:
            raise NotImplementedError("Sharing not supported")

        try:
            name = self.LW_TRANSLATION[container_name]
        except KeyError:
            if container_name in self.LW_NO_EQUIVALENT:
                raise NotImplementedError("Labware {} is not supported"
                                          .format(container_name))
            elif container_name in ('magdeck', 'tempdeck'):
                raise NotImplementedError("Module load not yet implemented")
            else:
                name = container_name

        return self._ctx.load_labware_by_name(name, slot, label)

    def create(self,  *args, **kwargs):
        raise NotImplementedError

    def list(self, *args, **kwargs):
        raise NotImplementedError


class BCModules:
    def __init__(self, ctx: ProtocolContext) -> None:
        self._ctx = ctx

    def load(self, *args, **wargs):
        pass


def build_globals(hardware=None, loop=None):
    hw = hardware or hc.adapters.SingletonAdapter(loop)
    ctx = ProtocolContext(loop)
    rob = BCRobot(hw, ctx)
    instr = BCInstruments(ctx)
    lw = BCLabware(ctx)
    mod = BCModules(ctx)

    return rob, instr, lw, lw, mod


def set_globals(rob, instr, lw, mod):
    global robot
    global instruments
    global labware
    global containers
    global modules
    robot = rob
    instruments = instr
    labware = lw
    containers = lw
    modules = mod


def reset_globals(hardware=None, loop=None):
    robot, instruments, containers, labware, modules = build_globals(hardware,
                                                                     loop)
    set_globals(robot, instruments, containers, modules)


def reset():
    global robot
    robot.reset()


robot, instruments, containers, labware, modules = build_globals()

__all__ = ['robot', 'reset', 'instruments', 'containers', 'labware', 'modules']
