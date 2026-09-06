# Hugr Decision Logs & Architecture Evolution

This document tracks all key technical decisions, trade-offs, and changes made during the evolution of the Hugr platform.

---

## Log Entry 001: Transition from Vertical Supply-Chain to Dataset-Agnostic Backend
- **Date**: 2026-09-06
- **Context**: Hugr was initially crafted for the DataCo Smart Supply Chain dataset with static metric registrations (`revenue`, `profit`, `on_time_rate`) and fixed dimension definitions (`market`, `department`, `shipping_mode`). The goal is to make the backend universal across arbitrary datasets (HR, E-commerce, Education, Healthcare, Urban Planning, Finance).
- **Decisions**:
  1. **Dynamic Schema Discovery**: Implement automated column classification (`schema_discovery.py`) inspecting cardinality, patterns, and dtypes to classify columns into:
     - Numeric Measures
     - Categorical Dimensions
     - Date/Time Columns
     - ID Columns
     - Boolean Columns
  2. **Dynamic Semantic Catalog**: Introduce a first-class `Catalog` abstraction in `metrics.py` holding generated metrics (`sum_*`, `avg_*`, `min_*`, `max_*`, `row_count`), dimensions, and time grains.
  3. **Preserve Deterministic Guarantees**: Retain plan-based LLM querying, AST checks, DuckDB in-memory OLAP, and two-layer hallucination verification.
  4. **Do Not Touch UI**: All refactoring is confined to `src/dtp/` and tests.
  5. **Incremental Migration**: Maintain backward compatibility for existing supply-chain tests where they make architectural sense, while testing the new universal backend against 6 real-world messy public datasets.

---

## Log Entry 002: Verification & Real-World Validation Across 6 Domains
- **Date**: 2026-09-06
- **Context**: Validation of the dataset-agnostic backend against real messy open datasets without adding hardcoded domain rules or column names.
- **Acquired Public Datasets** (stored under `data/sample_datasets/`):
  1. **HR / Employees**: Adult Census Income dataset (UCI / US Census, CC BY 4.0, 1,500 rows x 15 columns). Messy features: missing values in `workclass`/`occupation`, mixed categories.
  2. **E-commerce / Sales**: Chipotle Orders dataset (Public Domain / Kaggle, 1,500 rows x 6 columns). Messy features: price formatted with `$` strings (cleaned to numeric during load), variable item options.
  3. **Education / Students**: Student Performance & Alcohol Consumption dataset (UCI, CC BY 4.0, 395 rows x 33 columns). Messy features: binary indicators, numeric grade measures, family background dimensions.
  4. **Healthcare / Clinical**: Heart Disease Cleveland dataset (UCI, CC BY 4.0, 303 rows x 14 columns). Messy features: encoded categorical clinical indicators, missing values as `?`.
  5. **Urban Planning / Mobility**: Capital Bikeshare Bike Sharing dataset (UCI, CC BY 4.0, 1,500 rows x 17 columns). Messy features: temporal fields (`dtedday`, `hour`, `season`), weather metrics, count targets (`cnt`, `casual`, `registered`).
  6. **Finance / Banking**: Bank Direct Marketing Campaign dataset (UCI, CC BY 4.0, 1,500 rows x 21 columns). Messy features: call duration metrics, previous outcome dimensions, social/economic context attributes.
- **Backend Changes**:
  - `Warehouse.from_df()` and `Warehouse.from_tables()` create `CREATE TABLE` entries in DuckDB in-memory session to ensure thread-safe cursors access the datasets seamlessly.
  - `Filters.clauses()` handles datasets without date/time columns gracefully.
  - Chart selector (`choose()` and `auto_figure()`) consumes dynamic temporal grains directly from `M.get_active_catalog()`.
  - AI Agent planner, validator, and hallucination guard query `wh.catalog` dynamically.
- **Test Suite Verification**:
  - `tests/test_universal_backend.py`: All 14 dynamic schema discovery, DuckDB aggregation, chart selection, and agent execution tests passed.
  - Full test suite: **939 passed**, 0 failed.

---

