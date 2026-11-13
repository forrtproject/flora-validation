# FLoRA preparation report

Generated: 2026-11-13T03:18:48+00:00

Status: **needs review**

Mode: built from the database

Rows: 2,881

## Stages

- build flora.csv and check IDs: passed
- structural validation: needs review — 45 problems and 10 possible problems
- network validation: needs review
- release files and history: passed
- store dataset: passed — 2,881 rows in flora_data
- write output files: passed

## Warnings

- 39 records left out for a missing title; see flora_export_log.csv.
- 62 merged records whose duplicates gave different outcomes; see dup_outcome_conflicts.csv.
- 21 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates); see preprint_dedup_candidates.csv.
- Structural validation found 45 problems and 10 possible problems.
- Network validation needs review: 39 problems and 69 possible problems. Original work types unavailable for 180 DOI(s); previous review decisions retained. This screening is advisory..

## Stored dataset

- 2,881 rows stored in `flora_data`, which Download full CSV and the public API serve.

## Diagnostic files

- flora_export_log.csv: 39 records left out for a missing title.
- dup_outcome_conflicts.csv: 62 merged records whose duplicates gave different outcomes.
- preprint_dedup_candidates.csv: 21 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates).

The JSON report lists the affected records.

## FLoRA structural validation

_Generated: 2026-11-13 03:29:06 UTC | Dataset: 2,881 rows_

Each item names the FLoRA record by its FLoRA ID and `doi_o | doi_r` (or `url_r`), says what is wrong and, after the dash, gives the value.

### Problems (45)

**Invalid URLs** (7 findings):

_A url column holds something other than one complete http(s) link._

- REPL-001235 (`10.1006/jesp.2001.1488 | 10.71889/5fylantbak.29863007`): oa_url_r contains spaces (an open-access link from enrichment, not from the source record) — `http://libres.uncg.edu/ir/asu/f/Eshenbaugh_Brooklyn Spring 2018 Thesis.pdf`
- REPL-001621 (`10.1016/j.jml.2020.104174 | 10.31234/osf.io/kt3sw_v2`): url_r holds a DOI, not a link — `10.1037/xlm0001650`
- FRED-000419 (`10.1037/0278-7393.34.2.408 | 10.1126/science.aac4716`): oa_url_o contains spaces (an open-access link from enrichment, not from the source record) — `http://libres.uncg.edu/ir/uncg/f/P_Delaney_Intentional_2008(MULTI UNCG AUTHORS).pdf`
- FRED-000048 (`10.1177/0956797616674999 | 10.1098/rsos.231240`): url_r holds two links — `https://osf.io/v6zk8 or http://rpubs.com/catherinecthomas/final_replication`
- VAL-000044 (`10.2307/749455 | 10.5964/jnc.6063`): url_r holds a DOI, not a link — `10.31234/osf.io/wej8z`
- VAL-000258 (`10.2224/sbp.2008.36.6.753 | 10.5114/cipp.2021.110948`): oa_url_r contains spaces (an open-access link from enrichment, not from the source record) — `https://www.termedia.pl/Journal/-75/pdf-45660-10?filename=Williams Conceptual.pdf`
- VAL-000274 (`10.1111/j.1467-9280.2008.02072.x | 10.17605/osf.io/yptua`): url_r is cut off — `https://journals.sagepub.com/doi/full/10.1177/2515…`

**Duplicate records** (1 finding):

_Rows the build would have made one record: the same type, doi_o, doi_r and url_r, or the same DOIs with one url_r between them._

- REPRO-000009, REPRO-000019 (`10.1145/2382448.2382450 | 10.17879/replicationresearch-2026-9414`): 2 rows the build would make one record — `reproduction|10.1145/2382448.2382450|10.17879/replicationresearch-2026-9414|`

