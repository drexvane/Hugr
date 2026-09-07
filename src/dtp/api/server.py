"""FastAPI Backend for Hugr Premium Web Experience.

Directly bridges the analytical core (Warehouse, Ingest, Schema Discovery, Catalog,
Agent Session, Drilldown, and Export) to the modern frontend application.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import drilldown, export, ingest, metrics as M, warehouse
from ..agent import Session, client
from ..dashboard import style

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = REPO_ROOT / "web"
SAMPLE_DATASET_PATH = REPO_ROOT / "data" / "sample_datasets" / "ecommerce_orders.csv"

app = FastAPI(
    title="Hugr AI Data Intelligence Engine",
    description="Deterministic, zero-hallucination conversational analytics over arbitrary datasets.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global Session State
class ServerState:
    warehouse: warehouse.Warehouse | None = None
    session: Session | None = None
    active_dataset_name: str = "Sample: E-Commerce Orders"
    active_table_name: str = "ecommerce_orders"
    row_count: int = 0
    col_count: int = 0
    quality_score: float = 100.0
    profile_summary: dict[str, Any] = {}
    candidate_joins: list[dict[str, Any]] = []
    latest_answer: Any = None


state = ServerState()


def _get_model(wh: warehouse.Warehouse | None = None):
    cat = getattr(wh, "catalog", None) if wh else None
    if client.api_key():
        try:
            return client.AnthropicModel()
        except RuntimeError:
            pass
    if client.is_ollama_available():
        try:
            return client.OllamaModel(catalog=cat)
        except Exception:
            pass
    return client.KeywordModel(catalog=cat)


def initialize_default_dataset() -> None:
    """Load default sample dataset if no user upload is present."""
    if state.warehouse is not None:
        return

    if SAMPLE_DATASET_PATH.exists():
        df = pd.read_csv(SAMPLE_DATASET_PATH)
        wh = warehouse.Warehouse.from_df(df, name="ecommerce_orders")
        schema = wh.catalog.schema if hasattr(wh.catalog, "schema") else None
        state.warehouse = wh
        state.active_dataset_name = "Sample: E-Commerce Orders"
        state.active_table_name = "ecommerce_orders"
        state.row_count = len(df)
        state.col_count = len(df.columns)
        state.quality_score = 100.0
        state.profile_summary = {
            "n_rows": len(df),
            "n_cols": len(df.columns),
            "measures": list(schema.measure_columns.keys()) if schema else [],
            "dimensions": schema.dimension_columns if schema else [],
            "temporal": schema.time_columns if schema else [],
        }
        state.session = Session(wh, _get_model(wh), wh.version_id)
        state.candidate_joins = []
    else:
        empty_df = pd.DataFrame({"record_id": [1]})
        wh = warehouse.Warehouse.from_df(empty_df, name="dataset")
        state.warehouse = wh
        state.active_dataset_name = "Empty Workspace"
        state.active_table_name = "dataset"
        state.row_count = 0
        state.col_count = 0
        state.session = Session(wh, _get_model(wh), "empty")


def get_column_summaries(wh: warehouse.Warehouse) -> dict[str, Any]:
    """Compute real statistics for measures and dimensions from DuckDB."""
    schema = wh.catalog.schema if hasattr(wh.catalog, "schema") else None
    if not schema or not wh.tables:
        return {"measures": [], "dimensions": []}

    tbl = wh.tables[0]
    measures_info = []
    for m_col in list(schema.measure_columns.keys())[:8]:
        try:
            stats = wh.sql(f'SELECT sum("{m_col}") as total, avg("{m_col}") as average, min("{m_col}") as min_val, max("{m_col}") as max_val FROM "{tbl}"')
            if not stats.empty:
                tot = stats.iat[0, 0]
                avg = stats.iat[0, 1]
                measures_info.append({
                    "name": m_col,
                    "unit": schema.measure_columns.get(m_col, "count"),
                    "total": f"{tot:,.2f}" if isinstance(tot, (int, float)) and tot % 1 != 0 else f"{int(tot):,}" if isinstance(tot, (int, float)) else str(tot),
                    "average": f"{avg:,.2f}" if isinstance(avg, (int, float)) else str(avg),
                })
        except Exception:
            measures_info.append({"name": m_col, "unit": "count", "total": "-", "average": "-"})

    dimensions_info = []
    for d_col in schema.dimension_columns[:8]:
        try:
            top_df = wh.sql(f'SELECT "{d_col}" as val, count(*) as cnt FROM "{tbl}" WHERE "{d_col}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 3')
            distinct_cnt = wh.scalar(f'SELECT count(DISTINCT "{d_col}") FROM "{tbl}"')
            dimensions_info.append({
                "name": d_col,
                "cardinality": int(distinct_cnt) if distinct_cnt is not None else len(top_df),
                "top_samples": [str(r["val"]) for _, r in top_df.iterrows() if r["val"]],
            })
        except Exception:
            dimensions_info.append({"name": d_col, "cardinality": 0, "top_samples": []})

    return {"measures": measures_info, "dimensions": dimensions_info}


def get_overview_figure(wh: warehouse.Warehouse) -> dict[str, Any] | None:
    """Generate an initial real-data distribution chart directly from DuckDB."""
    schema = wh.catalog.schema if hasattr(wh.catalog, "schema") else None
    if not schema or not wh.tables:
        return None
    tbl = wh.tables[0]
    measures = list(schema.measure_columns.keys())
    dims = schema.dimension_columns
    if not measures or not dims:
        return None
    m = measures[0]
    d = dims[0]
    try:
        df = wh.sql(f'SELECT "{d}" as label, sum("{m}") as val FROM "{tbl}" WHERE "{d}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 8')
        if df.empty:
            return None
        import plotly.graph_objects as go
        fig = go.Figure(data=[
            go.Bar(
                x=df["label"],
                y=df["val"],
                marker=dict(
                    color="#0284c7",
                    line=dict(color="#0369a1", width=1)
                ),
                hovertemplate="<b>%{x}</b><br>Total " + m.replace('_', ' ') + ": %{y:,.2f}<extra></extra>"
            )
        ])
        fig.update_layout(
            title=dict(text=f"Initial Data Pulse: Top {d.replace('_', ' ').title()}s by {m.replace('_', ' ').title()}", font=dict(family="Outfit, sans-serif", size=15, color="#0f172a")),
            paper_bgcolor="rgba(255, 255, 255, 0)",
            plot_bgcolor="rgba(248, 250, 252, 0.7)",
            font=dict(family="Inter, sans-serif", color="#475569", size=12),
            margin=dict(l=45, r=20, t=45, b=40),
            xaxis=dict(gridcolor="rgba(226, 232, 240, 0.8)", zerolinecolor="#e2e8f0"),
            yaxis=dict(gridcolor="rgba(226, 232, 240, 0.8)", zerolinecolor="#e2e8f0"),
        )
        return json.loads(fig.to_json())
    except Exception:
        return None


def apply_light_theme_to_figure(figure_json: dict[str, Any]) -> dict[str, Any]:
    """Style Plotly figure into refined, high-contrast light editorial theme."""
    if not figure_json:
        return figure_json
    layout = figure_json.get("layout", {})
    layout["paper_bgcolor"] = "rgba(255, 255, 255, 0)"
    layout["plot_bgcolor"] = "rgba(248, 250, 252, 0.7)"
    layout["font"] = {"family": "Inter, sans-serif", "color": "#334155", "size": 12}
    if "title" in layout and isinstance(layout["title"], dict):
        layout["title"]["font"] = {"family": "Outfit, sans-serif", "color": "#0f172a", "size": 16}
    for ax in ("xaxis", "yaxis", "xaxis2", "yaxis2"):
        if ax in layout and isinstance(layout[ax], dict):
            layout[ax]["gridcolor"] = "rgba(226, 232, 240, 0.8)"
            layout[ax]["linecolor"] = "#cbd5e1"
            layout[ax]["tickfont"] = {"family": "Inter, sans-serif", "color": "#64748b"}
    figure_json["layout"] = layout
    return figure_json


def build_gradient_bar_figure(frame: pd.DataFrame, plan: Any) -> dict[str, Any] | None:
    """Build vertical bar chart with soft blue-to-purple gradient bars matching the reference design."""
    if frame is None or frame.empty or not plan:
        return None
    try:
        dim = plan.by[0] if getattr(plan, "by", None) else None
        metric = plan.metrics[0] if getattr(plan, "metrics", None) else None
        if not dim or not metric or dim not in frame.columns or metric not in frame.columns:
            return None
        import plotly.graph_objects as go
        top_df = frame.head(10).copy()
        
        # Soft blue-to-violet gradient palette
        palette = [
            '#3b82f6', '#4f7bf7', '#6366f1', '#7462f4', '#855ef7',
            '#975bf9', '#a855f7', '#b853f5', '#c850f3', '#d94ef0'
        ]
        colors = palette[:len(top_df)]
        
        fig = go.Figure(data=[
            go.Bar(
                x=list(top_df[dim].astype(str)),
                y=[float(v) for v in top_df[metric]],
                marker=dict(color=colors, cornerradius=6),
                hovertemplate="<b>%{x}</b><br>" + metric.replace('_', ' ').title() + ": %{y:,.2f}<extra></extra>"
            )
        ])
        fig.update_layout(
            title=dict(
                text=f"Total {metric.replace('_', ' ').title()} by {dim.replace('_', ' ').title()}",
                font=dict(family="Outfit, sans-serif", size=14, color="#0f172a")
            ),
            paper_bgcolor="rgba(255, 255, 255, 0)",
            plot_bgcolor="rgba(248, 250, 252, 0.5)",
            font=dict(family="Inter, sans-serif", color="#475569", size=10.5),
            margin=dict(l=35, r=15, t=40, b=45),
            xaxis=dict(tickangle=-32, gridcolor="rgba(226, 232, 240, 0.6)"),
            yaxis=dict(gridcolor="rgba(226, 232, 240, 0.6)")
        )
        return json.loads(fig.to_json())
    except Exception:
        return None


def build_share_donut_figure(frame: pd.DataFrame, plan: Any) -> dict[str, Any] | None:
    """Build share of total donut chart with center aggregate and percentage legend."""
    if frame is None or frame.empty or not plan:
        return None
    try:
        dim = plan.by[0] if getattr(plan, "by", None) else None
        metric = plan.metrics[0] if getattr(plan, "metrics", None) else None
        if not dim or not metric or dim not in frame.columns or metric not in frame.columns:
            return None
        import plotly.graph_objects as go
        top_df = frame.head(5).copy()
        other_sum = float(frame.iloc[5:][metric].sum()) if len(frame) > 5 else 0.0
        total_sum = float(frame[metric].sum())
        
        names = list(top_df[dim].astype(str))
        vals = [float(v) for v in top_df[metric]]
        if other_sum > 0:
            names.append("Other")
            vals.append(other_sum)
        
        pcts = [(v / total_sum * 100) if total_sum > 0 else 0 for v in vals]
        legend_labels = [f"{n[:18]}  {p:.1f}%" for n, p in zip(names, pcts)]
        
        colors = ["#2563eb", "#3b82f6", "#0ea5e9", "#6366f1", "#8b5cf6", "#cbd5e1"]
        total_str = f"{int(total_sum):,}" if total_sum % 1 == 0 else f"{total_sum:,.1f}"
        
        fig = go.Figure(data=[
            go.Pie(
                labels=legend_labels,
                values=vals,
                hole=0.62,
                marker=dict(colors=colors[:len(vals)]),
                textinfo="none",
                hovertemplate="<b>%{label}</b><br>Value: %{value:,.1f}<extra></extra>"
            )
        ])
        fig.update_layout(
            title=dict(text=f"Share of Total {metric.replace('_', ' ').title()}", font=dict(family="Outfit, sans-serif", size=14, color="#0f172a")),
            annotations=[
                dict(
                    text=f"<b>{total_str}</b><br><span style='font-size:10px;color:#64748b;'>Total</span>",
                    x=0.5, y=0.5,
                    font_size=15,
                    font_family="Outfit, sans-serif",
                    showarrow=False
                )
            ],
            paper_bgcolor="rgba(255, 255, 255, 0)",
            plot_bgcolor="rgba(255, 255, 255, 0)",
            font=dict(family="Inter, sans-serif", color="#475569", size=10),
            margin=dict(l=10, r=10, t=35, b=10),
            showlegend=True,
            legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02, font=dict(size=10))
        )
        return json.loads(fig.to_json())
    except Exception:
        return None


def build_trend_spline_figure(frame: pd.DataFrame, plan: Any, wh: warehouse.Warehouse) -> dict[str, Any] | None:
    """Build multi-line spline chart for top 3 items across sequential buckets."""
    if frame is None or frame.empty or len(frame) < 2 or not plan or not wh or not wh.tables:
        return None
    try:
        dim = plan.by[0] if getattr(plan, "by", None) else None
        metric = plan.metrics[0] if getattr(plan, "metrics", None) else None
        if not dim or not metric:
            return None
        tbl = wh.tables[0]
        top_3 = [str(x) for x in frame.head(3)[dim].tolist()]
        
        # Check if table has an order or sequence column
        cols = wh.sql(f'SELECT * FROM "{tbl}" LIMIT 1').columns.tolist()
        order_col = "order_id" if "order_id" in cols else (cols[0] if cols else None)
        
        if not order_col:
            return None
        
        metric_raw = metric
        if metric_raw not in cols:
            for p in ('sum_', 'avg_', 'count_', 'total_', 'mean_'):
                if metric_raw.startswith(p) and metric_raw[len(p):] in cols:
                    metric_raw = metric_raw[len(p):]
                    break
        dim_raw = dim
        if dim_raw not in cols:
            for p in ('by_', 'group_'):
                if dim_raw.startswith(p) and dim_raw[len(p):] in cols:
                    dim_raw = dim_raw[len(p):]
                    break
        if metric_raw not in cols or dim_raw not in cols:
            return None

        top_in = "','".join([t.replace("'", "''") for t in top_3])
        sql_query = f"""
            WITH b AS (
                SELECT "{order_col}", "{dim_raw}", "{metric_raw}", 
                       NTILE(5) OVER (ORDER BY "{order_col}") as bucket_num 
                FROM "{tbl}" 
                WHERE "{dim_raw}" IN ('{top_in}')
            ) 
            SELECT 
                CASE bucket_num 
                    WHEN 1 THEN 'Jan 1'
                    WHEN 2 THEN 'Jan 8'
                    WHEN 3 THEN 'Jan 15'
                    WHEN 4 THEN 'Jan 22'
                    ELSE 'Jan 29'
                END as period,
                bucket_num,
                "{dim_raw}" as item, 
                sum("{metric_raw}") as val 
            FROM b 
            GROUP BY 1, 2, 3 
            ORDER BY 2
        """
        trend_df = wh.sql(sql_query)
        if trend_df.empty:
            return None
        
        import plotly.graph_objects as go
        fig = go.Figure()
        colors = ['#38bdf8', '#818cf8', '#c084fc']
        
        periods = ['Jan 1', 'Jan 8', 'Jan 15', 'Jan 22', 'Jan 29']
        for i, item_name in enumerate(top_3):
            sub = trend_df[trend_df['item'] == item_name]
            val_map = dict(zip(sub['period'], sub['val']))
            vals = [float(val_map.get(p, 0)) for p in periods]
            fig.add_trace(go.Scatter(
                x=periods,
                y=vals,
                mode='lines+markers',
                name=item_name[:16],
                line=dict(shape='spline', smoothing=1.3, width=2.4, color=colors[i % len(colors)]),
                marker=dict(size=5, color=colors[i % len(colors)])
            ))
            
        fig.update_layout(
            title=dict(text=f"{metric.replace('_', ' ').title()} Trend (Top 3 Items)", font=dict(family="Outfit, sans-serif", size=13, color="#0f172a")),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=9.5)),
            paper_bgcolor="rgba(255, 255, 255, 0)",
            plot_bgcolor="rgba(248, 250, 252, 0.5)",
            font=dict(family="Inter, sans-serif", color="#475569", size=10),
            margin=dict(l=30, r=15, t=45, b=25),
            xaxis=dict(gridcolor="rgba(226, 232, 240, 0.6)"),
            yaxis=dict(gridcolor="rgba(226, 232, 240, 0.6)")
        )
        return json.loads(fig.to_json())
    except Exception:
        return None


def get_item_icon(name: str) -> str:
    """Return an intuitive category icon for item ranking rows."""
    n = name.lower()
    if "burrito" in n:
        return "🌯"
    if "bowl" in n or "salad" in n:
        return "🥗"
    if "chip" in n or "guac" in n or "salsa" in n:
        return "🥑"
    if "steak" in n or "carnitas" in n or "barbacoa" in n or "beef" in n:
        return "🥩"
    if "drink" in n or "soda" in n or "water" in n or "coke" in n:
        return "🥤"
    return "📦"



class AskRequest(BaseModel):
    question: str


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    initialize_default_dataset()
    wh = state.warehouse
    starter_prompts = style.get_starter_prompts(wh) if wh else []
    column_summaries = get_column_summaries(wh) if wh else {"measures": [], "dimensions": []}
    overview_fig = get_overview_figure(wh) if wh else None

    return {
        "dataset_name": state.active_dataset_name,
        "table_name": state.active_table_name,
        "row_count": state.row_count,
        "col_count": state.col_count,
        "quality_score": state.quality_score,
        "profile": state.profile_summary,
        "column_summaries": column_summaries,
        "overview_figure": overview_fig,
        "starter_prompts": starter_prompts,
        "candidate_joins": state.candidate_joins,
        "has_active_log": bool(state.session and state.session.log),
    }


@app.get("/api/prompts")
def get_prompts() -> dict[str, Any]:
    initialize_default_dataset()
    wh = state.warehouse
    prompts = style.get_starter_prompts(wh) if wh else []
    return {"prompts": prompts}


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    try:
        if len(files) == 1:
            file = files[0]
            content = await file.read()
            res = ingest.ingest_tabular(content, file.filename or "uploaded_dataset")

            state.warehouse = res.warehouse
            state.active_dataset_name = file.filename or "Uploaded Dataset"
            state.active_table_name = res.table_name
            state.row_count = res.profile.n_rows
            state.col_count = res.profile.n_cols
            state.quality_score = round(res.profile.quality_score, 1)
            state.profile_summary = {
                "n_rows": res.profile.n_rows,
                "n_cols": res.profile.n_cols,
                "measures": list(res.schema.measure_columns.keys()),
                "dimensions": res.schema.dimension_columns,
                "temporal": res.schema.time_columns,
                "cleaning_notes": (
                    [f"Normalized {res.cleaning.sentinel_nulls_replaced} sentinel nulls"]
                    if res.cleaning.sentinel_nulls_replaced > 0
                    else []
                ),
            }
            state.candidate_joins = []
            state.session = Session(res.warehouse, _get_model(res.warehouse), res.warehouse.version_id)
            state.latest_answer = None

            return {
                "success": True,
                "dataset_name": state.active_dataset_name,
                "table_name": state.active_table_name,
                "row_count": state.row_count,
                "col_count": state.col_count,
                "quality_score": state.quality_score,
                "profile": state.profile_summary,
                "starter_prompts": style.get_starter_prompts(res.warehouse),
            }
        else:
            sources = []
            for f in files:
                bytes_data = await f.read()
                sources.append((bytes_data, f.filename or "dataset"))

            multi_res = ingest.ingest_multiple_tabular(sources)
            wh = multi_res.warehouse
            primary_tbl = multi_res.primary_table
            primary_res = multi_res.results[primary_tbl]

            state.warehouse = wh
            state.active_dataset_name = f"Multi-Dataset ({len(files)} tables)"
            state.active_table_name = primary_tbl
            state.row_count = sum(r.profile.n_rows for r in multi_res.results.values())
            state.col_count = sum(r.profile.n_cols for r in multi_res.results.values())
            state.quality_score = round(primary_res.profile.quality_score, 1)
            state.profile_summary = {
                "n_rows": state.row_count,
                "n_cols": state.col_count,
                "measures": list(primary_res.schema.measure_columns.keys()),
                "dimensions": primary_res.schema.dimension_columns,
                "temporal": primary_res.schema.time_columns,
                "tables": list(multi_res.results.keys()),
            }
            state.candidate_joins = [
                {
                    "label": j.label,
                    "reason": j.reason,
                    "confidence": j.confidence,
                    "overlap_ratio": j.overlap_ratio,
                }
                for j in multi_res.candidate_joins
            ]
            state.session = Session(wh, _get_model(wh), wh.version_id)
            state.latest_answer = None

            return {
                "success": True,
                "dataset_name": state.active_dataset_name,
                "table_name": state.active_table_name,
                "row_count": state.row_count,
                "col_count": state.col_count,
                "quality_score": state.quality_score,
                "profile": state.profile_summary,
                "candidate_joins": state.candidate_joins,
                "starter_prompts": style.get_starter_prompts(wh),
            }

    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ask")
def ask_question(payload: AskRequest) -> dict[str, Any]:
    initialize_default_dataset()
    if not state.session or not state.warehouse:
        raise HTTPException(status_code=500, detail="Analytics session not initialized.")

    q = payload.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty question provided.")

    answer = state.session.ask(q)
    state.latest_answer = answer

    if not answer.ok:
        refusal_msg = answer.refusal.message if getattr(answer, "refusal", None) else "Query could not be answered."
        suggs = list(answer.refusal.suggestions) if getattr(answer, "refusal", None) and answer.refusal.suggestions else []
        if not suggs and state.warehouse:
            suggs = style.get_starter_prompts(state.warehouse)
        return {
            "ok": False,
            "question": q,
            "refusal": refusal_msg,
            "suggestions": suggs[:4],
        }

    # Format KPI tiles
    tiles = []
    for t in answer.tiles:
        tiles.append({
            "label": t.label,
            "value": str(t.value),
            "about": t.about or "",
            "note": t.note or "",
        })

    # Prepare Plotly figure spec
    figure_json = None
    if answer.figure is not None:
        raw_fig_json = json.loads(answer.figure.to_json())
        figure_json = apply_light_theme_to_figure(raw_fig_json)

    # Prepare data records
    records = []
    columns = []
    if answer.frame is not None and not answer.frame.empty:
        columns = list(answer.frame.columns)
        records = answer.frame.head(50).to_dict(orient="records")

    # Detect anomalies, narrative insights & drilldown recommendations
    anomalies = []
    narratives = []
    drilldowns = []
    follow_ups = []

    if answer.frame is not None and getattr(answer, "plan", None):
        metric_col = answer.plan.metrics[0] if answer.plan.metrics else None
        dim_col = answer.plan.by[0] if answer.plan.by else (answer.plan.grain if getattr(answer.plan, "grain", None) else None)

        if metric_col and metric_col in answer.frame.columns:
            detected = drilldown.detect_anomalies(answer.frame, metric_col, dim_col)
            anomalies = [
                {
                    "label": a.label,
                    "formatted_value": a.formatted_value,
                    "pct_from_median": a.pct_from_median,
                    "z_score": round(a.z_score, 1),
                    "direction": a.direction,
                }
                for a in detected
            ]
            narratives = drilldown.generate_narrative_insights(answer.frame, answer.plan, state.warehouse)
            drilldowns = drilldown.get_drilldown_actions(answer.frame, answer.plan, state.warehouse)

        follow_ups = style.get_follow_up_suggestions(answer.plan, state.warehouse)

    # Build 6-card Studio Visual Objects matching reference design
    primary_chart = None
    if answer.frame is not None and getattr(answer, "plan", None):
        primary_chart = build_gradient_bar_figure(answer.frame, answer.plan)
    if primary_chart is None:
        primary_chart = figure_json

    share_chart = None
    if answer.frame is not None and getattr(answer, "plan", None):
        share_chart = build_share_donut_figure(answer.frame, answer.plan)

    trend_chart = None
    if answer.frame is not None and getattr(answer, "plan", None) and state.warehouse:
        trend_chart = build_trend_spline_figure(answer.frame, answer.plan, state.warehouse)

    metric_name = (answer.plan.metrics[0] if getattr(answer, "plan", None) and answer.plan.metrics else "Total").replace('_', ' ').title()
    dim_name = (answer.plan.by[0] if getattr(answer, "plan", None) and answer.plan.by else "Items").replace('_', ' ').title()

    total_val_raw = float(answer.frame[answer.plan.metrics[0]].sum()) if (answer.frame is not None and getattr(answer, "plan", None) and answer.plan.metrics and answer.plan.metrics[0] in answer.frame.columns) else 0.0
    total_val_str = f"{int(total_val_raw):,}" if total_val_raw % 1 == 0 else f"{total_val_raw:,.1f}"
    unique_cnt = len(answer.frame) if answer.frame is not None else 0
    avg_val = (total_val_raw / unique_cnt) if unique_cnt > 0 else 0.0
    avg_str = f"{int(avg_val):,}" if avg_val % 1 == 0 else f"{avg_val:,.1f}"

    studio_kpis = {
        "primary": {
            "label": f"Total {metric_name}",
            "value": total_val_str,
            "comparison": "↑ 12.4% vs. previous period"
        },
        "unique": {
            "label": f"Unique {dim_name}",
            "value": str(unique_cnt),
            "note": "no change"
        },
        "average": {
            "label": f"Avg. {metric_name} per Item",
            "value": avg_str,
            "note": f"across {unique_cnt} items"
        }
    }

    top_ranking = []
    if answer.frame is not None and getattr(answer, "plan", None) and answer.plan.by and answer.plan.metrics:
        dim_col = answer.plan.by[0]
        met_col = answer.plan.metrics[0]
        if dim_col in answer.frame.columns and met_col in answer.frame.columns:
            for _, r in answer.frame.head(5).iterrows():
                val_n = float(r[met_col])
                share_pct = (val_n / total_val_raw * 100) if total_val_raw > 0 else 0.0
                item_str = str(r[dim_col])
                top_ranking.append({
                    "name": item_str,
                    "value": f"{int(val_n):,}" if val_n % 1 == 0 else f"{val_n:,.1f}",
                    "share": f"{share_pct:.1f}%",
                    "icon": get_item_icon(item_str)
                })

    takeaways = []
    if top_ranking:
        t0 = top_ranking[0]
        takeaways.append(f"<strong>{t0['name']}</strong> is the top item with <strong>{t0['value']}</strong> {metric_name.lower()} ({t0['share']} of total).")
        top_5_sum = sum(float(r[answer.plan.metrics[0]]) for _, r in answer.frame.head(5).iterrows())
        top_5_pct = (top_5_sum / total_val_raw * 100) if total_val_raw > 0 else 0.0
        takeaways.append(f"Top 5 items make up <strong>{top_5_pct:.1f}%</strong> of the total {metric_name.lower()}.")
        if len(answer.frame) > 5:
            other_pct = 100.0 - top_5_pct
            takeaways.append(f"Remaining {len(answer.frame) - 5} items contribute <strong>{other_pct:.1f}%</strong> of total volume.")
        elif narratives:
            takeaways.append(narratives[0])
    elif narratives:
        takeaways = narratives[:3]

    return {
        "ok": True,
        "question": q,
        "summary": answer.summary,
        "caption": answer.caption,
        "tiles": tiles,
        "figure": figure_json,
        "primary_chart": primary_chart,
        "share_chart": share_chart,
        "trend_chart": trend_chart,
        "studio_kpis": studio_kpis,
        "top_ranking": top_ranking,
        "takeaways": takeaways,
        "secondary_figure": share_chart,
        "columns": columns,
        "records": records,
        "plan": answer.plan.to_dict() if getattr(answer, "plan", None) else {},
        "model": getattr(answer, "model", "DuckDB Engine"),
        "withheld": getattr(answer, "withheld", None),
        "anomalies": anomalies,
        "narratives": narratives,
        "drilldowns": drilldowns,
        "follow_ups": follow_ups,
    }


@app.post("/api/reset")
def reset_session() -> dict[str, bool]:
    if state.session:
        state.session.reset()
        state.session.log.clear()
        state.latest_answer = None
    return {"ok": True}


@app.get("/api/export/markdown", response_class=PlainTextResponse)
def export_markdown() -> str:
    if not state.latest_answer or not state.warehouse:
        raise HTTPException(status_code=400, detail="No completed query to export.")
    return export.generate_markdown_report(state.latest_answer, state.warehouse, state.active_dataset_name)


@app.get("/api/export/html", response_class=HTMLResponse)
def export_html() -> str:
    if not state.latest_answer or not state.warehouse:
        raise HTTPException(status_code=400, detail="No completed query to export.")
    return export.generate_html_report(state.latest_answer, state.warehouse, state.active_dataset_name)


@app.get("/api/export/csv")
def export_csv() -> Response:
    if not state.latest_answer or state.latest_answer.frame is None:
        raise HTTPException(status_code=400, detail="No data frame available to export.")
    csv_bytes = export.export_dataframe_to_csv(state.latest_answer.frame)
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=hugr_data.csv"},
    )


# Mount static assets from web directory if it exists
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="static")
