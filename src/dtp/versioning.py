"""Dataset versioning (Phase 1.3).

A snapshot is a dated directory of Parquet files plus a manifest. The manifest is
the point: it records the row count, column count, dtypes and a content hash for
every table, so "which data produced this number?" has an answer that does not
depend on anyone remembering.

Content hashing rather than file hashing. Parquet embeds a writer version and
compresses non-deterministically, so two byte-different files can hold identical
data; hashing the values means an unchanged snapshot is recognisable as unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import VERSIONS_DIR

MANIFEST_NAME = "manifest.json"
# Snapshot ids sort lexicographically into chronological order, which is what
# makes `latest()` a max() rather than a date parse.
STAMP_FORMAT = "%Y%m%dT%H%M%S"


@dataclass
class TableVersion:
    table: str
    rows: int
    columns: int
    content_hash: str
    dtypes: dict[str, str]
    null_counts: dict[str, int]
    file: str


@dataclass
class Manifest:
    version_id: str
    created_at: str
    dtp_version: str
    source_dir: str
    tables: list[TableVersion] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    @property
    def total_rows(self) -> int:
        return sum(t.rows for t in self.tables)

    def table(self, name: str) -> TableVersion | None:
        return next((t for t in self.tables if t.table == name), None)


def content_hash(df: pd.DataFrame) -> str:
    """Stable hash of the values, independent of how Parquet chose to write them."""
    h = hashlib.sha256()
    # Column names and dtypes are part of the identity: same numbers under
    # different names is a different dataset.
    for col in df.columns:
        h.update(str(col).encode("utf-8"))
        h.update(str(df[col].dtype).encode("utf-8"))
    for col in df.columns:
        values = pd.util.hash_pandas_object(df[col], index=False)
        h.update(values.to_numpy().tobytes())
    return h.hexdigest()[:32]


def _describe(table: str, df: pd.DataFrame, filename: str) -> TableVersion:
    return TableVersion(
        table=table,
        rows=int(len(df)),
        columns=int(df.shape[1]),
        content_hash=content_hash(df),
        dtypes={str(c): str(t) for c, t in df.dtypes.items()},
        null_counts={str(c): int(df[c].isna().sum()) for c in df.columns},
        file=filename,
    )


def new_version_id(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime(STAMP_FORMAT)


def write_snapshot(tables: dict[str, pd.DataFrame], source_dir: Path,
                   versions_dir: Path | None = None,
                   validation: dict[str, Any] | None = None,
                   notes: str | None = None,
                   version_id: str | None = None) -> tuple[Path, Manifest]:
    from . import __version__

    versions_dir = versions_dir or VERSIONS_DIR
    version_id = version_id or new_version_id()
    target = versions_dir / version_id
    target.mkdir(parents=True, exist_ok=True)

    manifest = Manifest(
        version_id=version_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        dtp_version=__version__,
        source_dir=str(source_dir),
        validation=validation or {},
        notes=notes,
    )
    for name, df in sorted(tables.items()):
        filename = name + ".parquet"
        df.to_parquet(target / filename, index=False)
        manifest.tables.append(_describe(name, df, filename))

    (target / MANIFEST_NAME).write_text(
        json.dumps(_manifest_payload(manifest), indent=2), encoding="utf-8"
    )
    return target, manifest


def _manifest_payload(m: Manifest) -> dict[str, Any]:
    payload = asdict(m)
    payload["total_rows"] = m.total_rows
    return payload


def read_manifest(path: Path) -> Manifest:
    """`path` may be the snapshot directory or the manifest file itself."""
    if path.is_dir():
        path = path / MANIFEST_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    tables = [TableVersion(**t) for t in raw.get("tables", [])]
    return Manifest(
        version_id=raw["version_id"],
        created_at=raw["created_at"],
        dtp_version=raw.get("dtp_version", "unknown"),
        source_dir=raw.get("source_dir", ""),
        tables=tables,
        validation=raw.get("validation") or {},
        notes=raw.get("notes"),
    )


def list_versions(versions_dir: Path | None = None) -> list[Manifest]:
    versions_dir = versions_dir or VERSIONS_DIR
    if not versions_dir.exists():
        return []
    out: list[Manifest] = []
    for d in sorted(versions_dir.iterdir()):
        if d.is_dir() and (d / MANIFEST_NAME).exists():
            out.append(read_manifest(d))
    return out


def latest(versions_dir: Path | None = None) -> Manifest | None:
    versions = list_versions(versions_dir)
    return versions[-1] if versions else None


def load_version(version_id: str | None = None,
                 versions_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Read a snapshot back. Defaults to the most recent one."""
    versions_dir = versions_dir or VERSIONS_DIR
    manifest = (read_manifest(versions_dir / version_id) if version_id
                else latest(versions_dir))
    if manifest is None:
        raise FileNotFoundError("no snapshots in " + str(versions_dir))
    base = versions_dir / manifest.version_id
    return {t.table: pd.read_parquet(base / t.file) for t in manifest.tables}


