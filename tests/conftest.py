"""Shared fixtures: one synthetic messy dataset, generated once per session."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dtp import io_utils, profile

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def synthetic_raw(tmp_path_factory) -> Path:
    """Directory holding the fixture sources with known, deliberate defects."""
    raw = tmp_path_factory.mktemp("raw")
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "make_synthetic_messy.py"),
            "--rows", "500",
            "--outdir", str(raw),
            "--seed", "42",
        ],
        check=True,
        capture_output=True,
    )
    return raw


@pytest.fixture(scope="session")
def loaded(synthetic_raw: Path):
    tables, errors = io_utils.load_all(synthetic_raw)
    assert not errors, errors
    return tables


@pytest.fixture(scope="session")
def profile_list(loaded) -> list[profile.TableProfile]:
    return [profile.profile_table(t) for t in loaded]


@pytest.fixture(scope="session")
def profiled(profile_list) -> dict[str, profile.TableProfile]:
    return {tp.name: tp for tp in profile_list}
