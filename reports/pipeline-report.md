# Pipeline run

**PIPELINE OK - published snapshot 20260906T000311**

Source: `dataset`

- clean [ok, 12.8s]: 2 table(s), 647,247 rows, 0 rejected cell(s)
- validate [ok, 1.9s]: PASS - every rule held
- snapshot [ok, 1.3s]: 20260906T000311 - 2 table(s), 647,247 rows (identical to 20260903T224815)
- dictionary [ok, 0.6s]: 53 field(s), 100.0% documented
- monitor [ok, 0.0s]: OK - nothing to report

## Alerts

- [INFO] access_logs: unchanged since 20260903T224815 - content hash identical
- [INFO] order_items: unchanged since 20260903T224815 - content hash identical
- [INFO] Source skipped by config: DescriptionDataCoSupplyChain.csv - The vendor's own field glossary (52 of the 53 fact columns). Not a table: it is consumed by dtp.dictionary as the business description of each field.

