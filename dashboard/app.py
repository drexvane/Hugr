"""The Streamlit renderer. Run it with:

    streamlit run dashboard/app.py

Everything this file does is place things on a page. What a view contains is
decided in `dtp.dashboard.views`, which imports no Streamlit and can be tested
without a browser; if a number here is wrong, it is wrong there. The Ask screen is
the same arrangement one layer over: `dtp.agent` returns an `Answer`, and an `Answer`
has a view's shape, so rendering it is placement too.

No authentication by default. This binds to localhost and reads local Parquet, which
is adequate for a demo on one machine. Setting `DTP_DASHBOARD_PASSWORD` turns on a
shared-secret gate so the demo can be hosted without being open — read
`dtp.dashboard.auth` before trusting it, because a password is not identity and this
data carries customer names, street addresses and 3,340 client IPs. No view surfaces
any of those columns and geography stops at country, but that is a choice about
display enforced by tests, not an access control.
`docs/02-dashboard-design.md` records the decision.

The Ask screen is the one thing here that talks to a third party, and only when a key
is present: it sends the question, the registry and the head of an aggregated frame.
`docs/03-agent-design.md` reason 20 is the argument; without a key it runs the
keyless stub instead and says so.
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
from dtp.dashboard import auth                                # noqa: E402
from dtp.dashboard import views as V                          # noqa: E402
from dtp.dashboard import style                               # noqa: E402

st.set_page_config(page_title="Hugr — AI Data Analyst", page_icon="\u2726",
                   layout="wide")
style.inject_custom_css()

# Order status is deliberately not offered. Every money metric already excludes
# cancelled and suspected-fraud lines, so filtering to CANCELED would return zeros
# from a control that looks like it should return rows - the gate owns that column.
FILTER_DIMS = ("market", "segment", "shipping_mode", "department")

# The seventh entry in the nav, and not a seventh view: the six in `V.CATALOGUE` are
# built from the sidebar's filters, and this one is built from a question. Keeping it
# out of the catalogue is what keeps `V.BUILDERS` and the nav the same six things.
ASK = "ask"
ASK_TITLE = "Ask a question"


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

    view = st.sidebar.radio("View", [k for k, _, _ in V.CATALOGUE] + [ASK],
                            format_func=lambda k: V.TITLES.get(k, ASK_TITLE))

    lo, hi = _bounds(version_id)
    if view == ASK:
        # No date range, no filters, no thin-group floor: the question carries its
        # own window and its own grouping. Controls that quietly do nothing are
        # worse than controls that are absent.
        st.sidebar.divider()
        st.sidebar.caption("The question sets its own window and filters, so the "
                           "controls the other views use are not shown here.")
        _privacy_note()
        return version_id, M.Filters(date_from=str(lo.date()),
                                     date_to=str(hi.date()), where={}), 0, view

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
    _privacy_note()
    return (version_id,
            M.Filters(date_from=str(date_from), date_to=str(date_to),
                      where=where),
            int(min_lines), view)


def _privacy_note() -> None:
    entry = ("password-gated" if auth.required() else "no login")
    st.sidebar.caption("Local process, local Parquet, " + entry + ". Customer "
                       "names, street addresses and client IPs are in the data and "
                       "are not shown on any view; that is a display choice, not an "
                       "access control.")


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


# --------------------------------------------------------------------------- #
# the ask screen
#
# `Answer` is a value with the same shape as a view - tiles, a figure, a frame, a
# sentence - so this is placement too. Nothing here decides what a number is.
# --------------------------------------------------------------------------- #

@st.cache_resource(show_spinner=False)
def _model():
    """One model per process. The stub when there is no key, and it says which.

    Falling back rather than failing: the dashboard is the demo, and a screen that
    cannot be opened without a credential is a screen nobody sees. The stub matches
    registry keys against the question's words and declines the rest, so what it
    cannot do it refuses rather than guesses.
    """
    from dtp.agent import client as agent_client

    if agent_client.api_key():
        try:
            return agent_client.AnthropicModel()
        except RuntimeError as exc:                  # the SDK is not installed
            st.warning(str(exc))
    return agent_client.KeywordModel()


def _session(wh, version_id: str):
    """The session, kept across reruns. A snapshot change clears its memory.

    The state key is not a widget key: Streamlit reserves those, and naming this one
    `ask` made the form below refuse to build.
    """
    from dtp.agent import Session

    if "ask_session" not in st.session_state:
        st.session_state["ask_session"] = Session(wh, _model(), version_id)
    session = st.session_state["ask_session"]
    session.use(wh, version_id)
    return session


def _render_answer(answer) -> None:
    if not answer.ok:
        st.warning(answer.refusal.message, icon="\N{NO ENTRY SIGN}")
        return
    st.caption(answer.caption)
    if answer.tiles:
        _render_tiles(answer.tiles)
    if answer.figure is not None:
        st.plotly_chart(answer.figure, width="stretch",
                        config={"displaylogo": False})
    if answer.frame is not None:
        with st.expander("The rows behind it", expanded=answer.figure is None):
            st.dataframe(answer.frame, width="stretch", hide_index=True)
    st.markdown(answer.summary)
    if answer.withheld:
        # Reason 11: the downgrade is shown, not swallowed. A user who cannot see
        # that the model's sentence was dropped cannot tell the two apart.
        st.caption("\N{WARNING SIGN} " + answer.withheld)
    for note in answer.notes:
        st.caption(note)
    with st.expander("The plan that ran"):
        st.json(answer.plan.to_dict())
        st.caption("Filled in by " + answer.model + ", validated against the "
                   "registry, then executed by `metrics.aggregate`. No SQL came "
                   "from the model.")


def _ask_screen(wh, version_id: str) -> None:
    style.render_brand_header()

    if "uploaded_warehouse" in st.session_state:
        dataset_display_name = st.session_state.get("uploaded_file_name", "Uploaded Dataset")
        row_count = st.session_state.get("uploaded_rows", 0)
        col_count = st.session_state.get("uploaded_cols", 0)
    elif getattr(wh, "manifest", None) and wh.manifest.tables:
        t_entry = wh.manifest.tables[0]
        dataset_display_name = f"DataCo Supply Chain ({t_entry.table})"
        row_count, col_count = t_entry.rows, t_entry.columns
    else:
        dataset_display_name = "Connected Dataset"
        row_count, col_count = 0, 0

    style.render_dataset_pill(dataset_display_name, row_count, col_count)

    session = _session(wh, version_id)

    if not session.log:
        style.render_hero_intro()

    st.title(ASK_TITLE, anchor=False)
    st.caption("Natural language in, the same charts and the same numbers out. "
               "Every figure is computed here; the model only chooses which.")

    uploaded_file = st.file_uploader(
        "Upload CSV or Excel dataset",
        type=["csv", "xlsx", "xls"],
        help="Upload any tabular CSV or Excel dataset to explore immediately. Hugr auto-discovers columns, metrics, and dimensions.",
        key="hugr_uploader",
    )
    if uploaded_file is not None:
        file_id = f"{uploaded_file.name}_{uploaded_file.size}"
        if st.session_state.get("current_uploaded_file_id") != file_id:
            try:
                import re
                import pandas as pd
                if uploaded_file.name.endswith((".xlsx", ".xls")):
                    df = pd.read_excel(uploaded_file)
                else:
                    df = pd.read_csv(uploaded_file)

                clean_name = Path(uploaded_file.name).stem.lower()
                clean_name = re.sub(r"[^a-z0-9_]+", "_", clean_name).strip("_") or "dataset"

                new_wh = warehouse.Warehouse.from_df(df, name=clean_name)
                st.session_state["uploaded_warehouse"] = new_wh
                st.session_state["uploaded_file_name"] = uploaded_file.name
                st.session_state["uploaded_rows"] = len(df)
                st.session_state["uploaded_cols"] = len(df.columns)
                st.session_state["current_uploaded_file_id"] = file_id
                from dtp.agent import Session
                st.session_state["ask_session"] = Session(new_wh, _model(), new_wh.version_id)
                st.rerun()
            except Exception as exc:
                st.error(f"Error ingesting {uploaded_file.name}: {exc}")
    elif "uploaded_warehouse" in st.session_state and uploaded_file is None:
        del st.session_state["uploaded_warehouse"]
        st.session_state.pop("uploaded_file_name", None)
        st.session_state.pop("uploaded_rows", None)
        st.session_state.pop("uploaded_cols", None)
        st.session_state.pop("current_uploaded_file_id", None)
        st.session_state.pop("ask_session", None)
        st.rerun()

    starter_prompts = style.get_starter_prompts(wh)
    placeholder_text = starter_prompts[0] if starter_prompts else "revenue and margin by category"

    with st.form("ask_form", clear_on_submit=False):
        question = st.text_input(
            "Question", placeholder=placeholder_text,
            label_visibility="collapsed")
        asked = st.form_submit_button("Ask", type="primary")

    if asked and question.strip():
        with st.spinner("Asking..."):
            session.ask(question)
    if session.log and st.button(
            "Start over", help="Forget the last plan, so the next question is not "
                               "read as a follow-up."):
        session.reset()
        session.log.clear()

    st.caption("Try: " + "  ·  ".join(starter_prompts))
    if session.plan is not None:
        st.caption("Follow-ups patch the last plan, so \"break that down by "
                   "region\" keeps everything else.")

    if session.log:
        _render_answer(session.log[-1])
        for earlier in reversed(session.log[:-1]):
            with st.expander(earlier.question):
                _render_answer(earlier)
    else:
        style.render_initial_cards()


# --------------------------------------------------------------------------- #
# the gate
#
# Placement only, again: `dtp.dashboard.auth` decides whether a string is correct and
# whether the configured secret is worth anything. Off entirely unless an operator
# sets DTP_DASHBOARD_PASSWORD, so a local run is exactly what it was.
# --------------------------------------------------------------------------- #

def _gate() -> None:
    """Stop the script unless the visitor knows the shared secret.

    Before the sidebar, not after: the snapshot list and the filter boxes are made of
    real market, segment and product values, so a page that renders the controls and
    hides only the charts has already answered a question.
    """
    if not auth.required():
        return
    problem = auth.weakness()
    if problem:
        # Refusing to serve is the point. A gate the operator believes in and that
        # accepts "1234" is worse than no gate at all.
        st.error("The dashboard password is misconfigured, so nothing is being "
                 "served.\n\n" + problem)
        st.stop()
    if st.session_state.get("authed"):
        return

    st.title("DataCo supply chain", anchor=False)
    st.caption("This deployment is password-protected. Ask whoever set it up.")
    with st.form("gate"):
        supplied = st.text_input("Password", type="password")
        sent = st.form_submit_button("Enter", type="primary")
    tries = int(st.session_state.get("tries", 0))
    if tries >= auth.MAX_ATTEMPTS:
        st.error("Too many attempts in this session. Reload to try again.")
        st.stop()
    if sent:
        if auth.verify(supplied):
            st.session_state["authed"] = True
            st.session_state["tries"] = 0
            st.rerun()
        st.session_state["tries"] = tries + 1
        # No detail and nothing logged: which half was wrong is information, and a
        # log of failed passwords is a log of passwords.
        st.error("Not that.")
    st.stop()


def main() -> None:
    _gate()
    version_id, filters, min_lines, key = _sidebar()

    if key == ASK and "uploaded_warehouse" in st.session_state:
        wh = st.session_state["uploaded_warehouse"]
        ask_version = wh.version_id
    else:
        wh = _open(version_id)
        ask_version = version_id

    if key == ASK:
        _ask_screen(wh, ask_version)
        return
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
