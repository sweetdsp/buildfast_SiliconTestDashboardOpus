"""Dashboard build and the end-to-end pipeline."""

import copy
import json
import re

import pytest
import yaml

import run_pipeline
from dashboard import build_dashboard
from generator.common import read_json, resolve


def _embedded_data(html):
    m = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', html, re.S)
    assert m, "dashboard data block missing"
    return json.loads(m.group(1))


@pytest.mark.parametrize("name, short", [
    ("FW_v14.2.088", ".088"), ("FW_v14.2.106_RC1", "RC1"), ("FW_v14.2.109_RC3", "RC3")])
def test_short_build_labels(name, short):
    assert build_dashboard.short_build(name) == short


def test_heatmap_rates(runs):
    heat = build_dashboard.heatmap(runs)
    assert len(heat["HCI_REGRESSION"]) == 25 and len(heat["PHONE_TESTING"]) == 15
    for rows in heat.values():
        for r in rows:
            assert len(r["cells"]) == len(runs)
            for c in r["cells"]:
                assert c["rate"] == pytest.approx(100 * (c["fail"] + c["blocked"]) / c["total"], abs=0.01)


@pytest.fixture(scope="module")
def pipeline_outputs(tmp_path_factory):
    """Run every stage into a temp directory (reusing the committed test plan)."""
    tmp = tmp_path_factory.mktemp("pipeline")
    cfg = yaml.safe_load(resolve("config/settings.yaml").read_text())
    cfg["paths"] = {
        "test_plan": str(resolve(cfg["paths"]["test_plan"])),
        "runs_dir": str(tmp / "runs"),
        "validation_report": str(tmp / "validation_report.json"),
        "insights": str(tmp / "insights.json"),
        "narrative": str(tmp / "narrative.json"),
        "dashboard": str(tmp / "index.html"),
    }
    cfg_path = tmp / "settings.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    code = run_pipeline.main(["--config", str(cfg_path), "--narrative", "template"])
    return code, tmp


def test_pipeline_runs_end_to_end(pipeline_outputs):
    code, tmp = pipeline_outputs
    assert code == 0
    for name in ("validation_report.json", "insights.json", "narrative.json", "index.html", "runs/index.json"):
        assert (tmp / name).exists(), name
    assert read_json(tmp / "validation_report.json")["status"] == "PASS"


def test_committed_outputs_are_current(pipeline_outputs, config):
    """The committed data/ and dashboard match a fresh pipeline run (template narrative)."""
    _, tmp = pipeline_outputs
    assert read_json(tmp / "insights.json") == read_json(config["paths"]["insights"])
    committed = read_json(config["paths"]["narrative"])
    if committed["source"] != "template":
        pytest.skip("committed narrative was written by Claude; only the template output is reproducible")

    def without_reason(n):  # auto and template mode differ only in why the template was used
        return {k: v for k, v in n.items() if k != "fallback_reason"}

    assert without_reason(read_json(tmp / "narrative.json")) == without_reason(committed)
    fresh_html = (tmp / "index.html").read_text()
    committed_html = resolve(config["paths"]["dashboard"]).read_text()
    strip = lambda html: re.sub(r'(id="dashboard-data">).*?(</script>)', r"\1\2", html, flags=re.S)
    assert strip(fresh_html) == strip(committed_html)
    fresh, old = _embedded_data(fresh_html), _embedded_data(committed_html)
    fresh["narrative"], old["narrative"] = without_reason(fresh["narrative"]), without_reason(old["narrative"])
    assert fresh == old


def test_dashboard_is_self_contained(pipeline_outputs):
    _, tmp = pipeline_outputs
    html = (tmp / "index.html").read_text()
    assert not re.search(r"<script[^>]+\bsrc=", html), "no external scripts"
    assert not re.search(r"<link[^>]+stylesheet", html), "no external stylesheets"
    assert "http://" not in html.replace('xmlns="http://www.w3.org/2000/svg"', "").replace(
        '"http://www.w3.org/2000/svg"', "")
    for section in ("tldr", "release", "trends", "domains", "anomalies", "clusters", "actions"):
        assert f'href="#{section}"' in html


def test_dashboard_data_round_trips(pipeline_outputs):
    _, tmp = pipeline_outputs
    data = _embedded_data((tmp / "index.html").read_text())
    insights = read_json(tmp / "insights.json")
    assert data["insights"] == insights
    assert data["meta"]["firmware_build"] == insights["tldr"]["firmware_build"]
    assert len(data["builds"]) == len(data["build_labels"]) == len(insights["trends"])


def test_embedded_data_cannot_close_the_script_tag(config, tmp_path, monkeypatch):
    """A '</script>' inside any data string must not break out of the JSON block."""
    insights = copy.deepcopy(read_json(config["paths"]["insights"]))
    insights["tldr"]["headline"][0] = "</script><script>alert(1)</script>"
    (tmp_path / "insights.json").write_text(json.dumps(insights))
    cfg = copy.deepcopy(config)
    cfg["paths"] = {**config["paths"], "insights": str(tmp_path / "insights.json"),
                    "dashboard": str(tmp_path / "index.html")}
    html = build_dashboard.build(cfg).read_text()
    assert html.count("</script>") == 2               # the data block and the app script only
    assert _embedded_data(html)["insights"]["tldr"]["headline"][0].startswith("</script>")


def test_validation_failure_stops_pipeline(tmp_path):
    cfg = yaml.safe_load(resolve("config/settings.yaml").read_text())
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    src = resolve(cfg["paths"]["runs_dir"])
    index = read_json(src / "index.json")
    for e in index["runs"]:
        run = read_json(src / e["file"])
        if e is index["runs"][-1]:
            run["domain_summaries"][0]["failed"] += 1
        (runs_dir / e["file"]).write_text(json.dumps(run))
    (runs_dir / "index.json").write_text(json.dumps(index))
    cfg["paths"] = {**{k: str(resolve(v)) for k, v in cfg["paths"].items()},
                    "runs_dir": str(runs_dir), "validation_report": str(tmp_path / "report.json"),
                    "insights": str(tmp_path / "insights.json")}
    cfg_path = tmp_path / "settings.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    assert run_pipeline.main(["--config", str(cfg_path), "--stage", "validate"]) == 1
    assert read_json(tmp_path / "report.json")["status"] == "FAIL"
