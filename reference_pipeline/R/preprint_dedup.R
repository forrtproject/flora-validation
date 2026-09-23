# Detect and resolve preprint-publication duplicates in FLoRA data
#
# Addresses: https://github.com/.../issues/105
#
# Two kinds of duplicate:
#   1. Replication-side: same doi_o + study_o, but doi_r is both a preprint
#      and a published version of the same paper.
#   2. Original-side: different doi_o values that are preprint/published (or
#      DOI-format variants) of the same original paper.
#
# Workflow:
#   - find_preprint_pub_duplicates() identifies candidates.
#   - resolve_preprint_dedup() detects + applies: by default it drops one DOI
#     from each pair (preprint loses; for DOI variants the non-canonical loses;
#     otherwise doi_2 loses). Pairs listed in cache/confirmed_preprint_duplicates.csv
#     override that default: action=keep_both retains both rows; keep_1/keep_2
#     pick which survives.
#   - apply_confirmed_preprint_dedup() applies *only* keep_1/keep_2 entries
#     from the confirmed file early in the pipeline, so the reference fetch
#     sees canonical DOIs.
#   - Detected candidates + the action taken are logged to
#     output/preprint_dedup_candidates.csv each run.

library(dplyr)
library(jsonlite)
library(stringr)

# Canonical outcome vocabulary + normalise_outcome(), used when merging rows.
source(here::here("R", "outcome_vocabulary.R"))

# ---- constants ----
PREPRINT_DOI_PREFIXES <- c(
"10.31234/",
  "10.31219/",
  "10.17605/",
  "10.48550/",
  "10.1101/",
  "10.2139/",
  "10.20944/",
  "10.21203/",
  "10.53841/"
)

CONFIRMED_DEDUP_PATH <- here::here("cache", "confirmed_preprint_duplicates.csv")
CANDIDATES_PATH      <- here::here("output", "preprint_dedup_candidates.csv")

# Marker used in the title of auto-filed GitHub issues so we can detect and
# skip filing duplicates on subsequent runs.
DEDUP_ISSUE_MARKER   <- "[preprint-dedup-review]"
DEDUP_STALE_DAYS     <- 7

# ---- (doi_o, doi_r) duplicate merge ----

#' Merge rows that share (doi_o, doi_r) when their url_r values are compatible.
#'
#' Within each (doi_o, doi_r) group:
#'   - if there are <=1 distinct non-NA `url_r` values, all rows are merged
#'     into a single row.
#'   - if there are >=2 distinct non-NA `url_r` values, the rows are kept
#'     separate (they may be different replication reports of the same dataset).
#'
#' Merging rules per column:
#'   - url_r:                first non-NA
#'   - study_o:              paste unique non-NA values with "; "
#'   - outcome:              the single non-NA value, or "mixed" when distinct
#'                            non-NA outcomes disagree (which is logged as a
#'                            data-entry warning — same paper, same url_r but
#'                            conflicting outcomes is a source-data mistake).
#'   - outcome_quote, outcome_quote_source: paste unique non-NA with " || "
#'   - source:               prefer "COS" if any row has it; else first non-NA
#'   - everything else:      first non-NA
#'
#' Groups with conflicting outcomes are also written to
#' `output/dup_outcome_conflicts.csv` for follow-up.
#'
#' Rows with NA doi_o or NA doi_r are returned untouched.
merge_doi_pair_dups <- function(data, verbose = TRUE) {
  if (!all(c("doi_o", "doi_r") %in% names(data))) return(data)

  na_rows <- data %>% filter(is.na(doi_o) | is.na(doi_r))
  pair_rows <- data %>% filter(!is.na(doi_o), !is.na(doi_r))

  url_r_col <- if ("url_r" %in% names(pair_rows)) "url_r" else NULL

  # Decide which (doi_o, doi_r) groups should be merged
  group_flags <- pair_rows %>%
    group_by(doi_o, doi_r) %>%
    summarise(
      .group_size = dplyr::n(),
      .n_distinct_url_r = if (!is.null(url_r_col)) dplyr::n_distinct(url_r, na.rm = TRUE) else 0L,
      .groups = "drop"
    ) %>%
    mutate(.merge = .group_size > 1 & .n_distinct_url_r <= 1)

  pair_rows <- pair_rows %>% left_join(group_flags, by = c("doi_o", "doi_r"))
  to_merge <- pair_rows %>% filter(.merge)
  to_keep  <- pair_rows %>% filter(!.merge)

  if (nrow(to_merge) == 0) {
    return(data)
  }

  prefer_cos <- function(x) {
    v <- x[!is.na(x)]
    if (length(v) == 0) return(NA)
    if (any(toupper(as.character(v)) == "COS")) return(v[which(toupper(as.character(v)) == "COS")[1]])
    v[1]
  }
  paste_unique <- function(x, sep) {
    v <- unique(stats::na.omit(x))
    if (length(v) == 0) return(NA_character_)
    paste(as.character(v), collapse = sep)
  }
  # Normalise before comparing, so that a spelling variant (e.g. "success" vs
  # "successful") is not mistaken for a genuine outcome clash. A real clash is
  # retained as an "A || B" value, which validate_flora.R's outcome check picks
  # up because it is not in the allowed vocabulary.
  pick_outcome <- function(x) {
    v <- unique(stats::na.omit(normalise_outcome(x)))
    if (length(v) == 0) return(NA_character_)
    if (length(v) == 1) return(as.character(v))
    if (all(v %in% c("successful", "mixed", "failed"))) return("mixed")
    paste(as.character(v), collapse = OUTCOME_CLASH_SEP)
  }
  first_nona <- function(x) {
    v <- x[!is.na(x)]
    if (length(v) == 0) return(x[1])  # preserves type when all NA
    v[1]
  }

  cols <- setdiff(names(pair_rows),
                  c("doi_o", "doi_r", ".group_size", ".n_distinct_url_r", ".merge"))

  # Detect outcome conflicts within groups about to be merged. Same paper +
  # same url_r but conflicting outcomes is a source-data mistake — log it.
  # Compare normalised values so that a spelling variant is not reported as a
  # conflict, and record how pick_outcome() actually resolved each group.
  outcome_conflicts <- if ("outcome" %in% cols) {
    to_merge %>%
      mutate(.outcome_norm = normalise_outcome(outcome)) %>%
      group_by(doi_o, doi_r) %>%
      summarise(
        outcomes = paste(sort(unique(stats::na.omit(.outcome_norm))), collapse = " | "),
        n_outcomes = dplyr::n_distinct(.outcome_norm, na.rm = TRUE),
        resolved_to = pick_outcome(outcome),
        url_r = paste(unique(stats::na.omit(url_r)), collapse = " | "),
        sources = paste(sort(unique(stats::na.omit(source))), collapse = " | "),
        .groups = "drop"
      ) %>%
      filter(n_outcomes > 1)
  } else {
    tibble::tibble()
  }

  merged <- to_merge %>%
    group_by(doi_o, doi_r) %>%
    summarise(
      across(all_of(cols), ~ {
        cn <- dplyr::cur_column()
        if (cn == "study_o")                                 paste_unique(.x, "; ")
        else if (cn %in% c("outcome_quote",
                           "outcome_quote_source"))          paste_unique(.x, " || ")
        else if (cn == "source")                             prefer_cos(.x)
        else if (cn == "outcome")                            pick_outcome(.x)
        else                                                  first_nona(.x)
      }),
      .groups = "drop"
    )

  out <- bind_rows(
    to_keep %>% select(-.group_size, -.n_distinct_url_r, -.merge),
    merged,
    na_rows
  )

  if (verbose) {
    n_groups_merged <- nrow(merged)
    rows_removed <- nrow(data) - nrow(out)
    cat(sprintf(
      "  Merged %d (doi_o, doi_r) duplicate group(s); removed %d row(s)\n",
      n_groups_merged, rows_removed
    ))
  }

  if (nrow(outcome_conflicts) > 0) {
    n_retained <- sum(grepl(OUTCOME_CLASH_SEP, outcome_conflicts$resolved_to, fixed = TRUE))
    cat(sprintf(
      paste0("  ⚠ %d merged group(s) had CONFLICTING outcomes (same paper, same url_r); ",
             "%d collapsed to 'mixed', %d retained as a clash for validation.\n"),
      nrow(outcome_conflicts), nrow(outcome_conflicts) - n_retained, n_retained
    ))
    cat("    These are likely source-data mistakes. Affected (doi_o, doi_r):\n")
    for (i in seq_len(nrow(outcome_conflicts))) {
      cat(sprintf("      - %s + %s  [outcomes: %s -> '%s'; sources: %s]\n",
                  outcome_conflicts$doi_o[i],
                  outcome_conflicts$doi_r[i],
                  outcome_conflicts$outcomes[i],
                  outcome_conflicts$resolved_to[i],
                  outcome_conflicts$sources[i]))
    }
    out_dir <- here::here("output")
    if (dir.exists(out_dir)) {
      readr::write_excel_csv(
        outcome_conflicts,
        file.path(out_dir, "dup_outcome_conflicts.csv")
      )
      cat(sprintf("    Logged to output/dup_outcome_conflicts.csv\n"))
    }
  }

  out
}

