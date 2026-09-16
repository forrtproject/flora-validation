# LLM-based replication intent classification
# Uses ellmer package with Google Gemini to classify whether replication authors
# set out to replicate a finding vs. results happening to replicate.

library(dplyr)
library(ellmer)
library(purrr)

REPLICATION_INTENT_SYSTEM_PROMPT <- "
You are classifying whether a scientific paper was *designed* as a replication study.
A study counts as a self-identified replication if the authors set out to replicate,
reproduce, or re-test a specific prior finding.

It does NOT count if:
- The study merely cites or builds on prior work
- Results happen to be consistent with prior findings
- It's a meta-analysis or review

Given the replication paper's title and abstract (and the original paper's title for context),
assess whether the replication authors intended to replicate the original finding.
"

REPLICATION_INTENT_TYPE <- type_object(
  self_identified_replication = type_boolean(
    "Did the authors set out to replicate the original finding? TRUE if the paper was designed as a replication study."
  ),
  confidence = type_enum(
    "Confidence in the classification",
    values = c("high", "medium", "low")
  ),
  reasoning = type_string(
    "Brief explanation (1-2 sentences) for the classification"
  )
)

#' Build prompt for a single replication pair
#'
#' @param title_o Title of the original paper
#' @param title_r Title of the replication paper
#' @param abstract_r Abstract of the replication paper (may be NA)
#' @return Character string prompt
build_replication_intent_prompt <- function(title_o, title_r, abstract_r) {
  abstract_part <- if (is.na(abstract_r) || !nzchar(trimws(abstract_r))) {
    "Abstract: [not available]"
  } else {
    paste0("Abstract: ", abstract_r)
  }

  paste0(
    "Original paper title: ", ifelse(is.na(title_o), "[not available]", title_o), "\n\n",
    "Replication paper title: ", ifelse(is.na(title_r), "[not available]", title_r), "\n",
    abstract_part
  )
}

# Manual overrides: replication DOIs where the LLM misclassifies because the
# paper is a meta-study that also serves as the published data source for
# individual replication results (e.g. multi-lab projects).
# All pairs involving these doi_r values are forced to TRUE / high.
REPLICATION_INTENT_DOI_OVERRIDES <- c(
  "10.1098/rsos.231240"  # "Eleven years of student replication projects ..."
)

# URL patterns that indicate the paper is a self-identified replication.
# PsychFileDrawer was a site specifically for reporting replication attempts.
REPLICATION_INTENT_URL_OVERRIDES <- c(
  "psychfiledrawer",
  "osf.io/8nzfm"
)

