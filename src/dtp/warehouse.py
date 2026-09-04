"""Query layer: DuckDB over the versioned Parquet snapshots (Phase 2.2).

Every consumer of the clean data - the dashboard now, the agent in Phase 3 - goes
through here rather than opening Parquet itself. That buys three things:

1. **One snapshot per session.** A view reads `data/versions/<id>/`, never
   `data/clean/`, so a pipeline run starting mid-presentation cannot change the
   numbers under a chart. The snapshot id is on screen.
2. **The join keys exist.** `department_name`, `category_name` and `product_name`
   are lowercase in `access_logs` and Title Case in `order_items`, so a literal
   join returns **zero rows** - a silent empty result, not an error. The views add
   `*_key` columns alongside the originals, folded, so the obvious join is also
   the correct one. The original values are untouched: the log really does emit
   lowercase, and the clean tables stay a faithful typed copy of it.
3. **Read-only by construction.** The connection is opened against `:memory:` and
   the Parquet files are attached as views, so no query can write to a snapshot.

The fold has to reach past case. The log writes `indoor outdoor games` where the
fact table writes `Indoor/Outdoor Games`, and case-folding alone leaves those as
two orphans - 16,073 page views on one side and 19,298 order lines on the other,
the second-largest category in the data, silently absent from every funnel. So the
fold also collapses punctuation (see `FOLD`).

What it deliberately does **not** rescue is a difference in meaning. The fact table
splits the vendor's one 'Electronics' label into 'Electronics (Footwear)' and
'Electronics (Outdoors)', so its keys are `electronics footwear` and
`electronics outdoors` and neither equals the log's `electronics`; `category_id` is
the join for that one. `unmatched_categories()` names the residue rather than
letting a chart quietly drop it: after the fold, two log categories have page views
and no sales at all - `featured shops` (26,006 views) and `electronics` - and
nineteen ordered categories never appear in the log, which is expected, since the
log covers five of the thirty-seven months.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from . import VERSIONS_DIR
from . import versioning as version_mod

# Label columns that need folding, per table. The fold lowercases, replaces every
# run of non-alphanumeric characters with one space, and trims - so the log's
# trailing spaces, its lowercase, and its 'indoor outdoor games' against the fact
# table's 'Indoor/Outdoor Games' all land on the same key. Verified against both
# tables: no two distinct labels in either one collapse onto the same key, so the
# fold cannot invent a join that the labels do not support.
JOIN_KEYS: dict[str, tuple[str, ...]] = {
    "order_items": ("department_name", "category_name", "product_name"),
    "access_logs": ("department_name", "category_name", "product_name"),
}

# The fold, as SQL. `lower` is the ASCII-safe equivalent of str.casefold for this
# data (every label is ASCII); the character class is deliberately a whitelist, so
# a label that arrives with a character nobody anticipated is normalised rather
# than passed through to produce a silent zero-row join.
FOLD = "trim(regexp_replace(lower({col}), '[^a-z0-9]+', ' ', 'g'))"

# The funnel window is a property of the log, not a constant: `log_window()` reads
# it from the data. In the shipped extract that is 2017-09-01 to 2018-01-31 - the
# last 5 of the fact table's 37 months - which is why any funnel metric has to
# carry its window rather than inherit the dashboard's date filter. Hardcoding it
# would mean a new extract silently kept the old window.


@dataclass
class Warehouse:
    """A read-only DuckDB session over one snapshot."""

    manifest: version_mod.Manifest
    con: duckdb.DuckDBPyConnection
    path: Path
    _log_window: tuple[str, str] | None = None

    @property
    def version_id(self) -> str:
        return self.manifest.version_id

    @property
    def tables(self) -> list[str]:
        return sorted(t.table for t in self.manifest.tables)

    def sql(self, query: str, **params: Any) -> pd.DataFrame:
        """Run a query and return a DataFrame.

        Parameters are passed to DuckDB as `$name` bindings rather than formatted
        into the string, so a filter value can never close a quote and continue
        the statement. Callers building SQL from user or model input must use
        this, not f-strings.
        """
        rel = self.con.sql(query, params=params) if params else self.con.sql(query)
        return rel.df()

    def scalar(self, query: str, **params: Any) -> Any:
        df = self.sql(query, **params)
        if df.empty or not len(df.columns):
            return None
        return df.iat[0, 0]

    def schema(self, table: str) -> pd.DataFrame:
        """Column names and types, for the agent's prompt and the health view."""
        return self.sql("DESCRIBE SELECT * FROM " + _ident(table))

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Warehouse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _ident(name: str) -> str:
    """Quote an identifier, and refuse one that could not be a table here.

    The table list comes from the manifest, so this is belt-and-braces rather
    than the only defence - but `_ident` is what stops a crafted table name from
    reaching the SQL text at all.
    """
    if not name.replace("_", "").isalnum():
        raise ValueError("not a valid table name: " + repr(name))
    return '"' + name + '"'


