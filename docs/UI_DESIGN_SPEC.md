# Hugr UI/UX Design Specification

**Status**: Authoritative UI/UX Design Specification  
**Design Philosophy**: AI-First Data Intelligence, Minimal Restraint, Rich Polish  
**Rule**: Minimal does not mean empty. Rich does not mean cluttered. Real data only; no fake/hardcoded analytical numbers.

---

## 1. Design System & Aesthetics

### 1.1 Color Palette
- **Canvas / Background**: Deep dark zinc `#090d16` with fixed atmospheric radial gradients (indigo `rgba(99, 102, 241, 0.08)`, violet `rgba(139, 92, 246, 0.06)`, cyan `rgba(6, 182, 212, 0.05)`).
- **Surfaces / Cards**: Translucent glassmorphism `#0f172a` (opacity 0.65 to 0.75) with `backdrop-filter: blur(16px)` and subtle borders (`rgba(255, 255, 255, 0.08)`).
- **Accents**:
  - Primary Gradient: Indigo `#6366f1` → Purple `#a855f7` → Cyan `#06b6d4`
  - Active Status: Emerald `#10b981` with soft glow
  - Text Primary: White / Slate 50 (`#f8fafc`)
  - Text Secondary: Slate 400 (`#94a3b8`)
  - Text Muted: Slate 500 (`#64748b`)

### 1.2 Typography
- **Headings & Display**: `'Outfit', -apple-system, sans-serif` (letter-spacing: -0.03em, semibold/bold).
- **Body & Controls**: `'Inter', -apple-system, sans-serif` (clean, readable, high contrast).

---

## 2. Phase 1 — Initial Experience (Active Scope)

### 2.1 Brand Header & Navigation
- **Glyph**: Radiant `✦` with indigo/cyan gradient drop-shadow.
- **Wordmark**: `Hugr` in Outfit with silver gradient.
- **Badge**: Pill badge `Universal AI Data Analyst`.
- **Status Pill**: Shows active dataset name, row count, and column count with glowing green dot indicator.

### 2.2 AI-First Hero & Question Input
- **Hero Title**: "Ask anything about your data" with high-contrast gradient text.
- **Hero Subtitle**: Clear explanation of automated discovery across CSV or Excel files.
- **Question Bar**:
  - Prominent glassmorphic input box.
  - Border transition and focus glow on interaction.
  - Contextual placeholder reflecting active data structure.
  - Clear `Ask` button with primary styling.

### 2.3 CSV/XLSX Ingestion Entry Point
- **Format Support**: Upload CSV (`.csv`) and Excel (`.xlsx`, `.xls`) files.
- **Engine**: Direct ingestion into DuckDB in-memory session via `Warehouse.from_df()`.
- **Discovery**: Automated execution of `schema_discovery.py` classifying measures, dimensions, dates, and ID columns.
- **Feedback**: Instant status display with row count, column count, and dataset name.

### 2.4 Dynamic Starter Prompts (No Fake Data)
- Dynamically extracted from the active dataset's real schema catalog (`wh.catalog`).
- Generates natural starter questions such as:
  - Total `{measure}` by `{dimension}`
  - Average `{measure}` by `{dimension}`
  - `{measure}` over time (if temporal grain exists)
  - Top 5 `{dimension}` by `{measure}`
- Guaranteed 100% grounded in real dataset columns; never hardcoded or hallucinated.

### 2.5 Intentional Composition (Avoiding Emptiness)
- When no questions have been asked yet, render 3 balanced capability cards:
  1. **Conversational Analytics**: Ask questions in plain English without SQL or manual pivot tables.
  2. **Automated Discovery**: Dynamic column classification identifies measures, dimensions, and temporal grains in seconds.
  3. **Zero-Hallucination OLAP**: Verified SQL computation in DuckDB ensures every figure is mathematically exact.

---

## 3. Phase 2 — Upload Integration (Active Scope)

