# Draft answers: saved in this browser

Choosing answers and editing text prepares a draft. The draft is copied to this browser's local storage every 10 seconds; it has not yet reached the shared database.

[Open interactive guide](../index.html#view=overview&node=review-draft)

Code snapshot: 25 November 2026, `f0cb8de`.

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

- [docs/app.js:2387 — _DRAFT_KEY](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L2387) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [docs/app.js:2399 — _prepareDraft](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L2399) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [docs/app.js:2423 — _startDraftSave](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L2423) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [docs/app.js:2736 — draft restoration](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L2736) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
