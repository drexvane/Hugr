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

---

## Log Entry 006: Phase 3 — Conversational Analytics & Exploration
- **Date**: 2026-09-06
- **Context**: Implement Phase 3 — Conversational Analytics & Exploration according to `docs/UI_DESIGN_SPEC.md` and user specifications: interactive chart styling with high-contrast typography and tooltips, context-aware follow-up query suggestions, multi-turn dialogue memory, dynamic KPI summary cards, and verified zero-hallucination execution.
- **Decisions & Implementation**:
  1. **Plotly Dark Mode Styling & Interactive Tooltips (`apply_dark_theme_to_figure`)**:
     - Automatically applies dark glass theming to all answer figures (`paper_bgcolor="rgba(0,0,0,0)"`, `plot_bgcolor="rgba(15, 23, 42, 0.4)"`).
     - Refines typography with `Outfit` for titles and `Inter` for tick labels and axes.
     - Sets custom dark hoverlabels (`#1e293b` surface with indigo border glow `#6366f1`) and subtle gridlines (`rgba(255, 255, 255, 0.08)`).
  2. **Context-Aware Dynamic Follow-Up Suggestions (`get_follow_up_suggestions` & `render_follow_up_chips`)**:
     - Analyzes active `plan` and DuckDB catalog to dynamically generate 3-4 logical next turns:
       - Alternative breakdown dimensions (`break down by <dim>`)
       - Ranking limits (`top 5`)
       - Temporal trends (`over time` if temporal columns exist)
       - Secondary metric comparisons (`and <other_metric>`)
     - Renders suggestions as sleek pill chips (`↳ <suggestion>`) beneath answers.
  3. **Multi-Turn Dialogue Memory & Plan Patching (`dashboard/app.py`)**:
     - Preserves complete conversational history in `session.log` while seamlessly patching follow-up queries onto preceding plans (e.g. `average age by sex` followed by `by choice description` preserves `avg_age` and switches dimension).
     - Renders historical turns inside expanders with question titles, allowing instant retrospective review of earlier charts and metrics.
  4. **Zero-Hallucination & Model Attribution Badge (`render_verification_badge`)**:
     - Surfaces verified execution indicators confirming all numbers were computed deterministically in DuckDB and validated against the semantic catalog.
  5. **Harness Stability**:
     - Preserved all `AppTest` widget and metric contracts.
     - Added dedicated test suite `tests/test_phase3_conversational_analytics.py`.
- **Verification**:
  - `tests/test_phase3_conversational_analytics.py`: 5 passed.
  - Dashboard test suite: 103 passed, 0 failed.
  - Full repo test suite: **999 passed**, 0 failed, 0 regressions.

---

## Log Entry 007: Phase 4 — Deep Drilldowns & Anomaly Detection
- **Date**: 2026-09-06
- **Context**: Implement Phase 4 — Deep Drilldowns & Anomaly Detection per `docs/UI_DESIGN_SPEC.md` and user direction: automated statistical outlier detection, segment concentration comparisons, data-grounded narrative insights, and context-aware interactive drilldown recommendations.
- **Decisions & Implementation**:
  1. **Automated Anomaly & Outlier Engine (`src/dtp/drilldown.py`)**:
     - `detect_anomalies`: Leverages Median Absolute Deviation (MAD) robust z-scores to identify statistical outliers without skewing from extreme values.
     - Flags points with `abs(z) >= 2.5` on frames with >= 4 points, computing directional shift and percentage delta from median.
     - `render_anomaly_alert`: Renders an amber alert banner highlighting the outlier, exact metric value, and sigma score.
  2. **Segment Comparisons & Pareto Concentration (`analyze_segments`)**:
     - Calculates top vs bottom segment contributions and share of total.
     - Computes Pareto top-3 share percentage (flags when >= 50%).
     - Measures performance spread ratio between leading segment and group median.
  3. **Deterministic Narrative Insights (`generate_narrative_insights` & `render_narrative_insights`)**:
     - Generates 2-4 bulleted findings directly computed from the DuckDB execution frame (leading contributor, concentration, spread, and outlier notes).
     - Styled in a dark glassmorphic card (`.hugr-narrative-card`) with glowing gradient border accent.
     - 100% mathematically grounded with zero hallucination.
  4. **Interactive Context-Aware Drilldown Recommendations (`get_drilldown_actions` & `render_drilldown_chips`)**:
     - Suggests actionable next-level queries (e.g. drilling into leading segment by secondary dimension, filtering to top 5, or viewing temporal trends).
     - Rendered as interactive chips (`.hugr-drilldown-chip`).
  5. **Harness Stability**:
     - Preserved all `AppTest` widget and metric contracts.
     - Added dedicated test suite `tests/test_phase4_drilldowns_and_anomalies.py`.
