"""Shared helpers: config loading, JSON output, time and random utilities."""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "settings.yaml"


def load_config(path=DEFAULT_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve(path):
    """Resolve a config path relative to the repo root."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def write_json(path, payload):
    path = resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return path


def read_json(path):
    with open(resolve(path), encoding="utf-8") as fh:
        return json.load(fh)


def parse_time(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_time(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_hours(dt, hours):
    return dt + timedelta(hours=hours)


def lerp(first, last, index, count):
    """Linear interpolation across builds: index 0 -> first, index count-1 -> last."""
    if count <= 1:
        return last
    return first + (last - first) * index / (count - 1)


def poisson(rng, lam):
    """Knuth's Poisson sampler (lam is small here, so this is fine)."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def weighted_choice(rng, weights):
    """Pick a key from a {key: weight} dict."""
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def apportion(total, weights):
    """Split an integer total across weights (largest remainder), preserving the sum."""
    wsum = sum(weights)
    raw = [total * w / wsum for w in weights]
    counts = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    return counts
