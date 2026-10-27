"""Validate the test plan and every test run before analysis.

Each check records PASS or FAIL with a detail message; the pipeline stops if any
check fails, so the analysis engine never works on inconsistent data.
"""

from generator.common import parse_time

DOMAIN_ORDER = ["HCI_REGRESSION", "PHONE_TESTING", "MTBF", "KPI_AUDIO"]
SEVERITIES = ["Blocker", "Critical", "Medium", "Trivial"]
REQUIRED = {
    "test_run_metadata": ["run_id", "build_index", "chipset_revision", "firmware_build", "start_time",
                          "end_time", "cycle_type", "baseline_cycle_hours", "actual_cycle_hours",
                          "cycle_delta_hours", "critical_path_domain"],
    "TEST_DOMAIN": ["domain", "total_tests", "executed", "passed", "failed", "blocked", "pass_rate_percent",
                    "baseline_duration_hours", "execution_duration_hours", "feature_breakdown",
                    "failed_tests", "blocked_tests", "passed_on_retry_tests"],
    "MTBF": ["domain", "duts_active", "planned_hours_per_dut", "total_operational_hours", "total_crashes",
             "unique_crashes", "recovery_success_rate_percent", "mtbf_hours", "execution_duration_hours",
             "dut_details"],
    "KPI_AUDIO": ["domain", "metrics_total", "metrics_pass", "execution_duration_hours", "metrics"],
    "jira_telemetry": ["total_tickets_linked", "severity_distribution", "crash_mapping", "tickets"],
}


