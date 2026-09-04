"""The dashboard: a pure view layer plus a Streamlit renderer over it.

`views` computes what a view contains and returns it as values. `dashboard/app.py`
at the repo root imports `views` and does nothing but render. The split is the same
one `charts.py` already makes for a figure, extended to a whole screen: a view that
is a value can be tested without a browser, and Phase 3's agent can ask for the
overview's panels without starting Streamlit.
"""

from dtp.dashboard import views

__all__ = ["views"]
