"""Business lead data model (Phase 8.1), identity extraction (8.2), aggregation (8.3), identity (9.2) and exact de-duplication (9.3), multi-page aggregation with conflicts and sources (9.4) JSON export (10.1) CSV export (10.2) filtering (10.3) and sorting/pagination (10.4) the query/export pipeline (10.5) the listing rows/text and output controls (11.1, 11.4) and the export interface (11.3), all pure data; the Phase 8.5
`LeadCollector` (pipeline.py) is the crawler hook that creates and stores the lead through `LeadRepository`."""

from app.leads.aggregate import PageSource, aggregate_leads, aggregate_parsed_pages
from app.leads.dedupe import deduplicate_leads, merge_leads
from app.leads.errors import LeadDataError
from app.leads.export import export_stored_leads, lead_to_json, leads_to_json, write_leads_json
from app.leads.export_csv import export_stored_leads_csv, leads_to_csv, write_leads_csv
from app.leads.export_command import EXPORT_FORMATS, LeadExportError, export_format, render_export, select_lead_page, select_leads, validate_export, write_export
from app.leads.filters import LeadFilter, LeadFilterError, filter_leads, filter_stored_leads
from app.leads.identity import BusinessIdentity, NameCandidate, extract_identity
from app.leads.lead_identity import LeadIdentity, lead_identity
from app.leads.listing import LeadListing, LeadRow, format_lead_listing, list_leads_page, list_stored_leads
from app.leads.metadata import WebsiteMetadata, merge_metadata
from app.leads.model import BusinessLead
from app.leads.multipage import AggregatedLead, LeadConflict, aggregate_pages, lead_conflicts
from app.leads.paging import LeadPagingError, Page, paginate, sort_and_paginate, sort_and_paginate_stored, sort_leads
from app.leads.query import (
    LeadQuery, QueryResult, export_query_csv, export_query_json, query_leads, query_stored_leads, query_to_csv, query_to_json,
    write_query_csv, write_query_json,
)
from app.leads.pipeline import LeadCollector

__all__ = [
    "AggregatedLead", "BusinessIdentity", "BusinessLead", "LeadCollector", "LeadConflict", "LeadDataError", "LeadExportError", "LeadFilter", "LeadFilterError", "LeadIdentity", "LeadListing", "LeadPagingError", "LeadQuery", "LeadRow", "NameCandidate", "Page", "PageSource", "QueryResult", "WebsiteMetadata", "merge_metadata",
    "EXPORT_FORMATS", "aggregate_leads", "aggregate_pages", "aggregate_parsed_pages", "deduplicate_leads", "export_format", "export_query_csv", "export_query_json", "export_stored_leads", "export_stored_leads_csv", "extract_identity", "filter_leads", "filter_stored_leads", "format_lead_listing", "lead_conflicts", "lead_identity", "list_leads_page", "list_stored_leads", "lead_to_json", "leads_to_csv", "leads_to_json", "merge_leads", "paginate", "render_export", "query_leads", "query_stored_leads", "query_to_csv", "query_to_json", "select_lead_page", "select_leads", "sort_and_paginate", "sort_and_paginate_stored", "sort_leads", "validate_export", "write_export", "write_leads_csv", "write_leads_json", "write_query_csv", "write_query_json",
]