### 3.1 Tabular Ingestion Lifecycle
- **Pipeline Order**: `Upload → Ingestion → Cleaning → Profiling → Schema Discovery → Catalog → DuckDB`.
- **Format Support**: Delimited text (`.csv`, `.tsv`, `.txt`, `.psv`) and Excel workbooks (`.xlsx`, `.xls`).
- **Real User Files Only**: Strictly 0 synthetic/fake numbers.
- **Engine**: Ingestion via `src/dtp/ingest.py`, registering sanitized tables into DuckDB memory sessions with typed `Catalog`.

### 3.2 Data Cleaning Engine
- Strips cell whitespace on all string values.
- Replaces sentinel null forms (`"", "-", "--", "n/a", "na", "nan", "null", "none", "?", "#n/a"`) with `np.nan`.
- Coerces formatted numeric text (currency `$`, `€`, `£`, percent `%`, and comma numbers `1,234.50`) into floats.
- Sanitizes and deduplicates column headers.

### 3.3 Data Profiling & Quality Communication
- Profiles total rows, columns, cells, missing cell percentage, and overall `quality_score`.
- Identifies duplicate rows and candidate primary keys.
- Surfaces dataset readiness via a compact glassmorphic card:
  - Header: Status (`Ready for Analysis`), Table identifier, and Quality Score badge.
  - Metrics Grid: Records, Columns, Discovered Measures, Discovered Dimensions.
  - Collapsible Drawer: Discovered Measures with unit badges, Dimensions, Temporal Grains, Candidate Keys, and Cleaning Audit Notes.
- Communicates dataset readiness without UI clutter or pushing input controls below the fold.

---

---

## 4. Phase 3 — Conversational Analytics & Exploration
- Interactive dark-mode Plotly visualizations with responsive hover tooltips and Outfit/Inter typography.
- Context-aware dynamic follow-up query suggestions derived from active plan and catalog.
- Multi-turn dialogue memory with turn tracking and non-destructive plan patching.
- Zero-hallucination verification badges attributing mathematical operations to DuckDB.

---

## 5. Phase 4 — Deep Drilldowns & Anomaly Detection (Active Scope)

### 5.1 Automated Outlier & Anomaly Detection
- Statistical anomaly detection using median / Median Absolute Deviation (MAD) robust z-scores (`robust_z_scores`).
- Flags significant outliers (`abs(z) >= 2.5`) on any result frame with >= 4 data points.
- Surfaces an elegant amber alert banner (`.hugr-anomaly-alert`) reporting the outlier's value, percentage deviation from median, and sigma score.

### 5.2 Segment Analysis & Concentration
- Evaluates top and bottom segment contributions and share of total.
- Computes Pareto concentration (e.g. share of top 3 segments vs aggregate).
- Measures spread and disparity ratios (top segment value vs group median).

### 5.3 Automated Narrative Insights
- Deterministic, data-grounded analytical summaries derived directly from DuckDB execution numbers:
  - **Leading Segment**: Highlights highest-volume contributor and its exact share percentage.
  - **Concentration**: Summarizes top-3 concentration whenever it accounts for >= 50% of the total.
  - **Spread**: Outlines performance ratio of leader vs group median.
  - **Statistical Outliers**: Direct narrative description of extreme points.
- Rendered in a polished dark glass card (`.hugr-narrative-card`).

### 5.4 Interactive Drilldown Recommendations
- Suggests logical next-level drilldown actions:
  - Filtering into leader and breaking down by secondary dimension (`where <dim> is '<val>' by <sub_dim>`).
  - Restricting view to top 5 groups.
  - Generating temporal trend for the leading segment.
- Rendered as interactive pill chips (`.hugr-drilldown-chip`).

---

## 6. Subsequent Phases (Roadmap Reference)
- **Phase 5 — Export, Sharing & Multi-Dataset Synthesis**: Cross-dataset joins, PDF/HTML insight reports, and collaborative workspaces.


