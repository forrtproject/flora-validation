# Public FLoRA APIs

The existing website now exposes read-only `/v1` routes backed by the complete
prepared `flora_data` table. Four routes preserve the supplied `flora-backend`
response structure, and a separate route resolves permanent record-ID hashes.
The API reads the latest committed preparation snapshot; edits become visible
after the preparation pipeline stores its next complete dataset.

The supplied 2,914-row snapshot produces 4,817 DOI records. Two rows have no DOI
on either side and are accessible through their permanent ID hashes. API
aggregation leaves the existing CSV and its record ordering unchanged.

## Endpoints

All routes support `OPTIONS`. Lookup and search requests accept either query
parameters or a JSON object; JSON body values take precedence when provided.

| Route | Methods | Input | Response |
| --- | --- | --- | --- |
| `/v1/prefix-lookup` | GET, POST | `prefixes` array; GET also accepts `prefix` | `{"results": {"dec": [DOI records]}}` |
| `/v1/original-lookup` | GET, POST | `dois` array | `{"results": {"normalized DOI": DOI record or null}}` |
| `/v1/dois` | GET | None | `{"total": 4817, "dois": [DOI strings]}` for the supplied snapshot |
| `/v1/search` | GET, POST | Search parameters below | Query, pagination metadata, and DOI-keyed `results` |
| `/v1/id-lookup` | GET, POST | `hashes` array; `id_md5` is an alias | `{"results": {"full ID MD5": published row or null}}` |

GET lookup lists accept repeated parameters or comma-separated values. Use a
JSON `dois` array when the DOI itself contains a comma. Lookup requests accept
at most 200 values, with at most 2,048 characters per value. A prefix request
matching more than 5,000 distinct DOI records is rejected; request fewer prefixes.

Despite its legacy name, `/v1/original-lookup` accepts a DOI in any role:
original, replication, or reproduction. DOI prefixes and case are normalized.
Unknown DOIs and full ID hashes return `null` under their requested normalized
key. An unknown three-character DOI prefix returns an empty array.

## Two different hash keys

| Key | Example | Identifies |
| --- | --- | --- |
| DOI hash prefix | `dec` | A bucket of DOI records, from `doi_o_hash` / `doi_r_hash` |
| Full MD5 of permanent row ID | `2de91c651bc82ac3d5e43031660a22b6` | Exactly one published relationship row: `FLORA-000001` |

`/v1/prefix-lookup` requires exactly three hexadecimal characters. Existing
DOI-prefix associations are retained; a missing short hash is derived from the
normalized DOI. `/v1/id-lookup` requires the complete 32-character MD5 of `id`
and accepts uppercase hexadecimal input. These are separate lookup contracts.

A DOI may participate in many published relationships, so DOI aggregates do
not receive an invented top-level row ID. Instead, every entry in
`record.originals`, `record.replications`, and `record.reproductions` carries
its own permanent `id` and `id_md5`. Distinct records sharing the same DOI pair
remain separate entries. Metadata uses the first nonempty value in publication
order; record counts and unique-DOI counts remain separate.

## Request examples

After starting the existing application locally, these PowerShell commands call
the implemented routes:

```powershell
$floraApiBase = 'http://127.0.0.1:8000'

Invoke-RestMethod "$floraApiBase/v1/prefix-lookup?prefixes=dec"

Invoke-RestMethod -Method Post "$floraApiBase/v1/original-lookup" -ContentType 'application/json' -Body '{"dois":["10.1016/0010-0285(72)90003-5"]}'

Invoke-RestMethod "$floraApiBase/v1/dois"

Invoke-RestMethod "$floraApiBase/v1/search?q=sentence%20memory&limit=10&offset=0"

Invoke-RestMethod -Method Post "$floraApiBase/v1/id-lookup" -ContentType 'application/json' -Body '{"hashes":["2de91c651bc82ac3d5e43031660a22b6"]}'

Invoke-RestMethod "$floraApiBase/v1/id-lookup?id_md5=2de91c651bc82ac3d5e43031660a22b6"
```

An advanced search request:

