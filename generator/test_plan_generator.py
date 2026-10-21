"""One-time Test Plan Generator.

Builds the master test catalog (data/test_plan.json) that every simulated run
refers to, so run results can name exactly which test cases failed or were blocked.

Each feature is split into sub-suites. The first test of a sub-suite is its
gating setup test; the rest depend on it, so a failing gating test blocks the
whole sub-suite (the source of realistic "Blocked" concentrations).
"""

import random

from generator import catalog
from generator.common import apportion

DOMAIN_PREFIX = {"HCI_REGRESSION": "HCI", "PHONE_TESTING": "PHN"}
STRESS_MARKERS = ("long run", "back-to-back", "repeat x")


def _test_type(variant):
    if variant.startswith("negative"):
        return "NEGATIVE"
    if variant == "boundary values":
        return "BOUNDARY"
    if any(m in variant for m in STRESS_MARKERS):
        return "STRESS"
    return "FUNCTIONAL"


class TestPlanGenerator:
    def __init__(self, config):
        self.config = config
        self.rng = random.Random(config["seeds"]["test_plan"])

    def generate(self):
        tp = self.config["test_plan"]
        hci = self._build_domain("HCI_REGRESSION", catalog.HCI_FEATURES, tp["hci_regression"])
        phone = self._build_domain("PHONE_TESTING", catalog.PHONE_FEATURES, tp["phone_testing"])
        return {
            "test_plan_version": tp["version"],
            "seed": self.config["seeds"]["test_plan"],
            "chipset_revision": self.config["project"]["chipset_revision"],
            "bt_core_version": self.config["project"]["bt_core_version"],
            "subsystems": catalog.SUBSYSTEMS,
            "domains": {
                "HCI_REGRESSION": hci,
                "PHONE_TESTING": phone,
                "MTBF": self._mtbf_plan(tp["mtbf"]),
                "KPI_AUDIO": self._kpi_plan(tp["kpi_audio"]),
            },
        }

    def _build_domain(self, domain, features, cfg):
        counts = apportion(cfg["total_tests"], [f["weight"] for f in features])
        tests, feature_index = [], {}
        for feat, count in zip(features, counts):
            feat_tests = self._build_feature(domain, feat, count, cfg["suite_size"])
            tests.extend(feat_tests)
            feature_index[feat["feature"]] = {
                "subsystem": feat["subsystem"],
                "test_count": count,
                "suite_count": sum(t["is_gating"] for t in feat_tests),
                **({"spec_ref": feat["spec_ref"]} if "spec_ref" in feat else {"profile": feat["profile"]}),
            }
        self._scale_durations(tests, cfg["baseline_hours"] * cfg["parallel_lanes"] * 60)
        return {
            "total_tests": len(tests),
            "baseline_hours": cfg["baseline_hours"],
            "parallel_lanes": cfg["parallel_lanes"],
            "feature_count": len(features),
            "features": feature_index,
            "test_cases": tests,
        }

    def _build_feature(self, domain, feat, count, suite_size):
        rng = self.rng
        prefix = f"{DOMAIN_PREFIX[domain]}-{feat['abbr']}"
        pool = catalog.VARIANT_POOLS[feat["variant_pool"]]
        width = 4 if domain == "HCI_REGRESSION" else 3

        # Split the feature into sub-suites; a short remainder joins the last suite.
        sizes, left = [], count
        while left > 0:
            size = min(rng.randint(*suite_size), left)
            if left - size < suite_size[0] // 2:
                size = left
            sizes.append(size)
            left -= size

        tests, n = [], 0
        for s_idx, size in enumerate(sizes):
            op = feat["operations"][s_idx % len(feat["operations"])]
            suite_id = f"{prefix}-S{s_idx + 1:02d}"
            gating_id = None
            for k in range(size):
                n += 1
                tc_id = f"{prefix}-{n:0{width}d}"
                is_gating = k == 0
                variant = "suite setup / precondition" if is_gating else rng.choice(pool)
                ttype = "SETUP" if is_gating else _test_type(variant)
                subsystem = feat["subsystem"]
                if feat["secondary"] and not is_gating and rng.random() < feat["secondary"][1]:
                    subsystem = feat["secondary"][0]
                tc = {
                    "tc_id": tc_id,
                    "name": f"{op} [{variant}]" + ("" if is_gating else f" #{k:02d}"),
                    "domain": domain,
                    "feature": feat["feature"],
                    "subsystem": subsystem,
                    "suite_id": suite_id,
                    "is_gating": is_gating,
                    "depends_on": gating_id,
                    "priority": "P0" if is_gating else rng.choices(["P1", "P2", "P3"], [35, 50, 15])[0],
                    "test_type": ttype,
                    "automated": True,
                }
                if domain == "HCI_REGRESSION":
                    tc["spec_ref"] = feat["spec_ref"]
                    tc["min_bt_version"] = feat["min_bt_version"]
                else:
                    tc["profile"] = feat["profile"]
                    tc["peer_device"] = rng.choice(feat["peers"])
                # Relative cost; scaled to the domain's time budget afterwards.
                weight = rng.lognormvariate(0, 0.5)
                if ttype == "STRESS":
                    weight *= 4
                elif ttype == "SETUP":
                    weight *= 0.5
                tc["expected_duration_min"] = weight
                tests.append(tc)
                if is_gating:
                    gating_id = tc_id
        return tests

    @staticmethod
    def _scale_durations(tests, budget_minutes):
        """Scale durations so the domain fits its baseline: sum / lanes == baseline hours."""
        factor = budget_minutes / sum(t["expected_duration_min"] for t in tests)
        for t in tests:
            t["expected_duration_min"] = round(t["expected_duration_min"] * factor, 1)

    @staticmethod
    def _mtbf_plan(cfg):
        return {
            "duts": catalog.DUT_FLEET[: cfg["duts"]],
            "planned_hours_per_dut": cfg["planned_hours_per_dut"],
            "total_planned_hours": cfg["duts"] * cfg["planned_hours_per_dut"],
            "scenario": "Continuous mixed traffic: A2DP + LE Audio + BLE wearable + role switch loops",
            "detection": "Crashes and hangs only; auto-recovery and restart with timestamp logging",
        }

    @staticmethod
    def _kpi_plan(cfg):
        keep = ("metric_name", "feature", "unit", "comparator", "target")
        return {
            "baseline_hours": cfg["baseline_hours"],
            "samples_per_metric": cfg["samples_per_metric"],
            "metrics": [{k: m[k] for k in keep} for m in catalog.KPI_METRICS],
        }