# ---- helpers ----

#' Check whether a DOI belongs to a known preprint server
is_preprint_doi <- function(doi) {
  vapply(doi, function(d) {
    if (is.na(d) || !nzchar(d)) return(FALSE)
    any(startsWith(tolower(d), PREPRINT_DOI_PREFIXES))
  }, logical(1))
}

#' Normalize a title for fuzzy comparison
normalize_title <- function(title) {
  title <- tolower(trimws(as.character(title)))
  title <- gsub("<[^>]+>", "", title)        # strip HTML tags
  title <- gsub("[^a-z0-9 ]", "", title)     # keep alphanumeric + spaces
  title <- gsub("\\s+", " ", title)          # collapse whitespace
  trimws(title)
}

#' Pairwise title similarity (0-1) using Levenshtein distance
title_similarity <- function(t1, t2) {
  t1 <- normalize_title(t1)
  t2 <- normalize_title(t2)
  if (is.na(t1) || is.na(t2) || !nzchar(t1) || !nzchar(t2)) return(0)
  if (t1 == t2) return(1)
  max_len <- max(nchar(t1), nchar(t2))
  if (max_len == 0) return(0)
  1 - adist(t1, t2)[1, 1] / max_len
}

#' Extract the first author's family name from a CrossRef-style JSON string
#' or from a plain-text author string
extract_first_author <- function(author_str) {
  if (is.na(author_str) || !nzchar(trimws(author_str))) return(NA_character_)
  # Try JSON parse first (CrossRef format)
  parsed <- tryCatch(fromJSON(author_str, simplifyDataFrame = TRUE), error = function(e) NULL)
  if (!is.null(parsed) && is.data.frame(parsed) && "family" %in% names(parsed)) {
    first <- parsed %>% filter(sequence == "first" | row_number() == 1) %>% slice(1)
    return(tolower(trimws(first$family)))
  }
  # Fallback: plain text — take first word (assumed surname)
  tolower(trimws(strsplit(author_str, "[,;&]")[[1]][1]))
}

#' Append an identifier to a comma-separated list, preserving existing entries
#' and deduping case-insensitively. Returns NA when both inputs are NA/empty.
append_alt_identifier <- function(existing, new_id) {
  parts <- function(x) {
    if (is.na(x) || !nzchar(trimws(as.character(x)))) return(character(0))
    trimws(strsplit(as.character(x), ",", fixed = TRUE)[[1]])
  }
  combined <- c(parts(existing), parts(new_id))
  combined <- combined[nzchar(combined)]
  if (length(combined) == 0) return(NA_character_)
  combined <- combined[!duplicated(tolower(combined))]
  paste(combined, collapse = ", ")
}

