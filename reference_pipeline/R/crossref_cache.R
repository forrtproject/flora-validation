# CrossRef and DataCite Caching Functions
# Consolidates citation, DOI metadata, and author caching by data type
# Sources: crossref_citation_cache.R, hackathon prep - flora.qmd, crossref_author_retrieval.qmd

library(dplyr)
library(glue)
library(httr)
library(jsonlite)
library(openxlsx)
library(rcrossref)
library(readxl)
library(stringr)
library(tibble)

`%||%` <- function(x, y) if (is.null(x) || length(x) == 0 || all(is.na(x))) y else x
is_doi <- function(x) grepl("^10\\.[0-9]{4,}/\\S+$", x)

# ============================================================================
# Citation Caching (APA/BibTeX)
# ============================================================================

#' Load citation cache from disk
#' @param cache_file Path to cache file (RDS format)
#' @return List with 'apa' and 'bibtex' named lists
load_citation_cache <- function(cache_file = CROSSREF_CITATIONS_CACHE) {
  if (file.exists(cache_file)) {
    readRDS(cache_file)
  } else {
    list(apa = list(), bibtex = list())
  }
}

#' Save citation cache to disk
#' @param cache List with 'apa' and 'bibtex' sublists
#' @param cache_file Path to save cache
save_citation_cache <- function(cache, cache_file = CROSSREF_CITATIONS_CACHE) {
  saveRDS(cache, cache_file)
}

#' Load manual reference overrides from Excel
#' @param manual_ref_file Path to Excel file with columns: key, reference_apa, reference_bibtex,
#'   and optional field columns: title, author, journal, year, volume, issue, pages
#' @return Tibble with manual references
load_manual_references <- function(manual_ref_file = MANUAL_REFERENCES) {
  empty_df <- tibble(
    key = character(),
    reference_apa = character(),
    reference_bibtex = character(),
    title = character(),
    author = character(),
    journal = character(),
    year = integer(),
    volume = character(),
    issue = character(),
    pages = character()
  )

  if (!file.exists(manual_ref_file)) {
    return(empty_df)
  }

  tryCatch({
    df <- read_excel(manual_ref_file)
    # Ensure all expected columns exist
    for (col in names(empty_df)) {
      if (!col %in% names(df)) {
        df[[col]] <- if (col == "year") NA_integer_ else NA_character_
      }
    }
    if (exists("URLR_DOIR_MAP_CACHE") && file.exists(URLR_DOIR_MAP_CACHE)) {
      map_df <- tryCatch(readRDS(URLR_DOIR_MAP_CACHE), error = function(e) NULL)
      if (!is.null(map_df) && all(c("url_r", "doi_r") %in% names(map_df))) {
        map_df <- map_df %>%
          transmute(
            url_r = tolower(trimws(url_r)),
            doi_r = tolower(trimws(doi_r))
          ) %>%
          filter(!is.na(url_r), !is.na(doi_r)) %>%
          distinct(url_r, .keep_all = TRUE)

        key_norm <- tolower(trimws(df$key))
        hit <- match(key_norm, map_df$url_r)
        df$key <- ifelse(!is.na(hit), map_df$doi_r[hit], df$key)
      }
    }
    df
  }, error = function(e) {
    warning(sprintf("Could not read manual references file %s", manual_ref_file))
    empty_df
  })
}

#' Parse BibTeX author string to JSON format
#' @param author_str BibTeX author string (e.g., "Family, Given and Family, Given")
#' @return JSON string in CrossRef format
parse_bibtex_authors_to_json <- function(author_str) {
  if (is.na(author_str) || !nzchar(author_str)) return(NA_character_)

  # Split by " and " (BibTeX author separator)
  authors <- strsplit(author_str, "\\s+and\\s+", perl = TRUE)[[1]]
  authors <- trimws(authors)

  author_list <- lapply(seq_along(authors), function(i) {
    a <- authors[i]
    given <- NA_character_
    family <- NA_character_

    if (grepl(",", a)) {
      # Format: "Family, Given" or "Family, Given Middle"
      parts <- strsplit(a, ",\\s*")[[1]]
      family <- trimws(parts[1])
      given <- if (length(parts) > 1) trimws(parts[2]) else NA_character_
    } else {
      # Format: "Given Family" or "Given Middle Family"
      parts <- strsplit(trimws(a), "\\s+")[[1]]
      if (length(parts) >= 2) {
        family <- parts[length(parts)]
        given <- paste(parts[-length(parts)], collapse = " ")
      } else if (length(parts) == 1) {
        family <- parts[1]
      }
    }

    list(
      given = if (!is.na(given) && nzchar(given)) given else NULL,
      family = if (!is.na(family) && nzchar(family)) family else NULL,
      sequence = if (i == 1) "first" else "additional"
    )
  })

  as.character(toJSON(author_list, auto_unbox = TRUE, null = "null"))
}

#' Parse BibTeX string to extract individual fields
#' @param bibtex BibTeX string
#' @return Named list with title, author (as JSON), journal, year, volume, issue, pages
parse_bibtex_fields <- function(bibtex) {
  result <- list(
    title = NA_character_,
    author = NA_character_,
    journal = NA_character_,
    year = NA_integer_,
    volume = NA_character_,
    issue = NA_character_,
    pages = NA_character_
  )

  if (is.na(bibtex) || !nzchar(bibtex)) return(result)

  # Robust BibTeX field extractor
  # Supports:
  #   field = { ... }   (handles nested braces)
  #   field = " ... "   (handles escaped quotes)
  #   field = ' ... '   (handles escaped quotes)
  #   field = bareValue (until comma or newline)
 extract_field <- function(bib, field) {
  if (is.na(bib) || !nzchar(bib)) return(NA_character_)

  bib <- as.character(bib)

  # Find "field =" (case-insensitive)
  m <- regexpr(paste0("(?i)\\b", field, "\\b\\s*=\\s*"), bib, perl = TRUE)
  if (m[1] == -1) return(NA_character_)

  i <- m[1] + attr(m, "match.length") # index right after "field = "
  n <- nchar(bib)

  # Skip whitespace
  while (i <= n && grepl("\\s", substr(bib, i, i))) i <- i + 1
  if (i > n) return(NA_character_)

  start_char <- substr(bib, i, i)

  # -------- braced value { ... } with nested brace support --------
  if (start_char == "{") {
    depth <- 1
    j <- i + 1
    while (j <= n && depth > 0) {
      ch <- substr(bib, j, j)
      if (ch == "{") depth <- depth + 1
      if (ch == "}") depth <- depth - 1
      j <- j + 1
    }
    if (depth != 0) return(NA_character_)

    val <- substr(bib, i + 1, j - 2) # inside outer braces
    return(str_squish(val))
  }

  # -------- double-quoted value " ... " (supports escaped quotes) --------
  if (start_char == "\"") {
    j <- i + 1
    escaped <- FALSE
    while (j <= n) {
      ch <- substr(bib, j, j)
      if (!escaped && ch == "\"") break
      if (!escaped && ch == "\\") escaped <- TRUE else escaped <- FALSE
      j <- j + 1
    }
    if (j > n) return(NA_character_)

    val <- substr(bib, i + 1, j - 1)
    return(str_squish(val))
  }

  # -------- single-quoted value ' ... ' (supports escaped quotes) --------
  if (start_char == "'") {
    j <- i + 1
    escaped <- FALSE
    while (j <= n) {
      ch <- substr(bib, j, j)
      if (!escaped && ch == "'") break
      if (!escaped && ch == "\\") escaped <- TRUE else escaped <- FALSE
      j <- j + 1
    }
    if (j > n) return(NA_character_)

    val <- substr(bib, i + 1, j - 1)
    return(str_squish(val))
  }

  # -------- bare value (until comma or newline) --------
  j <- i
  while (j <= n) {
    ch <- substr(bib, j, j)
    if (ch == "," || ch == "\n" || ch == "\r") break
    j <- j + 1
  }
  val <- substr(bib, i, j - 1)
  val <- gsub("\\s+$", "", val)
  val <- gsub("^\\s+", "", val)
  val <- gsub("[{}\"]", "", val) # light cleanup for bare tokens
  val <- str_squish(val)

  if (!nzchar(val)) NA_character_ else val
}


# Drop-in replacement for parse_bibtex_fields() using the robust extractor
parse_bibtex_fields <- function(bibtex) {
  result <- list(
    title = NA_character_,
    author = NA_character_,
    journal = NA_character_,
    year = NA_integer_,
    volume = NA_character_,
    issue = NA_character_,
    pages = NA_character_
  )

  if (is.na(bibtex) || !nzchar(bibtex)) return(result)

  result$title <- extract_bibtex_field(bibtex, "title")

  # author -> JSON via your existing helper
  author_str <- extract_bibtex_field(bibtex, "author")
  result$author <- parse_bibtex_authors_to_json(author_str)

  result$journal <- extract_bibtex_field(bibtex, "journal") %||%
    extract_bibtex_field(bibtex, "booktitle") %||%
    extract_bibtex_field(bibtex, "school") %||%
    extract_bibtex_field(bibtex, "institution") %||%
    extract_bibtex_field(bibtex, "publisher")

  year_str <- extract_bibtex_field(bibtex, "year")
  result$year <- if (!is.na(year_str)) suppressWarnings(as.integer(year_str)) else NA_integer_

  result$volume <- extract_bibtex_field(bibtex, "volume")
  result$issue  <- extract_bibtex_field(bibtex, "number")
  result$pages  <- extract_bibtex_field(bibtex, "pages")

  result
}


  result$title <- extract_field(bibtex, "title")

  # Parse author to JSON format
  author_str <- extract_field(bibtex, "author")
  result$author <- parse_bibtex_authors_to_json(author_str)

  result$journal <- extract_field(bibtex, "journal") %||%
    extract_field(bibtex, "booktitle") %||%
    extract_field(bibtex, "school") %||%
    extract_field(bibtex, "institution")
  year_str <- extract_field(bibtex, "year")
  result$year <- if (!is.na(year_str)) suppressWarnings(as.integer(year_str)) else NA_integer_
  result$volume <- extract_field(bibtex, "volume")
  result$issue <- extract_field(bibtex, "number")
  result$pages <- extract_field(bibtex, "pages")

  result
}

