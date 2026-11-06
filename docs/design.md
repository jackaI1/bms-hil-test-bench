# Design and test notes

I wanted a bench that checks a controller through its message interface instead of calling its protection functions directly. I chose a deterministic Python loop so I can reproduce a failure on another computer without electrical equipment.

## Scope and assumptions

The default plant is eight series cells, each initially at 60% state of charge and 25 °C. Pack current is shared by all series cells. The harness also accepts 2 to 32 cells. Faults can target any cell. Every run creates new state; there is no automatic contactor reset during a run.

This is software in the loop with HIL-style separation. It does not model CAN arbitration, electrical isolation, precharge, welded contactors, ADC circuits, real bus errors, a cell's internal short circuit, or electrochemical thermal runaway. A thermal runaway ramp here means prescribed external heating, not a prediction of reaction kinetics. Opening a contactor cannot stop that heating.

I use a synthetic sodium-ion-inspired curve and explicit test limits. The numbers are not a datasheet, a standard, or a safety recommendation. The general testing pattern follows the distinction between a simulated plant and a physical controller described in [MathWorks' BMS testing walkthrough](https://www.mathworks.com/videos/series/verifying-validating-and-testing-battery-management-systems.html). [Typhoon HIL's BMS example](https://www.typhoon-hil.com/documentation/typhoon-hil-application-notes/References/battery_management_system.html) also treats protection of a cell's operating region as a BMS responsibility. [CATL's sodium-ion introduction](https://www.catl.com/en/news/665.html) gives chemistry context; it is not the source of my cell parameters or protection limits.

## Plant equations

`model.py` models each cell separately. Positive current discharges and negative current charges.

- Charge: `SOC_next = clamp(SOC - I * dt / (3600 * capacity_Ah), 0, 1)`.
- Open-circuit voltage is linearly interpolated between `(SOC, volts)` points: `(0, 1.8), (0.1, 2.3), (0.3, 2.8), (0.6, 3.15), (0.9, 3.65), (1, 4.0)`.
- Terminal voltage: `V = OCV - I * R0 - Vp`, with `R0 = 0.005 ohm`.
- Polarization: `Vp_next = a * Vp + (1-a) * I * R1`, where `a = exp(-dt/5 seconds)` and `R1 = 0.002 ohm`. This gives a delayed voltage response and recovery.
- Thermal balance: `C * dT/dt = I² * R0 - G * (T - ambient)`, with `C = 100 J/K`, `G = 1 W/K`, and `ambient = 25 °C`. I use the exact constant-current exponential update, which stays stable for larger steps too.

The contactor makes applied current zero when open. Polarization relaxes and cooling continues. Scenario voltage faults overwrite the selected cell's terminal voltage after the plant update, representing an imposed terminal condition. They do not claim to simulate an internal electrochemical failure. SOC is transported for observability but is not a protection input.

## Message boundary

`protocol.py` uses little-endian payloads of eight bytes, the size of classic CAN data. These are local project IDs, not a vehicle or industry protocol. Physical CAN's CRC, acknowledgements, bit timing and arbitration are absent.

| Identifier | Payload | Units |
| --- | --- | --- |
| `0x100 + cell_index` | `uint16 voltage, int16 temperature, uint16 SOC, uint8 sequence, 1 pad byte` | mV, 0.01 °C, 0.01% SOC |
| `0x200` | `int32 current, uint8 sequence, 3 pad bytes` | mA; positive is discharge |
| `0x300` | `uint8 closed, uint8 fault_mask, 6 pad bytes` | closed is 0 or 1 |

Fault mask bits: overvoltage 1, undervoltage 2, overtemperature 4, overcurrent 8, imbalance 16, sensor 32. Multiple protections may latch. A closed command carrying a fault mask is invalid.

Each channel owns its own last-good timestamp and sequence. An advance of 1 to 127 modulo 256 is fresh; duplicate or backwards counters do not refresh the timestamp. Wrap from 255 to 0 works. Eight-byte length and plausible ranges (0 to 6 V, -80 to 200 °C, 0 to 100% SOC, +/-1000 A) are checked. Unknown IDs are ignored. A malformed known channel trips the sensor protection immediately. Plausible but incorrect values remain a limitation of this simple protocol.

The counter assumes frequent updates: a stream silent for half the counter range must already have timed out. It is not a security mechanism. There is no API to reclose after a fault; a new scenario creates a new controller.

## Loop order and response measurement

`bench.py` advances an integer clock in 10 ms steps without sleeping or reading a wall clock:

1. Apply the controller command emitted at the previous step.
2. Update the plant with that contactor state and the scenario's requested current.
3. Inject the prescribed plant or sensor fault and encode new telemetry.
4. Let the independent checker observe plant truth and the applied wire command.
5. Deliver the previous step's telemetry to the controller, execute its protection logic, and queue its command for the next step.

The controller starts open. Complete, fresh telemetry must permit closure by 100 ms before fault injection (at least 150 ms). All threshold comparisons are strict. Continuous hold timers reset when a condition clears. Sensor freshness is checked separately for every channel, even when all other sensors keep updating.

`requirements.py` owns the acceptance specification and imports no controller configuration. The oracle timestamps the first actual threshold crossing, then the first applied contactor opening. It requires the correct fault bit at that opening, a nonnegative latency within the requirement, and no later reclosing. A pre-fault opening, missing crossing, wrong reason, or failure to close at startup fails the scenario. This catches an always-open controller and prevents unrelated protections from hiding a broken one.

A default overcurrent crosses at 200 ms, arrives at 210 ms, satisfies its 10 ms hold at 220 ms and opens the actuator at 230 ms: a 30 ms response. The 100 °C/s thermal ramp exceeds 60 °C at 560 ms, reaches its 50 ms hold at 620 ms and opens at 630 ms: a 70 ms response. The thermal ramp saturates at 190 °C to remain inside the synthetic sensor range.

The current spike lasts one 10 ms sample. Its timer clears on the next sample, before satisfying a 10 ms elapsed hold. For sensor dropout or replay, the acceptance clock starts with the first withheld or replayed frame; freshness timeout remains measured from the last valid reception. Corrupted frames cause an immediate controller trip once delivered. End-to-end time includes both message delays.

## Writing a scenario

Run `python -m bms_hil --scenarios examples/scenarios.json --output reports/custom`.

A file is a nonempty JSON array. Each entry has:

| Field | Meaning |
| --- | --- |
| `name` | Nonempty display label |
| `kind` | One of the fault names below |
| `inject_ms` | Default 200; multiple of 10, at least 150 |
| `duration_ms` | Default 1200; multiple of 10, at least 300 after injection, maximum 60000 |
| `cell_index` | Zero-based target cell; default 0 |
| `value` | Optional magnitude in the units below |

Kinds: `healthy`, `current_pulse`, `overvoltage` (V), `undervoltage` (V), `overtemperature` (°C), `thermal_ramp` (°C/s), `overcurrent` (signed A), `imbalance` (target cell's absolute voltage in V), `sensor_dropout`, `current_dropout`, `sensor_replay`, `corrupt_frame`. A voltage or temperature step persists to the end. `current_pulse` lasts exactly one step; the normal load is +10 A. Sensor dropout removes one selected cell frame; current dropout removes the current frame; replay repeats the last pre-fault cell frame; corruption replaces that frame with a malformed payload.

The required protection and deadline are inferred from `kind`, not supplied by the scenario file. A value that never crosses its protection threshold deliberately produces FAIL. `healthy` and `current_pulse` must stay closed without a fault. For example, increasing the pulse to an extreme current that also causes a voltage trip correctly fails its no-trip expectation.

Use `--cells 32` when targeting cell 31. Whole-suite validation happens before any scenario runs. Unknown fields, invalid kinds, nonfinite values, and values outside the wire range are rejected with exit code 2. File errors also exit 2. Report writes need a writable output folder; use a different folder per concurrent run.

## Evidence and reproducibility

- `results.csv`: one scenario/requirement verdict, threshold and response timestamps, latency, deadline, expected and observed fault masks, failure detail. A blank timing field means no observation, not zero milliseconds.
- `trace.csv`: every tick's voltage and SOC ranges, highest cell temperature, current, applied contactor state, fault bits and delivered message count. This is a compact pack trace rather than an individual-cell waveform recorder.
- `report.html`: a self-contained readable table, with links to the CSVs. It escapes scenario text and needs no network, scripts or server.

`examples/` contains the complete built-in suite's saved report and trace. Recreate them with `python -m bms_hil --output examples`. The JSON file is a shorter custom example, not the source of that saved built-in report.

The pytest parameterization covers every scripted requirement, both current signs, counter wrap, single-channel staleness, malformed data, exact thresholds, timer reset, latched shutdown, nonfinite configuration, report escaping, CLI error exits, and 2/4/16/32-cell runs. Forty fixed seeds vary fault type, magnitude, injection time and target cell. Twenty further fixed seeds vary healthy measurements near limits over 100 updates. Seeds appear in pytest IDs so a failure can be rerun directly.

The checker is itself tested against deliberately disabled, late, wrong-reason and pre-tripped controllers. Passing the controller's own unit tests alone would not prove that the bench can detect these regressions.

## CI and a future hardware connection

`.github/workflows/ci.yml` tests Python 3.11, 3.12 and 3.13. It runs pytest and linting, builds a wheel, installs that wheel, changes out of the source directory, and runs the scenario CLI. CSV, HTML, traces and JUnit results are uploaded even when a test step fails. This checks the public installation path as well as the source tree.

To connect physical hardware, I would replace the one-step in-memory frame exchange with a CAN adapter transport, map these local IDs to the device's protocol, and collect device timestamps and real contactor feedback. The present code does not claim that integration is already done. Physical testing would need characterized cells, actual timing and electrical behavior, and independent protection hardware.
