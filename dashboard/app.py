"""The Streamlit renderer. Run it with:

    streamlit run dashboard/app.py

Everything this file does is place things on a page. What a view contains is
decided in `dtp.dashboard.views`, which imports no Streamlit and can be tested
without a browser; if a number here is wrong, it is wrong there.

No authentication. This binds to localhost and reads local Parquet, which is
adequate for a demo on one machine and not adequate the moment it is hosted: the
clean data carries customer names, street addresses and 3,340 client IPs. No view
surfaces any of those columns and geography stops at city level, but that is a
choice about display, not an access control. `docs/02-dashboard-design.md` records
the decision and Phase 4 owns it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    # `dtp` is not installed in this environment - the test suite reaches it
    # through pytest's `pythonpath`, and `streamlit run` has no equivalent.
    sys.path.insert(0, str(REPO / "src"))

import streamlit as st                                        # noqa: E402

from dtp import metrics as M                                  # noqa: E402
from dtp import warehouse                                     # noqa: E402
from dtp.dashboard import views as V                          # noqa: E402

st.set_page_config(page_title="DataCo supply chain", page_icon="\N{PACKAGE}",
                   layout="wide")

# Order status is deliberately not offered. Every money metric already excludes
# cancelled and suspected-fraud lines, so filtering to CANCELED would return zeros
# from a control that looks like it should return rows - the gate owns that column.
FILTER_DIMS = ("market", "segment", "shipping_mode", "department")


@st.cache_resource(show_spinner=False)
def _open(version_id: str | None):
    """One warehouse per snapshot, kept open across reruns.

    `cache_resource` rather than `cache_data`: a DuckDB connection is a handle, not
    a value, and reopening it on every widget change would reread the Parquet.
    """
    return warehouse.open_warehouse(version_id)


@st.cache_data(show_spinner=False)
def _values(version_id: str, key: str) -> list[str]:
    """A filter box's options. Cached per snapshot, because they never change
    within one - and they are ordered by frequency with an alphabetical tiebreak,
    so the list does not reshuffle underneath a selection between reruns."""
    return M.dimension_values(_open(version_id), key)


@st.cache_data(show_spinner=False)
def _bounds(version_id: str):
    return M.date_bounds(_open(version_id))


def _sidebar() -> tuple[str, M.Filters, int, str]:
    """The controls, and the four things the views need from them."""
    st.sidebar.title("DataCo supply chain")
    ids = V.snapshot_ids()
    if not ids:
        st.sidebar.error("No snapshot found under data/versions.")
        st.stop()
    version_id = st.sidebar.selectbox(
        "Snapshot", ids, index=0,
        help="Newest first, and ordered in the view layer rather than here so the "
             "order is tested. A dashboard reading the live clean directory would "
             "change mid-presentation if a pipeline run started.")

    view = st.sidebar.radio("View", [k for k, _, _ in V.CATALOGUE],
                            format_func=lambda k: V.TITLES[k])

    lo, hi = _bounds(version_id)
    st.sidebar.divider()
    picked = st.sidebar.date_input(
        "Order date", value=(lo.date(), hi.date()),
        min_value=lo.date(), max_value=hi.date(),
        help="Inclusive of both days. The funnel view ignores this: its numerator "
             "only exists for five months of this range.")
    date_from, date_to = (picked if isinstance(picked, tuple) and len(picked) == 2
                          else (lo.date(), hi.date()))

    where = {}
    for key in FILTER_DIMS:
        chosen = st.sidebar.multiselect(M.dimension(key).label,
                                        _values(version_id, key))
        if chosen:
            where[key] = chosen

    min_lines = st.sidebar.number_input(
        "Ignore groups under this many lines", min_value=0, max_value=5_000,
        value=0, step=10,
        help="A rate over a handful of lines swings on rounding and tops any "
             "ranked chart. Nothing is dropped at 0, which is the default because "
             "the threshold is a judgement and should be visible rather than "
             "built in.")

    st.sidebar.divider()
    st.sidebar.caption("Local process, local Parquet, no login. Customer names, "
                       "street addresses and client IPs are in the data and are "
                       "not shown on any view; that is a display choice, not an "
                       "access control.")
    return (version_id,
            M.Filters(date_from=str(date_from), date_to=str(date_to),
                      where=where),
            int(min_lines), view)


# --------------------------------------------------------------------------- #
# rendering - placement only, no arithmetic
# --------------------------------------------------------------------------- #

def _render_tiles(tiles) -> None:
    for column, tile in zip(st.columns(len(tiles)), tiles):
        with column:
            st.metric(tile.label, tile.value,
                      help=(tile.about + ("\n\n" + tile.note if tile.note else ""))
                      or None)
            if tile.note and not tile.about:
                st.caption(tile.note)


def _render_panel(panel) -> None:
    if panel.title:
        st.subheader(panel.title, anchor=False)
    if panel.figure is not None:
        st.plotly_chart(panel.figure, width="stretch",
                        config={"displaylogo": False})
    if panel.table is not None:
        if len(panel.table):
            st.dataframe(panel.table, width="stretch", hide_index=True)
        else:
            st.caption("Nothing matches - which for this panel is the good case.")
    if panel.caption:
        st.markdown(panel.caption)
    if panel.note:
        st.caption(panel.note)


def _render(view) -> None:
    st.title(view.title, anchor=False)
    st.caption(view.question)
    for note in view.notes:
        st.info(note, icon="\N{INFORMATION SOURCE}")
    if view.tiles:
        _render_tiles(view.tiles)
    for panel in view.panels:
        st.divider()
        _render_panel(panel)


def main() -> None:
    version_id, filters, min_lines, key = _sidebar()
    wh = _open(version_id)

    if key == "health":
        _render(V.data_health(version_id=version_id))
        return
    if key == "funnel":
        by = st.sidebar.selectbox("Funnel grain",
                                  ["product", "category", "department"],
                                  format_func=lambda k: M.dimension(k).label)
        _render(V.funnel(wh, by=by))
        return
    if key == "geography":
        level = st.sidebar.radio("Geography level", V.GEO_PATH, horizontal=True,
                                 format_func=lambda k: M.dimension(k).label)
        narrow = st.sidebar.multiselect(
            "Narrow to " + M.dimension(level).label.lower(),
            _values(version_id, level),
            help="Drilling down narrows the date window with it here, because in "
                 "this extract a market is very nearly a period.")
        _render(V.geography(wh, filters=V.drill_into(filters, level, narrow),
                            level=level, min_lines=min_lines))
        return
    if key == "overview":
        _render(V.overview(wh, filters=filters))
        return
    _render(V.build(key, wh=wh, filters=filters, min_lines=min_lines))


main()
