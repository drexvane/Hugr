# Cipher Universal AI Data Analyst — System Architecture

This document describes the architectural design of Cipher as a **Universal, Dataset-Agnostic Data-to-Insights Platform**. It covers the system context, component containers, dynamic schema discovery, DuckDB analytical query engine, LLM planning and hallucination guardrail flow, and storage architectures.

---

## 1. High-Level Architecture Overview

Cipher processes arbitrary tabular datasets through five core layers:

```mermaid
flowchart TD
    subgraph Ingestion ["1. Ingestion & Storage"]
        RawData[("Raw Dataset\n(CSV / Parquet / Excel)")] --> CleanEngine["Cleaning & Profiling Engine"]
        CleanEngine --> SnapshotStore[("Versioned Parquet Snapshots\n& Manifests")]
    end

    subgraph Discovery ["2. Dynamic Semantic Layer"]
        SnapshotStore --> SchemaDiscovery["Dynamic Schema Discovery\n(Measures, Dimensions, Dates, IDs, Booleans)"]
        SchemaDiscovery --> Catalog["Dynamic Metric & Dimension Catalog"]
    end

    subgraph OLAP ["3. Query & Analytical Engine"]
        Catalog --> DuckDBEngine["Embedded DuckDB Engine\n(Thread-Safe Cursor Pool, Dynamic SQL Folding)"]
        SnapshotStore --> DuckDBEngine
    end

    subgraph Agent ["4. AI Analyst Agent & Safety"]
        UserQuestion["User Natural Language Question"] --> GuardIn["Safety & Refusal Guardrails"]
        GuardIn --> Planner["Query Planner\n(Plan vs SQL: Metrics, Dimensions, Grains, Filters)"]
        Planner --> DuckDBEngine
        DuckDBEngine --> AggFrame["Aggregated Result Frame"]
        AggFrame --> ChartSelector["Deterministic Chart Selector\n(Plotly: Line, Bar, Heatmap, Scatter, KPI)"]
        AggFrame --> Summarizer["Prose Explanation Generator"]
        Summarizer --> HallucinationGuard["Hallucination & Entity Verifier"]
    end

    subgraph Presentation ["5. Unified Response"]
        ChartSelector --> VerifiedAnswer["Verified Answer\n(Interactive Chart + Checked Prose + KPI Tiles)"]
        HallucinationGuard --> VerifiedAnswer
    end
```

---

## 2. C4 Model Context Diagram (System Context)

The C4 Context diagram shows how external actors and data sources interact with the Universal AI Data Analyst platform:

```mermaid
C4Context
    title C4 Context Diagram - Universal AI Data Analyst Platform

    Person(user, "Business / Data User", "Uploads arbitrary datasets, explores dashboards, asks natural language questions.")
    
    System(cipher, "Cipher Universal Analytics Platform", "Ingests, profiles, auto-discovers schemas, provides embedded OLAP analytics, and answers questions using planned query execution.")
    
    System_Ext(open_data, "Public / Enterprise Data Sources", "CSVs, Excel files, open data portals (HR, Sales, Health, Finance, etc.).")
    System_Ext(llm, "LLM Service (Anthropic Claude / Local)", "Translates natural language questions into structured Plans and crafts concise summaries.")

    Rel(open_data, cipher, "Loads raw tabular data into", "CSV/Parquet")
    Rel(user, cipher, "Interacts with and queries via", "Web UI / CLI / API")
    Rel(cipher, llm, "Requests structured query plan & prose from", "JSON / Prompt API")
```

---

## 3. C4 Container Diagram

```mermaid
C4Container
    title C4 Container Diagram - Cipher Architecture

    Container(cli_ui, "CLI & Presentation Interfaces", "Python Click / Streamlit", "Exposes interactive terminal (`dtp ask`, `dtp pipeline`) and dashboard screens.")
    
    Container(schema_disc, "Schema Discovery Engine", "Python / pandas", "Analyzes column cardinality, types, date patterns, boolean indicators, and financial/quantity heuristics.")

    Container(catalog, "Dynamic Semantic Catalog", "Python dataclasses", "Maintains active metrics (sum, avg, count), dimensions, time grains, and drill paths.")

    ContainerDb(duckdb_olap, "Embedded Warehouse (DuckDB)", "DuckDB C++ Engine via Python", "Performs ultra-fast analytical queries over parquet snapshots with thread-safe cursor pooling.")

    Container(agent_system, "AI Analyst Agent", "Python / LLM Client", "Deconstructs questions into structured plans, validates enums, enforces refusals, and eliminates hallucinations.")

    Container(charts_engine, "Plotly Visualization Engine", "Python / Plotly", "Selects chart types deterministically based on dimensionality and metric count.")

    Rel(cli_ui, schema_disc, "Triggers profiling and discovery on", "DataFrames")
    Rel(schema_disc, catalog, "Builds dynamic metrics & dimensions into")
    Rel(catalog, duckdb_olap, "Compiles single-call aggregate SQL into")
    Rel(cli_ui, agent_system, "Submits natural language questions to")
    Rel(agent_system, duckdb_olap, "Executes plan-generated queries against")
    Rel(duckdb_olap, charts_engine, "Feeds aggregated frames to")
```