#' Vectorised version: append `new_id[i]` to `existing[i]` row-wise.
append_alt_identifier_vec <- function(existing, new_id) {
  n <- max(length(existing), length(new_id))
  existing <- rep_len(existing, n)
  new_id   <- rep_len(new_id, n)
  vapply(seq_len(n),
         function(i) append_alt_identifier(existing[i], new_id[i]),
         character(1))
}

#' Normalize a DOI to canonical form
#'  - lowercase
#'  - remove double slashes that are typos (10.1037//0022 -> 10.1037/0022)
#'  - decode URL-encoded characters like %3c %3e
normalize_doi <- function(doi) {
  if (is.na(doi) || !nzchar(doi)) return(doi)
  d <- tolower(trimws(doi))
  d <- gsub("^(https?://)?(dx\\.)?doi\\.org/", "", d)
  d <- gsub("^doi:", "", d)
  # Fix double-slash typo (e.g. 10.1037//0022-3514...)
  d <- gsub("^(10\\.[0-9]+)//", "\\1/", d)
  # Decode percent-encoded chars so variant DOIs match
  d <- tryCatch(utils::URLdecode(d), error = function(e) d)
  d
}

# ---- main detection function ----

#' Find candidate preprint-publication duplicates
#'
#' @param data  A data frame with at least: doi_o, doi_r, title_o, title_r,
#'              author_o, author_r, year_o, year_r, study_o
#' @param title_threshold  Minimum title similarity (0-1) to flag a pair
#' @param verbose  Print progress messages
#' @return A tibble of candidate duplicate pairs
find_preprint_pub_duplicates <- function(data,
                                         title_threshold = 0.80,
                                         verbose = TRUE) {
  candidates <- tibble(
    side            = character(),
    doi_1           = character(),
    doi_2           = character(),
    title_1         = character(),
    title_2         = character(),
    title_sim       = numeric(),
    first_author_1  = character(),
    first_author_2  = character(),
    year_1          = character(),
    year_2          = character(),
    is_preprint_1   = logical(),
    is_preprint_2   = logical(),
    group_key       = character(),
    doi_o_group     = character()
  )

  # ----------------------------------------------------------------
  # A) Replication-side: same doi_o, different doi_r
  #    Check both within same study_o and across different study_o
  #    (study_o may differ due to data entry inconsistencies)
  # ----------------------------------------------------------------
  data <- data %>% mutate(.row = row_number())
  groups <- data %>%
    group_by(doi_o) %>%
    filter(n() > 1, !is.na(title_r)) %>%
    group_split()

  if (verbose) cat(sprintf("Checking %d doi_o groups with >1 row for replication-side duplicates...\n",
                            length(groups)))

  for (grp in groups) {
    n <- nrow(grp)
    if (n < 2) next
    for (i in seq_len(n - 1)) {
      for (j in (i + 1):n) {
        # Skip if same doi_r (already deduped by exact match)
        if (!is.na(grp$doi_r[i]) && !is.na(grp$doi_r[j]) &&
            normalize_doi(grp$doi_r[i]) == normalize_doi(grp$doi_r[j])) next

        sim <- title_similarity(grp$title_r[i], grp$title_r[j])
        if (sim < title_threshold) next

        fa1 <- extract_first_author(grp$author_r[i])
        fa2 <- extract_first_author(grp$author_r[j])

        # Require either first-author match or at least one preprint DOI
        author_match <- !is.na(fa1) && !is.na(fa2) && fa1 == fa2
        any_preprint <- is_preprint_doi(grp$doi_r[i]) || is_preprint_doi(grp$doi_r[j])

        if (!author_match && !any_preprint) next

        candidates <- bind_rows(candidates, tibble(
          side           = "replication",
          doi_1          = grp$doi_r[i],
          doi_2          = grp$doi_r[j],
          title_1        = grp$title_r[i],
          title_2        = grp$title_r[j],
          title_sim      = round(sim, 3),
          first_author_1 = fa1 %||% NA_character_,
          first_author_2 = fa2 %||% NA_character_,
          year_1         = as.character(grp$year_r[i]),
          year_2         = as.character(grp$year_r[j]),
          is_preprint_1  = is_preprint_doi(grp$doi_r[i]),
          is_preprint_2  = is_preprint_doi(grp$doi_r[j]),
          group_key      = paste0("doi_o: ", grp$doi_o[1]),
          doi_o_group    = grp$doi_o[1]
        ))
      }
    }
  }

  # ----------------------------------------------------------------
  # B) Original-side: different doi_o pointing to the same paper
  # ----------------------------------------------------------------
  originals <- data %>%
    filter(!is.na(doi_o), !is.na(title_o)) %>%
    distinct(doi_o, .keep_all = TRUE) %>%
    mutate(
      first_author_o = vapply(author_o, extract_first_author, character(1)),
      title_o_norm   = normalize_title(title_o),
      doi_o_norm     = vapply(doi_o, normalize_doi, character(1))
    )

  if (verbose) cat(sprintf("Checking %d unique doi_o for original-side duplicates...\n",
                            nrow(originals)))

  # B1: exact normalized-title match
  title_dupes <- originals %>%
    group_by(title_o_norm) %>%
    filter(n() > 1) %>%
    group_split()

  for (tgrp in title_dupes) {
    n <- nrow(tgrp)
    for (i in seq_len(n - 1)) {
      for (j in (i + 1):n) {
        if (tgrp$doi_o_norm[i] == tgrp$doi_o_norm[j]) next   # same DOI after normalisation

        candidates <- bind_rows(candidates, tibble(
          side           = "original",
          doi_1          = tgrp$doi_o[i],
          doi_2          = tgrp$doi_o[j],
          title_1        = tgrp$title_o[i],
          title_2        = tgrp$title_o[j],
          title_sim      = 1,
          first_author_1 = tgrp$first_author_o[i],
          first_author_2 = tgrp$first_author_o[j],
          year_1         = as.character(tgrp$year_o[i]),
          year_2         = as.character(tgrp$year_o[j]),
          is_preprint_1  = is_preprint_doi(tgrp$doi_o[i]),
          is_preprint_2  = is_preprint_doi(tgrp$doi_o[j]),
          group_key      = paste0("title: ", substr(tgrp$title_o_norm[1], 1, 60)),
          doi_o_group    = NA_character_
        ))
      }
    }
  }

  # B2: DOI-normalisation duplicates (e.g. 10.1037//... vs 10.1037/...)
  doi_norm_dupes <- originals %>%
    group_by(doi_o_norm) %>%
    filter(n() > 1) %>%
    group_split()

  for (dgrp in doi_norm_dupes) {
    n <- nrow(dgrp)
    for (i in seq_len(n - 1)) {
      for (j in (i + 1):n) {
        if (dgrp$doi_o[i] == dgrp$doi_o[j]) next
        # Skip if already captured by title match above
        already <- candidates %>%
          filter(side == "original",
                 (doi_1 == dgrp$doi_o[i] & doi_2 == dgrp$doi_o[j]) |
                 (doi_1 == dgrp$doi_o[j] & doi_2 == dgrp$doi_o[i]))
        if (nrow(already) > 0) next

        candidates <- bind_rows(candidates, tibble(
          side           = "original (DOI variant)",
          doi_1          = dgrp$doi_o[i],
          doi_2          = dgrp$doi_o[j],
          title_1        = dgrp$title_o[i],
          title_2        = dgrp$title_o[j],
          title_sim      = title_similarity(dgrp$title_o[i], dgrp$title_o[j]),
          first_author_1 = dgrp$first_author_o[i],
          first_author_2 = dgrp$first_author_o[j],
          year_1         = as.character(dgrp$year_o[i]),
          year_2         = as.character(dgrp$year_o[j]),
          is_preprint_1  = is_preprint_doi(dgrp$doi_o[i]),
          is_preprint_2  = is_preprint_doi(dgrp$doi_o[j]),
          group_key      = paste0("doi_variant: ", dgrp$doi_o_norm[1]),
          doi_o_group    = NA_character_
        ))
      }
    }
  }

  # B3: fuzzy title match within same first-author block
  author_blocks <- originals %>%
    filter(!is.na(first_author_o)) %>%
    group_by(first_author_o) %>%
    filter(n() > 1) %>%
    group_split()

  for (ablk in author_blocks) {
    n <- nrow(ablk)
    if (n < 2) next
    for (i in seq_len(n - 1)) {
      for (j in (i + 1):n) {
        if (ablk$doi_o_norm[i] == ablk$doi_o_norm[j]) next
        if (ablk$title_o_norm[i] == ablk$title_o_norm[j]) next  # already caught above

        sim <- title_similarity(ablk$title_o[i], ablk$title_o[j])
        if (sim < title_threshold) next

        candidates <- bind_rows(candidates, tibble(
          side           = "original (fuzzy)",
          doi_1          = ablk$doi_o[i],
          doi_2          = ablk$doi_o[j],
          title_1        = ablk$title_o[i],
          title_2        = ablk$title_o[j],
          title_sim      = round(sim, 3),
          first_author_1 = ablk$first_author_o[i],
          first_author_2 = ablk$first_author_o[j],
          year_1         = as.character(ablk$year_o[i]),
          year_2         = as.character(ablk$year_o[j]),
          is_preprint_1  = is_preprint_doi(ablk$doi_o[i]),
          is_preprint_2  = is_preprint_doi(ablk$doi_o[j]),
          group_key      = paste0("author: ", ablk$first_author_o[1]),
          doi_o_group    = NA_character_
        ))
      }
    }
  }

  # deduplicate candidates (a pair may be found by multiple methods)
  candidates <- candidates %>%
    mutate(pair_key = paste0(pmin(doi_1, doi_2), "||", pmax(doi_1, doi_2))) %>%
    distinct(pair_key, .keep_all = TRUE) %>%
    select(-pair_key) %>%
    arrange(side, group_key)

  if (verbose) {
    cat(sprintf("\nFound %d candidate duplicate pairs:\n", nrow(candidates)))
    repl_n <- sum(candidates$side == "replication")
    orig_n <- sum(grepl("^original", candidates$side))
    cat(sprintf("  Replication-side: %d\n", repl_n))
    cat(sprintf("  Original-side:    %d\n", orig_n))
  }

  candidates
}

