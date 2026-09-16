# OSF Release Helper Functions
# Refactored from release/release_dataset.R to be more modular and pipeline-friendly

library(dplyr)
library(glue)
library(openxlsx)
library(osfr)
library(stringr)

# ============================================================================
# Version Management
# ============================================================================

#' Extract current version from changelog markdown
#' @param markdown_content Character string containing changelog markdown
#' @return Character string with version number (e.g., "1.2.3")
extract_current_version <- function(markdown_content) {
  version_pattern <- "(?<=\\*\\*Version:\\*\\* )\\d+\\.\\d+\\.\\d+"
  version_matches <- str_extract(markdown_content, version_pattern)
  version_matches[!is.na(version_matches)][1]  # Return first non-NA match
}

#' Increment version number
#' @param version Character string with current version (e.g., "1.2.3")
#' @param version_type One of "major", "minor", "patch"
#' @return Character string with incremented version
increment_version <- function(version, version_type = c("major", "minor", "patch")) {
  version_type <- match.arg(version_type)
  version_numbers <- as.numeric(str_split(version, "[.]")[[1]])

  if (version_type == "major") {
    version_numbers[1] <- version_numbers[1] + 1
    version_numbers[2] <- 0
    version_numbers[3] <- 0
  } else if (version_type == "minor") {
    version_numbers[2] <- version_numbers[2] + 1
    version_numbers[3] <- 0
  } else if (version_type == "patch") {
    version_numbers[3] <- version_numbers[3] + 1
  }

  paste(version_numbers, collapse = ".")
}

# ============================================================================
# Changelog Management
# ============================================================================

#' Update changelog markdown with new version and release notes
#' @param markdown_content Current changelog content
#' @param new_version New version string
#' @param release_notes Release notes text
#' @param release_date Release date (defaults to today)
#' @return Updated markdown content
update_markdown_metadata <- function(markdown_content,
                                    new_version,
                                    release_notes,
                                    release_date = Sys.Date()) {
  formatted_date <- format(release_date, "%Y-%m-%d")

  # Update version line
  version_pattern <- "(### Current Version\\n- \\*\\*Version:\\*\\* )[\\d\\.v]+(?: \\(.*?\\))?"
  version_replacement <- glue("\\1{new_version} ({formatted_date})")
  markdown_content <- sub(version_pattern, version_replacement, markdown_content, perl = TRUE)

  # Format and insert new release notes
  new_notes_formatted <- paste0("    ", str_replace_all(release_notes, "\n", "\n    "))
  new_notes_section <- glue("### Latest Release Notes\n- **Notes for Version {new_version} ({formatted_date})**\n{new_notes_formatted}\n\n### Previous Release Notes")

  markdown_content <- sub("### Latest Release Notes", new_notes_section, markdown_content)

  # Clean up markdown formatting
  split_content <- str_split(markdown_content, "(### Previous Release Notes\n)", n = 2, simplify = TRUE)
  if (length(split_content) > 1) {
    split_content[2] <- split_content[2] %>%
      str_remove_all("### Previous Release Notes\n") %>%
      str_replace_all("\n\n", "\n")
    markdown_content <- paste0(split_content[1], "### Previous Release Notes\n", split_content[2])
  }

  markdown_content
}

#' Prepare changelog for new release
#' @param release_notes Character string with release notes
#' @param version_type One of "major", "minor", "patch"
#' @param changelog_file OSF file ID or path for changelog (e.g., "fj3xc")
#' @return List with old_version and new_changelog
prepare_changelog <- function(release_notes,
                             version_type = c("major", "minor", "patch"),
                             changelog_file = "https://osf.io/fj3xc") {
  version_type <- match.arg(version_type)

  temp_dir <- tempdir()

  # Download current changelog
  changelog_node <- osfr::osf_retrieve_file(changelog_file)
  osfr::osf_download(changelog_node, temp_dir, conflicts = "overwrite")

  markdown_content <- readLines(file.path(temp_dir, "change_log.md"), warn = FALSE) %>%
    paste(collapse = "\n")

  current_version <- extract_current_version(markdown_content)
  next_version <- increment_version(current_version, version_type)

  markdown_content <- update_markdown_metadata(
    markdown_content = markdown_content,
    new_version = next_version,
    release_notes = release_notes
  )

  list(old_version = current_version, new_changelog = markdown_content)
}

#' Upload changelog to OSF
#' @param changelog Character string with updated changelog
#' @param osf_folder OSF folder node
upload_changelog <- function(changelog, osf_folder) {
  temp_dir <- tempdir()
  writeLines(changelog, file.path(temp_dir, "change_log.md"))
  osfr::osf_upload(osf_folder, file.path(temp_dir, "change_log.md"), conflicts = "overwrite")
}

# ============================================================================
# File Management
# ============================================================================

