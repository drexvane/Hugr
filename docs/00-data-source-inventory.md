# Data Source Inventory (Phase 0)

The technical half of this table is generated — run the audit and it fills
itself from the files present:

```
python -m dtp.cli audit --raw data/raw
```

That writes `reports/profiling/profiling_report.md`, which already records each
source's format, encoding, delimiter, shape and per-column types. **What it
cannot know is the human half: who owns the data and how often it changes.**
Those two columns are the reason this file exists.

## Sources

| File / table | Format | Rows x cols | Owner | Refresh cadence | System of record? | Notes |
|---|---|---|---|---|---|---|
| _(awaiting demo data)_ | | | **TBD** | **TBD** | **TBD** | |

Fill one row per file. For a workbook, one row per sheet — sheets routinely
disagree with each other.

## Questions to put to each source owner

1. **Day/month order.** For any date column written as `03/04/2024`: is that
   3 April or 4 March? The profiler flags when the data cannot answer this
   itself, and no amount of code can guess it. Getting it wrong makes every
   trend line quietly incorrect.
2. **Currency.** If amounts carry more than one symbol, which is the reporting
   currency, and is there an agreed FX rate and date to convert on?
3. **Percentages.** Is `0.1` ten percent or one tenth of a percent?
4. **Nulls.** Do `"N/A"`, `"-"`, `"unknown"` and `"TBD"` mean the same thing, or
   do they encode different situations worth preserving?
5. **Keys.** What uniquely identifies a row? If nothing does, may we mint a
   surrogate key, and is the current duplication expected?
6. **Codes vs names.** Where one source holds `US` and another `United States`,
   which is canonical, and does a crosswalk table already exist?
7. **Refresh.** Is a new extract a full replacement or an increment? Can rows be
   restated after the fact?
8. **Sensitivity.** Does any column carry personal data that must not reach the
   dashboard or the AI agent?

## Access

| Source | How we get it | Credential needed | Confirmed working |
|---|---|---|---|
| _(awaiting demo data)_ | | | |

Roadmap risk note: *"Data source access/permissions should be confirmed early to
avoid mid-project blockers."* This table is where that confirmation is recorded.
