"""Output 1 - Automated insight synthesis (TL;DR) and the build health index."""

from analysis.common import clamp, domains, meta, test_totals


def health_index(run, cfg, gates):
    """Weighted 0-100 score: test pass rate, open defect load, MTBF stability, KPI compliance."""
    h = cfg["health_index"]
    d = domains(run)
    totals = test_totals(run)
    sev = run["jira_telemetry"]["severity_distribution"]
    mtbf, kpi = d["MTBF"], d["KPI_AUDIO"]

    floor = h["pass_rate_floor"]
    pass_score = clamp((totals["pass_rate_percent"] - floor) / (100 - floor) * 100)
    penalty = sum(h["defect_penalty"][s] * n for s, n in sev.items())
    defect_score = clamp(100 - penalty)
    if mtbf["mtbf_hours"] is None:
        mtbf_score = 100.0
    else:
        mtbf_score = clamp(mtbf["mtbf_hours"] / gates["min_mtbf_hours"] * 100)
    mtbf_score *= mtbf["recovery_success_rate_percent"] / 100
    kpi_score = 100 * (kpi["metrics_pass"] + 0.5 * kpi["metrics_degraded"]) / kpi["metrics_total"]

    components = {
        "pass_rate": {"score": pass_score, "detail": f"{totals['pass_rate_percent']}% pass rate (floor {floor}%)"},
        "defects": {"score": defect_score,
                    "detail": f"{sev['Blocker']} Blocker, {sev['Critical']} Critical, {sev['Medium']} Medium, "
                              f"{sev['Trivial']} Trivial open (penalty {penalty:.1f})"},
        "mtbf": {"score": mtbf_score,
                 "detail": f"MTBF {mtbf['mtbf_hours']} h vs {gates['min_mtbf_hours']} h gate, "
                           f"{mtbf['recovery_success_rate_percent']}% auto-recovery"},
        "kpi": {"score": kpi_score,
                "detail": f"{kpi['metrics_pass']}/{kpi['metrics_total']} KPIs pass, {kpi['metrics_degraded']} degraded"},
    }
    score = 0.0
    for name, comp in components.items():
        comp["weight"] = h["weights"][name]
        comp["score"] = round(comp["score"], 1)
        score += comp["weight"] * comp["score"]
    score = round(score, 1)
    band = "GREEN" if score >= h["bands"]["green"] else "AMBER" if score >= h["bands"]["amber"] else "RED"
    return {"score": score, "band": band, "components": components}


def synthesize(runs, cfg, gates, health_by_build):
    latest, prev = runs[-1], (runs[-2] if len(runs) > 1 else None)
    m, d = meta(latest), domains(latest)
    totals = test_totals(latest)
    prev_totals = test_totals(prev) if prev else None
    health = health_by_build[-1]
    prev_health = health_by_build[-2] if prev else None
    mtbf, kpi = d["MTBF"], d["KPI_AUDIO"]
    sev = latest["jira_telemetry"]["severity_distribution"]
    critical = d[m["critical_path_domain"]]
    breached = [x for x in kpi["metrics"] if x["status"] != "PASS"]

    cycle = {
        "actual_hours": m["actual_cycle_hours"],
        "actual_days": round(m["actual_cycle_hours"] / 24, 2),
        "baseline_hours": m["baseline_cycle_hours"],
        "delta_hours": m["cycle_delta_hours"],
        "delta_percent": round(100 * m["cycle_delta_hours"] / m["baseline_cycle_hours"], 1),
        "critical_path_domain": m["critical_path_domain"],
        "overhead_breakdown_hours": critical.get("duration_overhead_hours", {}),
    }

    pr_delta = round(totals["pass_rate_percent"] - prev_totals["pass_rate_percent"], 2) if prev else None
    h_delta = round(health["score"] - prev_health["score"], 1) if prev else None
    headline = [
        f"{m['firmware_build']}: health index {health['score']}/100 ({health['band']})"
        + (f", {h_delta:+} vs {meta(prev)['firmware_build']}." if prev else "."),
        f"Pass rate {totals['pass_rate_percent']}% across {totals['total_tests']:,} HCI + phone tests "
        f"({totals['failed']} failed, {totals['blocked']} blocked)"
        + (f", {pr_delta:+} pts vs previous build." if prev else "."),
        f"Full cycle took {cycle['actual_days']} days ({cycle['actual_hours']} h), {cycle['delta_hours']:+} h over the "
        f"7-day baseline due to failure re-runs; critical path {cycle['critical_path_domain']}.",
        f"MTBF {mtbf['mtbf_hours']} h per DUT with {mtbf['total_crashes']} crashes "
        f"({mtbf['unique_crashes']} unique signatures); {sev['Blocker']} Blocker and {sev['Critical']} Critical Jiras open.",
    ]
    if breached:
        headline.append("KPI breaches: " + ", ".join(
            f"{x['metric_name']} {x['measured_avg']} vs target {x['target']} ({x['status']})" for x in breached) + ".")

    return {
        "run_id": m["run_id"],
        "firmware_build": m["firmware_build"],
        "previous_firmware_build": m["previous_firmware_build"],
        "test_totals": totals,
        "pass_rate_delta_pts": pr_delta,
        "domain_pass_rates": {dom: d[dom]["pass_rate_percent"] for dom in ("HCI_REGRESSION", "PHONE_TESTING")},
        "cycle": cycle,
        "health_index": health,
        "health_delta": h_delta,
        "mtbf_hours": mtbf["mtbf_hours"],
        "total_crashes": mtbf["total_crashes"],
        "unique_crashes": mtbf["unique_crashes"],
        "open_blockers": sev["Blocker"],
        "open_criticals": sev["Critical"],
        "kpi_summary": {"pass": kpi["metrics_pass"], "degraded": kpi["metrics_degraded"], "fail": kpi["metrics_fail"],
                        "breached": [x["metric_name"] for x in breached]},
        "headline": headline,
    }
