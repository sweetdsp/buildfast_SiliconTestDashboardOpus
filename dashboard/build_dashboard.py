"""Build the static dashboard (dashboard/index.html).

Combines insights.json, narrative.json and per-build series from the run files
into one JSON payload, and injects it into template.html. The result is a single
self-contained file: no server, no external scripts, opens straight from disk.
"""

import json
from pathlib import Path

from generator.common import read_json, resolve

TEMPLATE = Path(__file__).with_name("template.html")
TEST_DOMAINS = ("HCI_REGRESSION", "PHONE_TESTING")


def short_build(name):
    """FW_v14.2.088 -> .088, FW_v14.2.106_RC1 -> RC1."""
    tail = name.split("_")[-1]
    return tail if tail.startswith("RC") else "." + name.rsplit(".", 1)[-1]


def _by_domain(run):
    return {d["domain"]: d for d in run["domain_summaries"]}


def heatmap(runs):
    out = {}
    for dom in TEST_DOMAINS:
        features = list(_by_domain(runs[-1])[dom]["feature_breakdown"])
        rows = []
        for feat in features:
            cells = []
            for r in runs:
                v = _by_domain(r)[dom]["feature_breakdown"][feat]
                cells.append({"fail": v["fail"], "blocked": v["blocked"], "total": v["total"],
                              "rate": round(100 * (v["fail"] + v["blocked"]) / v["total"], 2)})
            latest = _by_domain(runs[-1])[dom]["feature_breakdown"][feat]
            rows.append({"feature": feat, "subsystem": latest["subsystem"], "total": latest["total"], "cells": cells})
        out[dom] = rows
    return out


def kpi_series(runs):
    names = [m["metric_name"] for m in _by_domain(runs[0])["KPI_AUDIO"]["metrics"]]
    series = {}
    for name in names:
        rows = [next(m for m in _by_domain(r)["KPI_AUDIO"]["metrics"] if m["metric_name"] == name) for r in runs]
        series[name] = {"avg": [m["measured_avg"] for m in rows], "status": [m["status"] for m in rows]}
    return series


def latest_kpis(run):
    keep = ("metric_name", "feature", "unit", "target", "comparator", "target_value", "measured_avg",
            "measured_p95", "status", "delta_vs_previous_build", "linked_jira")
    return [{k: m[k] for k in keep} for m in _by_domain(run)["KPI_AUDIO"]["metrics"]]


def build(config):
    insights = read_json(config["paths"]["insights"])
    narrative_path = resolve(config["paths"]["narrative"])
    narrative = read_json(narrative_path) if narrative_path.exists() else None
    runs_dir = resolve(config["paths"]["runs_dir"])
    index = read_json(runs_dir / "index.json")
    runs = [read_json(runs_dir / e["file"]) for e in index["runs"]]
    latest = runs[-1]
    meta = latest["test_run_metadata"]

    payload = {
        "meta": {**{k: meta[k] for k in ("run_id", "firmware_build", "chipset_revision", "start_time", "end_time",
                                          "cycle_type", "test_plan_version")},
                 "runs": len(runs)},
        "builds": [r["test_run_metadata"]["firmware_build"] for r in runs],
        "build_labels": [short_build(r["test_run_metadata"]["firmware_build"]) for r in runs],
        "narrative": narrative,
        "insights": insights,
        "heatmap": heatmap(runs),
        "kpi_series": kpi_series(runs),
        "kpis": latest_kpis(latest),
        "gates_config": config["release"]["gates"],
    }
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DASHBOARD_DATA__*/null", data)
    out = resolve(config["paths"]["dashboard"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