- **Verification**:
  - `tests/test_phase4_drilldowns_and_anomalies.py`: 6 passed.
  - Dashboard test suite: 109 passed, 0 failed.
  - Full repo test suite: **1,005 passed**, 0 failed, 0 regressions.

---

## Log Entry 008: Universal Ingestion & Localhost Fallback Resilience
- **Date**: 2026-09-06
- **Context**: When running `streamlit run dashboard/app.py` in standalone localhost environments where offline Parquet snapshots under `data/versions/` have not been generated, the sidebar previously emitted `st.sidebar.error("No snapshot found under data/versions.")` and halted via `st.stop()`. This resulted in a blank dark screen preventing the AI-first starting experience, CSV/XLSX uploader, and Ask screen from initializing.
- **Decisions & Implementation**:
  1. **Resilient Sidebar Fallback (`dashboard/app.py`)**:
     - When `V.snapshot_ids()` returns empty, the app transitions seamlessly into Universal Ingestion Mode instead of halting.
     - Sidebar renders `"Hugr Analytics"` branding, `"✦ Universal AI Data Analyst"` caption, and defaults navigation to `ASK` view.
     - Preserves existing snapshot picker and multi-view navigation when offline snapshots are present.
  2. **In-Memory Sample Warehouse Fallback (`_sample_warehouse`)**:
     - If no user file has been uploaded yet and no snapshot exists, loads `data/sample_datasets/ecommerce_orders.csv` via `Warehouse.from_df()` into DuckDB.
     - The active dataset pill renders `"Sample: E-Commerce Orders"` with accurate row/column metrics (4,622 rows, 5 columns).
     - Starter prompts dynamically derive from the sample schema, allowing immediate zero-setup conversational analysis.
     - Once the user uploads a custom CSV/XLSX, the uploaded dataset cleanly replaces the fallback sample in `st.session_state`.
  3. **Harness Verification & Invariance**:
     - Added `test_app_boots_cleanly_without_snapshots` to `tests/test_phase1_initial_experience.py`.
     - Confirmed all 1,006 tests pass with 0 regressions.
- **Verification**:
  - Full repo test suite: **1,006 passed**, 0 failed.

---

## Log Entry 009: Phase 5 — Export, Sharing & Multi-Dataset Synthesis
- **Date**: 2026-09-06
- **Context**: Complete Phase 5 — Export, Sharing & Multi-Dataset Synthesis per authoritative `docs/UI_DESIGN_SPEC.md` and user direction: executive analysis report generation (Markdown & standalone dark-mode HTML), clean tabular data export (CSV & JSON), multi-file upload synthesis mounting into unified DuckDB sessions, and automated cross-dataset candidate join discovery.
- **Decisions & Implementation**:
  1. **Executive Analysis Report Generator (`src/dtp/export.py`)**:
     - `generate_markdown_report`: Produces a complete executive-ready intelligence report featuring metadata, question asked, summary findings, KPI metric indicators table, detected anomalies & sigma scores, automated narrative insights, top-15 underlying data records table, and the validated execution plan JSON.
     - `generate_html_report`: Produces a standalone, styled dark-mode HTML report (with Outfit/Inter typography, translucent glass cards, KPI tiles, and responsive data tables) ready for offline viewing or browser PDF printing.
     - `export_dataframe_to_csv` & `export_dataframe_to_json`: High-performance UTF-8 byte serialization for clean data downloads.
  2. **Multi-Dataset Ingestion & Synthesis Engine (`src/dtp/ingest.py`)**:
     - Added `ingest_multiple_tabular(sources)`: Ingests multiple CSV, TSV, or Excel files simultaneously into unique DuckDB tables via `Warehouse.from_tables()`.
     - Analyzes and creates typed catalogs across all ingested tables, setting the primary table to the highest-volume dataset.
     - Automatically invokes `find_candidate_joins()` to discover relations.
  3. **Automated Cross-Dataset Join Discovery (`find_candidate_joins`)**:
     - Discovers entity relationships and potential foreign keys across pairs of tables in DuckDB.
     - Evaluates exact column name matches, entity ID patterns (e.g. `customer_id` ↔ `id`), and samples data to compute value set intersection and overlap ratios.
     - Assigns confidence ratings and descriptive join rationales.
  4. **UI Integration (`dashboard/app.py` & `src/dtp/dashboard/style.py`)**:
     - `_render_answer`: Added "📥 Export & Share Intelligence Report" expander with one-click download buttons for Markdown report, standalone HTML report, and underlying CSV table.
     - `_ask_screen`: Updated file uploader to support `accept_multiple_files=True`. When multiple files are uploaded, mounts all into DuckDB, provides an active table inspection switcher, and renders discovered cross-dataset relationships via `style.render_candidate_joins()`.
  5. **Harness Stability & Verification**:
     - Download buttons render cleanly without perturbing standard `at.button` contracts.
     - Added dedicated test suite `tests/test_phase5_export_and_multi_dataset.py` (6 tests).
