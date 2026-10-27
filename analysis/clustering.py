"""Output 4 - Semantic bug clustering.

  * Root-cause buckets by firmware subsystem, ranked by impact
  * Candidate duplicates: separate tickets sharing a subsystem and failure signature
  * Recurring crash signatures vs single-instance crashes (across all builds)
  * Persistent test failures vs one-off failures
"""

import re

from analysis.common import (SEVERITIES, SEVERITY_WEIGHT, TEAM_BY_SUBSYSTEM, TEST_DOMAINS, domains, meta,
                             tickets)
from generator.catalog import SUBSYSTEMS


def _normalise(signature):
    """Collapse numbers and case so near-identical signatures group together."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", signature.lower())).strip()


def by_subsystem(run):
    buckets = {}
    for t in tickets(run):
        b = buckets.setdefault(t["subsystem"], {
            "subsystem": t["subsystem"], "subsystem_name": SUBSYSTEMS[t["subsystem"]],
            "owner_team": TEAM_BY_SUBSYSTEM[t["subsystem"]], "tickets": 0,
            "severity": {s: 0 for s in SEVERITIES}, "failed_tests": 0, "blocked_tests": 0,
            "crash_occurrences": 0, "features": {}, "jira_ids": [], "signatures": {}})
        b["tickets"] += 1
        b["severity"][t["severity"]] += 1
        b["failed_tests"] += len(t["linked_failed_tests"])
        b["blocked_tests"] += len(t["linked_blocked_tests"])
        b["crash_occurrences"] += t["crash_occurrences"]
        b["jira_ids"].append(t["jira_id"])
        b["signatures"][t["signature"]] = b["signatures"].get(t["signature"], 0) + 1
    feature_of = {f["tc_id"]: f["feature"] for dom in TEST_DOMAINS for f in domains(run)[dom]["failed_tests"]}
    for t in tickets(run):
        feats = buckets[t["subsystem"]]["features"]
        for tc in t["linked_failed_tests"]:
            feats[feature_of[tc]] = feats.get(feature_of[tc], 0) + 1
    for b in buckets.values():
        b["impact_score"] = round(sum(SEVERITY_WEIGHT[s] * n for s, n in b["severity"].items())
                                  + 0.2 * (b["failed_tests"] + b["blocked_tests"]) + b["crash_occurrences"], 1)
        b["top_features"] = sorted(b.pop("features").items(), key=lambda kv: -kv[1])[:5]
        b["top_signatures"] = sorted(b.pop("signatures").items(), key=lambda kv: -kv[1])[:5]
    return sorted(buckets.values(), key=lambda b: -b["impact_score"])


def duplicate_candidates(run):
    groups = {}
    for t in tickets(run):
        groups.setdefault((t["subsystem"], _normalise(t["signature"])), []).append(t)
    out = []
    for (subsystem, _), ts in groups.items():
        if len(ts) < 2:
            continue
        out.append({
            "subsystem": subsystem, "signature": ts[0]["signature"], "tickets": [t["jira_id"] for t in ts],
            "failed_tests": sum(len(t["linked_failed_tests"]) for t in ts),
            "summary": f"{len(ts)} {subsystem} tickets share the signature '{ts[0]['signature']}' - "
                       f"likely one root cause; triage together.",
        })
    return sorted(out, key=lambda c: -len(c["tickets"]))


def crash_signatures(runs):
    sigs = {}
    latest_build = meta(runs[-1])["firmware_build"]
    for r in runs:
        build = meta(r)["firmware_build"]
        for c in r["jira_telemetry"]["crash_mapping"]:
            s = sigs.setdefault(c["crash_signature"], {
                "crash_signature": c["crash_signature"], "jira_id": c["jira_id"], "severity": c["severity"],
                "subsystem": c["subsystem"], "stack_trace_top_frames": c["stack_trace_top_frames"],
                "builds_seen": [], "total_occurrences": 0, "affected_duts": set(), "occurrences_by_build": {}})
            s["builds_seen"].append(build)
            s["total_occurrences"] += c["occurrences"]
            s["affected_duts"].update(c["affected_duts"])
            s["occurrences_by_build"][build] = c["occurrences"]
    out = []
    for s in sigs.values():
        s["affected_duts"] = sorted(s["affected_duts"])
        s["status"] = "ACTIVE" if latest_build in s["builds_seen"] else "RESOLVED"
        s["classification"] = ("RECURRING" if s["total_occurrences"] > 1 or len(s["builds_seen"]) > 1
                               else "SINGLE_INSTANCE")
        s["last_seen_build"] = s["builds_seen"][-1]
        out.append(s)
    out.sort(key=lambda s: (s["status"] != "ACTIVE", -s["total_occurrences"]))
    return {
        "recurring": sum(s["classification"] == "RECURRING" for s in out),
        "single_instance": sum(s["classification"] == "SINGLE_INSTANCE" for s in out),
        "active": sum(s["status"] == "ACTIVE" for s in out),
        "resolved": sum(s["status"] == "RESOLVED" for s in out),
        "signatures": out,
    }


def failure_persistence(runs, cfg):
    k = cfg["clustering"]["persistent_failure_builds"]
    failed_sets = [{f["tc_id"] for dom in TEST_DOMAINS for f in domains(r)[dom]["failed_tests"]} for r in runs]
    latest = runs[-1]
    streaks, by_jira = {}, {}
    for dom in TEST_DOMAINS:
        for f in domains(latest)[dom]["failed_tests"]:
            n = 0
            for s in reversed(failed_sets):
                if f["tc_id"] not in s:
                    break
                n += 1
            streaks[f["tc_id"]] = n
            if n >= k:
                j = by_jira.setdefault(f["jira_id"], {"jira_id": f["jira_id"], "tests": 0, "max_streak": 0})
                j["tests"] += 1
                j["max_streak"] = max(j["max_streak"], n)
    return {
        "persistent_threshold_builds": k,
        "persistent_failures": sum(n >= k for n in streaks.values()),
        "new_failures": sum(n == 1 for n in streaks.values()),
        "intermittent_or_short": sum(1 < n < k for n in streaks.values()),
        "persistent_by_jira": sorted(by_jira.values(), key=lambda j: -j["tests"]),
    }


def cluster(runs, cfg):
    latest = runs[-1]
    t = tickets(latest)
    origin = {o: sum(x["origin"] == o for x in t) for o in ("TRACKED", "AUTO_MINOR", "SINGLE_INSTANCE")}
    return {
        "subsystems": by_subsystem(latest),
        "duplicate_candidates": duplicate_candidates(latest),
        "crash_signatures": crash_signatures(runs),
        "failure_persistence": failure_persistence(runs, cfg),
        "ticket_origin": origin,
    }
