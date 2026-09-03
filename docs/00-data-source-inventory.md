# Data Source Inventory (Phase 0)

The technical half of this table is generated — run the audit and it fills itself
from the files present:

```bash
python -m dtp.cli audit --raw dataset
```

That writes `reports/profiling/profiling_report.md`, which records each source's
format, encoding, delimiter, shape and per-column types. **What it cannot know is
the human half: who owns the data and how often it changes.** Those two columns
are the reason this file exists, and they are the two still open.

## Sources

The DataCo Smart Supply Chain export, as received in [`dataset/`](../dataset).

| File / table | Format | Rows × cols | Owner | Refresh cadence | System of record? | Notes |
|---|---|---|---|---|---|---|
| `DataCoSupplyChainDataset.csv` → `order_items` | CSV, cp1252, comma, 95.9 MB | 180,519 × 53 → 46 | **TBD** | **TBD** — treated as a static export | **Assumed yes** for orders, customers and products | One row per order line, not per order: 45,902 orders hold more than one. Covers 2015-01-01 → 2018-01-31. |
| `tokenized_access_logs.csv` → `access_logs` | CSV, cp1252, comma, 95.4 MB | 469,977 × 8 → 466,728 × 7 | **TBD** | **TBD** — treated as a static export | **Assumed yes** for page views | Covers 2017-09-01 → 2018-01-31 only — the last 5 months of the order window. No natural key; `access_log_id` is minted after de-duplication. |
| `DescriptionDataCoSupplyChain.csv` | CSV, cp1252, comma, 3 KB | 52 × 2 | **TBD** — vendor-supplied | static | n/a — it is documentation, not data | Field glossary. Read by `dtp dict`, never cleaned or published. Every entry maps to a real column, but 5 of the 52 describe nothing: they restate the column name back at you. |

Encoding is **cp1252, not UTF-8**, in all three files, and is pinned in
`config/cleaning_rules.yml` rather than detected per run — a detector that guesses
right today can guess differently on next month's extract.

`46` and `7` are the clean column counts: 12 source columns dropped from the fact
table and 5 derived, 2 dropped from the logs and 1 derived. Every drop and every
derivation carries a reason in `reports/cleaning-report.md`.

### Still open, and who it needs

| Question | Why it matters | Blocked on |
|---|---|---|
| Who owns each extract? | Nobody to ask when a load looks wrong | stakeholder |
| Full replacement or increment? | Decides whether `row_shrink_pct: 0` is the right alarm | stakeholder |
| Can rows be restated after the fact? | Decides whether a snapshot may ever be overwritten | stakeholder |
| Is this a live system or a one-off export? | Everything about refresh cadence | stakeholder |

Until those are answered the pipeline assumes **full replacement of a static
export**: any row loss is critical, and no snapshot is ever overwritten.

## The questions, answered by the data

The Phase 0 list below was written for source owners. Most of it the data ended up
answering itself, so the evidence is recorded here and only the genuinely human
questions are left outstanding. Each answer is enforced by a rule in
`config/validation_rules.yml`, so it stays true rather than being true once.

1. **Day/month order** — *resolved, and the data settles it.* Both date columns
   are `M/D/YYYY H:MM`: field one never exceeds 12, field two reaches 31 and
   exceeds 12 on 109,416 rows, and every one of the 12 months contains at least
   one day above 12. No other reading parses, and no month is left ambiguous.
   Nothing was guessed.
2. **Currency** — *resolved, single currency.* No symbol, comma or letter appears
   anywhere in the five money columns; all amounts are bare decimal. Two
   arithmetic identities hold across all 180,519 rows —
   `sales == price × quantity` exactly, and `total == sales - discount` to within
   one cent — which a column mixing currencies could not do. Amounts are assumed
   **USD**, and that assumption is the one item here still worth a stakeholder's
   confirmation. Both identities are enforced as `error` rules, so a future
   extract that mixes currencies fails the run instead of quietly summing.
3. **Percentages** — *resolved, fractions not 0–100.* `order_item_discount_rate`
   runs 0 → 0.2504. `order_item_profit_ratio` runs −2.7508 → 0.5006: signed,
   because a line item can lose money, so a bare `AVG` over it is meaningful only
   alongside the sign. Both were dropped and recomputed because the stored values
   were rounded to 2dp; the recomputation is itself checked by an identity rule.
4. **Nulls** — *resolved.* Missingness is encoded as blank only: no `N/A`, `-`,
   `unknown`, `TBD`, `null` or `?` variant appears in any of the 53 columns.
   336,209 blanks were recognised as missing. Two near-empty columns mean
   different things: `Product Description` is 100% blank and was dropped,
   `Order Zipcode` is 86.2% blank because it is US-only, and is deliberately left
   null elsewhere rather than imputed.
5. **Keys** — *resolved.* `order_item_id` is unique across all 180,519 rows. The
   access log has **no** unique column or combination even before
   de-duplication — 3,249 rows are byte-identical — so those were dropped and
   counted, and `access_log_id` is minted as a surrogate. That decision is
   recorded rather than assumed: two views of one product in the same minute from
   the same IP are indistinguishable from one double-logged event in this file.
6. **Codes vs names** — *resolved, and it is a trap.* The two tables share
   `department_name`, `category_name` and `product_name`, and **a literal join on
   any of them returns zero rows**: the access log is lowercase where the fact
   table is Title Case, and every log department carried a trailing space.
   Casefolded, all 6 log departments match, 30 of 33 categories match and 72 of
   76 products match. The remaining mismatches are pinned with
   `expect_violations` and explained: 2 categories have traffic and no sales (a
   funnel finding), and `electronics` cannot match because the fact table splits
   it into two department-qualified names — join that one on `category_id`.
7. **Refresh** — **open.** See the table above.
8. **Sensitivity** — *resolved, and acted on.* `Customer Password` and
   `Customer Email` are constant `'XXXXXXXXX'`, pre-masked at source; both are
   dropped, and the credential column would be dropped regardless of its
   contents. What remains is real personal data: customer names, street
   addresses, and 466,728 log rows carrying 3,340 distinct client IPs. These are
   carried in the clean table because the analysis needs geography, but the data
   dictionary suppresses value examples for name, street, IP and URL columns, so
   the document meant to be read widely does not itself become a personal-data
   leak. **Whether IPs and street addresses may reach the dashboard and the AI
   agent in Phases 2–3 is a stakeholder decision, not a technical one** — it is
   the one sensitivity question still open. Note that 3,340 distinct IPs across
   five months is few enough that an IP is close to a person here.

## Access

| Source | How we get it | Credential needed | Confirmed working |
|---|---|---|---|
| All three files | Committed to the repo under `dataset/` | none | ✅ read end to end by `dtp pipeline --raw dataset`, 647,247 rows out |
| Future extracts | **TBD** — no delivery mechanism agreed | **TBD** | ❌ |

Roadmap risk note: *"Data source access/permissions should be confirmed early to
avoid mid-project blockers."* Access to the data in hand is confirmed. Access to
the *next* extract is not, and that is the live version of this risk: nothing in
Phase 1 depends on it, and Phase 2 onward does.
