# Tech Stack Decision (Phase 0)

Status: **decided for Phase 1, provisional for Phases 2-3.** Change now is cheap;
change after Phase 2 starts is not. Raise objections against the numbered
reasons below rather than the conclusion.

## Environment as found

Python 3.12.10, Node 24.18, npm 12.0, git 2.54, Windows 11. `pandas` 3.0.2,
`numpy`, `pyarrow`, `scipy`, `plotly`, `streamlit`, `openpyxl`, `PyYAML`,
`Jinja2`, `fastapi` were already installed; `duckdb`, `pytest` and `anthropic`
were added. `requirements.txt` pins what is actually installed, not aspirational
versions.

> pandas is on **3.x**, not 2.x. Copy-on-Write is the default and the default
> string dtype is Arrow-backed `str`. Code written against 2.x idioms
> (`applymap`, chained assignment, `object` dtype assumptions) will misbehave.

## Layer by layer

| Layer | Choice | Why this, not the alternative |
|---|---|---|
| Ingest / clean | Python + pandas | 1. Already installed. 2. Every messy-data operation this project needs (per-format date parsing, currency stripping, fuzzy category folding) is a few lines. Polars is faster but the data volume here does not justify a second dataframe API. |
| Raw storage | files in `data/raw/`, read as **text** | 3. Type inference on import silently destroys the defects we are paid to find. Everything is read as strings; typing is an explicit, logged step. |
| Clean storage | Parquet snapshots in `data/versions/` | 4. Typed, compressed, self-describing, and readable by both DuckDB and pandas without a server. |
| Query layer | DuckDB over Parquet | 5. Phase 3 needs real SQL for the agent to generate against. DuckDB gives that with zero infrastructure, and reads the Parquet files directly. 6. A Postgres instance would add hosting and credentials for no analytical gain at this size. |
| Validation | small in-repo rule engine | 7. Great Expectations is the obvious library and is too heavy for this: it brings its own config format, store layout and CLI. Roadmap 1.2 needs range / required / referential checks that fail loudly — that is a few hundred lines we fully control. |
| Dashboard | *provisional:* Streamlit + Plotly | 8. Already installed, and one language across the whole stack. Chosen because Phase 2's "innovation layer" (anomaly flags, comparative views, narrative annotations) is mostly data work, not UI work. **Revisit at Phase 2 kickoff:** if the dashboard needs to look bespoke rather than functional, a React front end over a FastAPI endpoint is the alternative, and Node 24 is present for it. |
| AI agent | Claude via the official `anthropic` SDK | 9. Roadmap 3.1 wants text → intent → **scoped** SQL → chart. That is tool-use against a fixed schema, which is exactly what the SDK's tool calling does. 10. Exact model id is chosen at Phase 3 kickoff against the current model list rather than hard-coded here, where it would go stale. |
| Tests | pytest | 11. Roadmap "done when" criteria are only real if they are executable. Every deliberate defect in the fixture has a test asserting the pipeline still catches it. |

## Consequences accepted

- **No orchestrator** (Airflow/Dagster/Prefect). The pipeline is a CLI that runs
  end to end in one process. If scheduling is needed later, a cron entry calling
  the same CLI is enough at this size.
- **No cloud dependency.** Everything runs locally, so there is no account,
  credential or spend approval on the critical path. Deployment target for
  Phase 4 is still open.
- **Secrets** live in `.env` (gitignored). No API key is committed; nothing in
  Phase 1 needs one.

## Open, and who should decide

| Question | Blocks | Owner |
|---|---|---|
| Streamlit vs React front end | Phase 2 build | dashboard dev + stakeholder, on how polished the demo must look |
| Where this is deployed | Phase 4 launch | whoever owns hosting |
| Claude model tier and per-query budget | Phase 3 | whoever owns the API spend |
