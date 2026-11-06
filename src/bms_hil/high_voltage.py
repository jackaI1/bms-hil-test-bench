"""Deterministic, software-only traction pack circuit and contactor sequencer."""

from dataclasses import dataclass, field
from enum import Enum
from math import exp, isfinite, log


@dataclass(frozen=True)
class Config:
    pack_v: float = 400.0
    resistance_ohm: float = 100.0
    capacitance_f: float = 0.001
    target_fraction: float = 0.95
    timeout_s: float = 0.5
    minimum_isolation_ohm: float = 200_000.0

    def __post_init__(self):
        if (
            not all(
                isfinite(x) and x > 0
                for x in (
                    self.pack_v,
                    self.resistance_ohm,
                    self.capacitance_f,
                    self.timeout_s,
                    self.minimum_isolation_ohm,
                )
            )
            or not 0 < self.target_fraction < 1
        ):
            raise ValueError("invalid circuit configuration")

    @property
    def expected_precharge_s(self):
        return -self.resistance_ohm * self.capacitance_f * log(1 - self.target_fraction)


@dataclass
class Faults:
    welded_positive: bool = False
    welded_negative: bool = False
    precharge_open: bool = False
    hvil_open: bool = False
    isolation_ohm: float = 1_000_000.0


@dataclass
class Plant:
    config: Config
    faults: Faults = field(default_factory=Faults)
    link_v: float = 0.0
    command_negative: bool = False
    command_positive: bool = False
    command_precharge: bool = False

    @property
    def negative(self):
        return self.command_negative or self.faults.welded_negative

    @property
    def positive(self):
        return self.command_positive or self.faults.welded_positive

    @property
    def precharge(self):
        return self.command_precharge and not self.faults.precharge_open

    def step(self, dt):
        if not isfinite(dt) or dt <= 0:
            raise ValueError("step must be positive and finite")
        if self.negative and self.positive:
            self.link_v = self.config.pack_v
        elif self.negative and self.precharge:
            tau = self.config.resistance_ohm * self.config.capacitance_f
            self.link_v = self.config.pack_v - (self.config.pack_v - self.link_v) * exp(-dt / tau)
        # No load or discharge path is modeled; the capacitor retains its charge.


class State(Enum):
    OFF = "off"
    PRECHARGE = "precharge"
    RUN = "run"
    FAULT = "fault"


class Sequencer:
    def __init__(self, config):
        self.config = config
        self.state = State.OFF
        self.reason = None
        self.started = None
        self.last_time = None

    def step(self, plant, now, *, start):
        if not isfinite(now) or now < 0 or (self.last_time is not None and now <= self.last_time):
            raise ValueError("time must increase")
        self.last_time = now
        if self.state == State.FAULT:
            return
        reason = None
        if plant.faults.hvil_open:
            reason = "hvil_open"
        elif not isfinite(plant.faults.isolation_ohm) or (
            plant.faults.isolation_ohm < self.config.minimum_isolation_ohm
        ):
            reason = "low_isolation"
        elif not plant.command_positive and plant.positive:
            reason = "welded_positive"
        elif not plant.command_negative and plant.negative:
            reason = "welded_negative"
        if reason:
            self._trip(plant, reason)
        elif not start:
            plant.command_positive = plant.command_precharge = plant.command_negative = False
            self.state = State.OFF
        elif self.state == State.OFF:
            # A retained, charged DC link is not accepted as a fresh precharge.
            if plant.link_v > self.config.pack_v * 0.05:
                self._trip(plant, "link_not_discharged")
            else:
                plant.command_negative = plant.command_precharge = True
                self.started = now
                self.state = State.PRECHARGE
        elif self.state == State.PRECHARGE:
            if plant.link_v >= self.config.pack_v * self.config.target_fraction:
                plant.command_positive = True
                plant.command_precharge = False
                self.state = State.RUN
            elif now - self.started >= self.config.timeout_s:
                self._trip(plant, "precharge_timeout")

    def _trip(self, plant, reason):
        plant.command_positive = plant.command_precharge = plant.command_negative = False
        self.reason = reason
        self.state = State.FAULT


@dataclass(frozen=True)
class Verdict:
    requirement: str
    passed: bool
    detail: str


def run_demo():
    """Run independent acceptance checks against the applied circuit states."""
    results = []
    cases = [
        ("HV-001", Faults(), None),
        ("HV-002", Faults(precharge_open=True), "precharge_timeout"),
        ("HV-003", Faults(welded_positive=True), "welded_positive"),
        ("HV-003", Faults(welded_negative=True), "welded_negative"),
        ("HV-004", Faults(hvil_open=True), "hvil_open"),
        ("HV-005", Faults(isolation_ohm=100_000), "low_isolation"),
    ]
    for requirement, faults, expected in cases:
        cfg = Config()
        plant = Plant(cfg, faults)
        controller = Sequencer(cfg)
        reached_run = False
        run_time = None
        for i in range(701):
            if i:
                plant.step(0.001)
            controller.step(plant, i * 0.001, start=True)
            if controller.state == State.RUN and not reached_run:
                reached_run = True
                run_time = i * 0.001
        if expected is None:
            passed = (
                reached_run
                and cfg.expected_precharge_s <= run_time <= cfg.expected_precharge_s + 0.001001
                and plant.positive
                and plant.negative
                and not plant.precharge
            )
            controller.step(plant, 0.701, start=False)
            passed = (
                passed
                and controller.state == State.OFF
                and not (plant.positive or plant.negative or plant.precharge)
            )
            detail = f"run at {run_time} s; calculated {cfg.expected_precharge_s:.3f} s"
        else:
            passed = (
                not reached_run
                and controller.state == State.FAULT
                and controller.reason == expected
                and not any(
                    (plant.command_positive, plant.command_negative, plant.command_precharge)
                )
            )
            detail = f"{expected}: {controller.reason}"
        results.append(Verdict(requirement, bool(passed), detail))
    return results


def main():
    results = run_demo()
    for result in results:
        print(f"{result.requirement} {'PASS' if result.passed else 'FAIL'}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
