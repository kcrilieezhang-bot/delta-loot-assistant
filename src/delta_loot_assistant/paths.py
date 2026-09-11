from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
RESOURCE_ROOT = (
    Path(sys._MEIPASS) / "resources"  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False)
    else PROJECT_ROOT / "resources"
)


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    path = root / "DeltaLootAssistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_database_path() -> Path:
    return user_data_dir() / "assistant.sqlite3"


def orzice_token_path() -> Path:
    return user_data_dir() / "orzice-token.dpapi"


def sample_catalog_path() -> Path:
    return RESOURCE_ROOT / "sample_catalog.json"


def sample_price_pack_path() -> Path:
    return RESOURCE_ROOT / "sample_price_pack.json"


def app_config_path() -> Path:
    return RESOURCE_ROOT / "app_config.json"


def template_root() -> Path:
    path = user_data_dir() / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def contribution_root() -> Path:
    path = user_data_dir() / "contributed_samples"
    path.mkdir(parents=True, exist_ok=True)
    return path


def layout_profile_path() -> Path:
    return user_data_dir() / "layout_1080p.json"
