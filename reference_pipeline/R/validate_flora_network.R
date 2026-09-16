# FLoRA network validation
#
# Checks that require network access:
#   N1. Retracted papers (Retraction Watch DB) — both doi_o and doi_r
#   N2. DOIs resolve (HEAD request to doi.org)
#   N3. URLs resolve (HEAD request)
#
# Caches successful resolutions for 30 days.
# Supports false positive suppression via the shared suppressions CSV.
#
# Usage: Rscript R/validate_flora_network.R

library(readr)
library(dplyr)
library(stringr)
library(httr)
library(glue)
library(here)

source(here("R", "cache_config.R"))
# Reuse suppression helpers from structural validation
source(here("R", "validate_flora.R"))

CACHE_MAX_AGE_DAYS <- 30
RATE_LIMIT_DELAY <- 0.2  # seconds between requests (~5 req/sec)

# --- Cache helpers ---
load_cache <- function(cache_path) {
  if (!file.exists(cache_path)) return(list())
  cache <- readRDS(cache_path)
  cutoff <- Sys.time() - CACHE_MAX_AGE_DAYS * 86400
  cache[sapply(cache, function(x) x$timestamp > cutoff)]
}

save_cache <- function(cache, cache_path) {
  dir.create(dirname(cache_path), showWarnings = FALSE, recursive = TRUE)
  saveRDS(cache, cache_path)
}

# --- Resolution checkers ---
check_doi_resolves <- function(doi, cache) {
  if (doi %in% names(cache)) return(list(ok = cache[[doi]]$ok, cache = cache))
  Sys.sleep(RATE_LIMIT_DELAY)
  url <- paste0("https://doi.org/", doi)
  ok <- tryCatch({
    resp <- HEAD(url, timeout(15), user_agent("R (FLoRA validation pipeline)"))
    status_code(resp) < 400
  }, error = function(e) FALSE)
  if (ok) cache[[doi]] <- list(ok = TRUE, timestamp = Sys.time())
  list(ok = ok, cache = cache)
}

check_url_resolves <- function(url_val, cache) {
  if (url_val %in% names(cache)) return(list(ok = cache[[url_val]]$ok, cache = cache))
  Sys.sleep(RATE_LIMIT_DELAY)
  ok <- tryCatch({
    resp <- HEAD(url_val, timeout(15), user_agent("R (FLoRA validation pipeline)"))
    status_code(resp) < 400
  }, error = function(e) FALSE)
  if (ok) cache[[url_val]] <- list(ok = TRUE, timestamp = Sys.time())
  list(ok = ok, cache = cache)
}

