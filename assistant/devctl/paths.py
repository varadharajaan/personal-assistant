"""Configured path definitions for personal-assistant local tooling."""

from __future__ import annotations

from pathlib import Path

from .config import get_list, get_path, project_root

PROJECT_ROOT = project_root()
DESKTOP_ROOT = get_path("paths.desktop_root")

OPENCLAW_PROJECT_DIR = get_path("paths.openclaw_project_dir")
OPENCLAW_WORKSPACE_DIR = get_path("paths.openclaw_workspace_dir")
USER_OPENCLAW_DIR = get_path("paths.user_openclaw_dir")

EXAMPLE_APP_DIR = get_path("paths.example_app_dir")
JAR_DIR = get_path("paths.jar_dir")

DATA_DIR = get_path("paths.data_dir")
MOBILE_DIR = get_path("paths.mobile_dir")
RUNS_DIR = get_path("paths.runs_dir")
OPENCLAW_RUNS_DIR = get_path("paths.openclaw_runs_dir")
REPORTS_DIR = get_path("paths.reports_dir")

MOBILE_COMMAND_EVENTS_FILE = get_path("paths.mobile_command_events_file")

LOGS_DIR = get_path("paths.logs_dir")
UNIFIED_LOGS_DIR = get_path("paths.unified_logs_dir")
PY_LOGS_DIR = get_path("paths.py_logs_dir")
PS1_LOGS_DIR = get_path("paths.ps1_logs_dir")
CHECKPOINT_FILE = get_path("paths.checkpoint_file")


def ensure_runtime_dirs() -> None:
    """Create configured runtime directories that are safe to create eagerly."""

    for key in get_list("paths.runtime_dir_keys"):
        get_path(f"paths.{key}").mkdir(parents=True, exist_ok=True)
