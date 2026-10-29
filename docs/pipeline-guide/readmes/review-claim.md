# Get a study to review

The validator opens a study and the server reserves one of its two human review slots. Opening a study changes the queue, but does not save a judgement.

[Open interactive guide](../index.html#view=overview&node=review-claim)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- An authenticated validator and a choice of normal or hard mode.
- Eligible records in unvalidated, two human slots in validation_queue, and the administrator's serving_config priorities.

## What happens

1. POST /api/next-pairs resumes an active record in the chosen mode or claims a free slot. It can also reserve a small buffer of upcoming studies.
2. The claim records the validator, shown_at and, for an active study, started_at. An unvalidated record becomes validation_inprogress.
3. POST /api/pairs/{queue_id}/start promotes a buffered study when the validator opens it.

## What is saved, and where

- PostgreSQL: validation_queue claim fields and unvalidated.validation_status.

## When it updates

- The returned study appears immediately after the API response.
- The stale-slot job runs every 2 minutes. Unstarted buffered claims can be released after 45 minutes; started claims can be released after 5 days.

## What comes out

- A reserved study card with a queue_id and record_id.

## Keep in mind

- A reservation is not a submitted review. The displayed next study may have been prefetched earlier.
- Normal and hard mode have separate active-study resumption; switching modes does not submit a parked study.

## Find it in the code

- [app.py:1787 — _claim_one_pair](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L1787) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:1855 — next_pairs](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L1855) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:1933 — start_pair](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L1933) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:6609 — _reap_stale_slots](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6609) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