## Log Entry 003: Full Universal Backend E2E Validation Across 6 Datasets & Local Ollama Support
- **Date**: 2026-09-06
- **Context**: Before any UI work, validate the complete backend flow end-to-end on all 6 sample datasets (`LLM → structured Plan → dynamic Catalog validation → DuckDB execution → chart selection → hallucination guard → final answer`) with 8-10 meaningful questions per domain (aggregation, filtering, grouping, time-based, invalid/refusal, multi-turn). Support local Ollama (`gemma3:4b`) as fallback when no Anthropic API key is provided.
- **Decisions**:
  1. **Local Ollama Model Integration (`client.py`)**:
     - Introduced `OllamaModel` targeting `http://localhost:11434` (with model default `gemma3:4b`).
     - Uses JSON mode formatting with markdown fence cleanup and JSON parsing fallback.
     - Added `is_ollama_available()` check and automated provider cascading: Anthropic Key -> Local Ollama -> Keyless Keyword Stub.
  2. **Dynamic Metric & Dimension Resolution**:
     - `plan.py`: `_get_active_time_grains()` queries catalog-specific time grains, preventing fallback to retail `order_date`.
     - Gracefully drops date filters and time grains with explicit diagnostic notes when datasets lack temporal columns.
     - `session.py`: Dynamically derives KPI tiles, captions, summaries, and fallback shapes from active catalog rather than retail defaults (`"records"` instead of `"order lines"`).
     - Resets sorting when follow-up queries explicitly select a new metric.
  3. **Universal Keyword Parsing & Multi-Word Filter Support**:
     - Updated `where` clause extraction to capture multi-word and quoted values (`where item_name is Chicken Bowl`).
     - Added metric prefix aliases (`sum`, `avg`, `min`, `max`) with spaces and underscores, plus English plural matching (`-s`, `-es`), while preserving strict word boundaries to avoid false substring matches (`"average"` matching `"age"`).
     - Restrained multi-turn patching to genuine follow-up phrasing (`"by ..."`, `"where ..."`, `"top ..."`) to prevent unknown metrics from silently adopting previous turn metrics.
  4. **Generalized Scope Guard (`guard.py`)**:
     - Expanded `personal_data` patterns to protect employee, student, patient, customer, and user names and emails across domains.
     - Expanded `write` patterns to catch mutation verbs (`increase`, `decrease`, `raise`) and nouns (`balance`, `accounts`, `salary`).
- **Test Suite Verification**:
  - `tests/test_universal_agent_e2e.py`: 56+ natural language questions tested across all 6 domains and live Ollama LLM execution; all 7 test suites passed (100%).
  - Full repo test suite: **983 passed**, 0 failed, 0 regressions.

---

## Log Entry 004: Phase 1 — Initial Experience Implementation
- **Date**: 2026-09-06
- **Context**: Implement Phase 1 of the Hugr user experience as defined in `docs/UI_DESIGN_SPEC.md`: strong AI-first starting interaction, question input, CSV/XLSX ingestion entry point, branding, and polished intentional composition that does not feel empty or cluttered.
- **Decisions & Implementation**:
  1. **Authoritative UI/UX Design Specification (`docs/UI_DESIGN_SPEC.md` & `UI.md`)**:
     - Formulated explicit design tokens: `#090d16` canvas background, glassmorphism (`rgba(15, 23, 42, 0.65)` with backdrop blur), primary accent gradient (indigo `#6366f1` -> purple `#a855f7` -> cyan `#06b6d4`), and typography (`Outfit` for display/headings, `Inter` for body).
     - Strict adherence to core constraint: Real data only; no fake or hardcoded analytical numbers.
  2. **Modular Style & Component Architecture (`src/dtp/dashboard/style.py`)**:
     - `inject_custom_css()`: Injects custom CSS rules for dark canvas, glow accents, glassmorphic cards, and responsive grids.
     - `render_brand_header()`: Top brand identity with glowing glyph `✦`, wordmark `Hugr`, and `Universal AI Data Analyst` pill.
     - `render_dataset_pill()`: Live indicator pill displaying active dataset name, row count, and column count with glowing status dot.
     - `render_hero_intro()`: Centered typographic hero inviting natural language queries.
     - `render_initial_cards()`: 3 capability cards (Ask Naturally, Dynamic Discovery, Hallucination Guard) providing a balanced, intentional initial state that never feels barren.
     - `get_starter_prompts(wh)`: Generates real, schema-derived starter prompts dynamically from `wh.catalog` measures and dimensions (e.g. `total <measure> by <dim>`, `average <measure> by <dim>`, `top 5 <dim> by <measure>`).
  3. **Universal CSV/XLSX Ingestion Entry Point (`dashboard/app.py`)**:
     - Embedded `st.file_uploader` supporting `.csv`, `.xlsx`, and `.xls` files.
     - Loads datasets via `pd.read_csv` or `pd.read_excel` (using `openpyxl`).
     - Mounts uploaded datasets into DuckDB via `Warehouse.from_df()` with automated column discovery.
     - Seamlessly swaps the active `Session` and dataset status pill without breaking view routing.
     - Clean clearing mechanism to revert to the default warehouse snapshot when files are removed.
  4. **Backward Compatibility & Harness Verification**:
     - Preserved exact Streamlit widget contracts tested in `tests/test_dashboard_ask.py` (`st.title(ASK_TITLE)` presence, single question input widget, single button before query execution, caption conventions).
     - All 17 `test_dashboard_ask.py` tests pass without modification.
     - Added dedicated test suite `tests/test_phase1_initial_experience.py` covering CSS tokens, dynamic prompts, ingestion flow, and `AppTest` rendering.
