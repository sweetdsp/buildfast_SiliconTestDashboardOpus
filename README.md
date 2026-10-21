# Bluetooth Test Infrastructure Dashboard Agent

An agent that analyses mock telemetry from Bluetooth silicon/platform validation runs
(BT 5.2 chipset for mobile platforms) and produces an executive dashboard: TL;DR,
release risk, anomalies, bug clusters and next actions.

There is no real test infrastructure. A deterministic mock data engine generates
the test plan and test run data that the agent analyses.

## Status

| Step | Scope | State |
|---|---|---|
| 1 | Project scaffolding and config | Done |
| 2 | One-time test plan generator | Done |
| 3 | Sample test run generator (8 firmware builds) | Done, in review |
| 4 | Data validation layer | Next |
| 5 | Analysis engine (5 intelligence outputs) | |
| 6 | Narrative layer (optional Claude API) | |
| 7 | Dashboard UI (static HTML) | |
| 8 | Tests | |
| 9 | Pipeline, docs and delivery | |

## Quick start

```bash
pip install -r requirements.txt
python run_pipeline.py                # reuses the committed test plan, regenerates runs
python run_pipeline.py --regen-plan   # rebuild the one-time test plan too
```

Everything is seeded (`config/settings.yaml` -> `seeds`), so the same config always
produces byte-identical output.

## Layout

```
config/settings.yaml               all tunables: seeds, plan sizes, simulation rates, release gates
generator/catalog.py               subsystems, 25 HCI + 15 phone features, DUT fleet, KPI matrix
generator/defect_catalog.py        curated firmware defects that drive the simulated runs
generator/test_plan_generator.py   one-time test plan -> data/test_plan.json
generator/run_generator.py         SampleTestRunGenerator -> data/runs/*.json
run_pipeline.py                    entry point
data/test_plan.json                6,000 test cases + MTBF and KPI plans
data/runs/                         one JSON per full test cycle, plus index.json
```

## Test plan (`data/test_plan.json`)

| Domain | Size | Baseline | Notes |
|---|---|---|---|
| HCI_REGRESSION | 5,000 tests, 25 features | 168 h (20 lanes) | Spec refs to BT Core v5.2 |
| PHONE_TESTING | 1,000 tests, 15 features | 24 h (8 lanes) | Each test has a peer device category |
| MTBF | 16 DUTs x 120 h | 120 h | Bench, board revision and RF module lot per DUT |
| KPI_AUDIO | 14 metrics | 72 h | Target and comparator per metric |

Every test case has an ID, name, feature, firmware subsystem, priority, type and
expected duration. Each feature is split into sub-suites: the first test is a
gating setup test and the rest `depends_on` it, so a failing gating test blocks
its sub-suite.

## Test runs (`data/runs/BT_FULL_CYCLE_<end date>_01.json`)

One full cycle per firmware build (`FW_v14.2.088` ... `FW_v14.2.109_RC3`). Each file
follows the spec's schema (`test_run_metadata`, `domain_summaries`, `jira_telemetry`)
and adds:

- **Metadata:** previous build, baseline vs actual cycle hours, cycle delta, critical-path domain.
- **HCI / Phone:** pass rate, per-feature breakdown, `failed_tests` (signature, Jira,
  retries, log reference), `blocked_tests` (blocking test and Jira), tests passed on
  retry, and the duration overhead from re-runs, diagnostic logs and isolation suites.
- **MTBF:** per-DUT details with timestamped crash events, uptime at crash,
  recovery mode and time, and per-DUT MTBF.
- **KPI:** average, p95, min/max, standard deviation, raw samples, PASS / DEGRADED /
  FAIL status, change vs previous build, and the linked Jira.
- **Jira:** every linked ticket (severity, subsystem, status, first-seen build,
  linked tests, DUTs, KPI metrics, stack trace frames for crashes), crash mapping
  and tickets resolved since the previous build.

### How the data is simulated

A defect timeline decides each build's results:

- **Curated defects** (`defect_catalog.py`) carry the main story: 10 unique crash
  signatures in the first build falling to 3 by RC3, an LC3 codec NULL dereference
  (BTFW-4012) blocking about 45 LE Audio tests, and a scheduler regression (BTFW-4031)
  that raises LE Audio failures and breaks A2DP latency at the same time.
- **Minor defects** are generated per build, each failing a few tests in one feature.
- **Single-instance failures** each get their own Jira (Medium/Trivial).
- **DUT-02** (an EVT2 board) has 3x crash susceptibility, a planted hardware anomaly.
- **Elastic duration:** failures add retry time, diagnostic log collection and
  isolation suites, so builds with more failures take longer than 7 days.
