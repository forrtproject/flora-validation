##' Extract contributors, last update and title from an OSF object URL
##'
##' This function fetches an OSF file/node URL and extracts the formatted citation
##'
##' @param url character. URL to an OSF file or node (e.g. https://osf.io/cwmb5/)
##' @param format character. One of 'apa' (default), 'bibtex' or 'both_tibble'.
##' @return a character (APA or BibTex reference) or a tibble with columns `url`, `apa`, `bibtex`.
##' @examples
##' generate_osf_citation('https://osf.io/cwmb5', format = 'apa')

library(jsonlite)
library(tibble)

generate_osf_citation <- function(url, format = c("apa", "bibtex", "both_tibble")) {
  format <- match.arg(format)
  res <- tryCatch({
    id <- basename(url)
    out <- suppressWarnings(try(fromJSON(paste0("https://api.osf.io/v2/nodes/", id, "/citation/apa/")), silent = TRUE))
    if (inherits(out, "try-error")) {
      id <- fromJSON(paste0("https://api.osf.io/v2/files/", id, "/"))$data$relationships$target$data$id
      out <- fromJSON(paste0("https://api.osf.io/v2/nodes/", id, "/citation/apa/"))
    }
    bib <- if (format != "apa") fromJSON(paste0("https://api.osf.io/v2/nodes/", id, "/citation/bibtex/"))$data$attributes$citation else NA_character_
    list(apa = out$data$attributes$citation, bib = bib)
  }, error = function(e) list(apa = NA_character_, bib = NA_character_))
  if (format == "both_tibble") return(tibble(url = url, apa = res$apa, bibtex = res$bib))
  if (format == "apa") res$apa else res$bib
}