#' Fetch a citation from CrossRef for a given DOI
#' @param doi DOI string (e.g., "10.1234/example")
#' @param type Citation format: "bibtex" or "apa"
#' @return Citation string or NA if not found
get_crossref_citation <- function(doi, type = c("bibtex", "apa")) {
  doi <- trimws(doi)

  # Validate DOI format
  if (!grepl("^10\\.[0-9]{4,}/\\S+$", doi)) {
    return(NA_character_)
  }

  type <- match.arg(type)

  # Set CrossRef content negotiation parameters
  if (type == "bibtex") {
    fmt <- "bibtex"
    style <- NULL
  } else if (type == "apa") {
    fmt <- "text"
    style <- "apa"
  }

  # Fetch from CrossRef with error handling
  tryCatch({
    cr_cn(dois = doi, format = fmt, style = style)
  }, error = function(e) {
    NA_character_
  })
}
# ------------------------------------------------------------------------------
# DUMMY_* DOI stripping
#
# Placeholder DOIs of the form "DUMMY_XXX" are used to key rows into
# manual_references.xlsx when the referenced paper has no real DOI or URL
# (e.g., book chapters). The prefix bypasses CrossRef/DataCite/content-neg
# lookups (is_doi() filters them out) while still matching manual_refs by key.
#
# Before final output we replace DUMMY_* identifiers with NA so consumers of
# flora.csv don't see fake DOIs. Applies to doi_o, doi_r, and their _alt/_hash
# companions when present.
# ------------------------------------------------------------------------------
strip_dummy_dois <- function(data, verbose = TRUE) {
  is_dummy <- function(x) !is.na(x) & grepl("^dummy_", tolower(x))
  before <- list(
    doi_o = if ("doi_o" %in% names(data)) sum(is_dummy(data$doi_o)) else 0L,
    doi_r = if ("doi_r" %in% names(data)) sum(is_dummy(data$doi_r)) else 0L
  )
  for (col in c("doi_o", "doi_r", "alt_identifier_o", "alt_identifier_r")) {
    if (col %in% names(data)) data[[col]][is_dummy(data[[col]])] <- NA_character_
  }
  # Hashes computed from DUMMY_* leak the placeholder via md5; null them too
  # when the corresponding DOI was stripped.
  if ("doi_o_hash" %in% names(data) && before$doi_o > 0) {
    data$doi_o_hash[is.na(data$doi_o)] <- NA_character_
  }
  if ("doi_r_hash" %in% names(data) && before$doi_r > 0) {
    data$doi_r_hash[is.na(data$doi_r)] <- NA_character_
  }
  if (verbose && (before$doi_o + before$doi_r) > 0) {
    cat(sprintf("  Stripped DUMMY_ placeholders: %d doi_o, %d doi_r\n",
                before$doi_o, before$doi_r))
  }
  data
}

# ------------------------------------------------------------------------------
# URL-only manual overrides for the original (_o) or replication (_r) side.
# Works on Step-7 column names: ref_{side}_clean, bibtex_{side}, title_{side}, …
# Does NOT create apa_ref_{side} / bibtex_ref_{side} (so later rename() won't
# break). Parametrized by `side` so O-side papers without DOIs can be patched
# via a url_o key in manual_references.xlsx.
# ------------------------------------------------------------------------------

augment_with_manual_url_refs <- function(data,
                                         manual_ref_file = MANUAL_REFERENCES,
                                         side = c("r", "o"),
                                         url_col = NULL,
                                         recover_title_from_bibtex = TRUE,
                                         verbose = TRUE) {
  side <- match.arg(side)
  if (is.null(url_col)) url_col <- paste0("url_", side)

  # Column name helpers based on side
  col_ref_clean <- paste0("ref_", side, "_clean")
  col_bibtex    <- paste0("bibtex_", side)
  col_title     <- paste0("title_", side)
  col_author    <- paste0("author_", side)
  col_journal   <- paste0("journal_", side)
  col_year      <- paste0("year_", side)
  col_volume    <- paste0("volume_", side)
  col_issue     <- paste0("issue_", side)
  col_pages     <- paste0("pages_", side)

  stopifnot(is.data.frame(data))

  if (!file.exists(manual_ref_file)) {
    if (verbose) message("Manual refs file not found: ", manual_ref_file, " (skipping)")
    return(data)
  }
  if (!url_col %in% names(data)) {
    if (verbose) message("Column ", url_col, " not found (skipping manual URL merge)")
    return(data)
  }

  norm_url <- function(x) {
    x <- tolower(trimws(as.character(x)))
    x <- gsub("^https?://", "", x)
    x <- gsub("^www\\.", "", x)
    x <- gsub("/+$", "", x)
    na_if(x, "")
  }
  has_text_vec <- function(x) {
    x <- trimws(as.character(x))
    !is.na(x) & nzchar(x)
  }

  for (nm in c(col_ref_clean, col_bibtex, col_title, col_author,
               col_journal, col_volume, col_issue, col_pages)) {
    if (!nm %in% names(data)) data[[nm]] <- NA_character_
  }
  if (!col_year %in% names(data)) data[[col_year]] <- NA_integer_

  manual_df <- read_excel(manual_ref_file)
  expected <- c("key","reference_apa","reference_bibtex","title","author","journal","year","volume","issue","pages")
  for (col in expected) if (!col %in% names(manual_df)) manual_df[[col]] <- NA

  manual_url <- manual_df %>%
    mutate(
      url_key = norm_url(key),
      reference_apa    = na_if(trimws(as.character(reference_apa)), ""),
      reference_bibtex = na_if(trimws(as.character(reference_bibtex)), ""),
      title   = na_if(trimws(as.character(title)), ""),
      author  = na_if(trimws(as.character(author)), ""),
      journal = na_if(trimws(as.character(journal)), ""),
      volume  = na_if(trimws(as.character(volume)), ""),
      issue   = na_if(trimws(as.character(issue)), ""),
      pages   = na_if(trimws(as.character(pages)), "")
    ) %>%
    filter(!is.na(url_key)) %>%
    distinct(url_key, .keep_all = TRUE) %>%
    rowwise() %>%
    mutate(
      .parsed = list(if (has_text_vec(reference_bibtex)) parse_bibtex_fields(reference_bibtex) else
        list(title=NA_character_, author=NA_character_, journal=NA_character_,
             year=NA_integer_, volume=NA_character_, issue=NA_character_, pages=NA_character_)),
      title  = if (has_text_vec(title)) title else .parsed$title,
      author = if (has_text_vec(author)) author else .parsed$author,
      journal= if (has_text_vec(journal)) journal else .parsed$journal,
      year   = if (!is.na(year)) as.integer(year) else .parsed$year,
      volume = if (has_text_vec(volume)) volume else .parsed$volume,
      issue  = if (has_text_vec(issue)) issue else .parsed$issue,
      pages  = if (has_text_vec(pages)) pages else .parsed$pages
    ) %>%
    ungroup() %>%
    transmute(
      url_key,
      manual_ref_clean = reference_apa,
      manual_bibtex    = reference_bibtex,
      manual_title     = title,
      manual_author    = author,
      manual_journal   = journal,
      manual_year      = year,
      manual_volume    = volume,
      manual_issue     = issue,
      manual_pages     = pages
    )

  before_missing_title <- sum(!has_text_vec(data[[col_title]]))

  out <- data %>%
    mutate(.url_key_tmp = norm_url(.data[[url_col]])) %>%
    left_join(manual_url, by = c(".url_key_tmp" = "url_key"))

  out[[col_ref_clean]] <- coalesce(out[[col_ref_clean]], out$manual_ref_clean)
  out[[col_bibtex]]    <- coalesce(out[[col_bibtex]],    out$manual_bibtex)
  out[[col_title]]     <- coalesce(out[[col_title]],     out$manual_title)
  out[[col_author]]    <- coalesce(out[[col_author]],    out$manual_author)
  out[[col_journal]]   <- coalesce(out[[col_journal]],   out$manual_journal)
  out[[col_year]]      <- coalesce(out[[col_year]],      out$manual_year)
  out[[col_volume]]    <- coalesce(out[[col_volume]],    out$manual_volume)
  out[[col_issue]]     <- coalesce(out[[col_issue]],     out$manual_issue)
  out[[col_pages]]     <- coalesce(out[[col_pages]],     out$manual_pages)

  out <- out %>% select(-.url_key_tmp, -starts_with("manual_"))

  if (recover_title_from_bibtex) {
    need_idx <- which(!has_text_vec(out[[col_title]]) & has_text_vec(out[[col_bibtex]]))
    if (length(need_idx) > 0) {
      recovered <- vapply(out[[col_bibtex]][need_idx],
                          function(b) parse_bibtex_fields(b)$title,
                          character(1))
      out[[col_title]][need_idx] <- coalesce(out[[col_title]][need_idx], recovered)
    }
  }

  after_missing_title <- sum(!has_text_vec(out[[col_title]]))

  if (verbose) {
    n_matches <- sum(norm_url(out[[url_col]]) %in% manual_url$url_key, na.rm = TRUE)
    message("=== Manual URL overrides (", toupper(side), " side) ===")
    message("URL matches found: ", n_matches)
    message("Missing ", col_title, " before: ", before_missing_title)
    message("Missing ", col_title, " after : ", after_missing_title)
    message("Titles recovered     : ", before_missing_title - after_missing_title)
  }

  out
}

