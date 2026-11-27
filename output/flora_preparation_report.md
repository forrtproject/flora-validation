# FLoRA preparation report

Generated: 2026-11-27T03:08:55+00:00

Status: **needs review**

Mode: built from the database

Rows: 3,004

## Stages

- build flora.csv and check IDs: passed
- structural validation: needs review — 32 problems and 11 possible problems
- network validation: needs review
- release files and history: passed
- store dataset: passed — 3,004 rows in flora_data
- write output files: passed

## Warnings

- 59 records left out for a missing title; see flora_export_log.csv.
- 65 merged records whose duplicates gave different outcomes; see dup_outcome_conflicts.csv.
- 25 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates); see preprint_dedup_candidates.csv.
- Structural validation found 32 problems and 11 possible problems.
- Network validation needs review: 19 problems and 72 possible problems. Original work types unavailable for 2 DOI(s); previous review decisions retained. This screening is advisory..

## Stored dataset

- 3,004 rows stored in `flora_data`, which Download full CSV and the public API serve.

## Diagnostic files

- flora_export_log.csv: 59 records left out for a missing title.
- dup_outcome_conflicts.csv: 65 merged records whose duplicates gave different outcomes.
- preprint_dedup_candidates.csv: 25 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates).

The JSON report lists the affected records.

## FLoRA structural validation

_Generated: 2026-11-27 03:12:24 UTC | Dataset: 3,004 rows_

Each item names the FLoRA record by its FLoRA ID and `doi_o | doi_r` (or `url_r`), says what is wrong and, after the dash, gives the value.

### Problems (32)

<details><summary><b>Invalid URLs</b> (11 findings)</summary>

_A url column holds something other than one complete http(s) link._

