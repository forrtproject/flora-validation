# Draft answers: saved in this browser

Choosing answers and editing text prepares a draft. The draft is copied to this browser's local storage every 10 seconds; it has not yet reached the shared database.

[Open interactive guide](../index.html#view=overview&node=review-draft)

Code snapshot: 24 November 2026, `3cc13da`.

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

- [docs/app.js:2384 — _DRAFT_KEY](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L2384) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [docs/app.js:2396 — _prepareDraft](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L2396) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [docs/app.js:2420 — _startDraftSave](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L2420) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [docs/app.js:2720 — draft restoration](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L2720) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
