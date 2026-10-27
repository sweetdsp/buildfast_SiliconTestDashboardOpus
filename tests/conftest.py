"""Shared fixtures: the committed config, test plan and runs, plus fresh analysis output."""

import copy

import pytest

from analysis.engine import analyse
from generator.common import load_config, read_json, resolve


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def plan(config):
    return read_json(config["paths"]["test_plan"])


@pytest.fixture(scope="session")
def runs(config):
    runs_dir = resolve(config["paths"]["runs_dir"])
    index = read_json(runs_dir / "index.json")
    return [read_json(runs_dir / e["file"]) for e in index["runs"]]


@pytest.fixture(scope="session")
def index(config):
    return read_json(resolve(config["paths"]["runs_dir"]) / "index.json")


@pytest.fixture(scope="session")
def insights(config, plan, runs):
    return analyse(config, plan, runs)


@pytest.fixture
def latest_run(runs):
    """A private copy of the latest run that a test may mutate."""
    return copy.deepcopy(runs[-1])
