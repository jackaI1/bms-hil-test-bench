"""Run built-in or JSON scenarios: python -m bms_hil --output reports."""

import argparse
from pathlib import Path

from .bench import run_suite
from .report import write_reports
from .scenarios import default_scenarios, load_scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic BMS fault acceptance tests")
    parser.add_argument(
        "--scenarios", type=Path, help="JSON scenario array (default: built-in suite)"
    )
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--cells", type=int, default=8, choices=range(2, 33), metavar="2..32")
    args = parser.parse_args()
    try:
        scenarios = (
            load_scenarios(args.scenarios, cell_count=args.cells)
            if args.scenarios
            else default_scenarios()
        )
        results = run_suite(scenarios, cell_count=args.cells)
        write_reports(results, args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    passed = sum(result.passed for result in results)
    print(f"{passed}/{len(results)} passed; reports: {args.output / 'report.html'}")
    for result in results:
        if not result.passed:
            print(f"FAIL {result.scenario}: {result.detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
