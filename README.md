# Data-to-Insights Platform

Messy data → clean data → dashboard → AI query agent. Roadmap and phase gates in
[`project_roadmap.md`](project_roadmap.md).

**Phase 1.1 (audit & assessment) is built and tested. No real data has been
loaded yet** — everything below is verified against a synthetic fixture with
deliberately injected defects.

## Quick start

```bash
pip install -r requirements.txt
pip install -e .              # puts `dtp` on PATH; also makes `python -m dtp.cli` work

# No real data yet? Generate the messy fixture and audit it:
dtp synthetic
dtp audit --raw data/_synthetic

# Once the demo data is in data/raw/:
dtp audit
```

`dtp <command>` and `python -m dtp.cli <command>` are interchangeable.

`audit` exits non-zero when it finds a BLOCKER, so it works as a CI gate.

## What `audit` produces

| Report | Answers |
|---|---|
| `reports/profiling/profiling_report.md` | per column: type, missing %, distinct count, and every defect found |
| `reports/schema/schema_matrix.md` | which columns across sources mean the same thing, and whether they can actually be joined |
| `reports/risk-summary.md` | the same findings ranked BLOCKER → LOW, each with its downstream impact and a proposed fix |

Each also has a `.json` twin for programmatic use.

## Commands

| Command | Does |
|---|---|
| `dtp audit` | all three reports (this is the one you want) |
| `dtp profile` | profiling report only |
| `dtp schema` | cross-source schema matrix only |
| `dtp risks` | ranked risk summary only |
| `dtp synthetic` | regenerate the messy test fixture |

All accept `--raw DIR` to point at a different source directory.

## What the profiler detects

Formats read without configuration: `.csv` `.tsv` `.psv` `.txt` `.xlsx` `.xlsm`
`.xls` `.json` `.jsonl` `.parquet`, with encoding and delimiter detected per file
and every sheet of a workbook treated as its own table.

Sources are read **as text**. Letting pandas infer types on import destroys the
evidence — `"1,200"`, `"$4.50"`, `"N/A"` and `"03/04/2024"` all become something
else before you ever see them. Typing is an explicit, logged step instead.

The findings it is built to surface, in the order they usually cause damage:

1. **Day/month order that the data cannot resolve** — and the worse case, one
   column holding both orders because two systems wrote to it.
2. **Multiple currencies in one column** — every `SUM` is then meaningless.
3. **Percent scale mixing** — `10%` alongside `0.1` is a 100x error on part of
   the column.
4. **Nulls stored as text** — `N/A`, `-`, `unknown`, `TBD` all pass `COUNT`.
5. **Same category spelled several ways** — `USA` / `usa` / `U.S.A.` splits one
   bar into three.
6. **Names that match while values do not** — `Country_Code` holds `US`,
   `country` holds `United States`; the join silently returns almost nothing.
7. Duplicates (including case/whitespace-only ones that survive
   `drop_duplicates()`), mojibake, constant columns, missing keys, outliers.

## Layout

```
config/          pipeline + validation config (Phase 1.2)
data/raw/        <- put the demo data here (gitignored)
data/_synthetic/ generated fixture with known defects
data/versions/   Parquet snapshots of clean output (Phase 1.3)
docs/            Phase 0 decision records + templates
reports/         generated audit output
src/dtp/         io_utils, profile, schema_map, risks, cli
scripts/         make_synthetic_messy.py
tests/           68 tests; every fixture defect has one asserting it is caught
```

## Tests

```bash
python -m pytest
```

The suite generates the fixture, runs the full audit over it, and asserts each
injected defect is still detected — plus a set of tests asserting the profiler
stays *quiet* where it should (no IQR outlier findings on a 5-row column, no
"looks like an identifier" on a numeric column).

## Environment notes

Python 3.12 with **pandas 3.x** — Copy-on-Write is default and the default string
dtype is Arrow-backed. Code written against pandas 2.x idioms will misbehave.
See [`docs/00-tech-stack.md`](docs/00-tech-stack.md) for the full stack decision
and what is still open.

## Status against the roadmap

| Phase | State |
|---|---|
| 0 — Discovery | tech stack decided; success metrics proposed pending sign-off; **audience/decisions and source inventory blocked on stakeholder input** |
| 1.1 — Audit & assessment | done, tested |
| 1.2 — Cleaning & standardization | not started; the transformation rules are per-column, so this wants the real data |
| 1.3 — Pipeline & docs | not started |
| 2-4 | not started |
