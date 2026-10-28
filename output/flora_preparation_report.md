# FLoRA preparation report

Generated: 2026-10-28T03:06:33+00:00

Status: **needs review**

Mode: built from the database

Rows: 2,847

## Stages

- build flora.csv and check IDs: passed
- structural validation: needs review — 44 problems and 25 possible problems
- network validation: needs review
- release files and history: passed
- store dataset: passed — 2,847 rows in flora_data
- write output files: passed

## Warnings

- 32 records left out for a missing title; see flora_export_log.csv.
- 59 merged records whose duplicates gave different outcomes; see dup_outcome_conflicts.csv.
- 25 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates); see preprint_dedup_candidates.csv.
- Structural validation found 44 problems and 25 possible problems.
- Network validation needs review: 40 problems and 8 possible problems.

## Stored dataset

- 2,847 rows stored in `flora_data`, which Download full CSV and the public API serve.

## Diagnostic files

- flora_export_log.csv: 32 records left out for a missing title.
- dup_outcome_conflicts.csv: 59 merged records whose duplicates gave different outcomes.
- preprint_dedup_candidates.csv: 25 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates).

The JSON report lists the affected records.

## FLoRA structural validation

_Generated: 2026-10-28 03:09:39 UTC | Dataset: 2,847 rows_

Each item names the FLoRA record by its FLoRA ID and `doi_o | doi_r` (or `url_r`), says what is wrong and, after the dash, gives the value.

### Problems (44)

**Invalid URLs** (7 findings):

_A url column holds something other than one complete http(s) link._

- REPL-001235 (`10.1006/jesp.2001.1488 | 10.71889/5fylantbak.29863007`): oa_url_r contains spaces (an open-access link from enrichment, not from the source record) — `http://libres.uncg.edu/ir/asu/f/Eshenbaugh_Brooklyn Spring 2018 Thesis.pdf`
- REPL-001621 (`10.1016/j.jml.2020.104174 | 10.31234/osf.io/kt3sw_v2`): url_r holds a DOI, not a link — `10.1037/xlm0001650`
- FRED-000419 (`10.1037/0278-7393.34.2.408 | 10.1126/science.aac4716`): oa_url_o contains spaces (an open-access link from enrichment, not from the source record) — `http://libres.uncg.edu/ir/uncg/f/P_Delaney_Intentional_2008(MULTI UNCG AUTHORS).pdf`
- FRED-000048 (`10.1177/0956797616674999 | 10.1098/rsos.231240`): url_r holds two links — `https://osf.io/v6zk8 or http://rpubs.com/catherinecthomas/final_replication`
- VAL-000044 (`10.2307/749455 | 10.5964/jnc.6063`): url_r holds a DOI, not a link — `10.31234/osf.io/wej8z`
- VAL-000258 (`10.2224/sbp.2008.36.6.753 | 10.5114/cipp.2021.110948`): oa_url_r contains spaces (an open-access link from enrichment, not from the source record) — `https://www.termedia.pl/Journal/-75/pdf-45660-10?filename=Williams Conceptual.pdf`
- VAL-000274 (`10.1111/j.1467-9280.2008.02072.x | 10.17605/osf.io/yptua`): url_r is cut off — `https://journals.sagepub.com/doi/full/10.1177/2515…`

**Invalid outcome values** (2 findings):

_An outcome outside the allowed set for the row's type, not_a_replication, or missing._

- REPL-000419 (`10.1002/j.2164-585x.1979.tb00097.x | 10.1353/csd.2014.0018`): outcome is blank
- SCORE-000055 (`10.1017/xps.2014.22 | https://osf.io/8uc5z/files/jmk9d`): outcome is blank

<details><summary><b>Missing required fields</b> (21 findings)</summary>

_title_o, title_r, doi_r or url_r, and an identifier for the original: doi_o, a url_o that is a link or DOI (not a title), alt_identifier_o or oa_work_id_o._

