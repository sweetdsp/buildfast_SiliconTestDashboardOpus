"""Analysis engine: runs the 5 intelligence modules and assembles insights.json."""

from analysis import anomalies, clustering, next_actions, risk, tldr
from analysis.common import domains, meta, test_totals

INSIGHTS_VERSION = "1.0"


def trends(runs, health_by_build):
    rows = []
    for r, h in zip(runs, health_by_build):
        m, d, t = meta(r), domains(r), test_totals(r)
        sev = r["jira_telemetry"]["severity_distribution"]
        rows.append({
            "firmware_build": m["firmware_build"], "run_id": m["run_id"], "end_time": m["end_time"],
            "health_index": h["score"], "health_band": h["band"],
            "pass_rate_percent": t["pass_rate_percent"], "failed": t["failed"], "blocked": t["blocked"],
            "hci_failed": d["HCI_REGRESSION"]["failed"], "phone_failed": d["PHONE_TESTING"]["failed"],
            "cycle_hours": m["actual_cycle_hours"], "cycle_delta_hours": m["cycle_delta_hours"],
            "crashes": d["MTBF"]["total_crashes"], "unique_crashes": d["MTBF"]["unique_crashes"],
            "mtbf_hours": d["MTBF"]["mtbf_hours"],
            "kpi_pass": d["KPI_AUDIO"]["metrics_pass"], "kpi_total": d["KPI_AUDIO"]["metrics_total"],
            "open_blockers": sev["Blocker"], "open_criticals": sev["Critical"],
            "tickets": r["jira_telemetry"]["total_tickets_linked"],
            "resolved_since_previous": len(r["jira_telemetry"]["resolved_since_previous_build"]),
        })
    return rows


def analyse(config, plan, runs):
    cfg = config["analysis"]
    gates = config["release"]["gates"]
    release_cfg = {**config["release"], "cycle_cadence_days": config["runs"]["cycle_cadence_days"]}

    health_by_build = [tldr.health_index(r, cfg, gates) for r in runs]
    anomaly_out = anomalies.detect(runs, cfg)
    risk_out = risk.assess(runs, cfg, gates, release_cfg, anomaly_out["outlier_duts"])
    cluster_out = clustering.cluster(runs, cfg)
    return {
        "insights_version": INSIGHTS_VERSION,
        "generated_from": {
            "latest_run_id": meta(runs[-1])["run_id"],
            "firmware_build": meta(runs[-1])["firmware_build"],
            "chipset_revision": meta(runs[-1])["chipset_revision"],
            "runs_analysed": [meta(r)["run_id"] for r in runs],
            "test_plan_version": plan["test_plan_version"],
        },
        "tldr": tldr.synthesize(runs, cfg, gates, health_by_build),
        "risk": risk_out,
        "anomalies": anomaly_out,
        "clusters": cluster_out,
        "next_actions": next_actions.generate(runs, plan, cfg, risk_out, anomaly_out, cluster_out),
        "trends": trends(runs, health_by_build),
    }
