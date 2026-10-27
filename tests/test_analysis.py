"""Analysis engine: the 5 intelligence outputs and their formulas."""

import copy

import pytest

from analysis import anomalies, clustering, risk, tldr
from analysis.common import domains, impact_score, pearson
from analysis.common import test_totals as totals_of
from analysis.engine import analyse


# ------------------------------------------------------------- 1. TL;DR
def test_pass_rate_covers_hci_and_phone(runs):
    t = totals_of(runs[-1])
    assert t["total_tests"] == 6000
    assert t["pass_rate_percent"] == pytest.approx(100 * t["passed"] / 6000, abs=0.01)


def test_health_index_formula(config, latest_run):
    cfg, gates = config["analysis"], config["release"]["gates"]
    h = tldr.health_index(latest_run, cfg, gates)
    weighted = sum(c["weight"] * c["score"] for c in h["components"].values())
    assert h["score"] == pytest.approx(weighted, abs=0.1)
    assert sum(c["weight"] for c in h["components"].values()) == pytest.approx(1.0)
    assert 0 <= h["score"] <= 100


def test_health_index_perfect_build(config, latest_run):
    """No failures, no open tickets, no crashes, all KPIs pass -> 100 / GREEN."""
    d = domains(latest_run)
    for dom in ("HCI_REGRESSION", "PHONE_TESTING"):
        d[dom]["passed"], d[dom]["failed"], d[dom]["blocked"] = d[dom]["total_tests"], 0, 0
        d[dom]["executed"] = d[dom]["total_tests"]
    d["MTBF"].update(mtbf_hours=None, total_crashes=0, recovery_success_rate_percent=100.0)
    k = d["KPI_AUDIO"]
    k.update(metrics_pass=k["metrics_total"], metrics_degraded=0, metrics_fail=0)
    latest_run["jira_telemetry"]["severity_distribution"] = {s: 0 for s in ("Blocker", "Critical", "Medium", "Trivial")}
    h = tldr.health_index(latest_run, config["analysis"], config["release"]["gates"])
    assert h["score"] == 100.0 and h["band"] == "GREEN"


def test_tldr_reports_cycle_delta(insights, runs):
    m = runs[-1]["test_run_metadata"]
    c = insights["tldr"]["cycle"]
    assert c["baseline_hours"] == 168
    assert c["delta_hours"] == pytest.approx(m["actual_cycle_hours"] - 168, abs=0.05)
    assert len(insights["tldr"]["headline"]) >= 4


# ---------------------------------------------------------------- 2. risk
def test_gates_reflect_latest_build(insights):
    gates = {g["gate"]: g for g in insights["risk"]["gates"]}
    assert gates["Open Blocker Jiras"]["status"] == "FAIL"     # BTFW-4012 is open
    assert insights["risk"]["release_recommendation"] == "NO-GO"


def test_must_fix_set_clears_the_gates(config, latest_run):
    gates = config["release"]["gates"]
    must_fix, remaining_crashes = risk.must_fix_set(latest_run, gates)
    ids = {t["jira_id"] for t in must_fix}
    sev = latest_run["jira_telemetry"]["severity_distribution"]
    assert "BTFW-4012" in ids                                   # the open Blocker
    assert sum(t["severity"] == "Critical" for t in must_fix) >= sev["Critical"] - gates["max_open_criticals"]
    kpi_jiras = {m["linked_jira"] for m in domains(latest_run)["KPI_AUDIO"]["metrics"] if m["linked_jira"]}
    assert kpi_jiras <= ids                                     # KPI breaches are covered
    hours = domains(latest_run)["MTBF"]["total_operational_hours"]
    assert remaining_crashes == 0 or hours / remaining_crashes >= gates["min_mtbf_hours"]


def test_forecast_arithmetic(insights):
    fc = insights["risk"]["forecast"]
    b = fc["breakdown_days"]
    assert b["fix"] == max(fc["fix_days_by_subsystem"].values())
    total = b["fix"] + b["triage"] + b["verification_cycle"] + (b["burn_down_cycles"] or 0)
    from generator.common import parse_time
    span = (parse_time(fc["projected_ready_date"]) - parse_time(fc["target_release_date"])).total_seconds() / 86400
    assert fc["slip_days"] == pytest.approx(span, abs=0.1)
    assert total > 0
    assert fc["schedule_status"] in ("ON_TRACK", "AT_RISK", "OFF_TRACK")


def test_forecast_without_burn_down_is_off_track(config, runs):
    """If failures are not falling, there is no credible ready date."""
    flat = [copy.deepcopy(runs[-1]) for _ in range(4)]
    release_cfg = {**config["release"], "cycle_cadence_days": 9}
    fc = risk.forecast(flat, config["analysis"], config["release"]["gates"], release_cfg)
    assert fc["pass_rate_projection"]["shortfall"] > 0
    assert fc["pass_rate_projection"]["burn_down_velocity_per_build"] == 0
    assert fc["schedule_status"] == "OFF_TRACK" and fc["projected_ready_date"] is None


