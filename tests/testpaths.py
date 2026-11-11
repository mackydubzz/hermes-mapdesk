"""Shared test paths: scratch root override for CI (MAPDESK_TEST_SCRATCH)."""
import os
from pathlib import Path


def scratch_dir() -> Path:
    root = Path(os.environ.get("MAPDESK_TEST_SCRATCH")
                or Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch")
    root.mkdir(parents=True, exist_ok=True)
    return root
