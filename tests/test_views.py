"""The view layer: a screen is a value, so a screen can be asserted about.

Nothing here starts Streamlit. That is the whole point of the split - "the delivery
view names its worst cell" is a claim about a string - and the first test is the one
that keeps it true.

The privacy promise from `docs/02-dashboard-design.md` is also enforced here rather
than by review: `test_no_view_carries_a_personal_column` walks every frame and every
table of every view and fails if a customer name, street or IP reaches one.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest

from dtp import metrics as M
from dtp import versioning
from dtp.dashboard import views as V

SRC = Path(__file__).resolve().parents[1] / "src" / "dtp" / "dashboard" / "views.py"
APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"

# The columns `docs/02` promises no view will surface. Named here as strings rather
# than read from the registry, so removing one from the registry cannot quietly
# empty this list.
PERSONAL = ("customer_first_name", "customer_last_name", "customer_street",
            "client_ip", "product_image_url", "customer_email",
            "customer_password")


@pytest.fixture
def built(wh):
    """Every view in the catalogue, built once against the fixture snapshot."""
    return {key: V.build(key, **({} if key == "health" else {"wh": wh}))
            for key, _, _ in V.CATALOGUE}


# --------------------------------------------------------------------------- #
# the layer boundary
# --------------------------------------------------------------------------- #

def test_the_view_layer_knows_nothing_about_streamlit():
    """The claim the module docstring makes, as a test.

    A view is testable and Phase 3's agent can reuse it only while it is a value.
    One `st.` call here and both of those stop being true - the agent would need
    its own composition, and this file would need a browser.
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0] if node.level == 0 else "dtp")
    assert imported <= {"__future__", "typing", "json", "dataclasses", "pathlib",
                        "pandas", "plotly", "dtp"}, imported


def test_the_renderer_does_no_arithmetic():
    """`app.py` places things. It must not compute one.

    A number computed in the renderer is a number no test can reach, and the two
    would drift the moment someone "just adjusted" a percentage for display. The
    view builders are fair game - calling `V.overview` is placement. Reaching past
    them to `aggregate` or to `wh.sql` is not.
    """
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func,
                                                           ast.Attribute)}
    assert not called & {"aggregate", "timeseries", "totals", "funnel_totals",
                        "sql", "scalar", "sum", "mean", "groupby", "merge",
                        "fmt", "fmt_metric"}, called


def test_the_nav_and_the_builders_are_the_same_six_views():
    """A view in one and not the other is a menu entry that raises or a dead view."""
    assert set(V.BUILDERS) == {key for key, _, _ in V.CATALOGUE}
    assert set(V.TITLES) == set(V.QUESTIONS) == set(V.BUILDERS)
    assert len(V.CATALOGUE) == 6


def test_an_unknown_view_names_the_ones_that_exist(wh):
    with pytest.raises(KeyError) as err:
        V.build("kpis", wh=wh)
    assert "overview" in str(err.value)


# --------------------------------------------------------------------------- #
# every view, built
# --------------------------------------------------------------------------- #

def test_every_view_in_the_catalogue_builds(built):
    for key, title, question in V.CATALOGUE:
        view = built[key]
        assert view.key == key
        assert view.title == title
        assert view.question == question


def test_every_view_asks_its_question_in_the_docs_words():
    """The question-to-view map in `docs/02` is the nav, not a parallel list."""
    text = (Path(__file__).resolve().parents[1] / "docs"
            / "02-dashboard-design.md").read_text(encoding="utf-8")
    for _, _, question in V.CATALOGUE:
        assert question in text, question


def test_every_tile_is_a_formatted_string_with_a_label(built):
    """A tile carries no raw float: the registry owns the unit, so it formats."""
    for key, view in built.items():
        for tile in view.tiles:
            assert isinstance(tile.value, str) and tile.value, (key, tile)
            assert tile.label and tile.label[0].isupper(), (key, tile)


def test_every_figure_is_a_plotly_figure_and_every_panel_says_something(built):
    for key, view in built.items():
        assert view.panels, key
        for panel in view.panels:
            assert panel.key, key
            if panel.figure is not None:
                assert isinstance(panel.figure, go.Figure), (key, panel.key)
            assert panel.caption or panel.table is not None, (key, panel.key)
            if panel.caption:
                assert panel.caption.endswith((".", "%")), (key, panel.key,
                                                            panel.caption)


def test_a_panel_keeps_both_the_frame_and_the_display_table(built):
    """The caption is checkable against the frame; the table is strings to show."""
    for view in built.values():
        for panel in view.panels:
            if panel.table is not None:
                assert panel.frame is not None, panel.key
                assert len(panel.table) == len(panel.frame)