validate_flora_network <- function(data, suppressions = NULL) {
  data <- data %>%
    mutate(id = paste(doi_o, coalesce(doi_r, url_r), sep = " | "))

  report_parts <- c(
    "## FLoRA Network Validation Report\n",
    glue("_Generated: {format(Sys.time(), '%Y-%m-%d %H:%M:%S')} | Dataset: {nrow(data)} rows_\n")
  )

  n_suppressed <- 0L
  results <- list()

  # Helper: filter suppressed values for a given check
  filter_suppressed <- function(values, check_name) {
    if (is.null(suppressions) || nrow(suppressions) == 0) return(values)
    suppressed <- suppressions$item_id[suppressions$check_name == check_name]
    keep <- !values %in% suppressed
    n_suppressed <<- n_suppressed + sum(!keep)
    values[keep]
  }

  # =========================================================================
  # N1: Retracted papers (Retraction Watch — both doi_o AND doi_r)
  # =========================================================================
  message("Downloading Retraction Watch database from Crossref...")
  rw_data <- tryCatch({
    rw_response <- GET(
      "https://api.labs.crossref.org/data/retractionwatch",
      timeout(120),
      user_agent("R (FLoRA validation pipeline)")
    )
    if (http_error(rw_response)) stop("HTTP ", status_code(rw_response))
    read_csv(content(rw_response, as = "raw"), show_col_types = FALSE)
  }, error = function(e) {
    message("WARNING: Failed to download Retraction Watch DB: ", e$message)
    NULL
  })

  if (!is.null(rw_data)) {
    rw_dois <- tolower(trimws(rw_data$OriginalPaperDOI))
    rw_dois <- rw_dois[!is.na(rw_dois) & nzchar(rw_dois)]
    message("Retraction Watch database: ", length(rw_dois), " unique DOIs")

    retracted_r <- data %>%
      filter(!is.na(doi_r), tolower(doi_r) %in% rw_dois) %>%
      mutate(which_doi = "doi_r", retracted_doi = doi_r)
    retracted_o <- data %>%
      filter(!is.na(doi_o), tolower(doi_o) %in% rw_dois) %>%
      mutate(which_doi = "doi_o", retracted_doi = doi_o)
    retracted_all <- bind_rows(retracted_r, retracted_o) %>% distinct()

    # Filter suppressed retracted DOIs
    if (nrow(retracted_all) > 0) {
      suppressed_dois <- if (!is.null(suppressions) && nrow(suppressions) > 0) {
        suppressions$item_id[suppressions$check_name == "Retracted papers"]
      } else {
        character()
      }
      keep <- !retracted_all$retracted_doi %in% suppressed_dois
      n_suppressed <- n_suppressed + sum(!keep)
      retracted_all <- retracted_all[keep, ]
    }

    if (nrow(retracted_all) > 0) {
      report_parts <- c(report_parts,
        glue("**Retracted papers** ({nrow(retracted_all)} issues):\n"))
      for (i in seq_len(nrow(retracted_all))) {
        row <- retracted_all[i, ]
        report_parts <- c(report_parts,
          glue("- [ ] **{row$which_doi}**: `{row$retracted_doi}` \u2014 {row$id}"))
      }
      report_parts <- c(report_parts, "")
    } else {
      report_parts <- c(report_parts, "- [x] No retracted papers found\n")
    }
    results$retracted <- retracted_all
  } else {
    report_parts <- c(report_parts,
      "- [ ] Retraction Watch check skipped (download failed)\n")
  }

  # =========================================================================
  # N2: DOIs resolve
  # =========================================================================
  message("Checking DOI resolution...")
  doi_cache <- load_cache(VALIDATION_DOI_RESOLUTION_CACHE)

  all_dois <- unique(c(
    data$doi_o[!is.na(data$doi_o)],
    data$doi_r[!is.na(data$doi_r)]
  ))
  message("  ", length(all_dois), " unique DOIs to check (",
          sum(all_dois %in% names(doi_cache)), " cached)")

  failed_dois <- character()
  for (doi in all_dois) {
    res <- check_doi_resolves(doi, doi_cache)
    doi_cache <- res$cache
    if (!res$ok) failed_dois <- c(failed_dois, doi)
  }

  save_cache(doi_cache, VALIDATION_DOI_RESOLUTION_CACHE)
  failed_dois <- filter_suppressed(failed_dois, "DOIs that failed to resolve")

  if (length(failed_dois) > 0) {
    n <- length(failed_dois)
    doi_lines <- paste0("- [ ] `", failed_dois, "`")
    if (n <= 10) {
      report_parts <- c(report_parts,
        glue("**DOIs that failed to resolve** ({n} issues):\n"),
        doi_lines, "")
    } else {
      report_parts <- c(report_parts,
        glue("<details><summary><b>DOIs that failed to resolve</b> ({n} issues)</summary>"),
        "",  # blank line required for GFM to render markdown inside HTML
        doi_lines, "",
        "</details>", "")
    }
  } else {
    report_parts <- c(report_parts, "- [x] All DOIs resolved successfully\n")
  }
  results$failed_dois <- failed_dois

  # =========================================================================
  # N3: URLs resolve
  # =========================================================================
  message("Checking URL resolution...")
  url_cache <- load_cache(VALIDATION_URL_RESOLUTION_CACHE)

  url_cols <- c("url_r", "oa_url_o", "oa_url_r")
  all_urls <- unique(unlist(lapply(
    intersect(url_cols, names(data)),
    function(col) data[[col]][!is.na(data[[col]])]
  )))
  message("  ", length(all_urls), " unique URLs to check (",
          sum(all_urls %in% names(url_cache)), " cached)")

  failed_urls <- character()
  for (url_val in all_urls) {
    res <- check_url_resolves(url_val, url_cache)
    url_cache <- res$cache
    if (!res$ok) failed_urls <- c(failed_urls, url_val)
  }

  save_cache(url_cache, VALIDATION_URL_RESOLUTION_CACHE)
  failed_urls <- filter_suppressed(failed_urls, "URLs that failed to resolve")

  if (length(failed_urls) > 0) {
    n <- length(failed_urls)
    url_lines <- paste0("- [ ] `", failed_urls, "`")
    if (n <= 10) {
      report_parts <- c(report_parts,
        glue("**URLs that failed to resolve** ({n} issues):\n"),
        url_lines, "")
    } else {
      report_parts <- c(report_parts,
        glue("<details><summary><b>URLs that failed to resolve</b> ({n} issues)</summary>"),
        "",  # blank line required for GFM to render markdown inside HTML
        url_lines, "",
        "</details>", "")
    }
  } else {
    report_parts <- c(report_parts, "- [x] All URLs resolved successfully\n")
  }
  results$failed_urls <- failed_urls

  if (n_suppressed > 0) {
    report_parts <- c(report_parts,
      glue("\n_({n_suppressed} suppressed false positive{ifelse(n_suppressed > 1, 's', '')} not shown)_"))
  }

  results$report <- paste(report_parts, collapse = "\n")
  results
}

