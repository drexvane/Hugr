# Cleaning & Pipeline Decisions (Phase 1.2 / 1.3)

Status: **implemented and enforced.** Every decision below is either encoded in
`config/` where a reviewer can change it, or asserted by a test that fails if
someone quietly reverses it. Raise objections against the numbered reasons rather
than the conclusion.

Written against the real data: 180,519 order lines and 469,977 access-log rows in
[`dataset/`](../dataset), catalogued in
[`00-data-source-inventory.md`](00-data-source-inventory.md).

## Design decisions

| Decision | Choice | Why this, not the alternative |
|---|---|---|
| Where the rules live | YAML in `config/`, not Python | 1. Per-column policy is a business question — which values mean missing, why a column was dropped, how much row growth is normal. A stakeholder can review a YAML block; they will not review a transformation function. 2. The engine stays small because it only interprets; the judgement is all in data. |
| Expression evaluation | whitelisted AST walk | 3. Config has to express `order_status not in [...]` and `discount / sales`. `eval()` would do it in one line and would also execute anything a config author typed. The walk permits comparisons, boolean ops, arithmetic and column references, and refuses everything else. |
| A cell that will not coerce | null it, quarantine it, count it — keep the row | 4. Crashing on one bad cell in 180,519 rows makes the pipeline unusable. Silently coercing loses the evidence. The value goes to `<table>__rejects.parquet`, the count reaches the report and the alerts, and the row survives with a null. 5. The threshold is configurable (`max_rejects`), so "some rejects are normal" is a stated position rather than an accident. |
| Nulls in value checks | three-valued logic | 6. A `range`, `regex`, `allowed_values`, `identity` or `expression` rule treats a null operand as *not checked*, exactly like a SQL CHECK constraint. Counting nulls as violations would make `expression: qty >= 1` and `range: qty min 1` disagree about the same column, and would put null-handling in eleven places instead of one. `not_null` owns nulls. 7. The exemption is not allowed to be invisible: exempted rows are excluded from the checked count and named in the report. |
| Severity | `error` blocks, `warn` reports | 8. A validation layer where everything is fatal gets disabled within a week. One where nothing is fatal is decoration. |
| Known defects | `expect_violations: N`, failing **both** directions | 9. 7,754 cancelled orders is a fact about this extract, not a bug to re-discover every run. Pinning the count converts it from noise into a tripwire: if it becomes 7,800 *or* 7,700, something changed upstream and the run says so. 10. Every pinned count must carry a `reason`, enforced by a test — an unexplained pin is indistinguishable from a suppressed failure. |
| A rule that cannot run | escalated to `error` regardless of what it declared | 11. A check nobody is performing is worse than one that fails, because it reports nothing. `severity: warn` must not be able to hide a typo in a column name. |

| Publishing a snapshot | only if validation passed | 12. This is the load-bearing decision of Phase 1.3. A pipeline that publishes whatever it produced turns a bad extract into bad dashboards automatically. On failure `data/versions/` is untouched, the previous snapshot stays newest, and downstream keeps reading the last known-good data. |
| The escape hatch | `--force` publishes and **stamps the manifest** | 13. Sometimes you need the broken snapshot — to show someone, or to diff against. Refusing outright invites someone to bypass the tool. 14. So the forced snapshot records `validation.forced: true`, the failed rule ids, and a `PUBLISHED WITH --force` note *inside its own manifest*, and shows `validation=FAIL` in `dtp versions`. The fact is attached to the data, not to whoever's memory of typing the flag. 15. The run still exits 1: forcing changes what is published, never the verdict. |
| Snapshot identity | content hash of the frame, not the file | 16. Parquet does not compress deterministically — two writes of identical data give different bytes. File hashing would report drift that does not exist, and after two false alarms nobody reads the alerts. `pd.util.hash_pandas_object` is sensitive to a changed value, a renamed column, a dtype change and row order, and stable across a round-trip. |
| Alert delivery | files, stdout, exit code — **no network sink** | 17. That is what CI and cron actually read. 18. Sending the contents of a dataset to Slack, email or a webhook is a decision for whoever operates this, not a default someone inherits. A test fails if `requests`, `urllib`, `http.client`, `smtplib` or `socket` ever appears in `monitoring.py`. |
| Exit code | tracks *critical* alerts only | 19. Warnings and info must not turn a good run red, or the exit code stops meaning anything. |
| Monitoring on a failed run | runs anyway — publishing stops at the gate, reporting does not | 20. Monitoring reports; it does not produce, so there is nothing unsafe about it running after a failure. Skipping it would leave `reports/alerts.md` holding the last *successful* run's "nothing to report" at exactly the moment there was something to report — the failure would live in the exit code and be absent from the file the alerting actually reads. 21. An explicit `--stop-after` is the one exception: a deliberate early exit is not a failure, so it alerts nowhere. |

## Data decisions, and what was rejected

