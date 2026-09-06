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

