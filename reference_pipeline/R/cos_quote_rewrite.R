# Rewrite shorthand outcome_quote values from the COS coding sheet so each row
# reads on its own. Several large multi-study projects use coder shorthand (e.g.
# "no marked in Replicate column") instead of a real source quote; we replace
# these with a description of the project's coding logic. The replacement does
# not repeat the location (column/table/dataset) - that lives in
# outcome_quote_source - and does not name the project, since ref_r already
# identifies it.

suppressPackageStartupMessages({
  library(stringr)
})

# Standard Soto explanation: applied identically across all rating values.
.soto_rule_text <- paste(
  "The replication report rated each sub-outcome on a 0-1 scale;",
  "the row is recorded as failed only when all sub-outcomes equal 0,",
  "successful only when all equal 1, and mixed for any value strictly between."
)

.soto_quote <- function(val) {
  parens <- if (val == 0)      " (failed)"
            else if (val == 1) " (successful)"
            else               " (mixed)"
  sprintf("Subjective replication success rating = %s%s. %s",
          format(val, drop0trailing = TRUE), parens, .soto_rule_text)
}

rewrite_cos_quote_one <- function(q) {
  if (is.na(q)) return(NA_character_)
  qm <- str_squish(q)

  # 1. RP:P (Reproducibility Project: Psychology): "Replicate" column
  if (str_detect(qm, regex("^(yes|no) marked in Replicate( \\(R\\))? column$", ignore_case = TRUE))) {
    yes <- str_detect(qm, regex("^yes", ignore_case = TRUE))
    return(sprintf(
      "Coded as %s: Replicate = %s (the project's binary success indicator combining significance and direction).",
      if (yes) "successfully replicated" else "not replicated",
      if (yes) "1" else "0"))
  }

  # 2. X-Phi Replication Project: ReplicationSUCCESS column (and minor variants)
  if (str_detect(qm, regex(
    "^(YES|NO) in ReplicationSUCCESS column$|^ReplicationSUCCESS (marked as |= )(YES|NO)$",
    ignore_case = TRUE))) {
    yes <- str_detect(qm, regex("YES", ignore_case = TRUE))
    return(sprintf(
      "Coded as %s: ReplicationSUCCESS = %s (the project's pre-registered binary indicator of whether the replication met its success criterion).",
      if (yes) "successfully replicated" else "not replicated",
      if (yes) "1" else "0"))
  }

  # 3. Camerer EE (Camerer et al. 2016): "Replicated" column
  if (str_detect(qm, regex("^(Yes|No) stated in Replicated column$", ignore_case = TRUE))) {
    yes <- str_detect(qm, regex("^Yes", ignore_case = TRUE))
    return(sprintf(
      "Replicated = %s: the project's binary indicator (p < 0.05 in the same direction as the original) is %smet.",
      if (yes) "Yes" else "No",
      if (yes) "" else "not "))
  }

  # 4. Camerer SS (Camerer et al. 2018): Rep. s1 / s1+s2 columns
  m <- str_match(qm, regex("^(Yes|No) stated in the Rep\\. (s1\\+s2|s1|s2) column$", ignore_case = TRUE))
  if (!is.na(m[1, 1])) {
    yes <- tolower(m[1, 2]) == "yes"
    col <- m[1, 3]
    sample <- switch(col,
      "s1"    = "first-stage replication sample",
      "s1+s2" = "pooled first-stage and replication-sample test",
      "s2"    = "replication sample"
    )
    return(sprintf(
      "Rep. %s = %s: the %s %s significance in the same direction as the original.",
      col, if (yes) "Yes" else "No", sample, if (yes) "reaches" else "fails to reach"))
  }

  # 5. Many Labs 5 (Ebersole et al. 2020) - both protocols always present
  if (str_detect(qm, fixed("Table 3 ML5 Revised CI includes 0, ML5:RP:P Protocol CI includes 0"))) {
    return("95% CI for the meta-analytic replication includes 0 under both the RP:P and Revised Protocols (effect not significant in the same direction as the original under either).")
  }
  if (str_detect(qm, fixed("Table 3 ML5 Revised CI does not include 0, ML5:RP:P Protocol CI includes 0"))) {
    return("95% CI for the meta-analytic replication includes 0 under the RP:P Protocol but not under the Revised Protocol (effect significant in the same direction as the original only under the Revised Protocol).")
  }

  # 6. Soto repository: subjective replication success rating (many shorthand forms)
  m <- str_match(qm, regex(
    paste0(
      "^subjective replication success(?: (?:rating|score))?\\s*[:=]?\\s*(\\.?\\d+(?:\\.\\d+)?)$",
      "|^sub_rep (\\.?\\d+(?:\\.\\d+)?) in dataset$",
      "|^(\\.?\\d+(?:\\.\\d+)?) \\[In figure 2,.*?\\]\\.?\\s*subjective replication success rating \\.?\\d+(?:\\.\\d+)?\\s*$",
      "|^subjective replication success rating (\\.?\\d+(?:\\.\\d+)?)\\.?\\s*\\.?\\d+(?:\\.\\d+)? \\[In figure 2,.*?\\]\\.?\\s*$"
    ),
    ignore_case = TRUE))
  if (!is.na(m[1, 1])) {
    raw <- if (!is.na(m[1, 2])) m[1, 2]
           else if (!is.na(m[1, 3])) m[1, 3]
           else if (!is.na(m[1, 4])) m[1, 4]
           else m[1, 5]
    val <- as.numeric(if (startsWith(raw, ".")) paste0("0", raw) else raw)
    return(.soto_quote(val))
  }

  # 7. Soto repository: mixed because sub-outcome ratings split
  if (str_detect(qm, regex("^mixed as not both columns 1 or 0$", ignore_case = TRUE))) {
    return(paste(
      "Classified as mixed: sub-outcome ratings were neither all 0 nor all 1.",
      .soto_rule_text))
  }

  # 8. Website "Result Type" badges
  m <- str_match(qm, regex("^Result Type\\s+(Successful Replication|Failure to Replicate)$", ignore_case = TRUE))
  if (!is.na(m[1, 1])) {
    return(sprintf(
      "Outcome classified as \"%s\" (the project's own label).",
      m[1, 2]))
  }

  q
}

rewrite_cos_quote <- function(quotes) {
  vapply(quotes, rewrite_cos_quote_one, character(1), USE.NAMES = FALSE)
}
