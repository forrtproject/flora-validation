# Skip or report an inaccessible article

Skip releases a reserved slot without submitting a scientific judgement. Its reason is kept separately so administrators can see recurring problems.

[Open interactive guide](../index.html#view=overview&node=review-skip)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- An open reserved study, a skip reason, and a comment when that reason requires one.

## What happens

1. POST /api/skip checks that the current validator still owns an open slot.
2. It releases the claim and adds a validation_skips audit event in one transaction.
3. An inaccessible reason marks the record restricted_access so it leaves ordinary circulation.
4. The record status is restored according to remaining work, the validator's skipped_count increases, and the browser loads another study.
5. The hard-mode cannot-access action has its own /api/restricted endpoint and routes the record to the review team without a judgement or points.

## What is saved, and where

- PostgreSQL validation_skips: validator, record, queue, reason and comment.
- PostgreSQL validation_queue: cleared claim; unvalidated: status and any restricted-access flag; validators: skipped_count for Skip.

## When it updates

- The slot is available again after a successful skip transaction unless the record has been restricted.
- Skipped/restricted admin views show the change on their next fetch.

## What comes out

- An auditable skip or access report, not a completed human review.

## Keep in mind

- Skip is different from marking the study scientifically ineligible.
- The Skipped escalation summary counts distinct validators, so repeated requests cannot inflate it: more than 5 validators for any reason or at least 2 eligibility/data-quality skips can surface an issue.

## Find it in the code

- [app.py:3114 — skip_pair](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L3114) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
- [app.py:2253 — _insert_skip_event](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L2253) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
- [app.py:2271 — _skip_summary](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L2271) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
- [app.py:2575 — report_restricted](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L2575) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
- [db_schema.sql:140 — validation_skips](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/db_schema.sql#L140) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/db_schema.sql)
