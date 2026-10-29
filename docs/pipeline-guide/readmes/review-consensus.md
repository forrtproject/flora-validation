# Two reviews, Gemini, and the approval decision

After two human reviews are complete, the consensus engine compares their answers. It can request administrator review, prepare a result for approval, reject a non-eligible record, or automatically approve a qualifying agreement.

[Open interactive guide](../index.html#view=overview&node=review-consensus)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- Completed human_1 and human_2 rows from validation_queue, the original extraction, and validator experience/tier.
- Gemini's structured sanity-check or tiebreak response when that branch calls the model.

## What happens

1. With fewer than two completed human reviews, the engine returns without a final decision.
2. A senior rejection is authoritative. Explicit uncertainty or an evidence-quote flag sends the record to need_review.
3. Matching checks and corrections invoke Gemini as a sanity check. A normal accepted agreement becomes consensus_reached and waits for an administrator.
4. Automatic approval is allowed only for a plain agreement with matching Gemini, no disqualifying uncertainty/evidence/original-identity issue, and either at least one Trusted/Senior reviewer or two experienced reviewers. Experienced means at least 20 admin-approved records and at most 2 flagged judgements.
5. If checks match but corrections differ, the result is need_review without a Gemini call. If checks differ, Gemini is a tiebreaker: an eligible one-sided match can yield consensus_reached; otherwise the record needs review.
6. When both reviewers say neither replication nor reproduction, matching Gemini can reject it. A disputed rejection goes to an administrator.

## What is saved, and where

- PostgreSQL unvalidated: validation_status, selected final_* values, llm_validator response, and supporting quality information.
- Automatic acceptance also inserts/updates the approved record in validated. Human submissions remain in validation_queue.

## When it updates

- Consensus runs during the request that completes the second review, before that request's database transaction commits.
- The admin queue sees the new status on its next fetch.
- This step does not itself rebuild the combined flora.csv dataset or publish a GitHub release.

## What comes out

- consensus_reached = prepared for admin approval; need_review = admin decision needed; validated = approved validation record; rejected = excluded from the validation export.

## Keep in mind

- Two matching reviewers do not always mean immediate approval.
- Approval of a validation record and publication of the final combined FLoRA dataset are separate steps.
- Gemini output is stored evidence for the decision tree, not an unrestricted database editor.

## Find it in the code

- [consensus_engine.py:625 — evaluate_consensus](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py#L625) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py)
- [consensus_engine.py:111 — auto_validation_rule](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py#L111) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py)
- [consensus_engine.py:458 — _update_status](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py#L458) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py)
- [consensus_engine.py:529 — _insert_validated](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py#L529) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/consensus_engine.py)
- [llm_validator.py:1 — Gemini validation implementation](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/llm_validator.py#L1) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/llm_validator.py)