# ---- derive doi_remove / doi_keep from action ----

#' Given a row from the candidates table with an action filled in,
#' return the DOI to remove and the DOI to keep.
derive_remove_keep <- function(action, doi_1, doi_2) {
  switch(action,
    keep_1 = list(doi_remove = doi_2, doi_keep = doi_1),
    keep_2 = list(doi_remove = doi_1, doi_keep = doi_2),
    list(doi_remove = NA_character_, doi_keep = NA_character_)
  )
}

# ---- apply confirmed duplicates ----

#' Apply confirmed preprint-publication deduplication
#'
#' Reads confirmed_preprint_duplicates.csv (if it exists).
#' The file has the same format as the candidates file; only rows where the
#' `action` column is filled in ("keep_1" or "keep_2") are applied.
#'
#' - Replication-side (action = keep_1/keep_2): removes the row whose doi_r
#'   is the one NOT kept.
#' - Original-side (action = keep_1/keep_2): replaces the removed doi_o with
#'   the kept doi_o in all rows, then re-dedups.
#'
#' @param data  FLoRA data frame (must have doi_o, doi_r)
#' @param confirmed_path  Path to confirmed duplicates CSV
#' @param verbose Print progress
#' @return Updated data frame
apply_confirmed_preprint_dedup <- function(data,
                                            confirmed_path = CONFIRMED_DEDUP_PATH,
                                            verbose = TRUE) {
  # Ensure alt columns exist (sourced from flora replication sheet)
  if (!"alt_identifier_o" %in% names(data)) data$alt_identifier_o <- NA_character_
  if (!"alt_identifier_r" %in% names(data)) data$alt_identifier_r <- NA_character_

  if (!file.exists(confirmed_path)) {
    if (verbose) cat("No confirmed duplicates file found at:", confirmed_path, "\n")
    return(data)
  }

  confirmed <- readr::read_csv(confirmed_path, show_col_types = FALSE) %>%
    filter(!is.na(action), action %in% c("keep_1", "keep_2"))

  if (nrow(confirmed) == 0) {
    if (verbose) cat("No actionable entries in confirmed duplicates file\n")
    return(data)
  }

  if (verbose) cat(sprintf("Applying %d confirmed duplicate resolutions\n", nrow(confirmed)))

  # Derive doi_remove / doi_keep from action
  resolved <- confirmed %>%
    rowwise() %>%
    mutate(
      doi_remove = derive_remove_keep(action, doi_1, doi_2)$doi_remove,
      doi_keep   = derive_remove_keep(action, doi_1, doi_2)$doi_keep
    ) %>%
    ungroup()

  n_before <- nrow(data)

  # --- Replication-side: append removed DOI to alt_identifier_r on kept rows, then drop ---
  repl_resolved <- resolved %>% filter(grepl("^replication", side))

  for (i in seq_len(nrow(repl_resolved))) {
    doi_rm <- tolower(repl_resolved$doi_remove[i])
    doi_kp <- tolower(repl_resolved$doi_keep[i])

    # Skip entries with NA doi_remove (nothing to remove)
    if (is.na(doi_rm)) {
      if (verbose) cat(sprintf("  Skipping replication entry with NA doi_remove (doi_keep=%s)\n", doi_kp))
      next
    }

    # Find all doi_o groups where the kept doi_r exists
    doi_os_with_kept <- unique(tolower(data$doi_o[tolower(data$doi_r) == doi_kp]))

    is_target <- tolower(data$doi_r) == doi_rm & tolower(data$doi_o) %in% doi_os_with_kept
    is_kept   <- tolower(data$doi_r) == doi_kp & tolower(data$doi_o) %in% doi_os_with_kept
    is_target[is.na(is_target)] <- FALSE
    is_kept[is.na(is_kept)]     <- FALSE

    # Confirmed override -> annotate alt_identifier_r on the kept row(s)
    if (any(is_kept)) {
      data$alt_identifier_r[is_kept] <- append_alt_identifier_vec(
        data$alt_identifier_r[is_kept], repl_resolved$doi_remove[i]
      )
    }

    data <- data[!is_target, ]
  }

  if (nrow(repl_resolved) > 0 && verbose) {
    cat(sprintf("  Removed %d replication-side duplicate rows (alt identifiers preserved)\n",
                n_before - nrow(data)))
  }

  # --- Original-side: replace doi_remove with doi_keep; record old DOI in alt_identifier_o ---
  orig_resolved <- resolved %>%
    filter(grepl("^original", side))

  if (nrow(orig_resolved) > 0) {
    doi_map <- setNames(tolower(orig_resolved$doi_keep), tolower(orig_resolved$doi_remove))
    matched <- tolower(data$doi_o) %in% names(doi_map)
    data <- data %>%
      mutate(
        alt_identifier_o = dplyr::if_else(matched,
                                           append_alt_identifier_vec(alt_identifier_o, doi_o),
                                           alt_identifier_o),
        doi_o            = dplyr::if_else(matched,
                                           unname(doi_map[tolower(doi_o)]),
                                           doi_o)
      )
    if (verbose) cat(sprintf("  Replaced %d original-side DOIs (alt identifiers preserved)\n",
                              nrow(orig_resolved)))

    # Re-dedup after DOI replacement (may create exact duplicates).
    # Distinct url_r values are kept separate (different replication reports
    # of the same dataset).
    n_before_dedup <- nrow(data)
    data <- merge_doi_pair_dups(data, verbose = verbose)
    if (verbose && nrow(data) < n_before_dedup) {
      cat(sprintf("  (After DOI replacement: %d row(s) collapsed by merge_doi_pair_dups)\n",
                  n_before_dedup - nrow(data)))
    }
  }

  if (verbose) cat(sprintf("  Net rows removed: %d\n", n_before - nrow(data)))
  data
}

