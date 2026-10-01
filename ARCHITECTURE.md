# Project Architecture

## Purpose

This project collects websites, extracts business/lead information, stores leads, and provides filtering/export functionality.

The system is organized into these main layers:

1. Crawling
2. HTML/Page Parsing
3. Lead Extraction
4. Lead Aggregation
5. Deduplication
6. Persistence
7. Enrichment
8. Filtering/Querying
9. Export
10. User Interface

## Core Flow

Website/URL
→ HTTP Crawler
→ HTML/Page Parser
→ Extracted Page Data
→ Lead Extraction
→ Lead Aggregation
→ Deduplication
→ Persistence
→ Enrichment
→ Filtering
→ Export/UI

## Existing Components

### Crawler

Responsible for:

- HTTP requests
- URL normalization
- link discovery
- crawl limits
- visited-page tracking
- crawl persistence
- robots/sitemap handling

Do not replace the existing crawler.

### Page Parser

Responsible for:

- HTML parsing
- title
- visible text
- headings
- links
- metadata
- emails
- phones
- social links
- JSON-LD
- address/business information
- page classification
- content quality information

Reuse the existing parser.

Do not create a second HTML parsing system.

### Lead Layer

Responsible for:

- business identity
- contact information
- website information
- aggregated data from multiple pages
- lead creation
- lead persistence

### Deduplication

Responsible for:

- canonical website identity
- lead identity
- duplicate prevention
- multi-page aggregation

Existing deduplication behavior must remain unchanged.

### Enrichment

Responsible for adding additional information to existing leads.

Enrichment must:

- reuse existing parsed data when possible
- avoid unnecessary crawling
- preserve existing lead data
- safely handle missing values
- avoid overwriting valid data with empty values

### Persistence

Responsible for storing:

- crawled pages
- leads
- enrichment data
- crawl state

Use the existing persistence layer.

Do not introduce a second database/storage mechanism.

## Design Rules

- Preserve existing behavior.
- Prefer reuse over duplication.
- Keep changes isolated to the current phase.
- Do not refactor unrelated code.
- Keep data models backward compatible where possible.
- Optional enrichment data must never break lead creation.
- Missing data must be handled safely.
- URL normalization must use existing utilities.
- Extraction should be deterministic.
- Tests should cover new behavior.

## Phase 12.2 Focus

Phase 12.2 adds contact-data enrichment to existing leads.

Expected scope:

- email enrichment
- phone enrichment
- contact-data normalization
- integration with existing lead data

Do NOT implement unrelated enrichment, UI, external APIs, SEO scoring, or new crawling architecture.