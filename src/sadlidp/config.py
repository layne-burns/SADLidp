"""Configuration: config/sadl.toml (SPEC.md §9) and the alias tables beside it."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

DEFAULT_CONFIG = Path("config/sadl.toml")


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict:
    path = Path(path)
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["_root"] = str(path.parent)
    return cfg


def load_alias(cfg: dict, name: str) -> dict[str, str]:
    """An alias table from config/aliases/<name>.json, or {} if absent."""
    path = Path(cfg.get("_root", "config")) / "aliases" / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def per_position(cfg: dict, section: str, slot: str, default: float) -> float:
    return float((cfg.get(section) or {}).get(slot, default))
