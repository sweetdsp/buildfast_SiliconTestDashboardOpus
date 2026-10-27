"""Output 3 - Anomaly detection & blind-spot flagging.

  * DUT-specific crash variance and hardware-group (board / RF lot / bench) effects
  * Feature degradation cross-talk: KPI step changes that coincide with feature failure spikes
  * Blocked-test concentrations behind gating bugs, and persistent coverage gaps
  * New high-severity regressions in the latest build
"""

import statistics

from analysis.common import TEST_DOMAINS, domains, fmt_list, meta, pearson, tickets


def dut_variance(runs, cfg):
    a = cfg["anomalies"]
    fleet = domains(runs[-1])["MTBF"]["dut_details"]
    attrs = {d["dut_id"]: d for d in fleet}
    totals = {d["dut_id"]: 0 for d in fleet}
    hours = {d["dut_id"]: 0.0 for d in fleet}
    signatures = {d["dut_id"]: {} for d in fleet}
    for r in runs:
        for d in domains(r)["MTBF"]["dut_details"]:
            totals[d["dut_id"]] += d["crashes"]
            hours[d["dut_id"]] += d["operational_hours"]
            for e in d["crash_events"]:
                sig = signatures[d["dut_id"]]
                sig[e["crash_signature"]] = sig.get(e["crash_signature"], 0) + 1
    counts = list(totals.values())
    median = statistics.median(counts)
    mean, sd = statistics.fmean(counts), statistics.pstdev(counts)
    lowest = min(totals, key=totals.get)
    latest = {d["dut_id"]: d["crashes"] for d in fleet}

    outliers = []
    for dut, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        ratio = n / median if median else None
        z = (n - mean) / sd if sd else 0
        if ratio and ratio >= a["dut_outlier_ratio"] and z >= a["dut_outlier_z"]:
            top = sorted(signatures[dut].items(), key=lambda kv: -kv[1])[:3]
            info = attrs[dut]
            outliers.append({
                "type": "DUT_CRASH_VARIANCE", "severity": "HIGH", "dut_id": dut,
                "crashes_all_builds": n, "fleet_median": median, "ratio_vs_median": round(ratio, 1),
                "z_score": round(z, 2), "crashes_latest_build": latest[dut],
                "board_rev": info["board_rev"], "rf_module_lot": info["rf_module_lot"], "bench": info["bench"],
                "top_signatures": [{"signature": s, "count": c} for s, c in top],
                "summary": f"{dut} crashed {n} times across {len(runs)} builds: {ratio:.1f}x the fleet median "
                           f"({median:g}) and {n / max(totals[lowest], 1):.1f}x {lowest} ({totals[lowest]}). "
                           f"Board {info['board_rev']}, RF lot {info['rf_module_lot']}.",
            })

    # Hardware groups are judged without the outlier DUTs, so one bad unit does not
    # make its whole bench or platform look bad.
    outlier_ids = {o["dut_id"] for o in outliers}
    rest = [d for d in fleet if d["dut_id"] not in outlier_ids]
    groups = []
    for attr in ("board_rev", "rf_module_lot", "bench", "host_platform"):
        values = sorted({d[attr] for d in rest})
        if len(values) < 2:
            continue
        for v in values:
            ins = [d["dut_id"] for d in rest if d[attr] == v]
            outs = [d["dut_id"] for d in rest if d[attr] != v]
            rate_in = 1000 * sum(totals[u] for u in ins) / sum(hours[u] for u in ins)
            rate_out = 1000 * sum(totals[u] for u in outs) / sum(hours[u] for u in outs)
            if rate_out and rate_in / rate_out >= a["group_ratio"]:
                groups.append({
                    "type": "HARDWARE_GROUP_VARIANCE", "severity": "MEDIUM", "attribute": attr, "value": v,
                    "duts": ins, "crashes_per_1000h": round(rate_in, 1), "rest_of_fleet_per_1000h": round(rate_out, 1),
                    "ratio": round(rate_in / rate_out, 2),
                    "summary": f"{attr} {v} ({fmt_list(ins, 4)}) crashes {rate_in / rate_out:.1f}x more often than "
                               f"the rest of the fleet ({rate_in:.1f} vs {rate_out:.1f} per 1,000 h"
                               + (f", excluding outlier {fmt_list(sorted(outlier_ids))}" if outlier_ids else "") + ").",
                })
    per_dut = [{"dut_id": u, "crashes_all_builds": totals[u], "crashes_latest_build": latest[u],
                **{k: attrs[u][k] for k in ("board_rev", "rf_module_lot", "bench")}} for u in totals]
    return outliers, groups, per_dut


