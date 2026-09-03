# Pipeline run

**PIPELINE OK - published snapshot 20260903T224815**

- clean [ok, 30.4s]: 2 table(s), 647,247 rows, 0 rejected cell(s)
- validate [ok, 2.6s]: PASS - every rule held
- snapshot [ok, 13.5s]: 20260903T224815 - 2 table(s), 647,247 rows (identical to 20260903T012120)
- dictionary [ok, 1.2s]: 53 field(s), 100.0% documented
- monitor [ok, 0.2s]: OK - nothing to report

## Alerts

- [INFO] access_logs: unchanged since 20260903T012120 - content hash identical
- [INFO] order_items: unchanged since 20260903T012120 - content hash identical
- [INFO] Source skipped by config: DescriptionDataCoSupplyChain.csv - The vendor's own field glossary (52 of the 53 fact columns). Not a table: it is consumed by dtp.dictionary as the business description of each field.

