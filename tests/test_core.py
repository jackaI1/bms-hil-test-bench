"""Controller requirements use explicit values, separate from production defaults."""

import math

import pytest

from bms_hil.controller import Controller, Fault
from bms_hil.model import Cell, Pack
from bms_hil.protocol import Frame, command_frame, decode_command, telemetry_frames


def healthy_frames(seq=0, **changes):
    cells = [Cell() for _ in range(4)]
    pack = Pack(cells)
    pack.step(10, 0, True)
    for key, value in changes.items():
        if key == "current_a":
            pack.current_a = value
        else:
            setattr(pack.cells[0], key, value)
    return telemetry_frames(pack, seq)


def test_plant_coulomb_count_heat_and_open_contactor():
    p = Pack([Cell(soc=0.6, capacity_ah=10) for _ in range(4)])
    initial = p.cells[0].soc
    p.step(1000, 10, True)
    assert p.cells[0].soc == pytest.approx(initial - 10 / 36000)
    assert p.current_a == 10
    assert p.cells[0].temperature_c > 25
    assert p.cells[0].voltage_v < p.cells[0].ocv_v
    after = p.cells[0].soc
    p.step(1000, 10, False)
    assert p.current_a == 0
    assert p.cells[0].soc == after
    p.step(1000, -10, True)
    assert p.cells[0].soc > after


@pytest.mark.parametrize("kwargs", [{"soc": -0.1}, {"capacity_ah": 0}, {"temperature_c": math.nan}])
def test_invalid_cells_rejected(kwargs):
    with pytest.raises(ValueError):
        Cell(**kwargs)


def test_wire_is_eight_bytes_and_command_round_trip():
    frames = healthy_frames(255)
    assert len(frames) == 5
    assert all(len(f.data) == 8 and f.arbitration_id <= 0x7FF for f in frames)
    assert decode_command(command_frame(False, Fault.OVERVOLTAGE)) == (False, 1)
    with pytest.raises(ValueError):
        decode_command(Frame(0x300, b"\x02" + bytes(7)))


