# FLoRA structural validation
#
# Checks data quality of output/flora.csv and produces a Markdown report
# with individual checkboxes per issue. Designed to be posted to a GitHub Issue;
# resolved issues disappear on the next run.
#
# False positive suppression: check an item in the GH issue, and the next
# workflow run will save it to output/flora_validation_suppressions.csv
# and exclude it from future reports.
#
# Usage: Rscript R/validate_flora.R

library(readr)
library(dplyr)
library(stringr)
library(glue)
library(here)

source(here("R", "outcome_vocabulary.R"))

is_doi <- function(x) grepl("^10\\.[0-9]{4,}/\\S+$", x)

SUPPRESSIONS_FILE <- here("output", "flora_validation_suppressions.csv")

# ---------------------------------------------------------------------------
# Parse a GH issue body to extract checked items as suppressions.
# Returns a tibble with check_name and item_id columns.
# ---------------------------------------------------------------------------
parse_issue_suppressions <- function(issue_body_file) {
  if (!file.exists(issue_body_file)) {
    return(tibble(check_name = character(), item_id = character()))
  }
  lines <- readLines(issue_body_file, warn = FALSE)
  current_section <- ""
  results <- list()

  for (line in lines) {
    # Stop parsing at the "Checks passed" section (those are always [x])
    if (str_detect(line, "^\\*\\*Checks passed")) break

    # Bold section header: **Check name** (N issues):
    if (str_detect(line, "^\\*\\*[^*]+\\*\\*")) {
      current_section <- str_extract(line, "(?<=^\\*\\*)[^*]+(?=\\*\\*)")
    }
    # <details><summary><b>Check name</b> ...
    if (str_detect(line, "<b>[^<]+</b>")) {
      current_section <- str_extract(line, "(?<=<b>)[^<]+(?=</b>)")
    }
    # Checked item: - [x] ...
    if (str_detect(line, "^- \\[x\\]") && nzchar(current_section)) {
      item_text <- str_replace(line, "^- \\[x\\] ", "")
      item_id <- extract_item_id(item_text)
      results <- c(results, list(tibble(
        check_name = current_section, item_id = item_id
      )))
    }
  }

  if (length(results) > 0) bind_rows(results) else {
    tibble(check_name = character(), item_id = character())
  }
}

# Extract the stable ID part from an item label.
# Structural: "row_id: details" -> "row_id", or just "row_id"
# Network DOI/URL: "`value`" -> "value"
# Network retracted: "**doi_r**: `value` -- id" -> "value"
extract_item_id <- function(item_text) {
  sapply(item_text, function(txt) {
    if (str_detect(txt, "^`")) {
      # Network: `doi_or_url`
      str_extract(txt, "(?<=^`)[^`]+")
    } else if (str_detect(txt, "^\\*\\*")) {
      # Network retracted: **doi_r**: `10.xxx` ...
      str_extract(txt, "(?<=`)[^`]+(?=`)")
    } else {
      # Structural: "id: details" or just "id"
      pos <- regexpr(": ", txt, fixed = TRUE)
      if (pos > 0) substr(txt, 1, pos - 1) else txt
    }
  }, USE.NAMES = FALSE)
}

# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------
validate_flora_data <- function(data, suppressions = NULL) {

  # --- Row identifier (FLoRA has no id column) ---
  data <- data %>%
    mutate(id = paste(doi_o, coalesce(doi_r, url_r), sep = " | "))

  # --- Valid value sets ---
  # VALID_REPLICATION_OUTCOMES / VALID_REPRODUCTION_OUTCOMES come from
  # R/outcome_vocabulary.R, which both pipelines normalise against.
  VALID_TYPES <- c("replication", "reproduction")
  VALID_SOURCES <- c("COS", "replications", "reproductions")
  URL_COLS <- c("url_r", "oa_url_o", "oa_url_r")
  DETAILS_THRESHOLD <- 10
  current_year <- as.integer(format(Sys.Date(), "%Y"))

  # --- Storage ---
  all_checks <- list()
  n_suppressed <- 0L

  # --- Helpers ---
  truncate_for_label <- function(x, width = 80) {
    str_trunc(as.character(x), width = width, ellipsis = "...")
  }

  format_labels <- function(issue_df, detail_template = NULL, detail_fn = NULL) {
    if (nrow(issue_df) == 0) return(character())
    issue_df <- distinct(issue_df) %>% mutate(id = as.character(id))
    if (!is.null(detail_fn)) return(detail_fn(issue_df))
    if (!is.null(detail_template)) {
      details <- truncate_for_label(glue_data(issue_df, detail_template))
      return(unique(glue("{issue_df$id}: {details}")))
    }
    unique(issue_df$id)
  }

  add_check <- function(issue_df, check_name, fail_heading = NULL,
                        description = NULL,
                        detail_template = NULL, detail_fn = NULL) {
    issue_df <- distinct(issue_df) %>% mutate(id = as.character(id))
    if (nrow(issue_df) > 0) {
      items <- format_labels(issue_df, detail_template, detail_fn)
      # Filter out suppressed items (match by check_name or fail_heading)
      fh <- fail_heading %||% check_name
      if (!is.null(suppressions) && nrow(suppressions) > 0) {
        suppressed_ids <- suppressions$item_id[
          suppressions$check_name %in% c(check_name, fh)
        ]
        if (length(suppressed_ids) > 0) {
          keep <- !extract_item_id(items) %in% suppressed_ids
          n_suppressed <<- n_suppressed + sum(!keep)
          items <- items[keep]
        }
      }
      if (length(items) > 0) {
        all_checks[[check_name]] <<- list(
          status = "fail", items = items,
          fail_heading = fh, description = description
        )
      } else {
        all_checks[[check_name]] <<- list(status = "pass")
      }
    } else {
      all_checks[[check_name]] <<- list(status = "pass")
    }
  }

  # =========================================================================
  # Check 1: No 'NA'/'N/A' literal strings
  # =========================================================================
  issues <- data %>%
    select(id, where(is.character)) %>%
    tidyr::pivot_longer(-id, names_to = "column", values_to = "value",
                        values_drop_na = TRUE) %>%
    filter(value %in% c("NA", "N/A"))
  add_check(issues, "No 'NA'/'N/A' strings",
            fail_heading = "Literal 'NA'/'N/A' strings found",
            description = "Text columns contain the literal string 'NA' or 'N/A' instead of a proper missing value.",
            detail_template = "{column}='{value}'")

  # =========================================================================
  # Check 2: All links are valid URLs (start with http)
  # =========================================================================
  url_cols_present <- intersect(URL_COLS, names(data))
  issues <- data %>%
    select(id, all_of(url_cols_present)) %>%
    tidyr::pivot_longer(-id, names_to = "column", values_to = "value",
                        values_drop_na = TRUE) %>%
    filter(!str_starts(value, "http"))
  add_check(issues, "All links are valid URLs",
            fail_heading = "Invalid URLs (not starting with http)",
            description = "Values in url_r, oa_url_o, or oa_url_r that don't start with 'http'.",
            detail_template = "{column}='{value}'")

  # =========================================================================
  # Check 6: DOI mapped to conflicting references
  # =========================================================================
  clean_ref_text <- function(x) {
    x %>%
      str_to_lower() %>%
      str_replace_all("\\bdoi:\\s*10[./]\\S+", "") %>%
      str_replace_all("https?://(dx\\.)?doi\\.org/10[./]\\S+", "") %>%
      str_replace_all("study\\s*\\d+[a-zA-Z]?\\b", "") %>%
      str_squish()
  }

  min_norm_distance <- function(refs) {
    refs <- unique(refs[!is.na(refs)])
    if (length(refs) <= 1) return(0)
    combos <- combn(seq_along(refs), 2)
    dists <- apply(combos, 2, function(idx) {
      r1 <- refs[idx[1]]; r2 <- refs[idx[2]]
      as.numeric(adist(r1, r2)) / max(nchar(r1), nchar(r2), 1)
    })
    min(dists)
  }

  check_doi_ref_conflicts <- function(data, doi_col, ref_col, label) {
    df <- data %>%
      select(id, doi = !!sym(doi_col), ref = !!sym(ref_col)) %>%
      filter(!is.na(doi), !is.na(ref)) %>%
      mutate(ref_clean = clean_ref_text(ref))
    if (nrow(df) == 0) return(tibble())
    flagged_dois <- df %>%
      group_by(doi) %>%
      filter(n() > 1) %>%
      summarise(min_dist = min_norm_distance(ref_clean), .groups = "drop") %>%
      filter(min_dist > 0.2) %>%
      pull(doi)
    df %>% filter(doi %in% flagged_dois) %>% mutate(source = label)
  }

  issues <- bind_rows(
    check_doi_ref_conflicts(data, "doi_o", "apa_ref_o", "doi_o/apa_ref_o"),
    check_doi_ref_conflicts(data, "doi_r", "apa_ref_r", "doi_r/apa_ref_r")
  )
  add_check(issues, "DOI mapped to conflicting references",
    description = "The same DOI appears with substantially different APA reference text across rows (>20% edit distance). May indicate a wrong DOI or inconsistent reference formatting.",
    detail_fn = function(df) {
      df %>%
        mutate(id = as.character(id),
               ref_short = truncate_for_label(ref, 60),
               detail = glue("{doi} ({source}); ref='{ref_short}'"),
               detail = truncate_for_label(detail)) %>%
        {unique(glue("{.$id}: {.$detail}"))}
    }
  )

  # =========================================================================
  # Check 7: Outcome values valid
  # =========================================================================
  issues <- data %>%
    filter(
      (type == "replication" & !outcome %in% VALID_REPLICATION_OUTCOMES) |
      (type == "reproduction" & !outcome %in% VALID_REPRODUCTION_OUTCOMES) |
      is.na(outcome)
    )
  add_check(issues, "Outcome values valid",
            description = paste0(
              "Outcome values that don't match the allowed set for the row's type ",
              "(replication or reproduction). A value containing '", trimws(OUTCOME_CLASH_SEP),
              "' is a genuine outcome clash between rows merged during preprint ",
              "deduplication and needs resolving in the source coding sheet."),
            detail_template = "type={type}; outcome='{outcome}'")

  # =========================================================================
  # Check 8: Type values valid
  # =========================================================================
  issues <- data %>% filter(!type %in% VALID_TYPES)
  add_check(issues, "Type values valid",
            description = "Type must be 'replication' or 'reproduction'.",
            detail_template = "type='{type}'")

  # =========================================================================
  # Check 9: Source values valid
  # =========================================================================
  issues <- data %>% filter(!source %in% VALID_SOURCES)
  add_check(issues, "Source values valid",
            description = "Source must be 'COS', 'replications', or 'reproductions'.",
            detail_template = "source='{source}'")

  # =========================================================================
  # Check 10: Year ranges reasonable
  # =========================================================================
  issues <- data %>%
    filter(
      (!is.na(year_o) & (year_o < 1890 | year_o > current_year + 1)) |
      (!is.na(year_r) & (year_r < 1890 | year_r > current_year + 1))
    )
  add_check(issues, "Year ranges reasonable",
            description = "year_o or year_r is outside 1890\u2013current year + 1.",
            detail_template = "year_o={year_o}; year_r={year_r}")

  # =========================================================================
  # Check 11: year_r >= year_o (warning)
  # =========================================================================
  issues <- data %>%
    filter(!is.na(year_o), !is.na(year_r), year_r < year_o)
  add_check(issues, "year_r >= year_o",
            fail_heading = "Replication year before original year",
            description = "year_r < year_o. Could indicate swapped entries or a data-entry error.",
            detail_template = "year_o={year_o}; year_r={year_r}")

  # =========================================================================
  # Check 12: No exact duplicates
  # =========================================================================
  issues <- data %>%
    group_by(doi_o, doi_r) %>%
    filter(n() > 1) %>%
    ungroup()
  add_check(issues, "No exact duplicates",
            fail_heading = "Exact duplicates (by doi_o + doi_r)",
            description = "Rows that share the same doi_o and doi_r combination.")

  # =========================================================================
  # Check 13: Required fields present
  # =========================================================================
  issues <- data %>%
    filter(is.na(title_o) | is.na(title_r) | is.na(doi_o) |
           (is.na(doi_r) & is.na(url_r)))
  add_check(issues, "Required fields present",
            fail_heading = "Missing required fields",
            description = "title_o, title_r, and doi_o must be non-NA; at least one of doi_r or url_r must be present.")

  # =========================================================================
  # Check 14: DOI format valid
  # =========================================================================
  issues <- data %>%
    filter(
      (!is.na(doi_o) & !is_doi(doi_o)) |
      (!is.na(doi_r) & !is_doi(doi_r))
    )
  add_check(issues, "DOI format valid",
            fail_heading = "Invalid DOI format",
            description = "DOI does not match the expected pattern `10.NNNN/...`.",
            detail_template = "doi_o={doi_o}; doi_r={doi_r}")

  # =========================================================================
  # Build Markdown report (individual checkboxes per issue)
  # =========================================================================
  report_parts <- c(
    "## FLoRA Data Validation Report\n",
    glue("_Generated: {format(Sys.time(), '%Y-%m-%d %H:%M:%S')} | Dataset: {nrow(data)} rows_"),
    "",
    "Each row is identified as `doi_o | doi_r (or url_r)`, followed by check-specific details after `:`.",
    "Check an item to mark it as a false positive; it will be suppressed on the next run.\n"
  )

  fail_checks <- all_checks[sapply(all_checks, function(x) x$status == "fail")]
  pass_checks <- all_checks[sapply(all_checks, function(x) x$status == "pass")]

  if (length(fail_checks) > 0) {
    for (check_name in names(fail_checks)) {
      heading <- fail_checks[[check_name]]$fail_heading
      desc <- fail_checks[[check_name]]$description
      items <- fail_checks[[check_name]]$items
      n <- length(items)
      checkbox_lines <- paste0("- [ ] ", items)
      desc_line <- if (!is.null(desc)) glue("_{desc}_\n") else ""
      if (n <= DETAILS_THRESHOLD) {
        report_parts <- c(report_parts,
          glue("**{heading}** ({n} issue{ifelse(n > 1, 's', '')}):\n"),
          desc_line,
          checkbox_lines, "")
      } else {
        report_parts <- c(report_parts,
          glue("<details><summary><b>{heading}</b> ({n} issues)</summary>"),
          "",  # blank line required for GFM to render markdown inside HTML
          desc_line,
          checkbox_lines, "",
          "</details>", "")
      }
    }
  }

  if (length(pass_checks) > 0) {
    report_parts <- c(report_parts, "---\n", "**Checks passed:**")
    for (check_name in names(pass_checks)) {
      report_parts <- c(report_parts, glue("- [x] {check_name}"))
    }
    report_parts <- c(report_parts, "")
  }

  if (n_suppressed > 0) {
    report_parts <- c(report_parts,
      glue("_({n_suppressed} suppressed false positive{ifelse(n_suppressed > 1, 's', '')} not shown)_"))
  }

  has_issues <- length(fail_checks) > 0
  final_report <- paste(report_parts, collapse = "\n")
  list(report = final_report, has_issues = has_issues)
}

# === CLI entry point (only when run directly, not when sourced) ===
is_main_script <- function(script_name) {
  args <- commandArgs(trailingOnly = FALSE)
  script_arg <- args[grep("--file=", args)]
  if (length(script_arg) == 0) return(FALSE)
  script_path <- sub("--file=", "", script_arg)
  grepl(script_name, normalizePath(script_path, mustWork = FALSE), fixed = TRUE)
}

if (is_main_script("validate_flora.R") ||
    identical(Sys.getenv("FLORA_VALIDATE_RUN"), "1")) {
  input_file <- here("output", "flora.csv")
  if (!file.exists(input_file)) stop("flora.csv not found at: ", input_file)

  flora <- read_csv(input_file, show_col_types = FALSE)
  message("Read ", nrow(flora), " rows from flora.csv")

  # Load existing suppressions
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

  result <- validate_flora_data(flora, suppressions = suppressions)
  cat(result$report, "\n")
}