def test_no_view_carries_a_personal_column(built):
    """`docs/02` promises no view plots a name, a street or an IP.

    The registry test proves such a column cannot be grouped by. This proves none
    reaches a panel by another route - a purpose-built query, a merge, a table of
    raw rows - which is the promise a reader of the dashboard is actually relying
    on. Both spellings are checked: the raw column name, and the prose heading
    `_display` would give it if one ever arrived.
    """
    forbidden = {c for c in PERSONAL} | {V._prose(c).lower() for c in PERSONAL}
    for key, view in built.items():
        for panel in view.panels:
            for frame in (panel.frame, panel.table):
                if frame is None:
                    continue
                seen = {str(c).lower() for c in frame.columns}
                assert not seen & forbidden, (key, panel.key, seen & forbidden)


def test_geography_stops_at_market_region_country(built):
    """The drill path is the whole of the geography a view can reach.

    The clean data holds a customer city and a client IP. Neither is on the path,
    so no amount of drilling arrives at one.
    """
    assert V.GEO_PATH == ("market", "region", "country")
    assert "customer_city" not in V.GEO_PATH
    assert V.next_level("country") is None


# --------------------------------------------------------------------------- #
# the overview
# --------------------------------------------------------------------------- #

def test_the_overview_states_the_ungated_total_as_a_delta(wh):
    """Once, on one view. A second ungated figure invites quoting the wrong one."""
    view = V.overview(wh)
    assert any("never converted" in note for note in view.notes)


def test_only_the_overview_mentions_the_ungated_total(built):
    """Every other view's numbers are gated and say nothing about the raw ones."""
    for key, view in built.items():
        if key == "overview":
            continue
        text = " ".join(view.notes) + " ".join(p.caption + p.note
                                              for p in view.panels)
        assert "ungated" not in text.lower(), key
        assert "never converted" not in text, key


def test_the_movers_panel_needs_a_bounded_window(wh):
    """"Everything" has no prior window, so the panel is absent rather than empty."""
    unbounded = {p.key for p in V.overview(wh).panels}
    assert "movers" not in unbounded
    bounded = V.overview(wh, filters=M.Filters(date_from="2016-04-01",
                                               date_to="2016-06-30"))
    panel = next(p for p in bounded.panels if p.key == "movers")
    assert "moved most" in panel.caption


def test_the_overview_series_rings_the_spike_month(wh):
    """The design doc's acceptance criterion, at the view rather than the chart."""
    view = V.overview(wh, filters=M.Filters(date_from="2017-01-01"))
    series = next(p for p in view.panels if p.key == "series")
    assert "Jul 2017" in series.caption
    assert len(series.frame) == 12


# --------------------------------------------------------------------------- #
# delivery, profitability, geography
# --------------------------------------------------------------------------- #

def test_the_delivery_grid_names_its_worst_cell(wh):
    """The sentence the split exists to make assertable."""
    grid = next(p for p in V.delivery(wh).panels if p.key == "grid")
    assert "worst for Standard Class in Pacific Asia at 0.0%" in grid.caption
    assert "blank cell" in grid.note


def test_the_delay_panel_counts_shipments_not_lines(wh):
    """A delay distribution over ungated lines shows deliveries that never were."""
    spread = next(p for p in V.delivery(wh).panels if p.key == "spread")
    assert "shipped_lines" in spread.frame.columns
    assert "lines" not in [c for c in spread.frame.columns if c != "n_lines"]
    assert spread.frame.loc[spread.frame["delay_days"] == 4,
                            "shipped_lines"].iloc[0] == 0


def test_min_lines_reaches_the_panels_that_rank(wh):
    """The threshold is the reader's judgement, so it has to actually apply."""
    wide = next(p for p in V.profitability(wh).panels if p.key == "discount")
    thin = next(p for p in V.profitability(wh, min_lines=4).panels
                if p.key == "discount")
    assert len(thin.frame) < len(wide.frame)
    assert thin.frame["n_lines"].min() >= 4


def test_the_benchmark_panel_marks_the_categories_that_trail(wh):
    bench = next(p for p in V.profitability(wh).panels if p.key == "benchmark")
    assert "beats_parent" in bench.frame.columns
    assert " pp" in bench.caption          # a margin gap is points, never percent


def test_a_geography_level_off_the_drill_path_is_refused(wh):
    with pytest.raises(KeyError) as err:
        V.geography(wh, level="customer_city")
    assert "market" in str(err.value)


