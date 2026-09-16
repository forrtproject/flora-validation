# Data Augmentation Functions
# Modular functions for augmenting FReD/FLoRA datasets
# Includes: author overlap, reference cleaning, keyword fetching

library(dplyr)
library(jsonlite)
library(rcrossref)


# ============================================================================
# Author Overlap Augmentation
# ============================================================================

#' Augment dataset with author overlap information
#' Computes author overlap between original and replication studies
#'
#' @param data Tibble with at least doi_o and doi_r columns
#' @param add_pct_column If TRUE, add author_overlap_pct column
#' @return Original data with added author_overlap column(s)
augment_with_author_overlap <- function(data, add_pct_column = TRUE) {

  message("\n=== Augmenting with Author Overlap ===")

  if (!"doi_o" %in% names(data) || !"doi_r" %in% names(data)) {
    warning("Data must have 'doi_o' and 'doi_r' columns. Skipping author overlap augmentation.")
    return(data)
  }

  # Get unique DOI pairs
  doi_pairs <- data %>%
    select(doi_o, doi_r) %>%
    distinct() %>%
    filter(!is.na(doi_o) & !is.na(doi_r))

  message("Found ", nrow(doi_pairs), " unique (doi_o, doi_r) pairs")

  # Compute author overlap
  message("Fetching author data from CrossRef...")
  augmented_pairs <- compute_author_overlap(doi_pairs, CROSSREF_AUTHORS_CACHE)

  message("Computing overlaps...")
  overlap_summary <- augmented_pairs %>%
    group_by(author_overlap) %>%
    count(name = "n_pairs") %>%
    arrange(desc(author_overlap))

  message("\nAuthor overlap distribution:")
  for (i in seq_len(nrow(overlap_summary))) {
    msg <- sprintf("  %d authors: %d pairs",
                   overlap_summary$author_overlap[i],
                   overlap_summary$n_pairs[i])
    message(msg)
  }

  # Merge back to original data
  result <- data %>%
    left_join(
      augmented_pairs %>% select(doi_o, doi_r, author_overlap, author_overlap_pct),
      by = c("doi_o", "doi_r")
    )

  # If add_pct_column is FALSE, remove the percentage column
  if (!add_pct_column) {
    result <- result %>% select(-author_overlap_pct)
  }

  message("✓ Author overlap augmentation complete")
  result
}

#' Compute author overlap from existing author_o and author_r JSON columns
#' This fills in author_overlap for rows where it's missing but author data exists
#'
#' @param data Tibble with author_o and author_r columns (JSON format)
#' @return Data with author_overlap filled in where possible
fill_author_overlap_from_columns <- function(data) {

  if (!all(c("author_o", "author_r") %in% names(data))) {
    message("Missing author_o or author_r columns, skipping fill")
    return(data)
  }

  # Initialize columns if not present

if (!"author_overlap" %in% names(data)) {
    data$author_overlap <- NA_integer_
  }
  if (!"author_overlap_pct" %in% names(data)) {
    data$author_overlap_pct <- NA_real_
  }

  # Find rows that need filling (missing overlap but have both author columns)
  needs_fill <- which(
    is.na(data$author_overlap) &
    !is.na(data$author_o) & nzchar(data$author_o) &
    !is.na(data$author_r) & nzchar(data$author_r)
  )

  if (length(needs_fill) == 0) {
    message("No rows need author overlap filling")
    return(data)
  }

  message(sprintf("Filling author overlap for %d rows from existing author columns...", length(needs_fill)))

  # Helper to extract family names from JSON
  extract_family_names <- function(json_str) {
    tryCatch({
      if (is.na(json_str) || !nzchar(json_str) || json_str == "[]") {
        return(character(0))
      }
      authors <- fromJSON(json_str)
      if (is.data.frame(authors) && "family" %in% names(authors)) {
        return(tolower(trimws(authors$family)))
      }
      character(0)
    }, error = function(e) character(0))
  }

  # Compute overlap for each row
  for (i in needs_fill) {
    authors_o <- extract_family_names(data$author_o[i])
    authors_r <- extract_family_names(data$author_r[i])

    if (length(authors_o) > 0 && length(authors_r) > 0) {
      overlap <- length(intersect(authors_o, authors_r))
      data$author_overlap[i] <- overlap
      # Percentage based on replication authors (measures independence of replication team)
      data$author_overlap_pct[i] <- round(100 * overlap / length(authors_r), 1)
    }
  }

  filled <- sum(!is.na(data$author_overlap[needs_fill]))
  message(sprintf("✓ Filled author overlap for %d rows", filled))

  data
}


# ============================================================================
# Text Cleaning (HTML tags, entities, stray linebreaks)
# ============================================================================

