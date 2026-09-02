# Pipeline run

**PIPELINE OK - published snapshot 20260903T005740**

- clean [ok, 18.2s]: 2 table(s), 647,247 rows, 0 rejected cell(s), 1 unconfigured file(s)
- validate [ok, 2.7s]: PASS - every rule held
- snapshot [ok, 1.9s]: 20260903T005740 - 2 table(s), 647,247 rows (identical to 20260903T005455)
- dictionary [ok, 0.9s]: 53 field(s), 100.0% documented
- monitor [ok, 0.0s]: OK - nothing to report

## Alerts

- [INFO] access_logs: unchanged since 20260903T005455 - content hash identical
- [INFO] order_items: unchanged since 20260903T005455 - content hash identical
- [INFO] Source skipped by config: DescriptionDataCoSupplyChain.csv - The vendor's own field glossary (52 of the 53 fact columns). Not a table: it is consumed by dtp.dictionary as the business description of each field.