# Backward-compat alias used by flora/fred pipelines
augment_with_manual_url_refs_r <- function(data,
                                          manual_ref_file = MANUAL_REFERENCES,
                                          url_col = "url_r",
                                          recover_title_from_bibtex = TRUE,
                                          verbose = TRUE) {
  augment_with_manual_url_refs(data,
                                manual_ref_file = manual_ref_file,
                                side = "r",
                                url_col = url_col,
                                recover_title_from_bibtex = recover_title_from_bibtex,
                                verbose = verbose)
}


#' Fetch APA citation from DataCite (fallback for CrossRef)
#' @param doi DOI string
#' @return APA-style citation string or NA
get_datacite_apa <- function(doi) {
  doi <- trimws(doi)
  if (!grepl("^10\\.[0-9]{4,}/\\S+$", doi)) return(NA_character_)

  tryCatch({
    url <- glue("https://api.datacite.org/dois/{URLencode(doi, reserved = TRUE)}")
    res <- GET(url, timeout(10))
    if (status_code(res) != 200) return(NA_character_)

    j <- fromJSON(content(res, "text", encoding = "UTF-8"))
    attr <- j$data$attributes

    # Extract components
    authors <- attr$creators
    if (is.data.frame(authors) && nrow(authors) > 0) {
      author_str <- paste(
        coalesce(authors$name, authors$familyName),
        collapse = ", "
      )
    } else {
      author_str <- "Unknown"
    }
    year <- attr$publicationYear %||% ""
    title <- if (is.data.frame(attr$titles)) {
      attr$titles$title[1]
    } else if (is.list(attr$titles) && length(attr$titles) > 0) {
      attr$titles[[1]]$title %||% attr$titles[[1]]
    } else {
      ""
    }
    publisher <- attr$publisher$name %||% attr$publisher %||% ""

    sprintf("%s (%s). %s. %s. https://doi.org/%s",
            author_str, year, title, publisher, doi)
  }, error = function(e) NA_character_)
}

#' Fetch BibTeX citation from DataCite (fallback for CrossRef)
#' @param doi DOI string
#' @return BibTeX string or NA
get_datacite_bibtex <- function(doi) {
  doi <- trimws(doi)
  if (!grepl("^10\\.[0-9]{4,}/\\S+$", doi)) return(NA_character_)

  tryCatch({
    url <- glue("https://api.datacite.org/dois/{URLencode(doi, reserved = TRUE)}")
    res <- GET(url, timeout(10))
    if (status_code(res) != 200) return(NA_character_)

    j <- fromJSON(content(res, "text", encoding = "UTF-8"))
    attr <- j$data$attributes

    # Extract components
    authors <- attr$creators
    if (is.data.frame(authors) && nrow(authors) > 0) {
      author_str <- paste(
        coalesce(authors$name,
                 paste(authors$givenName, authors$familyName)),
        collapse = " and "
      )
    } else {
      author_str <- ""
    }
    year <- attr$publicationYear %||% ""
    title <- if (is.data.frame(attr$titles)) {
      attr$titles$title[1]
    } else if (is.list(attr$titles) && length(attr$titles) > 0) {
      attr$titles[[1]]$title %||% attr$titles[[1]]
    } else {
      ""
    }
    publisher <- attr$publisher$name %||% attr$publisher %||% ""
    key <- gsub("[^a-zA-Z0-9]", "", doi)

    sprintf(
      "@misc{%s,\n  author = {%s},\n  title = {%s},\n  year = {%s},\n  publisher = {%s},\n  doi = {%s}\n}",
      key, author_str, title, year, publisher, doi
    )
  }, error = function(e) NA_character_)
}

# ---- Reference formatters from structured fields ----
# Build APA / BibTeX strings when CrossRef/DataCite can't be queried (e.g. an
# OpenAlex-only entry with no DOI). The author input is a CrossRef-style JSON
# array of {given, family, sequence} objects, matching what the rest of the
# pipeline produces.

# Convert "Mary Anne" -> "M. A." for APA author lists
.given_to_initials <- function(given) {
  if (is.null(given) || is.na(given) || !nzchar(given)) return("")
  parts <- unlist(strsplit(given, "[\\s.\\-]+", perl = TRUE))
  parts <- parts[nzchar(parts)]
  if (length(parts) == 0) return("")
  paste0(toupper(substr(parts, 1, 1)), ".", collapse = " ")
}

.parse_authors_json <- function(authors_json) {
  if (is.null(authors_json) || is.na(authors_json) || !nzchar(authors_json)) {
    return(list())
  }
  parsed <- tryCatch(fromJSON(authors_json, simplifyVector = FALSE),
                     error = function(e) NULL)
  if (is.null(parsed) || length(parsed) == 0) return(list())
  parsed
}

# APA 7-style author list. Handles 1, 2, 3-20, and >=21 authors per spec.
format_apa_authors <- function(authors_json) {
  authors <- .parse_authors_json(authors_json)
  if (length(authors) == 0) return(NA_character_)

  fmt_one <- function(a) {
    family   <- a$family %||% NA_character_
    given    <- a$given  %||% NA_character_
    initials <- .given_to_initials(given)
    if (is.na(family) || !nzchar(family)) {
      # Group/organisation author with only a literal name in `given`
      if (!is.na(given) && nzchar(given)) return(given)
      return(NA_character_)
    }
    if (nzchar(initials)) sprintf("%s, %s", family, initials) else family
  }

  formatted <- vapply(authors, fmt_one, character(1))
  formatted <- formatted[!is.na(formatted) & nzchar(formatted)]
  n <- length(formatted)
  if (n == 0) return(NA_character_)
  if (n == 1) return(formatted[[1]])
  if (n == 2) return(paste(formatted, collapse = ", & "))
  if (n <= 20) {
    return(paste0(paste(formatted[-n], collapse = ", "), ", & ", formatted[[n]]))
  }
  # 21+ authors: first 19, ellipsis, final author (no ampersand)
  paste0(paste(formatted[1:19], collapse = ", "), ", … ", formatted[[n]])
}

# BibTeX-style author list: "Family, Given and Family, Given"
format_bibtex_authors <- function(authors_json) {
  authors <- .parse_authors_json(authors_json)
  if (length(authors) == 0) return(NA_character_)

  fmt_one <- function(a) {
    family <- a$family %||% NA_character_
    given  <- a$given  %||% NA_character_
    if (is.na(family) || !nzchar(family)) {
      if (!is.na(given) && nzchar(given)) return(given)
      return(NA_character_)
    }
    if (!is.na(given) && nzchar(given)) sprintf("%s, %s", family, given) else family
  }

  parts <- vapply(authors, fmt_one, character(1))
  parts <- parts[!is.na(parts) & nzchar(parts)]
  if (length(parts) == 0) return(NA_character_)
  paste(parts, collapse = " and ")
}

# Build APA reference string from structured fields. Returns NA if too little
# information is available (need at least authors-or-title plus year).
format_apa_from_fields <- function(authors_json = NA_character_,
                                   year = NA,
                                   title = NA_character_,
                                   journal = NA_character_,
                                   volume = NA_character_,
                                   issue = NA_character_,
                                   pages = NA_character_,
                                   doi = NA_character_) {
  has <- function(x) !is.null(x) && length(x) == 1 && !is.na(x) && nzchar(trimws(as.character(x)))

  authors <- format_apa_authors(authors_json)
  if (!has(authors) && !has(title)) return(NA_character_)
  if (!has(year)) return(NA_character_)

  author_part <- if (has(authors)) authors else "[No author]"
  year_part   <- sprintf("(%s).", as.character(year))

  title_part <- if (has(title)) {
    t <- trimws(title)
    if (!grepl("[.!?]$", t)) paste0(t, ".") else t
  } else ""

  journal_part <- ""
  if (has(journal)) {
    journal_part <- trimws(journal)
    if (has(volume)) {
      journal_part <- paste0(journal_part, ", ", volume)
      if (has(issue)) journal_part <- paste0(journal_part, "(", issue, ")")
    }
    if (has(pages)) journal_part <- paste0(journal_part, ", ", pages)
    journal_part <- paste0(journal_part, ".")
  }

  doi_part <- if (has(doi)) {
    d <- sub("^https?://(dx\\.)?doi\\.org/", "", doi)
    paste0("https://doi.org/", d)
  } else ""

  pieces <- c(paste(author_part, year_part), title_part, journal_part, doi_part)
  pieces <- pieces[nzchar(pieces)]
  paste(pieces, collapse = " ")
}

