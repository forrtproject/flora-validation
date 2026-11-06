"""The rules of the paper review that need no database."""
from types import SimpleNamespace

import pytest

import paper_review
from consensus_engine import marked_wrong_without_replacement as no_original, removal_proposed

WRONG = {"type_check": "correct", "original_check": "incorrect", "outcome_check": "not_applicable"}
PAIR = {"type": "replication", "doi_r": "10.1/x", "outcome": "failed", "abstract_r": "Text."}


# Real pairs from the published data. These two abstracts are one abstract, as the publisher
# and the preprint word it, with different section labels. In full, because the rewording
# runs right through it, so a part of it matches less than the whole does.
AGAA009_PUBLISHER = (
    "AIMS: Recent research suggests that evaluative conditioning (EC) can change implicit "
    "evaluations of alcohol and reduce drinking behaviors among college students (Houben et "
    "al., 2010a). This research has been conceptually replicated in two previous studies. To "
    "date, however, no direct and independent replication of the original study has been "
    "performed. In this paper, we report a high-powered direct replication of Houben et al.'s"
    " (2010a) study. METHOD: About 168 French college students took part in this "
    "preregistered study. Drinking behavior was assessed before and 2 weeks after the "
    "intervention. The intervention consisted of 120 trials of words related to alcoholic "
    "beverages or soft drinks paired with neutral, positive or negative pictures. The two "
    "conditions were factually equivalent and differed only in the repeated pairing between "
    "alcohol-related words and negative pictures; in the EC condition, but not in the control"
    " condition, alcohol-related words were systematically paired with negative pictures. "
    "RESULTS: EC did not change participants' implicit evaluations of alcohol and drinking "
    "behaviors. However, EC reduced drinking behaviors among hazardous drinkers. Yet, further"
    " non-preregistered Bayesian analysis did not provide much support for this hypothesis. "
    "CONCLUSION: This high-powered preregistered direct replication of Houben et al.'s "
    "(2010a) study suggests that the original effects are more fragile than initially "
    "thought. The effect of EC on drinking behaviors may be restricted to heavy drinkers, and"
    " we found no evidence that this effect is mediated by a change in implicit attitudes. It"
    " is necessary to perform further studies to test the original effects in clinical "
    "populations."
)
AGAA009_PREPRINT = (
    "Background. Recent research suggests that evaluative conditioning can change implicit "
    "evaluation of alcohol and reduce drinking behaviors among college students (Houben, "
    "Havermans et al., 2010). This study has been conceptually replicated in two previous "
    "studies. To date, however, there is no direct and independent replication of the "
    "original study. In this paper, we report a high-powered direct replication of Houben, "
    "Havermans et al.’s (2010) study. Method. One hundred and sixty-eight French college "
    "students took part in this preregistered study. Drinking behavior was assessed before "
    "the intervention and two weeks after. The intervention consisted of 120 trials of words "
    "related to alcoholic beverages or to soft drink, paired with neutral, positive, or "
    "negative pictures. The two conditions were factually equivalent and only differed by the"
    " contingency between alcohol-related words and negative pictures. In the evaluative "
    "conditioning condition, but not in the control condition, alcohol-related words were "
    "systematically paired with negative pictures. Results. Evaluative conditioning did not "
    "change implicit evaluation of alcohol and drinking behaviors. However, evaluative "
    "conditioning reduced drinking behaviors specifically among hazardous drinkers. "
    "Conclusion. This high-powered pre-registered direct replication of Houben, Havermans et "
    "al. (2010) suggests that the original effects are more fragile than initially thought. "
    "The effect of evaluative conditioning on drinking behaviors may be restricted to heavy "
    "drinkers, and we found no evidence that this effect is mediated by a change in implicit "
    "attitudes. Further studies are needed to test the original effects in clinical "
    "populations."
)