#' Classify replication intent for a data frame of paper pairs
#'
#' @param df Data frame with columns: doi_o, doi_r, title_o, title_r, abstract_r
#' @param cache_file Path to RDS cache file
#' @param model Gemini model to use
#' @param max_active Max concurrent requests for parallel chat
#' @param rpm Requests per minute limit
#' @return Data frame with added columns: self_identified_replication, replication_intent_confidence, replication_intent_reasoning
classify_replication_intent <- function(df,
                                        cache_file = LLM_REPLICATION_INTENT_CACHE,
                                        model = "gemini-3-flash-preview",
                                        max_active = 10,
                                        rpm = 500) {
  # Validate required columns
  required_cols <- c("doi_o", "doi_r", "title_o", "title_r")
  missing_cols <- setdiff(required_cols, names(df))
  if (length(missing_cols) > 0) {
    stop("Missing required columns: ", paste(missing_cols, collapse = ", "))
  }

  # Ensure abstract_r exists
  if (!"abstract_r" %in% names(df)) {
    df$abstract_r <- NA_character_
  }


  # Ensure url_r exists for cache key fallback
  if (!"url_r" %in% names(df)) df$url_r <- NA_character_

  # Create cache key from doi pair; use url_r when doi_r is NA to avoid collisions
  df <- df %>%
    mutate(.cache_key = paste(doi_o, coalesce(doi_r, url_r, title_r), sep = " | "))

  # Load cache
  cache <- if (file.exists(cache_file)) readRDS(cache_file) else list()

  # Find which rows need classification
  already_cached <- df$.cache_key %in% names(cache)
  to_classify <- df[!already_cached, ]

  cat(sprintf("Replication intent: %d cached, %d to classify\n",
              sum(already_cached), nrow(to_classify)))

  if (nrow(to_classify) > 0) {
    tryCatch({
      # Build prompts (must be a list for parallel_chat_structured)
      prompts <- pmap(
        list(to_classify$title_o, to_classify$title_r, to_classify$abstract_r),
        function(t_o, t_r, a_r) build_replication_intent_prompt(t_o, t_r, a_r)
      )

      # Create chat object
      chat <- chat_google_gemini(
        system_prompt = REPLICATION_INTENT_SYSTEM_PROMPT,
        model = model
      )

      # Run parallel structured classification
      results <- parallel_chat_structured_robust(
        chat = chat,
        prompts = prompts,
        type = REPLICATION_INTENT_TYPE,
        max_active = max_active,
        rpm = rpm
      )

      # results is a data.frame with one row per prompt
      # Store each row as a list in the cache
      for (i in seq_len(nrow(to_classify))) {
        key <- to_classify$.cache_key[i]
        cache[[key]] <- list(
          self_identified_replication = results$self_identified_replication[i],
          confidence = as.character(results$confidence[i]),
          reasoning = results$reasoning[i]
        )
      }

      # Save updated cache
      dir.create(dirname(cache_file), showWarnings = FALSE, recursive = TRUE)
      saveRDS(cache, cache_file)
      cat(sprintf("  Classified %d new pairs, cache now has %d entries\n",
                  nrow(to_classify), length(cache)))
    }, error = function(e) {
      warning(sprintf(
        "LLM classification skipped for %d uncached rows (API unavailable): %s\n",
        nrow(to_classify), conditionMessage(e)
      ))
      cat("  Uncached rows will have NA replication intent — set GEMINI_API_KEY secret to classify them.\n")
    })
  }

  # Map cache results back to all rows
  df$self_identified_replication <- NA
  df$replication_intent_confidence <- NA_character_
  df$replication_intent_reasoning <- NA_character_

  for (i in seq_len(nrow(df))) {
    key <- df$.cache_key[i]
    if (key %in% names(cache)) {
      result <- cache[[key]]
      if (!is.null(result)) {
        df$self_identified_replication[i] <- result$self_identified_replication
        df$replication_intent_confidence[i] <- result$confidence
        df$replication_intent_reasoning[i] <- result$reasoning
      }
    }
  }

  df$.cache_key <- NULL

  # Apply manual overrides
  # 1. DOI-based overrides (multi-study replication papers)
  doi_mask <- !is.na(df$doi_r) & tolower(df$doi_r) %in% tolower(REPLICATION_INTENT_DOI_OVERRIDES)

  # 2. URL-based overrides (replication-specific platforms like PsychFileDrawer)
  url_mask <- rep(FALSE, nrow(df))
  if ("url_r" %in% names(df)) {
    for (pattern in REPLICATION_INTENT_URL_OVERRIDES) {
      url_mask <- url_mask | grepl(pattern, df$url_r, ignore.case = TRUE)
    }
  }

  override_mask <- doi_mask | url_mask
  n_overridden <- sum(override_mask & (df$self_identified_replication != TRUE | is.na(df$self_identified_replication)))
  if (any(override_mask)) {
    df$self_identified_replication[override_mask] <- TRUE
    df$replication_intent_confidence[override_mask] <- "high"
    df$replication_intent_reasoning[override_mask] <- ifelse(
      doi_mask[override_mask],
      "Manual override: multi-study replication paper",
      "Manual override: replication-specific platform (PsychFileDrawer)"
    )[seq_len(sum(override_mask))]
    cat(sprintf("  Overridden %d rows (%d DOI-based, %d URL-based)\n",
                n_overridden, sum(doi_mask & override_mask), sum(url_mask & !doi_mask & override_mask)))
  }

  df
}
