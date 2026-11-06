"""Deterministic software HIL-style loop with one-tick delays in both directions."""

from dataclasses import dataclass

from .controller import Controller
from .model import Cell, Pack
from .protocol import CELL_BASE, CURRENT_ID, Frame, decode_command, telemetry_frames
from .requirements import breached
from .scenarios import STEP_MS, Scenario


@dataclass(frozen=True)
class TraceRow:
    now_ms: int
    min_voltage_v: float
    max_voltage_v: float
    max_temperature_c: float
    current_a: float
    min_soc: float
    max_soc: float
    contactor_closed: bool
    faults: int
    telemetry_count: int


@dataclass(frozen=True)
class Result:
    scenario: str
    requirement: str
    expected_fault: int
    deadline_ms: int
    threshold_ms: int | None
    response_ms: int | None
    latency_ms: int | None
    observed_faults: int
    passed: bool
    detail: str
    trace: list[TraceRow]


def run_scenario(
    scenario: Scenario, *, controller: Controller | None = None, cell_count: int = 8
) -> Result:
    scenario.validate(cell_count)
    pack = Pack([Cell() for _ in range(cell_count)])
    dut = controller if controller is not None else Controller(cell_count)
    if dut.cell_count != cell_count:
        raise ValueError("controller and pack cell counts must match")
    requirement = scenario.requirement
    telemetry: list[Frame] = []
    command: Frame | None = None
    closed, faults = False, 0
    ever_closed = False
    threshold = response = None
    response_faults = 0
    errors: set[str] = set()
    trace: list[TraceRow] = []
    frozen: Frame | None = None

    for now in range(0, scenario.duration_ms + STEP_MS, STEP_MS):
        # Apply last tick's encoded command to the simulated actuator.
        if command is not None:
            closed, faults = decode_command(command)
        active = now >= scenario.inject_ms
        load = 10.0
        if active and scenario.kind == "overcurrent":
            load = scenario.magnitude
        if scenario.kind == "current_pulse" and now == scenario.inject_ms:
            load = scenario.magnitude
        if now > 0:
            pack.step(STEP_MS, load, closed)
        cell = pack.cells[scenario.cell_index]
        if active:
            if scenario.kind in ("overvoltage", "undervoltage", "imbalance"):
                cell.voltage_v = scenario.magnitude
            elif scenario.kind == "overtemperature":
                cell.temperature_c = scenario.magnitude
            elif scenario.kind == "thermal_ramp":
                # Prescribed external heating continues after the contactor opens.
                cell.temperature_c = min(
                    190, 25 + scenario.magnitude * (now - scenario.inject_ms) / 1000
                )
        outgoing = telemetry_frames(pack, (now // STEP_MS) % 256)
        selected = CELL_BASE + scenario.cell_index
        if not active:
            frozen = outgoing[scenario.cell_index]
        sensor_fault = active and scenario.kind in (
            "sensor_dropout",
            "current_dropout",
            "sensor_replay",
            "corrupt_frame",
        )
        if sensor_fault:
            if scenario.kind in ("sensor_dropout", "current_dropout"):
                target = CURRENT_ID if scenario.kind == "current_dropout" else selected
                outgoing = [f for f in outgoing if f.arbitration_id != target]
            elif scenario.kind == "sensor_replay":
                outgoing[scenario.cell_index] = frozen  # last genuinely fresh wire frame
            else:
                outgoing[scenario.cell_index] = Frame(selected, b"bad")

        # Independent oracle reads plant truth and the actuator, not controller timers.
        if requirement.fault and threshold is None and breached(requirement, pack, sensor_fault):
            threshold = now
        if closed:
            ever_closed = True
        elif ever_closed:
            if not requirement.fault or threshold is None:
                errors.add("contactor opened before the required fault")
            elif response is None:
                response, response_faults = now, faults
        if now == 100 and not closed:
            errors.add("contactor did not close by 100 ms during healthy startup")
        if response is not None and closed:
            errors.add("contactor reclosed after a protection trip")
        if requirement.fault == 0 and faults:
            errors.add("unexpected latched fault during healthy operation")

        trace.append(
            TraceRow(
                now,
                min(c.voltage_v for c in pack.cells),
                max(c.voltage_v for c in pack.cells),
                max(c.temperature_c for c in pack.cells),
                pack.current_a,
                min(c.soc for c in pack.cells),
                max(c.soc for c in pack.cells),
                closed,
                faults,
                len(telemetry),
            )
        )
        dut.tick(now, telemetry)
        command = dut.command()
        telemetry = outgoing

    latency = None if threshold is None or response is None else response - threshold
    if not ever_closed:
        errors.add("contactor never closed")
    if requirement.fault:
        if threshold is None:
            errors.add("fault never crossed the required threshold")
        if response is None:
            errors.add("no contactor opening observed")
        elif not response_faults & requirement.fault:
            errors.add("contactor opened with the wrong protection reason")
        if latency is not None and not 0 <= latency <= requirement.deadline_ms:
            errors.add(f"response {latency} ms exceeds {requirement.deadline_ms} ms limit")
    return Result(
        scenario.name,
        requirement.id,
        requirement.fault,
        requirement.deadline_ms,
        threshold,
        response,
        latency,
        response_faults,
        not errors,
        "; ".join(sorted(errors)) if errors else "All acceptance checks passed",
        trace,
    )


def run_suite(scenarios: list[Scenario], *, cell_count: int = 8) -> list[Result]:
    if not scenarios:
        raise ValueError("suite must not be empty")
    # Validate the entire suite before running any scenario or writing a report.
    for scenario in scenarios:
        scenario.validate(cell_count)
    return [run_scenario(s, cell_count=cell_count) for s in scenarios]