# Not one abstract: the registered plan on one record, the results on another.
RSOS_190699_PLAN = (
    "Evidence that affective factors (e.g., anxiety, depression, affect) are significantly "
    "related to individual differences in emotion recognition is mixed. Palermo et al. (2018 "
    "Journal of Experimental Psychology: Human Perception and Performance) recently reported "
    "that individuals who scored lower in anxiety performed significantly better on two "
    "measures of facial-expression recognition (emotion-matching and emotion-labeling tasks),"
    " but not a third measure (the Multimodal Emotion Recognition Test). By contrast, facial-"
    " expression recognition was not significantly correlated with measures of depression, "
    "positive or negative affect, empathy, or autistic-like traits. Because the range of "
    "affective factors considered in this study and its use of multiple "
    "expression-recognition tasks mean that it is a relatively comprehensive investigation of"
    " the role of affective factors in facial expression recognition, we propose to carry out"
    " a direct replication."
)
RSOS_190699_RESULTS = (
    "Evidence that affective factors (e.g. anxiety, depression, affect) are significantly "
    "related to individual differences in emotion recognition is mixed. Palermo et al . "
    "(Palermo et al . 2018 J. Exp. Psychol. Hum. Percept. Perform. 44 , 503–517) reported "
    "that individuals who scored lower in anxiety performed significantly better on two "
    "measures of facial-expression recognition (emotion-matching and emotion-labelling "
    "tasks), but not a third measure (the multimodal emotion recognition test). By contrast, "
    "facial-expression recognition was not significantly correlated with measures of "
    "depression, positive or negative affect, empathy, or autistic-like traits. Because the "
    "range of affective factors considered in this study and its use of multiple "
    "expression-recognition tasks mean that it is a relatively comprehensive investigation of"
    " the role of affective factors in facial expression recognition, we carried out a direct"
    " replication. In common with Palermo et al . (Palermo et al . 2018 J. Exp. Psychol. Hum."
    " Percept. Perform. 44 , 503–517), scores on the DASS anxiety subscale negatively "
    "predicted performance on the emotion recognition tasks across multiple analyses, "
    "although these correlations were only consistently significant for performance on the "
    "emotion-labelling task. However, and by contrast with Palermo et al . (Palermo et al . "
    "2018 J. Exp. Psychol. Hum. Percept. Perform. 44 , 503–517), other affective factors "
    "(e.g. those related to empathy) often also significantly predicted emotion-recognition "
    "performance. Collectively, these results support the proposal that affective factors "
    "predict individual differences in emotion recognition, but that these correlations are "
    "not necessarily specific to measures of general anxiety, such as the DASS anxiety "
    "subscale."
)


@pytest.mark.parametrize("judgement, expected", [
    (WRONG, True),
    ({**WRONG, "outcome_check": "correct"}, True),   # from a page that still asked for it
    ({**WRONG, "corrected_doi_o": "10.1/right"}, False),
    ({**WRONG, "corrected_title_o": "The right paper"}, False),
    ({**WRONG, "corrected_doi_o": "  "}, True),
    ({**WRONG, "additional_checks": {"was_unsure_original": True}}, False),
    ({**WRONG, "additional_checks": {"senior_reject": True}}, False),
    ({**WRONG, "type_check": "incorrect", "corrected_type": "not_validation"}, False),
    ({**WRONG, "original_check": "correct"}, False),
])
def test_what_counts_as_no_original(judgement, expected):
    assert no_original(judgement) is expected


def test_removal_needs_both_validators():
    assert removal_proposed(WRONG, WRONG) is True
    assert removal_proposed(WRONG, {**WRONG, "corrected_doi_o": "10.1/right"}) is False
    assert removal_proposed(WRONG, None) is False


@pytest.mark.parametrize("value, on", [("1", True), ("true", True), ("ON", True), ("0", False), ("", False), ("no", False)])
def test_the_flag(monkeypatch, value, on):
    monkeypatch.setenv("PAPER_REVIEW_ENABLED", value)
    assert paper_review.enabled() is on
    assert paper_review.available(PAIR) is on