# Build a BibTeX entry from structured fields. Returns NA if neither author nor
# title is available.
format_bibtex_from_fields <- function(authors_json = NA_character_,
                                      year = NA,
                                      title = NA_character_,
                                      journal = NA_character_,
                                      volume = NA_character_,
                                      issue = NA_character_,
                                      pages = NA_character_,
                                      doi = NA_character_,
                                      key = NULL) {
  has <- function(x) !is.null(x) && length(x) == 1 && !is.na(x) && nzchar(trimws(as.character(x)))

  authors <- format_bibtex_authors(authors_json)
  if (!has(authors) && !has(title)) return(NA_character_)

  if (is.null(key) || !has(key)) {
    # Best-effort key: first author family + year
    first_family <- NA_character_
    parsed <- .parse_authors_json(authors_json)
    if (length(parsed) > 0) first_family <- parsed[[1]]$family %||% NA_character_
    if (!has(first_family)) first_family <- "anon"
    yr <- if (has(year)) as.character(year) else "n.d."
    key <- tolower(paste0(gsub("[^A-Za-z0-9]", "", first_family), yr))
  }

  fields <- c()
  add <- function(name, value) {
    if (has(value)) fields[[length(fields) + 1]] <<-
      sprintf("  %s = {%s}", name, value)
  }
  add("author",  authors)
  add("title",   title)
  add("journal", journal)
  add("year",    if (has(year)) as.character(year) else NA_character_)
  add("volume",  volume)
  add("number",  issue)
  add("pages",   pages)
  if (has(doi)) {
    d <- sub("^https?://(dx\\.)?doi\\.org/", "", doi)
    add("doi", d)
  }

  entry_type <- if (has(journal)) "article" else "misc"
  body <- paste(unlist(fields), collapse = ",\n")
  sprintf("@%s{%s,\n%s\n}", entry_type, key, body)
}

#' Final-fallback synthesiser for APA + BibTeX strings.
#'
#' For rows where `ref_{side}_clean` and/or `bibtex_{side}` are still missing,
#' but enough structured fields (author/title/year/journal/...) are available,
#' build the strings with `format_apa_from_fields()` / `format_bibtex_from_fields()`.
#' Run after all upstream sources (CrossRef, DataCite, manual_references,
#' OpenAlex) have had their chance to fill the canonical ref columns.
synthesise_missing_refs_from_fields <- function(data,
                                                 side = c("r", "o"),
                                                 verbose = TRUE) {
  stopifnot(is.data.frame(data))
  side <- match.arg(side)

  col_apa     <- paste0("ref_", side, "_clean")
  col_bibtex  <- paste0("bibtex_", side)
  col_title   <- paste0("title_", side)
  col_author  <- paste0("author_", side)
  col_journal <- paste0("journal_", side)
  col_year    <- paste0("year_", side)
  col_volume  <- paste0("volume_", side)
  col_issue   <- paste0("issue_", side)
  col_pages   <- paste0("pages_", side)
  col_doi     <- paste0("doi_", side)

  has_text_vec <- function(x) {
    x <- trimws(as.character(x))
    !is.na(x) & nzchar(x)
  }
  for (nm in c(col_apa, col_bibtex)) {
    if (!nm %in% names(data)) data[[nm]] <- NA_character_
  }

  get_col <- function(nm, i) if (nm %in% names(data)) data[[nm]][i] else NA

  need_idx <- which(!has_text_vec(data[[col_apa]]) |
                     !has_text_vec(data[[col_bibtex]]))
  apa_built <- 0L
  bib_built <- 0L
  for (i in need_idx) {
    if (!has_text_vec(data[[col_apa]][i])) {
      apa <- format_apa_from_fields(
        authors_json = get_col(col_author, i),
        year         = get_col(col_year, i),
        title        = get_col(col_title, i),
        journal      = get_col(col_journal, i),
        volume       = get_col(col_volume, i),
        issue        = get_col(col_issue, i),
        pages        = get_col(col_pages, i),
        doi          = get_col(col_doi, i)
      )
      if (!is.na(apa) && nzchar(apa)) {
        data[[col_apa]][i] <- apa
        apa_built <- apa_built + 1L
      }
    }
    if (!has_text_vec(data[[col_bibtex]][i])) {
      bib <- format_bibtex_from_fields(
        authors_json = get_col(col_author, i),
        year         = get_col(col_year, i),
        title        = get_col(col_title, i),
        journal      = get_col(col_journal, i),
        volume       = get_col(col_volume, i),
        issue        = get_col(col_issue, i),
        pages        = get_col(col_pages, i),
        doi          = get_col(col_doi, i)
      )
      if (!is.na(bib) && nzchar(bib)) {
        data[[col_bibtex]][i] <- bib
        bib_built <- bib_built + 1L
      }
    }
  }
  if (verbose) {
    message("=== Field-based ref fallback (", toupper(side), " side) ===\n",
            "Candidate rows       : ", length(need_idx), "\n",
            "APA refs synthesised : ", apa_built, "\n",
            "BibTeX synthesised   : ", bib_built)
  }
  data
}

authors_to_crossref_json <- function(author) {
 # rcrossref returns author as a list containing a tibble (from list-column)
  if (is.null(author) || length(author) == 0) return(NA_character_)

 # If author is a list containing a data frame, extract it
  if (is.list(author) && !is.data.frame(author) && length(author) >= 1) {
    author <- author[[1]]
  }

  if (!is.data.frame(author) || nrow(author) == 0) return(NA_character_)

  df <- as_tibble(author)
  keep <- intersect(c("given", "family", "sequence", "ORCID", "affiliation"), names(df))
  if (length(keep) == 0) return(NA_character_)
  df <- df[, keep, drop = FALSE]
  as.character(toJSON(df, auto_unbox = TRUE, null = "null"))
}

# ---- cache I/O ----
load_ref_fields_cache <- function(cache_file = CROSSREF_REF_FIELDS_CACHE) {
  if (!file.exists(cache_file)) {
    return(tibble(
      doi = character(),
      title = character(),
      authors_json = character(),
      journal = character(),
      year = integer(),
      volume = character(),
      issue = character(),
      pages = character(),
      source = character()
    ))
  }
  readRDS(cache_file)
}

save_ref_fields_cache <- function(cache_df, cache_file = CROSSREF_REF_FIELDS_CACHE) {
  saveRDS(cache_df, cache_file)
}

# ---- CrossRef parsing (for rcrossref tibble format) ----
# cr_works()$data returns a tibble with dot-separated column names
# and dates as character strings like "2023-08-16"

extract_year_crossref <- function(x) {
  # rcrossref returns dates as character strings like "2023-08-16" or "2023"
  for (field in c("issued", "published.print", "published.online", "created")) {
    val <- x[[field]]
    if (!is.null(val) && length(val) > 0 && !all(is.na(val))) {
      val <- val[1]
      if (!is.na(val) && nzchar(val)) {
        # Extract year (first 4 digits)
        year <- suppressWarnings(as.integer(substr(as.character(val), 1, 4)))
        if (!is.na(year) && year > 1800 && year < 2100) {
          return(year)
        }
      }
    }
  }
  NA_integer_
}

extract_pages_crossref <- function(x) {
  page <- x$page %||% NA_character_
  page <- page[1]
  if (is.na(page) || !nzchar(page)) return(NA_character_)
  page <- str_squish(as.character(page))
  if (!nzchar(page)) NA_character_ else page
}

extract_journal_crossref <- function(x) {
 # rcrossref uses container.title (with dot, not hyphen)
  jt <- x$container.title %||% x$short.container.title %||% character(0)
  if (length(jt) >= 1 && !is.na(jt[1]) && nzchar(jt[1])) {
    return(str_squish(jt[1]))
  }
  NA_character_
}

extract_title_crossref <- function(x) {
  t <- x$title %||% character(0)
  if (length(t) >= 1 && !is.na(t[1]) && nzchar(t[1])) {
    return(str_squish(t[1]))
  }
  NA_character_
}

extract_volume_issue_crossref <- function(x) {
 # Use [[ ]] to avoid partial matching (x$issue would match x$issued)
  vol <- x[["volume"]] %||% NA_character_
  iss <- x[["issue"]] %||% NA_character_
  vol <- vol[1]
  iss <- iss[1]
  vol <- if (!is.na(vol) && nzchar(vol)) as.character(vol) else NA_character_
  iss <- if (!is.na(iss) && nzchar(iss)) as.character(iss) else NA_character_
  list(volume = vol, issue = iss)
}

parse_crossref_work_to_fields <- function(work) {
  vi <- extract_volume_issue_crossref(work)
  tibble(
    title = extract_title_crossref(work),
    authors_json = authors_to_crossref_json(work$author),
    journal = extract_journal_crossref(work),
    year = extract_year_crossref(work),
    volume = vi$volume,
    issue = vi$issue,
    pages = extract_pages_crossref(work)
  )
}

