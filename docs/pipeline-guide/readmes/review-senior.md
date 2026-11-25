# Senior override: reject

A Senior validator can immediately reject a reserved record as not meeting FLoRA inclusion criteria, even if it calls itself a replication. This is a special authoritative rejection, separate from the ordinary type answer.

[Open interactive guide](../index.html#view=overview&node=review-senior)

Code snapshot: 25 November 2026, `f0cb8de`.

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

- A reproduction is an eligible type; this override is for a record that does not meet FLoRA inclusion criteria.
- The displayed two matching slots may include the senior's stamped partner slot, not two independent human submissions.

## Find it in the code

- [app.py:3340 — senior_reject](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L3340) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [docs/app.js:4037 — senior rejection button handler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L4037) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [consensus_engine.py:774 — senior-reject consensus guard](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/consensus_engine.py#L774) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/consensus_engine.py)