@dataclass
class TableDiff:
    table: str
    status: str          # added | removed | unchanged | changed
    rows_before: int | None = None
    rows_after: int | None = None
    columns_added: list[str] = field(default_factory=list)
    columns_removed: list[str] = field(default_factory=list)
    dtype_changes: dict[str, str] = field(default_factory=dict)  # col -> "old -> new"
    null_changes: dict[str, str] = field(default_factory=dict)

    @property
    def row_delta(self) -> int | None:
        if self.rows_before is None or self.rows_after is None:
            return None
        return self.rows_after - self.rows_before


def diff(old: Manifest, new: Manifest) -> list[TableDiff]:
    """What changed between two snapshots.

    Deliberately manifest-only: no Parquet is read. Comparing two snapshots is
    something you want to do cheaply and often, and the manifest was written to
    make that possible - if the content hashes match, nothing else needs asking.
    """
    out: list[TableDiff] = []
    names = sorted({t.table for t in old.tables} | {t.table for t in new.tables})
    for name in names:
        before, after = old.table(name), new.table(name)
        if before is None:
            out.append(TableDiff(name, "added", rows_after=after.rows))
            continue
        if after is None:
            out.append(TableDiff(name, "removed", rows_before=before.rows))
            continue
        if before.content_hash == after.content_hash:
            out.append(TableDiff(name, "unchanged",
                                 rows_before=before.rows, rows_after=after.rows))
            continue

        d = TableDiff(name, "changed", rows_before=before.rows, rows_after=after.rows)
        d.columns_added = [c for c in after.dtypes if c not in before.dtypes]
        d.columns_removed = [c for c in before.dtypes if c not in after.dtypes]
        d.dtype_changes = {
            c: before.dtypes[c] + " -> " + after.dtypes[c]
            for c in after.dtypes
            if c in before.dtypes and before.dtypes[c] != after.dtypes[c]
        }
        # Nulls appearing where there were none is the quiet failure mode: the
        # schema still matches, the row count still matches, and a metric moves.
        d.null_changes = {
            c: str(before.null_counts.get(c, 0)) + " -> " + str(after.null_counts[c])
            for c in after.null_counts
            if before.null_counts.get(c, 0) != after.null_counts[c]
        }
        out.append(d)
    return out


def format_diff(old: Manifest, new: Manifest) -> str:
    lines = ["# Snapshot diff", "",
             "`" + old.version_id + "` -> `" + new.version_id + "`", ""]
    diffs = diff(old, new)
    if all(d.status == "unchanged" for d in diffs):
        lines.append("No change: every table's content hash is identical.")
        return "\n".join(lines) + "\n"

    for d in diffs:
        lines.append("## " + d.table + " - " + d.status)
        if d.row_delta is not None and d.row_delta:
            lines.append("- rows: " + str(d.rows_before) + " -> " + str(d.rows_after)
                         + " (" + ("+" if d.row_delta > 0 else "") + str(d.row_delta) + ")")
        elif d.rows_after is not None:
            lines.append("- rows: " + str(d.rows_after))
        for label, cols in (("columns added", d.columns_added),
                            ("columns removed", d.columns_removed)):
            if cols:
                lines.append("- " + label + ": " + ", ".join(cols))
        for label, changes in (("dtype changes", d.dtype_changes),
                               ("null-count changes", d.null_changes)):
            if changes:
                lines.append("- " + label + ":")
                lines.extend("  - `" + c + "`: " + v for c, v in sorted(changes.items()))
        lines.append("")
    return "\n".join(lines) + "\n"


def format_versions(versions: list[Manifest]) -> str:
    if not versions:
        return "No snapshots yet.\n"
    lines = []
    for m in versions:
        status = m.validation.get("status", "?")
        lines.append(m.version_id + "  " + m.created_at
                     + "  tables=" + str(len(m.tables))
                     + "  rows=" + format(m.total_rows, ",")
                     + "  validation=" + str(status))
        for t in m.tables:
            lines.append("    " + t.table.ljust(14) + format(t.rows, ">10,")
                         + " rows  " + str(t.columns).rjust(3) + " cols  "
                         + t.content_hash[:12])
    return "\n".join(lines) + "\n"