---

## 4. Database & Analytical Engine Architecture

Cipher uses **DuckDB** as an embedded in-memory OLAP warehouse reading columnar Parquet snapshots:

```mermaid
flowchart LR
    subgraph Storage ["Columnar Storage Layer"]
        P1["table_1.parquet"]
        P2["table_2.parquet"]
        Manifest["manifest.json\n(Content hashes, row counts, schema)"]
    end

    subgraph DuckDBPool ["Warehouse Session (:memory:)"]
        direction TB
        Conn["DuckDB In-Memory Connection"]
        Conn --> V1["VIEW table_1 AS SELECT * FROM '...parquet'"]
        Conn --> V2["VIEW table_2 AS SELECT * FROM '...parquet'"]
        
        subgraph ThreadLocal ["Thread-Safe Cursor Pooling"]
            C1["Cursor (Thread 1)"]
            C2["Cursor (Thread 2)"]
            Cn["Cursor (Thread N)"]
        end
        Conn --> ThreadLocal
    end

    subgraph QueryExecution ["Analytical Query Execution"]
        PlanSQL["Gated / Folded Aggregation SQL\n(FILTER, date_trunc, group by)"]
        ThreadLocal --> PlanSQL
        PlanSQL --> ArrowDF["Zero-Copy Arrow / Pandas DataFrame"]
    end

    Storage --> DuckDBPool
```

### Key Architectural Invariants
1. **Read-Only by Construction**: DuckDB connects to `:memory:` and mounts files as views, guaranteeing that no analytical query can corrupt disk files.
2. **Thread Safety**: DuckDB connections are wrapped in `threading.local` cursor dispensers, allowing concurrent user queries without locking errors.
3. **Deterministic Hashing**: Snapshots are fingerprinted using `pd.util.hash_pandas_object` rather than raw parquet byte hashes, making checks immune to compression nondeterminism.

---

## 5. LLM Query Planning & Hallucination Guard Architecture

Cipher avoids the catastrophic failure modes of naive Text-to-SQL by enforcing an **intermediate structured query plan**:

```mermaid
sequenceDiagram
    autonumber
    actor User as User
    participant Guard as Safety & Refusal Guard
    participant LLM as LLM / Keyword Stub
    participant Validator as Plan Validator
    participant OLAP as DuckDB Warehouse
    participant Verifier as Hallucination Verifier
    participant Chart as Chart Selector

    User->>Guard: "What were total sales by region last quarter?"
    Note over Guard: Check against scope refusals (PII, forecast, causal 'why', write)
    Guard->>LLM: System Prompt + Dynamic Catalog Schema + Question
    Note over LLM: Model selects ONLY from catalog enums
    LLM-->>Validator: Plan(metrics=['sum_sales'], by=['region'], grain='quarter')
    Note over Validator: Validate metric/dimension keys against Catalog
    Validator->>OLAP: Compile & Execute single-call aggregate SQL
    OLAP-->>Verifier: Aggregated DataFrame (e.g. 4 rows)
    Validator->>Chart: Determine honest chart (e.g. horizontal bar)
    Chart-->>User: Plotly Figure
    LLM-->>Verifier: Generated summary sentence ("Sales reached $1.2M...")
    Note over Verifier: Match every numeric literal & entity against DataFrame
    alt Numbers match DataFrame
        Verifier-->>User: Verified prose caption
    else Unmatched number detected
        Verifier-->>User: Fallback deterministic template (Drop hallucination)
    end
```

---

## 6. Dynamic Schema Discovery Classification

When an unprofiled dataset is loaded, columns are classified through a deterministic hierarchy:

```mermaid
flowchart TD
    Col["Input Column"] --> IDCheck{"High Unique Ratio (>0.9)\nor Name ends in _id, id, key, code?"}
    IDCheck -- Yes --> RoleID["Role: ID Column\n(Excluded from metric sums; countable via distinct)"]
    IDCheck -- No --> DateCheck{"Datetime dtype\nor parseable date strings\nor name matches date/time/year?"}
    
    DateCheck -- Yes --> RoleDate["Role: Date/Time Column\n(Primary axis for time grains: year, quarter, month, day)"]
    DateCheck -- No --> BoolCheck{"Bool dtype\nor 2 distinct values in boolean vocabulary?"}
    
    BoolCheck -- Yes --> RoleBool["Role: Boolean Column\n(Binary filter & binary dimension)"]
    BoolCheck -- No --> NumCheck{"Numeric dtype (int, float)\nwith variance?"}
    
    NumCheck -- Yes --> MeasureUnit{"Infer Unit from column name\n(sales, revenue, price -> money\npct, rate -> percent\ndays, duration -> days\nqty, count -> count)"}
    MeasureUnit --> RoleMeasure["Role: Numeric Measure\n(Auto-generates sum, avg, min, max)"]
    
    NumCheck -- No --> RoleDim["Role: Categorical Dimension\n(Breakdown groups, heatmaps, bar charts)"]
```