@pytest.mark.parametrize("change", [
    {"type": "reproduction"}, {"doi_r": " "}, {"outcome": "cannot_be_determined"}, {"abstract_r": ""},
])
def test_what_keeps_the_one_record_screen(monkeypatch, change):
    monkeypatch.setenv("PAPER_REVIEW_ENABLED", "1")
    assert paper_review.available({**PAIR, **change}) is False


@pytest.mark.parametrize("checks", [None, "x", {}, {"paper_review": "x"}, {"paper_review": {"outcome_group": 3}}])
def test_a_malformed_note_earns_ordinary_points(checks):
    assert paper_review.marker(checks) is None


def test_points_use_the_paper_rules():
    class Cursor:
        def __init__(self, prior): self.prior = prior
        def execute(self, *_): pass
        def fetchall(self): return self.prior

    def req(**fields):
        return SimpleNamespace(**{"validator_notes": None, "type_check": "correct", "corrected_type": None,
                                  "outcome_check": "correct", **fields})

    rec = {"doi_r": "10.1/x", "record_id": "r"}
    note = {"outcome_group": "g"}
    earlier = {"additional_checks": {"paper_review": note}, "validator_notes": None}
    assert paper_review.points(Cursor([]), rec, 1, req(), 10, False, note) == 14
    assert paper_review.points(Cursor([earlier]), rec, 1, req(), 10, False, note) == 2
    assert paper_review.points(Cursor([earlier]), rec, 1, req(), 10, True, {"outcome_group": "h"}) == 5
    assert paper_review.points(Cursor([]), rec, 1, req(outcome_check="not_applicable"), 10, False, note) == 12


def test_the_rollback_warning_says_what_the_previous_release_does():
    """Consensus before removal_proposed: two "no outcome" answers look like agreement
    and the extracted original and outcome survive, so a rollback is not harmless and
    neither the schema nor the rollout notes may say it is."""
    from pathlib import Path
    from consensus_engine import _checks_agree, _corrections_agree, _resolve_final

    root = Path(__file__).resolve().parent.parent
    assert _checks_agree(WRONG, WRONG) and _corrections_agree(WRONG, WRONG)
    final = _resolve_final({"type": "replication", "outcome": "failed", "doi_o": "10.1/wrong",
                            "title_o": "Wrong paper", "doi_r": "10.1/x"}, WRONG, WRONG)
    assert (final["doi_o"], final["title_o"], final["outcome"]) == ("10.1/wrong", "Wrong paper", "failed")
    schema = (root / "db_schema.sql").read_text(encoding="utf-8")
    assert 'readable as "not checked"' not in schema
    assert 'run the check in docs/PROJECT.md, "Wrong paper, no replacement"' in schema
    assert "-- rollback-check:" in (root / "docs" / "PROJECT.md").read_text(encoding="utf-8")


def _row(status="validated", **fields):
    return {"validation_status": status, **fields}


def _differs(*rows):
    return paper_review.paper_differs_on(list(rows))


def _abstracts(first, second):
    return _differs(_row(abstract_r=first), _row(abstract_r=second))


def _links(first, second):
    return _differs(_row(url_r=first), _row(url_r=second))


@pytest.mark.parametrize("first, second", [
    (AGAA009_PUBLISHER, AGAA009_PREPRINT),
    # The label glued to the text.
    ("AbstractIn one of the most influential studies on family firm internationalization, "
     "Sciascia et al. (2012) advance a curvilinear relationship between family ownership and "
     "firm internationalization. We replicate their study adopting a three-step approach.",
     "In one of the most influential studies on family firm internationalization, Sciascia "
     "et al. (2012) advance a curvilinear relationship between family ownership and firm "
     "internationalization. We replicate their study adopting a three-step approach."),
    # The label on its own, and a space in "(n = 50)".
    ("Abstract The decoy effect occurs when adding an inferior third option biases choice "
     "between two others, even though the decoy is rarely chosen. Participants ( n = 50) "
     "completed 400 trials.",
     "The decoy effect occurs when adding an inferior third option biases choice between two "
     "others, even though the decoy is rarely chosen. Participants (n = 50) completed 400 "
     "trials."),
])
def test_the_real_pairs_are_one_abstract(first, second):
    assert _abstracts(first, second) == []
    assert _abstracts(second, first) == []