# ---- CrossRef fetch + cache (fields) ----
get_crossref_reference_fields <- function(dois,
                                          cache_file = CROSSREF_REF_FIELDS_CACHE,
                                          manual_refs = MANUAL_REFERENCES,
                                          progress = TRUE) {
  dois <- tolower(trimws(dois))
  out <- tibble(doi = dois)

  # Load manual references (highest priority)
  manual_refs_df <- load_manual_references(manual_refs)


  # Process manual overrides - extract fields from BibTeX if not provided
  manual_fields <- NULL

  if (nrow(manual_refs_df) > 0) {
    # Filter to only keys that are in the requested DOIs
    manual_subset <- manual_refs_df %>%
      mutate(key = tolower(key)) %>%
      filter(key %in% dois)

    if (nrow(manual_subset) > 0) {
      # Helper to check if value is non-empty (safe for NA)
      has_value <- function(x) isTRUE(!is.na(x) && nzchar(as.character(x)))

      manual_fields <- manual_subset %>%
        rowwise() %>%
        mutate(
          # Parse BibTeX if individual fields not provided
          .parsed = list(if (has_value(reference_bibtex)) {
            parse_bibtex_fields(reference_bibtex)
          } else {
            list(title = NA_character_, author = NA_character_, journal = NA_character_,
                 year = NA_integer_, volume = NA_character_, issue = NA_character_, pages = NA_character_)
          }),
          # Use explicit fields if provided, otherwise fall back to parsed BibTeX
          title = if (has_value(title)) title else .parsed$title,
          authors_json = if (has_value(author)) author else .parsed$author,
          journal = if (has_value(journal)) journal else .parsed$journal,
          year = if (has_value(as.character(year))) as.integer(year) else .parsed$year,
          volume = if (has_value(volume)) volume else .parsed$volume,
          issue = if (has_value(issue)) issue else .parsed$issue,
          pages = if (has_value(pages)) pages else .parsed$pages,
          source = "manual"
        ) %>%
        ungroup() %>%
        select(doi = key, title, authors_json, journal, year, volume, issue, pages, source)
    }
  }

  # Determine which DOIs still need fetching
  valid <- !is.na(dois) & is_doi(dois)
  unique_dois <- unique(dois[valid])

  # Exclude DOIs that have manual overrides
  manual_dois <- if (!is.null(manual_fields)) manual_fields$doi else character(0)
  unique_dois <- setdiff(unique_dois, manual_dois)

  cache_df <- load_ref_fields_cache(cache_file)
  # Retry transient CrossRef failures (rate limit, timeout) but not permanent
  # ones (not_found) or DOIs already resolved via DataCite
  cached_ok <- cache_df %>% filter(source != "crossref_error") %>% pull(doi) %||% character(0)
  need <- setdiff(unique_dois, cached_ok)

  if (length(need) > 0) {
    if (progress) message("Fetching CrossRef reference fields for ", length(need), " DOI(s)...")

    pb <- NULL
    if (progress) {
      pb <- utils::txtProgressBar(min = 0, max = length(need), style = 3)
      on.exit(if (!is.null(pb)) close(pb), add = TRUE)
    }

    new_rows <- vector("list", length(need))
    names(new_rows) <- need

    # Classify rcrossref warnings so HTTP 429 / 5xx / timeouts are treated as
    # transient (retry next run) rather than permanent not-found. rcrossref
    # emits warnings of the form "429 (client error): /works/... -" for these
    # HTTP failures and returns empty data, which the previous code
    # misclassified as crossref_not_found.
    classify_http_warning <- function(msg) {
      if (grepl("^\\s*404\\b", msg)) return("not_found")
      if (grepl("^\\s*(429|5\\d{2})\\b", msg)) return("transient")
      if (grepl("timeout|timed out|connection|temporarily|curl|could not resolve",
                msg, ignore.case = TRUE)) return("transient")
      "other"
    }

    for (i in seq_along(need)) {
      d <- need[[i]]

      http_status <- "unknown"
      w <- NULL
      err_msg <- NA_character_

      tryCatch(
        withCallingHandlers(
          {
            w <- cr_works(dois = d)$data
          },
          warning = function(wn) {
            kind <- classify_http_warning(conditionMessage(wn))
            if (kind != "other") http_status <<- kind
            invokeRestart("muffleWarning")
          }
        ),
        error = function(e) {
          err_msg <<- e$message
          kind <- classify_http_warning(e$message)
          http_status <<- if (kind == "other") "error" else kind
        }
      )

      row <- if (http_status == "transient") {
        tibble(
          doi = d, title = NA_character_, authors_json = NA_character_,
          journal = NA_character_, year = NA_integer_,
          volume = NA_character_, issue = NA_character_, pages = NA_character_,
          source = "crossref_error"
        )
      } else if (http_status == "not_found" || !is.data.frame(w) || nrow(w) < 1) {
        tibble(
          doi = d, title = NA_character_, authors_json = NA_character_,
          journal = NA_character_, year = NA_integer_,
          volume = NA_character_, issue = NA_character_, pages = NA_character_,
          source = if (http_status == "error") "crossref_error" else "crossref_not_found"
        )
      } else {
        work <- as.list(w[1, ])
        parse_crossref_work_to_fields(work) %>%
          mutate(doi = d, source = "crossref") %>%
          select(doi, title, authors_json, journal, year, volume, issue, pages, source)
      }

      new_rows[[d]] <- row
      if (!is.null(pb)) utils::setTxtProgressBar(pb, i)

      # Small delay to reduce rate limiting (CrossRef polite pool allows ~50 rps;
      # a 50 ms pause gives us headroom without materially slowing the pipeline)
      Sys.sleep(0.05)
    }

    new_df <- bind_rows(new_rows)

    cache_df <- cache_df %>%
      filter(!doi %in% new_df$doi) %>%
      bind_rows(new_df)

    save_ref_fields_cache(cache_df, cache_file)
  }

  # Combine: manual overrides take priority, then cache
  result <- out %>%
    left_join(cache_df, by = "doi") %>%
    select(doi, title, authors_json, journal, year, volume, issue, pages, source)

  # Override with manual fields where available
 if (!is.null(manual_fields) && nrow(manual_fields) > 0) {
  # rows_update() requires unique keys in y (manual_fields$doi)
  manual_fields <- manual_fields %>%
    mutate(doi = tolower(trimws(doi))) %>%
    group_by(doi) %>%
    summarise(
      title = first(na.omit(title)),
      authors_json = first(na.omit(authors_json)),
      journal = first(na.omit(journal)),
      year = first(na.omit(year)),
      volume = first(na.omit(volume)),
      issue = first(na.omit(issue)),
      pages = first(na.omit(pages)),
      source = "manual",
      .groups = "drop"
    )
    result <- result %>%
      rows_update(manual_fields, by = "doi", unmatched = "ignore")
  }

  result
}

get_datacite_reference_fields <- function(dois,
                                          cache_file = CROSSREF_REF_FIELDS_CACHE,
                                          progress = TRUE) {

  dois <- tolower(trimws(dois))
  out <- tibble(doi = dois)

  valid <- !is.na(dois) & is_doi(dois)
  unique_dois <- unique(dois[valid])

  cache_df <- load_ref_fields_cache(cache_file)
  # Only skip DOIs already tried via DataCite — crossref_error entries should NOT

  # block DataCite lookups (they share the same cache file)
  dc_tried <- cache_df %>%
    filter(doi %in% unique_dois, source %in% c("datacite", "datacite_error")) %>%
    pull(doi)
  need <- setdiff(unique_dois, dc_tried)

  if (length(need) > 0) {
    if (progress) message("Fetching DataCite reference fields for ", length(need), " DOI(s)...")

    pb <- NULL
    if (progress) {
      pb <- utils::txtProgressBar(min = 0, max = length(need), style = 3)
      on.exit(if (!is.null(pb)) close(pb), add = TRUE)
    }

    new_rows <- vector("list", length(need))
    names(new_rows) <- need

    for (i in seq_along(need)) {
      d <- need[[i]]

      row <- tryCatch({
        url <- glue("https://api.datacite.org/dois/{URLencode(d, reserved = TRUE)}")
        res <- GET(url, timeout(10))
        if (status_code(res) != 200) stop("DataCite status ", status_code(res))

        j <- fromJSON(content(res, "text", encoding = "UTF-8"))
        attr <- j$data$attributes

        # title
        title <- NA_character_
        if (is.data.frame(attr$titles) && nrow(attr$titles) > 0) {
          title <- attr$titles$title[1] %||% NA_character_
        } else if (is.list(attr$titles) && length(attr$titles) > 0) {
          title <- attr$titles[[1]]$title %||% attr$titles[[1]] %||% NA_character_
        }
        title <- if (!is.na(title) && nzchar(title)) str_squish(title) else NA_character_

        # journal-ish container (best-effort; DataCite is heterogeneous)
        journal <- attr$container$title %||% attr$`container-title` %||% attr$publisher %||% NA_character_
        journal <- if (!is.na(journal) && nzchar(journal)) str_squish(journal) else NA_character_

        # year
        year <- suppressWarnings(as.integer(attr$publicationYear %||% NA))

        # pages (often absent in DataCite)
        pages <- NA_character_

        # AUTHORS AS JSON (convert DataCite creators to CrossRef-like shape)
        creators <- attr$creators %||% list()
        authors_json <- NA_character_
        if (is.data.frame(creators) && nrow(creators) > 0) {
          given <- creators$givenName %||% rep(NA_character_, nrow(creators))
          family <- creators$familyName %||% rep(NA_character_, nrow(creators))
          name <- creators$name %||% rep(NA_character_, nrow(creators))

          # if family missing but "name" present, attempt split on last space
          family2 <- ifelse(!is.na(family) & nzchar(family), family, NA_character_)
          given2  <- ifelse(!is.na(given) & nzchar(given), given, NA_character_)

          need_split <- (is.na(family2) | !nzchar(family2)) & !is.na(name) & nzchar(name)
          if (any(need_split)) {
            parts <- strsplit(name[need_split], "\\s+")
            fam_guess <- vapply(parts, function(p) if (length(p) >= 1) p[[length(p)]] else NA_character_, character(1))
            giv_guess <- vapply(parts, function(p) if (length(p) >= 2) paste(p[1:(length(p)-1)], collapse = " ") else NA_character_, character(1))
            family2[need_split] <- fam_guess
            given2[need_split] <- giv_guess
          }

          # sequence: first/additional
          seqv <- rep("additional", nrow(creators))
          if (nrow(creators) >= 1) seqv[1] <- "first"

          df_json <- tibble(
            given = given2,
            family = family2,
            sequence = seqv
          ) %>%
            mutate(
              given = na_if(given, ""),
              family = na_if(family, "")
            )

          authors_json <- as.character(toJSON(df_json, auto_unbox = TRUE, null = "null"))
        }

        tibble(
          doi = d,
          title = title,
          authors_json = authors_json,
          journal = journal,
          year = year,
          volume = NA_character_,
          issue = NA_character_,
          pages = pages,
          source = "datacite"
        )
      }, error = function(e) {
        tibble(
          doi = d,
          title = NA_character_,
          authors_json = NA_character_,
          journal = NA_character_,
          year = NA_integer_,
          volume = NA_character_,
          issue = NA_character_,
          pages = NA_character_,
          source = "datacite_error"
        )
      })

      new_rows[[d]] <- row
      if (!is.null(pb)) utils::setTxtProgressBar(pb, i)
    }

    new_df <- bind_rows(new_rows)

    cache_df <- cache_df %>%
      filter(!doi %in% new_df$doi) %>%
      bind_rows(new_df)

    save_ref_fields_cache(cache_df, cache_file)
  }

  out %>%
    left_join(cache_df, by = "doi") %>%
    select(doi, title, authors_json, journal, year, volume, issue, pages, source)
}

