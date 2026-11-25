# Observatory disagreements: separate review lane

The optional Disagreements feature collects two Trusted/Senior judgements on imported Observatory disagreements, then asks an administrator to approve an answer and separately add it to Source Records.

[Open interactive guide](../index.html#view=overview&node=review-adjudication)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- Imported records in the separate adjudication schema and a validator with tier 1 or higher.
- The feature must be enabled and successfully initialized.

## What happens

1. A validator claims one disagreement, chooses the original study/outcome, and submits. Claims have a 60-minute availability window.
2. Each submitted answer is stored in adjudication.judgements and adds to the validator's regular points total.
3. After two submitted answers, adjudication.records becomes awaiting_approval. This lane does not call the ordinary consensus/Gemini engine.
4. Admin Approve stores the selected answer in adjudication.final and marks it approved. An admin who judged that record cannot approve it.
5. Admin Publish then inserts or refreshes a source_records row and marks the adjudication record published. Subsequent FLoRA preparation is still needed for prepared data/output.
6. Withdraw soft-deletes that source row and returns the disagreement to approved; reviewed source rows are protected from overwrite or withdrawal here.

## What is saved, and where

- PostgreSQL adjudication.records, adjudication.judgements and adjudication.final.
- Publish adds a source_records row; Withdraw marks that row deleted rather than physically removing it.
- Regular validators totals include disagreement points.

## When it updates

- Submit waits for server confirmation before moving on.
- After two answers, the admin list can show awaiting approval on its next refresh.
- Approval and Publish are two separate clicks and two separate persistence steps.

## What comes out

- Reviewed disagreement evidence, an approved final answer, and optionally a new Source Records input to the combined pipeline.

## Keep in mind

- Published in this lane means copied to Source Records; it does not mean a GitHub dataset release has been created.
- Skip records that this validator passed on the item and prevents it being served to that validator again.
- The feature is optional, so the tab may be absent or unavailable when ADJUDICATION_ENABLED is off.

## Find it in the code

- [adjudication/judging.py:38 — JUDGES_PER_RECORD, CLAIM_MINUTES, MIN_TIER](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/judging.py#L38) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/judging.py)
- [adjudication/judging.py:390 — next_record](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/judging.py#L390) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/judging.py)
- [adjudication/judging.py:478 — submit](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/judging.py#L478) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/judging.py)
- [adjudication/review.py:339 — approve](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py#L339) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py)
- [adjudication/review.py:520 — publish](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py#L520) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py)
- [adjudication/review.py:567 — withdraw](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py#L567) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py)
- [adjudication/api.py:72 — create_router](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/api.py#L72) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/api.py)