#' Download, archive, and upload dataset file to OSF
#' @param data_folder OSF folder node where data is stored
#' @param old_version Current version string (for archiving)
#' @param filename Name of the file to upload (e.g., "FReD.xlsx")
#' @param github_raw_url GitHub raw URL to download from (optional)
#' @param gsheet_link Google Sheets download link (optional fallback)
#' @param keep_sheets For Excel files: which sheets to keep (others removed)
#' @return Invisible NULL
process_file <- function(data_folder,
                        old_version,
                        filename = "FReD.xlsx",
                        github_raw_url = NULL,
                        gsheet_link = NULL,
                        keep_sheets = c("Data", "FORRT R&R (editable)", "Additional Studies to be added",
                                       "Contributors FReD", "key variables")) {

  temp_dir <- tempdir()

  # Find and download current file from OSF for archiving
  data_file <- osfr::osf_ls_files(data_folder) %>%
    filter(name == filename)

  if (nrow(data_file) > 0) {
    osfr::osf_download(data_file, temp_dir, conflicts = "overwrite")

    # Archive with version
    archived_name <- glue("{tools::file_path_sans_ext(filename)}_{old_version}.{tools::file_ext(filename)}")
    file.rename(file.path(temp_dir, filename), file.path(temp_dir, archived_name))

    # Upload archive
    archive_folder <- osfr::osf_ls_files(data_folder, type = "folder") %>%
      filter(name == "Archive")

    if (nrow(archive_folder) > 0) {
      osfr::osf_upload(archive_folder, file.path(temp_dir, archived_name), conflicts = "overwrite")
    }
  }

  # Download new file
  dest <- file.path(temp_dir, filename)

  if (!is.null(github_raw_url)) {
    download.file(github_raw_url, dest, mode = "wb")
    message("Downloaded from GitHub: ", github_raw_url)
  } else if (!is.null(gsheet_link)) {
    download.file(gsheet_link, dest, mode = "wb")
    message("Downloaded from Google Sheets: ", gsheet_link)
  } else {
    stop("Must provide either 'github_raw_url' or 'gsheet_link'")
  }

  # For Excel files, optionally remove unused sheets
  if (endsWith(filename, ".xlsx")) {
    tryCatch({
      wb <- loadWorkbook(dest)
      all_sheets <- getSheetNames(dest)
      sheets_to_remove <- setdiff(all_sheets, keep_sheets)

      if (length(sheets_to_remove) > 0) {
        for(sheet_name in sheets_to_remove) {
          removeWorksheet(wb, sheet_name)
          message("Removed sheet: ", sheet_name)
        }
        saveWorkbook(wb, dest, overwrite = TRUE)
      }
    }, error = function(e) {
      message("Note: Could not process Excel sheets. File will be uploaded as-is.")
    })
  }

  # Upload new file
  osfr::osf_upload(data_folder, dest, conflicts = "overwrite")
  message("Uploaded new version to OSF: ", filename)

  invisible(NULL)
}

# ============================================================================
# Main Release Function
# ============================================================================

#' Release a dataset to OSF with versioning
#' @param dataset_path Local path to dataset file (e.g., "output/FReD.xlsx")
#' @param dataset_name Dataset name for logging (e.g., "FReD" or "flora")
#' @param osf_project OSF project ID (e.g., "9r62x")
#' @param osf_folder OSF folder path (e.g., "0 Data" or "0 Data/Flora")
#' @param version_type One of "major", "minor", "patch"
#' @param release_notes Character string with release notes
#' @param changelog_file OSF file ID for changelog
#' @return Invisible list with old_version and new_version
release_to_osf <- function(dataset_path,
                          dataset_name = "FReD",
                          osf_project = "9r62x",
                          osf_folder = "0 Data",
                          version_type = c("major", "minor", "patch"),
                          release_notes = "",
                          changelog_file = "https://osf.io/fj3xc") {

  version_type <- match.arg(version_type)
  osf_token <- Sys.getenv("OSF_TOKEN")

  if (osf_token == "" || is.null(osf_token)) {
    stop("OSF_TOKEN environment variable not set. Please authenticate with OSF.")
  }

  # Verify local file exists
  if (!file.exists(dataset_path)) {
    stop("Dataset file not found: ", dataset_path)
  }

  # Get filename from path
  filename <- basename(dataset_path)

  message("\n=== OSF Release: ", dataset_name, " ===")
  message("Dataset: ", filename)
  message("Version type: ", version_type)
  message("OSF folder: ", osf_folder)
  message("")

  # Authenticate to OSF
  osfr::osf_auth(osf_token)
  osf_project_node <- osfr::osf_retrieve_node(osf_project)

  # Navigate to target folder (supports nested paths like "0 Data/Flora")
  folder_segments <- strsplit(osf_folder, "/")[[1]]
  current_node <- osf_project_node

  for (seg in folder_segments) {
    children <- osfr::osf_ls_files(current_node, type = "folder")
    matched <- children %>% filter(name == seg)

    if (nrow(matched) == 0) {
      stop("OSF folder not found: ", seg)
    }

    current_node <- osfr::osf_retrieve_node(matched$id[1])
  }

  data_folder <- current_node

  # Prepare changelog
  message("Preparing changelog...")
  changelog_result <- prepare_changelog(
    release_notes = release_notes,
    version_type = version_type,
    changelog_file = changelog_file
  )

  old_version <- changelog_result$old_version
  new_version <- increment_version(old_version, version_type)

  message("Current version: ", old_version)
  message("New version: ", new_version)

  # Process and upload file
  message("Uploading file to OSF...")
  process_file(
    data_folder = data_folder,
    old_version = old_version,
    filename = filename,
    github_raw_url = NULL,
    gsheet_link = NULL
  )

  # Copy file from local path to temp and upload
  temp_dir <- tempdir()
  file.copy(dataset_path, file.path(temp_dir, filename), overwrite = TRUE)
  osfr::osf_upload(data_folder, file.path(temp_dir, filename), conflicts = "overwrite")

  # Upload changelog
  message("Uploading changelog...")
  upload_changelog(changelog_result$new_changelog, data_folder)

  message("\n✓ Successfully released ", dataset_name, " version ", new_version)
  message("Released on: ", format(Sys.Date(), "%Y-%m-%d"))

  invisible(list(old_version = old_version, new_version = new_version))
}
