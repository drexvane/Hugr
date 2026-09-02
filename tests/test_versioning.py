"""Tests for dataset versioning (Phase 1.3).

The manifest is the thing downstream work trusts, so the tests are about whether
it can be trusted: is the hash stable across a Parquet round-trip, is it
sensitive to changes that matter, and does `diff` notice the quiet failure mode
where the schema and row count both still look right.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from dtp import versioning as v


@pytest.fixture
def frames() -> dict[str, pd.DataFrame]:
    return {
        "a": pd.DataFrame({
            "id": pd.array([1, 2, 3], dtype="Int64"),
            "label": pd.array(["x", "y", None], dtype="string"),
            "amount": pd.array([1.5, 2.5, 3.5], dtype="Float64"),
        }),
        "b": pd.DataFrame({"k": pd.array([9], dtype="Int64")}),
    }


@pytest.fixture
def snapshot(frames, tmp_path):
    def _write(tables=None, version_id="20260101T000000", **kwargs):
        return v.write_snapshot(tables or frames, source_dir=tmp_path / "raw",
                                versions_dir=tmp_path / "versions",
                                version_id=version_id, **kwargs)
    return _write


# --------------------------------------------------------------------------- #
# content hashing
# --------------------------------------------------------------------------- #

def test_hash_is_stable_across_a_parquet_round_trip(frames, tmp_path):
    """The reason the hash is over values and not over bytes."""
    df = frames["a"]
    path = tmp_path / "a.parquet"
    df.to_parquet(path, index=False)
    assert v.content_hash(pd.read_parquet(path)) == v.content_hash(df)


def test_hash_is_stable_across_repeated_writes(frames, tmp_path):
    """Parquet compresses non-deterministically; the hash must not inherit that."""
    a, b = tmp_path / "1.parquet", tmp_path / "2.parquet"
    frames["a"].to_parquet(a, index=False)
    frames["a"].to_parquet(b, index=False)
    assert (v.content_hash(pd.read_parquet(a))
            == v.content_hash(pd.read_parquet(b)))


def test_hash_notices_a_changed_value(frames):
    changed = frames["a"].copy()
    changed.loc[0, "amount"] = 1.51
    assert v.content_hash(changed) != v.content_hash(frames["a"])


def test_hash_notices_a_renamed_column(frames):
    """Same numbers under a different name is a different dataset."""
    renamed = frames["a"].rename(columns={"amount": "total"})
    assert v.content_hash(renamed) != v.content_hash(frames["a"])


def test_hash_notices_a_dtype_change(frames):
    retyped = frames["a"].astype({"id": "Int32"})
    assert v.content_hash(retyped) != v.content_hash(frames["a"])


def test_hash_notices_reordered_rows(frames):
    """Row order is part of the identity: a stable snapshot means a stable file."""
    shuffled = frames["a"].iloc[::-1].reset_index(drop=True)
    assert v.content_hash(shuffled) != v.content_hash(frames["a"])


# --------------------------------------------------------------------------- #
# writing and reading back
# --------------------------------------------------------------------------- #

def test_write_snapshot_records_what_it_wrote(snapshot, frames):
    target, manifest = snapshot(validation={"status": "PASS"}, notes="first")
    assert (target / v.MANIFEST_NAME).exists()
    assert sorted(p.name for p in target.glob("*.parquet")) == ["a.parquet",
                                                               "b.parquet"]
    assert manifest.total_rows == 4
    a = manifest.table("a")
    assert a.rows == 3 and a.columns == 3
    assert a.null_counts["label"] == 1
    assert a.dtypes["id"] == "Int64"
    assert manifest.validation["status"] == "PASS"
    assert manifest.notes == "first"


def test_manifest_round_trips_through_json(snapshot):
    target, written = snapshot()
    read = v.read_manifest(target)
    assert read == written                       # dataclass equality, field by field
    # Accepting either the directory or the file is the ergonomic bit worth pinning.
    assert v.read_manifest(target / v.MANIFEST_NAME) == read


def test_manifest_payload_exposes_total_rows_for_readers(snapshot):
    target, _ = snapshot()
    raw = json.loads((target / v.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert raw["total_rows"] == 4                # derived, but written down


def test_load_version_returns_what_was_written(snapshot, frames, tmp_path):
    snapshot()
    back = v.load_version(versions_dir=tmp_path / "versions")
    assert set(back) == {"a", "b"}
    assert v.content_hash(back["a"]) == v.content_hash(frames["a"])


def test_load_version_without_snapshots_is_an_error_not_an_empty_dict(tmp_path):
    with pytest.raises(FileNotFoundError, match="no snapshots"):
        v.load_version(versions_dir=tmp_path / "nothing")


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #

def test_ids_sort_chronologically_so_latest_is_the_last(snapshot, tmp_path):
    for vid in ("20260101T000000", "20260103T000000", "20260102T000000"):
        snapshot(version_id=vid)
    versions = v.list_versions(tmp_path / "versions")
    assert [m.version_id for m in versions] == ["20260101T000000",
                                                "20260102T000000",
                                                "20260103T000000"]
    assert v.latest(tmp_path / "versions").version_id == "20260103T000000"


def test_new_version_id_is_sortable(monkeypatch):
    early = v.new_version_id(datetime(2026, 1, 2, 3, 4, 5))
    late = v.new_version_id(datetime(2026, 1, 2, 3, 4, 6))
    assert early == "20260102T030405"
    assert early < late


def test_a_directory_without_a_manifest_is_not_a_version(snapshot, tmp_path):
    """Half-written or hand-made directories must not be mistaken for snapshots."""
    snapshot()
    (tmp_path / "versions" / "scratch").mkdir()
    assert len(v.list_versions(tmp_path / "versions")) == 1


def test_no_versions_yet_is_not_an_error(tmp_path):
    assert v.list_versions(tmp_path / "absent") == []
    assert v.latest(tmp_path / "absent") is None
    assert v.format_versions([]) == "No snapshots yet.\n"


def test_format_versions_shows_the_validation_status(snapshot):
    _, m = snapshot(validation={"status": "FAIL"})
    text = v.format_versions([m])
    assert "validation=FAIL" in text
    assert m.table("a").content_hash[:12] in text


# --------------------------------------------------------------------------- #
# diff - all four statuses
# --------------------------------------------------------------------------- #

def test_diff_reports_unchanged_when_hashes_match(snapshot, frames):
    _, old = snapshot(version_id="20260101T000000")
    _, new = snapshot(version_id="20260102T000000")
    diffs = v.diff(old, new)
    assert {d.status for d in diffs} == {"unchanged"}
    assert "No change" in v.format_diff(old, new)


def test_diff_reports_added_and_removed_tables(snapshot, frames):
    _, old = snapshot(tables={"a": frames["a"]}, version_id="20260101T000000")
    _, new = snapshot(tables={"b": frames["b"]}, version_id="20260102T000000")
    by_table = {d.table: d for d in v.diff(old, new)}
    assert by_table["a"].status == "removed"
    assert by_table["a"].rows_before == 3 and by_table["a"].rows_after is None
    assert by_table["b"].status == "added"
    assert by_table["b"].rows_after == 1


def test_diff_reports_a_row_delta_with_its_sign(snapshot, frames):
    grown = pd.concat([frames["a"], frames["a"].iloc[[0]]], ignore_index=True)
    _, old = snapshot(tables={"a": frames["a"]}, version_id="20260101T000000")
    _, new = snapshot(tables={"a": grown}, version_id="20260102T000000")
    d = v.diff(old, new)[0]
    assert d.status == "changed" and d.row_delta == 1
    assert "3 -> 4 (+1)" in v.format_diff(old, new)


def test_diff_names_schema_changes(snapshot, frames):
    altered = frames["a"].drop(columns=["label"]).astype({"id": "Int32"})
    altered["extra"] = pd.array([1, 2, 3], dtype="Int64")
    _, old = snapshot(tables={"a": frames["a"]}, version_id="20260101T000000")
    _, new = snapshot(tables={"a": altered}, version_id="20260102T000000")
    d = v.diff(old, new)[0]
    assert d.columns_added == ["extra"]
    assert d.columns_removed == ["label"]
    assert d.dtype_changes == {"id": "Int64 -> Int32"}


def test_diff_catches_nulls_appearing_where_the_shape_did_not_change(snapshot,
                                                                    frames):
    """The quiet failure: same columns, same row count, a metric moves."""
    nulled = frames["a"].copy()
    nulled.loc[0, "amount"] = None
    _, old = snapshot(tables={"a": frames["a"]}, version_id="20260101T000000")
    _, new = snapshot(tables={"a": nulled}, version_id="20260102T000000")
    d = v.diff(old, new)[0]
    assert d.status == "changed"
    assert d.row_delta == 0
    assert not d.columns_added and not d.columns_removed and not d.dtype_changes
    assert d.null_changes == {"amount": "0 -> 1"}
    assert "null-count changes" in v.format_diff(old, new)


def test_row_delta_is_none_when_one_side_is_absent():
    assert v.TableDiff("t", "added", rows_after=5).row_delta is None
    assert v.TableDiff("t", "removed", rows_before=5).row_delta is None
