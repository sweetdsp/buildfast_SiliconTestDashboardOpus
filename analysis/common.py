"""Shared accessors and small statistics helpers for the analysis modules."""

import statistics

SEVERITIES = ["Blocker", "Critical", "Medium", "Trivial"]
SEVERITY_WEIGHT = {"Blocker": 10, "Critical": 5, "Medium": 2, "Trivial": 1}
TEST_DOMAINS = ("HCI_REGRESSION", "PHONE_TESTING")

# Developer role that owns each firmware subsystem (used for next actions).
TEAM_BY_SUBSYSTEM = {
    "HCI": "HCI / Transport Firmware",
    "LMP": "BR/EDR Controller Firmware",
    "BASEBAND": "BR/EDR Controller Firmware",
    "LINK_LAYER": "LE Controller Firmware",
    "PHY_RF": "RF / PHY",
    "AUDIO_CODEC": "Audio / Codec Firmware",
    "SECURITY": "Security Firmware",
    "HOST_STACK": "Host Stack",
    "POWER_MGMT": "Power Management Firmware",
    "COEX": "Coexistence Firmware",
}


def domains(run):
    return {d["domain"]: d for d in run["domain_summaries"]}


def meta(run):
    return run["test_run_metadata"]


def tickets(run):
    return run["jira_telemetry"]["tickets"]


def test_totals(run):
    """Combined HCI + phone counts (the scope of the overall pass rate)."""
    d = domains(run)
    out = {k: sum(d[dom][k] for dom in TEST_DOMAINS) for k in ("total_tests", "passed", "failed", "blocked", "executed")}
    out["pass_rate_percent"] = round(100 * out["passed"] / out["total_tests"], 2)
    out["executed_pass_rate_percent"] = round(100 * out["passed"] / out["executed"], 2) if out["executed"] else 0.0
    return out


def impact_score(t):
    """How much of the test campaign a ticket holds back."""
    return (len(t["linked_failed_tests"]) + len(t["linked_blocked_tests"])
            + 5 * t["crash_occurrences"] + 10 * len(t["kpi_metrics"]))


def ticket_brief(t):
    return {
        "jira_id": t["jira_id"], "title": t["title"], "severity": t["severity"], "subsystem": t["subsystem"],
        "status": t["status"], "first_seen_build": t["first_seen_build"], "builds_open": t["builds_open"],
        "failed_tests": len(t["linked_failed_tests"]), "blocked_tests": len(t["linked_blocked_tests"]),
        "crash_occurrences": t["crash_occurrences"], "affected_duts": t["affected_duts"],
        "kpi_metrics": t["kpi_metrics"], "impact_score": impact_score(t),
    }


def clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def pearson(xs, ys):
    try:
        return round(statistics.correlation(xs, ys), 3)
    except statistics.StatisticsError:  # constant series
        return None


def fmt_list(items, limit=3):
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" +{len(items) - limit} more"
