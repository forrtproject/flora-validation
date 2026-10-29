# Publish the repository copy

The Publish FLoRA GitHub Action writes the currently stored dataset to output/flora.csv and commits the release files so other repositories can read them.

[Open interactive guide](../index.html#view=overview&node=output-publication)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The current active flora_data snapshot, including eligible corrections saved since the last build.
- Optionally, a successful website pipeline job ID whose report should accompany the same bytes.

## What happens

1. Read the stored dataset and revision together in a read-only repeatable-read transaction.
2. Write flora.csv, a matching manifest and release notes, and daily count history.
3. Include a website run's reports only if its CSV fingerprint matches these current bytes; remove stale build-specific reports otherwise.
4. Commit and push the configured release files when flora.csv changed.

## What is saved, and where

- GitHub repository: output/flora.csv and the files listed in .github/release-files.txt.
- The script itself writes files; the GitHub workflow performs the commit/push.

## When it updates

- Automatically after Sync entry sheets completes, whether that sync succeeded or failed.
- Manually through Actions → Publish FLoRA, for example after a website Run pipeline or a saved correction.
- The successful nightly Sync entry sheets workflow also commits its freshly built release.

## What comes out

- A versioned repository release copy for downstream readers.

## Keep in mind

- A website button run and Save do not themselves trigger a GitHub commit.
- After a failed nightly build, this workflow can still publish the last stored dataset with saved corrections; it does not retry source preparation.
- No OSF upload is performed by these workflows.

## Find it in the code

- [publish_flora.py:73 — publish](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/publish_flora.py#L73) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/publish_flora.py)
- [publish_flora.py:123 — main](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/publish_flora.py#L123) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/publish_flora.py)
- [.github/workflows/publish-flora.yml:13 — workflow_dispatch and workflow_run](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/workflows/publish-flora.yml#L13) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/workflows/publish-flora.yml)
- [.github/workflows/sync-sources.yml:143 — repository release commit](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/workflows/sync-sources.yml#L143) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/workflows/sync-sources.yml)
- [.github/scripts/commit_and_push.sh:1 — commit/push helper](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/scripts/commit_and_push.sh#L1) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/scripts/commit_and_push.sh)
