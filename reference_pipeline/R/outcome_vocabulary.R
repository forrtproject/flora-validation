# Canonical outcome vocabulary for FLoRA and FReD
#
# FLoRA (`outcome`) and FReD (`reported_success`) draw on the same set of coding
# sheets, but each sheet has drifted slightly in how it spells the codes
# (underscores vs spaces, "descriptive" vs "descriptive only", "unclear" vs
# "uninformative", legacy "success"/"failure"). Left unnormalised, the variants
# survive into the released datasets and — worse — make `pick_outcome()` in
# preprint_dedup.R treat a spelling difference as a genuine outcome clash,
# producing concatenated values like "successful || success".
#
# This file is the single source of truth for the allowed values. Both pipelines
# normalise against it at ingest; validate_flora.R validates against it.

# --- Allowed values -------------------------------------------------------

VALID_REPLICATION_OUTCOMES <- c(
  "successful",
  "failed",
  "mixed",
  "uninformative",
  "descriptive only",
  "statistically successful but flawed"
)

VALID_REPRODUCTION_OUTCOMES <- c(
  "computationally successful, robust",
  "computationally successful, robustness challenges",
  "computationally successful, robustness not checked",
  "computational issues, robust",
  "computational issues, robustness challenges",
  "computational issues, robustness not checked",
  "computation not checked, robust",
  "computation not checked, robustness challenges",
  "computation not checked, robustness not checked"
)

VALID_OUTCOMES <- c(VALID_REPLICATION_OUTCOMES, VALID_REPRODUCTION_OUTCOMES)

# Separator used to retain a genuine clash between different outcomes on rows
# that are merged during preprint deduplication. Such values are deliberately
# NOT in VALID_OUTCOMES, so validate_flora.R's outcome check surfaces them.
OUTCOME_CLASH_SEP <- " || "

# --- Aliases --------------------------------------------------------------
# Keys are match-normalised (lowercase, underscores and punctuation runs
# collapsed to single spaces). Only variants actually observed in the source
# sheets or in previously released data are listed — an unrecognised value is
# passed through untouched so validation flags it rather than silently
# absorbing it into the wrong category.

OUTCOME_ALIASES <- c(
  # replications sheet
  "statistically successful but fundamentally flawed" = "statistically successful but flawed",
  "statistically successful but flawed"               = "statistically successful but flawed",
  # NB: "unclear" and "cannot be determined" are deliberately NOT aliased to
  # "uninformative" — they mean something different (the coder could not reach a
  # verdict, vs. the study being unable to inform the claim). They stay
  # unmapped so validate_flora.R flags them for re-coding at source.
  # validated_export (i4r / openalex imports)
  "descriptive"                                       = "descriptive only",
  # legacy vocabulary from pre-2026 imports
  "success"                                           = "successful",
  "failure"                                           = "failed",
  "successful replication"                            = "successful",
  "failed replication"                                = "failed",
  "mixed results"                                     = "mixed"
)

#' Match-normalise an outcome string for alias lookup.
#'
#' Lowercases, turns underscores/hyphens into spaces, and squishes whitespace.
#' Commas are preserved because the reproduction codes are comma-delimited
#' two-part labels (e.g. "computational issues, robust").
match_key <- function(x) {
  x <- tolower(trimws(as.character(x)))
  x <- gsub("[_\\-]+", " ", x)
  x <- gsub("\\s+", " ", x)
  trimws(x)
}

#' Recode outcome values onto the canonical vocabulary.
#'
#' Handles clash values produced by preprint deduplication ("A || B") by
#' normalising each component: if the components collapse to a single canonical
#' value the clash disappears, otherwise it is retained for validation to pick
#' up.
#'
#' @param x Character vector of raw outcome values.
#' @return Character vector of the same length. Values with no known canonical
#'   form are returned trimmed but otherwise unchanged.
normalise_outcome <- function(x) {
  x <- as.character(x)
  out <- vapply(x, function(v) {
    if (is.na(v)) return(NA_character_)
    v <- trimws(v)
    if (!nzchar(v)) return(NA_character_)

    parts <- trimws(strsplit(v, OUTCOME_CLASH_SEP, fixed = TRUE)[[1]])
    parts <- parts[nzchar(parts)]
    if (length(parts) == 0) return(NA_character_)

    keys <- match_key(parts)
    canonical <- ifelse(
      keys %in% names(OUTCOME_ALIASES), OUTCOME_ALIASES[keys],
      ifelse(keys %in% match_key(VALID_OUTCOMES),
             VALID_OUTCOMES[match(keys, match_key(VALID_OUTCOMES))],
             parts)
    )
    canonical <- unique(unname(canonical))
    paste(canonical, collapse = OUTCOME_CLASH_SEP)
  }, character(1), USE.NAMES = FALSE)
  out
}

#' Report how many values a normalisation pass changed, and to what.
#'
#' Returns a data frame of from/to/n for logging in the pipelines.
outcome_recode_summary <- function(before, after) {
  changed <- !is.na(before) & (is.na(after) | before != after)
  if (!any(changed)) {
    return(data.frame(from = character(), to = character(), n = integer()))
  }
  tab <- table(before[changed], after[changed])
  idx <- which(tab > 0, arr.ind = TRUE)
  data.frame(
    from = rownames(tab)[idx[, "row"]],
    to   = colnames(tab)[idx[, "col"]],
    n    = as.integer(tab[idx]),
    stringsAsFactors = FALSE
  )
}
