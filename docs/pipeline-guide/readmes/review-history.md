# My Judgements and corrections after submission

My Judgements reads reviews already saved by the server. Opening a history card does not rewrite the submitted review or publish a correction.

[Open interactive guide](../index.html#view=overview&node=review-history)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The current validator's completed queue rows, linked extraction/final values, and any reviewer feedback.

## What happens

1. GET /api/my-judgements lists the signed-in validator's saved work.
2. GET /api/my-judgements/{queue_id} returns the detailed review, including corrected and final outcomes/evidence.
3. Administrator review actions handle decisions that need further scrutiny. Flag for review can return pending approval or an automatically approved record to need_review.
4. Sending an automatically approved record back removes it from validated; the source-retirement mechanism prevents it continuing to feed the combined dataset.

## What is saved, and where

- Opening My Judgements: no new scientific data is written.
- An admin Flag for review writes unvalidated status/notes and admin_decisions; for an auto-approved record it also removes the validated row.

## When it updates

- Saved history appears after the server confirms submission and the history list is fetched again.
- Admin decisions become visible on the next detail/list fetch.

## What comes out

- An audit view of what the person submitted, current final values, and any feedback.

## Keep in mind

- There is no ordinary validator redo/edit-submitted endpoint in the current API. Change an unsent draft before Submit; a saved review needs the review-team workflow.
- Flag for review is an administrator action on the record, not a reset that erases the original human submissions.

## Find it in the code

- [app.py:1381 — get_my_judgements](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L1381) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:1474 — get_my_judgement_detail](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L1474) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:5518 — admin_flag_review](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5518) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