def _feature_fail_series(runs):
    series = {}
    for r in runs:
        d = domains(r)
        for dom in TEST_DOMAINS:
            for feat, v in d[dom]["feature_breakdown"].items():
                series.setdefault((dom, feat), []).append(v["fail"] + v["blocked"])
    return series


def cross_talk(runs, cfg):
    """KPI step changes that land in the same build as feature failure spikes."""
    a = cfg["anomalies"]
    feat_series = _feature_fail_series(runs)
    kpi_names = [m["metric_name"] for m in domains(runs[0])["KPI_AUDIO"]["metrics"]]
    findings = []
    for name in kpi_names:
        rows = [next(m for m in domains(r)["KPI_AUDIO"]["metrics"] if m["metric_name"] == name) for r in runs]
        avgs = [m["measured_avg"] for m in rows]
        for i in range(1, len(runs)):
            step = avgs[i] - avgs[i - 1]
            worse = step > 0 if rows[i]["comparator"] == "<" else step < 0
            if not worse or abs(step) <= a["kpi_step_sigma"] * rows[i - 1]["std_dev"]:
                continue
            run = runs[i]
            kpi_jira = rows[i]["linked_jira"]
            spikes = []
            for (dom, feat), s in feat_series.items():
                before, after = s[i - 1], s[i]
                if after - before >= a["feature_spike_min"] and after >= a["feature_spike_ratio"] * max(before, 1):
                    failed = [f for f in domains(run)[dom]["failed_tests"] if f["feature"] == feat]
                    shared = sum(f["jira_id"] == kpi_jira for f in failed) if kpi_jira else 0
                    spikes.append({"domain": dom, "feature": feat, "before": before, "after": after,
                                   "correlation": pearson(avgs, s), "tests_linked_to_kpi_jira": shared})
            if not spikes:
                continue
            spikes.sort(key=lambda x: (-x["tests_linked_to_kpi_jira"], -(x["correlation"] or 0)))
            confirmed = [x for x in spikes if x["tests_linked_to_kpi_jira"]]
            named = confirmed or spikes
            findings.append({
                "type": "FEATURE_CROSS_TALK", "severity": "HIGH" if confirmed else "MEDIUM",
                "build": meta(run)["firmware_build"], "metric": name,
                "metric_before": avgs[i - 1], "metric_after": avgs[i], "metric_status": rows[i]["status"],
                "common_jira": kpi_jira if confirmed else None,
                "feature_spikes": named, "other_concurrent_spikes": [x for x in spikes if x not in named],
                "summary": f"At {meta(run)['firmware_build']}, {name} degraded {avgs[i - 1]} -> {avgs[i]} "
                           f"while failures spiked in " + fmt_list(f"{x['feature']} ({x['before']}->{x['after']})" for x in named)
                           + (f"; {kpi_jira} links both." if confirmed else "; no shared Jira yet - investigate."),
            })
    return findings


