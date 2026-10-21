"""Output 5 - Automated "Next Actions" with targeted re-run subsets.

Actions are prescriptive and assigned to a developer role. Each fix action comes
with a re-run subset (the ticket's failing and blocked tests, plus the gating
smoke tests of the affected features, a short MTBF soak on affected DUTs and a
KPI re-measure) so verification does not need a full cycle.
"""

from analysis.common import TEAM_BY_SUBSYSTEM, TEST_DOMAINS, domains, fmt_list, meta, tickets

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def _count(records, key):
    out = {}
    for r in records:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def _by_feature_text(counts):
    return ", ".join(f"{n} {f}" for f, n in sorted(counts.items(), key=lambda kv: -kv[1]))


class _Planner:
    def __init__(self, runs, plan, cfg):
        self.latest = runs[-1]
        self.d = domains(self.latest)
        self.plan = plan
        self.cfg = cfg["rerun"]
        self.tests = {tc["tc_id"]: tc for dom in TEST_DOMAINS for tc in plan["domains"][dom]["test_cases"]}
        self.gating_by_feature = {}
        for tc in self.tests.values():
            if tc["is_gating"]:
                self.gating_by_feature.setdefault((tc["domain"], tc["feature"]), []).append(tc["tc_id"])
        self.subsets = []

    def hours(self, tc_ids, duts=(), metrics=()):
        parts = {}
        for dom in TEST_DOMAINS:
            mins = sum(self.tests[t]["expected_duration_min"] for t in tc_ids if self.tests[t]["domain"] == dom)
            parts[dom] = mins / self.plan["domains"][dom]["parallel_lanes"] / 60
        parts["MTBF"] = self.cfg["mtbf_soak_hours"] if duts else 0
        parts["KPI_AUDIO"] = self.cfg["kpi_remeasure_hours"] * len(metrics)
        return round(max(parts.values()), 1)

    def subset(self, purpose, jira_ids, tc_ids, duts=(), metrics=()):
        feats = {(self.tests[t]["domain"], self.tests[t]["feature"]) for t in tc_ids}
        smoke = {g for f in feats for g in self.gating_by_feature.get(f, [])}
        ids = sorted(set(tc_ids) | smoke)
        full = meta(self.latest)["actual_cycle_hours"]
        est = self.hours(ids, duts, metrics)
        s = {
            "id": f"RS-{len(self.subsets) + 1:02d}", "purpose": purpose, "jira_ids": list(jira_ids),
            "tests": {dom: [t for t in ids if self.tests[t]["domain"] == dom] for dom in TEST_DOMAINS},
            "test_count": len(ids), "smoke_gating_tests": len(smoke - set(tc_ids)),
            "mtbf_duts": sorted(duts), "kpi_metrics": list(metrics),
            "estimated_hours": est, "full_cycle_hours": full,
            "savings_percent": round(100 * (1 - est / full), 1) if full else 0,
        }
        self.subsets.append(s)
        return s


