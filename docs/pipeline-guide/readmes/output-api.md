# Public FLoRA API

Public clients search and look up the stored FLoRA dataset. Requests read PostgreSQL through a read-only transaction and never start an import or preparation run.

[Open interactive guide](../index.html#view=overview&node=output-api)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- Requests to /v1/prefix-lookup, /v1/original-lookup, /v1/dois, /v1/search and /v1/id-lookup.
- The active stored flora_data dataset.

## What happens

1. Validate query values and limits.
2. Read the shared dataset revision; reuse an in-process projection only when its database/revision key still matches.
3. Return DOI-level records, title/search matches or permanent ID-hash records as requested.

## What is saved, and where

- No dataset writes. A process-local projection cache improves read performance.

## When it updates

- A request reaching the server checks the database revision each time.
- HTTP responses allow a 300-second cache for search and 3600 seconds for other successful endpoints; a browser/proxy may show older data until its cache expires.

## What comes out

- JSON lookup/search results for public consumers.

## Keep in mind

- The API reads the stored snapshot, not the raw source_records table or the local output/flora.csv file.
- If no dataset has been stored yet, the API reports dataset not ready (503).
- id-lookup expects the entire 32-character ID MD5; DOI-prefix lookup uses three hexadecimal characters of the DOI hash.

## Find it in the code

- [flora_public_api.py:40 — readonly_database](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py#L40) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py)
- [flora_public_api.py:66 — DatasetCache](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py#L66) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py)
- [flora_public_api.py:217 — create_router](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py#L217) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py)
- [flora_public_api.py:290 — HTTP cache lifetime](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py#L290) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_public_api.py)
