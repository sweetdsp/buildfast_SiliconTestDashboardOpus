# Bluetooth Test Infrastructure Dashboard Agent

An agent that analyses mock telemetry from Bluetooth silicon/platform validation runs
(BT 5.2 chipset for mobile platforms) and produces an executive dashboard: TL;DR,
release risk, anomalies, bug clusters and next actions.

There is no real test infrastructure. A deterministic mock data engine generates
the test plan and test run data that the agent analyses.

## Status

All 9 steps are complete.

| Step | Scope | Where |
|---|---|---|
| 1 | Project scaffolding and config | `config/settings.yaml`, `run_pipeline.py` |
| 2 | One-time test plan generator | `generator/test_plan_generator.py` |
| 3 | Sample test run generator (8 firmware builds) | `generator/run_generator.py` |
| 4 | Data validation layer | `validation/` |
| 5 | Analysis engine (5 intelligence outputs) | `analysis/` |
| 6 | Narrative layer (optional Claude API) | `narrative/` |
| 7 | Dashboard UI (static HTML) | `dashboard/` |
| 8 | Unit tests | `tests/` (64 tests) |
| 9 | Docs, CI and Vercel deployment | this README, `.github/workflows/ci.yml`, `vercel.json` |

The original specification is in [`Requirements/SpecDocument.pdf`](Requirements/SpecDocument.pdf).

## Quick start

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python run_pipeline.py                  # plan (reused) -> runs -> validate -> analyse -> narrate -> dashboard
open dashboard/index.html               # or double-click it: a single self-contained file
python run_pipeline.py --regen-plan     # rebuild the one-time test plan too
python run_pipeline.py --stage dashboard  # re-run a single stage

