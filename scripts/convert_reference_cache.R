# One-time bridge for the supplied R caches; the website does not require R.
# Usage: Rscript scripts/convert_reference_cache.R CACHE_DIRECTORY OUTPUT_JSON
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2L)
if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("jsonlite is required to convert the original R cache")
}
read_optional <- function(name, default) {
  path <- file.path(args[[1]], name)
  if (file.exists(path)) readRDS(path) else default
}
fields <- read_optional("crossref_fields.rds", data.frame())
citations <- read_optional("crossref_citations.rds", list())
mapping <- read_optional("urlr_doir_map.rds", data.frame())
jsonlite::write_json(list(fields = fields, citations = citations, url_to_doi = mapping),
                     args[[2]], auto_unbox = TRUE, na = "null", null = "null",
                     dataframe = "rows", pretty = FALSE)
cat("Converted", nrow(fields), "reference fields and", nrow(mapping), "URL mappings\n")
