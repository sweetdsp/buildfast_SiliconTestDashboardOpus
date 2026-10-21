"""Bluetooth Test Infrastructure Dashboard Agent - pipeline entry point.

Stages (more are added as the project progresses):
  plan  - one-time test plan generator   -> data/test_plan.json
  runs  - sample test run generator      -> data/runs/*.json

Usage:
  python run_pipeline.py                 # all stages; reuses an existing test plan
  python run_pipeline.py --regen-plan    # force a new test plan
  python run_pipeline.py --stage runs
"""

import argparse
import sys

from generator.common import DEFAULT_CONFIG, load_config, read_json, resolve, write_json
from generator.run_generator import SampleTestRunGenerator, write_runs
from generator.test_plan_generator import TestPlanGenerator

STAGES = ["plan", "runs"]


def stage_plan(config, regen):
    path = resolve(config["paths"]["test_plan"])
    if path.exists() and not regen:
        print(f"[plan] reusing existing test plan {path.relative_to(resolve('.'))} (use --regen-plan to rebuild)")
        return
    plan = TestPlanGenerator(config).generate()
    write_json(path, plan)
    d = plan["domains"]
    print(f"[plan] wrote {path.name}: {d['HCI_REGRESSION']['total_tests']} HCI tests / "
          f"{d['HCI_REGRESSION']['feature_count']} features, {d['PHONE_TESTING']['total_tests']} phone tests / "
          f"{d['PHONE_TESTING']['feature_count']} features, {len(d['MTBF']['duts'])} DUTs, "
          f"{len(d['KPI_AUDIO']['metrics'])} KPI metrics")


def stage_runs(config):
    plan = read_json(config["paths"]["test_plan"])
    runs = SampleTestRunGenerator(config, plan).generate_all()
    out = write_runs(config, runs)
    print(f"[runs] wrote {len(runs)} runs to {out.relative_to(resolve('.'))}/")
    print(f"  {'build':<18}{'cycle h':>8}{'HCI f/b':>10}{'PHN f/b':>9}{'crash/uniq':>11}"
          f"{'MTBF h':>8}{'KPI ok':>7}{'Jiras':>7}{'B/C':>6}")
    for r in runs:
        m, (hci, phn, mtbf, kpi), j = r["test_run_metadata"], r["domain_summaries"], r["jira_telemetry"]
        sev = j["severity_distribution"]
        print(f"  {m['firmware_build']:<18}{m['actual_cycle_hours']:>8}"
              f"{hci['failed']:>6}/{hci['blocked']:<3}{phn['failed']:>5}/{phn['blocked']:<3}"
              f"{mtbf['total_crashes']:>7}/{mtbf['unique_crashes']:<3}{str(mtbf['mtbf_hours']):>8}"
              f"{kpi['metrics_pass']:>4}/{kpi['metrics_total']:<2}{j['total_tickets_linked']:>7}"
              f"{sev['Blocker']:>3}/{sev['Critical']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--stage", choices=STAGES + ["all"], default="all")
    parser.add_argument("--regen-plan", action="store_true", help="rebuild the one-time test plan")
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.stage in ("plan", "all"):
        stage_plan(config, args.regen_plan)
    if args.stage in ("runs", "all"):
        stage_runs(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
