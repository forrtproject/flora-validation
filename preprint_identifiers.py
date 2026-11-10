"""Keep replaced preprint identifiers at the validation publication boundary."""
from preprint_dedup import append_alt_identifier, is_preprint_doi, normalize_doi


def is_preprint_identifier(value):
    """Recognise preprint identifiers; generic OSF deposits need separate evidence."""
    doi = normalize_doi(value)
    return bool(doi and (is_preprint_doi(doi)
                        or doi.startswith('10.2196/preprints.')))


def preserve_preprint(existing, old, new):
    """Append a replaced preprint once, comparing bare and resolver DOI forms."""
    old, new = normalize_doi(old), normalize_doi(new)
    if not old or not new or old == new or not is_preprint_identifier(old):
        return existing
    aliases = append_alt_identifier(existing, None)
    if any(normalize_doi(part.strip()) == old for part in (aliases or '').split(',')):
        return existing
    return append_alt_identifier(existing, old)


def validated_identifiers(row):
    """Publish validator DOI decisions with aliases, including original-side edits.

    All validation routes converge on validated -> source_records. The retained
    extracted DOI and logged admin transitions recover earlier preprint versions;
    no inference from bibliographic similarity is made here.
    """
    result = {field: row.get(field) for side in ('o', 'r')
              for field in ('doi_' + side, 'alt_identifier_' + side)}
    published = normalize_doi(row.get('doi_r_published'))
    if published and is_preprint_identifier(result['doi_r']) and not is_preprint_identifier(published):
        result['doi_r'] = published
    extracted = row.get('_extracted_identifiers') or {}
    history = row.get('_identifier_history') or []
    published = row.get('_published_identifiers') or []
    for side in ('o', 'r'):
        doi, alt = 'doi_' + side, 'alt_identifier_' + side
        for prior in [row.get(doi), extracted.get(doi)]:
            result[alt] = preserve_preprint(result[alt], prior, result[doi])
        # Extractor refreshes may have overwritten the raw DOI since a previous
        # validation. Its source snapshots still retain the published history.
        for prior in published:
            for old in [prior.get(doi), *(prior.get(alt) or '').split(',')]:
                result[alt] = preserve_preprint(result[alt], old, result[doi])
        # An intermediate preprint version may have replaced the extracted DOI.
        # Only follow transitions that actually lead to the published identifier.
        edges = [change[doi] for change in history
                 if isinstance(change.get(doi), list) and len(change[doi]) == 2]
        targets = {normalize_doi(result[doi])}
        while True:
            previous = set(targets)
            for old, new in edges:
                if normalize_doi(new) in targets:
                    targets.add(normalize_doi(old))
            if targets == previous:
                break
        for old, new in edges:
            if normalize_doi(new) in targets:
                result[alt] = preserve_preprint(result[alt], old, result[doi])
    return result


# The same provenance is used by the daily CSV and the source-record bridge.
VALIDATED_PROVENANCE_JOIN = """
    LEFT JOIN unvalidated u ON u.record_id = v.record_id
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object(
            'doi_o', d.changes->'doi_o', 'doi_r', d.changes->'doi_r')
            ORDER BY d.decision_id) AS changes
        FROM admin_decisions d WHERE d.record_id = v.record_id
    ) identifier_history ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object(
            'doi_o', s.raw->>'doi_o', 'doi_r', s.raw->>'doi_r',
            'alt_identifier_o', s.alt_identifier_o,
            'alt_identifier_r', s.alt_identifier_r) ORDER BY s.sheet_row_id) AS identifiers
        FROM source_records s
        WHERE s.source = 'validated' AND s.raw->>'record_id' = v.record_id::text
    ) published_history ON true
"""
VALIDATED_PROVENANCE_COLUMNS = """
    jsonb_build_object('doi_o', u.doi_o, 'doi_r', u.doi_r)
        AS _extracted_identifiers,
    identifier_history.changes AS _identifier_history,
    published_history.identifiers AS _published_identifiers
"""
