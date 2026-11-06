import csv
import json
import random
import subprocess
import sys
from dataclasses import replace

import pytest

from bms_hil.bench import run_scenario, run_suite
from bms_hil.controller import Controller, Limits
from bms_hil.report import write_reports
from bms_hil.scenarios import Scenario, default_scenarios, load_scenarios


@pytest.mark.parametrize("scenario", default_scenarios(), ids=lambda s: s.name)
def test_each_requirement_end_to_end(scenario):
    result = run_scenario(scenario)
    assert result.passed, result.detail
    assert result.trace[-1].current_a == 0 if result.expected_fault else True
    if result.expected_fault:
        assert 0 <= result.latency_ms <= result.deadline_ms
        assert result.response_ms >= result.threshold_ms
        assert result.observed_faults & result.expected_fault


@pytest.mark.parametrize("seed", range(40))
def test_reproducible_randomized_fault_sweep(seed):
    rng = random.Random(seed)
    kinds = [
        "overvoltage",
        "undervoltage",
        "overtemperature",
        "overcurrent",
        "imbalance",
        "sensor_dropout",
        "thermal_ramp",
        "sensor_replay",
    ]
    kind = rng.choice(kinds)
    values = {
        "overvoltage": rng.uniform(4.02, 4.4),
        "undervoltage": rng.uniform(1.5, 1.95),
        "overtemperature": rng.uniform(61, 90),
        "overcurrent": rng.choice([-1, 1]) * rng.uniform(55, 80),
        "imbalance": rng.uniform(3.4, 3.7),
        "thermal_ramp": rng.uniform(60, 150),
    }
    scenario = Scenario(
        name=f"sweep-{seed}",
        kind=kind,
        cell_index=rng.randrange(8),
        inject_ms=rng.randrange(15, 40) * 10,
        value=values.get(kind),
    )
    result = run_scenario(scenario)
    assert result.passed, f"seed={seed}: {result.detail}"


def test_oracle_rejects_disabled_late_and_wrong_protections():
    scenario = Scenario("ov", "overvoltage")
    for limits in (
        Limits(overvoltage_v=5),
        Limits(voltage_dwell_ms=100),
        Limits(overvoltage_v=5, imbalance_dwell_ms=0),
    ):
        result = run_scenario(scenario, controller=Controller(8, limits))
        assert not result.passed


def test_oracle_rejects_preexisting_trip_and_missing_threshold():
    early = run_scenario(
        Scenario("ov", "overvoltage"), controller=Controller(8, Limits(overvoltage_v=3.0))
    )
    assert not early.passed
    never = run_scenario(Scenario("low ramp", "thermal_ramp", value=1))
    assert not never.passed
    assert never.threshold_ms is None


def test_thermal_timer_starts_at_threshold_not_injection():
    result = run_scenario(Scenario("ramp", "thermal_ramp", value=100))
    assert result.passed
    assert result.threshold_ms == 560  # 25 C + 100 C/s, strict >60 C
    assert result.latency_ms == 70  # telemetry + dwell + actuator


def test_trace_proves_plant_receives_wire_command_and_stays_open():
    result = run_scenario(Scenario("oc", "overcurrent", value=75))
    assert result.passed
    trip = next(r for r in result.trace if r.now_ms == result.response_ms)
    assert not trip.contactor_closed
    assert trip.current_a == 0
    assert max(r.current_a for r in result.trace) == 75
    assert all(not r.contactor_closed for r in result.trace if r.now_ms >= result.response_ms)
    assert result.trace[1].telemetry_count == 9


def test_report_has_requirement_result_and_failed_result(tmp_path):
    ok = run_scenario(Scenario("normal", "healthy"))
    bad = run_scenario(Scenario("<bad & name>", "thermal_ramp", value=1))
    write_reports([ok, bad], tmp_path)
    rows = list(csv.DictReader((tmp_path / "results.csv").open()))
    assert [r["status"] for r in rows] == ["PASS", "FAIL"]
    assert all(r["requirement"] for r in rows)
    html = (tmp_path / "report.html").read_text()
    assert "&lt;bad &amp; name&gt;" in html
    assert "<bad & name>" not in html
    assert "FAIL" in html and "PASS" in html
    traces = list(csv.DictReader((tmp_path / "trace.csv").open()))
    assert len(traces) == len(ok.trace) + len(bad.trace)


def test_cli_custom_suite_and_failure_exit(tmp_path):
    scenarios = tmp_path / "suite.json"
    scenarios.write_text(json.dumps([{"name": "no breach", "kind": "thermal_ramp", "value": 1}]))
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "bms_hil",
            "--scenarios",
            str(scenarios),
            "--output",
            str(tmp_path / "reports"),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1, run.stderr
    assert "0/1 passed" in run.stdout
    assert (tmp_path / "reports/report.html").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "mystery"},
        {"inject_ms": -1},
        {"duration_ms": 210},
        {"value": float("nan")},
        {"cell_index": -1},
        {"inject_ms": 201},
        {"name": ""},
        {"value": "bad"},
        {"cell_index": 0.5},
    ],
)
def test_invalid_scenarios_rejected(changes):
    with pytest.raises(ValueError):
        run_scenario(replace(Scenario("bad", "overvoltage"), **changes))


def test_json_schema_unknown_keys_and_empty_suite_rejected(tmp_path):
    for payload in ([], {}, [{"name": "x", "kind": "healthy", "typo": 2}]):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            load_scenarios(path)
    assert all(r.passed for r in run_suite(default_scenarios()))


def test_custom_scenario_can_address_last_cell_of_32_cell_pack(tmp_path):
    path = tmp_path / "suite.json"
    path.write_text('[{"name":"last cell","kind":"overvoltage","cell_index":31}]')
    scenarios = load_scenarios(path, cell_count=32)
    assert run_suite(scenarios, cell_count=32)[0].passed


@pytest.mark.parametrize("cells", [2, 4, 16, 32])
def test_default_suite_other_pack_sizes(cells):
    assert all(r.passed for r in run_suite(default_scenarios(), cell_count=cells))


def test_reports_use_portable_lf_line_endings(tmp_path):
    write_reports([run_scenario(Scenario("normal", "healthy"))], tmp_path)
    assert b"\r" not in (tmp_path / "results.csv").read_bytes()
    assert b"\r" not in (tmp_path / "trace.csv").read_bytes()
