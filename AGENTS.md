# Development guide

## Purpose and architecture
Collect authorized official Equibase charts conservatively. Python 3.12+; focused
modules under `src/equibase_scraper`: configuration, paced HTTP, link discovery,
PDF downloading, layout-aware parsing, Supabase database/storage, and CLI orchestration.

## Conventions
Use typed, small functions, PEP 8, and Pydantic boundary models. Preserve unknown
values as null with warnings. Never infer finishing order from post/point-of-call
columns. Keep network operations separate from parsing. Do not add speculative features.

## Security and operations
Scraping defaults to disabled. Never bypass access restrictions, weaken request
limits, log credentials, commit `.env`/PDFs/data dumps, or make the storage bucket
public. Dry runs may discover links only when explicitly enabled; no persistence.
No production runs, bulk collection, pushes, or merges without user approval.

## Verification and migrations
Run `python -m pytest` and `ruff check .` after changes. Tests use synthetic PDFs,
mock HTTP and mock Supabase; no live requests or credentials. Keep migrations
versioned and transactional. Preserve valid data after failed/incomplete parses;
replace related results atomically. Add migrations rather than changing an already
deployed schema in place. Update relevant tests, README and CHANGELOG with changes.

## Git
Use local `dev` for development and `main` for stable releases. Preserve user work.
Suggest conventional commits; do not commit, push or merge unless requested.