# Fetch reference fields via DOI content negotiation (doi.org CSL-JSON).
# Works for any DOI whose registration agency supports CSL-JSON content
# negotiation — covering CrossRef, DataCite, Airiti, mEDRA, and others — so
# this serves as a universal fallback after the CrossRef + DataCite paths.
get_doi_content_negotiation_fields <- function(dois,
                                               cache_file = CROSSREF_REF_FIELDS_CACHE,
                                               progress = TRUE) {
  dois <- tolower(trimws(dois))
  out <- tibble(doi = dois)

  valid <- !is.na(dois) & is_doi(dois)
  unique_dois <- unique(dois[valid])

  cache_df <- load_ref_fields_cache(cache_file)

  # Skip DOIs already attempted via content negotiation (success or permanent
  # failure). Retryable outcomes use `doi_cn_error` so they get tried again.
  cn_tried <- cache_df %>%
    filter(doi %in% unique_dois, source %in% c("doi_cn", "doi_cn_not_found")) %>%
    pull(doi)
  need <- setdiff(unique_dois, cn_tried)

  if (length(need) == 0) {
    return(out %>%
             left_join(cache_df, by = "doi") %>%
             select(doi, title, authors_json, journal, year, volume, issue, pages, source))
  }

  if (progress) message("Fetching DOI content-negotiation fields for ", length(need), " DOI(s)...")

  pb <- NULL
  if (progress) {
    pb <- utils::txtProgressBar(min = 0, max = length(need), style = 3)
    on.exit(if (!is.null(pb)) close(pb), add = TRUE)
  }

  empty_row <- function(d, src) {
    tibble(doi = d, title = NA_character_, authors_json = NA_character_,
           journal = NA_character_, year = NA_integer_,
           volume = NA_character_, issue = NA_character_, pages = NA_character_,
           source = src)
  }

  authors_to_crossref_json <- function(author_list) {
    if (is.null(author_list) || length(author_list) == 0) return(NA_character_)
    df <- tibble(
      given = vapply(author_list,
                     function(a) a$given %||% NA_character_, character(1)),
      family = vapply(author_list,
                      function(a) a$family %||% a$literal %||% NA_character_, character(1)),
      sequence = c("first", rep("additional", max(0, length(author_list) - 1)))
    ) %>%
      mutate(given = na_if(given, ""), family = na_if(family, ""))
    as.character(toJSON(df, auto_unbox = TRUE, null = "null"))
  }

  new_rows <- vector("list", length(need))
  names(new_rows) <- need

  for (i in seq_along(need)) {
    d <- need[[i]]
    url <- paste0("https://doi.org/", URLencode(d, reserved = TRUE))
    row <- tryCatch({
      res <- GET(url,
                 add_headers(Accept = "application/vnd.citationstyles.csl+json"),
                 timeout(12))
      sc <- status_code(res)
      body <- content(res, as = "text", encoding = "UTF-8")

      # doi.org returns its "DOI not found" HTML page (200 OK with text/html)
      # for unregistered DOIs. Detect by content-type or parseability.
      ct <- tolower(headers(res)[["content-type"]] %||% "")
      if (sc >= 400 || grepl("html", ct) || !startsWith(trimws(body), "{")) {
        empty_row(d, "doi_cn_not_found")
      } else {
        j <- tryCatch(fromJSON(body, simplifyVector = FALSE),
                      error = function(e) NULL)
        if (is.null(j)) {
          empty_row(d, "doi_cn_not_found")
        } else {
          title <- j$title %||% NA_character_
          if (!is.na(title) && nzchar(title)) title <- str_squish(title)
          journal <- j$`container-title` %||% j$publisher %||% NA_character_
          if (!is.na(journal) && nzchar(journal)) journal <- str_squish(journal)
          year <- suppressWarnings(as.integer(
            j$issued$`date-parts`[[1]][[1]] %||% NA))
          volume <- j$volume %||% NA_character_
          issue <- j$issue %||% NA_character_
          pages <- j$page %||% j$`page-first` %||% NA_character_
          authors_json <- authors_to_crossref_json(j$author)

          tibble(doi = d, title = title, authors_json = authors_json,
                 journal = journal, year = year,
                 volume = as.character(volume),
                 issue = as.character(issue),
                 pages = as.character(pages),
                 source = "doi_cn")
        }
      }
    }, error = function(e) empty_row(d, "doi_cn_error"))

    new_rows[[d]] <- row
    if (!is.null(pb)) utils::setTxtProgressBar(pb, i)
    Sys.sleep(0.1)  # polite pacing
  }

  new_df <- bind_rows(new_rows)

  cache_df <- cache_df %>%
    filter(!doi %in% new_df$doi) %>%
    bind_rows(new_df)

  save_ref_fields_cache(cache_df, cache_file)

  out %>%
    left_join(cache_df, by = "doi") %>%
    select(doi, title, authors_json, journal, year, volume, issue, pages, source)
}