def _view_sql(table: str, parquet: Path) -> str:
    """A view over one Parquet file, with the folded join keys appended."""
    folded = [
        FOLD.format(col=col) + " AS " + col.replace("_name", "") + "_key"
        for col in JOIN_KEYS.get(table, ())
    ]
    select = ", ".join(["*", *folded]) if folded else "*"
    return ("CREATE OR REPLACE VIEW " + _ident(table) + " AS SELECT " + select
            + " FROM read_parquet('" + parquet.as_posix().replace("'", "''") + "')")


def open_warehouse(version_id: str | None = None,
                   versions_dir: Path | None = None) -> Warehouse:
    """Open the newest snapshot, or a named one.

    Raises rather than falling back to `data/clean/`: a dashboard that silently
    reads unversioned output when no snapshot exists is a dashboard whose numbers
    cannot be reproduced.
    """
    versions_dir = versions_dir or VERSIONS_DIR
    if version_id:
        target = versions_dir / version_id
        if not (target / "manifest.json").exists():
            raise FileNotFoundError("no snapshot " + version_id + " in "
                                    + str(versions_dir))
        manifest = version_mod.read_manifest(target / "manifest.json")
    else:
        manifest = version_mod.latest(versions_dir)
        if manifest is None:
            raise FileNotFoundError(
                "no snapshot in " + str(versions_dir)
                + " - run `dtp pipeline` before starting the dashboard")
        target = versions_dir / manifest.version_id

    con = duckdb.connect(":memory:")
    for table in manifest.tables:
        con.execute(_view_sql(table.table, target / table.file))
    return Warehouse(manifest=manifest, con=con, path=target)


def available_versions(versions_dir: Path | None = None) -> list[version_mod.Manifest]:
    """Newest first, so a picker's first entry is the default."""
    return sorted(version_mod.list_versions(versions_dir),
                  key=lambda m: m.version_id, reverse=True)


def log_window(wh: Warehouse) -> tuple[str, str]:
    """The dates `access_logs` actually covers, as inclusive `YYYY-MM-DD` strings.

    Read from the data rather than declared, because it is the denominator of every
    funnel figure. In the shipped extract it is 2017-09-01 to 2018-01-31 against a
    fact table spanning 2015-01 to 2018-01: a conversion rate computed over the
    order window instead of this one divides a five-month numerator by a
    thirty-seven-month denominator and understates conversion about sevenfold.

    Cached on the connection - it is one `min`/`max` over a column, but every funnel
    call needs it and the snapshot cannot change underneath a session.
    """
    if wh._log_window is None:
        row = wh.sql("SELECT min(viewed_at) AS lo, max(viewed_at) AS hi "
                     "FROM access_logs")
        lo, hi = row.iat[0, 0], row.iat[0, 1]
        if pd.isna(lo) or pd.isna(hi):
            raise ValueError("access_logs is empty: there is no funnel window")
        wh._log_window = (pd.Timestamp(lo).strftime("%Y-%m-%d"),
                          pd.Timestamp(hi).strftime("%Y-%m-%d"))
    return wh._log_window


def unmatched_categories(wh: Warehouse) -> pd.DataFrame:
    """Category keys present on one side of the funnel join and not the other.

    Named rather than dropped, because each side means something different: a key
    with views and no orders is a funnel finding (`featured shops` has 26,006 page
    views and has never sold), while a key with orders and no views is usually just
    a category the five-month log does not reach. `electronics` is neither - it is
    one vendor label the fact table splits in two, and `category_id` is its join.
    """
    return wh.sql("""
        SELECT coalesce(o.category_key, a.category_key) AS category_key,
               o.orders, a.views,
               CASE WHEN o.category_key IS NULL THEN 'viewed, never ordered'
                    WHEN a.category_key IS NULL THEN 'ordered, never viewed'
               END AS side
        FROM (SELECT category_key, count(*) AS orders
                FROM order_items GROUP BY 1) o
        FULL OUTER JOIN (SELECT category_key, count(*) AS views
                FROM access_logs GROUP BY 1) a USING (category_key)
        WHERE o.category_key IS NULL OR a.category_key IS NULL
        ORDER BY coalesce(a.views, 0) DESC
    """)
