"""Output 2 - Risk assessment & milestone forecasting.

Release gates, the gate-driven must-fix set, schedule slippage and MTBF stability.

Slippage model (all inputs in config `analysis.slippage`):
  ready date = last run end
             + fix time      (must-fix tickets; teams work in parallel, serially within a subsystem)
             + triage time   (failed tests x triage hours / team capacity)
             + verification  (one full cycle at the current cycle length)
             + burn-down     (extra fix cycles if the pass-rate gate still fails after the must-fix set,
                              at the failure burn-down velocity of recent builds)
  slip = ready date - target date (last run end + target_offset_days)
"""

import math
from datetime import timedelta

from generator.common import fmt_time, parse_time
from analysis.common import (TEST_DOMAINS, domains, impact_score, meta, test_totals, ticket_brief, tickets)


def evaluate_gates(run, gates):
    totals = test_totals(run)
    sev = run["jira_telemetry"]["severity_distribution"]
    d = domains(run)
    mtbf, kpi = d["MTBF"]["mtbf_hours"], d["KPI_AUDIO"]
    breached = [m["metric_name"] for m in kpi["metrics"] if m["status"] != "PASS"]
    rows = [
        ("Open Blocker Jiras", f"<= {gates['max_open_blockers']}", sev["Blocker"], sev["Blocker"] <= gates["max_open_blockers"]),
        ("Open Critical Jiras", f"<= {gates['max_open_criticals']}", sev["Critical"], sev["Critical"] <= gates["max_open_criticals"]),
        ("Overall pass rate %", f">= {gates['min_pass_rate_percent']}", totals["pass_rate_percent"],
         totals["pass_rate_percent"] >= gates["min_pass_rate_percent"]),
        ("MTBF hours per DUT", f">= {gates['min_mtbf_hours']}", mtbf, mtbf is None or mtbf >= gates["min_mtbf_hours"]),
        ("KPI metrics passing", "all" if gates["all_kpis_pass"] else "n/a",
         f"{kpi['metrics_pass']}/{kpi['metrics_total']}", not (gates["all_kpis_pass"] and breached)),
    ]
    return [{"gate": g, "threshold": th, "actual": act, "status": "PASS" if ok else "FAIL"} for g, th, act, ok in rows]


def must_fix_set(run, gates):
    """Smallest ticket set (by impact) that the gates require to be fixed before release."""
    open_t = tickets(run)
    chosen = {t["jira_id"]: "Blocker gate" for t in open_t if t["severity"] == "Blocker"}
    crits = sorted((t for t in open_t if t["severity"] == "Critical"), key=impact_score, reverse=True)
    for t in crits[: max(0, len(crits) - gates["max_open_criticals"])]:
        chosen[t["jira_id"]] = "Critical gate"

    d = domains(run)
    if gates["all_kpis_pass"]:
        for m in d["KPI_AUDIO"]["metrics"]:
            if m["linked_jira"]:
                chosen.setdefault(m["linked_jira"], "KPI gate")

    # MTBF gate: add crash tickets (most occurrences first) until projected MTBF clears it.
    mtbf = d["MTBF"]
    by_id = {t["jira_id"]: t for t in open_t}
    remaining = mtbf["total_crashes"] - sum(by_id[j]["crash_occurrences"] for j in chosen)
    for t in sorted((t for t in open_t if t["crash_occurrences"]), key=lambda t: -t["crash_occurrences"]):
        if remaining == 0 or mtbf["total_operational_hours"] / remaining >= gates["min_mtbf_hours"]:
            break
        if t["jira_id"] not in chosen:
            chosen[t["jira_id"]] = "MTBF gate"
            remaining -= t["crash_occurrences"]
    return [{**ticket_brief(by_id[j]), "required_by": why} for j, why in chosen.items()], remaining


