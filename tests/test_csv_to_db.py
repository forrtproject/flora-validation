import pandas as pd


def test_url_o_prefers_csv_value():
    """DOI-less originals (books, chapters, pre-DOI papers) carry their identity
    in the CSV's own url_o (usually an OpenAlex link) — it must survive import
    rather than being overwritten by a derived (and here, empty) doi.org link."""
    from csv_to_db import _url_o
    row = pd.Series({"doi_o": "", "url_o": "https://openalex.org/W2003152982"})
    assert _url_o(row) == "https://openalex.org/W2003152982"


def test_url_o_falls_back_to_derived_doi_link():
    """When the CSV doesn't supply url_o, fall back to a doi.org link built from doi_o."""
    from csv_to_db import _url_o
    row = pd.Series({"doi_o": "10.1000/xyz", "url_o": ""})
    assert _url_o(row) == "https://doi.org/10.1000/xyz"


def test_url_o_blank_when_neither_present():
    from csv_to_db import _url_o
    row = pd.Series({"doi_o": "", "url_o": ""})
    assert _url_o(row) == ""


def test_url_o_csv_value_wins_even_with_a_doi_present():
    """If the CSV supplies both, prefer its url_o over a derived doi.org link —
    it may point somewhere more specific (e.g. an OA copy)."""
    from csv_to_db import _url_o
    row = pd.Series({"doi_o": "10.1000/xyz", "url_o": "https://openalex.org/W123"})
    assert _url_o(row) == "https://openalex.org/W123"


# ---------------------------------------------------------------------------
# Ambiguous DOI-less originals — see docs/PROJECT.md "DOI-less originals".
# doi_o is '' for ALL of them, so study_o is the only thing keeping two
# originals of the same replication apart under validated's natural key.
# ---------------------------------------------------------------------------

def _row(key, doi_r, study_r, study_o, doi_o=""):
    return {"key": key, "doi_r": doi_r, "study_r": study_r,
            "study_o": study_o, "doi_o": doi_o}


def test_duplicate_titles_same_replication_are_flagged():
    """Two DOI-less originals of the SAME replication sharing a title would
    collide on (doi_r, study_r, doi_o='', study_o) and silently merge."""
    from csv_to_db import _flag_ambiguous_doi_o_titles
    flagged = _flag_ambiguous_doi_o_titles([
        _row("p1", "10.1/rep", "Rep Study", "Gender Advertisements"),
        _row("p2", "10.1/rep", "Rep Study", "Gender Advertisements"),
    ])
    assert set(flagged) == {"p1", "p2"}
    assert "shares its title" in flagged["p1"]


def test_near_duplicate_titles_are_flagged():
    """Comparison is loose (case + whitespace) so near-dupes are caught too."""
    from csv_to_db import _flag_ambiguous_doi_o_titles
    flagged = _flag_ambiguous_doi_o_titles([
        _row("p1", "10.1/rep", "Rep", "Gender  Advertisements"),
        _row("p2", "10.1/rep", "Rep", "gender advertisements"),
    ])
    assert set(flagged) == {"p1", "p2"}


def test_blank_title_is_flagged_on_its_own():
    """A blank title can't distinguish anything — flag it even without a twin."""
    from csv_to_db import _flag_ambiguous_doi_o_titles
    flagged = _flag_ambiguous_doi_o_titles([_row("p1", "10.1/rep", "Rep", "")])
    assert "blank title" in flagged["p1"]


def test_same_title_different_replications_is_fine():
    """Two replications may each cite the same DOI-less book — different doi_r
    keeps the validated rows distinct, so this is NOT a collision."""
    from csv_to_db import _flag_ambiguous_doi_o_titles
    flagged = _flag_ambiguous_doi_o_titles([
        _row("p1", "10.1/repA", "Rep A", "Gender Advertisements"),
        _row("p2", "10.2/repB", "Rep B", "Gender Advertisements"),
    ])
    assert flagged == {}


def test_distinct_titles_are_not_flagged():
    from csv_to_db import _flag_ambiguous_doi_o_titles
    flagged = _flag_ambiguous_doi_o_titles([
        _row("p1", "10.1/rep", "Rep", "Gender Advertisements"),
        _row("p2", "10.1/rep", "Rep", "Frame Analysis"),
    ])
    assert flagged == {}


def test_originals_with_real_dois_are_ignored():
    """Records WITH a doi_o are never at risk — the DOI keeps them apart, so
    duplicate titles there are none of this check's business."""
    from csv_to_db import _flag_ambiguous_doi_o_titles
    flagged = _flag_ambiguous_doi_o_titles([
        _row("p1", "10.1/rep", "Rep", "Same Title", doi_o="10.1/origA"),
        _row("p2", "10.1/rep", "Rep", "Same Title", doi_o="10.1/origB"),
    ])
    assert flagged == {}


def test_mixed_group_flags_only_the_doi_less_pair():
    """A DOI-bearing original sharing a title with DOI-less ones doesn't count
    toward the collision — only the DOI-less ones can actually merge."""
    from csv_to_db import _flag_ambiguous_doi_o_titles
    flagged = _flag_ambiguous_doi_o_titles([
        _row("p1", "10.1/rep", "Rep", "Shared Title", doi_o="10.1/real"),
        _row("p2", "10.1/rep", "Rep", "Shared Title"),
    ])
    assert flagged == {}