class Validator:
    def __init__(self, test_plan):
        self.plan = test_plan
        self.tests = {tc["tc_id"]: tc for dom in ("HCI_REGRESSION", "PHONE_TESTING")
                      for tc in test_plan["domains"][dom]["test_cases"]}
        self.results = []
        self._scope = "test_plan"

    # ------------------------------------------------------------- helpers
    def check(self, name, ok, detail=""):
        self.results.append({"scope": self._scope, "check": name, "status": "PASS" if ok else "FAIL",
                             "detail": "" if ok else detail})
        return ok

    def require(self, obj, kind, label):
        missing = [k for k in REQUIRED[kind] if k not in obj]
        return self.check(f"{label}: required fields", not missing, f"missing {missing}")

    # ---------------------------------------------------------------- plan
    def validate_plan(self):
        self._scope = "test_plan"
        for dom in ("HCI_REGRESSION", "PHONE_TESTING"):
            d = self.plan["domains"][dom]
            tcs = d["test_cases"]
            self.check(f"{dom}: total_tests matches test cases", d["total_tests"] == len(tcs),
                       f"{d['total_tests']} != {len(tcs)}")
            self.check(f"{dom}: feature test counts sum to total",
                       sum(f["test_count"] for f in d["features"].values()) == len(tcs))
            ids = [tc["tc_id"] for tc in tcs]
            self.check(f"{dom}: unique test case IDs", len(set(ids)) == len(ids))
            bad = [tc["tc_id"] for tc in tcs if not tc["is_gating"] and
                   (tc["depends_on"] not in self.tests or not self.tests[tc["depends_on"]]["is_gating"])]
            self.check(f"{dom}: every dependency points at a gating test", not bad, f"{len(bad)} bad, e.g. {bad[:3]}")
            budget = sum(tc["expected_duration_min"] for tc in tcs) / d["parallel_lanes"] / 60
            self.check(f"{dom}: durations fit the baseline", abs(budget - d["baseline_hours"]) < 0.5,
                       f"{budget:.1f} h vs {d['baseline_hours']} h")
        self.check("HCI_REGRESSION: 25 features", self.plan["domains"]["HCI_REGRESSION"]["feature_count"] == 25)
        self.check("PHONE_TESTING: 15 features", self.plan["domains"]["PHONE_TESTING"]["feature_count"] == 15)

    # ---------------------------------------------------------------- runs
    def validate_runs(self, runs, index=None):
        for run in runs:
            self.validate_run(run)
        self._scope = "cross_run"
        builds = [r["test_run_metadata"]["build_index"] for r in runs]
        self.check("build indexes are 1..N in order", builds == list(range(1, len(runs) + 1)), str(builds))
        starts = [parse_time(r["test_run_metadata"]["start_time"]) for r in runs]
        self.check("runs are in chronological order", starts == sorted(starts))
        if index is not None:
            self.check("index.json lists every run",
                       [e["run_id"] for e in index["runs"]] == [r["test_run_metadata"]["run_id"] for r in runs])

    def validate_run(self, run):
        meta = run.get("test_run_metadata", {})
        self._scope = meta.get("run_id", "unknown_run")
        if not self.require(meta, "test_run_metadata", "metadata"):
            return
        domains = run["domain_summaries"]
        if not self.check("domain order", [d["domain"] for d in domains] == DOMAIN_ORDER,
                          str([d["domain"] for d in domains])):
            return
        hci, phone, mtbf, kpi = domains
        jira = run["jira_telemetry"]
        self.require(jira, "jira_telemetry", "jira_telemetry")
        tickets = {t["jira_id"]: t for t in jira["tickets"]}

        self._validate_metadata(meta, domains)
        for d in (hci, phone):
            self._validate_test_domain(d, tickets)
        self._validate_mtbf(mtbf, jira, tickets)
        self._validate_kpi(kpi, tickets)
        self._validate_jira(jira, tickets)

    def _validate_metadata(self, meta, domains):
        hours = (parse_time(meta["end_time"]) - parse_time(meta["start_time"])).total_seconds() / 3600
        self.check("metadata: end - start equals actual cycle hours",
                   abs(hours - meta["actual_cycle_hours"]) <= 0.05, f"{hours:.2f} vs {meta['actual_cycle_hours']}")
        longest = max(domains, key=lambda d: d["execution_duration_hours"])
        self.check("metadata: cycle equals the longest domain",
                   meta["actual_cycle_hours"] == longest["execution_duration_hours"]
                   and meta["critical_path_domain"] == longest["domain"])
        self.check("metadata: cycle delta = actual - baseline",
                   abs(meta["cycle_delta_hours"] - (meta["actual_cycle_hours"] - meta["baseline_cycle_hours"])) <= 0.05)

    def _validate_test_domain(self, d, tickets):
        dom = d["domain"]
        if not self.require(d, "TEST_DOMAIN", dom):
            return
        plan = self.plan["domains"][dom]
        self.check(f"{dom}: total matches test plan", d["total_tests"] == plan["total_tests"])
        self.check(f"{dom}: passed + failed + blocked = total",
                   d["passed"] + d["failed"] + d["blocked"] == d["total_tests"])
        self.check(f"{dom}: executed = total - blocked", d["executed"] == d["total_tests"] - d["blocked"])
        self.check(f"{dom}: pass rate consistent",
                   abs(d["pass_rate_percent"] - 100 * d["passed"] / d["total_tests"]) < 0.01)
        fb = d["feature_breakdown"]
        self.check(f"{dom}: all plan features present", set(fb) == set(plan["features"]))
        bad = [f for f, v in fb.items() if v["pass"] + v["fail"] + v["blocked"] != v["total"]
               or v["total"] != plan["features"][f]["test_count"]]
        self.check(f"{dom}: feature pass + fail + blocked = feature total", not bad, f"bad features {bad}")
        for key, col in (("fail", "failed"), ("blocked", "blocked")):
            self.check(f"{dom}: feature {key} counts sum to domain {col}",
                       sum(v[key] for v in fb.values()) == d[col])
        self.check(f"{dom}: failed_tests list matches failed count", len(d["failed_tests"]) == d["failed"])
        self.check(f"{dom}: blocked_tests list matches blocked count", len(d["blocked_tests"]) == d["blocked"])

        failed = {f["tc_id"]: f for f in d["failed_tests"]}
        blocked = {b["tc_id"]: b for b in d["blocked_tests"]}
        listed = list(failed) + list(blocked) + [r["tc_id"] for r in d["passed_on_retry_tests"]]
        unknown = [t for t in listed if t not in self.tests or self.tests[t]["domain"] != dom]
        self.check(f"{dom}: every listed test exists in the plan", not unknown, f"e.g. {unknown[:3]}")
        self.check(f"{dom}: no test both failed and blocked", not set(failed) & set(blocked))
        mismatch = [t for t, f in failed.items() if t in self.tests and self.tests[t]["feature"] != f["feature"]]
        self.check(f"{dom}: failed test features match the plan", not mismatch, f"e.g. {mismatch[:3]}")
        no_jira = [t for t, f in failed.items() if f["jira_id"] not in tickets]
        self.check(f"{dom}: every failure is linked to a Jira (100% automation)", not no_jira, f"e.g. {no_jira[:3]}")
        orphan = [t for t, b in blocked.items()
                  if b["blocked_by_tc"] not in failed or self.tests.get(t, {}).get("depends_on") != b["blocked_by_tc"]]
        self.check(f"{dom}: every blocked test depends on a failed gating test", not orphan, f"e.g. {orphan[:3]}")
        self.check(f"{dom}: duration >= baseline", d["execution_duration_hours"] >= d["baseline_duration_hours"])

    def _validate_mtbf(self, m, jira, tickets):
        if not self.require(m, "MTBF", "MTBF"):
            return
        duts = m["dut_details"]
        events = [e for dut in duts for e in dut["crash_events"]]
        self.check("MTBF: duts_active matches DUT details", m["duts_active"] == len(duts))
        self.check("MTBF: crash totals consistent",
                   m["total_crashes"] == sum(d["crashes"] for d in duts) == len(events))
        self.check("MTBF: unique crash count consistent",
                   m["unique_crashes"] == len({e["crash_signature"] for e in events}))
        self.check("MTBF: crash_mapping occurrences sum to total crashes",
                   sum(c["occurrences"] for c in jira["crash_mapping"]) == m["total_crashes"])
        expected = round(m["total_operational_hours"] / m["total_crashes"], 1) if m["total_crashes"] else None
        self.check("MTBF: mtbf_hours = operational hours / crashes", m["mtbf_hours"] == expected,
                   f"{m['mtbf_hours']} vs {expected}")
        self.check("MTBF: crash events linked to Jira", all(e["jira_id"] in tickets for e in events))
        start, end = parse_time(m["start_time"]), parse_time(m["end_time"])
        self.check("MTBF: crash timestamps inside the MTBF window",
                   all(start <= parse_time(e["timestamp"]) <= end for e in events))
        for c in jira["crash_mapping"]:
            duts_seen = sorted({d["dut_id"] for d in duts for e in d["crash_events"] if e["jira_id"] == c["jira_id"]})
            if not self.check(f"MTBF: affected DUTs for {c['jira_id']} match events", duts_seen == c["affected_duts"]):
                break

    def _validate_kpi(self, k, tickets):
        if not self.require(k, "KPI_AUDIO", "KPI_AUDIO"):
            return
        plan = {m["metric_name"]: m for m in self.plan["domains"]["KPI_AUDIO"]["metrics"]}
        self.check("KPI: every plan metric measured", {m["metric_name"] for m in k["metrics"]} == set(plan))
        self.check("KPI: pass count consistent", k["metrics_pass"] == sum(m["status"] == "PASS" for m in k["metrics"]))
        wrong = []
        for m in k["metrics"]:
            meets = m["measured_avg"] < m["target_value"] if m["comparator"] == "<" else m["measured_avg"] > m["target_value"]
            if meets != (m["status"] == "PASS"):
                wrong.append(m["metric_name"])
        self.check("KPI: status agrees with target", not wrong, f"{wrong}")
        bad_link = [m["metric_name"] for m in k["metrics"]
                    if (m["status"] != "PASS") != (m["linked_jira"] is not None)
                    or (m["linked_jira"] and m["linked_jira"] not in tickets)]
        self.check("KPI: every breach has a linked Jira", not bad_link, f"{bad_link}")

    def _validate_jira(self, jira, tickets):
        self.check("Jira: ticket IDs unique", len(tickets) == len(jira["tickets"]))
        self.check("Jira: total matches tickets", jira["total_tickets_linked"] == len(tickets))
        sev = jira["severity_distribution"]
        self.check("Jira: severity distribution sums to total", sum(sev.values()) == len(tickets))
        self.check("Jira: severity distribution matches tickets",
                   all(sev.get(s, 0) == sum(t["severity"] == s for t in tickets.values()) for s in SEVERITIES))

    # -------------------------------------------------------------- report
    def report(self):
        failed = [r for r in self.results if r["status"] == "FAIL"]
        return {
            "checks_total": len(self.results),
            "checks_passed": len(self.results) - len(failed),
            "checks_failed": len(failed),
            "status": "PASS" if not failed else "FAIL",
            "failures": failed,
            "checks": self.results,
        }
