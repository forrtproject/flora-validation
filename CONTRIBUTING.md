# Contributing to FLoRA Validation

We welcome documentation improvements, bug reports, focused fixes, and improvements to the human-validation workflow. Please follow the [FORRT Code of Conduct](https://forrt.org/coc/). Human validators can contribute through the [application](https://validation.forrt.org) without setting up a development environment.

## Discuss changes and report useful issues

Check [issues](https://github.com/forrtproject/flora-validation/issues) and open pull requests first. Discuss changes to judgement rules, outcome vocabularies, identity/deduplication, authentication, ingestion, or publication with maintainers before implementing them: these affect research data or reviewers' work.

For bugs, include the affected screen or script, steps to reproduce, expected and actual behaviour, and a public DOI or record ID where relevant. For a data-quality suggestion, cite the supporting public evidence. Remove names, email addresses, session cookies, database URLs, API keys, and private review comments from logs or screenshots.

## Set up a development copy

Fork the repository and replace `YOUR-USERNAME` with your GitHub username:

```bash
git clone https://github.com/YOUR-USERNAME/flora-validation.git
cd flora-validation
git remote add upstream https://github.com/forrtproject/flora-validation.git
git switch -c fix/describe-your-change
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Read the [root README](README.md) and [implementation guide](docs/README.md) for the repository boundaries, configuration, and architecture. On Windows, use the PowerShell setup in that guide.

The ordinary test suite needs no `.env`: `tests/conftest.py` supplies a placeholder database URL, and service calls are mocked. To run the application, follow the guide using a disposable PostgreSQL database and development-only settings. Importing `app.py` applies schema SQL, may bootstrap records and an administrator, and starts scheduled jobs. Do not point a development checkout at the production database or run sync, export, notification, or LLM jobs against live services as a routine contribution check.

## Verify changes

Run focused tests while developing, then the ordinary suite and JavaScript syntax check before opening a code PR:

```bash
python -m pytest -q
node --check docs/app.js
```

Add regression coverage for changed behaviour using synthetic or public, minimal fixtures and mocked external services. Existing tests cover ingestion contracts, consensus, identity, source preparation, and frontend behaviour. See [Testing and verification](docs/README.md#testing-and-verification) for examples and limits.

Database changes also need PostgreSQL verification in an isolated environment; SQLite does not exercise the constraints, triggers, or locking used here. `FLORA_TEST_DATABASE_URL` opts into tests that create throwaway databases and must point only to a local test server. Explain migration, concurrency, and rollback implications in the PR. For interface changes, test the affected validator/admin journey with disposable records and include screenshots with personal details removed.

Preserve provenance, quote/source pairing, permanent record identities, and distinctions between uncertain, missing, and negative evidence. Keep extraction research changes in `flora-extractor`; this repository handles ingestion, human validation, and prepared-data publication.

## Open a pull request

Target `main` with a focused change. Link the issue, explain the resulting behaviour, list tests and manual checks, and state any checks you could not run. Update the relevant implementation/API documentation when behaviour changes. Documentation-only changes can be checked for accurate commands and working local links without running the application.

Maintainers review changes before merging. Deployment, production migration, scheduled ingestion, and dataset publication remain maintainer operations; contributors do not need production credentials.
