# Project Map

## Important Rule

This file is a navigation guide.

Do NOT scan the entire repository.

For Phase 12.2, inspect only files directly related to:

- lead model
- lead aggregation
- lead persistence
- email extraction
- phone extraction
- page parsing
- enrichment

## Main Areas

### Crawler

Contains HTTP crawling and page discovery.

Relevant only if Phase 12.2 requires obtaining additional pages for contact enrichment.

Do not modify crawler behavior unless strictly necessary.

### Parser

Contains HTML parsing and extracted page information.

Look here for existing:

- email extraction
- phone extraction
- parsed page models
- normalized page data

Reuse existing extraction logic.

### Lead

Contains:

- lead/business models
- lead creation
- lead aggregation
- lead identity

Phase 12.2 should extend existing lead data rather than create a parallel lead model.

### Deduplication

Contains:

- canonical website handling
- lead identity
- duplicate prevention

Do not modify unless required for contact-data integration.

### Persistence

Contains database/storage logic for leads and related entities.

Reuse the existing persistence mechanism.

### Enrichment

Contains or should contain functionality for adding additional information to existing leads.

Phase 12.2 belongs primarily here.

## Phase 12.2 Relevant Data Flow

Existing Website/Page
→ Existing HTML Parser
→ Existing Email/Phone Extraction
→ Contact Data Normalization
→ Lead Enrichment
→ Existing Lead Aggregation
→ Existing Persistence

## Phase 12.2 Scope

Implement only:

- email enrichment
- phone enrichment
- normalization
- merging into existing leads
- persistence of enriched contact data
- focused tests

Do NOT implement:

- social media enrichment
- address enrichment
- JSON-LD enrichment
- external APIs
- SEO scoring
- UI
- new crawler architecture
- unrelated refactoring

## Context Efficiency

For Phase 12.2:

1. Read this file.
2. Read ARCHITECTURE.md.
3. Locate the existing lead model.
4. Locate existing email/phone extraction.
5. Locate lead aggregation.
6. Locate lead persistence.
7. Modify only directly relevant files.

Do not read unrelated modules.

Do not rewrite existing files unnecessarily.

Keep the implementation small and focused.