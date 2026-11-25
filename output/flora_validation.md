## FLoRA structural validation

_Generated: 2026-11-25 03:06:27 UTC | Dataset: 2,918 rows_

Each item names the FLoRA record by its FLoRA ID and `doi_o | doi_r` (or `url_r`), says what is wrong and, after the dash, gives the value.

### Problems (21)

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

**Title says retracted** (6 findings):

_title_o or title_r marks the paper retracted or withdrawn._

- REPL-000405 (`10.1126/science.abh4340 | 10.1126/sciadv.adl0999`): title_o marks the paper retracted or withdrawn — `RETRACTED: In vivo direct imaging of neuronal activity at high temporospatial resolution`
- REPL-000626 (`10.1016/j.cell.2013.04.008 | 10.2337/db13-1435`): title_o marks the paper retracted or withdrawn — `RETRACTED: Betatrophin: A Hormone that Controls Pancreatic β Cell Proliferation`
- REPL-001349 (`10.1073/pnas.1209746109 | 10.1073/pnas.1911695117`): title_o marks the paper retracted or withdrawn — `RETRACTED: Signing at the beginning makes ethics salient and decreases dishonest self-reports in comparison to signing …`
- REPRO-000331 (`10.1257/aer.20210369 | 10.1257/aer.20221432`): title_o marks the paper retracted or withdrawn — `RETRACTED BY THE AUTHORS: Dividend Taxes and the Allocation of Capital`
- FRED-000891 (`10.1086/661890 | 10.15626/mp.2020.2538`): title_o marks the paper retracted or withdrawn — `Retracted: Super Size Me: Product Size as a Signal of Status`
- FRED-000420 (`10.1177/1948550611410890 | 10.1037/xge0000067`): title_o marks the paper retracted or withdrawn — `RETRACTED: Sense Creative! The Impact of Global and Local Vision, Hearing, Touching, Tasting and Smelling on Creative a…`

### Possible problems (11)

_Heuristics: a person decides whether each one is a problem._

**Replication year before original year** (4 findings):

_Could be swapped entries or a data-entry error. Not reported when the original has a preprint or other earlier version._

- REPL-000014 (`10.1080/13639080.2011.573776 | 10.1007/s12186-012-9090-0`): year_r is before year_o (2013) — `2012`
- REPL-000476 (`10.1002/bin.1836 | 10.1002/jaba.659`): year_r is before year_o (2021) — `2019`
- REPL-001689 (`10.1177/1042258719892987 | 10.1111/apps.12247`): year_r is before year_o (2021) — `2020`
- VAL-000528 (`10.1007/s10389-020-01338-4 | 10.3389/fpubh.2021.620264`): year_r is before year_o (2022) — `2021`

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

- All links are valid URLs
- Outcome values valid
- Text fields hold text
- Type values valid
- Source values valid
- Year ranges reasonable
- No duplicate records
- DOI format valid
- DOI mapped to conflicting references
- No 'NA'/'N/A' strings
