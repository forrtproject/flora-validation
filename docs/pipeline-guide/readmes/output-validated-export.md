# Separate validated-only export

A different daily workflow exports only the application's approved validation records. It is not the combined FLoRA release containing entry sheets.

[Open interactive guide](../index.html#view=overview&node=output-validated-export)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- validated rows and bibliographic reference lookups/cache.

## What happens

1. Read approved records and prepare citation/reference fields.
2. Write the validated-only CSV and a list of references needing manual attention.
3. Commit changed export files and the reference cache back to the repository.

## What is saved, and where

- data/validated_export.csv, data/needs_manual_refs.csv and oa_ref_cache.json.

## When it updates

- GitHub Actions Daily validated export is scheduled for 04:00 UTC daily and supports manual dispatch.

## What comes out

- Approved app records as a separate CSV and a manual-reference follow-up file.

## Keep in mind

- This does not build the combined output/flora.csv or store flora_data.
- GitHub schedules are configured start times, not a guarantee of exact completion or success.

## Find it in the code

- [export_validated.py:1 — validated export script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/export_validated.py#L1) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/export_validated.py)
- [export_validated.py:222 — validated CSV write](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/export_validated.py#L222) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/export_validated.py)
- [.github/workflows/daily-export.yml:3 — schedule and export steps](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/workflows/daily-export.yml#L3) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/.github/workflows/daily-export.yml)