#' Get APA and/or BibTeX references for DOIs/keys
#' Four-tier lookup: manual overrides → cache → CrossRef → DataCite
#' Note: API queries (CrossRef/DataCite) only happen for valid DOIs.
#' Non-DOI keys can still be resolved via manual_refs.
#'
#' @param doi_vec Vector of DOI strings (or other keys for manual lookup)
#' @param cache Path to cache file (default from cache_config)
#' @param manual_refs Path to manual references file
#' @param format Which format(s) to return: "both" (default), "apa", or "bibtex"
#' @param return_source If TRUE, include source columns
#' @param progress If TRUE, show progress messages
#' @return Tibble with: doi, reference_apa and/or reference_bibtex, [source cols]
get_references <- function(doi_vec,
                           cache = CROSSREF_CITATIONS_CACHE,
                           manual_refs = MANUAL_REFERENCES,
                           format = c("both", "apa", "bibtex"),
                           return_source = FALSE,
                           include_fields = TRUE,
                           fields_cache = CROSSREF_REF_FIELDS_CACHE,
                           progress = TRUE) {

  fields_fallback_datacite <- TRUE # whether to fallback to DataCite for missing fields, no longer exposed as argument
  format <- match.arg(format)
  cache_path <- cache

  # Load manual references (highest priority)
  manual_refs_df <- load_manual_references(manual_refs)

  cache_exists <- file.exists(cache_path)
  if (progress) {
    message(if (cache_exists) {
      sprintf("Using existing cache at %s", cache_path)
    } else {
      sprintf("Initializing new cache at %s", cache_path)
    })
  }

  # Load or initialize cache
  cache_obj <- load_citation_cache(cache_path)

  # Normalize keys to lowercase for consistent caching
  doi_vec <- tolower(doi_vec)
  unique_keys <- unique(doi_vec)
  n_unique <- length(unique_keys)

  # DOI validation helper
  is_doi <- function(x) grepl("^10\\.[0-9]{4,}/\\S+$", x)

  # Track where each reference came from (separate for apa/bibtex)
  sources_apa <- character(length(unique_keys))
  sources_bibtex <- character(length(unique_keys))
  names(sources_apa) <- unique_keys
  names(sources_bibtex) <- unique_keys

  # Progress bar
  pb <- NULL
  if (progress && n_unique > 0) {
    pb <- utils::txtProgressBar(min = 0, max = n_unique, style = 3)
    on.exit(close(pb), add = TRUE)
  }

  # Loop through unique keys
  for (i in seq_along(unique_keys)) {
    d <- unique_keys[i]

    # === APA ===
    if (format %in% c("both", "apa")) {
      # 1. Check manual references (works for any key, not just DOIs)
      manual_apa <- manual_refs_df %>%
        filter(tolower(key) == d) %>%
        pull(reference_apa) %>%
        first()

      if (!is.na(manual_apa) && nzchar(manual_apa)) {
        cache_obj$apa[[d]] <- manual_apa
        sources_apa[i] <- "manual"
      } else {
        # 2. Check cache
        apa_cached <- cache_obj$apa[[d]]

        if (!is.null(apa_cached) && !is.na(apa_cached[1]) && nzchar(apa_cached[1])) {
          sources_apa[i] <- "cached"
        } else if (!is_doi(d)) {
          # Not a DOI, can't query APIs
          cache_obj$apa[[d]] <- NA_character_
          sources_apa[i] <- "not_found"
        } else {
          # 3. Try CrossRef
          if (progress) {
            message(sprintf("(%d/%d) Fetching citations for %s", i, n_unique, d))
          }
          apa_cr <- get_crossref_citation(d, "apa")

          if (!is.na(apa_cr) && length(apa_cr) > 0 && nzchar(apa_cr)) {
            cache_obj$apa[[d]] <- apa_cr
            sources_apa[i] <- "crossref"
          } else {
            # 4. Fallback to DataCite
            apa_dc <- get_datacite_apa(d)
            if (!is.na(apa_dc) && nzchar(apa_dc)) {
              cache_obj$apa[[d]] <- apa_dc
              sources_apa[i] <- "datacite"
            } else {
              cache_obj$apa[[d]] <- NA_character_
              sources_apa[i] <- "not_found"
            }
          }
        }
      }
    }

    # === BibTeX ===
    if (format %in% c("both", "bibtex")) {
      # 1. Check manual references
      manual_bib <- manual_refs_df %>%
        filter(tolower(key) == d) %>%
        pull(reference_bibtex) %>%
        first()

      if (!is.na(manual_bib) && nzchar(manual_bib)) {
        cache_obj$bibtex[[d]] <- manual_bib
        sources_bibtex[i] <- "manual"
      } else {
        # 2. Check cache
        bib_cached <- cache_obj$bibtex[[d]]

        if (!is.null(bib_cached) && !is.na(bib_cached[1]) && nzchar(bib_cached[1])) {
          sources_bibtex[i] <- "cached"
        } else if (!is_doi(d)) {
          cache_obj$bibtex[[d]] <- NA_character_
          sources_bibtex[i] <- "not_found"
        } else {
          # 3. Try CrossRef
          bib_cr <- get_crossref_citation(d, "bibtex")

          if (!is.na(bib_cr) && length(bib_cr) > 0 && nzchar(bib_cr)) {
            cache_obj$bibtex[[d]] <- bib_cr
            sources_bibtex[i] <- "crossref"
          } else {
            # 4. Fallback to DataCite
            bib_dc <- get_datacite_bibtex(d)
            if (!is.na(bib_dc) && nzchar(bib_dc)) {
              cache_obj$bibtex[[d]] <- bib_dc
              sources_bibtex[i] <- "datacite"
            } else {
              cache_obj$bibtex[[d]] <- NA_character_
              sources_bibtex[i] <- "not_found"
            }
          }
        }
      }
    }

    if (!is.null(pb)) {
      utils::setTxtProgressBar(pb, i)
    }
  }

  # Save updated cache to disk
  save_citation_cache(cache_obj, cache_path)

  if (progress && n_unique > 0) {
    message("Reference retrieval complete.")
  }

  # Map back to original order and build result tibble
  result <- tibble(doi = doi_vec)

  if (format %in% c("both", "apa")) {
    apa_final <- vapply(doi_vec, function(d) {
      ref <- cache_obj$apa[[d]]
      if (is.null(ref) || length(ref) != 1 || is.na(ref[1]) || !nzchar(ref[1])) {
        NA_character_
      } else {
        ref
      }
    }, character(1))

    if (format == "apa") {
      result$reference <- apa_final
    } else {
      result$reference_apa <- apa_final
    }

    if (return_source) {
      src_apa <- vapply(doi_vec, function(d) sources_apa[d], character(1))
      if (format == "apa") {
        result$source <- src_apa
      } else {
        result$source_apa <- src_apa
      }
    }
  }

  if (format %in% c("both", "bibtex")) {
    bib_final <- vapply(doi_vec, function(d) {
      ref <- cache_obj$bibtex[[d]]
      if (is.null(ref) || length(ref) != 1 || is.na(ref[1]) || !nzchar(ref[1])) {
        NA_character_
      } else {
        ref
      }
    }, character(1))

    if (format == "bibtex") {
      result$reference <- bib_final
    } else {
      result$reference_bibtex <- bib_final
    }

    if (return_source) {
      src_bib <- vapply(doi_vec, function(d) sources_bibtex[d], character(1))
      if (format == "bibtex") {
        result$source <- src_bib
      } else {
        result$source_bibtex <- src_bib
      }
    }
  }

  if (include_fields) {
    fields_cr <- get_crossref_reference_fields(
      doi_vec,
      cache_file = fields_cache,
      progress = progress
    )

    fields_out <- fields_cr

    if (fields_fallback_datacite) {
      miss <- fields_out %>%
        mutate(.miss = (is.na(title) | !nzchar(title)) |
                 (is.na(authors_json) | !nzchar(authors_json)) |
                 (is.na(journal) | !nzchar(journal)) |
                 is.na(year) |
                 (is.na(volume) | !nzchar(volume)) |
                 (is.na(issue) | !nzchar(issue)) |
                 (is.na(pages) | !nzchar(pages))) %>%
        filter(.miss & !is.na(doi) & is_doi(doi)) %>%
        distinct(doi) %>%
        pull(doi)

      if (length(miss) > 0) {
        fields_dc <- get_datacite_reference_fields(
          miss,
          cache_file = fields_cache,
          progress = progress
        )

        fields_out <- fields_out %>%
          left_join(fields_dc, by = "doi", suffix = c("", "_dc")) %>%
          transmute(
            doi,
            title  = coalesce(title, title_dc),
            authors_json = coalesce(authors_json, authors_json_dc),
            journal = coalesce(journal, journal_dc),
            year   = coalesce(year, year_dc),
            volume = coalesce(volume, volume_dc),
            issue  = coalesce(issue, issue_dc),
            pages  = coalesce(pages, pages_dc),
            source = coalesce(source, source_dc)
          )

        save_ref_fields_cache(
          load_ref_fields_cache(fields_cache) %>%
            filter(!doi %in% fields_out$doi) %>%
            bind_rows(fields_out),
          fields_cache
        )
      }

      # Third fallback: DOI content negotiation (covers non-CrossRef/non-DataCite
      # registries such as Airiti, mEDRA, and other CSL-JSON-supporting agents).
      miss2 <- fields_out %>%
        mutate(.miss = is.na(title) | !nzchar(title)) %>%
        filter(.miss & !is.na(doi) & is_doi(doi)) %>%
        distinct(doi) %>%
        pull(doi)

      if (length(miss2) > 0) {
        fields_cn <- get_doi_content_negotiation_fields(
          miss2,
          cache_file = fields_cache,
          progress = progress
        )

        fields_out <- fields_out %>%
          left_join(fields_cn, by = "doi", suffix = c("", "_cn")) %>%
          transmute(
            doi,
            title  = coalesce(title, title_cn),
            authors_json = coalesce(authors_json, authors_json_cn),
            journal = coalesce(journal, journal_cn),
            year   = coalesce(year, year_cn),
            volume = coalesce(volume, volume_cn),
            issue  = coalesce(issue, issue_cn),
            pages  = coalesce(pages, pages_cn),
            source = coalesce(source, source_cn)
          )

        save_ref_fields_cache(
          load_ref_fields_cache(fields_cache) %>%
            filter(!doi %in% fields_out$doi) %>%
            bind_rows(fields_out),
          fields_cache
        )
      }
    }

    result <- result %>%
      left_join(
        fields_out %>% select(doi, title, author = authors_json, journal, year, volume, issue, pages),
        by = "doi"
      )

    # Fallback: recover missing author/year from BibTeX strings
    if ("reference_bibtex" %in% names(result)) {
      has_bib   <- !is.na(result$reference_bibtex) & nzchar(result$reference_bibtex)
      miss_auth <- is.na(result$author) | !nzchar(trimws(result$author))
      miss_year <- is.na(result$year)
      idx <- which((miss_auth | miss_year) & has_bib)
      if (length(idx) > 0) {
        if (progress) message("Recovering missing author/year from BibTeX for ", length(idx), " DOI(s)...")
        for (i in idx) {
          parsed <- parse_bibtex_fields(result$reference_bibtex[i])
          if (miss_auth[i] && !is.na(parsed$author) && nzchar(parsed$author))
            result$author[i] <- parsed$author
          if (miss_year[i] && !is.na(parsed$year))
            result$year[i] <- as.integer(parsed$year)
        }
      }
    }
  }

  result

}

# ============================================================================
# DOI Metadata Caching (from CrossRef and DataCite)
# ============================================================================

#' Load DOI metadata cache
#' @param cache_file Path to cache file
#' @return Tibble with cached DOI metadata
load_doi_cache <- function(cache_file = CROSSREF_DOI_CACHE) {
  if (!file.exists(cache_file)) {
    return(tibble(
      doi = character(),
      title = character(),
      authors = character(),
      year = integer(),
      source = character()
    ))
  }

  readRDS(cache_file)
}

#' Save DOI metadata cache
#' @param cache_df Tibble with DOI metadata
#' @param cache_file Path to save cache
save_doi_cache <- function(cache_df, cache_file = CROSSREF_DOI_CACHE) {
  saveRDS(cache_df, cache_file)
}

# ============================================================================
# Author Caching (from CrossRef)
# ============================================================================

