# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Custom dialects and views at runtime, in the format of auditfile-spec: `Auditfile.dialects.add(version, json)` and
  `Auditfile.views.add(json)`, with `remove` and `clear` (`DialectRegistry`, `ViewRegistry`). They appear after the
  built-in ones in `AuditfileVersion.dialects`, `views` and `find_view`.
- Typed row accessors (`get_string`, `get_decimal`, `get_integer`, `get_date`, `get_datetime`, `get_time`, `get_boolean`) that check the column type, also with a column looked up once.
- Report counts per severity that include diagnostics left out of a full list; errors take the place of infos and warnings.
- Cancellation in the scan and load phases; XAF-S006 (missing required field) in unverified mode; XAF-X004 for a part of a
  split file in another encoding than the first part.
- Dialect actions, view aggregates and view operators are checked when the definitions are loaded.
- `DialectAction`, `ViewAggregate` and `ViewOperator` enums; `ReadOptions.cancel`, `ReadCancelledError`, `SinkAbortError`.
- pandas DataFrames, with the optional extra `pandas`: `MemorySink.to_pandas(table)` (index `_id`, column `_parent_id`)
  and `ViewResult.to_pandas()`; amounts stay exact `Decimal` unless `decimals="float"`.
- polars DataFrames, with the optional extra `polars`: `MemorySink.to_polars(table)` (columns `_id`, `_parent_id`) and
  `ViewResult.to_polars()`; amounts get an exact `Decimal` type unless `decimals="float"`.
- Views: joins with `alternatives` (`ViewJoin.alternatives`) and the `pseudonym` operation (`ViewOperator.PSEUDONYM`,
  `ViewValue.prefix`); the views `ledger` and `ledger_pseudonymized`.
- Streaming reader for XAF and related audit files, ported from auditfile-dotnet with the same API in Python style:
  `Auditfile.read`, `validate`, `scan` and `open`, `AuditfileSource`, `AuditfileVersion`, `AuditfileSchema` with JSON round-trip,
  the `Sink` protocol and `MemorySink`, views (`ViewDefinition.run`, `MemorySink.run`).
- Version resolution (forced, registry, alias, resolver), scan and load phases, split audit files, dialect corrections,
  key and reference checks, XAF business rules, diagnostics in Dutch and English.
- Streaming XSD validator for the XSD subset of auditfile-spec, including identity constraints.
- No dependencies; exact `Decimal` amounts.
- Security: DTDs refused, embedded-only XSD resolution, depth and diagnostic limits, caller-owned streams left open.
