library(dplyr)
library(glue)
library(stringr)
library(tidyr)

#' Cleans a FReD dataframe with safe, automated actions.
#'
#' @param data A raw FReD dataframe.
#' @return A list containing `cleaned_data` (the cleaned dataframe) and `report`
#'         (a Markdown string detailing the cleaning actions taken).
clean_fred_data <- function(data) {

  # --- Storage for report items ---
  cleaning_report <- list()

  # --- Create a copy to modify ---
  cleaned_data <- data

  # --- Helper function to report changes ---
  add_report_item <- function(report_list, message) {
    report_list[[length(report_list) + 1]] <- message
    return(report_list)
  }

  # --- Helper to standardize test statistics and append sample sizes where available ---
  standardize_es_stat <- function(val, n_val) {
    if (length(val) == 0 || is.na(val)) return(val)

    clean_n <- NULL
    if (!is.na(n_val) && nchar(trimws(as.character(n_val))) > 0) {
      clean_n <- format(n_val, scientific = FALSE, trim = TRUE)
    }

    val <- str_replace_all(val, regex("[χx]2|χ²", ignore_case = TRUE), "X2")
    val <- str_replace_all(val, "(^t|^F)\\s+", "\\1")
    val <- str_replace_all(val, "^z", "Z")
    val <- str_replace_all(val, "X2\\s+\\(", "X2(")
    val <- str_replace_all(val, "F\\( ", "F(")
    val <- str_replace_all(val, "\\s*<\\s*", " < ")
    val <- str_replace_all(val, "\\s*≤\\s*", " ≤ ")
    val <- str_replace_all(val, "\\s*=\\s*", " = ")
    val <- str_replace_all(val, "\\s*,\\s*", ", ")
    val <- str_replace_all(val, "-\\s+", "-")
    val <- str_replace_all(val, "[-–—−]", "-")
    val <- str_replace_all(val, "\\[", "(")
    val <- str_replace_all(val, "\\]", ")")
    val <- str_squish(val)

    # Add/standardize N for chi-squared stats
    x2_pattern <- regex("^X2\\s*\\(\\s*(\\d+(?:\\.\\d+)?)\\s*(?:,\\s*N\\s*=\\s*(\\d+(?:\\.\\d+)?))?\\s*\\)(.*)$", ignore_case = TRUE)
    x2_match <- str_match(val, x2_pattern)
    if (!is.na(x2_match[1, 1])) {
      df <- x2_match[1, 2]
      n_existing <- x2_match[1, 3]
      rest <- x2_match[1, 4]
      n_to_use <- if (!is.na(n_existing) && n_existing != "") n_existing else if (!is.null(clean_n)) clean_n else NA
      rest_clean <- str_trim(rest)
      rest_clean <- ifelse(rest_clean == "", "", paste0(" ", rest_clean))
      val <- if (!is.na(n_to_use)) glue("X2({df}, N = {n_to_use}){rest_clean}") else glue("X2({df}){rest_clean}")
    }

    # Add/standardize N for Z stats
    z_pattern <- regex("^Z\\s*=\\s*([\\-\\+]?\\d*\\.?\\d+(?:[eE][\\-\\+]?\\d+)?)(?:\\s*,\\s*N\\s*=\\s*(\\d+(?:\\.\\d+)?))?(.*)$", ignore_case = TRUE)
    z_match <- str_match(val, z_pattern)
    if (!is.na(z_match[1, 1])) {
      z_val <- z_match[1, 2]
      n_existing <- z_match[1, 3]
      rest <- z_match[1, 4]
      n_to_use <- if (!is.na(n_existing) && n_existing != "") n_existing else if (!is.null(clean_n)) clean_n else NA
      rest_clean <- str_trim(rest)
      rest_clean <- ifelse(rest_clean == "", "", paste0(" ", rest_clean))
      if (!is.na(n_to_use)) {
        val <- glue("Z = {z_val}, N = {n_to_use}{rest_clean}")
      } else {
        val <- glue("Z = {z_val}{rest_clean}")
      }
    }

    str_squish(val)
  }

  # --- 1. Trim and squish Whitespace from all character columns ---
  char_cols <- names(cleaned_data)[sapply(cleaned_data, is.character)]
  trimmed_changes <- cleaned_data %>%
    select(all_of(char_cols)) %>% # Operate only on character columns
    mutate(across(everything(), ~ str_squish(.))) %>%
    mutate(across(everything(), ~ str_trim(.))) %>%
    summarise(across(everything(), ~ sum(.x != data[[cur_column()]], na.rm = TRUE))) %>%
    pivot_longer(everything()) %>%
    filter(value > 0)

  if (nrow(trimmed_changes) > 0) {
    # Apply the change
    cleaned_data <- cleaned_data %>%
      mutate(across(all_of(char_cols), ~ str_squish(.))) %>%
      mutate(across(all_of(char_cols), ~ str_trim(.)))
    report_message <- glue("Trimmed leading/trailing/repeated whitespace from {sum(trimmed_changes$value)} cell(s) in columns: `{paste(trimmed_changes$name, collapse=', ')}`.")
    cleaning_report <- add_report_item(cleaning_report, report_message)
  }

  # --- 2. Remove Non-Printable Characters ---
  non_printable_pattern <- "[^[:print:]]"

  # Find all cells with non-printable characters before cleaning
  affected_cells <- cleaned_data %>%
    select(all_of(char_cols)) %>%
    mutate(row = row_number()) %>%
    pivot_longer(-row, names_to = "column", values_to = "value") %>%
    filter(str_detect(value, non_printable_pattern))

  if (nrow(affected_cells) > 0) {
    # Extract the specific characters for descriptive reporting
    all_bad_chars <- unlist(str_extract_all(affected_cells$value, non_printable_pattern))
    unique_bad_chars <- unique(all_bad_chars)

    # Helper function to describe characters by their common name or Unicode point
    describe_char <- function(ch) {
      code_point <- utf8ToInt(ch)
      known_chars <- c(
        "\v" = "Vertical Tab", "\u00A0" = "Non-Breaking Space",
        "\a" = "Bell", "\b" = "Backspace", "\f" = "Form Feed"
      )
      # Check for name, otherwise return Unicode ID
      if (ch %in% names(known_chars)) {
        return(sprintf("%s (U+%04X)", known_chars[ch], code_point))
      } else {
        return(sprintf("U+%04X", code_point))
      }
    }
    char_descriptions <- sapply(unique_bad_chars, describe_char, USE.NAMES = FALSE)

    # Generate the report message
    report_message <- glue("Removed non-printable characters from {nrow(affected_cells)} cell(s). Characters found: `{paste(char_descriptions, collapse=', ')}`.")
    cleaning_report <- add_report_item(cleaning_report, report_message)

    # Apply the cleaning action
    cleaned_data <- cleaned_data %>%
      mutate(across(all_of(char_cols), ~ str_replace_all(., non_printable_pattern, "")))
  }

  # --- 3. Standardize NA values (convert "NA", "N/A" to NA) ---
  na_changes_before <- sum(sapply(cleaned_data, function(x) sum(is.na(x))))

  cleaned_data <- cleaned_data %>%
    mutate(across(where(is.character), ~ na_if(., "NA"))) %>%
    mutate(across(where(is.character), ~ na_if(., "N/A"))) %>%
    mutate(across(where(is.character), ~ na_if(., "#N/A")))   %>%
    mutate(across(where(is.character), ~ na_if(., "not reported"))) %>%
    mutate(across(where(is.character), ~ na_if(., "NOT REPORTED"))) %>%
    mutate(across(where(is.character), ~ na_if(., "NA (not reported)")))


  na_changes_after <- sum(sapply(cleaned_data, function(x) sum(is.na(x))))
  na_rows_affected <- na_changes_after - na_changes_before

  if (na_rows_affected > 0) {
    report_message <- glue("Converted {na_rows_affected} string(s) ('NA' or 'N/A') to proper `NA` values.")
    cleaning_report <- add_report_item(cleaning_report, report_message)
  }

  # --- 4. Clean Reference Columns (remove DOIs) ---
  ref_cols <- c("ref_o", "ref_r")
  doi_pattern <- "\\s*(doi: ?10[./]\\S+|https?://(dx\\.)?doi\\.org/10[./]\\S+)"

  ref_changes <- cleaned_data %>%
    summarise(across(all_of(ref_cols), ~ sum(str_detect(., regex(doi_pattern, ignore_case = TRUE)), na.rm = TRUE))) %>%
    pivot_longer(everything()) %>%
    filter(value > 0)

  if (nrow(ref_changes) > 0) {
    cleaned_data <- cleaned_data %>%
      mutate(across(all_of(ref_cols), ~ str_remove_all(., regex(doi_pattern, ignore_case = TRUE))))
    total_ref_changes <- sum(ref_changes$value)
    report_message <- glue("Removed DOI strings (both URL and prefix format) from {total_ref_changes} cell(s) in `ref_o`/`ref_r` columns.")
    cleaning_report <- add_report_item(cleaning_report, report_message)
  }

  # --- 5. Clean DOI Columns (remove URL/prefix variants and standardize case/spacing) ---
  doi_cols <- c("doi_o", "doi_r")
  doi_prefix_regex <- regex("^(https?://(dx\\.)?doi\\.org/|doi:\\s*|doi\\.org/)", ignore_case = TRUE)

  doi_prefix_changes <- cleaned_data %>%
    summarise(across(all_of(doi_cols), ~ sum(str_detect(., doi_prefix_regex), na.rm = TRUE))) %>%
    pivot_longer(everything()) %>%
    filter(value > 0)

  if (nrow(doi_prefix_changes) > 0) {
    cleaned_data <- cleaned_data %>%
      mutate(across(all_of(doi_cols), ~ str_remove(., doi_prefix_regex)))
    total_doi_prefix_changes <- sum(doi_prefix_changes$value)
    report_message <- glue("Removed DOI prefixes/URLs from {total_doi_prefix_changes} cell(s) in `doi_o`/`doi_r` columns (e.g., 'doi:', 'doi.org/', 'https://doi.org/').")
    cleaning_report <- add_report_item(cleaning_report, report_message)
  }

  # Convert DOI to lowercase
  doi_lowercase_changes <- cleaned_data %>%
    summarise(across(all_of(doi_cols), ~ sum(. != tolower(.), na.rm = TRUE))) %>%
    pivot_longer(everything()) %>%
    filter(value > 0)
  if (nrow(doi_lowercase_changes) > 0) {
    cleaned_data <- cleaned_data %>%
      mutate(across(all_of(doi_cols), ~ tolower(.)))
    total_doi_lowercase_changes <- sum(doi_lowercase_changes$value)
    report_message <- glue("Converted {total_doi_lowercase_changes} cell(s) in `doi_o`/`doi_r` columns to lowercase.")
    cleaning_report <- add_report_item(cleaning_report, report_message)
  }

  # --- 6. Clean ES Value Columns (Standardize stats, spacing, brackets) ---
  es_cols <- c("es_value_o", "es_value_r")
  data_before_es_clean <- cleaned_data

  cleaned_data <- cleaned_data %>%
    mutate(
      es_value_o = mapply(standardize_es_stat, es_value_o, n_o, USE.NAMES = FALSE),
      es_value_r = mapply(standardize_es_stat, es_value_r, n_r, USE.NAMES = FALSE)
    )

  es_changes <- sum(cleaned_data$es_value_o != data_before_es_clean$es_value_o, na.rm = TRUE) +
    sum(cleaned_data$es_value_r != data_before_es_clean$es_value_r, na.rm = TRUE)

  if (es_changes > 0) {
    report_message <- glue("Standardized formatting for {es_changes} cell(s) in `es_value_o`/`es_value_r` (e.g., 'X2', spacing around '=', brackets).")
    cleaning_report <- add_report_item(cleaning_report, report_message)
  }

  # --- 7. Clean DOIs (remove spaces) ---
  cleaned_data <- cleaned_data %>%
    mutate(across(all_of(doi_cols), ~ str_remove_all(., " ")))
    

  # --- 8. Normalize OSF links missing scheme ---
  osf_cols <- intersect(c("url_r", "link_o", "link_r", "prereg_o", "prereg_r"), names(cleaned_data))
  if (length(osf_cols) > 0) {
    osf_prefix_changes <- cleaned_data %>%
      summarise(across(all_of(osf_cols), ~ sum(str_starts(., "osf.io/"), na.rm = TRUE))) %>%
      pivot_longer(everything()) %>%
      filter(value > 0)

    if (nrow(osf_prefix_changes) > 0) {
      cleaned_data <- cleaned_data %>%
        mutate(across(all_of(osf_cols), ~ str_replace(., regex("^osf\\.io/", ignore_case = TRUE), "https://osf.io/")))
      total_osf_prefix_changes <- sum(osf_prefix_changes$value)
      report_message <- glue("Prefixed 'https://' to {total_osf_prefix_changes} OSF link(s) missing the scheme in columns: `{paste(osf_prefix_changes$name, collapse = ', ')}`.")
      cleaning_report <- add_report_item(cleaning_report, report_message)
    }
  }

  # --- 9. Fix ids ---
  cleaned_data <- cleaned_data %>%
    arrange(as.numeric(rowid)) %>%
    mutate(fred_id = str_remove(id, "_[a-z]{1,2}$"),
           fred_id_old = id,
           effect_id = rowid) %>%
    group_by(ref_o, study_o, ref_r, study_r) %>%
    mutate(entry_id = first(rowid)) %>%
    ungroup()

  # --- Finalize Report ---
  final_report_string <- ""
  if (length(cleaning_report) > 0) {
    report_header <- "## FReD Automatic Cleaning Report\n\n"
    report_body <- paste0("- ✅ ", cleaning_report, collapse = "\n")
    final_report_string <- paste0(report_header, "Sorted by rowid and generated fred_id and effect_id.\n\n", report_body)
  } else {
    final_report_string <- "## FReD Automatic Cleaning Report\n\nSorted by rowid and generated fred_id and effect_id.\n\n- No automatic cleaning actions were required."
  }

  return(list(cleaned_data = cleaned_data, report = final_report_string))
}