- REPL-000077 (`| 10.1016/j.lindif.2015.03.029`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000091 (`| 10.1177/2053168016652420`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000141 (`| 10.1177/0016986220932533`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000146 (`| 10.1002/aur.2496`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000234 (`| 10.18148/srm/2012.v6i1.5092`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000241 (`| 10.1103/physrevstper.10.010110`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000244 (`| 10.1080/22243534.2015.11828344`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000356 (`| 10.1177/0958928712471227`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000448 (`| 10.1371/journal.pone.0120644`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-000621 (`| 10.5539/ibr.v5n4p71`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
- REPL-001248 (`| 10.3390/jintelligence7010006`): the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o
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
- FRED-000330 (`art00005 | 10.1007/s13164-018-0400-9`): doi_o is not a DOI — `art00005`
- FRED-000059 (`djaerv_2018 | 10.1098/rsos.231240`): doi_o is not a DOI — `djaerv_2018`
- FRED-000052 (`escholarship.orgucitem39d347bk | 10.1098/rsos.231240`): doi_o is not a DOI — `escholarship.orgucitem39d347bk`
- VAL-000005 (`10.1016/j.im.2015.06.009 | https://aisel.aisnet.org/trr/vol7/iss1/3`): doi_r is a link, not a DOI — `https://aisel.aisnet.org/trr/vol7/iss1/3`
- VAL-000210 (`https://www.jstor.org/stable/41387554 | 10.1037/0022-0663.83.4.444`): doi_o is a link, not a DOI — `https://www.jstor.org/stable/41387554`
- FRED-000886 (`scholars.indianastate.edu/etds/2136 | https://digitalcommons.memphis.edu/etd/3538/`): doi_o is a link, not a DOI — `scholars.indianastate.edu/etds/2136`
- FRED-000520 (`thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025`): doi_o is a link, not a DOI — `thekeep.eiu.edu/theses/4783`

**Title says retracted** (6 findings):

_title_o or title_r marks the paper retracted or withdrawn._

- REPL-000405 (`10.1126/science.abh4340 | 10.1126/sciadv.adl0999`): title_o marks the paper retracted or withdrawn — `RETRACTED: In vivo direct imaging of neuronal activity at high temporospatial resolution`
- REPL-000626 (`10.1016/j.cell.2013.04.008 | 10.2337/db13-1435`): title_o marks the paper retracted or withdrawn — `RETRACTED: Betatrophin: A Hormone that Controls Pancreatic β Cell Proliferation`
- REPL-001349 (`10.1073/pnas.1209746109 | 10.1073/pnas.1911695117`): title_o marks the paper retracted or withdrawn — `RETRACTED: Signing at the beginning makes ethics salient and decreases dishonest self-reports in comparison to signing …`
- REPRO-000331 (`10.1257/aer.20210369 | 10.1257/aer.20221432`): title_o marks the paper retracted or withdrawn — `RETRACTED BY THE AUTHORS: Dividend Taxes and the Allocation of Capital`
- FRED-000891 (`10.1086/661890 | 10.15626/mp.2020.2538`): title_o marks the paper retracted or withdrawn — `Retracted: Super Size Me: Product Size as a Signal of Status`
- FRED-000420 (`10.1177/1948550611410890 | 10.1037/xge0000067`): title_o marks the paper retracted or withdrawn — `RETRACTED: Sense Creative! The Impact of Global and Local Vision, Hearing, Touching, Tasting and Smelling on Creative a…`

### Possible problems (25)

_Heuristics: a person decides whether each one is a problem._

**Replication year before original year** (4 findings):

_Could be swapped entries or a data-entry error. Not reported when the original has a preprint or other earlier version._

- REPL-000014 (`10.1080/13639080.2011.573776 | 10.1007/s12186-012-9090-0`): year_r is before year_o (2013) — `2012`
- REPL-000476 (`10.1002/bin.1836 | 10.1002/jaba.659`): year_r is before year_o (2021) — `2019`
- REPRO-000371 (`10.1086/740225 | https://hdl.handle.net/10419/280434`): year_r is before year_o (2026) — `2023`
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

<details><summary><b>Title looks like a file or project name</b> (15 findings)</summary>

_title_r is a file or project name, or a generic name such as 'Analysis'._

- REPL-000453 (`10.1037/a0023937 | https://osf.io/up8xs`): title_r is a generic name, not a title — `Analysis`
- REPL-000679 (`10.1002/job.697 | https://osf.io/snrcv`): title_r is a generic name, not a title — `Analysis`
- REPRO-000056 (`10.1037/xge0000157 | https://osf.io/d3s25`): title_r is a generic name, not a title — `Analysis`
- REPRO-000060 (`10.1007/s10683-012-9342-8 | https://osf.io/6xce3`): title_r is a generic name, not a title — `Analysis`
- REPRO-000077 (`10.1509/jmkr.47.2.348 | https://osf.io/za8vm`): title_r looks like a file or project name — `LeBoeuf_JournMarketRes_2010_EKBZ_Somo_191z`
- REPRO-000079 (`10.1509/jmr.12.0430 | https://osf.io/ptjqy`): title_r looks like a file or project name — `Yang_JournMarketRes_2013_G1Lr_Somo_2zg7`
- REPRO-000081 (`10.1016/j.jesp.2010.11.006 | https://osf.io/4mtgf`): title_r looks like a file or project name — `Woltin_JournExpSocPsych_2011_Wre_Somo_316k`
- REPRO-000096 (`10.1002/per.2062 | https://osf.io/ye5mq`): title_r looks like a file or project name — `Wetzel_EurJournPersonality_2016_Ovkm_Somo_78gg`
- REPRO-000099 (`10.1037/pspi0000065 | https://osf.io/ayvzd`): title_r looks like a file or project name — `Steinmetz_JournPerSocPsy_2016_E4Am_Somo_93mg`
- REPRO-000115 (`10.1016/j.jesp.2016.11.014 | https://osf.io/ptzma`): title_r looks like a file or project name — `Zunick_JournExpSocPsych_2017_zlw_Somo_zg66`
- REPRO-000118 (`10.5465/amj.2016.0086 | https://osf.io/bjfvk`): title_r looks like a file or project name — `Dumas_AcaManageJourn_2018_5KrD_Somo_82y7`
- REPRO-000151 (`10.1086/700235 | https://osf.io/63bzr`): title_r looks like a file or project name — `Usmani_AmJournSocio_2018_GJe4_Somo_y4m6`
- SCORE-000066 (`10.1093/jcr/ucx109 | https://osf.io/tywer`): title_r is a generic name, not a title — `Final Report`
- SCORE-000068 (`10.1086/698750 | https://osf.io/rzny2`): title_r is a generic name, not a title — `Analysis`
- SCORE-000071 (`10.1016/j.socscimed.2010.08.007 | https://osf.io/yva89`): title_r is a generic name, not a title — `Analysis`

</details>

---

**Checks passed:**

- Text fields hold text
- Type values valid
- Source values valid
- Year ranges reasonable
- No duplicate records
- DOI mapped to conflicting references
- No 'NA'/'N/A' strings


## FLoRA network validation

_Generated: 2026-10-28 03:09:39 UTC | Dataset: 2,847 rows_

Each item names the FLoRA records carrying the DOI or link by FLoRA ID, says what is wrong and, after the dash, gives the DOI or link.

### Problems (40)

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

<details><summary><b>Dead links</b> (24 findings)</summary>

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
- REPL-001624 (`10.1177/0146167208315555 | 10.1002/ejsp.70015`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `http://eprints.keele.ac.uk/2958/1/M%20Noor%20-%20Intergroup%20forgiveness%20and%20reconciliation.pdf`
- FRED-000270, VAL-000311: oa_url_o is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `https://autopapers.ssrn.com/sol3/papers.cfm?abstract_id=1324778`
- FRED-000012 (`10.1037/0022-3514.86.5.680 | 10.1016/j.jesp.2020.104052`): oa_url_o is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `https://autopapers.ssrn.com/sol3/papers.cfm?abstract_id=929932`
- FRED-000643 (`10.1037/0278-7393.34.1.97 | 10.1126/science.aac4716`): oa_url_o is dead (HTTP 404; an open-access link from enrichment, not from the source record) — `http://hdl.handle.net/10026.1/3213`
- FRED-000469 (`10.1177/0146167210386238 | 10.1525/collabra.192`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `http://www.collabra.org/articles/10.1525/collabra.192/galley/251/download/`
- FRED-000772 (`10.1177/0956797611434748 | 10.1016/j.cognition.2014.09.003`): oa_url_r is dead (connection refused; an open-access link from enrichment, not from the source record) — `http://orbilu.uni.lu/handle/10993/17967`
- VAL-000031 (`10.1016/0010-0285(89)90003-0 | 10.7916/d8f76r2r`): url_r is dead (DNS: name not found) — `https://tesol-dev.journals.cdrs.columbia.edu/wp-content/uploads/sites/12/2015/06/3.-Seol-2005.pdf`
- VAL-000031 (`10.1016/0010-0285(89)90003-0 | 10.7916/d8f76r2r`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `https://tesol-dev.journals.cdrs.columbia.edu/wp-content/uploads/sites/12/2015/06/3.-Seol-2005.pdf`
- VAL-000077 (`10.1016/j.cognition.2006.12.015 | 10.1525/collabra.87`): oa_url_r is dead (DNS: name not found; an open-access link from enrichment, not from the source record) — `http://www.collabra.org/articles/10.1525/collabra.87/galley/208/download/`
- VAL-000196 (`10.1371/journal.pone.0197268 | 10.1371/journal.pone.0253023`): oa_url_o is dead (HTTP 400; an open-access link from enrichment, not from the source record) — `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0197268/1/pone.0197268.pdf?X-Goog-Algorithm=GOOG4-…`
- REPL-000373 (`10.1177/0956797613508412 | https://openalex.org/W7110675912`): url_r is dead (HTTP 404) — `https://openalex.org/W7110675912`
- REPL-001327 (`10.1006/jmla.1996.0032 | https://openalex.org/W7110491536`): url_r is dead (HTTP 404) — `https://openalex.org/W7110491536`
- FRED-000267 (`10.1037/0278-6133.3.5.431 | https://docs.google.com/document/d/10OKLI0-Kw2D7SDyLU_Ieql88gLtbnYGSyZhCBqsEAyk`): url_r is dead (HTTP 410) — `https://docs.google.com/document/d/10OKLI0-Kw2D7SDyLU_Ieql88gLtbnYGSyZhCBqsEAyk`
- VAL-000315 (`10.1111/j.1540-4781.1996.tb05468.x | 10.32601/ejal.460588`): url_r is dead (HTTP 404) — `http://dergipark.gov.tr/download/article-file/536686`

</details>

### Possible problems (8)

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

<details><summary><b>Could not check</b> (9 links)</summary>

_Not findings: no answer could be had for these 9, and they are checked again on the next run._

**Links** (9):

- REPL-000009, oa_url_r: HTTP 418 — `https://hrcak.srce.hr/file/334421`
- REPL-001176, oa_url_r: timed out — `https://cdr.lib.unc.edu/downloads/7w62fg56x`
- REPL-001436, oa_url_o: timed out — `http://hdl.handle.net/2328/35450`
- FRED-000766, oa_url_o: timed out — `http://hdl.handle.net/10366/159754`
- VAL-000159, oa_url_r: TLS: unable to get local issuer certificate — `https://polipapers.upv.es/index.php/MUSE/article/download/10390/11334`
- REPL-000190, oa_url_o: TLS: unable to get local issuer certificate — `http://hdl.handle.net/10902/7358`
- REPL-001417, url_r: redirect loop — `https://www.proquest.com/docview/221001969?pq-origsite=gscholar&fromopenview=true&sourcetype=Scholarly%20Journals`
- REPL-001618, url_o: redirect loop — `https://www.proquest.com/docview/1470800730`
- FRED-000798, oa_url_o: ConnectionResetError: \[Errno 104\] Connection reset by peer — `http://ira.lib.polyu.edu.hk/bitstream/10397/97105/1/Huang_Influence_Product_Anthropomorphism.pdf`

</details>


**Network validation status: needs review**

- DOIs: 4,625 distinct; 4,605 cached, 20 checked, 0 deferred.
- Links: 3,187 distinct; 3,143 cached, 44 checked (9 could not be checked), 0 deferred.
- Originals without a DOI: 26 distinct; 26 cached, 0 checked, 0 deferred.


## Release files

- flora.csv: 2,847 rows, SHA-256 `6bad4d9dd1af137594aeb803e140f62d2d4547dcb00bb6e8ce7aef3246f5403d`.
- Also written: the release manifest and notes, and today's counts in flora_history.csv and flora_dataset_summary.md.
- Publication: The repository copy (`output/` on GitHub) is committed by two workflows, each when flora.csv changed: the nightly job, with its own run's files, and Publish FLoRA, with the stored dataset. Website and local command-line runs commit nothing. Nothing is uploaded to OSF.