def blocked_concentrations(runs, cfg):
    a = cfg["anomalies"]
    latest = runs[-1]
    d = domains(latest)
    by_jira = {}
    for dom in TEST_DOMAINS:
        for b in d[dom]["blocked_tests"]:
            g = by_jira.setdefault(b["blocking_jira"], {"blocked": 0, "features": {}, "gating_tests": set(), "suites": set()})
            g["blocked"] += 1
            g["features"][b["feature"]] = g["features"].get(b["feature"], 0) + 1
            g["gating_tests"].add(b["blocked_by_tc"])
            g["suites"].add(b["suite_id"])
    titles = {t["jira_id"]: t for t in tickets(latest)}
    concentrations = []
    for jid, g in sorted(by_jira.items(), key=lambda kv: -kv[1]["blocked"]):
        if g["blocked"] < a["blocked_concentration_min"]:
            continue
        t = titles[jid]
        concentrations.append({
            "type": "BLOCKED_CONCENTRATION", "severity": "HIGH", "jira_id": jid, "title": t["title"],
            "ticket_severity": t["severity"], "subsystem": t["subsystem"], "blocked_tests": g["blocked"],
            "features": g["features"], "gating_tests": sorted(g["gating_tests"]), "suites": len(g["suites"]),
            "summary": f"{jid} ({t['severity']}, {t['subsystem']}) fails {len(g['gating_tests'])} gating tests and blocks "
                       f"{g['blocked']} tests in {len(g['suites'])} suites: "
                       + ", ".join(f"{n} {f}" for f, n in g["features"].items()) + ". Indicative of a gating driver bug.",
        })

    # Coverage gaps: features with a large blocked share now, and tests blocked for several builds in a row.
    gaps = []
    k = a["persistent_blocked_builds"]
    recent = runs[-k:]
    for dom in TEST_DOMAINS:
        persistent = set.intersection(*({b["tc_id"] for b in domains(r)[dom]["blocked_tests"]} for r in recent))
        stuck_by_feature = {}
        for b in d[dom]["blocked_tests"]:
            if b["tc_id"] in persistent:
                stuck_by_feature.setdefault(b["feature"], []).append(b["tc_id"])
        for feat, v in d[dom]["feature_breakdown"].items():
            share = v["blocked"] / v["total"]
            stuck = stuck_by_feature.get(feat, [])
            if share >= a["blocked_feature_share"] or stuck:
                gaps.append({
                    "type": "COVERAGE_GAP", "severity": "MEDIUM", "domain": dom, "feature": feat,
                    "blocked_now": v["blocked"], "blocked_share_percent": round(100 * share, 1),
                    "blocked_every_build_of_last": k if stuck else 0, "persistently_blocked_tests": len(stuck),
                    "summary": f"{feat}: {v['blocked']} of {v['total']} tests ({100 * share:.1f}%) not executed this build"
                               + (f"; {len(stuck)} tests have been blocked in each of the last {k} builds." if stuck else "."),
                })
    return concentrations, gaps


def regressions(runs):
    if len(runs) < 2:
        return []
    latest = runs[-1]
    build = meta(latest)["firmware_build"]
    out = []
    for t in tickets(latest):
        if t["first_seen_build"] == build and t["severity"] in ("Blocker", "Critical"):
            out.append({"type": "NEW_REGRESSION", "severity": "HIGH", "jira_id": t["jira_id"], "title": t["title"],
                        "ticket_severity": t["severity"], "subsystem": t["subsystem"],
                        "failed_tests": len(t["linked_failed_tests"]),
                        "summary": f"New {t['severity']} regression in {build}: {t['jira_id']} {t['title']}."})
    return out


def detect(runs, cfg):
    outliers, groups, per_dut = dut_variance(runs, cfg)
    talk = cross_talk(runs, cfg)
    conc, gaps = blocked_concentrations(runs, cfg)
    regs = regressions(runs)
    flagged = outliers + groups + talk + conc + regs + gaps
    return {
        "count": len(flagged),
        "high": sum(f["severity"] == "HIGH" for f in flagged),
        "dut_variance": outliers,
        "hardware_groups": groups,
        "dut_crash_table": per_dut,
        "cross_talk": talk,
        "blocked_concentrations": conc,
        "coverage_gaps": gaps,
        "regressions": regs,
        "outlier_duts": [o["dut_id"] for o in outliers],
    }