@pytest.mark.parametrize(
    "field,value,fault,dwell",
    [
        ("voltage_v", 4.1, Fault.OVERVOLTAGE, 20),
        ("voltage_v", 1.8, Fault.UNDERVOLTAGE, 20),
        ("temperature_c", 70, Fault.OVERTEMPERATURE, 50),
        ("current_a", 70, Fault.OVERCURRENT, 10),
        ("current_a", -70, Fault.OVERCURRENT, 10),
        ("voltage_v", 3.5, Fault.IMBALANCE, 200),
    ],
)
def test_protection_continuous_dwell_and_latched_open(field, value, fault, dwell):
    c = Controller(4)
    c.tick(0, healthy_frames())
    assert c.closed
    for t in range(10, 10 + dwell, 10):
        c.tick(t, healthy_frames(t // 10, **{field: value}))
        assert not c.faults & fault
    c.tick(10 + dwell, healthy_frames(1 + dwell // 10, **{field: value}))
    assert c.faults & fault
    assert not decode_command(c.command())[0]
    for t in range(300, 400, 10):
        c.tick(t, healthy_frames(t // 10))
    assert not c.closed  # no automatic reclosing after a transient fault


def test_transient_timer_resets_and_exact_threshold_is_healthy():
    c = Controller(4)
    c.tick(0, healthy_frames())
    c.tick(10, healthy_frames(1, current_a=60))
    c.tick(20, healthy_frames(2, current_a=50))
    assert c.closed
    c.tick(30, healthy_frames(3, current_a=60))
    assert c.closed
    c.tick(40, healthy_frames(4, current_a=60))
    assert c.faults == Fault.OVERCURRENT


def test_startup_requires_every_channel_and_times_out():
    c = Controller(4)
    frames = healthy_frames()
    c.tick(0, frames[1:])
    assert not c.closed
    c.tick(100, [])
    assert c.faults & Fault.SENSOR


def test_one_cell_dropout_replay_and_sequence_wrap():
    c = Controller(4)
    for n in range(20):
        c.tick(n * 10, healthy_frames((250 + n) % 256))
    assert c.closed
    replay = healthy_frames(13)[0]
    for n in range(20, 30):
        frames = healthy_frames((250 + n) % 256)
        c.tick(n * 10, [replay, *frames[1:]])
    assert c.faults & Fault.SENSOR


@pytest.mark.parametrize(
    "frame",
    [
        Frame(0x100, b"bad"),
        Frame(0x100, bytes.fromhex("ffff000000000000")),
        Frame(0x200, bytes.fromhex("ffffff7f00000000")),
    ],
)
def test_malformed_or_implausible_measurement_opens(frame):
    c = Controller(4)
    c.tick(0, healthy_frames())
    c.tick(10, [frame])
    assert c.faults & Fault.SENSOR
    assert not c.closed


def test_controller_rejects_backwards_time():
    c = Controller(4)
    c.tick(20, healthy_frames())
    with pytest.raises(ValueError):
        c.tick(10, [])


@pytest.mark.parametrize(
    "changes",
    [
        {"overcurrent_a": -1},
        {"voltage_dwell_ms": -1},
        {"temperature_dwell_ms": 1.5},
        {"imbalance_v": math.nan},
        {"sensor_timeout_ms": 0},
        {"undervoltage_v": 4.0},
        {"overtemperature_c": math.inf},
    ],
)
def test_invalid_controller_limits_rejected(changes):
    from bms_hil.controller import Limits

    with pytest.raises(ValueError):
        Limits(**changes)


@pytest.mark.parametrize("voltage", [2.0, 3.95])
def test_voltage_boundary_is_inclusive(voltage):
    c = Controller(4)
    pack = Pack([Cell() for _ in range(4)])
    for cell in pack.cells:
        cell.voltage_v = voltage
        cell.temperature_c = 60
    for n in range(100):
        c.tick(n * 10, telemetry_frames(pack, n))
    assert c.closed
    assert c.faults == Fault.NONE


def test_plant_soc_saturation_and_passive_cooling():
    p = Pack([Cell(soc=0.001, temperature_c=80)])
    p.step(3600000, 1, True)
    assert p.cells[0].soc == 0
    p.step(3600000, -100, True)
    assert p.cells[0].soc == 1
    # Use the intended small fixed step for thermal behavior.
    p = Pack([Cell(temperature_c=80)])
    for _ in range(1000):
        p.step(10, 0, False)
    assert 25 < p.cells[0].temperature_c < 80


@pytest.mark.parametrize("seed", range(20))
def test_healthy_sensor_noise_near_limits_does_not_trip(seed):
    import random

    rng = random.Random(seed)
    c = Controller(8)
    pack = Pack()
    for n in range(100):
        center = rng.uniform(2.2, 3.7)
        for cell in pack.cells:
            cell.voltage_v = center + rng.uniform(-0.06, 0.06)
            cell.temperature_c = rng.uniform(-10, 59.99)
        pack.current_a = rng.uniform(-49.9, 49.9)
        c.tick(n * 10, telemetry_frames(pack, n))
        assert c.closed, f"seed={seed}, step={n}"


def test_large_step_thermal_integration_stays_between_initial_and_equilibrium():
    p = Pack([Cell(temperature_c=80)])
    p.step(3600000, 1, True)
    assert 25.005 <= p.cells[0].temperature_c < 80


@pytest.mark.parametrize("high,expect_trip", [(3.200, False), (3.201, True)])
def test_imbalance_at_one_wire_unit_above_and_exact_boundary(high, expect_trip):
    pack = Pack([Cell(), Cell()])
    pack.cells[0].voltage_v = high
    pack.cells[1].voltage_v = 3.050
    c = Controller(2)
    for n in range(22):
        c.tick(n * 10, telemetry_frames(pack, n))
    assert bool(c.faults & Fault.IMBALANCE) is expect_trip
    assert c.closed is not expect_trip


def test_independent_imbalance_oracle_respects_equality():
    from bms_hil.requirements import REQUIREMENTS, breached

    pack = Pack([Cell(), Cell()])
    pack.cells[0].voltage_v = 3.200
    pack.cells[1].voltage_v = 3.050
    assert not breached(REQUIREMENTS["IMBALANCE"], pack, False)
    pack.cells[0].voltage_v = 3.201
    assert breached(REQUIREMENTS["IMBALANCE"], pack, False)