```powershell
Invoke-RestMethod -Method Post "$floraApiBase/v1/search" -ContentType 'application/json' -Body '{"mustHave":["memory"],"anyOf":["sentence","recall"],"exclude":["visual"],"yearFrom":1970,"yearTo":2026,"paperTypes":["original"],"limit":20,"offset":0}'
```

The local [`output/flora_api_examples.json`](../output/flora_api_examples.json)
artifact contains captured request/response examples from an isolated API check.

## DOI response fields

Each DOI record has `doi`, `types`, `doi_hash`, `title`, `authors`, `journal`,
`year`, `volume`, `issue`, `pages`, `apa_ref`, `bibtex_ref`, `url`, and `record`.
JSON author/BibTeX cells are decoded when they contain valid JSON; ordinary
BibTeX citation text stays text. `types` uses the stable order `original`,
`replication`, `reproduction`, including every role the paper has.

`record` contains `originals`, `replications`, `reproductions`, and `stats`:

```json
{
  "n_replications_total": 2,
  "n_replications_with_doi": 2,
  "n_replications_only": 0,
  "n_unique_replication_dois": 1,
  "n_reproductions_total": 0,
  "n_reproductions_with_doi": 0,
  "n_reproductions_only": 0,
  "n_originals_total": 0,
  "n_unique_original_dois": 0
}
```

This illustrative example counts two distinct published replication rows with
the same replication DOI. Linked entries expose their citation fields and
permanent row IDs. Replication/reproduction entries also include type, outcome,
quote/source, and the available allowlisted evidence fields.

Anonymous DOI responses include these derived fields once, at the top level:

- `outcome_mix`: counts of nonblank replication outcomes.
- `replication_year_counts`: counts of replications by year.
- `first_replication_year` and `first_replication_outcome`: earliest replication
  year and its outcome, or null when no replication year is available.
- `citation_timeline` and `n_citations`: present only when explicit
  paper-specific citation data was stored. Replication counts are never
  substituted for citation counts.

Passing a nonblank `apiEmail` in the JSON body or query string suppresses the
derived and citation fields for legacy clients. It is a response-size preference,
not authentication. It does not alter IDs, relationships, stats, or the database.

## Permanent ID-hash response

`/v1/id-lookup` returns the stored relationship row rather than a DOI aggregate:
the 37 published fields (`id`, `id_md5`, and the original 35 columns), available
standard output/provenance extras, and `_meta`. Arbitrary additional database
fields are withheld by the public projection. This response keeps the CSV field
names, such as `title_o`, `title_r`, `author_o`, and `author_r`.

`_meta` includes `export_position`, `record_version`, `created_at`, `updated_at`,
`retired_at`, and `active`. A retired ID still resolves with `active: false` and
its retirement timestamp. Ordinary DOI routes/search use active rows only.
This lets an old published ID continue to resolve after exclusion or retirement.

## Search parameters and matching

| Parameter | Behavior |
| --- | --- |
| `query`, or `q` | Title/author terms, or an exact DOI; at most 2,048 characters |
| `mustHave` | All supplied terms must match |
| `anyOf` | At least one supplied term must match |
| `exclude` | Omit records matching any supplied term |
| `yearFrom`, `yearTo` | Inclusive positive year bounds; start must not exceed end |
| `paperTypes` | `original`, `replication`, `reproduction`; multiple values allowed |
| `outcomes` | Keep direct matches whose nonblank replication outcomes all belong to this set |
| `limit` | Integer from 1 to 1,000; default 1,000 |
| `offset` | Nonnegative integer; default 0 |

Term/filter lists accept JSON strings or arrays, or repeated/comma-separated GET
parameters, with at most 50 values per list. At least one of `query`, `mustHave`,
`anyOf`, or `exclude` is required. Supplying advanced term fields makes those
fields the search terms instead of the free-text query. Without advanced terms,
a year from 1800 through 2099 in free text becomes an exact publication-year
filter. Range filters retain papers whose year is unknown; an exact-year filter
does not.

