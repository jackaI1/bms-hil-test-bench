"""Small JSON scenario format; no executable expressions or external dependencies."""

import json
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from .requirements import REQUIREMENTS, Requirement

KINDS = {
    "healthy": "NORMAL",
    "current_pulse": "NORMAL",
    "overvoltage": "OV",
    "undervoltage": "UV",
    "overtemperature": "OT",
    "thermal_ramp": "OT",
    "overcurrent": "OC",
    "imbalance": "IMBALANCE",
    "sensor_dropout": "SENSOR",
    "current_dropout": "SENSOR",
    "sensor_replay": "SENSOR",
    "corrupt_frame": "SENSOR",
}
DEFAULT_VALUES = {
    "overvoltage": 4.1,
    "undervoltage": 1.8,
    "overtemperature": 70,
    "overcurrent": 75,
    "imbalance": 3.5,
    "thermal_ramp": 100,
    "current_pulse": 75,
}
STEP_MS = 10


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    inject_ms: int = 200
    duration_ms: int = 1200
    cell_index: int = 0
    value: float | None = None

    @property
    def requirement(self) -> Requirement:
        return REQUIREMENTS[KINDS[self.kind]]

    @property
    def magnitude(self) -> float:
        return self.value if self.value is not None else DEFAULT_VALUES.get(self.kind, 0)

    def validate(self, cell_count: int = 8):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("scenario needs a nonempty name")
        if not isinstance(self.kind, str) or self.kind not in KINDS:
            raise ValueError(f"unknown scenario kind: {self.kind}")
        for label, value in (
            ("inject_ms", self.inject_ms),
            ("duration_ms", self.duration_ms),
            ("cell_index", self.cell_index),
        ):
            if type(value) is not int:
                raise ValueError(f"{label} must be an integer")
        if not 2 <= cell_count <= 32 or not 0 <= self.cell_index < cell_count:
            raise ValueError("bench requires 2 to 32 cells and a valid cell_index")
        if self.inject_ms < 150 or self.inject_ms % STEP_MS:
            raise ValueError("inject_ms must be a 10 ms multiple, at least 150 ms after startup")
        if (
            self.duration_ms < self.inject_ms + 300
            or self.duration_ms > 60_000
            or self.duration_ms % STEP_MS
        ):
            raise ValueError("duration must be a 10 ms multiple, 300 ms after injection, <=60000")
        if self.value is not None and (
            type(self.value) not in (int, float) or not isfinite(self.value)
        ):
            raise ValueError("value must be a finite number")
        value = self.magnitude
        if self.kind in ("overvoltage", "undervoltage", "imbalance") and not 0 <= value <= 6:
            raise ValueError("injected voltage must fit 0 to 6 V sensor range")
        if self.kind == "overtemperature" and not -80 <= value <= 190:
            raise ValueError("injected temperature must fit -80 to 190 C")
        if self.kind == "thermal_ramp" and not 0 < value <= 150:
            raise ValueError("thermal ramp must be >0 and <=150 C/s")
        if self.kind in ("overcurrent", "current_pulse") and abs(value) > 1000:
            raise ValueError("injected current must fit +/-1000 A")


def default_scenarios() -> list[Scenario]:
    return [
        Scenario("normal discharge", "healthy"),
        Scenario("brief current spike", "current_pulse"),
        Scenario("cell overvoltage", "overvoltage"),
        Scenario("cell undervoltage", "undervoltage"),
        Scenario("cell overtemperature", "overtemperature"),
        Scenario("thermal runaway ramp", "thermal_ramp"),
        Scenario("discharge overcurrent", "overcurrent"),
        Scenario("charge overcurrent", "overcurrent", value=-75),
        Scenario("cell imbalance", "imbalance"),
        Scenario("cell sensor dropout", "sensor_dropout"),
        Scenario("current sensor dropout", "current_dropout"),
        Scenario("frozen cell counter", "sensor_replay"),
        Scenario("corrupted cell frame", "corrupt_frame"),
    ]


def load_scenarios(path: str | Path, *, cell_count: int = 8) -> list[Scenario]:
    try:
        payload = json.loads(Path(path).read_text())
        if not isinstance(payload, list) or not payload:
            raise ValueError("scenario file must be a nonempty JSON array")
        scenarios = [Scenario(**item) for item in payload]
        for scenario in scenarios:
            scenario.validate(cell_count)
        return scenarios
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid scenario JSON: {exc}") from exc
