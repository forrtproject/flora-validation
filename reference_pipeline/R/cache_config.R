# Cache file paths (by data type, not purpose)
# Sourced by helper scripts to ensure consistent cache locations

library(here)

# Base cache directory (project-relative)
CACHE_DIR <- here("cache")

# CrossRef/DataCite DOI metadata cache
CROSSREF_DOI_CACHE <- file.path(CACHE_DIR, "crossref_doi_cache.rds")

# CrossRef citations cache (APA/BibTeX references)
CROSSREF_CITATIONS_CACHE <- file.path(CACHE_DIR, "crossref_citations.rds")

CROSSREF_REF_FIELDS_CACHE <- file.path(CACHE_DIR, "crossref_fields.rds")

# CrossRef author lists cache
CROSSREF_AUTHORS_CACHE <- file.path(CACHE_DIR, "crossref_authors.xlsx")

# Author overlap computation results
AUTHOR_OVERLAP_CACHE <- file.path(CACHE_DIR, "author_overlap.xlsx")

# Manual reference overrides (highest priority in 3-tier lookup)
MANUAL_REFERENCES <- file.path(CACHE_DIR, "manual_references.xlsx")

# Swapped manual references (keys normalized via URLR->DOIR map)
MANUAL_REFERENCES_SWAPPED_CACHE <- file.path(CACHE_DIR, "manual_references_swapped.rds")

# URL-R to DOI-R mapping cache (from FLoRA pipeline)
URLR_DOIR_MAP_CACHE <- file.path(CACHE_DIR, "urlr_doir_map.rds")

# OpenAlex keywords cache
OPENALEX_CACHE <- file.path(CACHE_DIR, "openalex_keywords_language.csv")

# OpenAlex abstracts cache
OPENALEX_ABSTRACTS_CACHE <- file.path(CACHE_DIR, "openalex_abstracts.csv")

# OpenAlex work-id → bibliographic fields cache (keyed by work ID, e.g. W123)
OPENALEX_WORK_FIELDS_CACHE <- file.path(CACHE_DIR, "openalex_work_fields.csv")

# LLM replication intent classification cache
LLM_REPLICATION_INTENT_CACHE <- file.path(CACHE_DIR, "llm_replication_intent.rds")

# Unpaywall open access URL cache
UNPAYWALL_CACHE <- file.path(CACHE_DIR, "unpaywall_oa.csv")

# Validation report from last run (for incremental reporting)
VALIDATION_REPORT_CACHE <- here("output", "validation_report.rds")

# FLoRA validation: DOI resolution cache (successful results cached 30 days)
VALIDATION_DOI_RESOLUTION_CACHE <- file.path(CACHE_DIR, "validation_doi_resolution.rds")

# FLoRA validation: URL resolution cache (successful results cached 30 days)
VALIDATION_URL_RESOLUTION_CACHE <- file.path(CACHE_DIR, "validation_url_resolution.rds")
