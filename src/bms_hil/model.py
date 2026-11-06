"""Synthetic sodium-ion-inspired cell: sloping OCV, series R, RC and thermal mass.

These parameters are explicit engineering test fixtures, not a characterized cell.
Positive current discharges. The contactor interrupts electrical load, not external heat.
"""

from dataclasses import dataclass, field
from math import exp, isfinite

# (state of charge, open-circuit voltage). Linear interpolation, no fitted vendor data.
OCV = ((0.0, 1.8), (0.1, 2.3), (0.3, 2.8), (0.6, 3.15), (0.9, 3.65), (1.0, 4.0))


@dataclass
class Cell:
    soc: float = 0.6
    temperature_c: float = 25.0
    capacity_ah: float = 10.0
    resistance_ohm: float = 0.005
    polarization_v: float = 0.0
    voltage_v: float = field(init=False)

    def __post_init__(self):
        if not all(
            isfinite(v)
            for v in (
                self.soc,
                self.temperature_c,
                self.capacity_ah,
                self.resistance_ohm,
                self.polarization_v,
            )
        ):
            raise ValueError("cell parameters must be finite")
        if not 0 <= self.soc <= 1 or self.capacity_ah <= 0 or self.resistance_ohm <= 0:
            raise ValueError("invalid cell SOC, capacity or resistance")
        self.voltage_v = self.ocv_v

    @property
    def ocv_v(self) -> float:
        for (s0, v0), (s1, v1) in zip(OCV, OCV[1:]):
            if self.soc <= s1:
                return v0 + (v1 - v0) * (self.soc - s0) / (s1 - s0)
        return OCV[-1][1]


@dataclass
class Pack:
    cells: list[Cell] = field(default_factory=lambda: [Cell() for _ in range(8)])
    current_a: float = 0.0

    def __post_init__(self):
        if not 1 <= len(self.cells) <= 32:
            raise ValueError("pack requires 1 to 32 cells")

    def step(self, dt_ms: int, requested_current_a: float, closed: bool):
        if not isinstance(dt_ms, int) or dt_ms <= 0 or not isfinite(requested_current_a):
            raise ValueError("positive integer step and finite current required")
        dt = dt_ms / 1000
        self.current_a = requested_current_a if closed else 0.0
        for cell in self.cells:
            cell.soc = min(
                1.0, max(0.0, cell.soc - self.current_a * dt / (3600 * cell.capacity_ah))
            )
            # First-order polarization, R1=2 milliohm and tau=5 seconds.
            alpha = exp(-dt / 5.0)
            cell.polarization_v = alpha * cell.polarization_v + (1 - alpha) * self.current_a * 0.002
            cell.voltage_v = cell.ocv_v - self.current_a * cell.resistance_ohm - cell.polarization_v
            # 100 J/K heat capacity, 1 W/K cooling to 25 C ambient.
            equilibrium = 25 + self.current_a**2 * cell.resistance_ohm
            cell.temperature_c = equilibrium + (cell.temperature_c - equilibrium) * exp(-dt / 100)
