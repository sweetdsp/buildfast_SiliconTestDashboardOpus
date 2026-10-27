"""Data validation layer: clean data passes, each kind of corruption is caught."""

import pytest

from validation.validator import Validator


def _failed_checks(plan, run):
    v = Validator(plan)
    v.validate_run(run)
    return {r["check"] for r in v.report()["failures"]}


def test_committed_data_passes(plan, runs, index):
    v = Validator(plan)
    v.validate_plan()
    v.validate_runs(runs, index)
    report = v.report()
    assert report["status"] == "PASS", report["failures"][:5]
    assert report["checks_total"] > 400


def _bump_failed(run):
    run["domain_summaries"][0]["failed"] += 1


def _break_jira_link(run):
    run["domain_summaries"][0]["failed_tests"][0]["jira_id"] = "BTFW-0000"


def _orphan_blocked(run):
    run["domain_summaries"][0]["blocked_tests"][0]["blocked_by_tc"] = "HCI-LEADV-0001"


def _bump_crashes(run):
    run["domain_summaries"][2]["total_crashes"] += 1


def _wrong_kpi_status(run):
    m = next(m for m in run["domain_summaries"][3]["metrics"] if m["status"] == "PASS")
    m["status"] = "FAIL"


def _bump_severity(run):
    run["jira_telemetry"]["severity_distribution"]["Blocker"] += 1


def _shift_end_time(run):
    run["test_run_metadata"]["end_time"] = run["test_run_metadata"]["start_time"]


def _unknown_test(run):
    run["domain_summaries"][1]["failed_tests"][0]["tc_id"] = "PHN-NOPE-999"


@pytest.mark.parametrize("corrupt, expected", [
    (_bump_failed, "HCI_REGRESSION: passed + failed + blocked = total"),
    (_break_jira_link, "HCI_REGRESSION: every failure is linked to a Jira (100% automation)"),
    (_orphan_blocked, "HCI_REGRESSION: every blocked test depends on a failed gating test"),
    (_bump_crashes, "MTBF: crash totals consistent"),
    (_wrong_kpi_status, "KPI: status agrees with target"),
    (_bump_severity, "Jira: severity distribution sums to total"),
    (_shift_end_time, "metadata: end - start equals actual cycle hours"),
    (_unknown_test, "PHONE_TESTING: every listed test exists in the plan"),
])
def test_corruption_is_detected(plan, latest_run, corrupt, expected):
    corrupt(latest_run)
    assert expected in _failed_checks(plan, latest_run)


def test_cross_run_order(plan, runs, index):
    v = Validator(plan)
    v.validate_runs(list(reversed(runs)), index)
    failed = {r["check"] for r in v.report()["failures"]}
    assert "build indexes are 1..N in order" in failed
