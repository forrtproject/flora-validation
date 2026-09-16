# Filter flora.csv for the DynamoDB-backed API
#
# Applies 4 filters in order:
#   1. DOI/handle.net validity
#   2. Boyce exclusion (multi-study paper)
#   3. Retraction Watch check
#   4. OSF registration check
#
# Outputs:
#   output/flora_filtered.csv  - filtered dataset
#   output/flora_filter_log.csv - log of removed rows with reasons

library(readr)
library(dplyr)
library(stringr)
library(httr)
library(jsonlite)
library(here)

is_doi <- function(x) grepl("^10\\.[0-9]{4,}/\\S+$", x)

input_file <- here("output", "flora.csv")
output_file <- here("output", "flora_filtered.csv")
log_file <- here("output", "flora_filter_log.csv")

if (!file.exists(input_file)) {
  stop("flora.csv not found at: ", input_file)
}

flora <- read_csv(input_file, show_col_types = FALSE)
n_start <- nrow(flora)
message("Read ", n_start, " rows from flora.csv")

# Accumulate removal log entries
removal_log <- tibble(
  doi_o = character(),
  doi_r = character(),
  reason = character(),
  detail = character()
)

# ============================================================================
# Filter 1: DOI/handle.net validity
# ============================================================================
valid_id <- is_doi(flora$doi_r) | str_detect(flora$doi_r, fixed("handle.net/"))
valid_id[is.na(flora$doi_r)] <- FALSE

removed_f1 <- flora[!valid_id, ]
if (nrow(removed_f1) > 0) {
  removal_log <- bind_rows(removal_log, tibble(
    doi_o = removed_f1$doi_o,
    doi_r = removed_f1$doi_r,
    reason = "invalid_identifier",
    detail = "doi_r is not a valid DOI and does not contain handle.net/"
  ))
}

flora <- flora[valid_id, ]
message("Filter 1 (DOI/handle.net validity): removed ", nrow(removed_f1),
        " rows, ", nrow(flora), " remaining")

# ============================================================================
# Filter 2: Boyce exclusion
# ============================================================================
boyce_doi <- "10.1098/rsos.231240"
is_boyce <- tolower(flora$doi_r) == boyce_doi

removed_f2 <- flora[is_boyce, ]
if (nrow(removed_f2) > 0) {
  removal_log <- bind_rows(removal_log, tibble(
    doi_o = removed_f2$doi_o,
    doi_r = removed_f2$doi_r,
    reason = "boyce_exclusion",
    detail = "Multi-study paper: Eleven years of student replication projects"
  ))
}

flora <- flora[!is_boyce, ]
message("Filter 2 (Boyce exclusion): removed ", nrow(removed_f2),
        " rows, ", nrow(flora), " remaining")

# ============================================================================
# Filter 3: Retraction Watch check
# ============================================================================
message("Downloading Retraction Watch database from Crossref...")
rw_response <- tryCatch(
  GET("https://api.labs.crossref.org/data/retractionwatch",
      timeout(120),
      user_agent("R (FReD filter pipeline)")),
  error = function(e) {
    stop("Failed to download Retraction Watch database: ", e$message)
  }
)

if (http_error(rw_response)) {
  stop("Retraction Watch download failed with status: ",
       status_code(rw_response))
}

rw_data <- tryCatch(
  read_csv(content(rw_response, as = "raw"), show_col_types = FALSE),
  error = function(e) {
    stop("Failed to parse Retraction Watch CSV: ", e$message)
  }
)

message("Retraction Watch database: ", nrow(rw_data), " entries")

# Normalize DOIs for matching
rw_dois <- tolower(trimws(rw_data$OriginalPaperDOI))
rw_dois <- rw_dois[!is.na(rw_dois) & nzchar(rw_dois)]

flora_doi_r_lower <- tolower(flora$doi_r)
is_retracted <- flora_doi_r_lower %in% rw_dois

removed_f3 <- flora[is_retracted, ]
if (nrow(removed_f3) > 0) {
  # Get retraction details for log
  retraction_details <- rw_data %>%
    mutate(doi_lower = tolower(trimws(OriginalPaperDOI))) %>%
    filter(doi_lower %in% tolower(removed_f3$doi_r)) %>%
    distinct(doi_lower, .keep_all = TRUE)

  detail_map <- setNames(
    paste0("Nature: ", retraction_details$RetractionNature,
           "; Reason: ", retraction_details$Reason),
    retraction_details$doi_lower
  )

  removal_log <- bind_rows(removal_log, tibble(
    doi_o = removed_f3$doi_o,
    doi_r = removed_f3$doi_r,
    reason = "retracted",
    detail = detail_map[tolower(removed_f3$doi_r)]
  ))
}

flora <- flora[!is_retracted, ]
message("Filter 3 (Retraction Watch): removed ", nrow(removed_f3),
        " rows, ", nrow(flora), " remaining")

# ============================================================================
# Filter 4: OSF registration check
# ============================================================================
osf_mask <- str_detect(tolower(flora$doi_r), fixed("osf.io/"))
osf_indices <- which(osf_mask)
message("Filter 4: checking ", length(osf_indices), " OSF DOIs for registrations")

osf_registrations <- logical(nrow(flora))

for (i in osf_indices) {
  doi_r <- flora$doi_r[i]

  # Extract GUID from DOI (the part after osf.io/)
  guid <- str_extract(doi_r, "(?<=osf\\.io/)[A-Za-z0-9]+")

  if (is.na(guid)) {
    message("  Could not extract GUID from: ", doi_r, " - keeping")
    next
  }

  result <- tryCatch({
    resp <- GET(
      paste0("https://api.osf.io/v2/guids/", guid, "/"),
      timeout(15),
      user_agent("R (FReD filter pipeline)")
    )

    if (http_error(resp)) {
      message("  OSF API error for ", guid, " (status ", status_code(resp),
              ") - keeping row")
      FALSE
    } else {
      body <- fromJSON(content(resp, as = "text", encoding = "UTF-8"))
      is_reg <- identical(body$data$type, "registrations")
      if (is_reg) message("  ", guid, " is a registration - removing")
      is_reg
    }
  }, error = function(e) {
    message("  OSF API failed for ", guid, ": ", e$message, " - keeping row")
    FALSE
  })

  osf_registrations[i] <- result
  Sys.sleep(0.5)  # Rate limiting
}

removed_f4 <- flora[osf_registrations, ]
if (nrow(removed_f4) > 0) {
  removal_log <- bind_rows(removal_log, tibble(
    doi_o = removed_f4$doi_o,
    doi_r = removed_f4$doi_r,
    reason = "osf_registration",
    detail = "OSF GUID resolved to a registration, not a preprint/paper"
  ))
}

flora <- flora[!osf_registrations, ]
message("Filter 4 (OSF registrations): removed ", nrow(removed_f4),
        " rows, ", nrow(flora), " remaining")

# ============================================================================
# Write outputs
# ============================================================================
n_end <- nrow(flora)
n_removed <- n_start - n_end

message("\n=== Summary ===")
message("Input:   ", n_start, " rows")
message("Output:  ", n_end, " rows")
message("Removed: ", n_removed, " rows")

write_excel_csv(flora, output_file)
message("Wrote filtered data to: ", output_file)

write_excel_csv(removal_log, log_file)
message("Wrote removal log (", nrow(removal_log), " entries) to: ", log_file)
