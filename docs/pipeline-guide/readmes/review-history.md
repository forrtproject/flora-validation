# My Judgements and corrections after submission

My Judgements reads reviews already saved by the server. Opening a history card does not rewrite the submitted review or publish a correction.

[Open interactive guide](../index.html#view=overview&node=review-history)

Code snapshot: 25 November 2026, `f0cb8de`.

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

- [app.py:1453 — get_my_judgements](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L1453) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [app.py:1546 — get_my_judgement_detail](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L1546) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [app.py:6308 — admin_flag_review](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L6308) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
