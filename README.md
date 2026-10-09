# FLoRA Validation

FLoRA Validation collects human judgements on extracted replication and reproduction studies, prepares the combined FLoRA dataset, and provides its public lookup API. This repository is the current source for FLoRA ingestion and validation logic.

The [FLoRA database](https://forrt.org/replication-hub/flora/) is part of [FORRT](https://forrt.org/). The application accepts records from [flora-extractor](https://github.com/forrtproject/flora-extractor), collects human reviews and administrator decisions, and combines approved records with the published FLoRA entry sheets. It prepares `output/flora.csv`, maintains permanent dataset identities in PostgreSQL, and serves DOI, title, and ID-hash lookups.

## Find the right place

- **Review studies:** use the [validation application](https://validation.forrt.org). You do not need to install this repository to contribute human validation.
- **Report a problem or suggest a change:** open a [GitHub issue](https://github.com/forrtproject/flora-validation/issues). Give a public record identifier, expected behaviour, and steps to reproduce; keep validator identities and private administrative information out of reports.
- **Contribute code or documentation:** read [CONTRIBUTING.md](CONTRIBUTING.md) and the [FORRT Code of Conduct](https://forrt.org/coc/).
- **Develop or operate the application:** start with the [implementation guide](docs/README.md), which covers setup, configuration, startup, workflows, and testing.
- **Understand preparation and publication:** read the [pipeline integration report](docs/PIPELINE_INTEGRATION_REPORT.md) and [prepared FLoRA database table guide](docs/FLORA_DATA_TABLE.md).
- **Use the public API:** read the [API guide](docs/FLORA_API.md).

## Repository boundaries

`flora-extractor` discovers candidate papers, screens them, identifies their original studies, and extracts outcomes. This repository imports that output and handles human validation, source-sheet ingestion, deduplication, preparation, and publication. The historical FLoRA pipeline in [fred-data](https://github.com/forrtproject/fred-data) overlaps with this work; make new FLoRA ingestion and validation changes here. Effect-level FReD work remains distinct.

The main application is FastAPI/Python with PostgreSQL. Its frontend is plain HTML, JavaScript, and CSS in `docs/`, with no frontend build step. The `reference_pipeline/` directory retains an R/Quarto reference implementation; see its [README](reference_pipeline/README.md) before using it.

## First development checks

Python 3.12 is the declared runtime. A fresh development environment can run the mocked tests without production credentials:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
node --check docs/app.js
```

**Starting or importing `app.py` performs database initialisation and can start scheduled jobs.** Use a disposable local database for application development. The [implementation guide](docs/README.md#quick-start) explains configuration and first-start behaviour. Keep production database URLs, API keys, personal information, and export/release credentials out of your development checkout and pull requests.