| Decision | Rejected alternative |
|---|---|
| **Flag** the 7,754 cancelled / suspected-fraud rows with `is_revenue_recognised` rather than dropping them | Dropping them would silently change every count, and they are legitimate rows: a cancellation is a fact worth analysing. The flag makes the filter explicit at the point of use. Same reasoning for `is_shipment_valid` over the shipping counters those rows still carry. |
| **Drop and recompute** `Order Item Discount Rate` and `Order Item Profit Ratio` | Trusting them: they are the primitives' quotient rounded to 2dp, off by up to 41.6% relatively — $9.30 on one line item. Keeping *and* recomputing would leave two columns that disagree and no guidance on which to use. The recomputation is itself proven by an identity rule against the primitives. |
| **Text** for zipcodes, zero-padded to 5 | Integer, which is how the source stored them and how they lost their leading zeros in the first place. Leaving them short was also rejected: the pad is injective here (995 distinct values before and after, 609 for order zipcode), so it reconstructs the original and cannot collide. |
| **Rename** `Order Profit Per Order` → `order_item_profit` | Keeping the source name, which invites order-level treatment. It is measured per line: none of the 45,902 multi-item orders share one value. Someone de-duplicating by `order_id` on the strength of the name loses $2.58M. |
| **De-duplicate** the access log (3,249 exact rows), but not the fact table | The fact table has a unique key and no duplicates. The log has no key at all, and two views of one product in the same minute from one IP are indistinguishable from one double-logged event — so they are dropped, counted, and the count is reported so the judgement stays visible. `access_log_id` is minted afterwards as a surrogate. |
| **Casefold inside the referential rules** rather than rewriting the log's case | Rewriting would overwrite what the source system actually emitted. See the accepted consequence below — this one is not fully settled. |
| **Suppress value examples** for name, street, IP and URL columns in the dictionary | Printing them. The dictionary is the document most likely to be shared widely; it should not be the thing that leaks 3,340 client IPs. |

## Documented, not fixed

Two things I chose to leave alone and write down, because changing them would mean
inventing data rather than cleaning it.

**1. Money is rounded to cents, and it moves the grand total by $0.70.** Clean
`SUM(order_item_sales)` is 36,784,734.31 against 36,784,735.01 summed from the raw
strings — a relative difference of 1.9 parts per hundred million.

The $0.70 is float32 noise being removed, not precision being lost. The raw file
carries up to **9 decimal places** on a money column, and the extra digits are
artefacts, not fractions of a cent: `Sales` contains `119.9800034` and
`79.98000336`, which are $119.98 and $79.98 as written by a float32 pipeline
upstream. 155,013 of 180,519 `Sales` values have more than two decimal places, as
do 156,939 `Order Item Total` and 66,552 `Order Item Discount` values. Summed
exactly, the raw column comes to 36,784,735.0133798…, and that tail is meaningless.

So rounding to cents recovers the intended value rather than discarding a real
one, and it makes every aggregate reproducible run to run. The alternative,
carrying `Decimal`, converts at the same speed but has no native pandas dtype: it
would mean an object-typed column, no vectorised arithmetic, no clean Parquet
type, and a comparison story that breaks at every downstream boundary — all to
preserve digits the source never meant.

It is written down because someone will eventually reconcile a total against the
raw CSV and find $0.70 missing. They should find this paragraph first. The
residual tolerances in `config/validation_rules.yml` are measured against the same
noise floor: `total == sales - discount` needs a full cent, and the reason field
says so.

**2. The source taxonomy is odd, and left as it is.** 'Cleats' sits in Apparel,
'Cardio Equipment' in Footwear, 'Camping & Hiking' in Fan Shop, "Women's Apparel"
in Golf.

These are the vendor's own department assignments, they are internally consistent —
every row with a given `category_id` gets the same department — and no external
truth says what the right parent is. Re-parenting them would be inventing a
taxonomy and calling it cleaning, and any dashboard built on the invented version
would disagree with the source system forever.

What *was* fixed is the genuinely ambiguous case: `category_id` 13 and 37 both
carried the label 'Electronics' under two different departments, so one label
meant two things. Those became `Electronics (Footwear)` and
`Electronics (Outdoors)` — disambiguating a name, not moving a category. A
stakeholder who wants an analytical hierarchy that differs from the source should
get a mapping table in Phase 2, kept separate from the cleaned source.

## Consequences accepted

- **Joining the two tables requires casefolding.** The access log is lowercase
  where the fact table is Title Case, so a literal join on `department_name`,
  `category_name` or `product_name` returns **zero rows**. The referential rules
  fold case and prove the join is sound (all 6 departments, 30 of 33 categories,
  72 of 76 products), but the clean data does not make it obvious. **Revisit at
  Phase 2 kickoff:** the dashboard needs this join, and the right fix is probably
  a normalised join key added alongside the original values rather than in place
  of them. Left open deliberately rather than guessed at now.
- **The two tables cover different windows.** Orders span 2015-01-01 →
  2018-01-31; the log only 2017-09-01 → 2018-01-31. Any funnel or
  views-to-orders metric is defined only over those five months, and 46 of the
  118 catalogue products were never viewed at all. This is a real finding for
  Phase 2, not a defect to fix.
- **No orchestrator.** The pipeline is one process, end to end, exiting non-zero
  on failure. A cron entry calling the same CLI is sufficient scheduling at this
  size.
- **Thresholds are calibrated to a static export.** `row_shrink_pct: 0` means any
  row loss is critical, which is right for a full-replacement extract and wrong
  for an incremental one. The refresh model is still an open question with the
  source owner; when it is answered, `config/monitoring.yml` is the one file that
  changes.

## How each decision stays true

252 tests, none of which read the real dataset — they run against a seven-row
fixture with injected defects, or hand-built frames. The ones that guard the
decisions above: the snapshot gate refusing to publish and leaving the previous
snapshot newest; `--force` stamping the manifest while the run still fails; a
refused run still writing `reports/alerts.md`; the unrunnable rule being escalated
past `warn`; `expression` and `range` agreeing about nulls on the same column;
content hashes surviving a Parquet round-trip; `monitoring.py` containing no
network client; every pinned count in the shipped config carrying a reason; and a
vendor description that merely restates a column name counting as *undocumented*.
