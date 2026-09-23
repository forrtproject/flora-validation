# Unpaywall open access URL lookup + caching
# Vectorised Unpaywall lookup with persistent CSV cache for OA URLs
#
# Uses `httr`, `jsonlite`, and `readr`.

library(dplyr)
library(httr)
library(jsonlite)
library(purrr)
library(readr)

# Reuse normalize_doi from openalex_cache.R (already sourced in pipeline)

load_unpaywall_cache <- function(cache_file = UNPAYWALL_CACHE) {
  if (!file.exists(cache_file)) {
    data.frame(doi = character(0), is_oa = logical(0), oa_url = character(0),
               stringsAsFactors = FALSE)
  } else {
    read_csv(cache_file, show_col_types = FALSE, col_types = cols(
      doi = col_character(),
      is_oa = col_logical(),
      oa_url = col_character()
    ))
  }
}

save_unpaywall_cache <- function(df, cache_file = UNPAYWALL_CACHE) {
  dir.create(dirname(cache_file), showWarnings = FALSE, recursive = TRUE)
  if (nrow(df) > 0) {
    df <- df[!duplicated(df$doi), , drop = FALSE]
  }
  write_excel_csv(df, cache_file)
}

#' Fetch OA info for a single DOI from Unpaywall
#'
#' @param doi A single DOI string
#' @param email Contact email (required by Unpaywall API)
#' @return list with doi, is_oa, oa_url
fetch_unpaywall_single <- function(doi, email) {
  doi_norm <- normalize_doi(doi)
  if (is.na(doi_norm)) return(list(doi = NA_character_, is_oa = NA, oa_url = NA_character_))

  url <- paste0("https://api.unpaywall.org/v2/", URLencode(doi_norm, reserved = TRUE))

  res <- tryCatch({
    resp <- GET(url, query = list(email = email),
                      user_agent("R (Unpaywall lookup)"),
                      timeout(10))
    if (http_error(resp)) return(list(doi = doi_norm, is_oa = FALSE, oa_url = NA_character_))
    fromJSON(content(resp, as = "text", encoding = "UTF-8"), simplifyVector = TRUE)
  }, error = function(e) NULL)

  if (is.null(res)) {
    return(list(doi = doi_norm, is_oa = FALSE, oa_url = NA_character_))
  }

  is_oa <- isTRUE(res$is_oa)
  oa_url <- NA_character_
  if (is_oa && !is.null(res$best_oa_location)) {
    oa_url <- res$best_oa_location$url_for_pdf
    if (is.null(oa_url) || is.na(oa_url)) {
      oa_url <- res$best_oa_location$url
    }
    if (is.null(oa_url)) oa_url <- NA_character_
  }

  list(doi = doi_norm, is_oa = is_oa, oa_url = as.character(oa_url))
}

#' Get Unpaywall OA URLs (vectorised)
#'
#' Fetch OA URLs for a vector of DOIs from Unpaywall with persistent CSV caching.
#'
#' @param dois Character vector of DOIs
#' @param email Contact email for Unpaywall API
#' @param cache_file Path to CSV cache file
#' @return A data.frame with columns `doi`, `is_oa`, `oa_url`; rows correspond 1:1 to input `dois`
get_unpaywall_oa <- function(dois,
                             email = "lukas.wallrich@gmail.com",
                             cache_file = UNPAYWALL_CACHE) {
  input_dois <- as.character(dois)
  normalized_inputs <- vapply(input_dois, normalize_doi, character(1))

  cache_df <- load_unpaywall_cache(cache_file)
  if (nrow(cache_df) > 0) {
    cache_df$doi <- vapply(cache_df$doi, as.character, character(1))
  }

  unique_norm <- unique(normalized_inputs[!is.na(normalized_inputs)])
  already_cached <- if (nrow(cache_df) > 0) cache_df$doi else character(0)
  to_fetch <- setdiff(unique_norm, already_cached)

  if (length(to_fetch) > 0) {
    cat(sprintf("Fetching %d OA URLs from Unpaywall...\n", length(to_fetch)))
    new_rows <- map(
      to_fetch,
      ~{
        res <- fetch_unpaywall_single(.x, email = email)
        data.frame(doi = res$doi, is_oa = res$is_oa, oa_url = res$oa_url,
                   stringsAsFactors = FALSE)
      },
      .progress = TRUE
    ) %>%
      bind_rows()

    cache_df <- bind_rows(cache_df, new_rows)
    save_unpaywall_cache(cache_df, cache_file)
  }

  # Map inputs to cache
  out_oa_url <- character(length(normalized_inputs))
  for (i in seq_along(normalized_inputs)) {
    nd <- normalized_inputs[i]
    if (is.na(nd)) {
      out_oa_url[i] <- NA_character_
    } else {
      idx <- match(nd, cache_df$doi)
      out_oa_url[i] <- if (is.na(idx)) NA_character_ else cache_df$oa_url[idx]
    }
  }

  data.frame(doi = input_dois, oa_url = out_oa_url, stringsAsFactors = FALSE)
}
