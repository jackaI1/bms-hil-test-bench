"""Bench acceptance specification, independent of configurable controller limits.

The checker intentionally does not import Controller or Limits. Changing controller
settings cannot silently relax an acceptance deadline or the expected fault bit.
"""

from dataclasses import dataclass
from math import isclose

from .model import Pack


@dataclass(frozen=True)
class Requirement:
    id: str
    fault: int
    deadline_ms: int
    description: str
    hold_ms: int = 0


REQUIREMENTS = {
    "NORMAL": Requirement("BMS-001", 0, 100, "Close by 100 ms; stay closed in healthy operation"),
    "OV": Requirement("BMS-002", 1, 50, "Cell >3.95 V: open within 50 ms", 20),
    "UV": Requirement("BMS-003", 2, 50, "Cell <2.00 V: open within 50 ms", 20),
    "OT": Requirement("BMS-004", 4, 100, "Cell >60 C: open within 100 ms", 50),
    "OC": Requirement("BMS-005", 8, 30, "Absolute current >50 A: open within 30 ms", 10),
    "IMBALANCE": Requirement("BMS-006", 16, 250, "Cell spread >0.15 V: open within 250 ms", 200),
    "SENSOR": Requirement("BMS-007", 32, 120, "Lost/replayed/invalid sensor: open within 120 ms"),
}


def breached(requirement: Requirement, pack: Pack, sensor_fault_active: bool) -> bool:
    voltages = [c.voltage_v for c in pack.cells]
    spread = max(voltages) - min(voltages)
    return {
        "BMS-001": False,
        "BMS-002": max(voltages) > 3.95,
        "BMS-003": min(voltages) < 2.0,
        "BMS-004": max(c.temperature_c for c in pack.cells) > 60,
        "BMS-005": abs(pack.current_a) > 50,
        "BMS-006": spread > 0.15 and not isclose(spread, 0.15, rel_tol=0, abs_tol=1e-12),
        "BMS-007": sensor_fault_active,
    }[requirement.id]