def test_the_geography_view_warns_that_market_is_nearly_a_period(wh):
    """The finding that makes a market-to-market comparison an artefact."""
    view = V.geography(wh)
    assert any("window" in note or "period" in note for note in view.notes)


def test_the_places_panel_lies_down_once_labels_would_not_fit(wh):
    """Six is the cutoff. Three markets stay upright; a country list does not.

    A vertical bar chart with fifteen country names rotates the labels to 45
    degrees and truncates them, which is a chart the reader cannot use.
    """
    places = next(p for p in V.geography(wh).panels if p.key == "places")
    assert len(places.frame) == 3
    assert places.figure.data[0].orientation == "v"
    wide = pd.DataFrame({"country": list("abcdefgh"),
                         "revenue": [8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]})
    from dtp import charts as C
    assert C.bar(wide, "country", "revenue",
                 horizontal=len(wide) > 6).data[0].orientation == "h"


def test_the_trend_panel_does_not_repeat_the_ranking_beside_it(wh):
    """Two identical captions on one screen is one caption doing no work."""
    view = V.geography(wh)
    ranking = next(p for p in view.panels if p.key == "places").caption
    trend = next(p for p in view.panels if p.key == "trend").caption
    assert ranking != trend
    assert "peaking at" in trend or "is the largest" in trend


# --------------------------------------------------------------------------- #
# drilling
# --------------------------------------------------------------------------- #

def test_drilling_returns_a_new_filter_and_leaves_the_old_one_alone():
    """The app keeps the un-drilled filter to step back up to."""
    start = M.Filters(date_from="2017-01-01", date_to="2017-12-31",
                      where={"segment": ["Consumer"]})
    down = V.drill_into(start, "market", "Europe")
    assert down.where == {"segment": ["Consumer"], "market": ["Europe"]}
    assert start.where == {"segment": ["Consumer"]}
    assert down.date_from == start.date_from and down.date_to == start.date_to


def test_drilling_accepts_one_value_or_several():
    base = M.Filters()
    assert V.drill_into(base, "market", "Europe").where == {"market": ["Europe"]}
    assert V.drill_into(base, "market", ["Europe", "LATAM"]).where == {
        "market": ["Europe", "LATAM"]}


def test_drilling_into_nothing_narrows_nothing():
    """An empty multiselect is "all of them", not "none of them"."""
    assert V.drill_into(M.Filters(), "market", []).where == {}


def test_drilling_off_the_path_is_refused():
    with pytest.raises(KeyError):
        V.drill_into(M.Filters(), "category", "Sporting Goods")


def test_the_drill_path_bottoms_out():
    assert V.next_level("market") == "region"
    assert V.next_level("region") == "country"
    assert V.next_level("country") is None


def test_a_view_does_not_mutate_the_filters_it_was_given(wh):
    """The renderer holds one `Filters` and passes it to every view in turn."""
    filters = M.Filters(date_from="2016-01-01", date_to="2016-12-31",
                        where={"market": ["Europe"]})
    before = (filters.date_from, filters.date_to,
              {k: list(v) for k, v in filters.where.items()})
    for builder in (V.overview, V.delivery, V.profitability, V.geography):
        builder(wh, filters=filters)
    assert (filters.date_from, filters.date_to,
            {k: list(v) for k, v in filters.where.items()}) == before


# --------------------------------------------------------------------------- #
# the funnel takes no filters, deliberately
# --------------------------------------------------------------------------- #

def test_the_funnel_ignores_the_date_control(wh):
    """`build` must not pass filters through, and `funnel` must not accept them.

    The log covers five of the fact table's months. A rate over a range the reader
    picked divides a five-month numerator by their denominator and understates
    conversion by up to 7x - so the window is fixed and stated instead.
    """
    with pytest.raises(TypeError):
        V.funnel(wh, filters=M.Filters(date_from="2017-01-01"))
    view = V.funnel(wh)
    assert any("2016-03-01 to 2016-06-28" in note for note in view.notes)
    assert any("does not apply" in note for note in view.notes)


def test_the_funnel_headline_comes_from_one_query_over_the_window(wh):
    """Not from summing the frame, because per-group counts are distinct per group.

    Every fixture order sits in one product and one category, so here the two
    agree - the inflation is a property of orders that span groups, and in the real
    extract summing the column overstates orders by 29% at product grain. The tile
    is pinned to `funnel_totals` so it cannot drift to the sum when it would matter.
    """
    view = V.funnel(wh, by="product")
    orders = next(t for t in view.tiles if t.label == "Orders")
    assert orders.value == M.fmt_metric("orders", M.funnel_totals(wh)["orders"],
                                        compact=True) == "6"
    frame = next(p for p in view.panels if p.key == "parity").frame
    assert frame["orders"].sum() >= 6


