# Draft answers: saved in this browser

Choosing answers and editing text prepares a draft. The draft is copied to this browser's local storage every 10 seconds; it has not yet reached the shared database.

[Open interactive guide](../index.html#view=overview&node=review-draft)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- Type, original-study and outcome choices, corrections, evidence, and comments entered in the current review card.

## What happens

1. The interface updates state.judgement as the validator answers the questions.
2. _startDraftSave writes the current judgement under flora.draft.{pair_id} every 10 seconds.
3. When the same active pair is resumed, the interface restores draft text and replays the answer gates.
4. If the stored review baseline differs from the newly imported type or outcome, the interface asks for those answers to be checked again.

## What is saved, and where

- This browser only: localStorage key flora.draft.{pair_id}.
- No validation_queue judgement is saved by autosave.

## When it updates

- Edits change the open card immediately; the browser backup runs every 10 seconds.
- Draft restoration happens when a reserved record is resumed.

## What comes out

- A recoverable local draft, ready to submit.

## Keep in mind

- Drafts do not follow the user to another browser or computer.
- Clearing site storage removes the local draft. Changes made immediately before closing can precede the next 10-second save.

## Find it in the code

- [docs/app.js:2371 — _DRAFT_KEY](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L2371) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:2383 — _prepareDraft](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L2383) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:2407 — _startDraftSave](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L2407) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:2693 — draft restoration](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L2693) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
