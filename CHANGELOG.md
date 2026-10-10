# Changelog

## Version 0.1.0 - Initial development

- Initial Python 3.12+ project configuration, opt-in settings, and JSON logging.
- Link-based recent-card discovery, desktop race metadata, and published mobile PDF targets.
- Shared paced HTTP budget with bounded retries and access-challenge stopping.
- Verified PDF downloads, conditional revalidation, SHA-256 versions, and atomic local writes.
- Layout-aware primary results parsing, separate finish baselines, program-keyed trainers,
  explicit scratches/dead heats, raw text, timing, and incomplete-parse protection.
- Transactional PostgreSQL schema/RPCs, private hash-addressed Supabase uploads,
  safe failure tracking, and mocked recovery/idempotency checks.
- Sequential CLI pipeline, discovery-only dry runs, offline parsing, conservative caps,
  repeated-run skip behavior, truthful summaries, and nonzero failure exits.
- Disabled-by-default scheduled workflow (6:17/18:17 America/New_York), safe manual
  inputs, collection concurrency, and credential-free testing workflows.
- Limited live validation on 2026-10-10: Churchill Downs 2026-09-27 race 1 PDF,
  nine horse results checked against source layout, and discovery-only CLI run.
- Fixed HTTP compressed-response reconstruction; added regression coverage.
- SQL/PLpgSQL syntax, synthetic/mocked tests, Ruff, and workflow actionlint checks.

### Pending validation

- Live private Storage upload, PostgreSQL RPC execution, duplicate/rollback verification
  against the separately prepared Supabase schema.
- Representative additional tracks/PDF layouts and hosted GitHub Actions execution.
- Multi-race card PDFs and ambiguous disqualification narratives are not supported for
  structured persistence. Unattended production collection has not been enabled.

## Version 1.0.0 - First production-ready release

Not released. Requires verified official-chart parsing and live Supabase persistence,
as well as completion of the Version 1 acceptance criteria.
