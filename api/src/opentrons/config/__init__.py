"""
This module handles configuration management. It keeps track of where the
configuration data is found in the robot, and is able to search for data to
construct an index if an existing index is not found. All other modules that
use persistent configuration data should use this module to read and write it.

The settings file defined here is opentrons.json. This file should be located

- On the robot, in /data
- Not on the robot, either in
  - the directory from which the python importing this module was launched
  - ~/.opentrons for the current user (where it will be written if nothing is
    found)

The keys in opentrons.json are defined by the CONFIG_ELEMENTS tuple below.
The keys in the file are the name elements of the CONFIG_ELEMENTS. They can
also be specified via environment variables, the names of which are
OT_API_${UPPERCASED_NAME_ELEMENT}. For instance, to override the
robot_settings_file option from an environment variable, you would set the
OT_API_ROBOT_CONFIG_FILE variable.

This module's interface to the rest of the system are the IS_* attributes and
the CONFIG attribute.
"""
import enum
import os
import json
import logging
from pathlib import Path
import shutil
import sys
from typing import Dict, NamedTuple, Optional, Union

_CONFIG_FILENAME = 'config.json'
_LEGACY_INDICES = (Path('/mnt') / 'usbdrive' / 'config' / 'index.json',
                   Path('/data') / 'index.json')

log = logging.getLogger(__file__)

IS_WIN = sys.platform.startswith('win')
IS_OSX = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')
IS_ROBOT = IS_LINUX and os.environ.get('RUNNING_ON_PI')
IS_VIRTUAL = os.environ.get('ENABLE_VIRTUAL_SMOOTHIE')
#: This is the correct thing to check to see if we’re running on a robot


class ConfigElementType(enum.Enum):
    FILE = enum.auto()
    DIR = enum.auto()


class ConfigElement(NamedTuple):
    name: str
    display_name: str
    default: Path
    kind: ConfigElementType
    help: str


CONFIG_ELEMENTS = (
    ConfigElement('labware_database_file',
                  'API V3 Labware Database',
                  Path('opentrons.db'),
                  ConfigElementType.FILE,
                  'The SQLite database where labware definitions and offsets'
                  ' are stored'),
    ConfigElement('labware_user_definitions_dir_v3',
                  'API V3/Split Definitions Custom Labware Directory',
                  Path('labware')/'v3'/'definitions',
                  ConfigElementType.DIR,
                  'The location where APIV3 custom labware definition files'
                  ' are stored (if the split labware definitions feature flag)'
                  ' is set'),
    ConfigElement('labware_calibration_offsets_dir_v3',
                  'API V3/Split Definitions Labware Calibration Directory',
                  Path('labware')/'v3'/'offsets',
                  ConfigElementType.DIR,
                  'The location where APIV3 labware calibration is stored'
                  ' (if the split labware definitions feature flag is set'),
    ConfigElement('labware_calibration_offsets_dir_v4',
                  'API V4 Custom Labware Directory',
                  Path('labware')/'v4'/'offsets',
                  ConfigElementType.DIR,
                  'The location where APIV4 labware calibration is stored'),
    ConfigElement('labware_user_definitions_dir_v4',
                  'API V4 Custom Labware Directory',
                  Path('labware')/'v4'/'offsets',
                  ConfigElementType.DIR,
                  'The location where APIV4 labware calibration is stored'),
    ConfigElement('feature_flags_file',
                  'Feature Flags',
                  Path('feature_flags.json'),
                  ConfigElementType.FILE,
                  'The file storing the feature flags accessible via '
                  'Opentrons app'),
    ConfigElement('robot_settings_file',
                  'Robot Settings',
                  Path('robot_settings.json'),
                  ConfigElementType.FILE,
                  'The file storing settings relevant to motion'),
    ConfigElement('deck_calibration_file',
                  'Deck Calibration',
                  Path('deck_calibration.json'),
                  ConfigElementType.FILE,
                  'The file storing the deck calibration'),
    ConfigElement('log_dir',
                  'Log Directory',
                  Path('logs'),
                  ConfigElementType.FILE,
                  'The location for saving log files'),
    ConfigElement('api_log_file',
                  'API Log File',
                  Path('logs')/'api.log',
                  ConfigElementType.FILE,
                  'The location of the file to save API logs to. If this is an'
                  ' absolute path, it will be used directly. If it is a '
                  'relative path it will be relative to log_dir'),
    ConfigElement('serial_log_file',
                  'Serial Log File',
                  Path('logs')/'serial.log',
                  ConfigElementType.FILE,
                  'The location of the file to save serial logs to. If this is'
                  ' an absolute path, it will be used directly. If it is a '
                  'relative path it will be relative to log_dir'
                  'The location of the file to save serial logs to'),
    ConfigElement('wifi_keys_dir',
                  'Wifi Keys Dir',
                  Path('network_keys'),
                  ConfigElementType.FILE,
                  'The directory in which to save any key material for wifi'
                  ' auth. Not relevant outside of a robot.'),
    ConfigElement('hardware_controller_lockfile',
                  'Hardware Controller Lockfile',
                  Path('hardware.lock'),
                  ConfigElementType.FILE,
                  'The file to use for a hardware controller lockfile.'),
    ConfigElement('pipette_config_overrides_dir',
                  'Pipette Config User Overrides',
                  Path('pipettes'),
                  ConfigElementType.DIR,
                  'The dir where settings overrides for pipettes are stored')
)
#: The available configuration file elements to modify. All of these can be
#: changed by editing opentrons.json, where the keys are the name elements,
#: or by specifying as environment variables, where the keys are uppercase
#: versions of the name elements.
#: In addition to these flags, the OT_API_CONFIG_DIR env var (if present)
#: will change where the API looks for these settings by prepending it to the
#: normal search path.


