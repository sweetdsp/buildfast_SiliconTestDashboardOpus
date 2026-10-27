"""Mock data engine: the one-time test plan and the sample test run generator."""

import json

import pytest

from generator import common
from generator.run_generator import SampleTestRunGenerator
from generator.test_plan_generator import TestPlanGenerator


def _dump(obj):
    return json.dumps(obj, sort_keys=True)


# ------------------------------------------------------------------ common
def test_apportion_preserves_total():
    assert sum(common.apportion(5000, [280, 200, 320, 7])) == 5000
    assert common.apportion(10, [1, 1]) == [5, 5]


def test_lerp_endpoints():
    assert common.lerp(10, 2, 0, 5) == 10
    assert common.lerp(10, 2, 4, 5) == 2
    assert common.lerp(10, 2, 0, 1) == 2


def test_poisson_is_seeded():
    import random
    a = [common.poisson(random.Random(1), 3.0) for _ in range(5)]
    b = [common.poisson(random.Random(1), 3.0) for _ in range(5)]
    assert a == b
    assert common.poisson(random.Random(1), 0) == 0


# --------------------------------------------------------------- test plan
def test_plan_matches_spec_sizes(plan):
    d = plan["domains"]
    assert d["HCI_REGRESSION"]["total_tests"] == 5000
    assert d["HCI_REGRESSION"]["feature_count"] == 25
    assert d["PHONE_TESTING"]["total_tests"] == 1000
    assert d["PHONE_TESTING"]["feature_count"] == 15
    assert len(d["MTBF"]["duts"]) == 16
    assert d["MTBF"]["total_planned_hours"] == 16 * 120
    assert d["HCI_REGRESSION"]["baseline_hours"] == 168
    assert d["PHONE_TESTING"]["baseline_hours"] == 24


def test_plan_gating_structure(plan):
    for dom in ("HCI_REGRESSION", "PHONE_TESTING"):
        tests = {t["tc_id"]: t for t in plan["domains"][dom]["test_cases"]}
        for t in tests.values():
            if t["is_gating"]:
                assert t["depends_on"] is None and t["priority"] == "P0"
            else:
                gate = tests[t["depends_on"]]
                assert gate["is_gating"] and gate["suite_id"] == t["suite_id"]


def test_plan_durations_fit_baseline(plan):
    for dom in ("HCI_REGRESSION", "PHONE_TESTING"):
        d = plan["domains"][dom]
        hours = sum(t["expected_duration_min"] for t in d["test_cases"]) / d["parallel_lanes"] / 60
        assert hours == pytest.approx(d["baseline_hours"], abs=0.5)


def test_plan_regenerates_identically(config, plan):
    """The committed plan is exactly what the seeded generator produces."""
    assert _dump(TestPlanGenerator(config).generate()) == _dump(plan)


# -------------------------------------------------------------------- runs
@pytest.fixture(scope="module")
def regenerated(config, plan):
    return SampleTestRunGenerator(config, plan).generate_all()


def test_runs_regenerate_identically(regenerated, runs):
    """Deterministic: the committed run files match a fresh generation."""
    assert len(regenerated) == len(runs)
    for new, old in zip(regenerated, runs):
        assert _dump(new) == _dump(old)


def test_runs_one_per_build(config, runs):
    assert [r["test_run_metadata"]["firmware_build"] for r in runs] == config["runs"]["firmware_builds"]


def test_crash_signatures_converge(runs):
    """Spec 3.1: at least 10 crashes early on, falling over later builds."""
    mtbf = [next(d for d in r["domain_summaries"] if d["domain"] == "MTBF") for r in runs]
    assert mtbf[0]["unique_crashes"] >= 10
    assert mtbf[-1]["unique_crashes"] < mtbf[0]["unique_crashes"]
    assert mtbf[-1]["total_crashes"] < mtbf[0]["total_crashes"]


def test_elastic_duration(runs):
    """Spec 2.2: failures extend the cycle beyond the 7-day baseline."""
    for r in runs:
        m = r["test_run_metadata"]
        assert m["actual_cycle_hours"] > m["baseline_cycle_hours"] == 168
    hci = [r["domain_summaries"][0] for r in runs]
    worst = max(hci, key=lambda d: d["failed"])
    best = min(hci, key=lambda d: d["failed"])
    assert worst["execution_duration_hours"] > best["execution_duration_hours"]


def test_every_failure_has_a_jira(runs):
    for r in runs:
        ids = {t["jira_id"] for t in r["jira_telemetry"]["tickets"]}
        for d in r["domain_summaries"][:2]:
            assert all(f["jira_id"] in ids for f in d["failed_tests"])


def test_planted_lc3_gating_bug(runs):
    """BTFW-4012 blocks ~45 LE Audio tests while it is open (spec example)."""
    latest = runs[-1]["domain_summaries"][0]
    blocked = [b for b in latest["blocked_tests"] if b["blocking_jira"] == "BTFW-4012"]
    assert 35 <= len(blocked) <= 50
    assert {b["feature"] for b in blocked} == {"LE_Audio_CIS_BIS"}