# ---- default resolution rule ----

#' Decide which DOI in a candidate pair to drop when no override is set.
#'
#' Rule:
#'   1. If exactly one DOI is a preprint, drop the preprint.
#'   2. For "original (DOI variant)" pairs, drop the non-canonical form
#'      (the one whose normalised DOI differs from itself).
#'   3. Otherwise, deterministically drop doi_2.
#'
#' Returns NULL when either DOI is NA — those pairs cannot be auto-resolved.
default_resolve_pair <- function(side, doi_1, doi_2,
                                  is_preprint_1, is_preprint_2) {
  if (is.na(doi_1) || is.na(doi_2)) return(NULL)

  pp1 <- isTRUE(is_preprint_1)
  pp2 <- isTRUE(is_preprint_2)
  if (pp1 && !pp2) return(list(doi_remove = doi_1, doi_keep = doi_2))
  if (!pp1 && pp2) return(list(doi_remove = doi_2, doi_keep = doi_1))

  if (grepl("DOI variant", side, fixed = TRUE)) {
    canon1 <- normalize_doi(doi_1) == tolower(trimws(doi_1))
    canon2 <- normalize_doi(doi_2) == tolower(trimws(doi_2))
    if (canon1 && !canon2) return(list(doi_remove = doi_2, doi_keep = doi_1))
    if (!canon1 && canon2) return(list(doi_remove = doi_1, doi_keep = doi_2))
  }

  list(doi_remove = doi_2, doi_keep = doi_1)
}