pip install -r requirements-dev.txt     # adds pytest and the Claude SDK
pytest                                  # 64 unit tests
```

The `Makefile` wraps the same commands: `make pipeline`, `make test`, `make dashboard`, `make narrative`.

Everything is seeded (`config/settings.yaml` -> `seeds`), so the same config always
produces byte-identical output. The one exception is the narrative when Claude writes it.

### Optional: Claude-written narrative

```bash
pip install -r requirements-claude.txt
export ANTHROPIC_API_KEY=...            # or `ant auth login`
python run_pipeline.py --stage narrate --narrative claude && python run_pipeline.py --stage dashboard
```

Without the SDK or credentials, the pipeline falls back to template wording and the
dashboard says which source wrote it.

## Layout

```
config/settings.yaml               all tunables: seeds, plan sizes, simulation rates, release gates
generator/catalog.py               subsystems, 25 HCI + 15 phone features, DUT fleet, KPI matrix
generator/defect_catalog.py        curated firmware defects that drive the simulated runs
generator/test_plan_generator.py   one-time test plan -> data/test_plan.json
generator/run_generator.py         SampleTestRunGenerator -> data/runs/*.json
validation/validator.py            structural + consistency checks -> data/validation_report.json
analysis/                          the 5 intelligence outputs -> data/insights.json
  tldr.py  risk.py  anomalies.py  clustering.py  next_actions.py  engine.py
narrative/narrator.py              executive wording (Claude API or template) -> data/narrative.json
dashboard/template.html            dashboard page: layout, theme tokens, SVG charts (no external libraries)
dashboard/build_dashboard.py       injects the data -> dashboard/index.html
run_pipeline.py                    entry point
tests/                             pytest suite
Requirements/SpecDocument.pdf      the original specification
vercel.json                        static deployment of the dashboard
.github/workflows/ci.yml           tests + reproducibility check on every PR
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

## Validation (`data/validation_report.json`)

About 520 checks run before analysis, and the pipeline stops if any fail. They cover:
the plan's totals, dependencies and time budget; pass + fail + blocked = total at
domain and feature level; every failed test linked to a Jira; every blocked test
depending on a failed gating test; crash totals, unique signatures and MTBF
arithmetic; KPI status vs target; Jira severity totals; cycle timing; and run order.

## Analysis engine (`data/insights.json`)

All formulas are configured under `analysis:` in `config/settings.yaml`.

| Output | What it computes |
|---|---|
| **1. TL;DR** | Pass rate over the 6,000 HCI + phone tests, cycle length vs the 7-day baseline (with re-run / log / isolation overhead), and the **health index**. Template headline bullets. |
| **2. Risk & forecast** | 5 release gates; the **must-fix set** (all Blockers, the highest-impact Criticals above the allowance, the tickets behind KPI breaches, and crash tickets until projected MTBF clears its gate); slippage forecast; MTBF per DUT and for the fleet without outliers. |
| **3. Anomalies** | DUT crash outliers (>= 2x fleet median and z >= 2), hardware-group effects without the outliers, KPI step changes coinciding with feature failure spikes (confirmed when they share a Jira), blocked-test concentrations behind gating bugs, persistent coverage gaps, new Blocker/Critical regressions. |
| **4. Clustering** | Tickets grouped by firmware subsystem and ranked by impact, candidate duplicates (same subsystem + signature), recurring vs single-instance crash signatures across builds, persistent vs new test failures. |
| **5. Next actions** | Tasks per developer role, P0-P2, each fix with a targeted **re-run subset** (failing + blocked tests, gating smoke tests of affected features, 24 h MTBF soak on affected DUTs, KPI re-measure), plus a next-build verification subset and the release decision. |

**Health index** = 0.40 x pass-rate score + 0.25 x defect score + 0.20 x MTBF score + 0.15 x KPI score.
- Pass-rate score: 90% maps to 0 and 100% to 100.
- Defect score: 100 minus 20 per open Blocker, 5 per Critical, 0.5 per Medium and 0.1 per Trivial.
- MTBF score: MTBF / 200 h gate (capped at 100) x auto-recovery rate.
- KPI score: (pass + 0.5 x degraded) / metric count.
- Bands: >= 85 GREEN, >= 70 AMBER, otherwise RED.

**Projected ready date** = last run end + fix days + triage days + one verification cycle + burn-down cycles.
- Fix days: per-ticket fix days (Blocker 5, Critical 3), serial within a subsystem team and parallel across teams.
- Triage days: failed tests x 0.5 h / 48 h per day of team capacity.
- Burn-down cycles: if the pass-rate gate still fails after the must-fix set, extra 9-day fix cycles at the failure burn-down velocity of the last 3 builds.

Slip = ready date - target date (last run end + 21 days). Status: <= 0 days ON_TRACK, <= 7 AT_RISK, otherwise OFF_TRACK.

## Narrative layer (`data/narrative.json`)

The narrative layer sends Claude (`claude-opus-5-5`, adaptive thinking, medium effort) a compact
fact sheet taken from `insights.json`, never the raw logs. The system prompt only allows
numbers, IDs and dates that appear in the fact sheet. The answer has a fixed JSON schema:
executive summary, TL;DR bullets, release statement, key risks and team briefs. Declined
requests use the server-side default fallback model. Missing credentials, API errors,
refusals and truncation all fall back to the deterministic template. Set
`narrative.mode` (or `--narrative`) to `auto`, `template` or `claude`.

## Dashboard (`dashboard/index.html`)

A single ~130 KB HTML file with the data embedded. It needs no server, makes no network
calls and opens from disk. Sections:

1. **TL;DR**: health index hero figure and score breakdown, executive summary, stat tiles.
2. **Release readiness**: go/no-go, schedule status, gate table, forecast timeline, must-fix set, open Blocker/Critical Jiras.
3. **Trends**: health, pass rate, crashes, MTBF, Blocker/Critical and cycle duration across the 8 builds, each with its gate line.
4. **Test domains**: feature x build heatmap (40 features), most-affected features, crashes per DUT, KPI table with sparklines.
5. **Anomalies & blind spots**: one card per finding, with severity.
6. **Bug clustering**: subsystem impact, candidate duplicates, crash signatures across builds.
7. **Next actions**: tasks by team (filterable), team briefs, re-run subsets with copy-to-clipboard test IDs.

Light and dark themes follow the OS setting, and a toggle overrides it. Every chart has
hover/focus tooltips and a table view. Status is always shown as icon + label, never
colour alone. The layout works down to phone width.

## Tests (`tests/`)

`pytest` runs 64 tests in a few seconds:

| File | Covers |
|---|---|
| `test_generators.py` | Spec sizes (5,000 / 1,000 tests, 25 / 15 features, 16 DUTs); gating structure; time budgets; crash convergence; elastic duration; the planted LC3 gating bug; the committed plan and runs regenerate byte-for-byte. |
| `test_validator.py` | Clean data passes. Eight kinds of corruption are each caught: totals, Jira links, orphan blocked tests, crash totals, KPI status, severity counts, timing, unknown test IDs. Wrong run order is caught too. |
| `test_analysis.py` | Health index formula (including a perfect build scoring 100 / GREEN); gates; must-fix set clears every gate; forecast arithmetic; the no-burn-down case; DUT outlier and cross-talk detection (and no false alarms on uniform data); gating concentrations; clustering; action ordering; targeted re-run subsets. |
| `test_narrative.py` | Template output. The Claude request against a mocked API: model, adaptive thinking, effort, JSON schema, fallback header. Falls back to the template on refusal, truncation, API error, missing SDK and unusable credentials. |
| `test_dashboard_and_pipeline.py` | End-to-end pipeline into a temp directory; committed `data/` and dashboard are current; the dashboard is self-contained; embedded data round-trips; `</script>` in data cannot break the page; a validation failure stops the pipeline. |

CI (`.github/workflows/ci.yml`) runs the tests on every pull request and push to `main`.
It then re-runs the pipeline and fails if anything in `data/` changed. Together with the
dashboard test, this means the committed dashboard can't drift from the code. A
Claude-written narrative is the one allowed difference.

## Deployment (Vercel)

`vercel.json` makes Vercel serve the dashboard as a static site, whatever framework preset
the Vercel project has:

- `framework: null`, plus its own install and build commands, override the project preset.
  A Next.js preset fails on this repo, which has no `package.json`.
- The build copies `dashboard/index.html` and the spec PDF into `public/`, which is served
  as the site root: `/` is the dashboard and `/SpecDocument.pdf` is the spec.
- No Python runs on Vercel. The dashboard is built by the pipeline and committed, and CI
  guarantees it is current.

To publish new data: change the config or code, run `python run_pipeline.py`, commit, push.
Vercel redeploys automatically.

## Spec traceability

| Spec requirement | Implementation |
|---|---|
| 2.1 Four test domains and their baselines | `config/settings.yaml` `test_plan:`; `generator/catalog.py` |
| 2.2 Elastic duration rule | `run_generator.py` `_simulate_test_domain` (re-runs, diagnostic logs, isolation suites); `tldr.cycle` |
| 3 One-time test plan generator | `generator/test_plan_generator.py` -> `data/test_plan.json` |
| 3 `SampleTestRunGenerator`, deterministic JSON | `generator/run_generator.py`, seeded -> `data/runs/` |
| 3.1 Schema: 25 HCI / 15 phone features, MTBF, KPI, Jira | Run files follow the schema, extended with per-test failures, DUT events and full tickets |
| 3.1 >= 10 crashes early, falling later | `defect_catalog.py`: 10 signatures in build 1 falling to 3 by RC3 |
| 4.1 #1 TL;DR: pass rate, duration delta, health 0-100 | `analysis/tldr.py`; dashboard TL;DR section |
| 4.1 #2 Gates, slippage, MTBF per DUT | `analysis/risk.py`; dashboard Release readiness section |
| 4.1 #3 DUT variance, cross-talk, blocked concentrations | `analysis/anomalies.py`; dashboard Anomalies section |
| 4.1 #4 Subsystem clusters, recurring vs single crashes | `analysis/clustering.py`; dashboard Bug clustering section |
| 4.1 #5 Role-based tasks, targeted re-run subsets | `analysis/next_actions.py`; dashboard Next actions section |
| Final output: a dashboard | `dashboard/index.html`, deployed via `vercel.json` |

## Limitations

- All data is simulated. Swapping in real telemetry means writing run files in the same
  schema; the validator, analysis and dashboard work unchanged.
- The slippage forecast is a planning model (configurable fix-day, triage and cadence
  assumptions), not a statistical prediction.
- The Claude narrative is not deterministic. The committed dashboard uses the template
  wording until you run `make narrative` and commit the result; CI accepts either.
