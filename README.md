# Battery fault test bench

I built a battery fault test bench because I wanted a repeatable way to check whether a battery controller disconnects a pack quickly enough when something goes wrong.

I run a simulated sodium-ion battery pack, inject a fault, and measure how long its controller takes to open a contactor, the switch that disconnects the pack. The bench catches high or low cell voltage, overheating, excessive current, uneven cell voltages, and missing sensor messages. It also checks that normal operation and a brief current spike do not cause an unnecessary shutdown.

To try it with Python 3.11 or newer:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m bms_hil --output reports
```

The last command runs 13 scenarios and prints `13/13 passed`. Open `reports/report.html` in a browser for the results. `reports/results.csv` contains one verdict per scenario, and `reports/trace.csv` records every simulated step. A failed scenario makes the command exit with code 1. I keep a [saved example report](examples/report.html) and its [CSV results](examples/results.csv) in the repository; download the HTML to view it.

## What I can change

I can test between 2 and 32 cells, or describe my own faults in a JSON file:

```sh
python -m bms_hil --cells 16 --output reports/16-cell
python -m bms_hil --scenarios examples/scenarios.json --output reports/custom
python -m pytest -q
```

The [scenario guide](docs/design.md#writing-a-scenario) explains each field, its units, and the supported faults. Each scenario starts with a fresh pack and controller, so one test's latched shutdown cannot contaminate the next test.

## What the bench checks

These are my project's acceptance requirements, not manufacturer safety limits. A limit must be exceeded continuously for the controller's hold time before it trips; equality is allowed. The response deadline includes message delivery and contactor actuation. Once a protection trips, the contactor must stay open for the rest of that scenario.

| Requirement | Condition | Hold time | Latest opening after condition begins | Scripted test |
| --- | --- | --- | --- | --- |
| BMS-001 | Healthy startup and operation | None | Close by 100 ms; then stay closed | Normal discharge; brief 10 ms current spike |
| BMS-002 | Any cell above 3.95 V | 20 ms | 50 ms | Cell overvoltage |
| BMS-003 | Any cell below 2.00 V | 20 ms | 50 ms | Cell undervoltage |
| BMS-004 | Any cell above 60 °C | 50 ms | 100 ms | Overtemperature; thermal runaway ramp |
| BMS-005 | Charging or discharging current above 50 A in magnitude | 10 ms | 30 ms | Charge and discharge overcurrent |
| BMS-006 | Highest minus lowest cell voltage above 0.15 V | 200 ms | 250 ms | Cell imbalance |
| BMS-007 | Missing, replayed, or invalid sensor data | 100 ms without fresh data; invalid data trips immediately | 120 ms from first injected bad/missing frame | Cell/current dropout; frozen counter; corrupted frame |

`tests/test_bench.py::test_each_requirement_end_to_end` runs every scripted test in that table. `tests/test_core.py` checks threshold boundaries, continuous hold times, recovery from a brief spike, message validation, and the latched shutdown. The checker rejects a skipped hold time, a late response, an opening for the wrong reason, a shutdown before the fault, or a fault that never actually reaches its threshold.

## How I put it together

Each cell has voltage, temperature, current through the series pack, and state of charge, the fraction of charge remaining. My model updates charge from current over time, calculates voltage sag and recovery, and balances electrical heating against cooling. I use an illustrative sodium-ion-style voltage curve that slopes with charge level. I have not measured or fitted a commercial sodium-ion cell.

Every 10 milliseconds of simulated time, the pack sends encoded eight-byte messages to the controller. These are CAN-like frames, meaning small messages with an identifier and a fixed payload. The controller returns another frame commanding the contactor. Telemetry and the command each take one step to arrive. The controller reads decoded messages rather than the pack's internal state.

The checker observes the pack and the applied contactor command independently of the controller's settings. For a heating ramp, it starts the response clock when temperature crosses 60 °C, not when heating begins. It also verifies that opening the contactor removes electrical current. External heating continues after shutdown.

This is a **software HIL-style bench**. HIL, hardware in the loop, normally connects real controller hardware to a simulated plant. Here both sides run in Python, with the same message boundary and fault-injection pattern. I have not connected a physical controller, battery, CAN adapter, or real-time scheduler. The model and thresholds are test fixtures, so these results do not qualify a real battery for use.

## Design and repeatability

[Design notes](docs/design.md) cover the equations, message layout, timing, fault injection, and the path to a physical controller. The runtime uses Python's standard library and does not need a network service.

GitHub Actions runs the complete test suite, lint checks, package build, and an installed-wheel scenario run on Python 3.11, 3.12, and 3.13. Each run uploads CSV, HTML, trace, and pytest results. Locally, **137 tests pass**, including 40 seeded fault sweeps and 20 seeded healthy-noise sweeps. The default report has **13 passing scenarios across seven requirements**; the fault-response measurements range from **20 to 220 ms**. These are simulated response times, not computer performance measurements.