def infer_config_base_dir() -> Path:
    """ Return the directory to store data in.

    Defaults are ~/.opentrons if not on a pi; OT_API_CONFIG_DIR is
    respected here.

    When this module is imported, this function is called automatically
    and the result stored in :py:attr:`APP_DATA_DIR`.

    This directory may not exist when the module is imported. Even if it
    does exist, it may not contain data, or may require data to be moved
    to it.

    :return pathlib.Path: The path to the desired root settings dir.
    """
    if 'OT_API_CONFIG_DIR' in os.environ:
        return Path(os.environ['OT_API_CONFIG_DIR'])
    elif IS_ROBOT:
        return Path('/data')
    else:
        search = (Path.cwd(),
                  Path.home()/'.opentrons')
        for path in search:
            if (path/_CONFIG_FILENAME).exists():
                return path
        else:
            return search[-1]


def load_and_migrate() -> Dict[str, Path]:
    """ Ensure the settings directory tree is properly configured.

    This function does most of its work on the actual robot. It will move
    all settings files from wherever they happen to be to the proper
    place. On non-robots, this mostly just loads. In addition, it writes
    a default config and makes sure all directories required exist (though
    the files in them may not).
    """
    if IS_ROBOT:
        _migrate_robot()
    base = infer_config_base_dir()
    base.mkdir(parents=True, exist_ok=True)
    index = _load_with_overrides(base)
    return _ensure_paths_and_types(index)


def _load_with_overrides(base) -> Dict[str, str]:
    """ Load an config or write its defaults """
    should_write = False
    overrides = _get_environ_overrides()
    try:
        index = json.load((base/_CONFIG_FILENAME).open())
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write("Error loading config from {}: {}\nRewriting...\n"
                         .format(str(base), e))
        should_write = True
        index = generate_config_index(overrides)

    for key in CONFIG_ELEMENTS:
        if key.name not in index:
            sys.stderr.write(
                f"New config index key {key.name}={key.default}"
                "\nRewriting...\n")
            if key.kind in (ConfigElementType.DIR, ConfigElementType.FILE):
                index[key.name] = base/key.default
            else:
                index[key.name] = key.default
            should_write = True

    if should_write:
        try:
            write_config(index, path=base)
        except Exception as e:
            sys.stderr.write(
                "Error writing config to {}: {}\nProceeding memory-only\n"
                .format(str(base), e))
    index.update(overrides)
    return index


def _ensure_paths_and_types(index: Dict[str, str]) -> Dict[str, Path]:
    """ Take the direct results of loading the config and make sure
    the filesystem reflects them.
    """
    configs_by_name = {ce.name: ce for ce in CONFIG_ELEMENTS}
    correct_types: Dict[str, Path] = {}
    for key, item in index.items():
        if key not in configs_by_name:  # old config, ignore
            continue
        if configs_by_name[key].kind == ConfigElementType.FILE:
            it = Path(item)
            it.parent.mkdir(parents=True, exist_ok=True)
            correct_types[key] = it
        elif configs_by_name[key].kind == ConfigElementType.DIR:
            it = Path(item)
            it.mkdir(parents=True, exist_ok=True)
            correct_types[key] = it
        else:
            raise RuntimeError(
                f"unhandled kind in ConfigElements: {key}: "
                f"{configs_by_name[key].kind}")
    return correct_types


