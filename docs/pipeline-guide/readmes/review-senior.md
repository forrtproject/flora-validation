# Senior override: reject

A Senior validator can immediately mark a reserved record as neither a replication nor a reproduction. This is a special authoritative rejection, separate from the ordinary type answer.

[Open interactive guide](../index.html#view=overview&node=review-senior)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- A Senior validator (tier 2 or higher), an open owned slot, and optional notes.

## What happens

1. POST /api/senior-reject checks tier and ownership.
2. The server completes the senior's queue row with a senior_reject marker, sets the record to rejected, and removes any matching row from validated.
3. If the partner's human slot is still open, the server fills it with the senior rejection at zero additional points; an already-submitted partner review remains intact.
4. The consensus engine recognizes the marker and cannot overturn the rejection with a later ordinary review or Gemini result.

## What is saved, and where

- PostgreSQL validation_queue rejection evidence; unvalidated status and review summaries; validators points/judgement totals.
- The record is removed from the approved validation table, validated.

## When it updates

- The rejection is durable after the request commits; the review card then advances.
- Downstream prepared files require their own subsequent preparation/publication cycle.

## What comes out

- A rejected record with attributable override evidence.

## Keep in mind

- A reproduction is an eligible type; this override is for neither type.
- The displayed two matching slots may include the senior's stamped partner slot, not two independent human submissions.

## Find it in the code

- [app.py:3050 — senior_reject](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3050) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [docs/app.js:3801 — senior rejection button handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L3801) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [consensus_engine.py:688 — senior-reject consensus guard](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py#L688) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py)
