"""Narrative layer: template wording, the Claude path against a mocked API, and fallbacks."""

import builtins
import json

import pytest

from narrative import narrator

CFG = {"model": "claude-opus-5-5", "effort": "medium", "max_tokens": 16000}
FIELDS = {"executive_summary": "Summary.", "tldr_bullets": ["a", "b"], "release_statement": "NO-GO.",
          "key_risks": ["r"], "team_briefs": [{"team": "Release Lead", "brief": "Hold."}]}


def _mock_client(monkeypatch, *, stop_reason="end_turn", status=200, seen=None):
    """Route anthropic.Anthropic() to an in-memory transport returning a canned reply."""
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")

    def handler(request):
        if seen is not None:
            seen["body"] = json.loads(request.content)
            seen["beta"] = request.headers.get("anthropic-beta")
        if status != 200:
            return httpx.Response(status, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
        content = [] if stop_reason == "refusal" else [{"type": "text", "text": json.dumps(FIELDS)}]
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: real(
        api_key="test", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))))


def test_template_mode_never_calls_the_api(insights):
    n = narrator.build_narrative(insights, {**CFG, "mode": "template"})
    assert n["source"] == "template" and n["model"] is None
    for key in ("executive_summary", "tldr_bullets", "release_statement", "key_risks", "team_briefs"):
        assert n[key]
    assert insights["tldr"]["firmware_build"] in n["executive_summary"]
    assert {b["team"] for b in n["team_briefs"]} == set(insights["next_actions"]["by_team"])


def test_template_team_briefs_keep_full_titles(insights):
    briefs = " ".join(b["brief"] for b in narrator.template_narrative(insights)["team_briefs"])
    assert "Robust caching: database hash stale after Service Changed indication" in briefs


def test_fact_sheet_is_compact_and_grounded(insights):
    sheet = narrator.fact_sheet(insights)
    text = json.dumps(sheet)
    assert len(text) < 20000                      # never the raw logs
    assert sheet["build"] == insights["tldr"]["firmware_build"]
    assert {m["jira_id"] for m in sheet["forecast"]["must_fix"]} == \
        {m["jira_id"] for m in insights["risk"]["forecast"]["must_fix"]}


def test_claude_path_request_and_parse(monkeypatch, insights):
    seen = {}
    _mock_client(monkeypatch, seen=seen)
    n = narrator.build_narrative(insights, {**CFG, "mode": "claude"})
    assert n["source"] == "claude" and n["model"] == "claude-opus-5-5"
    assert n["executive_summary"] == "Summary."
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default" and seen["beta"] == "server-side-fallback-2026-07-01"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["schema"] == narrator.SCHEMA


@pytest.mark.parametrize("kwargs, reason", [
    ({"stop_reason": "refusal"}, "request declined by the model"),
    ({"stop_reason": "max_tokens"}, "response truncated at max_tokens"),
    ({"status": 500}, "API error 500"),
])
def test_auto_mode_falls_back_to_template(monkeypatch, insights, kwargs, reason):
    _mock_client(monkeypatch, **kwargs)
    n = narrator.build_narrative(insights, {**CFG, "mode": "auto"})
    assert n["source"] == "template"
    assert n["fallback_reason"] == reason


def test_claude_mode_raises_when_unavailable(monkeypatch, insights):
    _mock_client(monkeypatch, stop_reason="refusal")
    with pytest.raises(narrator.NarrativeUnavailable):
        narrator.build_narrative(insights, {**CFG, "mode": "claude"})


def test_missing_sdk_falls_back(monkeypatch, insights):
    real_import = builtins.__import__

    def no_anthropic(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    n = narrator.build_narrative(insights, {**CFG, "mode": "auto"})
    assert n["source"] == "template"
    assert n["fallback_reason"].startswith("anthropic SDK not installed")


def test_missing_credentials_fall_back(monkeypatch, insights):
    pytest.importorskip("anthropic")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent")
    monkeypatch.setenv("HOME", "/nonexistent")
    n = narrator.build_narrative(insights, {**CFG, "mode": "auto"})
    assert n["source"] == "template"