def _get_environ_overrides() -> Dict[str, str]:
    """ Pull any overrides for the config elements from the environ and return
    a mapping from the names to the values (as strings). Config elements that
    are not overridden will not be in the mapping.
    """
    return {
        ce.name: os.environ['OT_API_' + ce.name.upper()]
        for ce in CONFIG_ELEMENTS
        if 'OT_API_' + ce.name.upper() in os.environ}


def _legacy_index() -> Union[None, Dict[str, str]]:
    """ Try and load an index file from the various places it might exist.

    If the legacy file cannot be found or cannot be parsed, return None.

    This method should only be called on a robot.
    """
    for index in _LEGACY_INDICES:
        if index.exists():
            try:
                return json.load(open(index))
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _erase_old_indices():
    """ Remove old index files so they don't pollute future loads.

    This method should only be called on a robot.
    """
    for index in _LEGACY_INDICES:
        if index.exists():
            index.unlink()


def _do_migrate(index: Dict[str, str]):
    base = infer_config_base_dir()
    new_index = generate_config_index(_get_environ_overrides(), base)
    moves = (('/data/user_storage/opentrons_data/opentrons.db',
              new_index['labware_database_file']),
             (index.get('robotSettingsFile'),
              new_index['robot_settings_file']),
             (index.get('deckCalibrationFile'),
              new_index['deck_calibration_file']),
             (index.get('featureFlagFile'),
              new_index['feature_flags_file']),
             (index.get('labware', {})  # type: ignore
              .get('userDefinitionDir'),
              new_index['labware_user_definitions_dir_v3']),
             (index.get('labware', {}).get('offsetDir'),  # type: ignore
              new_index['labware_calibration_offsets_dir_v3']))
    sys.stdout.write(f"config migration: new base {base}\n")
    for old, new in moves:
        if not old:
            continue
        old_path = Path(old)
        new_path = Path(new)
        if old_path.exists() and not old_path.is_symlink():
            sys.stdout.write(f"config migration: {old}->{new}\n")
            if new_path.is_dir():
                shutil.rmtree(new_path)
            shutil.move(old_path, new_path)
        else:
            sys.stdout.write(f"config migration: not moving {old}:")
            sys.stdout.write(f" exists={old_path.exists()}")
            sys.stdout.write(f" symlink={old_path.is_symlink()}\n")

    write_config(new_index, base)


def _migrate_robot():
    old_index = _legacy_index()
    if old_index:
        _do_migrate(old_index)
        _erase_old_indices()


def generate_config_index(defaults: Dict[str, str],
                          base_dir=None) -> Dict[str, Path]:
    """
    Determines where existing info can be found in the system, and creates a
    corresponding data dict that can be written to index.json in the
    baseDataDir.

    The information in the files defined by the config index is information
    required by the API itself and nothing else - labware definitions, feature
    flags, robot configurations. It does not include configuration files that
    relate to the rest of the system, such as network description file
    definitions.

    :param defaults: A dict of defaults to write, useful for specifying part
                     (but not all) of the index succinctly. This is used both
                     when loading a configuration file from disk and when
                     generating a new one.
    :param base_dir: If specified, a base path used if this function has to
                     generate defaults. If not specified, falls back to
                     :py:attr:`CONFIG_BASE_DIR`
    :returns: The config object
    """
    base = Path(base_dir) if base_dir else infer_config_base_dir()

    def parse_or_default(
            ce: ConfigElement, val: Optional[str]) -> Path:
        if not val:
            return base / Path(ce.default)
        else:
            return Path(val)

    return {
        ce.name: parse_or_default(ce,
                                  defaults.get(ce.name))
        for ce in CONFIG_ELEMENTS
    }


def write_config(config_data: Dict[str, Path],
                 path: Path = None):
    """ Save the config file.

    :param config_data: The index to save
    :param base_dir: The place to save the file. If ``None``,
                     :py:meth:`infer_config_base_dir()` will be used

    Only keys that are in the config elements will be saved.
    """
    path = Path(path) if path else infer_config_base_dir()
    valid_names = [ce.name for ce in CONFIG_ELEMENTS]
    try:
        os.makedirs(path, exist_ok=True)
        with (path/_CONFIG_FILENAME).open('w') as base_f:
            json.dump({k: str(v) for k, v in config_data.items()
                       if k in valid_names},
                      base_f, indent=2)
    except OSError as e:
        sys.stderr.write("Config index write to {} failed: {}\n"
                         .format(path/_CONFIG_FILENAME, e))


def reload():
    global CONFIG
    CONFIG.clear()
    CONFIG.update(load_and_migrate())


CONFIG = load_and_migrate()
#: The currently loaded config. This should not change for the lifetime
#: of the program. This is a dict much like os.environ() where the keys
#: are config element names
