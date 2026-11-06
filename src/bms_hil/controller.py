"""Controller under test. Inputs are wire messages, never the pack's Python objects."""

from dataclasses import dataclass
from enum import IntFlag
from math import isfinite

from .protocol import CELL_BASE, CURRENT_ID, Frame, command_frame, decode_measurement


class Fault(IntFlag):
    NONE = 0
    OVERVOLTAGE = 1
    UNDERVOLTAGE = 2
    OVERTEMPERATURE = 4
    OVERCURRENT = 8
    IMBALANCE = 16
    SENSOR = 32


@dataclass(frozen=True)
class Limits:
    overvoltage_v: float = 3.95
    undervoltage_v: float = 2.0
    overtemperature_c: float = 60.0
    overcurrent_a: float = 50.0
    imbalance_v: float = 0.15
    voltage_dwell_ms: int = 20
    temperature_dwell_ms: int = 50
    current_dwell_ms: int = 10
    imbalance_dwell_ms: int = 200
    sensor_timeout_ms: int = 100

    def __post_init__(self):
        for name in (
            "overvoltage_v",
            "undervoltage_v",
            "overtemperature_c",
            "overcurrent_a",
            "imbalance_v",
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if (
            not 0 <= self.undervoltage_v < self.overvoltage_v <= 6
            or not -80 <= self.overtemperature_c <= 190
            or self.overcurrent_a <= 0
            or self.imbalance_v <= 0
        ):
            raise ValueError("invalid protection thresholds")
        for name in (
            "voltage_dwell_ms",
            "temperature_dwell_ms",
            "current_dwell_ms",
            "imbalance_dwell_ms",
            "sensor_timeout_ms",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.sensor_timeout_ms == 0:
            raise ValueError("sensor timeout must be positive")


class Controller:
    def __init__(self, cell_count: int, limits: Limits | None = None):
        if not 1 <= cell_count <= 32:
            raise ValueError("controller requires 1 to 32 cells")
        self.limits = limits or Limits()
        self.cell_count = cell_count
        self.closed = False
        self.faults = Fault.NONE
        self._last_tick: int | None = None
        self._started: int | None = None
        self._measurements: dict[int, tuple[tuple[float, ...], int, int]] = {}
        self._since: dict[Fault, int] = {}

    def tick(self, now_ms: int, frames: list[Frame]):
        if not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("time must be non-negative integer milliseconds")
        if self._last_tick is not None and now_ms <= self._last_tick:
            raise ValueError("controller time must strictly increase")
        self._last_tick = now_ms
        if self._started is None:
            self._started = now_ms
        channels = [*(CELL_BASE + i for i in range(self.cell_count)), CURRENT_ID]
        for frame in frames:
            if frame.arbitration_id not in channels:
                continue
            try:
                values, sequence = decode_measurement(frame)
            except ValueError:
                self.faults |= Fault.SENSOR
                continue
            previous = self._measurements.get(frame.arbitration_id)
            # Modulo counter accepts wrap, rejects duplicates and backwards replay.
            if previous and not 0 < (sequence - previous[2]) % 256 < 128:
                continue
            self._measurements[frame.arbitration_id] = (values, now_ms, sequence)

        timeout = self.limits.sensor_timeout_ms
        for channel in channels:
            last = self._measurements.get(channel)
            age = now_ms - (last[1] if last else self._started)
            if age >= timeout:
                self.faults |= Fault.SENSOR
        complete = all(channel in self._measurements for channel in channels)
        if complete:
            cells = [self._measurements[CELL_BASE + i][0] for i in range(self.cell_count)]
            voltages = [c[0] for c in cells]
            current = self._measurements[CURRENT_ID][0][0]
            limits = self.limits
            conditions = (
                (Fault.OVERVOLTAGE, max(voltages) > limits.overvoltage_v, limits.voltage_dwell_ms),
                (
                    Fault.UNDERVOLTAGE,
                    min(voltages) < limits.undervoltage_v,
                    limits.voltage_dwell_ms,
                ),
                (
                    Fault.OVERTEMPERATURE,
                    max(c[1] for c in cells) > limits.overtemperature_c,
                    limits.temperature_dwell_ms,
                ),
                (Fault.OVERCURRENT, abs(current) > limits.overcurrent_a, limits.current_dwell_ms),
                (
                    Fault.IMBALANCE,
                    max(voltages) - min(voltages) > limits.imbalance_v,
                    limits.imbalance_dwell_ms,
                ),
            )
            for fault, active, dwell in conditions:
                if active:
                    since = self._since.setdefault(fault, now_ms)
                    if now_ms - since >= dwell:
                        self.faults |= fault
                else:
                    self._since.pop(fault, None)
        self.closed = complete and not bool(self.faults)

    def command(self) -> Frame:
        return command_frame(self.closed, self.faults)
