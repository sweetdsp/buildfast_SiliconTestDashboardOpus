"""SampleTestRunGenerator - deterministic mock telemetry for full test cycles.

Simulates one full cycle per firmware build against the one-time test plan.
A defect timeline (curated defects + procedurally generated minor ones) decides
which tests fail or get blocked, which DUTs crash and how KPIs shift, so the
data carries real, traceable signal from build to build.
"""

import copy
import random
import statistics
from datetime import timedelta

from generator import catalog
from generator.common import (add_hours, fmt_time, lerp, parse_time, poisson, resolve,
                              weighted_choice, write_json)
from generator.defect_catalog import DEFECTS

SCHEMA_VERSION = "1.0"
SEVERITY_RANK = {"Blocker": 0, "Critical": 1, "Medium": 2, "Trivial": 3}
TEST_DOMAINS = ("HCI_REGRESSION", "PHONE_TESTING")


class SampleTestRunGenerator:
    def __init__(self, config, test_plan):
        self.cfg = config
        self.sim = config["simulation"]
        self.plan = test_plan
        self.seed = config["seeds"]["test_runs"]
        self.rng = random.Random(self.seed)
        self.builds = config["runs"]["firmware_builds"]
        self.next_jira = 4101
        self._index_plan()
        self.defects = [self._resolve_defect(copy.deepcopy(d)) for d in DEFECTS]

    # ------------------------------------------------------------------ plan
    def _index_plan(self):
        self.tests = {}       # tc_id -> test case
        self.by_feature = {}  # (domain, feature) -> {"regular": [...], "suites": {suite_id: [...]}}
        self.dependents = {}  # gating tc_id -> [dependent tc_ids]
        for domain in TEST_DOMAINS:
            for tc in self.plan["domains"][domain]["test_cases"]:
                self.tests[tc["tc_id"]] = tc
                bucket = self.by_feature.setdefault((domain, tc["feature"]), {"regular": [], "suites": {}})
                bucket["suites"].setdefault(tc["suite_id"], []).append(tc["tc_id"])
                if tc["is_gating"]:
                    self.dependents.setdefault(tc["tc_id"], [])
                else:
                    bucket["regular"].append(tc["tc_id"])
                    self.dependents[tc["depends_on"]].append(tc["tc_id"])

    def _resolve_defect(self, d):
        """Pin a defect to concrete test case IDs (stable across builds)."""
        rng = self.rng
        d.setdefault("tests", [])
        d.setdefault("gating", [])
        d.setdefault("kpi", {})
        d.setdefault("crash", None)
        d["_gating"] = []
        for g in d["gating"]:
            suites = list(self.by_feature[(g["domain"], g["feature"])]["suites"].values())
            rng.shuffle(suites)
            remaining = g["blocked"]
            for suite in suites:
                if 0 < len(suite) - 1 <= remaining:
                    d["_gating"].append(suite[0])
                    remaining -= len(suite) - 1
        gated = {tc for gid in d["_gating"] for tc in self.dependents[gid]}
        d["_tests"] = []
        for t in d["tests"]:
            pool = [tc for tc in self.by_feature[(t["domain"], t["feature"])]["regular"] if tc not in gated]
            d["_tests"].extend(rng.sample(pool, min(t["count"], len(pool))))
        return d

    def _new_jira(self):
        jid = f"BTFW-{self.next_jira}"
        self.next_jira += self.rng.randint(1, 3)
        return jid

    def _spawn_minor_defects(self, build_no):
        """Auto-generated small defects: a handful of tests in one feature, short lifetime."""
        rng, sim, n = self.rng, self.sim, len(self.builds)
        mcfg = sim["minor_defects_per_build"]
        count = round(lerp(mcfg["first"], mcfg["last"], build_no - 1, n))
        for _ in range(count):
            domain = "HCI_REGRESSION" if rng.random() < 0.75 else "PHONE_TESTING"
            features = {f: v["test_count"] for f, v in self.plan["domains"][domain]["features"].items()}
            feature = weighted_choice(rng, features)
            regular = self.by_feature[(domain, feature)]["regular"]
            anchor = self.tests[rng.choice(regular)]
            subsystem = anchor["subsystem"]
            same = [tc for tc in regular if self.tests[tc]["subsystem"] == subsystem]
            k = min(rng.randint(*sim["minor_defect_tests"]), len(same))
            signature = rng.choice(catalog.FAILURE_SIGNATURES[subsystem])
            life = rng.randint(*sim["minor_defect_lifetime_builds"])
            fixed = build_no + life
            self.defects.append({
                "jira_id": self._new_jira(), "signature": signature,
                "title": f"{feature}: {signature}", "subsystem": subsystem,
                "severity": weighted_choice(rng, sim["minor_defect_severity"]),
                "introduced": build_no, "fixed": fixed if fixed <= n else None,
                "tests": [], "gating": [], "kpi": {}, "crash": None,
                "_gating": [], "_tests": rng.sample(same, k), "minor": True,
            })

    # ------------------------------------------------------------------ runs
    def generate_all(self):
        runs, prev_kpi = [], {}
        last_start = parse_time(self.cfg["runs"]["last_cycle_start"])
        cadence = self.cfg["runs"]["cycle_cadence_days"]
        n = len(self.builds)
        for b in range(1, n + 1):
            self._spawn_minor_defects(b)
            start = last_start - timedelta(days=cadence * (n - b))
            run = self._simulate_build(b, start, prev_kpi)
            prev_kpi = {m["metric_name"]: m["measured_avg"] for m in run["domain_summaries"][3]["metrics"]}
            runs.append(run)
        return runs

    def _simulate_build(self, b, start, prev_kpi):
        rng = random.Random(f"{self.seed}-build-{b}")
        active = sorted(
            (d for d in self.defects if d["introduced"] <= b and (d["fixed"] is None or b < d["fixed"])),
            key=lambda d: SEVERITY_RANK[d["severity"]])
        tickets = {}
        hci = self._simulate_test_domain("HCI_REGRESSION", "hci_regression", b, start, active, tickets, rng)
        phone = self._simulate_test_domain("PHONE_TESTING", "phone_testing", b, start, active, tickets, rng)
        mtbf = self._simulate_mtbf(b, start, active, tickets, rng)
        kpi = self._simulate_kpi(b, start, active, tickets, prev_kpi, rng)
        domains = [hci, phone, mtbf, kpi]

        critical = max(domains, key=lambda d: d["execution_duration_hours"])
        baseline = max(d["baseline_duration_hours"] for d in domains)
        end = add_hours(start, critical["execution_duration_hours"])
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "run_id": f"BT_FULL_CYCLE_{end:%Y%m%d}_01",
            "build_index": b,
            "chipset_revision": self.cfg["project"]["chipset_revision"],
            "firmware_build": self.builds[b - 1],
            "previous_firmware_build": self.builds[b - 2] if b > 1 else None,
            "start_time": fmt_time(start),
            "end_time": fmt_time(end),
            "cycle_type": "FULL_CYCLE",
            "baseline_cycle_hours": baseline,
            "actual_cycle_hours": critical["execution_duration_hours"],
            "cycle_delta_hours": round(critical["execution_duration_hours"] - baseline, 1),
            "critical_path_domain": critical["domain"],
            "test_plan_version": self.plan["test_plan_version"],
            "generator_seed": self.seed,
        }
        return {
            "test_run_metadata": metadata,
            "domain_summaries": domains,
            "jira_telemetry": self._jira_telemetry(b, tickets),
        }

    # ---------------------------------------------------------- HCI / phone
    def _simulate_test_domain(self, domain, cfg_key, b, start, active, tickets, rng):
        sim, plan = self.sim, self.plan["domains"][domain]
        p_fail = sim["defect_fail_probability"]
        n = len(self.builds)
        bg = sim["background_failure_rate"][domain]
        bg_rate = lerp(bg["first"], bg["last"], b - 1, n)
        retry_rate = sim["passed_on_retry_rate"][domain]
        max_retries = sim["retries"][domain]["max_retries"]
        diag_min = sim["retries"][domain]["diagnostic_log_minutes"]

        outcome = {}  # tc_id -> ("FAIL", defect) | ("BLOCKED", gating_id, defect) | ("RETRY_PASS",)
        for d in active:
            for gid in d["_gating"]:
                if self.tests[gid]["domain"] == domain and gid not in outcome and rng.random() < p_fail:
                    outcome[gid] = ("FAIL", d)
                    for dep in self.dependents[gid]:
                        outcome.setdefault(dep, ("BLOCKED", gid, d))
        for d in active:
            for tc in d["_tests"]:
                if self.tests[tc]["domain"] == domain and tc not in outcome and rng.random() < p_fail:
                    outcome[tc] = ("FAIL", d)

        failed, blocked, retried = [], [], []
        for tc in plan["test_cases"]:
            tid = tc["tc_id"]
            if tid not in outcome:
                if not tc["is_gating"] and rng.random() < bg_rate:
                    outcome[tid] = ("FAIL", self._singleton_defect(tc, b, rng))
                elif rng.random() < retry_rate:
                    outcome[tid] = ("RETRY_PASS",)
                else:
                    continue
            result = outcome[tid]
            if result[0] == "FAIL":
                d = result[1]
                rec = {"tc_id": tid, "name": tc["name"], "feature": tc["feature"],
                       "subsystem": tc["subsystem"], "suite_id": tc["suite_id"],
                       "priority": tc["priority"], "is_gating": tc["is_gating"],
                       "failure_signature": d["signature"], "jira_id": d["jira_id"],
                       "retries": max_retries, "log_ref": f"logs/{self.builds[b - 1]}/{tid}.log"}
                if "peer_device" in tc:
                    rec["peer_device"] = tc["peer_device"]
                failed.append(rec)
                self._link(tickets, d, b, domain, failed_test=tid)
            elif result[0] == "BLOCKED":
                blocked.append({"tc_id": tid, "feature": tc["feature"], "suite_id": tc["suite_id"],
                                "blocked_by_tc": result[1], "blocking_jira": result[2]["jira_id"]})
                self._link(tickets, result[2], b, domain, blocked_test=tid)
            else:
                retried.append({"tc_id": tid, "feature": tc["feature"], "retries": 1,
                                "first_attempt_signature": rng.choice(
                                    catalog.FAILURE_SIGNATURES[tc["subsystem"]])})

        # Feature breakdown
        status_of = {r["tc_id"]: "fail" for r in failed}
        status_of.update({r["tc_id"]: "blocked" for r in blocked})
        retry_ids = {r["tc_id"] for r in retried}
        breakdown = {}
        for feat, meta in plan["features"].items():
            breakdown[feat] = {"subsystem": meta["subsystem"], "total": meta["test_count"],
                               "pass": 0, "fail": 0, "blocked": 0, "passed_on_retry": 0}
        for tc in plan["test_cases"]:
            fb = breakdown[tc["feature"]]
            fb[status_of.get(tc["tc_id"], "pass")] += 1
            if tc["tc_id"] in retry_ids:
                fb["passed_on_retry"] += 1

        # Elastic duration: re-runs, diagnostic log collection, dynamic isolation suites.
        lanes = plan["parallel_lanes"]
        retry_min = sum(self.tests[r["tc_id"]]["expected_duration_min"] * max_retries for r in failed)
        retry_min += sum(self.tests[r["tc_id"]]["expected_duration_min"] for r in retried)
        retry_h = retry_min / lanes / 60
        diag_h = len(failed) * diag_min / lanes / 60
        iso = sim["isolation_suite"]
        hot = [f for f, v in breakdown.items() if v["fail"] / v["total"] > iso["fail_rate_threshold"]]
        iso_h = len(hot) * iso["hours"]
        duration = round(plan["baseline_hours"] + retry_h + diag_h + iso_h, 1)

        total = plan["total_tests"]
        passed = total - len(failed) - len(blocked)
        return {
            "domain": domain,
            "total_tests": total,
            "executed": total - len(blocked),
            "passed": passed,
            "failed": len(failed),
            "blocked": len(blocked),
            "passed_on_retry": len(retried),
            "pass_rate_percent": round(100 * passed / total, 2),
            "total_retries": len(failed) * max_retries + len(retried),
            "baseline_duration_hours": plan["baseline_hours"],
            "execution_duration_hours": duration,
            "duration_overhead_hours": {"retries": round(retry_h, 1), "diagnostic_logs": round(diag_h, 1),
                                        "isolation_suites": round(iso_h, 1)},
            "isolation_suites_triggered": hot,
            "start_time": fmt_time(start),
            "end_time": fmt_time(add_hours(start, duration)),
            "feature_breakdown": breakdown,
            "failed_tests": failed,
            "blocked_tests": blocked,
            "passed_on_retry_tests": retried,
        }

    def _singleton_defect(self, tc, b, rng):
        signature = rng.choice(catalog.FAILURE_SIGNATURES[tc["subsystem"]])
        return {"jira_id": self._new_jira(), "signature": signature,
                "title": f"{tc['tc_id']} {tc['name']}: {signature}", "subsystem": tc["subsystem"],
                "severity": weighted_choice(rng, self.sim["singleton_severity"]),
                "introduced": b, "fixed": b + 1, "crash": None, "kpi": {}, "singleton": True}

    # ------------------------------------------------------------------ MTBF
    def _simulate_mtbf(self, b, start, active, tickets, rng):
        plan = self.plan["domains"]["MTBF"]
        planned = plan["planned_hours_per_dut"]
        mcfg = self.sim["mtbf"]
        all_duts = [d["dut_id"] for d in plan["duts"]]
        events = {dut: [] for dut in all_duts}
        for d in active:
            if not d["crash"]:
                continue
            duts = d["crash"]["duts"] or all_duts
            weights = {u: catalog.DUT_CRASH_SUSCEPTIBILITY.get(u, 1.0) for u in duts}
            wsum = sum(weights.values())
            for dut in duts:
                for _ in range(poisson(rng, d["crash"]["expected"] * weights[dut] / wsum)):
                    events[dut].append((rng.uniform(0.5, planned), d))

        dut_details, all_events, durations = [], [], []
        for meta in plan["duts"]:
            dut = meta["dut_id"]
            downtime_min, recs = 0.0, []
            for uptime, d in sorted(events[dut], key=lambda e: e[0]):
                manual = rng.random() < d["crash"]["manual_recovery"]
                rec_min = rng.uniform(*mcfg["manual_recovery_minutes" if manual else "auto_recovery_minutes"])
                ts = add_hours(start, uptime + downtime_min / 60)
                downtime_min += rec_min
                recs.append({"timestamp": fmt_time(ts), "crash_signature": d["signature"],
                             "jira_id": d["jira_id"], "uptime_at_crash_hours": round(uptime, 2),
                             "recovery_mode": "MANUAL" if manual else "AUTO_RESTART",
                             "recovery_time_min": round(rec_min, 1), "recovered": True})
                self._link(tickets, d, b, "MTBF", crash_dut=dut)
            wall = planned + downtime_min / 60
            durations.append(wall)
            all_events.extend(recs)
            dut_details.append({**meta, "planned_hours": planned, "operational_hours": planned,
                                "downtime_hours": round(downtime_min / 60, 2),
                                "wall_clock_hours": round(wall, 2), "crashes": len(recs),
                                "mtbf_hours": round(planned / len(recs), 1) if recs else None,
                                "crash_events": recs})

        total_crashes = len(all_events)
        total_hours = planned * len(all_duts)
        auto = sum(e["recovery_mode"] == "AUTO_RESTART" for e in all_events)
        duration = round(max(durations), 1)
        return {
            "domain": "MTBF",
            "duts_active": len(all_duts),
            "planned_hours_per_dut": planned,
            "total_operational_hours": total_hours,
            "total_downtime_hours": round(sum(d["downtime_hours"] for d in dut_details), 2),
            "total_crashes": total_crashes,
            "unique_crashes": len({e["crash_signature"] for e in all_events}),
            "recovery_success_rate_percent": round(100 * auto / total_crashes, 1) if total_crashes else 100.0,
            "mtbf_hours": round(total_hours / total_crashes, 1) if total_crashes else None,
            "baseline_duration_hours": planned,
            "execution_duration_hours": duration,
            "start_time": fmt_time(start),
            "end_time": fmt_time(add_hours(start, duration)),
            "dut_details": dut_details,
        }

    # ------------------------------------------------------------------- KPI
    def _simulate_kpi(self, b, start, active, tickets, prev_kpi, rng):
        plan = self.plan["domains"]["KPI_AUDIO"]
        kcfg = self.sim["kpi"]
        metrics, breaches = [], 0
        for m in catalog.KPI_METRICS:
            name = m["metric_name"]
            contributors = [d for d in active if name in d["kpi"]]
            mean = m["nominal"] + sum(d["kpi"][name] for d in contributors)
            samples = [max(0.0, rng.gauss(mean, m["sd"])) for _ in range(plan["samples_per_metric"])]
            avg = statistics.fmean(samples)
            status = self._kpi_status(avg, m, kcfg["degraded_margin"])
            linked = None
            if status != "PASS":
                breaches += 1
                culprit = max(contributors, key=lambda d: abs(d["kpi"][name]), default=None)
                if culprit is None:
                    culprit = self._perf_defect(m, b, rng)
                linked = culprit["jira_id"]
                self._link(tickets, culprit, b, "KPI_AUDIO", kpi_metric=name)
            ordered = sorted(samples)
            digits = 3 if m["target"] < 10 else 1
            metrics.append({
                "metric_name": name, "feature": m["feature"], "unit": m["unit"],
                "target": f"{m['comparator']} {m['target']}", "comparator": m["comparator"],
                "target_value": m["target"], "measured_avg": round(avg, digits),
                "measured_p95": round(ordered[int(0.95 * (len(ordered) - 1))], digits),
                "measured_min": round(ordered[0], digits), "measured_max": round(ordered[-1], digits),
                "std_dev": round(statistics.stdev(samples), digits), "sample_count": len(samples),
                "status": status,
                "delta_vs_previous_build": round(avg - prev_kpi[name], digits) if name in prev_kpi else None,
                "linked_jira": linked,
                "samples": [round(s, digits) for s in samples],
            })
        duration = round(plan["baseline_hours"] + breaches * kcfg["remeasure_hours_per_breach"], 1)
        return {
            "domain": "KPI_AUDIO",
            "metrics_total": len(metrics),
            "metrics_pass": sum(m["status"] == "PASS" for m in metrics),
            "metrics_degraded": sum(m["status"] == "DEGRADED" for m in metrics),
            "metrics_fail": sum(m["status"] == "FAIL" for m in metrics),
            "baseline_duration_hours": plan["baseline_hours"],
            "execution_duration_hours": duration,
            "start_time": fmt_time(start),
            "end_time": fmt_time(add_hours(start, duration)),
            "metrics": metrics,
        }

    @staticmethod
    def _kpi_status(avg, m, margin):
        target = m["target"]
        if m["comparator"] == "<":
            if avg < target:
                return "PASS"
            return "DEGRADED" if avg <= target * (1 + margin) else "FAIL"
        if avg > target:
            return "PASS"
        return "DEGRADED" if avg >= target * (1 - margin) else "FAIL"

    def _perf_defect(self, m, b, rng):
        """Auto-generated performance bug report for a KPI breach with no known cause."""
        return {"jira_id": self._new_jira(), "signature": f"KPI breach: {m['metric_name']}",
                "title": f"Performance: {m['metric_name']} misses target {m['comparator']} {m['target']}",
                "subsystem": "AUDIO_CODEC" if "Audio" in m["metric_name"] else "BASEBAND",
                "severity": "Medium", "introduced": b, "fixed": b + 1, "crash": None, "kpi": {}}

    # ------------------------------------------------------------------ Jira
    def _link(self, tickets, d, b, domain, failed_test=None, blocked_test=None, crash_dut=None,
              kpi_metric=None):
        t = tickets.get(d["jira_id"])
        if t is None:
            t = tickets[d["jira_id"]] = {
                "jira_id": d["jira_id"], "title": d["title"], "severity": d["severity"],
                "subsystem": d["subsystem"], "subsystem_name": catalog.SUBSYSTEMS[d["subsystem"]],
                "status": "Open" if d["introduced"] == b else "In Progress",
                "first_seen_build": self.builds[d["introduced"] - 1],
                "builds_open": b - d["introduced"] + 1,
                "signature": d["signature"],
                "origin": "SINGLE_INSTANCE" if d.get("singleton") else ("AUTO_MINOR" if d.get("minor") else "TRACKED"),
                "domains": [], "linked_failed_tests": [], "linked_blocked_tests": [],
                "crash_occurrences": 0, "affected_duts": [], "kpi_metrics": [],
            }
        if domain not in t["domains"]:
            t["domains"].append(domain)
        if failed_test:
            t["linked_failed_tests"].append(failed_test)
        if blocked_test:
            t["linked_blocked_tests"].append(blocked_test)
        if crash_dut:
            t["crash_occurrences"] += 1
            if crash_dut not in t["affected_duts"]:
                t["affected_duts"].append(crash_dut)
            t["stack_trace_top_frames"] = d["crash"]["stack"]
        if kpi_metric:
            t["kpi_metrics"].append(kpi_metric)

    def _jira_telemetry(self, b, tickets):
        ordered = sorted(tickets.values(), key=lambda t: (SEVERITY_RANK[t["severity"]], t["jira_id"]))
        for t in ordered:
            t["affected_duts"].sort()
            t["ticket_type"] = ("CRASH" if t["crash_occurrences"] else
                                "PERFORMANCE" if t["kpi_metrics"] and not t["linked_failed_tests"] else
                                "FUNCTIONAL")
        severity = {s: 0 for s in SEVERITY_RANK}
        status = {}
        for t in ordered:
            severity[t["severity"]] += 1
            status[t["status"]] = status.get(t["status"], 0) + 1
        crash_mapping = [
            {"jira_id": t["jira_id"], "crash_signature": t["signature"],
             "occurrences": t["crash_occurrences"], "affected_duts": t["affected_duts"],
             "severity": t["severity"], "subsystem": t["subsystem"],
             "stack_trace_top_frames": t["stack_trace_top_frames"],
             "first_seen_build": t["first_seen_build"]}
            for t in sorted(ordered, key=lambda t: -t["crash_occurrences"]) if t["crash_occurrences"]
        ]
        resolved = [{"jira_id": d["jira_id"], "title": d["title"], "severity": d["severity"],
                     "subsystem": d["subsystem"]}
                    for d in self.defects if d["fixed"] == b and not d.get("singleton")]
        return {
            "total_tickets_linked": len(ordered),
            "severity_distribution": severity,
            "status_distribution": status,
            "crash_mapping": crash_mapping,
            "resolved_since_previous_build": resolved,
            "tickets": ordered,
        }


def write_runs(config, runs):
    runs_dir = resolve(config["paths"]["runs_dir"])
    runs_dir.mkdir(parents=True, exist_ok=True)
    for stale in runs_dir.glob("BT_FULL_CYCLE_*.json"):
        stale.unlink()
    index = []
    for run in runs:
        meta = run["test_run_metadata"]
        name = f"{meta['run_id']}.json"
        write_json(runs_dir / name, run)
        index.append({"run_id": meta["run_id"], "build_index": meta["build_index"],
                      "firmware_build": meta["firmware_build"], "file": name,
                      "start_time": meta["start_time"], "end_time": meta["end_time"]})
    write_json(runs_dir / "index.json", {"schema_version": SCHEMA_VERSION, "runs": index})
    return runs_dir
