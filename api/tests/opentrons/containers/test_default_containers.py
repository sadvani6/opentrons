import pytest
import tempfile
import shutil
import os

from opentrons.containers import load as containers_load
from opentrons.instruments import pipette
from opentrons.data_storage import database as ot_db



@pytest.fixture
def robot():
    from opentrons import Robot
    return Robot()

@pytest.fixture
def database():
    temp_db_fd = tempfile.NamedTemporaryFile(dir='./')
    testing_database_path = shutil.copy2('../testing_database.db', temp_db_fd.name)
    ot_db.change_database(testing_database_path)
    return ot_db

def test_new_containers(robot, database):
    trash_box = containers_load(robot, 'trash-box', 'A1')
    wheaton_vial_rack = containers_load(robot, 'wheaton_vial_rack', 'A2')
    tube_rack_80well = containers_load(robot, 'tube-rack-80well', 'A3')
    T75_flask = containers_load(robot, 'T75-flask', 'B1')
    T25_flask = containers_load(robot, 'T25-flask', 'B2')
    p200 = pipette.Pipette( robot, axis='a', max_volume=1000, name='test-pipette')
    p200.aspirate(100, wheaton_vial_rack[0])
    p200.aspirate(100, tube_rack_80well[0])
    p200.aspirate(100, T75_flask[0])
    p200.aspirate(100, T25_flask[0])
    p200.dispense(trash_box)
