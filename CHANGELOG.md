# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Streaming reader for XAF and related audit files, ported from auditfile-dotnet with the same API in Python style:
  `Auditfile.read`, `validate`, `scan` and `open`, `AuditfileSource`, `AuditfileVersion`, `AuditfileSchema` with JSON round-trip,
  the `Sink` protocol and `MemorySink`, views (`ViewDefinition.run`, `MemorySink.run`).
- Version resolution (forced, registry, alias, resolver), scan and load phases, split audit files, dialect corrections,
  key and reference checks, XAF business rules, diagnostics in Dutch and English.
- Streaming XSD validator for the XSD subset of auditfile-spec, including identity constraints.
- No dependencies; exact `Decimal` amounts.
- Security: DTDs refused, embedded-only XSD resolution, depth and diagnostic limits, caller-owned streams left open.