def test_the_funnel_separates_demand_that_left_from_a_log_with_gaps(wh):
    view = V.funnel(wh, by="product")
    dead = next(p for p in view.panels if p.key == "unordered")
    assert "never sold hat" in dead.table.to_string()
    unseen = next(p for p in view.panels if p.key == "unviewed")
    assert (unseen.frame["views"] == 0).all()
    assert "log's coverage" in unseen.caption


def test_a_display_table_drops_the_fold_used_to_join(wh):
    """`product_key` is how the query worked, not something to read."""
    frame = next(p for p in V.funnel(wh, by="product").panels
                 if p.key == "unordered").frame
    assert "product_key" in frame.columns
    table = V._display(frame)
    assert not [c for c in table.columns if str(c).endswith("_key")]
    assert "Product" in table.columns


def test_a_display_table_names_columns_as_a_reader_reads_them(wh):
    table = V._display(M.aggregate(wh, ["revenue"], by=["market"]))
    assert list(table.columns) == ["Market", "Revenue", "Lines"]
    assert table["Revenue"].iloc[0].startswith("$")


# --------------------------------------------------------------------------- #
# data health: the view that has to render when nothing else can
# --------------------------------------------------------------------------- #

def test_data_health_needs_no_warehouse(snapshot_dir):
    """Manifests only, so a snapshot whose Parquet will not load still reports.

    A failed load must be a row on this view rather than a traceback on the
    overview - otherwise the one view that would explain the problem is the one
    that breaks with it.
    """
    view = V.data_health(versions_dir=snapshot_dir, alerts_path=Path("nope.json"))
    assert view.title == "Data health"
    assert [t.value for t in view.tiles][:1] == ["20200101T000000"]
    assert dict((t.label, t.value) for t in view.tiles)["Validation"] == "PASS"


def test_data_health_says_so_when_there_is_no_snapshot_at_all(tmp_path):
    view = V.data_health(versions_dir=tmp_path / "empty")
    assert view.tiles[0].value == "none found"
    assert not view.panels
    assert any("dtp pipeline" in note for note in view.notes)


def test_a_missing_or_broken_alerts_file_is_not_an_error(tmp_path, snapshot_dir):
    """This view reports on the data's health; failing on its own input defeats it."""
    broken = tmp_path / "alerts.json"
    broken.write_text("{not json", encoding="utf-8")
    for path in (tmp_path / "absent.json", broken):
        view = V.data_health(versions_dir=snapshot_dir, alerts_path=path)
        assert dict((t.label, t.value) for t in view.tiles)["Alerts"] == "0"
        assert "alerts" not in {p.key for p in view.panels}


def test_an_alerts_payload_that_is_not_a_dict_is_ignored(tmp_path, snapshot_dir):
    path = tmp_path / "alerts.json"
    path.write_text('["critical"]', encoding="utf-8")
    assert V._read_alerts(path) == {}
    path.write_text('{"alerts": "critical"}', encoding="utf-8")
    assert V._read_alerts(path)["alerts"] == []


def test_alerts_are_ordered_by_severity_not_by_when_they_were_raised(
        tmp_path, snapshot_dir):
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps({
        "checked_at": "2026-09-03T22:48:15",
        "alerts": [{"id": "a", "severity": "info", "message": "third"},
                   {"id": "b", "severity": "critical", "message": "first"},
                   {"id": "c", "severity": "warning", "message": "second"},
                   "not a dict"],
    }), encoding="utf-8")
    view = V.data_health(versions_dir=snapshot_dir, alerts_path=path)
    panel = next(p for p in view.panels if p.key == "alerts")
    assert panel.frame["message"].tolist() == ["first", "second", "third"]
    assert dict((t.label, t.value) for t in view.tiles)["Alerts"] == "3"
    assert "id" not in panel.table.columns


def test_the_alerts_panel_dates_a_run_that_is_not_part_of_the_snapshot(
        tmp_path, snapshot_dir):
    """`reports/alerts.json` is overwritten by each run and pinned to none of them.

    Beside a snapshot the reader chose, an undated alert list reads as belonging to
    it. It may describe a different load entirely.
    """
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps({"checked_at": "2026-09-03T22:48:15",
                                "alerts": [{"severity": "warning", "m": "x"}]}),
                    encoding="utf-8")
    panel = next(p for p in V.data_health(versions_dir=snapshot_dir,
                                          alerts_path=path).panels
                 if p.key == "alerts")
    assert "2026-09-03T22:48:15" in panel.caption
    assert "not part of this snapshot" in panel.caption


