"""Write the executive narrative from insights.json.

Claude only rewords facts the analysis engine already computed: the prompt
carries a compact fact sheet (never raw logs) and a JSON schema for the answer.
When the SDK or credentials are unavailable, the API errors, or the model
declines, a deterministic template produces the same fields, so the dashboard
always has a narrative.

Modes (config `narrative.mode`):
  auto     - use Claude when possible, otherwise the template (default)
  template - never call the API
  claude   - call the API and fail if it is unavailable
"""

import json
import re

SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "tldr_bullets": {"type": "array", "items": {"type": "string"}},
        "release_statement": {"type": "string"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "team_briefs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"team": {"type": "string"}, "brief": {"type": "string"}},
                "required": ["team", "brief"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["executive_summary", "tldr_bullets", "release_statement", "key_risks", "team_briefs"],
    "additionalProperties": False,
}

SYSTEM = """You write the executive narrative for a Bluetooth chipset validation dashboard read by \
engineering managers and release leads.

You receive a fact sheet computed by an analysis engine. Use only those facts: every number, build \
name, Jira ID, DUT and date you write must appear in the fact sheet, and you must not add causes or \
predictions it does not state. Write plainly, lead with what matters most for the release decision, \
and keep it short:
- executive_summary: 3-4 sentences covering build health, release readiness and the single most \
important next step.
- tldr_bullets: 4-6 one-line bullets (pass rate, cycle duration vs baseline, health index, MTBF, \
KPI breaches, open Blocker/Critical Jiras).
- release_statement: one or two sentences with the go/no-go call, projected ready date vs target \
and the slip.
- key_risks: 3-5 one-line risks drawn from the gates, anomalies and clusters.
- team_briefs: one brief per team that has actions, 1-2 sentences each, naming its Jira IDs."""


def fact_sheet(insights):
    """Compact, model-friendly subset of insights.json."""
    t, r, a, c, n = (insights[k] for k in ("tldr", "risk", "anomalies", "clusters", "next_actions"))
    fc = r["forecast"]
    return {
        "build": t["firmware_build"],
        "previous_build": t["previous_firmware_build"],
        "health_index": {"score": t["health_index"]["score"], "band": t["health_index"]["band"],
                         "change_vs_previous": t["health_delta"],
                         "components": {k: v["score"] for k, v in t["health_index"]["components"].items()}},
        "tests": {**t["test_totals"], "pass_rate_change_pts": t["pass_rate_delta_pts"]},
        "cycle": t["cycle"],
        "mtbf": {"fleet_mtbf_hours": r["mtbf"]["fleet_mtbf_hours"], "gate_hours": r["mtbf"]["gate_hours"],
                 "crashes": t["total_crashes"], "unique_signatures": t["unique_crashes"],
                 "mtbf_excluding_outliers_hours": r["mtbf"]["mtbf_excluding_outliers_hours"]},
        "kpi": t["kpi_summary"],
        "open_blockers": t["open_blockers"],
        "open_criticals": t["open_criticals"],
        "release_gates": r["gates"],
        "release_recommendation": r["release_recommendation"],
        "forecast": {"target_date": fc["target_release_date"][:10],
                     "projected_ready_date": (fc["projected_ready_date"] or "")[:10] or None,
                     "slip_days": fc["slip_days"], "status": fc["schedule_status"],
                     "breakdown_days": fc["breakdown_days"],
                     "must_fix": [{"jira_id": m["jira_id"], "title": m["title"], "severity": m["severity"],
                                   "required_by": m["required_by"]} for m in fc["must_fix"]]},
        "anomalies": [f["summary"] for k in ("dut_variance", "hardware_groups", "cross_talk",
                                             "blocked_concentrations", "regressions", "coverage_gaps")
                      for f in a[k]],
        "top_subsystem_clusters": [{"subsystem": s["subsystem_name"], "tickets": s["tickets"],
                                    "impact_score": s["impact_score"]} for s in c["subsystems"][:4]],
        "crash_signatures": {k: c["crash_signatures"][k] for k in ("recurring", "single_instance", "active", "resolved")},
        "next_actions": [{"id": x["id"], "priority": x["priority"], "team": x["team"], "action": x["action"]}
                         for x in n["actions"]],
    }


def _headline(action):
    """First clause of an action ("Fix BTFW-4012 (...) immediately"), without the impact detail."""
    m = re.match(r"^(Fix .*?\)(?: immediately)?) to ", action)
    return m.group(1) if m else action.split(". ")[0].rstrip(".")


def template_narrative(insights):
    """Deterministic wording built straight from the analysis results."""
    t, r, n = insights["tldr"], insights["risk"], insights["next_actions"]
    fc = r["forecast"]
    failing = [g["gate"] for g in r["gates"] if g["status"] == "FAIL"]
    first = n["actions"][0] if n["actions"] else None
    ready = (fc["projected_ready_date"] or "n/a")[:10]
    summary = (
        f"{t['firmware_build']} scores {t['health_index']['score']}/100 ({t['health_index']['band']}) on the build "
        f"health index with a {t['test_totals']['pass_rate_percent']}% pass rate. "
        f"Release recommendation is {r['release_recommendation']}: {r['gates_passed']} of {r['gates_total']} gates pass"
        + (f"; failing: {', '.join(failing)}." if failing else ".")
        + f" Projected ready {ready} against a {fc['target_release_date'][:10]} target ({fc['schedule_status']})."
        + (f" Top priority: {_headline(first['action'])}." if first else "")
    )
    slip = f"{fc['slip_days']:+} days" if fc["slip_days"] is not None else "no credible forecast"
    release = (f"{r['release_recommendation']}: projected ready {ready} vs target {fc['target_release_date'][:10]} "
               f"({slip}, {fc['schedule_status']}). Must fix: "
               + ", ".join(m["jira_id"] for m in fc["must_fix"]) + ".")
    risks = [f"{g['gate']}: {g['actual']} (needs {g['threshold']})" for g in r["gates"] if g["status"] == "FAIL"]
    anomalies = insights["anomalies"]
    risks += [f["summary"] for k in ("blocked_concentrations", "cross_talk", "dut_variance")
              for f in anomalies[k]][:3]
    briefs = []
    for team, ids in n["by_team"].items():
        acts = [a for a in n["actions"] if a["id"] in ids]
        briefs.append({"team": team, "brief": "; ".join(f"{a['priority']} {_headline(a['action'])}" for a in acts) + "."})
    return {"executive_summary": summary, "tldr_bullets": t["headline"], "release_statement": release,
            "key_risks": risks[:6], "team_briefs": briefs}


def claude_narrative(insights, cfg):
    """Ask Claude for the narrative. Returns (fields, model) or raises NarrativeUnavailable."""
    try:
        import anthropic
    except ImportError as e:
        raise NarrativeUnavailable("anthropic SDK not installed (pip install -r requirements-claude.txt)") from e

    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # re-run on Anthropic's recommended model if the request is declined
            thinking={"type": "adaptive"},
            output_config={"effort": cfg["effort"], "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": "Fact sheet:\n" + json.dumps(fact_sheet(insights), indent=1)}],
        )
    except TypeError as e:  # raised by the SDK when no API key, auth token or profile is configured
        raise NarrativeUnavailable(f"client not configured (usually missing credentials): {e}") from e
    except anthropic.CredentialsError as e:  # a selected profile or config file that cannot be used
        raise NarrativeUnavailable(f"credentials not usable: {e}") from e
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise NarrativeUnavailable(f"credentials rejected: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise NarrativeUnavailable("rate limited") from e
    except anthropic.APIStatusError as e:
        raise NarrativeUnavailable(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise NarrativeUnavailable(f"cannot reach the API: {e}") from e

    if response.stop_reason == "refusal":
        raise NarrativeUnavailable("request declined by the model")
    if response.stop_reason == "max_tokens":
        raise NarrativeUnavailable("response truncated at max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), response.model


class NarrativeUnavailable(Exception):
    pass


def build_narrative(insights, cfg):
    gen = insights["generated_from"]
    base = {"run_id": gen["latest_run_id"], "firmware_build": gen["firmware_build"]}
    if cfg["mode"] != "template":
        try:
            fields, model = claude_narrative(insights, cfg)
            return {**base, "source": "claude", "model": model, "fallback_reason": None, **fields}
        except NarrativeUnavailable as e:
            if cfg["mode"] == "claude":
                raise
            reason = str(e).split(":")[0]
    else:
        reason = "template mode"
    return {**base, "source": "template", "model": None, "fallback_reason": reason, **template_narrative(insights)}
