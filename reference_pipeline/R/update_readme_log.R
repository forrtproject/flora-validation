#!/usr/bin/env Rscript
# update_readme_log.R
# Updates the FLoRA dataset section in README.md after each pipeline run.
#
# Renders a side-by-side static PNG (full-history month-end values | last 30
# days daily) and writes a collapsible <details> table of the full history.
# Reproductions/Replications breakdown lives in the table — at ~1500 vs ~9
# the two metrics can't share a y-axis without one becoming invisible.
#
# Usage:
#   Rscript R/update_readme_log.R <flora_csv_path> <readme_path>

suppressPackageStartupMessages({
  library(ggplot2)
  library(patchwork)
})

args <- commandArgs(trailingOnly = TRUE)

flora_path  <- if (length(args) >= 1) args[1] else file.path("output", "flora.csv")
readme_path <- if (length(args) >= 2) args[2] else "README.md"
chart_path  <- file.path("output", "flora_history.png")

START_MARKER <- "<!-- FLORA_UPDATE_LOG_START -->"
END_MARKER   <- "<!-- FLORA_UPDATE_LOG_END -->"
RECENT_DAYS  <- 30

# ── Read new flora.csv ──────────────────────────────────────────────────────
if (!file.exists(flora_path)) stop(sprintf("flora.csv not found at: %s", flora_path))

flora    <- read.csv(flora_path, check.names = FALSE)
new_rows <- nrow(flora)
date_str <- strftime(Sys.time(), "%Y-%m-%d", tz = "UTC")

replication_count  <- 0L
reproduction_count <- 0L
if ("type" %in% names(flora)) {
  counts             <- table(flora$type, useNA = "no")
  replication_count  <- if ("replication"  %in% names(counts)) as.integer(counts["replication"])  else 0L
  reproduction_count <- if ("reproduction" %in% names(counts)) as.integer(counts["reproduction"]) else 0L
}

cat(sprintf("Date         : %s\n", date_str))
cat(sprintf("Total        : %d\n", new_rows))
cat(sprintf("Replications : %d\n", replication_count))
cat(sprintf("Reproductions: %d\n", reproduction_count))

# ── Read README & locate markers ────────────────────────────────────────────
if (!file.exists(readme_path)) stop(sprintf("README.md not found at: %s", readme_path))

readme <- readLines(readme_path, warn = FALSE)
start_line <- which(readme == START_MARKER)
end_line   <- which(readme == END_MARKER)
if (length(start_line) != 1 || length(end_line) != 1 || start_line >= end_line) {
  stop("README markers missing or malformed")
}

section_text <- paste(readme[(start_line + 1):(end_line - 1)], collapse = "\n")

# ── Parse history: table preferred, mermaid fallback for legacy READMEs ─────
parse_table <- function(text) {
  lines <- strsplit(text, "\n", fixed = TRUE)[[1]]
  pat <- "^\\s*\\|\\s*(\\d{4}-\\d{2}-\\d{2})\\s*\\|\\s*(\\d+)\\s*\\|\\s*(\\d+)\\s*\\|\\s*(\\d+)\\s*\\|"
  m <- regmatches(lines, regexec(pat, lines, perl = TRUE))
  rows <- Filter(function(x) length(x) == 5, m)
  if (!length(rows)) return(NULL)
  data.frame(
    date   = vapply(rows, `[`, character(1), 2),
    total  = as.integer(vapply(rows, `[`, character(1), 3)),
    replic = as.integer(vapply(rows, `[`, character(1), 4)),
    reprod = as.integer(vapply(rows, `[`, character(1), 5)),
    stringsAsFactors = FALSE
  )
}

parse_legacy_mermaid <- function(text) {
  extract <- function(pattern) {
    m <- regexec(pattern, text, perl = TRUE)
    hits <- regmatches(text, m)[[1]]
    if (length(hits) < 2 || !nzchar(hits[2])) return(character(0))
    hits[2]
  }
  dates_raw <- extract('(?s)x-axis\\s*\\[([^\\]]*)\\]')
  if (!length(dates_raw)) return(NULL)
  dates <- gsub('"', "", trimws(strsplit(dates_raw, ",")[[1]]))
  # Legacy MM-DD labels — assume current year (best-effort migration only).
  if (all(nchar(dates) == 5)) {
    dates <- paste0(format(Sys.Date(), "%Y"), "-", dates)
  }

  line_blocks <- regmatches(text, gregexpr('line\\s+\\[([^\\]]*)\\]', text, perl = TRUE))[[1]]
  parse_line <- function(i) {
    if (length(line_blocks) < i) return(integer(0))
    inner <- sub('.*?\\[([^\\]]*)\\].*', '\\1', line_blocks[[i]], perl = TRUE)
    as.integer(trimws(strsplit(inner, ",")[[1]]))
  }
  # Legacy formats: 3 lines (total/replic/reprod) or 2 lines (replic, reprod)
  blocks <- lapply(seq_along(line_blocks), parse_line)
  n <- length(dates)
  if (!n) return(NULL)
  if (length(blocks) >= 3 && length(blocks[[1]]) == n) {
    totals <- blocks[[1]]; replic <- blocks[[2]]; reprod <- blocks[[3]]
  } else if (length(blocks) == 2 && length(blocks[[1]]) == n) {
    replic <- blocks[[1]]; reprod <- blocks[[2]]; totals <- replic + reprod
  } else {
    return(NULL)
  }
  data.frame(
    date   = dates,
    total  = totals,
    replic = if (length(replic) == n) replic else rep(NA_integer_, n),
    reprod = if (length(reprod) == n) reprod else rep(NA_integer_, n),
    stringsAsFactors = FALSE
  )
}

