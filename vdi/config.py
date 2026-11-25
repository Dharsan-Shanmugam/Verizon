"""Central configuration: paths, thresholds and model settings.

Values come from config/settings.json, the root .env file, and env vars:
  VDI_DATA_DIR   folder that holds the 37 CSV files
  VDI_HOST / VDI_PORT   web server bind address
  VDI_CACHE_DIR / VDI_ARTIFACTS_DIR   alternative cache / artifact folders
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False, interpolate=False)
SETTINGS_FILE = ROOT / "config" / "settings.json"
VERSION = "1.0.0"
MODEL_VERSION = "vdi-1.0"


@lru_cache(maxsize=1)
def settings() -> dict:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _resolve(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else (ROOT / path)


def data_dir() -> Path:
    env = os.environ.get("VDI_DATA_DIR")
    if env:
        return _resolve(env)
    primary = _resolve(settings()["data_dir"])
    if primary.exists():
        return primary
    # tolerate the CSVs being unzipped directly into ./data/
    flat = ROOT / "data"
    if (flat / "01_venues.csv").exists():
        return flat
    return primary


def cache_dir() -> Path:
    p = _resolve(os.environ.get("VDI_CACHE_DIR", settings()["cache_dir"]))
    p.mkdir(parents=True, exist_ok=True)
    return p


def artifacts_dir() -> Path:
    p = _resolve(os.environ.get("VDI_ARTIFACTS_DIR", settings()["artifacts_dir"]))
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit_dir() -> Path:
    p = _resolve(os.environ.get("VDI_AUDIT_DIR", settings()["audit_dir"]))
    p.mkdir(parents=True, exist_ok=True)
    return p


def static_dir() -> Path:
    return ROOT / "static"


def host() -> str:
    return os.environ.get("VDI_HOST", settings()["server"]["host"])


def port() -> int:
    return int(os.environ.get("VDI_PORT", settings()["server"]["port"]))


def thresholds() -> dict:
    return settings()["thresholds"]


def steering() -> dict:
    return settings()["steering"]


def model_cfg() -> dict:
    return settings()["models"]


TARGET_VENUE = settings()["target_venue"]
LIVE_EVENT_ID = settings()["live_event_id"]
TODAY = settings()["today"]
TRAIN_END_YEAR = settings()["split"]["train_end_year"]
