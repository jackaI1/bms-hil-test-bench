import math

import pytest

from bms_hil.high_voltage import Config, Faults, Plant, Sequencer, State, run_demo


def test_rc_precharge_and_ordered_start_stop():
    cfg = Config()
    plant = Plant(cfg)
    control = Sequencer(cfg)
    dt = 0.001
    control.step(plant, 0, start=True)
    assert control.state == State.PRECHARGE
    assert plant.negative and plant.precharge and not plant.positive
    for i in range(1, 1000):
        plant.step(dt)
        control.step(plant, i * dt, start=True)
        if control.state == State.RUN:
            break
    expected = -cfg.resistance_ohm * cfg.capacitance_f * math.log(1 - cfg.target_fraction)
    assert expected <= i * dt <= expected + dt * 1.01
    assert plant.link_v >= cfg.pack_v * cfg.target_fraction
    assert plant.negative and plant.positive and not plant.precharge
    control.step(plant, i * dt + dt, start=False)
    assert control.state == State.OFF
    assert not (plant.negative or plant.positive or plant.precharge)


@pytest.mark.parametrize(
    "fault,reason",
    [
        (Faults(welded_positive=True), "welded_positive"),
        (Faults(welded_negative=True), "welded_negative"),
        (Faults(precharge_open=True), "precharge_timeout"),
        (Faults(hvil_open=True), "hvil_open"),
        (Faults(isolation_ohm=100_000), "low_isolation"),
    ],
)
def test_faults_latch_and_command_open(fault, reason):
    plant = Plant(Config(), fault)
    control = Sequencer(plant.config)
    for i in range(1000):
        plant.step(0.001)
        control.step(plant, i * 0.001, start=True)
    assert control.state == State.FAULT
    assert control.reason == reason
    assert not any((plant.command_negative, plant.command_positive, plant.command_precharge))


def test_runtime_interlock_trips_and_stays_latched():
    plant = Plant(Config())
    control = Sequencer(plant.config)
    for i in range(500):
        plant.step(0.001)
        control.step(plant, i * 0.001, start=True)
        if control.state == State.RUN:
            break
    assert control.state == State.RUN
    plant.faults.hvil_open = True
    control.step(plant, (i + 1) * 0.001, start=True)
    assert control.state == State.FAULT
    plant.faults.hvil_open = False
    control.step(plant, (i + 2) * 0.001, start=True)
    assert control.state == State.FAULT


def test_demo_checks_all_requirements():
    results = run_demo()
    assert len(results) >= 6
    assert all(result.passed for result in results), results


def test_weld_on_shutdown_is_detected_from_feedback():
    plant = Plant(Config())
    control = Sequencer(plant.config)
    for i in range(500):
        plant.step(0.001)
        control.step(plant, i * 0.001, start=True)
        if control.state == State.RUN:
            break
    plant.faults.welded_positive = True
    control.step(plant, (i + 1) * 0.001, start=False)
    control.step(plant, (i + 2) * 0.001, start=False)
    assert control.reason == "welded_positive"
    assert plant.positive and not plant.command_positive


def test_low_isolation_during_run_trips_immediately():
    plant = Plant(Config())
    control = Sequencer(plant.config)
    for i in range(500):
        plant.step(0.001)
        control.step(plant, i * 0.001, start=True)
        if control.state == State.RUN:
            break
    plant.faults.isolation_ohm = 199_999
    control.step(plant, (i + 1) * 0.001, start=True)
    assert control.reason == "low_isolation"
    assert not plant.positive and not plant.negative
