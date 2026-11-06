"""Classic CAN-sized payloads. Local IDs and units are documented in docs/design.md."""

import struct
from dataclasses import dataclass

from .model import Pack

CELL_BASE = 0x100
CURRENT_ID = 0x200
COMMAND_ID = 0x300


@dataclass(frozen=True)
class Frame:
    arbitration_id: int
    data: bytes


def telemetry_frames(pack: Pack, sequence: int) -> list[Frame]:
    frames = [
        Frame(
            CELL_BASE + i,
            struct.pack(
                "<HhHBx",
                # Model the voltage sensor's rails instead of wrapping or crashing
                # when an extreme electrical fault drives plant truth out of range.
                min(6000, max(0, round(c.voltage_v * 1000))),
                round(c.temperature_c * 100),
                round(c.soc * 10000),
                sequence,
            ),
        )
        for i, c in enumerate(pack.cells)
    ]
    frames.append(Frame(CURRENT_ID, struct.pack("<iB3x", round(pack.current_a * 1000), sequence)))
    return frames


def decode_measurement(frame: Frame) -> tuple[tuple[float, ...], int]:
    if len(frame.data) != 8:
        raise ValueError("telemetry must be eight bytes")
    if frame.arbitration_id == CURRENT_ID:
        current, sequence = struct.unpack("<iB3x", frame.data)
        if abs(current) > 1_000_000:
            raise ValueError("implausible current")
        return (current / 1000,), sequence
    voltage, temperature, soc, sequence = struct.unpack("<HhHBx", frame.data)
    if voltage > 6000 or not -8000 <= temperature <= 20000 or soc > 10000:
        raise ValueError("implausible cell measurement")
    return (voltage / 1000, temperature / 100, soc / 10000), sequence


def command_frame(closed: bool, faults: int) -> Frame:
    return Frame(COMMAND_ID, struct.pack("<BB6x", int(closed), int(faults)))


def decode_command(frame: Frame) -> tuple[bool, int]:
    if frame.arbitration_id != COMMAND_ID or len(frame.data) != 8:
        raise ValueError("invalid command frame")
    closed, faults = struct.unpack("<BB6x", frame.data)
    if closed not in (0, 1) or faults & ~63 or (closed and faults):
        raise ValueError("invalid contactor command or fault mask")
    return bool(closed), faults