# ---- GitHub issue for unconfirmed auto-resolutions ----

#' If there are auto-resolved candidate pairs and `cache/<confirmed_path>`
#' has been stale for more than `stale_days`, ensure GitHub knows about it.
#'
#' Behaviour:
#'   - No open issue with the marker in its title -> file a new issue.
#'   - Open issue exists, last activity ≤ `stale_days` ago -> do nothing
#'     (a review nudge is already pending).
#'   - Open issue exists, last activity > `stale_days` ago -> add a comment
#'     (a recurring nudge so reviewers don't forget about it).
#'
#' "Last activity" is the issue's `updatedAt` (which GitHub bumps on any
#' edit/comment). Failures (no `gh`, not in a repo, network error) are
#' reported but do not abort the pipeline.
#'
#' @param decisions  The decisions tibble built by `resolve_preprint_dedup`.
#' @param confirmed_path  Path to confirmed duplicates CSV (mtime is the
#'                        staleness reference).
#' @param candidates_out  Path to candidates log (referenced in issue body).
#' @param stale_days  Threshold in days. Default 7.
#' @param verbose Print progress messages.
#' @return One of "filed", "commented", or "skipped".
maybe_open_dedup_review_issue <- function(decisions,
                                          confirmed_path = CONFIRMED_DEDUP_PATH,
                                          candidates_out = CANDIDATES_PATH,
                                          stale_days     = DEDUP_STALE_DAYS,
                                          verbose        = TRUE) {
  auto_rows <- decisions %>%
    filter(!is.na(resolution), grepl("^auto", resolution))
  if (nrow(auto_rows) == 0) {
    if (verbose) cat("  No auto-resolved pairs — no review issue needed.\n")
    return("skipped")
  }

  if (!file.exists(confirmed_path)) {
    age_days <- Inf
  } else {
    age_days <- as.numeric(difftime(Sys.time(),
                                     file.info(confirmed_path)$mtime,
                                     units = "days"))
  }
  if (age_days <= stale_days) {
    if (verbose) cat(sprintf(
      "  %d auto-resolved pair(s); confirmed file last touched %.1f day(s) ago — no issue activity needed.\n",
      nrow(auto_rows), age_days))
    return("skipped")
  }

  if (Sys.which("gh") == "") {
    if (verbose) cat("  ⚠ gh CLI not found; skipping auto-issue creation.\n")
    return("skipped")
  }

  # ---- Build body / preview ----
  # Show the candidates log as a repo-relative path (absolute local paths are
  # meaningless to other viewers on GitHub).
  candidates_rel <- sub(paste0("^", here::here(), "/?"), "", candidates_out)
  preview_n <- min(20L, nrow(auto_rows))
  preview <- auto_rows %>%
    head(preview_n) %>%
    mutate(line = sprintf("- `%s` ⇄ `%s` (%s, sim=%s) — auto: %s",
                          doi_1, doi_2, side,
                          ifelse(is.na(title_sim), "?", as.character(title_sim)),
                          applied_action)) %>%
    pull(line) %>%
    paste(collapse = "\n")

  body_intro <- paste0(
    "The FLoRA preparation pipeline auto-resolved **",
    nrow(auto_rows),
    "** preprint/publication duplicate pair(s), but `",
    basename(confirmed_path),
    "` has not been touched in over ", stale_days, " days ",
    sprintf("(last edit %.1f days ago).", age_days),
    "\n\n",
    "Please review the candidates and either confirm the auto-default by adding ",
    "the rows to `cache/", basename(confirmed_path), "` with `action=keep_1` / ",
    "`keep_2`, or override with `keep_both`.\n\n",
    "Until each pair is confirmed, the surviving row's `alt_identifier_*` will ",
    "**not** be annotated with the dropped DOI.\n\n",
    "**First ", preview_n, " of ", nrow(auto_rows), " auto-resolved pair(s):**\n\n",
    preview,
    "\n\n",
    "Full log: `", candidates_rel, "`\n\n",
    "_Filed automatically by `R/preprint_dedup.R`._"
  )

  write_body_file <- function(body_text) {
    f <- tempfile(fileext = ".md")
    writeLines(body_text, f)
    f
  }

  # ---- Look for existing open issue carrying the marker ----
  existing_json <- tryCatch(
    suppressWarnings(system2(
      "gh",
      c("issue", "list", "--state", "open",
        "--search", shQuote(DEDUP_ISSUE_MARKER),
        "--json", "number,title,updatedAt",
        "--limit", "20"),
      stdout = TRUE
    )),
    error = function(e) character(0)
  )

  parsed <- tryCatch(
    if (length(existing_json) == 0) list()
    else jsonlite::fromJSON(paste(existing_json, collapse = "\n"),
                             simplifyDataFrame = FALSE),
    error = function(e) list()
  )
  matches <- Filter(function(x) grepl(DEDUP_ISSUE_MARKER, x$title %||% "", fixed = TRUE),
                    parsed)

  # ---- No open issue: file a new one ----
  if (length(matches) == 0) {
    title <- sprintf("%s %d unconfirmed preprint-duplicate exclusions need review",
                     DEDUP_ISSUE_MARKER, nrow(auto_rows))
    body_file <- write_body_file(body_intro)
    on.exit(unlink(body_file), add = TRUE)
    res <- tryCatch(
      suppressWarnings(system2(
        "gh",
        c("issue", "create",
          "--title", shQuote(title),
          "--body-file", shQuote(body_file)),
        stdout = TRUE
      )),
      error = function(e) {
        if (verbose) cat(sprintf("  ⚠ gh issue create failed: %s\n", e$message))
        NULL
      }
    )
    if (!is.null(res) && length(res) > 0) {
      if (verbose) cat(sprintf("  ✓ Filed review issue: %s\n", paste(res, collapse = " ")))
      return("filed")
    }
    if (verbose) cat("  ⚠ gh issue create produced no URL output.\n")
    return("skipped")
  }

  # ---- Open issue exists: comment if its last activity is also stale ----
  # Pick the most recently updated matching issue.
  updated_at <- vapply(matches, function(x) x$updatedAt %||% NA_character_, character(1))
  last_idx <- which.max(as.POSIXct(updated_at, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ"))
  issue <- matches[[last_idx]]
  issue_age_days <- as.numeric(difftime(
    Sys.time(),
    as.POSIXct(issue$updatedAt, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ"),
    units = "days"
  ))

  if (is.na(issue_age_days) || issue_age_days <= stale_days) {
    if (verbose) cat(sprintf(
      "  Open review issue #%s last active %.1f day(s) ago (≤ %d) — leaving it alone.\n",
      issue$number, issue_age_days %||% NA, stale_days))
    return("skipped")
  }

  comment_body <- paste0(
    sprintf("Still **%d** unconfirmed preprint-duplicate exclusion(s) outstanding ",
            nrow(auto_rows)),
    sprintf("(this issue was last touched %.1f days ago).\n\n", issue_age_days),
    body_intro
  )
  body_file <- write_body_file(comment_body)
  on.exit(unlink(body_file), add = TRUE)

  res <- tryCatch(
    suppressWarnings(system2(
      "gh",
      c("issue", "comment", shQuote(as.character(issue$number)),
        "--body-file", shQuote(body_file)),
      stdout = TRUE
    )),
    error = function(e) {
      if (verbose) cat(sprintf("  ⚠ gh issue comment failed: %s\n", e$message))
      NULL
    }
  )
  if (!is.null(res) && length(res) > 0) {
    if (verbose) cat(sprintf("  ✓ Commented on review issue #%s: %s\n",
                              issue$number, paste(res, collapse = " ")))
    return("commented")
  }
  if (verbose) cat("  ⚠ gh issue comment produced no URL output.\n")
  "skipped"
}

# ---- pipeline integration ----

#' Detect preprint/publication duplicate candidates and apply resolutions.
#'
#' Each detected pair is resolved as follows:
#'   - If `cache/confirmed_preprint_duplicates.csv` has an entry for the pair
#'     with action `keep_both`, both rows are preserved.
#'   - If it has `keep_1` or `keep_2`, that choice is honoured.
#'   - Otherwise the default rule (see `default_resolve_pair`) drops one DOI.
#'
#' Detected pairs and the action taken are logged to
#' `output/preprint_dedup_candidates.csv`.
#'
#' @param data  Augmented FLoRA data (titles/authors must be populated).
#' @param confirmed_path  CSV with override decisions.
#' @param candidates_out  Where to write the detection log.
#' @param title_threshold Title similarity threshold for fuzzy detection.
#' @param verbose Print progress messages.
#' @return Updated data frame.
resolve_preprint_dedup <- function(data,
                                    confirmed_path = CONFIRMED_DEDUP_PATH,
                                    candidates_out = CANDIDATES_PATH,
                                    title_threshold = 0.80,
                                    open_review_issue = TRUE,
                                    stale_days = DEDUP_STALE_DAYS,
                                    verbose = TRUE) {
  if (verbose) cat("\n### Preprint-Publication Deduplication\n")

  candidates <- find_preprint_pub_duplicates(data,
                                              title_threshold = title_threshold,
                                              verbose = verbose)
  if (nrow(candidates) == 0) {
    if (verbose) cat("✓ No preprint-publication duplicates detected\n")
    return(data)
  }

  pair_key_for <- function(d1, d2) {
    a <- tolower(d1); b <- tolower(d2)
    paste0(pmin(a, b), "||", pmax(a, b))
  }

  candidates <- candidates %>% mutate(pair_key = pair_key_for(doi_1, doi_2))

  overrides <- if (file.exists(confirmed_path)) {
    readr::read_csv(confirmed_path, show_col_types = FALSE) %>%
      filter(!is.na(action), action %in% c("keep_1", "keep_2", "keep_both")) %>%
      transmute(
        pair_key        = pair_key_for(doi_1, doi_2),
        override_action = action,
        override_doi_1  = doi_1,
        override_doi_2  = doi_2
      )
  } else {
    tibble(pair_key = character(), override_action = character(),
           override_doi_1 = character(), override_doi_2 = character())
  }

  decisions <- candidates %>%
    left_join(overrides, by = "pair_key") %>%
    mutate(
      resolution     = NA_character_,
      applied_action = NA_character_,
      doi_remove     = NA_character_,
      doi_keep       = NA_character_
    )

  for (i in seq_len(nrow(decisions))) {
    if (!is.na(decisions$override_action[i])) {
      act <- decisions$override_action[i]
      if (act == "keep_both") {
        decisions$resolution[i]     <- "override: keep_both"
        decisions$applied_action[i] <- "keep_both"
        next
      }
      rk <- derive_remove_keep(act,
                                decisions$override_doi_1[i],
                                decisions$override_doi_2[i])
      decisions$doi_remove[i]     <- rk$doi_remove
      decisions$doi_keep[i]       <- rk$doi_keep
      decisions$resolution[i]     <- sprintf("override: %s", act)
      decisions$applied_action[i] <- act
    } else {
      rr <- default_resolve_pair(decisions$side[i],
                                  decisions$doi_1[i], decisions$doi_2[i],
                                  decisions$is_preprint_1[i],
                                  decisions$is_preprint_2[i])
      if (is.null(rr)) {
        decisions$resolution[i]     <- "skipped: NA DOI in pair"
        decisions$applied_action[i] <- "skipped"
        next
      }
      decisions$doi_remove[i]     <- rr$doi_remove
      decisions$doi_keep[i]       <- rr$doi_keep
      which_dropped <- if (identical(rr$doi_remove, decisions$doi_1[i])) "doi_1" else "doi_2"
      decisions$resolution[i]     <- sprintf("auto: drop %s", which_dropped)
      decisions$applied_action[i] <- if (which_dropped == "doi_1") "auto_keep_2" else "auto_keep_1"
    }
  }

  if (!"alt_identifier_o" %in% names(data)) data$alt_identifier_o <- NA_character_
  if (!"alt_identifier_r" %in% names(data)) data$alt_identifier_r <- NA_character_

  to_apply <- decisions %>% filter(!is.na(doi_remove))
  # Only confirmed (override) decisions annotate alt_identifier_*; auto
  # resolutions still drop/replace rows but do not claim equivalence until
  # the pair is reviewed and added to confirmed_preprint_duplicates.csv.
  is_confirmed <- function(action) !is.na(action) & action %in% c("keep_1", "keep_2", "keep_both")

  n_before <- nrow(data)

  repl <- to_apply %>% filter(grepl("^replication", side))
  for (i in seq_len(nrow(repl))) {
    doi_rm <- tolower(repl$doi_remove[i])
    doi_kp <- tolower(repl$doi_keep[i])
    doi_os_with_kept <- unique(tolower(data$doi_o[tolower(data$doi_r) == doi_kp]))
    is_target <- tolower(data$doi_r) == doi_rm & tolower(data$doi_o) %in% doi_os_with_kept
    is_kept   <- tolower(data$doi_r) == doi_kp & tolower(data$doi_o) %in% doi_os_with_kept
    is_target[is.na(is_target)] <- FALSE
    is_kept[is.na(is_kept)]     <- FALSE
    if (is_confirmed(repl$applied_action[i]) && any(is_kept)) {
      data$alt_identifier_r[is_kept] <- append_alt_identifier_vec(
        data$alt_identifier_r[is_kept], repl$doi_remove[i]
      )
    }
    data <- data[!is_target, ]
  }

  orig <- to_apply %>% filter(grepl("^original", side))
  if (nrow(orig) > 0) {
    doi_map <- setNames(tolower(orig$doi_keep), tolower(orig$doi_remove))
    confirmed_doi_remove <- tolower(orig$doi_remove[is_confirmed(orig$applied_action)])
    matched <- tolower(data$doi_o) %in% names(doi_map)
    annotate <- tolower(data$doi_o) %in% confirmed_doi_remove
    data <- data %>%
      mutate(
        alt_identifier_o = dplyr::if_else(annotate,
                                           append_alt_identifier_vec(alt_identifier_o, doi_o),
                                           alt_identifier_o),
        doi_o            = dplyr::if_else(matched, unname(doi_map[tolower(doi_o)]), doi_o)
      )

    n_before_dedup <- nrow(data)
    data <- merge_doi_pair_dups(data, verbose = verbose)
    if (verbose && nrow(data) < n_before_dedup) {
      cat(sprintf("  (After DOI replacement: %d row(s) collapsed by merge_doi_pair_dups)\n",
                  n_before_dedup - nrow(data)))
    }
  }

  log_rows <- decisions %>%
    select(side, doi_1, doi_2, title_1, title_2, title_sim,
           first_author_1, first_author_2, year_1, year_2,
           is_preprint_1, is_preprint_2, group_key, doi_o_group,
           applied_action, resolution)

  instructions <- tibble(
    side           = "INSTRUCTIONS -->",
    doi_1          = "DOI #1",
    doi_2          = "DOI #2",
    title_1        = "(title for doi_1)",
    title_2        = "(title for doi_2)",
    title_sim      = NA_real_,
    first_author_1 = NA_character_,
    first_author_2 = NA_character_,
    year_1         = NA_character_,
    year_2         = NA_character_,
    is_preprint_1  = NA,
    is_preprint_2  = NA,
    group_key      = NA_character_,
    doi_o_group    = NA_character_,
    applied_action = "auto-default = drop one (preprint loses; else doi_2)",
    resolution     = "Override: copy row to confirmed_preprint_duplicates.csv with action keep_both / keep_1 / keep_2"
  )
  out <- bind_rows(instructions, log_rows)
  readr::write_excel_csv(out, candidates_out)

  if (verbose) {
    auto <- sum(grepl("^auto",     decisions$resolution))
    over <- sum(grepl("^override", decisions$resolution))
    skip <- sum(grepl("^skipped",  decisions$resolution))
    cat(sprintf("\n  Detected: %d candidate pairs\n", nrow(decisions)))
    cat(sprintf("  Auto-dropped: %d  |  Override: %d  |  Skipped (NA DOI): %d\n",
                auto, over, skip))
    cat(sprintf("  Net rows removed: %d\n", n_before - nrow(data)))
    cat(sprintf("  Log: %s\n", candidates_out))
    if (over > 0 || auto > 0) {
      cat("  To override an auto-drop, add the row to confirmed_preprint_duplicates.csv\n")
      cat("  with action = keep_both (retain both) or keep_1/keep_2 (flip survivor)\n")
    }
  }

  if (open_review_issue) {
    maybe_open_dedup_review_issue(decisions,
                                   confirmed_path = confirmed_path,
                                   candidates_out = candidates_out,
                                   stale_days     = stale_days,
                                   verbose        = verbose)
  }

  data
}