#' Strip HTML/JATS tags and decode common HTML entities.
#'
#' Used for fields whose source metadata (CrossRef, OpenAlex, JATS) sometimes
#' carries inline markup like `<i>`, `<sup>`, `<scp>`, `<mml:math>`, or escaped
#' entities like `&amp;`. NA inputs are preserved.
#'
#' @param x Character vector.
#' @return Character vector with tags removed, entities decoded, whitespace
#'   collapsed, and leading/trailing whitespace trimmed.
clean_html_tags <- function(x) {
  if (is.null(x)) return(x)
  x <- as.character(x)
  y <- gsub("<[^>]+>", "", x, perl = TRUE)

  # Decode common HTML entities. Case-insensitive because CrossRef occasionally
  # returns capitalized forms (e.g., `&Amp;`, `&Rsquo;`).
  decode_named <- function(s) {
    s <- gsub("&amp;",   "&",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&lt;",    "<",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&gt;",    ">",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&quot;",  '"',  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&apos;",  "'",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&nbsp;",  " ",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&rsquo;", "'",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&lsquo;", "'",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&rdquo;", '"',  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&ldquo;", '"',  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&ndash;", "-",  s, ignore.case = TRUE, perl = TRUE)
    s <- gsub("&mdash;", "-",  s, ignore.case = TRUE, perl = TRUE)
    s
  }
  # Decode numeric entities (&#NN; and &#xHH;) per element via regmatches.
  decode_numeric_one <- function(s) {
    if (is.na(s)) return(s)
    m <- gregexpr("&#(?:\\d+|x[0-9a-fA-F]+);", s, perl = TRUE)
    toks <- regmatches(s, m)[[1]]
    if (length(toks) == 0) return(s)
    repl <- vapply(toks, function(tok) {
      hex <- grepl("^&#x", tok, ignore.case = TRUE)
      body <- sub("^&#x?", "", tok, ignore.case = TRUE)
      body <- sub(";$", "", body)
      if (hex) intToUtf8(strtoi(body, base = 16L)) else intToUtf8(as.integer(body))
    }, character(1), USE.NAMES = FALSE)
    regmatches(s, m) <- list(repl)
    s
  }

  # Two passes collapse double-encoded `&amp;amp;` → `&amp;` → `&`.
  for (pass in 1:2) {
    y <- decode_named(y)
  }
  y <- vapply(y, decode_numeric_one, character(1), USE.NAMES = FALSE)

  y <- gsub("[ \t]+", " ", y, perl = TRUE)
  trimws(y)
}

#' Collapse stray single linebreaks while preserving paragraph breaks.
#'
#' Many source abstracts and outcome quotes are pasted out of PDFs and contain
#' a hard linebreak after every typeset line, which corrupts downstream display
#' and LLM input. This function joins single linebreaks with a space but keeps
#' double (blank-line) breaks as paragraph separators.
#'
#' @param x Character vector.
#' @return Character vector with cleaned linebreaks.
clean_linebreaks <- function(x) {
  if (is.null(x)) return(x)
  x <- as.character(x)
  y <- gsub("\r\n", "\n", x, fixed = TRUE)
  y <- gsub("\r", "\n", y, fixed = TRUE)
  # Mark paragraph breaks (>=2 newlines, optionally with spaces between) so
  # they survive the single-newline collapse below.
  y <- gsub("\n[ \t]*\n+[ \t]*", "", y, perl = TRUE)
  y <- gsub("\n+", " ", y, perl = TRUE)
  y <- gsub("", "\n\n", y, fixed = TRUE)
  y <- gsub("[ \t]+", " ", y, perl = TRUE)
  y <- gsub(" *\n\n *", "\n\n", y, perl = TRUE)
  trimws(y)
}

#' Apply text-field cleanup to a FLoRA dataframe.
#'
#' Strips HTML tags + entities from titles, references, journals, abstracts,
#' and outcome quotes, and collapses stray single linebreaks in abstracts,
#' outcome quotes, and study_o. Columns missing from `data` are skipped, so
#' the function is safe to call at multiple pipeline stages.
#'
#' @param data Tibble.
#' @param verbose If TRUE, print per-column counts of cells changed.
#' @return Tibble with cleaned text fields.
clean_flora_text_fields <- function(data, verbose = TRUE) {
  html_cols <- c("title_o", "title_r",
                 "journal_o", "journal_r",
                 "ref_o", "ref_r",
                 "ref_o_clean", "ref_r_clean",
                 "abstract_r",
                 "outcome_quote")
  lb_cols   <- c("abstract_r", "outcome_quote", "study_o")

  count_changes <- function(old, new) {
    sum(!is.na(old) & !is.na(new) & old != new) +
      sum(is.na(old) != is.na(new))
  }

  for (col in intersect(html_cols, names(data))) {
    before <- data[[col]]
    data[[col]] <- clean_html_tags(before)
    if (verbose) {
      n_changed <- count_changes(before, data[[col]])
      if (n_changed > 0) {
        cat(sprintf("    ✓ clean_html_tags(%s): %d cell(s) changed\n", col, n_changed))
      }
    }
  }

  for (col in intersect(lb_cols, names(data))) {
    before <- data[[col]]
    data[[col]] <- clean_linebreaks(before)
    if (verbose) {
      n_changed <- count_changes(before, data[[col]])
      if (n_changed > 0) {
        cat(sprintf("    ✓ clean_linebreaks(%s): %d cell(s) changed\n", col, n_changed))
      }
    }
  }

  data
}