- **Verification**:
  - `tests/test_phase5_export_and_multi_dataset.py`: 6 passed.
  - Full repo test suite: **1,012 passed**, 0 failed, 0 regressions.

---

## Log Entry 010: Modern Luminous Material UI & Zero-Hallucination Visual Architecture
- **Date**: 2026-09-06
- **Context**: Fully execute the visual and UX quality bar specified in user instructions and visual inspiration (`designidea.mp4`). Migrate beyond framework limitations to a bespoke, modern AI workspace featuring:
  - White / very light primary canvas (`#f8fafc`).
  - Extremely restrained neon lighting only as subtle accents (emerald dot, cyan telemetry indicator).
  - Refined glassmorphism (frosted white acrylic glass, hairline borders, soft layered drop-shadows).
  - Liquid / fluid material feel (smooth cubic-bezier transitions, organic interaction).
  - Subtle 3D depth and spatial layering without visual clutter.
  - Editorial typography (`Outfit` headings, `Inter` body, `JetBrains Mono` code/metrics).
  - Grounded 100% in real DuckDB analytical data: live dataset telemetry, DuckDB column summaries (real aggregate sums and averages for measures, distinct cardinalities and top value samples for dimensions), automated initial pulse chart, dynamic KPI tiles, robust MAD outlier callouts, and multi-turn drilldown chips.
- **Decisions & Implementation**:
  1. **FastAPI Analytical Bridge (`src/dtp/api/server.py`)**:
     - Exposes typed REST endpoints directly wrapping core `dtp` engine: `GET /api/status`, `GET /api/prompts`, `POST /api/upload`, `POST /api/ask`, `POST /api/reset`, `GET /api/export/{markdown, html, csv}`.
     - Implemented `get_column_summaries(wh)`: Executes live queries in DuckDB to calculate exact totals and averages for discovered measures, and distinct counts plus top 3 samples for dimensions.
     - Implemented `get_overview_figure(wh)`: Generates an initial real-data distribution bar chart from DuckDB to avoid empty states.
     - Implemented `apply_light_theme_to_figure()`: Formats Plotly visualizations with light editorial styling, clean gridlines, and high contrast.
  2. **Bespoke Luminous Web Architecture (`web/` & `launch.py`)**:
     - `web/index.html`: Split-stage architecture comprising an Analysis Stage (live telemetry, pulse chart, answer card, narrative insights, drilldowns, data table preview, execution plan), a Right Inspector Panel (data quality badge, record/column/measure/dimension metrics, discovered measures with math aggregates, discovered dimensions with sample pills, candidate joins), and a Floating Docked Command Bar (starter prompt chips, question input, DuckDB engine tag).
     - `web/style.css`: Comprehensive design tokens adhering to luminous light canvas, translucent acrylic surfaces, subtle 3D elevations, micro-animations, and fluid responsive grid.
     - `web/app.js`: Reactive controller managing status telemetry, Plotly rendering, question execution, multi-turn drilldown flows, drag-and-drop file ingestion, and export menu toggles.
  3. **Backward Compatibility & Harness Stability**:
     - Kept `dashboard/app.py` completely intact and verified.
     - Added dedicated API server test suite `tests/test_api_server.py` (6 tests).
     - Cleaned up global catalog isolation to ensure all retail and planning test suites run in isolation.