def generate(runs, plan, cfg, risk, anomalies, clusters):
    p = _Planner(runs, plan, cfg)
    latest, d = p.latest, p.d
    by_id = {t["jira_id"]: t for t in tickets(latest)}
    blocked = [b for dom in TEST_DOMAINS for b in d[dom]["blocked_tests"]]
    failed = [f for dom in TEST_DOMAINS for f in d[dom]["failed_tests"]]
    kpis = {m["metric_name"]: m for m in d["KPI_AUDIO"]["metrics"]}
    actions = []

    def add(priority, team, action, rationale, jira_ids=(), subset=None, impact=None):
        actions.append({"priority": priority, "team": team, "action": action, "rationale": rationale,
                        "jira_ids": list(jira_ids), "rerun_subset": subset["id"] if subset else None,
                        "impact": impact or {}})

    # 1. Fix actions for the gate-driven must-fix set, then the remaining open Criticals.
    must_fix = {t["jira_id"]: t for t in risk["forecast"]["must_fix"]}
    others = [t for t in tickets(latest) if t["severity"] == "Critical" and t["jira_id"] not in must_fix]
    for jid in list(must_fix) + [t["jira_id"] for t in others]:
        t = by_id[jid]
        blk = _count([b for b in blocked if b["blocking_jira"] == jid], "feature")
        fl = _count([f for f in failed if f["jira_id"] == jid], "feature")
        parts = []
        if blk:
            parts.append(f"unblock {sum(blk.values())} blocked tests ({_by_feature_text(blk)})")
        if fl:
            parts.append(f"clear {sum(fl.values())} failing tests ({_by_feature_text(fl)})")
        if t["crash_occurrences"]:
            n = t["crash_occurrences"]
            parts.append(f"eliminate {n} MTBF crash{'es' if n > 1 else ''} on {fmt_list(t['affected_duts'], 4)}")
        for name in t["kpi_metrics"]:
            k = kpis[name]
            parts.append(f"bring {name} back within target ({k['measured_avg']} vs {k['target']})")
        sub = p.subset(f"Verify fix for {jid}", [jid], t["linked_failed_tests"] + t["linked_blocked_tests"],
                       t["affected_duts"], t["kpi_metrics"])
        urgent = " immediately" if t["severity"] == "Blocker" else ""
        why = must_fix[jid]["required_by"] if jid in must_fix else "open Critical"
        add("P0" if t["severity"] == "Blocker" else "P1" if jid in must_fix else "P2",
            TEAM_BY_SUBSYSTEM[t["subsystem"]],
            f"Fix {jid} ({t['title']}){urgent} to " + "; ".join(parts or ["close the ticket"]) + ".",
            f"{t['severity']}, open {t['builds_open']} build(s), required by: {why}. "
            f"Verify with {sub['id']} ({sub['test_count']} tests, ~{sub['estimated_hours']} h) instead of a full cycle.",
            [jid], sub,
            {"blocked_tests": sum(blk.values()), "failed_tests": sum(fl.values()),
             "crashes": t["crash_occurrences"], "kpi_metrics": len(t["kpi_metrics"])})

    # 2. Hardware-dependent anomalies.
    groups = anomalies["hardware_groups"]
    for o in anomalies["dut_variance"]:
        related = [g["summary"] for g in groups if o["dut_id"] in g["duts"]]
        add("P1", "Hardware / Board Bring-up",
            f"Inspect {o['dut_id']} ({o['board_rev']} board, RF lot {o['rf_module_lot']}) and swap it for a DVT1 board "
            f"in the next MTBF run to separate hardware from firmware causes.",
            o["summary"] + (" " + " ".join(related) if related else ""))

    # 3. Cross-talk without a confirmed common Jira needs a systems investigation.
    for c in anomalies["cross_talk"]:
        if not c["common_jira"]:
            add("P1", "Systems / Performance",
                f"Investigate {c['metric']} degradation at {c['build']} and its link to "
                f"{fmt_list(x['feature'] for x in c['feature_spikes'])} failures; raise a tracking Jira.",
                c["summary"])

    # 4. Test infrastructure: duplicates, coverage gaps, targeted re-runs.
    dups = clusters["duplicate_candidates"]
    if dups:
        add("P2", "Test Infrastructure / Triage",
            f"Merge {len(dups)} candidate duplicate clusters ({sum(len(c['tickets']) for c in dups)} tickets) into "
            f"root-cause tickets, starting with '{dups[0]['signature']}' ({len(dups[0]['tickets'])} tickets).",
            "Auto-ticketing created separate Jiras for failures that share subsystem and signature.",
            [j for c in dups for j in c["tickets"]])
    stuck = [g for g in anomalies["coverage_gaps"] if g["persistently_blocked_tests"]]
    if stuck:
        add("P2", "Test Infrastructure / Triage",
            "Add an alternative setup path for " + fmt_list(g["feature"] for g in stuck)
            + " so persistently blocked tests regain coverage while the gating bugs are open.",
            "; ".join(g["summary"] for g in stuck))

    all_ids = [f["tc_id"] for f in failed] + [b["tc_id"] for b in blocked]
    all_gating = [t for t, tc in p.tests.items() if tc["is_gating"]]
    breached = [m for m, k in kpis.items() if k["status"] != "PASS"]
    crash_duts = sorted({u for t in tickets(latest) for u in t["affected_duts"]})
    rc = p.subset("Next-build verification: all open failures + gating smoke across every feature",
                  sorted(must_fix), all_ids + all_gating, crash_duts, breached)
    add("P1", "Test Infrastructure / Triage",
        f"Run targeted subset {rc['id']} on the next build ({rc['test_count']} tests, ~{rc['estimated_hours']} h, "
        f"{rc['savings_percent']}% shorter than the {rc['full_cycle_hours']} h full cycle). Keep the full cycle for the "
        f"final release candidate.",
        "Every open failure, blocked test and feature gating test is covered; MTBF soak on DUTs that crashed.",
        sorted(must_fix), rc)

    # 5. Release lead decision.
    fc = risk["forecast"]
    failing = [g["gate"] for g in risk["gates"] if g["status"] == "FAIL"]
    if failing:
        add("P0", "Release Lead",
            f"Hold the release: {len(failing)} of {risk['gates_total']} gates failing ({fmt_list(failing, 5)}). "
            f"Projected ready {(fc['projected_ready_date'] or 'n/a')[:10]} vs target {fc['target_release_date'][:10]} "
            f"({fc['schedule_status']}" + (f", {fc['slip_days']:+} days" if fc['slip_days'] is not None else "") + ").",
            f"Must-fix set: {fmt_list(must_fix, 6)}.", sorted(must_fix))
    else:
        add("P0", "Release Lead", "All release gates pass: proceed with release sign-off.", "No gate failures.")

    actions.sort(key=lambda a: PRIORITY_ORDER[a["priority"]])
    for i, a in enumerate(actions, 1):
        a["id"] = f"NA-{i:02d}"
    by_team = {}
    for a in actions:
        by_team.setdefault(a["team"], []).append(a["id"])
    return {"actions": actions, "by_team": by_team, "rerun_subsets": p.subsets}
