# Automatic checks after updates

Background entry checks inspect the stored dataset and keep findings for the FLoRA tab. They report possible problems; they do not correct records.

[Open interactive guide](../index.html#view=overview&node=process-entry-checks)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The stored flora_data CSV, current revision, and shared network-check cache.

## What happens

1. Every five minutes, ask whether structural or network checks are due.
2. Run structural checks when the stored dataset revision has changed.
3. Run network checks once per UTC day from 04:00, with a 45-minute budget.
4. Store findings by record and check key. Resolve findings when the relevant check no longer finds the issue; retain a person's dismissals.

## What is saved, and where

- flora_check_runs and flora_check_findings.
- Network successes/DOI lookups use link_checks and original_doi_lookups.

## When it updates

- A correction can wait for the next five-minute structural-check tick before its finding changes.
- Network checks are daily from 04:00 UTC; a time budget can defer targets.
- A failed structural run has a one-hour retry delay.

## What comes out

- Per-record findings and run summaries in the FLoRA admin interface.

## Keep in mind

- Checking does not modify FLoRA values.
- Not a problem records a human dismissal; it does not change the dataset value or delete the audit evidence.
- A cached successful link check is intentionally reused for roughly 25–35 days.

## Find it in the code

- [entry_checks.py:1 — module workflow](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/entry_checks.py#L1) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/entry_checks.py)
- [entry_checks.py:57 — PARTS and schedule constants](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/entry_checks.py#L57) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/entry_checks.py)
- [app.py:6727 — entry-check scheduler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6727) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [validate_flora_network.py:288 — cache_days](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/validate_flora_network.py#L288) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/validate_flora_network.py)