Search includes the paper title, its authors, and authors of linked papers.
Terms support `*` and `?` wildcards. Ranking uses deterministic Python word
similarity with a 0.85 minimum for fuzzy matches; lower scores rank first, with
DOI ordering breaking ties. This preserves the response structure and supported
controls, but scores and ordering are not exact Fuse.js equivalents.

`paperTypes` matches paper roles or the corresponding relationship availability
and can add papers one relationship hop away. Added papers still obey year,
exclusion, role, and applicable outcome filters. Direct outcome-filter matches
need at least one nonblank replication outcome; added linked papers without
their own replication outcomes can remain. All filtering and expansion happen
before pagination. `total` and `hasMore` describe the resulting filtered set.

The response is shaped as:

```json
{
  "query": "sentence memory",
  "total": 0,
  "offset": 0,
  "limit": 10,
  "hasMore": false,
  "results": {}
}
```

When matches exist, `results` maps each DOI to its DOI record with an additional
numeric `score`. The zero-result example above illustrates the envelope only.

## Snapshot freshness and access

Requests use a PostgreSQL read-only, repeatable-read transaction. DOI aggregates
are cached in the application process, with the database snapshot hash and
revision timestamp checked on each request. A newly committed preparation
invalidates that projection on its next use. No request fetches metadata from
external services or rewrites the prepared dataset.

Successful search responses advertise `Cache-Control: public, max-age=300`;
the other successful routes use `max-age=3600`. Browser/intermediary caches may
therefore retain a previous response until that HTTP cache lifetime expires.

The lookup/DOI-list routes allow browser origins with `Access-Control-Allow-Origin: *`.
Search allows `https://forrt.org`; comma-separated `FLORA_SEARCH_ORIGINS` entries
add browser origins for other clients. Search responses include `Vary: Origin`.
These routes are public and do not require an administrator session.

## Errors

Errors use `{"error": "message"}` with `Cache-Control: no-store`:

| Status | Meaning |
| --- | --- |
| 400 | Invalid JSON/object shape, missing lookup values/search terms, malformed hashes, invalid bounds, or excessive list/match counts |
| 413 | POST body exceeds 64 KiB |
| 503 | The schema exists but no prepared dataset has been committed yet |
| 500 | Internal failure; response is the generic `Internal Server Error` without database details |

## Initialize and run

Use the existing project environment and application configuration. Apply the
schema and import the supplied complete snapshot:

```powershell
.\.venv\Scripts\python.exe flora_store.py --input output/flora.csv --init-schema
```

Start the existing website application normally:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

The application's existing startup behavior and required settings are described
in the [main guide](README.md#quick-start). Future successful database preparation
runs update `flora_data` automatically; see the [table guide](FLORA_DATA_TABLE.md)
for complete-snapshot imports, permanent identity, and recovery.

This implementation uses the current PostgreSQL application. It does not modify
AWS/DynamoDB resources or deploy the website to production.

## Verification report

Verified locally on 16 September 2026:

- **1,251 Python tests passed**, including 79 focused public API, aggregation,
  and search checks. The existing browser pipeline checks also passed.
- A complete import into an isolated PostgreSQL database exposed **4,817 DOI
  records**. HTTP batch lookup successfully resolved **all 2,914 permanent ID
  hashes**, including the two rows with no DOI on either side.
- API reads left the CSV bytes and stored record versions unchanged. The CSV
  SHA-256 remains
  `d7a54f14acf016260199e3b5bcb0104e175cb0d0c5ee7e5e53b5477d84ebb6e4`.
- Tests covered lookup normalization, derived-field suppression, duplicate-pair
  IDs, filters and pagination, snapshot cache invalidation, retired ID access,
  public field selection, read-only transactions, input errors, internal-error
  redaction, CORS, and administrator write protection.
- The [captured examples](../output/flora_api_examples.json) contain actual
  successful HTTP responses for DOI lookup, DOI-prefix lookup, title search,
  DOI search, and permanent ID-hash lookup.

These checks did not contact AWS or the production database. Deployment and the
initial production snapshot import remain pending. The broader pipeline and
helper integration details are in the [full report](PIPELINE_INTEGRATION_REPORT.md).
