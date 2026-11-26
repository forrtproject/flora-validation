## FLoRA structural validation

_Generated: 2026-11-26 03:34:00 UTC | Dataset: 2,970 rows_

Each item names the FLoRA record by its FLoRA ID and `doi_o | doi_r` (or `url_r`), says what is wrong and, after the dash, gives the value.

### Problems (21)

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

- All links are valid URLs
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
