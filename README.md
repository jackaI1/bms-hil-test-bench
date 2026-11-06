I merged this into my existing [lifeos-desk-node project](https://github.com/jackaI1/lifeos-desk-node/tree/main/projects/bms-hil-test-bench). The current code is in `projects/bms-hil-test-bench/` there. I kept the code here so older links still work.

The move keeps the original layout, including `tests/test_bench.py`, `tests/test_high_voltage.py`, `tests/test_core.py`, `src/bms_hil/scenarios.py`. I replaced the README with the report below and left out Git history, installed dependencies, caches and generated build output from the hub copy.

The 3 test source files moved with the code. No project-specific GitHub Actions workflow was retained; the report records the omitted checks, and the test sources can be run locally from the linked folder.

Omitted workflows: `bms-hil-test-bench-ci.yml`: The relocated workflow did not complete successfully within the migration batch. Local test sources remain available.

---

# bms-hil-test-bench

I built a battery fault test bench to learn how the underlying idea works. This is a learning project.

## What I can change

I can test between 2 and 32 cells, or describe my own faults in a JSON file:

```sh
python -m bms_hil --cells 16 --output reports/16-cell
python -m bms_hil --scenarios examples/scenarios.json --output reports/custom
python -m pytest -q
```

The [scenario guide](docs/design.md#writing-a-scenario) explains each field, its units, and the supported faults. Each scenario starts with a fresh pack and controller, so one test's latched shutdown cannot contaminate the next test.

## How I put it together

Each cell has voltage, temperature, current through the series pack, and state of charge, the fraction of charge remaining. My model updates charge from current over time, calculates voltage sag and recovery, and balances electrical heating against cooling. I use an illustrative sodium-ion-style voltage curve that slopes with charge level. I have not measured or fitted a commercial sodium-ion cell.

Every 10 milliseconds of simulated time, the pack sends encoded eight-byte messages to the controller. These are CAN-like frames, meaning small messages with an identifier and a fixed payload. The controller returns another frame commanding the contactor. Telemetry and the command each take one step to arrive. The controller reads decoded messages rather than the pack's internal state.

The checker observes the pack and the applied contactor command independently of the controller's settings. For a heating ramp, it starts the response clock when temperature crosses 60 °C, not when heating begins. It also verifies that opening the contactor removes electrical current. External heating continues after shutdown.

This is a **software HIL-style bench**. HIL, hardware in the loop, normally connects real controller hardware to a simulated plant. Here both sides run in Python, with the same message boundary and fault-injection pattern. I have not connected a physical controller, battery, CAN adapter, or real-time scheduler. The model and thresholds are test fixtures, so these results do not qualify a real battery for use.

## Design and repeatability

[Design notes](docs/design.md) cover the equations, message layout, timing, fault injection, and the path to a physical controller. The runtime uses Python's standard library and does not need a network service.

## Tests and limits

There are 3 test source files in this copy. That source count does not establish an execution result. This is a source snapshot, and running it on a new machine may need environment-specific setup.



The relocated workflow did not complete successfully within the migration batch. Local test sources remain available.

No project-specific GitHub Actions workflow was retained. The local test sources remain in this folder.
