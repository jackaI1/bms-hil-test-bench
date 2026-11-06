"""Portable CSV, per-step trace, and self-contained HTML requirement reports."""

import csv
from dataclasses import asdict
from html import escape
from pathlib import Path

from .bench import Result

SUMMARY_FIELDS = (
    "scenario",
    "requirement",
    "status",
    "threshold_ms",
    "response_ms",
    "latency_ms",
    "deadline_ms",
    "expected_fault",
    "observed_faults",
    "detail",
)


def write_reports(results: list[Result], output: str | Path):
    if not results:
        raise ValueError("cannot report an empty suite")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for result in results:
        row = {key: getattr(result, key) for key in SUMMARY_FIELDS if key != "status"}
        row["status"] = "PASS" if result.passed else "FAIL"
        summaries.append(row)
    with (output / "results.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SUMMARY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(summaries)
    with (output / "trace.csv").open("w", newline="") as file:
        fields = ["scenario", *asdict(results[0].trace[0])]
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for result in results:
            for trace in result.trace:
                writer.writerow({"scenario": result.scenario, **asdict(trace)})
    headers = "".join(f"<th scope='col'>{escape(key)}</th>" for key in SUMMARY_FIELDS)
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(str(row[key])) if row[key] is not None else 'n/a'}</td>"
            for key in SUMMARY_FIELDS
        )
        + "</tr>"
        for row in summaries
    )
    passed = sum(r.passed for r in results)
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>BMS protection test report</title>
<style>
body {{font:16px system-ui,sans-serif; margin:2rem; color:#172b3a; background:#fafbfc}}
h1 {{font-size:1.6rem}} table {{border-collapse:collapse; background:white}}
th,td {{padding:.65rem; border:1px solid #c9d2db; text-align:left; vertical-align:top}}
th {{background:#e9eff4}} .scroll {{overflow-x:auto}} p {{max-width:75ch}}
</style>
<h1>BMS protection test report</h1>
<p><strong>{passed}/{len(results)} passed.</strong> Simulated time; 10 ms loop, 10 ms telemetry
and 10 ms actuator delays. A PASS requires healthy startup, the expected protection reason,
a timely contactor opening and no reclosing. No physical hardware was tested.</p>
<p>Blank timing values mean no crossing or response was observed. Deadlines come from the
bench specification, independently of the controller configuration. BMS-001's 100 ms limit
is for startup; its normal operation checks span the entire scenario.</p>
<div class="scroll"><table><caption>One verdict per scenario and requirement</caption>
<thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table></div>
<p>See <a href="results.csv">results.csv</a> for verdicts and
<a href="trace.csv">trace.csv</a> for cell ranges, SOC, current, telemetry count and contactor
state at every step. Fault bits: OV=1, UV=2, OT=4, OC=8, imbalance=16, sensor=32.</p>
</html>"""
    (output / "report.html").write_text(document)