- **Verification**:
  - `tests/test_api_server.py`: 6 passed.
  - Browser Subagent Verification on `http://localhost:8000`: Verified live page load, luminous light canvas, initial pulse chart rendering real DuckDB data, inspector telemetry with real aggregates, interactive query execution (`total quantity by item name`), KPI tiles, outlier callouts, and multi-turn drilldown navigation. Captured screenshots and session recording.
  - Full repo test suite: **1,018 passed** in 72.89s, 0 failed, 0 regressions.

---

## Log Entry 011: Interactive Star Constellation & Uncluttered 6-Card Analytical Studio
- **Date**: 2026-09-06
- **Context**: Address user feedback on visual styling and composition:
  1. Replace random floating 3D polyhedral nodes with an interactive **star constellation** canvas.
  2. Implement **slow, staggered animations** for card and chart entry to prevent visual clutter and sudden jumps.
  3. Recreate the clean **6-card studio layout** from the user's reference design within a single viewport (`100vh`):
     - **Top-Left**: Vertical Gradient Bar Chart (soft pastel blue-to-purple gradient, rotated labels).
     - **Top-Center**: Hero Total metric card (`45,670` with `+` action button) + 2 sub-KPI cards (`Unique Items` & `Avg. Quantity per Item`).
     - **Top-Right**: Share of Total Quantity donut chart with center aggregate and percentage breakdown legend.
     - **Bottom-Left**: Quantity Trend (Top 3 Items) smooth multi-line spline curves.
     - **Bottom-Center**: Top 5 Items by Quantity ranking table with category emoji icons, formatted quantities, and share percentage badges.
     - **Bottom-Right**: Key Takeaways narrative card with blue bullet dots and `+ Ask a follow-up question` button.
  4. Ensure 100% calculation integrity using real DuckDB backend queries (no fabricated data).
- **Decisions & Implementation**:
  1. **Backend Analytical Figures (`src/dtp/api/server.py`)**:
     - `build_gradient_bar_figure`: Generates vertical bar charts styled with pastel blue-to-purple gradient palette (`#6485ff` to `#b088ff`), rounded bar corners, and angled axis labels.
     - `build_share_donut_figure`: Generates a donut chart displaying top category shares and "Other", with the center total count and right-hand percentage breakdown legend.
     - `build_trend_spline_figure`: Queries DuckDB to dynamically aggregate the top 3 items across chronological or order ID buckets, rendering smooth multi-line splines with pastel tones and shaded fill.
     - `get_item_icon`: Dynamically maps item/product names to relevant emojis (🌯, 🥗, 🥑, 🥩, 🥤, 📦).
     - Expanded `/api/ask` response payload to include `primary_chart`, `share_chart`, `trend_chart`, `studio_kpis`, `top_ranking`, and `takeaways`.
  2. **Celestial Star Constellation Engine (`web/app.js`)**:
     - Built a high-performance 2D HTML5 Canvas constellation system:
       - 75 celestial stars with randomized velocities, radii (0.8px - 2.2px), and harmonic alpha pulsing for realistic twinkling.
       - Automatic distance-threshold linking (connects stars within 120px with subtle opacities from 0.05 to 0.18).
       - Mouse proximity gravitational glow: stars within 160px of the cursor gently brighten and attract.
       - Zero external WebGL or Three.js dependencies needed.
  3. **Slow Staggered Motion Architecture (`web/style.css`)**:
     - Created `studioCardRise` keyframe with `cubic-bezier(0.16, 1, 0.3, 1)` and `0.85s` duration.
     - Applied cascading staggered animation delays (`0.05s`, `0.15s`, `0.26s`, `0.38s`, `0.52s`, `0.66s`) across cards 1 to 6.
     - Plotly charts resize automatically 350ms after animation to guarantee sharp layout rendering.
  4. **Non-Intrusive Drawer & Follow-Up Expansion (`web/index.html`, `web/app.js`)**:
     - Query pill at top center expands on click for seamless follow-up questions.
     - Underlying 47 records and DuckDB AST execution plan remain accessible via a smooth slide-up bottom drawer.
- **Verification**:
  - Full browser verification completed: verified star constellation, staggered entrance, 6-card layout, interactive drawer, and responsive Plotly charts.
  - Recording saved: `hugr_constellation_studio_1788714975202.webp`.
  - Automated tests passing.


