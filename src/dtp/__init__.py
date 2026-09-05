"""dtp - Data-to-Insights Platform.

Layered so each phase of project_roadmap.md maps onto modules:

  Phase 1.1  io_utils, profile, schema_map    audit & assessment
  Phase 1.2  clean, validate                  cleaning & standardization
  Phase 1.3  pipeline, versioning, dictionary, monitoring
  Phase 2    warehouse, metrics, insights, charts, dashboard
  Phase 3    agent                            questions in, checked answers out

Each layer reads the one above it and never the reverse. `warehouse` owns every
Parquet read, `metrics` is the only module that writes analytical SQL, and both the
dashboard and the agent are consumers of the values those produce - which is why one
`Answer` renders on a screen, prints in a terminal, and is asserted on in a test.
"""

from pathlib import Path

__version__ = "0.1.0"

# Repo root = two levels up from src/dtp/__init__.py
ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
CLEAN_DIR = ROOT / "data" / "clean"
VERSIONS_DIR = ROOT / "data" / "versions"
CONFIG_DIR = ROOT / "config"
REPORTS_DIR = ROOT / "reports"
DOCS_DIR = ROOT / "docs"
