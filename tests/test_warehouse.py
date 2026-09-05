"""The query layer: one snapshot per session, folded join keys, read-only.

Everything here runs against the fixture snapshot in `conftest`, never the real
extract, so the suite passes on a fresh clone where `data/` does not exist.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from dtp import warehouse as W


def test_opens_the_newest_snapshot_and_names_it(wh):
    assert wh.version_id == "20200101T000000"
    assert wh.tables == ["access_logs", "order_items"]
    assert wh.manifest.validation["status"] == "PASS"


def test_refuses_to_fall_back_to_unversioned_output(tmp_path: Path):
    """No snapshot must raise, not silently read `data/clean/`.

    A dashboard that falls back cannot answer "which data produced this number?",
    which is the whole point of reading a version.
    """
    with pytest.raises(FileNotFoundError) as err:
        W.open_warehouse(versions_dir=tmp_path)
    assert "dtp pipeline" in str(err.value)


def test_unknown_snapshot_id_names_the_directory(snapshot_dir: Path):
    with pytest.raises(FileNotFoundError) as err:
        W.open_warehouse(version_id="19990101T000000", versions_dir=snapshot_dir)
    assert "19990101T000000" in str(err.value)


def test_snapshot_is_read_only(wh):
    """A view over Parquet cannot be written through, and that is the guarantee."""
    with pytest.raises(duckdb.Error):
        wh.con.execute("DELETE FROM order_items")
    with pytest.raises(duckdb.Error):
        wh.con.execute("UPDATE order_items SET order_item_sales = 0")


def test_parameters_are_bound_not_formatted(wh):
    """A filter value cannot close a quote and continue the statement."""
    hostile = "Europe' OR 1=1 --"
    rows = wh.sql("SELECT count(*) AS n FROM order_items WHERE market = $m",
                  m=hostile)
    assert int(rows.iat[0, 0]) == 0


def test_one_warehouse_serves_concurrent_readers(wh):
    """Two people clicking at the same time is the normal case, not the edge one.

    One `Warehouse` is cached per process and Streamlit serves its sessions on
    threads, so this is what the dashboard actually does. A `DuckDBPyConnection` is
    not thread-safe: before `sql` handed each thread its own cursor, eight readers
    raised "Attempting to execute an unsuccessful or closed pending query result"
    within a second, and the load test in `scripts/qa_report.py` is what found it.
    """
    from concurrent.futures import ThreadPoolExecutor

    def query(i: int) -> int:
        frame = wh.sql("SELECT market, count(*) AS n FROM order_items "
                       "WHERE order_item_sales > $floor GROUP BY 1 ORDER BY 2 DESC",
                       floor=float(i % 3))
        return int(frame["n"].sum())

    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(query, range(64)))
    assert len(counts) == 64
    assert all(c > 0 for c in counts)
    # Same floor, same answer: a cursor per thread must not mean a different view of
    # the data per thread.
    assert len({c for i, c in enumerate(counts) if i % 3 == 0}) == 1


def test_every_thread_sees_the_same_catalogue(wh):
    # Cursors share the database, so the folded join-key views exist on all of them.
    # If they did not, a threaded reader would get "table not found" for a view the
    # main thread can see.
    from concurrent.futures import ThreadPoolExecutor

    def keys(_: int) -> list[str]:
        return sorted(wh.sql("SELECT category_key FROM access_logs "
                             "GROUP BY 1 ORDER BY 1")["category_key"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        seen = list(pool.map(keys, range(8)))
    assert seen[0]
    assert all(s == seen[0] for s in seen)


def test_ident_rejects_a_crafted_table_name():
    assert W._ident("order_items") == '"order_items"'
    for bad in ('a"; DROP TABLE x; --', "order items", "orders;", ""):
        with pytest.raises(ValueError):
            W._ident(bad)


def test_schema_lists_columns_for_the_agent_prompt(wh):
    schema = wh.schema("order_items")
    names = set(schema["column_name"])
    assert {"order_item_sales", "is_revenue_recognised", "shipping_delay_days"} <= names
    # The folded keys are part of the queryable surface, so they must show up.
    assert {"product_key", "category_key", "department_key"} <= names


def test_scalar_returns_none_for_an_empty_result(wh):
    assert wh.scalar("SELECT 1 WHERE false") is None


# --------------------------------------------------------------------------- #
# the join keys - the reason this layer exists
# --------------------------------------------------------------------------- #

def test_raw_label_join_returns_nothing(wh):
    """The failure the `*_key` columns prevent, asserted so it cannot be forgotten.

    A literal join on the labels is what an LLM writes in Phase 3. It returns zero
    rows and reports "no data" rather than raising, which is why the fix has to
    live in the view rather than in a warning.
    """
    joined = wh.scalar("""
        SELECT count(*) FROM order_items o
          JOIN access_logs a ON a.product_name = o.product_name
    """)
    assert int(joined) == 0


def test_folded_key_join_returns_rows(wh):
    joined = wh.scalar("""
        SELECT count(*) FROM order_items o
          JOIN access_logs a ON a.product_key = o.product_key
    """)
    assert int(joined) > 0


def test_fold_reaches_past_case_to_punctuation(wh):
    """'Indoor/Outdoor Games' and 'indoor outdoor games' are the same category.

    Case-folding alone leaves them as two orphans - in the real extract, 16,073
    page views on one side and 19,298 order lines on the other.
    """
    fact = wh.scalar("SELECT DISTINCT category_key FROM order_items "
                     "WHERE category_name = 'Indoor/Outdoor Games'")
    log = wh.scalar("SELECT DISTINCT category_key FROM access_logs "
                    "WHERE category_name = 'indoor outdoor games'")
    assert fact == log == "indoor outdoor games"


def test_fold_strips_the_logs_trailing_space(wh):
    assert wh.scalar("SELECT DISTINCT department_key FROM access_logs "
                     "WHERE department_name = 'fan shop '") == "fan shop"


def test_fold_does_not_merge_genuinely_different_labels(wh):
    """'Electronics (Footwear)' is not the log's 'electronics'.

    The fold normalises spelling, not meaning. Two products the vendor kept apart
    must stay apart, or the funnel silently attributes one's views to the other.
    """
    keys = set(wh.sql("SELECT DISTINCT category_key AS k FROM order_items "
                      "WHERE category_name LIKE 'Electronics%'")["k"])
    assert keys == {"electronics footwear", "electronics outdoors"}
    assert "electronics" not in keys


def test_no_two_labels_collapse_onto_one_key(wh):
    """The fold must not invent a join the labels do not support.

    If two distinct labels in the same table folded to one key, every figure
    grouped by that key would silently add two different things together.
    """
    for table in ("order_items", "access_logs"):
        for column in ("department", "category", "product"):
            collisions = wh.sql(
                "SELECT " + column + "_key AS k, count(DISTINCT " + column
                + "_name) AS labels FROM " + table
                + " GROUP BY 1 HAVING count(DISTINCT " + column + "_name) > 1")
            assert collisions.empty, (table, column, collisions.to_dict("records"))


def test_originals_are_untouched_beside_the_keys(wh):
    """The clean tables stay a faithful typed copy: the log really does emit
    lowercase, and folding in place would overwrite what the system sent."""
    assert wh.scalar("SELECT count(*) FROM access_logs "
                     "WHERE department_name = 'fan shop '") > 0
    assert wh.scalar("SELECT count(*) FROM order_items "
                     "WHERE category_name = 'Indoor/Outdoor Games'") > 0


# --------------------------------------------------------------------------- #
# the funnel window
# --------------------------------------------------------------------------- #

def test_log_window_is_read_from_the_data(wh):
    """Derived, not declared: a new extract must not inherit the old window."""
    assert W.log_window(wh) == ("2016-03-01", "2016-06-28")


def test_log_window_is_cached(wh):
    first = W.log_window(wh)
    assert W.log_window(wh) is first


def test_unmatched_categories_names_both_sides(wh):
    residue = W.unmatched_categories(wh)
    sides = dict(zip(residue["category_key"], residue["side"]))
    assert sides["featured shops"] == "viewed, never ordered"
    assert sides["electronics"] == "viewed, never ordered"
    # The punctuation pair is rescued by the fold, so it is not residue.
    assert "indoor outdoor games" not in sides


def test_available_versions_puts_the_newest_first(tmp_path: Path):
    """Its own directory, not the session fixture's.

    Writing a second snapshot into `snapshot_dir` would make it the newest, and
    every later test that opens the default snapshot would silently get this
    two-column stub instead of the fixture.
    """
    import pandas as pd

    from dtp import versioning

    stub = {"order_items": pd.DataFrame({"a": [1]})}
    for version in ("20200101T000000", "20991231T235959"):
        versioning.write_snapshot(stub, source_dir=Path("fixture"),
                                  versions_dir=tmp_path, version_id=version)
    ids = [m.version_id for m in W.available_versions(tmp_path)]
    assert ids == ["20991231T235959", "20200101T000000"]
