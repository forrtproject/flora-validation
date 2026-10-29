# Submit a judgement

Submit sends one person's decisions to PostgreSQL. In the normal buffered flow, the interface can show the next study while the save is still pending.

[Open interactive guide](../index.html#view=overview&node=review-submit)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The current draft, the reserved record_id, and the validator's server-issued session.

## What happens

1. submitJudgement creates a payload containing checks, corrections, evidence and notes.
2. The buffered flow stores the payload in flora.pendingSubmits, clears the separate draft, and shows the next study. A background queue posts the payload to /api/judge.
3. The server locks the record and its open human slot, checks the values, completes the queue row and copies a review summary to unvalidated.validator_1 or validator_2.
4. The server updates validator points and runs the consensus decision tree in the same transaction.
5. Only after the successful response does the browser remove the pending payload and refresh the points and leaderboard.

## What is saved, and where

- Before confirmation: browser localStorage flora.pendingSubmits, owned by the validator who wrote it.
- After the transaction commits: PostgreSQL validation_queue, unvalidated review summary and workflow state, and validators totals; consensus may also write validated.

## When it updates

- The next card can appear before the database save finishes.
- Points, stats and the leaderboard refresh after the server confirms success.
- Retryable network/server failures use backoff. Parked failures remain available in the pending-submissions recovery interface.

## What comes out

- One durable human judgement, or an explicitly pending local payload if saving failed.

## Keep in mind

- Seeing the next card is not proof that the previous review was saved. Check the pending indicator.
- The queue belongs to its author and is not sent under another validator's login.
- A failed submission is not automatically released just because the network failed; release requires a server-issued failure capability or confirmation that the slot is already closed.

## Find it in the code

- [docs/app.js:4677 — submitJudgement](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L4677) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:2791 — _enqueueSubmit](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L2791) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:2807 — _processSubmitQueue](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L2807) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:2625 — judge](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L2625) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:2889 — release_failed_submission](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L2889) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