<details><summary><b>Missing required fields</b> (23 findings)</summary>

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
- REPL-001248 (`| 10.3390/jintelligence7010006`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- FRED-000330 (`| 10.1007/s13164-018-0400-9`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000167 (`| 10.1177/0098628311421328`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000172 (`| 10.1177/0022466912449652`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000338 (`| 10.18653/v1/w19-8649`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000353 (`| 10.1111/j.1949-8594.2012.00166.x`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000357 (`| 10.1177/1029864913489703`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000622 (`| https://blogs.lse.ac.uk/impactofsocialsciences/2013/09/10/bias-at-the-bbc-replicating-the-cps-analysis/`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-001403 (`| 10.17885/heiup.1157.c19369`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-001425 (`| 10.1142/s1084946721500084`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-001641 (`| https://hdl.handle.net/20.500.14721/40318`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- VAL-000394 (`| 10.5406/bulcouresmusedu.193.0045`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o

</details>

**Invalid DOI format** (8 findings):

_doi_o or doi_r is not a bare DOI (10.NNNN/…), or a DOI listed in alt_identifier_o or alt_identifier_r is malformed._

- REPL-001062 (`10.1111/j.1468-0297.2009.02267.x | 10.1108/s0193-230620150000018002`): alt_identifier_r lists a DOI that carries query or tracking text (?…) — `10.1108/s0193-230620150000018002?utm_campaign=repec&wt.mc_id=repec`
- FRED-000059 (`djaerv_2018 | 10.1098/rsos.231240`): doi_o is not a DOI — `djaerv_2018`
- FRED-000052 (`escholarship.orgucitem39d347bk | 10.1098/rsos.231240`): doi_o is not a DOI — `escholarship.orgucitem39d347bk`
- VAL-000005 (`10.1016/j.im.2015.06.009 | https://aisel.aisnet.org/trr/vol7/iss1/3`): doi_r is a link, not a DOI — `https://aisel.aisnet.org/trr/vol7/iss1/3`
- VAL-000210 (`https://www.jstor.org/stable/41387554 | 10.1037/0022-0663.83.4.444`): doi_o is a link, not a DOI — `https://www.jstor.org/stable/41387554`
- FRED-000886 (`scholars.indianastate.edu/etds/2136 | https://digitalcommons.memphis.edu/etd/3538/`): doi_o is a link, not a DOI — `scholars.indianastate.edu/etds/2136`
- FRED-000520 (`thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025`): doi_o is a link, not a DOI — `thekeep.eiu.edu/theses/4783`
- FRED-000997 (`10.1007/s10683-011-9295-3 | 10.17605/osf.io/z9emg`): alt_identifier_r lists a DOI that is malformed — `10.17605/osf.io/w3kuz; 10.1038/s41586-025-10078-y`

**Title says retracted** (6 findings):

_title_o or title_r marks the paper retracted or withdrawn._

- REPL-000405 (`10.1126/science.abh4340 | 10.1126/sciadv.adl0999`): title_o marks the paper retracted or withdrawn — `RETRACTED: In vivo direct imaging of neuronal activity at high temporospatial resolution`
- REPL-000626 (`10.1016/j.cell.2013.04.008 | 10.2337/db13-1435`): title_o marks the paper retracted or withdrawn — `RETRACTED: Betatrophin: A Hormone that Controls Pancreatic β Cell Proliferation`
- REPL-001349 (`10.1073/pnas.1209746109 | 10.1073/pnas.1911695117`): title_o marks the paper retracted or withdrawn — `RETRACTED: Signing at the beginning makes ethics salient and decreases dishonest self-reports in comparison to signing …`
- REPRO-000331 (`10.1257/aer.20210369 | 10.1257/aer.20221432`): title_o marks the paper retracted or withdrawn — `RETRACTED BY THE AUTHORS: Dividend Taxes and the Allocation of Capital`
- FRED-000891 (`10.1086/661890 | 10.15626/mp.2020.2538`): title_o marks the paper retracted or withdrawn — `Retracted: Super Size Me: Product Size as a Signal of Status`
- FRED-000420 (`10.1177/1948550611410890 | 10.1037/xge0000067`): title_o marks the paper retracted or withdrawn — `RETRACTED: Sense Creative! The Impact of Global and Local Vision, Hearing, Touching, Tasting and Smelling on Creative a…`

### Possible problems (10)

_Heuristics: a person decides whether each one is a problem._

**Replication year before original year** (3 findings):

_Could be swapped entries or a data-entry error. Not reported when the original has a preprint or other earlier version._

- REPL-000014 (`10.1080/13639080.2011.573776 | 10.1007/s12186-012-9090-0`): year_r is before year_o (2013) — `2012`
- REPL-000476 (`10.1002/bin.1836 | 10.1002/jaba.659`): year_r is before year_o (2021) — `2019`
- REPL-001689 (`10.1177/1042258719892987 | 10.1111/apps.12247`): year_r is before year_o (2021) — `2020`

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

**Title looks like a file or project name** (1 finding):

_title_r is a file or project name, or a generic name such as 'Analysis'._

- REPL-000453 (`10.1037/a0023937 | https://osf.io/up8xs`): title_r is a generic name, not a title — `Analysis`

---

**Checks passed:**

- Outcome values valid
- Text fields hold text
- Type values valid
- Source values valid
- Year ranges reasonable
- DOI mapped to conflicting references
- No 'NA'/'N/A' strings


## FLoRA network validation

_Generated: 2026-11-13 03:29:06 UTC | Dataset: 2,881 rows_

**Original work types unavailable for 180 DOI(s); previous review decisions retained. This screening is advisory..**

Each item names the FLoRA records carrying the DOI or link by FLoRA ID, says what is wrong and, after the dash, gives the DOI or link.

### Problems (39)

**Retracted papers** (7 findings):

- REPL-000405 (`10.1126/science.abh4340 | 10.1126/sciadv.adl0999`): doi_o is retracted (Retraction Watch) — `10.1126/science.abh4340`
- REPL-000473 (`10.1001/jamapsychiatry.2014.763 | 10.1037/pas0000664`): doi_o is retracted (Retraction Watch) — `10.1001/jamapsychiatry.2014.763`
- REPL-000626 (`10.1016/j.cell.2013.04.008 | 10.2337/db13-1435`): doi_o is retracted (Retraction Watch) — `10.1016/j.cell.2013.04.008`
- REPL-001349 (`10.1073/pnas.1209746109 | 10.1073/pnas.1911695117`): doi_o is retracted (Retraction Watch) — `10.1073/pnas.1209746109`
- REPRO-000331 (`10.1257/aer.20210369 | 10.1257/aer.20221432`): doi_o is retracted (Retraction Watch) — `10.1257/aer.20210369`
- FRED-000891 (`10.1086/661890 | 10.15626/mp.2020.2538`): doi_o is retracted (Retraction Watch) — `10.1086/661890`
- FRED-000420 (`10.1177/1948550611410890 | 10.1037/xge0000067`): doi_o is retracted (Retraction Watch) — `10.1177/1948550611410890`

**DOIs not registered at doi.org** (9 findings):

- REPL-001616 (`10.1111/1467-9280.00441 | 10.1177/0956797626146077`): doi_r is not registered at doi.org — `10.1177/0956797626146077`
- REPRO-000003 (`10.1093/qje/qjac034 | 10.10419/295247`): doi_r is not registered at doi.org — `10.10419/295247`
- FRED-000585 (`10.1177/2f10986111211035002 | 10.1080/15614263.2022.2102494`): doi_o is not registered at doi.org — `10.1177/2f10986111211035002`
- VAL-000107 (`10.25300/misq/2018/13853 | 10.17705/1atrr.00067`): doi_r is not registered at doi.org — `10.17705/1atrr.00067`
- VAL-000125 (`10.1287/isre.1040.0032 | 10.17705/1atrr.00046`): doi_r is not registered at doi.org — `10.17705/1atrr.00046`
- VAL-000138 (`10.5465/amj.2010.51468978 | 10.17705/1atrr.00066`): doi_r is not registered at doi.org — `10.17705/1atrr.00066`
- VAL-000185 (`10.1080/07421222.2017.1394083 | 10.17705/1atrr.00053`): doi_r is not registered at doi.org — `10.17705/1atrr.00053`
- VAL-000215 (`10.2307/249754 | 10.17705/1atrr.00064`): doi_r is not registered at doi.org — `10.17705/1atrr.00064`
- VAL-000427 (`10.1287/isre.1040.0032 | 10.17705/1atrr.00061`): doi_r is not registered at doi.org — `10.17705/1atrr.00061`

<details><summary><b>Dead links</b> (23 findings)</summary>

- REPL-000068 (`10.1177/0146167205279905 | 10.1080/02619768.2014.882308`): oa_url_r is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `http://hdl.handle.net/1820/9055`
- REPL-000073 (`10.3102/0002831211419649 | 10.1037/a0036307`): oa_url_r is dead (connection refused; an open-access link from enrichment, not from the source record) — `http://orbilu.uni.lu/handle/10993/18672`
- REPL-000553 (`10.1016/j.obhdp.2006.10.001 | 10.1525/collabra.216`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `http://www.collabra.org/articles/10.1525/collabra.216/galley/263/download/`
- REPL-000866 (`10.1126/science.1239918 | 10.1525/collabra.117`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `http://www.collabra.org/articles/10.1525/collabra.117/galley/146/download/`
- REPL-001061 (`10.2753/mis0742-1222240209 | 10.17705/1atrr.00004`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `http://elibrary.aisnet.org/Default.aspx?url=https://aisel.aisnet.org/cgi/viewcontent.cgi?article=1006&context=trr`
- REPL-001076 (`10.3389/fpsyg.2016.00027 | 10.1371/journal.pone.0198620`): oa_url_r is dead (HTTP 400; an open-access link from enrichment, not from the source record) — `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0198620/1/pone.0198620.pdf?X-Goog-Algorithm=GOOG4-…`
- FRED-000983 (`10.1111/1467-9280.00376 | 10.1177/0956797620968526`): oa_url_r is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `https://biblio.ugent.be/publication/8707609`
- VAL-000239 (`10.1177/0956797620968526 | 10.5334/irsp.546`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `https://biblio.ugent.be/publication/8707609`
- REPL-001099 (`10.1037/edu0000115 | 10.1037/edu0000433`): oa_url_r is dead (connection refused; an open-access link from enrichment, not from the source record) — `http://orbilu.uni.lu/handle/10993/45413`
- REPL-001320 (`10.1016/s0022-1031(03)00057-x | 10.1371/journal.pone.0209263`): oa_url_r is dead (HTTP 400; an open-access link from enrichment, not from the source record) — `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0209263/1/pone.0209263.pdf?X-Goog-Algorithm=GOOG4-…`
- REPL-001624 (`10.1348/014466607x238751 | 10.1002/ejsp.70015`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `http://eprints.keele.ac.uk/2959/1/M%20Noor%20-%20Prescursors%20and%20mediators%20of%20Intergroup%20Reconciliation%20in%…`
- FRED-000270, VAL-000311: oa_url_o is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `https://autopapers.ssrn.com/sol3/papers.cfm?abstract_id=1324778`
- FRED-000012 (`10.1037/0022-3514.86.5.680 | 10.1016/j.jesp.2020.104052`): oa_url_o is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `https://autopapers.ssrn.com/sol3/papers.cfm?abstract_id=929932`
- FRED-000643 (`10.1037/0278-7393.34.1.97 | 10.1126/science.aac4716`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `http://hdl.handle.net/10026.1/3213`
- FRED-000469 (`10.1177/0146167210386238 | 10.1525/collabra.192`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `http://www.collabra.org/articles/10.1525/collabra.192/galley/251/download/`
- FRED-000772 (`10.1177/0956797611434748 | 10.1016/j.cognition.2014.09.003`): oa_url_r is dead (connection refused; an open-access link from enrichment, not from the source record) — `http://orbilu.uni.lu/handle/10993/17967`
- VAL-000031 (`10.1016/0010-0285(89)90003-0 | 10.7916/d8f76r2r`): url_r is dead (DNS: name not found) — `https://tesol-dev.journals.cdrs.columbia.edu/wp-content/uploads/sites/12/2015/06/3.-Seol-2005.pdf`
- VAL-000031 (`10.1016/0010-0285(89)90003-0 | 10.7916/d8f76r2r`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `https://tesol-dev.journals.cdrs.columbia.edu/wp-content/uploads/sites/12/2015/06/3.-Seol-2005.pdf`
- VAL-000077 (`10.1016/j.cognition.2006.12.015 | 10.1525/collabra.87`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `http://www.collabra.org/articles/10.1525/collabra.87/galley/208/download/`
- VAL-000196 (`10.1371/journal.pone.0197268 | 10.1371/journal.pone.0253023`): oa_url_o is dead (HTTP 400; an open-access link from enrichment, not from the source record) — `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0197268/1/pone.0197268.pdf?X-Goog-Algorithm=GOOG4-…`
- REPL-001327 (`10.1006/jmla.1996.0032 | https://openalex.org/W7110491536`): url_r is dead (HTTP 404) — `https://openalex.org/W7110491536`
- FRED-000267 (`10.1037/0278-6133.3.5.431 | https://docs.google.com/document/d/10OKLI0-Kw2D7SDyLU_Ieql88gLtbnYGSyZhCBqsEAyk`): url_r is dead (HTTP 410) — `https://docs.google.com/document/d/10OKLI0-Kw2D7SDyLU_Ieql88gLtbnYGSyZhCBqsEAyk`
- VAL-000315 (`10.1111/j.1540-4781.1996.tb05468.x | 10.32601/ejal.460588`): url_r is dead (HTTP 404) — `http://dergipark.gov.tr/download/article-file/536686`

</details>

### Possible problems (69)

_Heuristics: a person decides whether each one is a problem._

**Expressions of concern** (3 findings):

- FRED-000510 (`10.1111/j.0956-7976.2004.00757.x | 10.1525/collabra.32572`): doi_o has an expression of concern (Retraction Watch) — `10.1111/j.0956-7976.2004.00757.x`
- FRED-000906 (`10.1509/jmkr.45.6.633 | 10.1177/2515245918781032`): doi_o has an expression of concern (Retraction Watch) — `10.1509/jmkr.45.6.633`
- FRED-000187 (`10.1177/0956797611432497 | 10.1027/1864-9335/a000191`): doi_o has an expression of concern (Retraction Watch) — `10.1177/0956797611432497`

**Original probably has a DOI** (5 findings):

- REPL-001248 (`| 10.3390/jintelligence7010006`): doi_o is blank; Crossref lists this paper with a DOI: 'The Mismeasure of Man' (2012, Gould) — `10.4324/9780203718087-5`
- REPL-000409 (`| 10.1111/jpr.12599`): doi_o is blank; Crossref lists this paper with a DOI: 'Assimilation and Contrast Effects of Culture Priming Among Hong Kong Chinese' (2016, Ng) — `10.1177/0022022116631826`
- REPL-000412 (`| 10.36948/ijfmr.2025.v07i06.61995`): doi_o is blank; Crossref lists this paper with a DOI: 'Retro-cue benefits in working memory without sustained focal attention' (2014, Rerko) — `10.3758/s13421-013-0392-8`
- REPL-001425 (`| 10.1142/s1084946721500084`): doi_o is blank; Crossref lists this paper with a DOI: 'Poverty and Entrepreneurship in Developing Countries' (2011, Ernesto Amorós) — `10.1093/acprof:oso/9780199580866.003.0010`
- REPL-001445 (`| 10.1525/collabra.163080`): doi_o is blank; Crossref lists this paper with a DOI: 'The dark side of self- and social perception: Black uniforms and aggression in …' (1988, Frank) — `10.1037/0022-3514.54.1.74`

**Original target needs review (higher priority):** none

<details><summary><b>Original publication format needs review</b> (61 findings)</summary>

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
- REPL-000977 (`10.1109/vis54172.2023.00016 | 10.31219/osf.io/v6sfg_v1`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1109/vis54172.2023.00016 — `10.1109/vis54172.2023.00016`
- REPL-001019 (`10.18653/v1/2020.findings-emnlp.139 | 10.1109/iwsc55060.2022.00015`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.18653/v1/2020.findings-emnlp.139 — `10.18653/v1/2020.findings-emnlp.139`
- REPL-001038 (`10.2307/3512141 | 10.1111/j.1468-5906.2009.01499.x`): Publication-format review. OpenAlex type: book-review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.2307/3512141 — `10.2307/3512141`
- REPL-001071 (`10.4159/9780674369542 | 10.1017/ssh.2017.27`): Publication-format review. OpenAlex type: book. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.4159/9780674369542 — `10.4159/9780674369542`
- REPL-001117 (`10.1145/1404520.1404531 | 10.1080/08993408.2022.2079866`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/1404520.1404531 — `10.1145/1404520.1404531`
- REPL-001181 (`10.1093/acprof:osobl/9780199927418.003.0013 | 10.1080/09515089.2013.878834`): Publication-format review. OpenAlex type: book-chapter. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1093/acprof:osobl/9780199927418.003.0013 — `10.1093/acprof:osobl/9780199927418.003.0013`
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
- REPRO-000163 (`10.1038/s41562-023-01712-8 | https://osf.io/9y47b/`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/s41562-023-01712-8 — `10.1038/s41562-023-01712-8`
- VAL-000282 (`10.1145/3173574.3174066 | 10.1145/3334480.3381435`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1145/3173574.3174066 — `10.1145/3173574.3174066`
- REPL-001674 (`10.1111/jsbm.12240 | 10.1016/j.jbvi.2022.e00342`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1111/jsbm.12240 — `10.1111/jsbm.12240`
- REPL-001675 (`10.1016/j.jbusvent.2011.09.002 | 10.1016/j.jbvi.2020.e00158`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1016/j.jbusvent.2011.09.002 — `10.1016/j.jbusvent.2011.09.002`
- REPRO-000375 (`10.5465/ambpp.1987.17533767 | 10.1177/104225879301700303`): Publication-format review. OpenAlex type: conference-abstract. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.5465/ambpp.1987.17533767 — `10.5465/ambpp.1987.17533767`
- VAL-000417 (`10.1109/icse.2017.24 | 10.1109/icpc.2019.00050`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1109/icse.2017.24 — `10.1109/icse.2017.24`
- VAL-000450 (`10.21437/interspeech.2005-275 | 10.31274/psllt.17573`): Publication-format review. OpenAlex type: conference-paper. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.21437/interspeech.2005-275 — `10.21437/interspeech.2005-275`
- VAL-000455 (`10.1161/circresaha.117.310628 | 10.7554/elife.91498`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1161/circresaha.117.310628 — `10.1161/circresaha.117.310628`
- VAL-000459 (`10.1038/ng.2397 | 10.1038/s41598-017-07404-4`): Publication-format review. OpenAlex type: review. Check that this original is the study the replication tests. If valid, mark Not a problem with a reason; otherwise correct the identifier or review an extraneous original row. This warning changes no record. https://openalex.org/works/doi:10.1038/ng.2397 — `10.1038/ng.2397`

</details>

<details><summary><b>Could not check</b> (180 original work types, 9 links)</summary>

_Not findings: no answer could be had for these 189, and they are checked again on the next run._

**Original work types (advisory)** (180):

- FRED-000547, FRED-000912, doi_o: Original work type unavailable or stale; previous review retained — `10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y`
- REPL-000806, doi_o: Original work type unavailable or stale; previous review retained — `10.1026/0949-3946.48.2.94`
- FRED-000585, doi_o: Original work type unavailable or stale; previous review retained — `10.1177/2f10986111211035002`
- REPL-001126, doi_o: Original work type unavailable or stale; previous review retained — `10.2196/20572`
- REPL-000161, doi_o: Original work type unavailable or stale; previous review retained — `10.2196/26134`
- REPL-001009, doi_o: Original work type unavailable or stale; previous review retained — `10.2196/37954`
- FRED-000635, doi_o: Original work type unavailable or stale; previous review retained — `10.2196/jmir.2551`
- REPL-001457, doi_o: Original work type unavailable or stale; previous review retained — `10.22004/ag.econ.139020`
- VAL-000258, doi_o: Original work type unavailable or stale; previous review retained — `10.2224/sbp.2008.36.6.753`
- REPL-001653, doi_o: Original work type unavailable or stale; previous review retained — `10.2224/sbp.2013.41.2.283`
- REPL-000166, REPL-001208, VAL-000254, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/1128199`
- REPL-001075, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/1386523`
- VAL-000213, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/1388548`
- REPRO-000348, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/1392187`
- REPL-000499, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/1412188`
- REPL-001250, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/145795`
- REPL-000347, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/1961661`
- REPL-000629, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/20159604`
- VAL-000076, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/20466649`
- VAL-000147, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2061139`
- REPL-000424, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2082710`
- REPL-000226, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2091819`
- VAL-000420, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2092399`
- REPL-001588, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2118508`
- VAL-000091, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2392469`
- REPL-000711, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2393473`
- VAL-000131, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/249688`
- VAL-000215, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/249754`
- REPL-001364, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/255897`
- REPL-000986, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/256894`
- REPL-001419, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/257100`
- REPL-000049, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2585837`
- REPRO-000006, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2586119`
- REPL-001249, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/2786238`
- REPL-000715, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/3250921`
- REPL-001402, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/3333827`
- REPL-000822, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/3586735`
- VAL-000214, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/41703463`
- VAL-000044, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/749455`
- REPL-000552, doi_o: Original work type unavailable or stale; previous review retained — `10.2307/976307`
- REPL-000465, doi_o: Original work type unavailable or stale; previous review retained — `10.2308/acch-50181`
- REPL-000723, doi_o: Original work type unavailable or stale; previous review retained — `10.2308/accr-51448`
- REPL-000787, doi_o: Original work type unavailable or stale; previous review retained — `10.2337/diabetes.53.9.2467`
- FRED-000009, doi_o: Original work type unavailable or stale; previous review retained — `10.2466/pms.109.2.577-580`
- REPL-000783, doi_o: Original work type unavailable or stale; previous review retained — `10.2466/pms.1999.89.1.151`
- VAL-000295, doi_o: Original work type unavailable or stale; previous review retained — `10.2466/pr0.1972.30.2.636`
- VAL-000411, doi_o: Original work type unavailable or stale; previous review retained — `10.2466/pr0.1972.31.3.699`
- VAL-000356, doi_o: Original work type unavailable or stale; previous review retained — `10.2466/pr0.1994.75.1.199`
- VAL-000222, doi_o: Original work type unavailable or stale; previous review retained — `10.2466/pr0.1997.80.2.511`
- REPL-000782, doi_o: Original work type unavailable or stale; previous review retained — `10.2466/pr0.1998.83.2.455`
- REPL-000901, doi_o: Original work type unavailable or stale; previous review retained — `10.2478/v10036-009-0039-9`
- FRED-000193, doi_o: Original work type unavailable or stale; previous review retained — `10.2501/jar-40-1-2-29-34`
- VAL-000198, doi_o: Original work type unavailable or stale; previous review retained — `10.25300/misq/2013/37.4.01`
- VAL-000099, doi_o: Original work type unavailable or stale; previous review retained — `10.25300/misq/2014/38.1.14`
- VAL-000107, doi_o: Original work type unavailable or stale; previous review retained — `10.25300/misq/2018/13853`
- REPL-001198, doi_o: Original work type unavailable or stale; previous review retained — `10.25300/misq/2020/14110`
- REPL-000062, doi_o: Original work type unavailable or stale; previous review retained — `10.25656/01:31701`
- REPL-000506, doi_o: Original work type unavailable or stale; previous review retained — `10.2753/jec1086-4415130103`
- REPL-001225, doi_o: Original work type unavailable or stale; previous review retained — `10.2753/joa0091-3367370303`
- REPL-001061, doi_o: Original work type unavailable or stale; previous review retained — `10.2753/mis0742-1222240209`
- REPL-000746, doi_o: Original work type unavailable or stale; previous review retained — `10.2753/mis0742-1222310210`
- REPL-001125, doi_o: Original work type unavailable or stale; previous review retained — `10.29115/sp-2022-0009`
- SCORE-000016, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831208323278`
- REPL-000602, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831209340043`
- SCORE-000035, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831209350493`
- REPL-000906, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831210362589`
- SCORE-000005, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831210374874`
- SCORE-000017, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831211419490`
- REPL-000073, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831211419649`
- REPL-000513, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0002831214531323`
- REPL-000518, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0013189x11422112`
- FRED-000877, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0013189x13507104`
- REPL-000960, doi_o: Original work type unavailable or stale; previous review retained — `10.3102/0162373720954183`
- REPL-001174, doi_o: Original work type unavailable or stale; previous review retained — `10.3109/0142159x.2010.541533`
- REPL-000071, doi_o: Original work type unavailable or stale; previous review retained — `10.3109/0142159x.2012.714884`
- REPL-000086, doi_o: Original work type unavailable or stale; previous review retained — `10.3109/10253890.2015.1040987`
- REPL-000425, doi_o: Original work type unavailable or stale; previous review retained — `10.3109/13561820.2014.917407`
- REPL-000928, doi_o: Original work type unavailable or stale; previous review retained — `10.3109/13816810.2015.1005317`
- REPRO-000130, doi_o: Original work type unavailable or stale; previous review retained — `10.31234/osf.io/6a2cx`
- REPL-000422, doi_o: Original work type unavailable or stale; previous review retained — `10.31234/osf.io/7tm3s`
- REPRO-000147, doi_o: Original work type unavailable or stale; previous review retained — `10.31234/osf.io/e3kcw`
- REPRO-000124, doi_o: Original work type unavailable or stale; previous review retained — `10.31234/osf.io/jkfu3`
- REPRO-000129, doi_o: Original work type unavailable or stale; previous review retained — `10.31234/osf.io/rz78k`
- REPRO-000142, doi_o: Original work type unavailable or stale; previous review retained — `10.31234/osf.io/yb2h8`
- REPL-000482, doi_o: Original work type unavailable or stale; previous review retained — `10.31235/osf.io/3ye4b`
- REPL-000038, doi_o: Original work type unavailable or stale; previous review retained — `10.3138/cmlr.3831`
- REPL-000095, doi_o: Original work type unavailable or stale; previous review retained — `10.3233/jad-160881`
- FRED-000578, doi_o: Original work type unavailable or stale; previous review retained — `10.3233/jad-2009-1163`
- VAL-000030, doi_o: Original work type unavailable or stale; previous review retained — `10.3233/jsa-160165`
- REPL-001017, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fnagi.2022.804937`
- REPL-000341, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fnhum.2013.00257`
- REPL-000126, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fnhum.2017.00468`
- FRED-000027, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fped.2019.00206`
- REPL-000421, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2014.00444`
- REPL-000469, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2014.01202`
- FRED-000003, FRED-000628, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2014.01276`
- REPL-000079, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2014.01518`
- VAL-000404, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2015.01216`
- REPL-001076, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2016.00027`
- REPL-000909, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2017.00051`
- VAL-000256, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2017.00790`
- VAL-000085, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2017.01508`
- REPL-000963, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2020.01415`
- FRED-000699, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyg.2024.1335892`
- REPL-000693, REPL-001308, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyt.2012.00045`
- REPL-000392, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyt.2014.00019`
- REPL-000972, doi_o: Original work type unavailable or stale; previous review retained — `10.3389/fpsyt.2024.1432679`
- REPL-000657, doi_o: Original work type unavailable or stale; previous review retained — `10.3390/biomedicines8020034`
- REPL-000876, doi_o: Original work type unavailable or stale; previous review retained — `10.3390/brainsci8040062`
- REPL-001112, doi_o: Original work type unavailable or stale; previous review retained — `10.3390/cli7020028`
- REPL-000287, doi_o: Original work type unavailable or stale; previous review retained — `10.37016/mr-2020-47`
- REPL-000697, doi_o: Original work type unavailable or stale; previous review retained — `10.37237/relay/010204`
- FRED-000607, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/app.71.1.9`
- REPL-001657, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03193111`
- FRED-000526, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03193824`
- FRED-000401, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03194043`
- REPL-000619, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03196313`
- REPL-001439, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03196335`
- VAL-000498, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03196610`
- FRED-000275, FRED-000560, REPL-000290, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03197186`
- VAL-000137, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03197446`
- REPL-001239, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03202642`
- REPL-000813, REPL-000824, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03206733`
- FRED-000293, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03211160`
- REPL-000411, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/bf03214286`
- REPL-001189, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/mc.37.1.65`
- VAL-000264, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/pbr.16.1.139`
- REPL-001399, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/pbr.17.5.644`
- VAL-000395, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/pp.70.1.88`
- REPL-000383, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/pp.70.3.477`
- VAL-000370, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13414-012-0325-9`
- VAL-000204, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13414-018-1498-7`
- FRED-000246, FRED-000872, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13414-019-01723-6`
- VAL-000243, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13414-019-01946-7`
- FRED-000709, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13414-020-02150-8`
- REPL-000618, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13420-016-0250-1`
- REPL-000366, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13423-013-0572-3`
- FRED-000005, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13423-014-0663-9`
- REPL-000291, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13423-014-0683-5`
- REPL-001214, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13423-015-0923-3`
- REPL-000768, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13423-017-1306-8`
- VAL-000117, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13428-020-01440-1`
- FRED-000128, doi_o: Original work type unavailable or stale; previous review retained — `10.3758/s13428-021-01694-3`
- REPL-000949, doi_o: Original work type unavailable or stale; previous review retained — `10.3928/0048-5713-19951201-07`
- REPL-000502, doi_o: Original work type unavailable or stale; previous review retained — `10.3945/jn.112.159319`
- REPRO-000057, REPRO-000101, doi_o: Original work type unavailable or stale; previous review retained — `10.3982/ecta10813`
- REPRO-000040, doi_o: Original work type unavailable or stale; previous review retained — `10.3982/ecta11380`
- REPRO-000062, REPRO-000095, doi_o: Original work type unavailable or stale; previous review retained — `10.3982/ecta12987`
- REPL-000699, doi_o: Original work type unavailable or stale; previous review retained — `10.3982/ecta13408`
- REPL-000430, REPRO-000150, doi_o: Original work type unavailable or stale; previous review retained — `10.3982/ecta14370`
- REPRO-000341, doi_o: Original work type unavailable or stale; previous review retained — `10.3982/ecta6248`
- REPL-000456, REPRO-000143, doi_o: Original work type unavailable or stale; previous review retained — `10.3982/ecta8852`
- REPL-000973, doi_o: Original work type unavailable or stale; previous review retained — `10.4054/demres.2019.40.2`
- FRED-000769, doi_o: Original work type unavailable or stale; previous review retained — `10.4148/1944-9771.1299`
- REPL-001260, doi_o: Original work type unavailable or stale; previous review retained — `10.4161/viru.1.5.12486`
- REPL-000307, doi_o: Original work type unavailable or stale; previous review retained — `10.48550/arxiv.2302.02083`
- REPL-001026, doi_o: Original work type unavailable or stale; previous review retained — `10.4992/jjpsy.94.22334`
- FRED-000731, doi_o: Original work type unavailable or stale; previous review retained — `10.51548/joctec-2021-016`
- REPL-000238, doi_o: Original work type unavailable or stale; previous review retained — `10.5210/bsi.v18i1.2292`
- REPL-000818, doi_o: Original work type unavailable or stale; previous review retained — `10.5232/ricyde2010.01804`
- REPL-000987, doi_o: Original work type unavailable or stale; previous review retained — `10.5334/joc.91`
- REPL-001030, doi_o: Original work type unavailable or stale; previous review retained — `10.5465/amj.2005.18803920`
- VAL-000176, doi_o: Original work type unavailable or stale; previous review retained — `10.5465/amj.2010.0608`
- VAL-000138, doi_o: Original work type unavailable or stale; previous review retained — `10.5465/amj.2010.51468978`
- REPL-000549, doi_o: Original work type unavailable or stale; previous review retained — `10.5465/amj.2014.0424`
- REPRO-000118, SCORE-000050, doi_o: Original work type unavailable or stale; previous review retained — `10.5465/amj.2016.0086`
- REPL-001620, doi_o: Original work type unavailable or stale; previous review retained — `10.5465/amle.2010.0012`
- VAL-000434, doi_o: Original work type unavailable or stale; previous review retained — `10.5808/gi.2014.12.4.225`
- FRED-000311, doi_o: Original work type unavailable or stale; previous review retained — `10.5840/jphil20091061128`
- FRED-000303, doi_o: Original work type unavailable or stale; previous review retained — `10.5840/jphil20111081238`
- REPL-000628, doi_o: Original work type unavailable or stale; previous review retained — `10.5840/philtopics2001291/217`
- REPL-000097, doi_o: Original work type unavailable or stale; previous review retained — `10.58680/rte20076014`
- REPL-001637, doi_o: Original work type unavailable or stale; previous review retained — `10.5951/jresematheduc.29.5.0503`
- REPL-000025, doi_o: Original work type unavailable or stale; previous review retained — `10.6129/cjp.2003.4501.07`
- REPL-001226, doi_o: Original work type unavailable or stale; previous review retained — `10.64152/10125/66632`
- REPL-000286, doi_o: Original work type unavailable or stale; previous review retained — `10.64152/10125/66973`
- REPL-000706, doi_o: Original work type unavailable or stale; previous review retained — `10.7334/psicothema2016.24`
- VAL-000494, doi_o: Original work type unavailable or stale; previous review retained — `10.7554/elife.42256`
- VAL-000161, doi_o: Original work type unavailable or stale; previous review retained — `10.7554/elife.49700`
- REPL-000828, doi_o: Original work type unavailable or stale; previous review retained — `10.7820/vli.v02.1.ishii`

**Links** (9):

- REPL-001087, oa_url_r: timed out — `https://hal.science/hal-02440256/document`
- REPL-001176, oa_url_r: timed out — `https://cdr.lib.unc.edu/downloads/7w62fg56x`
- REPL-001436, oa_url_o: timed out — `http://hdl.handle.net/2328/35450`
- VAL-000159, oa_url_r: TLS: unable to get local issuer certificate — `https://polipapers.upv.es/index.php/MUSE/article/download/10390/11334`
- REPL-000190, oa_url_o: TLS: unable to get local issuer certificate — `http://hdl.handle.net/10902/7358`
- REPL-001417, url_r: redirect loop — `https://www.proquest.com/docview/221001969?pq-origsite=gscholar&fromopenview=true&sourcetype=Scholarly%20Journals`
- REPL-001618, url_o: redirect loop — `https://www.proquest.com/docview/1470800730`
- REPRO-000365, oa_url_o: timed out — `https://sciencespo.hal.science/hal-04211174/document`
- FRED-000798, oa_url_o: ConnectionResetError: \[Errno 104\] Connection reset by peer — `http://ira.lib.polyu.edu.hk/bitstream/10397/97105/1/Huang_Influence_Product_Anthropomorphism.pdf`

</details>


**Network validation status: needs review**

- Original work types (advisory): 2,575 distinct; 2,395 cached, 2,575 checked (180 could not be checked), 0 deferred.
- DOIs: 4,832 distinct; 4,590 cached, 242 checked, 0 deferred.
- Links: 3,400 distinct; 2,947 cached, 453 checked (9 could not be checked), 0 deferred.
- Originals without a DOI: 28 distinct; 28 cached, 0 checked, 0 deferred.


## Release files

- flora.csv: 2,881 rows, SHA-256 `0eb4bcb254481e1abe619b9cb05c9851fd6ee24465e2065a016b5e880e7c1e3a`.
- Also written: the release manifest and notes, and today's counts in flora_history.csv and flora_dataset_summary.md.
- Publication: The repository copy (`output/` on GitHub) is committed by two workflows, each when flora.csv changed: the nightly job, with its own run's files, and Publish FLoRA, with the stored dataset. Website and local command-line runs commit nothing. Nothing is uploaded to OSF.