# === CLI entry point ===
if (!interactive() || identical(Sys.getenv("FLORA_VALIDATE_RUN"), "1")) {
  input_file <- here("output", "flora.csv")
  if (!file.exists(input_file)) stop("flora.csv not found at: ", input_file)

  flora <- read_csv(input_file, show_col_types = FALSE)
  message("Read ", nrow(flora), " rows from flora.csv")

  # Load existing suppressions (shared with structural validation)
  suppressions <- if (file.exists(SUPPRESSIONS_FILE)) {
    read_csv(SUPPRESSIONS_FILE, show_col_types = FALSE)
  } else {
    tibble(check_name = character(), item_id = character(),
           suppressed_date = character())
  }

  # Parse new suppressions from GH issue body (CI sets this env var)
  issue_body_file <- Sys.getenv("FLORA_ISSUE_BODY_FILE", "")
  if (nzchar(issue_body_file)) {
    new_supp <- parse_issue_suppressions(issue_body_file)
    if (nrow(new_supp) > 0) {
      new_supp$suppressed_date <- format(Sys.Date(), "%Y-%m-%d")
      suppressions <- bind_rows(suppressions, new_supp) %>%
        distinct(check_name, item_id, .keep_all = TRUE)
      write_excel_csv(suppressions, SUPPRESSIONS_FILE)
      message("Updated suppressions: ", nrow(new_supp), " new entries")
    }
  }

  if (nrow(suppressions) > 0) {
    message("Loaded ", nrow(suppressions), " suppressions")
  }

  result <- validate_flora_network(flora, suppressions = suppressions)
  cat(result$report, "\n")

  # Save network report CSV for tracking
  network_issues <- bind_rows(
    if (length(result$failed_dois) > 0) {
      tibble(type = "doi", value = result$failed_dois, status = "failed")
    },
    if (length(result$failed_urls) > 0) {
      tibble(type = "url", value = result$failed_urls, status = "failed")
    },
    if (!is.null(result$retracted) && nrow(result$retracted) > 0) {
      tibble(type = "retracted",
             value = result$retracted$retracted_doi,
             status = result$retracted$which_doi)
    }
  )

  if (nrow(network_issues) > 0) {
    report_csv <- here("output", "flora_network_validation.csv")
    write_excel_csv(network_issues, report_csv)
    message("Saved network validation issues to: ", report_csv)
  } else {
    message("No network validation issues found.")
  }
}