def forecast(runs, cfg, gates, release_cfg):
    latest = runs[-1]
    s = cfg["slippage"]
    m = meta(latest)
    end = parse_time(m["end_time"])
    target = end + timedelta(days=release_cfg["target_offset_days"])
    must_fix, projected_crashes = must_fix_set(latest, gates)

    per_team = {}
    for t in must_fix:
        per_team[t["subsystem"]] = per_team.get(t["subsystem"], 0) + s["fix_days"][t["severity"]]
    fix_days = max(per_team.values(), default=0)

    totals = test_totals(latest)
    triage_days = totals["failed"] * s["triage_hours_per_failure"] / s["triage_capacity_hours_per_day"]
    verify_days = m["actual_cycle_hours"] / 24

    # Pass-rate gate after the must-fix set lands.
    non_pass = totals["failed"] + totals["blocked"]
    cleared = sum(t["failed_tests"] + t["blocked_tests"] for t in must_fix)
    projected_non_pass = non_pass - cleared
    allowed = math.floor(totals["total_tests"] * (1 - gates["min_pass_rate_percent"] / 100))
    shortfall = max(0, projected_non_pass - allowed)
    window = runs[-(s["burn_down_window_builds"] + 1):]
    series = [test_totals(r)["failed"] + test_totals(r)["blocked"] for r in window]
    velocity = (series[0] - series[-1]) / (len(series) - 1) if len(series) > 1 else 0
    cadence = release_cfg.get("cycle_cadence_days", 9)
    if shortfall == 0:
        extra_cycles = 0
    elif velocity > 0:
        extra_cycles = math.ceil(shortfall / velocity)
    else:
        extra_cycles = None  # failures are not burning down: no credible forecast
    extra_days = extra_cycles * cadence if extra_cycles is not None else None

    if extra_days is None:
        ready, slip, status = None, None, "OFF_TRACK"
    else:
        total_days = fix_days + triage_days + verify_days + extra_days
        ready = end + timedelta(days=total_days)
        slip = round((ready - target).total_seconds() / 86400, 1)
        status = "ON_TRACK" if slip <= 0 else "AT_RISK" if slip <= s["at_risk_slip_days"] else "OFF_TRACK"

    return {
        "target_release_date": fmt_time(target),
        "projected_ready_date": fmt_time(ready) if ready else None,
        "slip_days": slip,
        "schedule_status": status,
        "breakdown_days": {
            "fix": round(fix_days, 1), "triage": round(triage_days, 1), "verification_cycle": round(verify_days, 1),
            "burn_down_cycles": extra_days,
        },
        "fix_days_by_subsystem": per_team,
        "pass_rate_projection": {
            "current_non_pass": non_pass, "cleared_by_must_fix": cleared, "projected_non_pass": projected_non_pass,
            "allowed_non_pass": allowed, "shortfall": shortfall,
            "burn_down_velocity_per_build": round(velocity, 1), "extra_fix_cycles": extra_cycles,
        },
        "must_fix": must_fix,
        "projected_crashes_after_fixes": projected_crashes,
    }


def mtbf_stability(runs, gates, outlier_duts):
    latest = domains(runs[-1])["MTBF"]
    duts = latest["dut_details"]
    rest = [d for d in duts if d["dut_id"] not in outlier_duts]
    rest_crashes = sum(d["crashes"] for d in rest)
    rest_hours = sum(d["operational_hours"] for d in rest)
    return {
        "fleet_mtbf_hours": latest["mtbf_hours"],
        "gate_hours": gates["min_mtbf_hours"],
        "meets_gate": latest["mtbf_hours"] is None or latest["mtbf_hours"] >= gates["min_mtbf_hours"],
        "recovery_success_rate_percent": latest["recovery_success_rate_percent"],
        "mtbf_excluding_outliers_hours": round(rest_hours / rest_crashes, 1) if rest_crashes else None,
        "outlier_duts": outlier_duts,
        "per_dut": [{"dut_id": d["dut_id"], "board_rev": d["board_rev"], "rf_module_lot": d["rf_module_lot"],
                     "bench": d["bench"], "crashes": d["crashes"], "mtbf_hours": d["mtbf_hours"],
                     "downtime_hours": d["downtime_hours"]} for d in duts],
        "trend": [{"firmware_build": meta(r)["firmware_build"], "mtbf_hours": domains(r)["MTBF"]["mtbf_hours"],
                   "crashes": domains(r)["MTBF"]["total_crashes"],
                   "unique_crashes": domains(r)["MTBF"]["unique_crashes"]} for r in runs],
    }


def assess(runs, cfg, gates, release_cfg, outlier_duts):
    latest = runs[-1]
    gate_rows = evaluate_gates(latest, gates)
    release_blockers = sorted((ticket_brief(t) for t in tickets(latest) if t["severity"] in ("Blocker", "Critical")),
                              key=lambda t: (t["severity"] != "Blocker", -t["impact_score"]))
    fc = forecast(runs, cfg, gates, release_cfg)
    failing = [g["gate"] for g in gate_rows if g["status"] == "FAIL"]
    return {
        "gates": gate_rows,
        "gates_passed": len(gate_rows) - len(failing),
        "gates_total": len(gate_rows),
        "release_recommendation": "GO" if not failing else "NO-GO",
        "release_blockers": release_blockers,
        "forecast": fc,
        "mtbf": mtbf_stability(runs, gates, outlier_duts),
        "test_domains_scope": list(TEST_DOMAINS),
    }