@pytest.mark.parametrize("first, second", [
    (RSOS_190699_PLAN, RSOS_190699_RESULTS),
    # Another paper's abstract on this DOI.
    ("Empirically analyzing empirical evidence One of the central goals in any scientific "
     "endeavor is to understand causality.",
     "Schnall, Benton, and Harvey (2008) hypothesized that physical cleanliness reduces the "
     "severity of moral judgments."),
])
def test_a_really_different_abstract_counts(first, second):
    assert _abstracts(first, second) == ["abstract_r"]
    assert _abstracts(second, first) == ["abstract_r"]


# One word, so that the word match cannot cover for the rule under test.
@pytest.mark.parametrize("variant", [
    "<p>Negative</p>", '<jats:p xml:lang="en">Negative</jats:p>', "NEGATIVE", " negative\n", "Negative!",
    "Abstract Negative", "Abstract: Negative", "ABSTRACT. Negative", "AbstractNegative", "Background. Negative",
    "AIMS: Negative", "Summary - Negative", "Objectives: Negative",
])
def test_what_does_not_make_an_abstract_different(variant):
    assert _abstracts("negative", variant) == []


def test_three_quarters_of_the_words_is_the_line():
    assert _abstracts("one two three four", "one two three five") == []
    assert _abstracts("one two three four", "one two five six") == ["abstract_r"]


def test_a_long_abstract_is_compared_like_a_short_one():
    """From 200 words on, difflib by default stops trusting words that repeat."""
    long_text = "we found no effect " * 60
    assert _abstracts("Today " + long_text, long_text) == []


def test_the_answer_does_not_depend_on_the_order_of_the_rows():
    """This pair matches 0.8 one way round and 0.6 the other."""
    first, second = "cats dogs cats fish", "dogs cats dogs birds cats fish"
    assert _abstracts(first, second) == _abstracts(second, first) == []


@pytest.mark.parametrize("variant", [
    "http://doi.org/10.1/x", "HTTPS://DOI.ORG/10.1/X", "https://dx.doi.org/10.1/x", "https://www.doi.org/10.1/x",
    "https://doi.org/10.1/x/", "https://doi.org/10.1/x//", "doi.org/10.1/x", "www.doi.org/10.1/x",
    "  https://doi.org/10.1/x\n",
])
def test_what_does_not_make_a_link_different(variant):
    assert _links("https://doi.org/10.1/x", variant) == []


@pytest.mark.parametrize("variant", ["https://doi.org/10.1/y", "https://osf.io/abc", "https://doi.org/10.1/x/y"])
def test_a_link_to_somewhere_else_counts(variant):
    assert _links("https://doi.org/10.1/x", variant) == ["url_r"]


@pytest.mark.parametrize("blank", [None, "", "   ", "<p> </p>", "Abstract."])
def test_a_blank_abstract_is_missing_not_a_disagreement(blank):
    assert _abstracts(AGAA009_PUBLISHER, blank) == []
    assert _differs(_row(abstract_r=AGAA009_PUBLISHER), _row()) == []


@pytest.mark.parametrize("blank", [None, "", "   ", "https://", "/"])
def test_a_blank_link_is_missing_not_a_disagreement(blank):
    assert _links("https://doi.org/10.1/x", blank) == []


def test_one_record_differs_from_nothing():
    assert _differs() == []
    assert _differs(_row(url_r="https://a.org/1", abstract_r="Some text")) == []


ONE = {"url_r": "https://a.org/1", "abstract_r": "A study of birds in cities."}
TWO = {"url_r": "https://b.org/2", "abstract_r": "Survey results on fish stocks and quotas."}