#' Load author cache from Excel
#' @param cache_file Path to cache file
#' @return Tibble with author data
load_author_cache <- function(cache_file = CROSSREF_AUTHORS_CACHE) {
  if (!file.exists(cache_file)) {
    return(tibble(
      doi = character(),
      authors_json = character()
    ))
  }

  read_excel(cache_file)
}

#' Save author cache to Excel
#' @param cache_df Tibble with author data
#' @param cache_file Path to save cache
save_author_cache <- function(cache_df, cache_file = CROSSREF_AUTHORS_CACHE) {
  write.xlsx(cache_df, cache_file, overwrite = TRUE)
}

#' Get CrossRef authors for DOIs with caching
#' @param dois Vector of DOI strings
#' @param cache_file Path to cache file
#' @param batch_size Batch size for API calls
#' @return Tibble with doi and authors_json columns
get_crossref_authors <- function(dois,
                                cache_file = CROSSREF_AUTHORS_CACHE,
                                batch_size = 50) {

  dois <- tolower(trimws(dois))
  unique_dois <- unique(dois[!is.na(dois)])

  # Load existing cache
  cache_df <- load_author_cache(cache_file)
  cached_dois <- if (nrow(cache_df) > 0) cache_df$doi else character(0)

  # Find uncached DOIs
  uncached_dois <- setdiff(unique_dois, cached_dois)

  if (length(uncached_dois) > 0) {
    message("Fetching author data for ", length(uncached_dois), " new DOI(s)...")

    new_results <- tibble(doi = character(), authors_json = character())

    # Fetch in batches
    for (i in seq(1, length(uncached_dois), by = batch_size)) {
      batch <- uncached_dois[i:min(i + batch_size - 1, length(uncached_dois))]
      message(sprintf("  Batch %d-%d", i, min(i + batch_size - 1, length(uncached_dois))))

      for (doi in batch) {
        tryCatch({
          # CrossRef API call
          cr_result <- cr_cn(doi, format = "json")
          authors_json <- toJSON(cr_result$author %||% list())

          new_results <- bind_rows(new_results, tibble(
            doi = doi,
            authors_json = authors_json
          ))
        }, error = function(e) {
          message("    Error fetching ", doi, ": ", e$message)
        })
      }
    }

    # Append to cache and save
    cache_df <- bind_rows(cache_df, new_results)
    save_author_cache(cache_df, cache_file)
  }

  # Return all requested DOIs with their cached author data
  result <- tibble(doi = dois) %>%
    left_join(cache_df, by = "doi")

  result
}

# ============================================================================
# Author Overlap Computation
# ============================================================================

#' Compute author overlap between two studies
#' @param data Tibble with doi_o and doi_r columns
#' @param authors_cache_file Path to author cache
#' @return Tibble with original data plus author_overlap column
compute_author_overlap <- function(data,
                                  authors_cache_file = CROSSREF_AUTHORS_CACHE) {

  # Get all unique DOIs
  unique_dois <- unique(c(data$doi_o, data$doi_r))
  unique_dois <- unique_dois[!is.na(unique_dois)]

  # Fetch author data (with caching)
  authors_df <- get_crossref_authors(unique_dois, authors_cache_file)

  # Function to extract author family names from JSON
  extract_author_names <- function(authors_json) {
    tryCatch({
      if (is.na(authors_json) || authors_json == "[]") {
        return(character(0))
      }
      authors_list <- fromJSON(authors_json)
      if (is.data.frame(authors_list) && "family" %in% names(authors_list)) {
        tolower(authors_list$family)
      } else {
        character(0)
      }
    }, error = function(e) character(0))
  }

  # Compute overlap for each row
  data <- data %>%
    left_join(authors_df %>% rename(authors_json_o = authors_json),
              by = c("doi_o" = "doi")) %>%
    left_join(authors_df %>% rename(authors_json_r = authors_json),
              by = c("doi_r" = "doi")) %>%
    rowwise() %>%
    mutate(
      authors_o = list(extract_author_names(authors_json_o)),
      authors_r = list(extract_author_names(authors_json_r)),
      author_overlap = length(intersect(authors_o, authors_r)),
      # Percentage based on replication authors (measures independence of replication team)
      author_overlap_pct = if (length(authors_r) > 0) {
        round(100 * author_overlap / length(authors_r), 1)
      } else {
        NA_real_
      }
    ) %>%
    ungroup() %>%
    select(-authors_json_o, -authors_json_r, -authors_o, -authors_r)

  data
}


#' Fill missing title/author/year from a hand-written APA reference
#'
#' Rows entered in the FLoRA sheets with a URL but no DOI carry their citation
#' only as free text in `ref_<side>` (e.g. the i4replication reports). No
#' lookup service can give them a title, so parse the APA string itself:
#' `Authors (Year[, Month d]). Title. Rest.` Only rows still lacking a title
#' after the DOI, manual-file and OpenAlex passes are touched, and only when
#' the text has the citation shape (a bare DOI in the reference column is not).
#' Sets `title`, `author` (as the JSON list the rest of the pipeline uses),
#' `year`, and copies the text into `ref_<side>_clean` so it is exported as
#' the APA reference.
#' @return data with the parsed fields filled; prints how many rows were filled
augment_titles_from_apa_text <- function(data, side = c("r", "o"), verbose = TRUE) {
  stopifnot(is.data.frame(data))
  side <- match.arg(side)
  col_ref   <- paste0("ref_", side)
  col_clean <- paste0("ref_", side, "_clean")
  col_title <- paste0("title_", side)
  col_author <- paste0("author_", side)
  col_year  <- paste0("year_", side)
  if (!col_ref %in% names(data)) return(data)
  for (nm in c(col_clean, col_title, col_author)) {
    if (!nm %in% names(data)) data[[nm]] <- NA_character_
  }
  if (!col_year %in% names(data)) data[[col_year]] <- NA_integer_

  has_text_vec <- function(x) { x <- trimws(as.character(x)); !is.na(x) & nzchar(x) }

  parse_one <- function(ref) {
    ref <- str_squish(ref)
    # Authors end at the first "(YYYY" or "(n.d.)" group; the title starts
    # after "). ". A reference with no author is title-first:
    # `Title [Working paper]. (n.d.). URL`.
    m <- str_match(ref, "^(.*?)\\s*\\((\\d{4}|n\\.d\\.)[^)]*\\)\\.?\\s+(.*)$")
    if (is.na(m[1, 1])) return(NULL)
    authors <- m[1, 2]; rest <- m[1, 4]
    year <- suppressWarnings(as.integer(m[1, 3]))
    if (str_detect(authors, "[.!?\\]]$") && str_detect(rest, "^(https?://|doi|10\\.)")) {
      title <- str_squish(str_remove(authors, "\\s*\\[[^]]*\\]\\.?$"))
      title <- str_remove(title, "\\.$")
      if (!nzchar(title)) return(NULL)
      return(list(title = title, year = year, author = NA_character_))
    }
    # Title runs to the first ". " outside double quotes (titles may quote
    # another paper's title, which itself may contain full stops).
    # A closing quote right after a full stop also ends the title, and
    # "et al." does not.
    chars <- str_split(rest, "")[[1]]
    n <- length(chars)
    in_quote <- FALSE; end <- n
    for (i in seq_len(n)) {
      ch <- chars[i]
      if (ch %in% c('"', "“", "”")) {
        in_quote <- !in_quote
        if (!in_quote && i > 1 && chars[i - 1] == "." && (i == n || chars[i + 1] == " ")) { end <- i; break }
        next
      }
      if (in_quote || ch != "." || (i < n && chars[i + 1] != " ")) next
      prev <- paste(chars[seq_len(i - 1)], collapse = "")
      if (str_detect(prev, "\\bet al$")) next
      end <- i - 1; break
    }
    title <- str_squish(paste(chars[seq_len(end)], collapse = ""))
    if (!nzchar(title) || !nzchar(authors)) return(NULL)
    # "Last, F. M., Last, G., & Last, H." -> alternating family / given parts.
    parts <- str_split(str_replace_all(authors, ",?\\s*&\\s*", ", "), ",\\s*")[[1]]
    parts <- parts[nzchar(parts)]
    author_list <- if (length(parts) %% 2 == 0 && length(parts) > 0) {
      lapply(seq(1, length(parts), by = 2), function(j)
        list(family = parts[j], given = parts[j + 1]))
    } else {
      list(list(family = authors, given = ""))
    }
    author_list[[1]]$sequence <- "first"
    if (length(author_list) > 1) for (j in 2:length(author_list)) author_list[[j]]$sequence <- "additional"
    list(title = title, year = year,
         author = as.character(toJSON(author_list, auto_unbox = TRUE)))
  }

  idx <- which(!has_text_vec(data[[col_title]]) & has_text_vec(data[[col_ref]]))
  filled <- 0L
  for (i in idx) {
    p <- parse_one(data[[col_ref]][i])
    if (is.null(p)) next
    data[[col_title]][i] <- p$title
    if (!has_text_vec(data[[col_author]][i]) && !is.na(p$author)) data[[col_author]][i] <- p$author
    if (is.na(data[[col_year]][i]) && !is.na(p$year)) data[[col_year]][i] <- p$year
    if (!has_text_vec(data[[col_clean]][i])) data[[col_clean]][i] <- str_squish(data[[col_ref]][i])
    filled <- filled + 1L
  }
  if (verbose) {
    cat(sprintf("  Parsed title_%s from APA text for %d of %d rows lacking a title\n",
                side, filled, length(idx)))
  }
  data
}