history <- parse_table(section_text)
if (is.null(history)) history <- parse_legacy_mermaid(section_text)
if (is.null(history)) history <- data.frame(
  date = character(0), total = integer(0), replic = integer(0), reprod = integer(0),
  stringsAsFactors = FALSE
)

# ── Append or replace today's entry ─────────────────────────────────────────
new_entry <- data.frame(
  date = date_str, total = new_rows,
  replic = replication_count, reprod = reproduction_count,
  stringsAsFactors = FALSE
)

idx <- which(history$date == date_str)
if (length(idx)) history[idx[1], ] <- new_entry else history <- rbind(history, new_entry)
history <- history[order(history$date), , drop = FALSE]
history$date <- as.Date(history$date)

# ── Render PNG ──────────────────────────────────────────────────────────────
chart_rendered <- FALSE
if (nrow(history) >= 2) {
  # Month-end snapshot: last observation in each calendar month.
  monthly <- history
  monthly$ym <- format(monthly$date, "%Y-%m")
  monthly <- do.call(rbind, lapply(
    split(monthly, monthly$ym),
    function(d) d[which.max(d$date), ]
  ))
  monthly$plot_date <- as.Date(paste0(monthly$ym, "-15"))
  monthly <- monthly[order(monthly$plot_date), , drop = FALSE]

  cutoff <- max(history$date) - RECENT_DAYS
  recent <- history[history$date >= cutoff, , drop = FALSE]

  line_color <- "#2E5C8A"

  build_panel <- function(df, x, title, subtitle, x_scale) {
    latest <- df[which.max(df[[x]]), , drop = FALSE]
    ggplot(df, aes(x = .data[[x]], y = total)) +
      geom_line(color = line_color, linewidth = 0.7) +
      geom_point(color = line_color, size = 1.8) +
      geom_text(
        data = latest,
        aes(label = format(total, big.mark = ",")),
        vjust = -1.1, hjust = 0.5, size = 3.3, color = line_color
      ) +
      x_scale +
      scale_y_continuous(
        labels = function(v) format(v, big.mark = ","),
        expand = expansion(mult = c(0.05, 0.15))
      ) +
      labs(title = title, subtitle = subtitle, x = NULL, y = "Total rows") +
      theme_minimal(base_size = 11) +
      theme(
        plot.title       = element_text(face = "bold"),
        plot.subtitle    = element_text(color = "grey40", size = 9),
        panel.grid.minor = element_blank(),
        plot.margin      = margin(8, 12, 8, 8)
      ) +
      coord_cartesian(clip = "off")
  }

  # Monthly panel: explicit breaks at actual data points so every month is
  # labelled (auto-breaks drop ticks that fall outside the data range).
  monthly_breaks <- monthly$plot_date
  if (length(monthly_breaks) > 12) {
    monthly_breaks <- monthly_breaks[seq(1, length(monthly_breaks), by = 2)]
  }

  p_all <- build_panel(
    monthly, "plot_date",
    "All-time",
    sprintf("month-end values (%d %s)", nrow(monthly),
            ifelse(nrow(monthly) == 1, "month", "months")),
    scale_x_date(
      breaks = monthly_breaks, date_labels = "%b %Y",
      expand = expansion(mult = 0.08)
    )
  )
  p_recent <- build_panel(
    recent, "date",
    sprintf("Last %d days", RECENT_DAYS),
    sprintf("daily (%d %s)", nrow(recent),
            ifelse(nrow(recent) == 1, "entry", "entries")),
    scale_x_date(
      date_breaks = if (nrow(recent) > 14) "1 week" else "3 days",
      date_labels = "%b %d",
      expand = expansion(mult = 0.05)
    )
  )

  combined <- p_all + p_recent + plot_layout(ncol = 2)

  if (!dir.exists(dirname(chart_path))) {
    dir.create(dirname(chart_path), recursive = TRUE)
  }
  res <- try(
    ggsave(chart_path, combined, width = 11, height = 4, dpi = 150, bg = "white"),
    silent = TRUE
  )
  chart_rendered <- !inherits(res, "try-error")
  if (!chart_rendered) {
    message("Chart render failed: ", conditionMessage(attr(res, "condition")))
  }
}

# ── Build new README section ────────────────────────────────────────────────
latest <- tail(history, 1)
summary_line <- sprintf(
  "> **Latest (%s):** Total = %s | Replications = %s | Reproductions = %s",
  format(latest$date),
  format(latest$total,  big.mark = ","),
  format(latest$replic, big.mark = ","),
  format(latest$reprod, big.mark = ",")
)

chart_block <- if (chart_rendered) {
  c("", sprintf("![FLoRA dataset size over time](%s)", chart_path), "")
} else character(0)

table_rows <- history[order(history$date, decreasing = TRUE), , drop = FALSE]
table_block <- c(
  sprintf("<details><summary>Full history (%d entries)</summary>", nrow(table_rows)),
  "",
  "| Date | Total | Replications | Reproductions |",
  "|------|------:|-------------:|--------------:|",
  sprintf("| %s | %d | %d | %d |",
          format(table_rows$date), table_rows$total,
          table_rows$replic, table_rows$reprod),
  "",
  "</details>"
)

new_section <- c(summary_line, chart_block, table_block)

readme_updated <- c(
  readme[seq_len(start_line)],
  new_section,
  readme[end_line:length(readme)]
)
writeLines(readme_updated, readme_path)

cat(sprintf("✓ README.md updated for %s — %d history rows; chart=%s\n",
            date_str, nrow(history), chart_rendered))