@pytest.mark.parametrize("status", ["consensus_reached", "validated"])
def test_a_record_on_its_way_to_flora_counts(status):
    assert _differs(_row("consensus_reached", **ONE), _row(status, **TWO)) == ["url_r", "abstract_r"]


@pytest.mark.parametrize("status", ["rejected", "need_review", "unvalidated", "validation_inprogress", None])
def test_no_other_record_counts(status):
    assert _differs(_row("validated", **ONE), _row(status, **TWO)) == []
    assert _differs(_row(status, **ONE), _row(status, **TWO)) == []


def test_only_the_field_that_differs_is_named():
    assert _differs(_row(**ONE), _row(**{**ONE, "url_r": TWO["url_r"]})) == ["url_r"]
    assert _differs(_row(**ONE), _row(**{**ONE, "abstract_r": TWO["abstract_r"]})) == ["abstract_r"]
    assert _differs(_row(**ONE), _row(**ONE)) == []


def test_the_value_that_would_be_published_is_the_one_compared():
    corrected = {"final_url_r": ONE["url_r"], "final_abstract_r": ONE["abstract_r"]}
    assert _differs(_row(**ONE), _row(**TWO, **corrected)) == []
    assert _differs(_row(**TWO, **corrected), _row(**TWO)) == ["url_r", "abstract_r"]
    blank_final = {"final_url_r": "", "final_abstract_r": None}
    assert _differs(_row(**ONE), _row(**ONE, **blank_final)) == []


def test_a_published_record_counts_with_what_it_published():
    """An import rewrites the extracted value and leaves the published row alone."""
    moved = _row(url_r="https://osf.io/new", final_url_r="https://osf.io/final", published=True,
                 published_url_r="https://osf.io/old")
    assert _differs(moved, _row("consensus_reached", url_r="https://osf.io/old")) == []
    assert _differs(moved, _row("consensus_reached", url_r="https://osf.io/new")) == ["url_r"]

    blank = _row(url_r="https://osf.io/new", published=True, published_url_r=None)
    assert _differs(blank, _row("consensus_reached", url_r="https://osf.io/other")) == []


def test_comparison_signs_are_not_markup():
    assert paper_review._abstract_key("p<q and r>s the effect") == ("p", "q", "and", "r", "s", "the", "effect")
    assert paper_review._abstract_key('<jats:p class="x">The <i>effect</i><br/></jats:p>') == ("the", "effect")


def test_a_script_written_without_spaces_compares_by_character():
    text = "研究结果表明该效应可以被重复" * 6
    assert _abstracts(text, text[:-1] + "验") == []
    assert _abstracts(text, "完全不同的另一篇摘要内容" * 7) == ["abstract_r"]


def test_the_paper_set_compares_the_copies_and_does_not_send_them():
    class Cursor:
        def execute(self, sql, params): self.sql = sql
        def fetchall(self): return rows

    def row(n, status, **copies):
        return {"record_id": n, "validation_status": status, "validator_1": None, "validator_2": None,
                 "url_r": None, "final_url_r": None, "abstract_r": None, "final_abstract_r": None,
                 "published": False, "published_url_r": None, "published_abstract_r": None, **copies}

    rows = [row("a", "validated", **ONE), row("b", "consensus_reached", **TWO), row("c", "rejected", **ONE)]
    originals, differs_on = paper_review.admin_paper_set(Cursor(), {"doi_r": "10.1/x", "record_id": "a"})
    assert differs_on == ["url_r", "abstract_r"]
    assert [o["record_id"] for o in originals] == ["a", "b", "c"]
    for original in originals:
        assert set(original) == {"record_id", "validation_status", "original_checks", "outcome_checks",
                                 "shared_check", "is_current"}

    rows = rows[:1]
    assert paper_review.admin_paper_set(Cursor(), {"doi_r": "10.1/x"}) == ([], [])
    assert paper_review.admin_paper_set(Cursor(), {"doi_r": " "}) == ([], [])