- **Verification**:
  - `tests/test_phase1_initial_experience.py`: 5 passed.
  - Dashboard test suite: 92 passed, 0 failed.
  - Full repo test suite: **988 passed**, 0 failed, 0 regressions.

---

## Log Entry 005: Phase 2 — Upload Integration & Dataset Readiness
- **Date**: 2026-09-06
- **Context**: Implement Phase 2 — Upload Integration ensuring robust end-to-end processing of real user files (CSV, TSV, XLSX, XLS) without fake/sample data, executing: `Upload → Ingestion → Cleaning → Profiling → Schema Discovery → Catalog → DuckDB`. Clearly communicate dataset readiness without UI clutter, preserving the active dataset for subsequent analysis phases.
- **Decisions & Implementation**:
  1. **Universal Tabular Ingestion Engine (`src/dtp/ingest.py`)**:
     - Unified `ingest_tabular(source, filename, sheet_name=None) -> IngestionResult`.
     - Supports byte buffers, file paths, and Excel workbooks (`.xlsx`, `.xls` via `openpyxl`).
     - Automated encoding detection (`utf-8`, `utf-8-sig`, `cp1252`, `latin-1`) and CSV delimiter sniffing (`csv.Sniffer`).
     - Header sanitization and deduplication.
  2. **Audit-Grade Data Cleaning (`clean_dataframe`)**:
     - Strips leading and trailing whitespace across all string cells.
     - Replaces sentinel null forms (`"", "-", "--", "n/a", "na", "nan", "null", "none", "?", "#n/a"`) with `np.nan` while avoiding destructive removal of legitimate statuses (e.g. `pending`).
     - Coerces formatted numeric text (currency `$`, `€`, `£`, percent `%`, and comma thousand-separators `1,234.50`) into clean numeric floats when >= 80% of non-null cells conform.
     - Produces a structured `CleaningSummary` tracking every transformation.
  3. **Comprehensive Data Readiness Profiling (`profile_dataset`)**:
     - Calculates total rows, columns, cells, missing cell percentage, and overall `quality_score` (`100 - missing_pct`).
     - Identifies duplicate rows and candidate primary keys.
     - Generates per-column distributions, inferred roles, and non-null samples.
  4. **Dataset Readiness UI Component (`src/dtp/dashboard/style.py` & `dashboard/app.py`)**:
     - `render_dataset_readiness()`: Renders a compact, dark-mode glassmorphic readiness card showing status (`Ready for Analysis`), active table identifier, quality score badge, records, columns, measures, and dimensions count.
     - Includes a collapsible detail drawer (`Inspect Discovered Schema & Data Quality`) surfacing measure unit tags, dimension tags, temporal grains, candidate keys, and cleaning audit notes.
     - Zero clutter: collapsible design prevents displacing the question input or pushing content below the fold.
     - Preserves `AppTest` widget invariance (`asking.metric.values == []` on initial load).
  5. **Session Persistence**:
     - Stores `uploaded_warehouse`, `uploaded_ingestion_result`, and `ask_session` in `st.session_state` to ensure the dataset remains immediately available for conversational analytics.
- **Verification**:
  - `tests/test_phase2_upload_integration.py`: 6 passed covering real CSV (`ecommerce_orders.csv`), messy Excel (`.xlsx`), sentinel null cleaning, numeric coercion, profiling metrics, and active AI querying.
  - Dashboard test suite (`test_dashboard_ask.py`, `test_phase1_initial_experience.py`, `test_phase2_upload_integration.py`, `test_dashboard_auth.py`, `test_views.py`): 98 passed, 0 failed.
  - Full repo test suite: **994 passed**, 0 failed, 0 regressions.