- VAL-000639 (`10.1126/science.7455683 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000640 (`10.1086/265467 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000641 (`10.1016/j.jesp.2010.05.014 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000642 (`10.1086/268936 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000643 (`10.1080/00224545.1936.9919891 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000644 (`10.1017/s1930297500001170 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000645 (`10.1177/01461672952111004 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000646 (`10.1177/0956797611414726 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000647 (`10.1037/0022-3514.83.1.44 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000648 (`10.1037/a0029288 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`
- VAL-000649 (`10.1016/j.jesp.2009.03.009 | 10.1027/1864-9335/a000178`): url_r holds a DOI, not a link — `10.1027/1864-9335/a000178`

</details>

**Duplicate records** (6 findings):

_Rows the build would have made one record: the same type, doi_o, doi_r and url_r, or the same DOIs with one url_r between them._

- REPL-000933, REPL-001497 (`10.1007/s10683-015-9437-0 | 10.1007/s40881-022-00118-y`): 2 rows the build would make one record — `replication|10.1007/s10683-015-9437-0|10.1007/s40881-022-00118-y|`
- FRED-000199, FRED-000200 (`10.1037/0096-3445.135.4.641 | 10.1037/pspa0000214`): 2 rows the build would make one record — `replication|10.1037/0096-3445.135.4.641|10.1037/pspa0000214|`
- VAL-000207, VAL-000591 (`10.1162/jocn.2008.21154 | 10.3389/fnhum.2021.680885`): 2 rows the build would make one record — `replication|10.1162/jocn.2008.21154|10.3389/fnhum.2021.680885|`
- VAL-000260, VAL-000345 (`10.1073/pnas.0804388105 | 10.1016/j.infbeh.2019.03.005`): 2 rows the build would make one record — `replication|10.1073/pnas.0804388105|10.1016/j.infbeh.2019.03.005|doi.org/10.31234/osf.io/fs2ez`
- VAL-000285, VAL-000607 (`10.1027/1618-3169/a000414 | 10.1027/1618-3169/a000486`): 2 rows the build would make one record — `replication|10.1027/1618-3169/a000414|10.1027/1618-3169/a000486|osf.io/tj4sh`
- VAL-000293, VAL-000549 (`10.1111/puar.12401 | 10.1080/14719037.2016.1195009`): 2 rows the build would make one record — `replication|10.1111/puar.12401|10.1080/14719037.2016.1195009|`

<details><summary><b>Missing required fields</b> (15 findings)</summary>

_title_o, title_r, doi_r or url_r, and an identifier for the original: doi_o, a url_o that is a link or DOI (not a title), alt_identifier_o or oa_work_id_o._

- REPL-000077 (`| 10.1016/j.lindif.2015.03.029`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000091 (`| 10.1177/2053168016652420`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000141 (`| 10.1177/0016986220932533`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000146 (`| 10.1002/aur.2496`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000234 (`| 10.18148/srm/2012.v6i1.5092`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000241 (`| 10.1103/physrevstper.10.010110`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000244 (`| 10.1080/22243534.2015.11828344`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000306 (`| 10.1017/cnj.2023.13`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000356 (`| 10.1177/0958928712471227`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000448 (`| 10.1371/journal.pone.0120644`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000621 (`| 10.5539/ibr.v5n4p71`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000167 (`| 10.1177/0098628311421328`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000357 (`| 10.1177/1029864913489703`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-001403 (`| 10.17885/heiup.1157.c19369`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- VAL-000394 (`| 10.5406/bulcouresmusedu.193.0045`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o

</details>

### Possible problems (11)

_Heuristics: a person decides whether each one is a problem._

**Replication year before original year** (5 findings):

_Could be swapped entries or a data-entry error. Not reported when the original has a preprint or other earlier version._

- REPL-000014 (`10.1080/13639080.2011.573776 | 10.1007/s12186-012-9090-0`): year_r is before year_o (2013) — `2012`
- REPL-000476 (`10.1002/bin.1836 | 10.1002/jaba.659`): year_r is before year_o (2021) — `2019`
- REPL-001689 (`10.1177/1042258719892987 | 10.1111/apps.12247`): year_r is before year_o (2021) — `2020`
- VAL-000528 (`10.1007/s10389-020-01338-4 | 10.3389/fpubh.2021.620264`): year_r is before year_o (2022) — `2021`
- VAL-000557 (`10.1080/21670811.2021.1969974 | 10.1037/tmb0000083`): year_r is before year_o (2023) — `2022`

**Same paper pair as replication and reproduction** (5 findings):

_The same doi_o and doi_r (or url_r, without a doi_r) as a replication and as a reproduction: possibly one coding entered in both sheets._

- REPL-000312, REPRO-000378 (`10.1086/669122 | 10.31235/osf.io/2nwft`): the same paper pair is published as a replication and as a reproduction — `replication, reproduction`
- REPL-000748, REPRO-000376 (`10.1016/s0883-9026(02)00097-6 | 10.1177/10422587251322409`): the same paper pair is published as a replication and as a reproduction — `replication, reproduction`
- REPL-001309, REPRO-000305 (`10.1086/511995 | 10.5018/economics-ejournal.ja.2017-13`): the same paper pair is published as a replication and as a reproduction — `replication, reproduction`
- REPRO-000340, VAL-000127 (`10.1257/0002828054201242 | 10.1002/jae.2922`): the same paper pair is published as a replication and as a reproduction — `replication, reproduction`
- REPL-001633, REPRO-000018 (`10.1257/aer.20201550 | https://shs.hal.science/hal-05474766/`): the same paper pair is published as a replication and as a reproduction — `replication, reproduction`

**Pair entered both ways round** (1 finding):

_One row's doi_o and doi_r are another row's doi_r and doi_o._

- REPL-000476, REPL-000529: the paper pair is entered both ways round: one row's original is the other's replication — `10.1002/bin.1836 | 10.1002/jaba.659`

---

**Checks passed:**

- Outcome values valid
- Text fields hold text
- Type values valid
- Source values valid
- Year ranges reasonable
- DOI format valid
- Identifier lists use one separator
- DOI mapped to conflicting references
- No title_r marks a retraction, no title_o is a notice of one
- No 'NA'/'N/A' strings
- No mis-decoded characters
- References name their authors
- Report links lead to the report
- Replication titles look like titles


## FLoRA network validation

_Generated: 2026-11-27 03:12:24 UTC | Dataset: 3,004 rows_

**Original work types unavailable for 2 DOI(s); previous review decisions retained. This screening is advisory..**

Each item names the FLoRA records carrying the DOI or link by FLoRA ID, says what is wrong and, after the dash, gives the DOI or link.

### Problems (19)

**Retracted papers:** none

**DOIs not registered at doi.org** (8 findings):

- VAL-000005 (`10.1016/j.im.2015.06.009 | 10.17705/1atrr.00068`): doi_r is not registered at doi.org — `10.17705/1atrr.00068`
- VAL-000107 (`10.25300/misq/2018/13853 | 10.17705/1atrr.00067`): doi_r is not registered at doi.org — `10.17705/1atrr.00067`
- VAL-000125 (`10.1287/isre.1040.0032 | 10.17705/1atrr.00046`): doi_r is not registered at doi.org — `10.17705/1atrr.00046`
- VAL-000138 (`10.5465/amj.2010.51468978 | 10.17705/1atrr.00066`): doi_r is not registered at doi.org — `10.17705/1atrr.00066`
- VAL-000185 (`10.1080/07421222.2017.1394083 | 10.17705/1atrr.00055`): doi_r is not registered at doi.org — `10.17705/1atrr.00055`
- VAL-000215 (`10.2307/249754 | 10.17705/1atrr.00064`): doi_r is not registered at doi.org — `10.17705/1atrr.00064`
- VAL-000427 (`10.1287/isre.1040.0032 | 10.17705/1atrr.00061`): doi_r is not registered at doi.org — `10.17705/1atrr.00061`
- VAL-000617 (`10.1023/a:1009986821907 | 10.17705/1atrr.00045`): doi_r is not registered at doi.org — `10.17705/1atrr.00045`

<details><summary><b>Dead links</b> (11 findings)</summary>

- REPL-000073 (`10.3102/0002831211419649 | 10.1037/a0036307`): oa_url_r is dead (connection refused; an open-access link from enrichment, not from the source record) — `http://orbilu.uni.lu/handle/10993/18672`
- REPL-001099 (`10.1037/edu0000115 | 10.1037/edu0000433`): oa_url_r is dead (connection refused; an open-access link from enrichment, not from the source record) — `http://orbilu.uni.lu/handle/10993/45413`
- REPL-001213 (`10.1123/jmld.2015-0036 | 10.51224/cik.v1i2.39`): oa_url_r is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `https://storkjournals.org/index.php/cik/article/download/39/23`
- FRED-000772 (`10.1177/0956797611434748 | 10.1016/j.cognition.2014.09.003`): oa_url_r is dead (connection refused; an open-access link from enrichment, not from the source record) — `http://orbilu.uni.lu/handle/10993/17967`
- VAL-000534 (`10.2147/ott.s136006 | 10.1016/j.tranon.2017.09.008`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `https://www.dovepress.com/getfile.php?fileID=35846`
- VAL-000535 (`10.2147/ott.s129042 | 10.1016/j.tranon.2017.09.008`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `https://www.dovepress.com/getfile.php?fileID=34813`
- VAL-000571 (`10.2147/ppa.s30763 | 10.2147/ppa.s96880`): url_r is dead (HTTP 404) — `https://www.dovepress.com/getfile.php?fileID=29102`
- VAL-000571 (`10.2147/ppa.s30763 | 10.2147/ppa.s96880`): oa_url_r is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `https://www.dovepress.com/getfile.php?fileID=29102`
- VAL-000571 (`10.2147/ppa.s30763 | 10.2147/ppa.s96880`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `https://www.dovepress.com/getfile.php?fileID=13146`
- VAL-000595 (`10.1016/j.cub.2012.05.054 | 10.1016/j.brainres.2018.06.031`): url_o is dead (the DOI is not registered at doi.org) — `https://doi.org/10.1523/jneurosci.1915-12.2012`
- VAL-000620 (`10.1163/9789004270848_008 | 10.5117/tvt2017.1.jans`): url_o is dead (the DOI is not registered at doi.org) — `https://doi.org/10.5117/tvt2015.1.graa`

</details>

### Possible problems (72)

_Heuristics: a person decides whether each one is a problem._

**Expressions of concern** (3 findings):

- FRED-000510 (`10.1111/j.0956-7976.2004.00757.x | 10.1525/collabra.32572`): doi_o has an expression of concern (Retraction Watch) — `10.1111/j.0956-7976.2004.00757.x`
- FRED-000906 (`10.1509/jmkr.45.6.633 | 10.1177/2515245918781032`): doi_o has an expression of concern (Retraction Watch) — `10.1509/jmkr.45.6.633`
- FRED-000187 (`10.1177/0956797611432497 | 10.1027/1864-9335/a000191`): doi_o has an expression of concern (Retraction Watch) — `10.1177/0956797611432497`

**Original probably has a DOI** (3 findings):

- REPL-000409 (`| 10.1111/jpr.12599`): doi_o is blank; Crossref lists this paper with a DOI: 'Assimilation and Contrast Effects of Culture Priming Among Hong Kong Chinese' (2016, Ng) — `10.1177/0022022116631826`
- REPL-000412 (`| 10.36948/ijfmr.2025.v07i06.61995`): doi_o is blank; Crossref lists this paper with a DOI: 'Retro-cue benefits in working memory without sustained focal attention' (2014, Rerko) — `10.3758/s13421-013-0392-8`
- REPL-001445 (`| 10.1525/collabra.163080`): doi_o is blank; Crossref lists this paper with a DOI: 'The dark side of self- and social perception: Black uniforms and aggression in …' (1988, Frank) — `10.1037/0022-3514.54.1.74`

**Original target needs review (higher priority):** none

<details><summary><b>Original publication format needs review</b> (66 findings)</summary>

- REPL-000015 (`10.1037/bar0000276 | 10.1002/bin.70067`): Publication-format review. OpenAlex type: letter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1037/bar0000276 — `10.1037/bar0000276`
- REPL-000019 (`10.1145/3328778.3366948 | 10.1080/08993408.2022.2071543`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3328778.3366948 — `10.1145/3328778.3366948`
- REPL-000045 (`10.1002/ana.22403 | 10.1161/strokeaha.111.623090`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1002/ana.22403 — `10.1002/ana.22403`
- REPL-000060 (`10.1017/cbo9781139524575.009 | 10.1111/j.1540-4781.2011.01236.x`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1017/cbo9781139524575.009 — `10.1017/cbo9781139524575.009`
- REPL-000099 (`10.18653/v1/p16-2067 | 10.18653/v1/d17-1076`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.18653/v1/p16-2067 — `10.18653/v1/p16-2067`
- REPL-000107 (`10.1073/pnas.0905267106 | 10.1038/s41598-018-35209-6`): Publication-format review. OpenAlex type: letter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1073/pnas.0905267106 — `10.1073/pnas.0905267106`
- REPL-000153 (`10.1093/acprof:oso/9780190205782.001.0001 | 10.1017/s0003055422000946`): Publication-format review. OpenAlex type: book. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1093/acprof:oso/9780190205782.001.0001 — `10.1093/acprof:oso/9780190205782.001.0001`
- REPL-000225 (`10.1038/ng.3598 | 10.1371/journal.pone.0261477`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.3598 — `10.1038/ng.3598`
- REPL-000233 (`10.1145/1368088.1368161 | 10.1109/esem.2011.30`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/1368088.1368161 — `10.1145/1368088.1368161`
- REPL-000242 (`10.1109/icse.2007.32 | 10.1145/2652524.2652556`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1109/icse.2007.32 — `10.1109/icse.2007.32`
- REPL-000267 (`10.18420/muc2017-up-0002 | 10.1016/j.heliyon.2019.e02434`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.18420/muc2017-up-0002 — `10.18420/muc2017-up-0002`
- REPL-000285 (`10.1145/3517745.3561436 | 10.1145/3618257.3624820`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3517745.3561436 — `10.1145/3517745.3561436`
- REPL-000313 (`10.7551/mitpress/4117.003.0010 | 10.1515/gcla-2014-0004`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.7551/mitpress/4117.003.0010 — `10.7551/mitpress/4117.003.0010`
- REPL-000327 (`10.1016/b978-0-12-205050-3.50008-7 | 10.1007/s11205-005-5074-x`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1016/b978-0-12-205050-3.50008-7 — `10.1016/b978-0-12-205050-3.50008-7`
- REPL-000520 (`10.1145/3180155.3180192 | 10.48550/arxiv.2603.15935`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3180155.3180192 — `10.1145/3180155.3180192`
- REPL-000534 (`10.1007/978-3-031-76332-8_21 | 10.57229/2834-2267.1072`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1007/978-3-031-76332-8_21 — `10.1007/978-3-031-76332-8_21`
- REPL-000623 (`10.1093/acprof:osobl/9780199927418.003.0013 | 10.1080/09515089.2014.893288`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1093/acprof:osobl/9780199927418.003.0013 — `10.1093/acprof:osobl/9780199927418.003.0013`
- REPL-000646 (`10.3998/mpub.9946611 | 10.1017/xps.2020.18`): Publication-format review. OpenAlex type: book. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.3998/mpub.9946611 — `10.3998/mpub.9946611`
- REPL-000704 (`10.1145/2025113.2025141 | 10.1109/esem.2015.7321191`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/2025113.2025141 — `10.1145/2025113.2025141`
- REPL-000709 (`10.1145/1449715.1449724 | 10.1109/tvcg.2021.3101854`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/1449715.1449724 — `10.1145/1449715.1449724`
- REPL-000724 (`10.1075/sibil.9.05bre | 10.1111/j.1944-9720.2010.01057.x`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1075/sibil.9.05bre — `10.1075/sibil.9.05bre`
- REPL-000758 (`10.1177/0022219421997103 | 10.1177/00222194231215031`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1177/0022219421997103 — `10.1177/0022219421997103`
- REPL-000850 (`10.1038/ng.2802 | 10.18632/oncotarget.7945`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.2802 — `10.1038/ng.2802`
- REPL-000881 (`10.1145/2380718.2380739 | 10.1017/s1930297500002941`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/2380718.2380739 — `10.1145/2380718.2380739`
- REPL-000898 (`10.1017/s0140525x07001185 | 10.1093/cercor/bhaa282`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1017/s0140525x07001185 — `10.1017/s0140525x07001185`
- REPL-000917 (`10.1145/985692.985709 | 10.1093/hcr/hqab019`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/985692.985709 — `10.1145/985692.985709`
- REPL-000922 (`10.1145/3379597.3387482 | 10.1109/icpc52881.2021.00052`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3379597.3387482 — `10.1145/3379597.3387482`
- REPL-000966 (`10.4324/9780203865972-24 | 10.1098/rsos.250290`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.4324/9780203865972-24 — `10.4324/9780203865972-24`
- REPL-000977 (`10.1109/vis54172.2023.00016 | 10.1109/vis60296.2025.00070`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1109/vis54172.2023.00016 — `10.1109/vis54172.2023.00016`
- REPL-001019 (`10.18653/v1/2020.findings-emnlp.139 | 10.1109/iwsc55060.2022.00015`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.18653/v1/2020.findings-emnlp.139 — `10.18653/v1/2020.findings-emnlp.139`
- REPL-001038 (`10.2307/3512141 | 10.1111/j.1468-5906.2009.01499.x`): Publication-format review. OpenAlex type: book-review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.2307/3512141 — `10.2307/3512141`
- REPL-001071 (`10.4159/9780674369542 | 10.1017/ssh.2017.27`): Publication-format review. OpenAlex type: book. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.4159/9780674369542 — `10.4159/9780674369542`
- REPL-001117 (`10.1145/1404520.1404531 | 10.1080/08993408.2022.2079866`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/1404520.1404531 — `10.1145/1404520.1404531`
- REPL-001181 (`10.1093/acprof:osobl/9780199927418.003.0013 | 10.1080/09515089.2013.878834`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1093/acprof:osobl/9780199927418.003.0013 — `10.1093/acprof:osobl/9780199927418.003.0013`
- REPL-001248 (`10.4324/9780203718087-5 | 10.3390/jintelligence7010006`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.4324/9780203718087-5 — `10.4324/9780203718087-5`
- REPL-001269 (`10.1017/cbo9780511667268.005 | 10.1017/s0261444813000049`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1017/cbo9780511667268.005 — `10.1017/cbo9780511667268.005`
- REPL-001273 (`10.1038/ng.2390 | 10.1155/2013/641596`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.2390 — `10.1038/ng.2390`
- REPL-001275 (`10.1038/ng.2520 | 10.1371/journal.pone.0102100`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.2520 — `10.1038/ng.2520`
- REPL-001278 (`10.1007/978-3-642-20677-1_13 | 10.1145/2593868.2593874`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1007/978-3-642-20677-1_13 — `10.1007/978-3-642-20677-1_13`
- REPL-001296 (`10.1038/ng.2802 | 10.18632/oncotarget.12648`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.2802 — `10.1038/ng.2802`
- REPL-001297 (`10.1038/ng.2802 | 10.18632/oncotarget.9819`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.2802 — `10.1038/ng.2802`
- REPL-001392 (`10.1145/2702123.2702593 | 10.1007/s10664-025-10739-6`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/2702123.2702593 — `10.1145/2702123.2702593`
- REPL-001632 (`10.1145/3545945.3569766 | 10.1145/3770762.3772568`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3545945.3569766 — `10.1145/3545945.3569766`
- REPL-001634 (`10.1038/s41467-022-28252-5 | 10.1016/j.xjidi.2026.100490`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/s41467-022-28252-5 — `10.1038/s41467-022-28252-5`
- FRED-000269 (`10.1126/science.1091721 | 10.15626/mp.2022.3108`): Publication-format review. OpenAlex type: editorial. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1126/science.1091721 — `10.1126/science.1091721`
- FRED-000608 (`10.1145/3105726.3106182 | 10.1145/3649217.3653545`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3105726.3106182 — `10.1145/3105726.3106182`
- FRED-000112 (`10.1145/3173574.3174188 | 10.1098/rsos.231240`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3173574.3174188 — `10.1145/3173574.3174188`
- FRED-000085 (`10.1145/3290605.3300469 | 10.1098/rsos.231240`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3290605.3300469 — `10.1145/3290605.3300469`
- FRED-000561 (`10.4324/9781032633275-7 | 10.1177/2515245918810225`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.4324/9781032633275-7 — `10.4324/9781032633275-7`
- VAL-000072 (`10.1037/1082-989x.6.2.161 | 10.1037/a0014697`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1037/1082-989x.6.2.161 — `10.1037/1082-989x.6.2.161`
- VAL-000139 (`10.36019/9781978817111 | 10.1111/j.1747-4469.2011.01256.x`): Publication-format review. OpenAlex type: book. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.36019/9781978817111 — `10.36019/9781978817111`
- VAL-000166 (`10.1145/3196398.3196421 | 10.7717/peerj-cs.245`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3196398.3196421 — `10.1145/3196398.3196421`
- REPL-000501 (`10.1016/j.jenvp.2013.09.003 | 10.17605/osf.io/hz97d`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1016/j.jenvp.2013.09.003 — `10.1016/j.jenvp.2013.09.003`
- REPL-001425 (`10.1093/acprof:oso/9780199580866.003.0010 | 10.1142/s1084946721500084`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1093/acprof:oso/9780199580866.003.0010 — `10.1093/acprof:oso/9780199580866.003.0010`
- REPRO-000163 (`10.1038/s41562-023-01712-8 | https://osf.io/9y47b/`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/s41562-023-01712-8 — `10.1038/s41562-023-01712-8`
- VAL-000282 (`10.1145/3173574.3174066 | 10.1145/3334480.3381435`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3173574.3174066 — `10.1145/3173574.3174066`
- REPL-001674 (`10.1111/jsbm.12240 | 10.1016/j.jbvi.2022.e00342`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1111/jsbm.12240 — `10.1111/jsbm.12240`
- REPL-001675 (`10.1016/j.jbusvent.2011.09.002 | 10.1016/j.jbvi.2020.e00158`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1016/j.jbusvent.2011.09.002 — `10.1016/j.jbusvent.2011.09.002`
- REPRO-000375 (`10.5465/ambpp.1987.17533767 | 10.1177/104225879301700303`): Publication-format review. OpenAlex type: conference-abstract. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.5465/ambpp.1987.17533767 — `10.5465/ambpp.1987.17533767`
- VAL-000417 (`10.1109/icse.2017.24 | 10.1109/icpc.2019.00050`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1109/icse.2017.24 — `10.1109/icse.2017.24`
- VAL-000450 (`10.21437/interspeech.2005-275 | 10.31274/psllt.17573`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.21437/interspeech.2005-275 — `10.21437/interspeech.2005-275`
- VAL-000455 (`10.1161/circresaha.117.310628 | 10.7554/elife.91498`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1161/circresaha.117.310628 — `10.1161/circresaha.117.310628`
- VAL-000459 (`10.1038/ng.2397 | 10.1038/s41598-017-07404-4`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.2397 — `10.1038/ng.2397`
- VAL-000545 (`10.1016/j.ejpoleco.2003.10.001 | 10.1177/1091142114568659`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1016/j.ejpoleco.2003.10.001 — `10.1016/j.ejpoleco.2003.10.001`
- VAL-000620 (`10.1163/9789004270848_008 | 10.5117/tvt2017.1.jans`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1163/9789004270848_008 — `10.1163/9789004270848_008`
- VAL-000633 (`10.1038/ng.175 | 10.1097/mib.0b013e3182a3eaeb`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.175 — `10.1038/ng.175`

</details>

<details><summary><b>Could not check</b> (2 original work types, 6 links)</summary>

_Not findings: no answer could be had for these 8, and they are checked again on the next run._

**Original work types (advisory)** (2):

- REPL-000806, doi_o: Original work type unavailable or stale; previous review retained — `10.1026/0949-3946.48.2.94`
- REPL-000025, doi_o: Original work type unavailable or stale; previous review retained — `10.6129/cjp.2003.4501.07`

**Links** (6):

- REPL-001176, oa_url_r: timed out — `https://cdr.lib.unc.edu/downloads/7w62fg56x`
- REPL-001436, oa_url_o: timed out — `http://hdl.handle.net/2328/35450`
- VAL-000159, oa_url_r: TLS: unable to get local issuer certificate — `https://polipapers.upv.es/index.php/MUSE/article/download/10390/11334`
- REPL-001417, url_r: redirect loop — `https://www.proquest.com/docview/221001969?pq-origsite=gscholar&fromopenview=true&sourcetype=Scholarly%20Journals`
- REPL-001618, url_o: redirect loop — `https://www.proquest.com/docview/1470800730`
- FRED-000798, oa_url_o: ConnectionResetError: \[Errno 104\] Connection reset by peer — `http://ira.lib.polyu.edu.hk/bitstream/10397/97105/1/Huang_Influence_Product_Anthropomorphism.pdf`

</details>


**Network validation status: needs review**

- Original work types (advisory): 2,685 distinct; 2,683 cached, 2,685 checked (2 could not be checked), 0 deferred.
- DOIs: 5,044 distinct; 5,009 cached, 35 checked, 0 deferred.
- Links: 3,537 distinct; 3,506 cached, 31 checked (6 could not be checked), 0 deferred.
- Originals without a DOI: 31 distinct; 29 cached, 2 checked, 0 deferred.


## Release files

- flora.csv: 3,004 rows, SHA-256 `a08fda16ca02c0ea7502c42be71e6f4df623a28757558c5210a001fc15ee1ffb`.
- Also written: the release manifest and notes, and today's counts in flora_history.csv and flora_dataset_summary.md.
- Publication: The repository copy (`output/` on GitHub) is committed by two workflows, each when flora.csv changed: the nightly job, with its own run's files, and Publish FLoRA, with the stored dataset. Website and local command-line runs commit nothing. Nothing is uploaded to OSF.