def test_severity_counts_survive_a_severity_nobody_defined():
    counts = V._severity_counts([{"severity": "catastrophic"}, {"severity": "info"},
                                 {}])
    assert counts["catastrophic"] == 1
    assert counts["info"] == 2          # the bare dict defaults to info


# --------------------------------------------------------------------------- #
# snapshot history, and the wrap that would have shown the wrong diff
# --------------------------------------------------------------------------- #

@pytest.fixture
def two_snapshots(tmp_path):
    """Two snapshots of the same table, the second one row shorter."""
    versions = tmp_path / "versions"
    frame = pd.DataFrame({"order_item_id": [1, 2, 3], "order_item_sales": [1.0,
                                                                          2.0,
                                                                          3.0]})
    versioning.write_snapshot({"order_items": frame}, source_dir=Path("fixture"),
                              versions_dir=versions,
                              validation={"status": "PASS", "verdict": "PASS",
                                          "rules": 1, "passed": 1},
                              version_id="20200101T000000")
    versioning.write_snapshot({"order_items": frame.head(2)},
                              source_dir=Path("fixture"), versions_dir=versions,
                              validation={"status": "FAIL", "verdict": "FAIL",
                                          "rules": 1, "passed": 0},
                              version_id="20200202T000000")
    return versions


def test_the_history_lists_the_newest_snapshot_first(two_snapshots):
    view = V.data_health(versions_dir=two_snapshots)
    rows = next(p for p in view.panels if p.key == "history").frame
    assert rows["version"].tolist() == ["20200202T000000", "20200101T000000"]
    # The oldest has nothing before it, so its change is blank rather than 0 - a 0
    # would read as "the load produced the same number of rows as the one before".
    assert rows["row_delta"].iloc[0] == -1
    assert pd.isna(rows["row_delta"].iloc[1])


def test_pinning_the_oldest_snapshot_offers_no_diff_rather_than_the_newest(
        two_snapshots):
    """`history[at - 1]` wraps. The guard is `at > 0`, not `len(history) > 1`.

    With the wrong guard, pinning the first snapshot diffs it against the last -
    a panel headed "against 20200202T000000" showing the change backwards.
    """
    oldest = V.data_health(version_id="20200101T000000",
                           versions_dir=two_snapshots)
    assert "diff" not in {p.key for p in oldest.panels}
    newest = V.data_health(version_id="20200202T000000",
                           versions_dir=two_snapshots)
    diff = next(p for p in newest.panels if p.key == "diff")
    assert "20200101T000000" in diff.title
    assert diff.frame["row_delta"].tolist() == [-1]


def test_a_failed_validation_makes_the_other_views_provisional(two_snapshots):
    """A dashboard that hides the verdict invites presenting a number from a
    failed load."""
    view = V.data_health(versions_dir=two_snapshots)
    assert dict((t.label, t.value) for t in view.tiles)["Validation"] == "FAIL"
    assert any("provisional" in note for note in view.notes)
    passing = V.data_health(version_id="20200101T000000",
                            versions_dir=two_snapshots)
    assert not any("provisional" in note for note in passing.notes)


def test_an_unknown_version_id_is_reported_not_silently_replaced(two_snapshots):
    """Falling back to the latest would put the wrong snapshot's figures on screen
    under the id the reader asked for."""
    view = V.data_health(version_id="20991231T000000",
                         versions_dir=two_snapshots)
    assert view.tiles[0].value == "none found"


def test_the_snapshot_picker_opens_on_the_newest(two_snapshots):
    """The defect this function exists for: the renderer reversed the list.

    The picker opened on the oldest snapshot under a label promising the newest.
    Every figure below it was right - four snapshots of one extract total the same -
    so only the id was wrong, which is the kind of thing a demo does not reveal.
    """
    assert V.snapshot_ids(two_snapshots) == ["20200202T000000",
                                             "20200101T000000"]
    newest = V.snapshot_ids(two_snapshots)[0]
    view = V.data_health(version_id=newest, versions_dir=two_snapshots)
    assert view.tiles[0].value == newest
    # The picker's default and the no-argument default must be the same snapshot.
    assert V.data_health(versions_dir=two_snapshots).tiles[0].value == newest


def test_the_picker_is_empty_rather_than_raising_when_nothing_is_written(tmp_path):
    assert V.snapshot_ids(tmp_path / "nothing") == []