def test_mtbf_excluding_outliers_is_higher(insights):
    m = insights["risk"]["mtbf"]
    assert m["outlier_duts"] == ["DUT-02"]
    assert m["mtbf_excluding_outliers_hours"] >= m["fleet_mtbf_hours"]


# ------------------------------------------------------------ 3. anomalies
def test_detects_planted_dut_outlier(insights):
    outliers = insights["anomalies"]["dut_variance"]
    assert [o["dut_id"] for o in outliers] == ["DUT-02"]
    assert outliers[0]["ratio_vs_median"] >= 2


def test_no_dut_outlier_when_crashes_are_uniform(config, runs):
    flat = copy.deepcopy(runs)
    for r in flat:
        for d in domains(r)["MTBF"]["dut_details"]:
            d["crashes"] = 2
            d["crash_events"] = []
    outliers, groups, _ = anomalies.dut_variance(flat, config["analysis"])
    assert outliers == [] and groups == []


def test_detects_cross_talk_with_shared_jira(insights):
    talk = [c for c in insights["anomalies"]["cross_talk"] if c["metric"] == "A2DP_Latency_ms"]
    assert talk and talk[0]["common_jira"] == "BTFW-4031"
    assert talk[0]["build"] == "FW_v14.2.104"
    assert any(s["feature"] == "LE_Audio_CIS_BIS" for s in talk[0]["feature_spikes"])


def test_detects_gating_concentration(insights):
    conc = {c["jira_id"]: c for c in insights["anomalies"]["blocked_concentrations"]}
    assert "BTFW-4012" in conc
    assert conc["BTFW-4012"]["blocked_tests"] >= 40


def test_pearson_handles_constant_series():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None


# ------------------------------------------------------------ 4. clusters
def test_subsystem_buckets_cover_every_ticket(insights, runs):
    subs = insights["clusters"]["subsystems"]
    assert sum(s["tickets"] for s in subs) == runs[-1]["jira_telemetry"]["total_tickets_linked"]
    scores = [s["impact_score"] for s in subs]
    assert scores == sorted(scores, reverse=True)


def test_crash_signature_classification(runs):
    cs = clustering.crash_signatures(runs)
    assert cs["active"] == domains(runs[-1])["MTBF"]["unique_crashes"]
    assert cs["recurring"] + cs["single_instance"] == len(cs["signatures"])
    for s in cs["signatures"]:
        expected = "RECURRING" if s["total_occurrences"] > 1 or len(s["builds_seen"]) > 1 else "SINGLE_INSTANCE"
        assert s["classification"] == expected


def test_duplicate_candidates_share_signature(insights, runs):
    by_id = {t["jira_id"]: t for t in runs[-1]["jira_telemetry"]["tickets"]}
    for group in insights["clusters"]["duplicate_candidates"]:
        assert len(group["tickets"]) >= 2
        assert len({by_id[j]["subsystem"] for j in group["tickets"]}) == 1


# -------------------------------------------------------- 5. next actions
def test_actions_sorted_and_assigned(insights):
    actions = insights["next_actions"]["actions"]
    order = [a["priority"] for a in actions]
    assert order == sorted(order)
    assert all(a["team"] for a in actions)
    first = actions[0]
    assert first["priority"] == "P0" and "BTFW-4012" in first["action"] and "unblock" in first["action"]


def test_rerun_subsets_are_targeted(insights, runs):
    subsets = insights["next_actions"]["rerun_subsets"]
    full = runs[-1]["test_run_metadata"]["actual_cycle_hours"]
    by_id = {t["jira_id"]: t for t in runs[-1]["jira_telemetry"]["tickets"]}
    for s in subsets:
        ids = set(s["tests"]["HCI_REGRESSION"]) | set(s["tests"]["PHONE_TESTING"])
        assert len(ids) == s["test_count"] < 6000
        assert s["estimated_hours"] < full
        if len(s["jira_ids"]) == 1:
            t = by_id[s["jira_ids"][0]]
            assert set(t["linked_failed_tests"]) | set(t["linked_blocked_tests"]) <= ids


def test_impact_score():
    t = {"linked_failed_tests": [1, 2], "linked_blocked_tests": [3], "crash_occurrences": 2, "kpi_metrics": ["x"]}
    assert impact_score(t) == 2 + 1 + 10 + 10


def test_analysis_is_deterministic(config, plan, runs, insights):
    assert analyse(config, plan, runs) == insights
