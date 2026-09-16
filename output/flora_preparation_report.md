# FLoRA preparation report

Generated: 2026-09-16T14:06:35+00:00

Status: **needs_attention**

Mode: database_pipeline

Rows: 2,913

## Stages

- final CSV: passed
- structural validation: needs_attention — 210 data issue(s)
- network validation: skipped
- release preparation and history: passed
- store prepared FLoRA table: passed — 2913 active rows in flora_data
- publish CSV artifact: passed

## Items needing attention

- 12 rows omitted because a title is missing; see flora_export_log.csv.
- 55 merged groups with conflicting outcomes; see dup_outcome_conflicts.csv.
- Structural validation found 210 data issue(s).
- Network validation: skipped; 0 finding(s).

## Stored dataset

- Database status: committed.
- Active rows in flora_data: 2,913.

## Transform diagnostics

- flora_export_log.csv: 12 rows omitted because a title is missing.
- dup_outcome_conflicts.csv: 55 merged groups with conflicting outcomes.

The JSON report includes the affected records and reasons.

## FLoRA Data Validation Report

_Generated: 2026-09-16 14:07:38 UTC | Dataset: 2913 rows_

Each row is identified as `doi_o | doi_r (or url_r)`, followed by check-specific detail after `:`.
Tick an item to mark it a false positive; it is suppressed on the next run.

<details><summary><b>Invalid URLs (not starting with http)</b> (125 issues)</summary>

_A url column holds something that is not a link._

- [ ] 10.1111/j.2047-2927.2012.00013.x | 10.1177/1042258720985478: url_o='10.1111/j.2047-2927.2012.00013.x'
- [ ] 10.1016/j.jml.2020.104174 | 10.31234/osf.io/kt3sw_v2: url_r='10.1037/xlm0001650'
 Evidence from Kenya*'
 *'
- [ ] 10.1257/aer.20210369 | 10.1257/aer.20221432: url_o='RETRACTED BY THE AUTHORS: Dividend Taxes and the Allocation of Capital'
- [ ] 10.1257/0002828054201242 | 10.1002/jae.2922: url_o='Do Rural Banks Matter? Evidence from the Indian Social Banking Experiment'
- [ ]  | 10.15626/mp.2025.4708: url_o='www.proquest.com/docview/1470800730'
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025: url_o='thekeep.eiu.edu/theses/4783'
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025/?utm_source=thekeep.eiu.edu%2Ftheses%2F5025: url_o='thekeep.eiu.edu/theses/4783'

</details>

<details><summary><b>Invalid outcome values</b> (11 issues)</summary>

_An outcome outside the allowed set for the row's type, or missing._

- [ ] 10.1002/j.2164-585x.1979.tb00097.x | 10.1353/csd.2014.0018: outcome is blank
- [ ] 10.1086/511995 | 10.5018/economics-ejournal.ja.2017-13: type=replication; outcome='failed || computational issues, robustness challenges'
- [ ] 10.1257/0002828054201242 | 10.1002/jae.2922: type=reproduction; outcome='not checked, robustness challenges || failed'
- [ ] 10.3982/ecta6248 | 10.1002/jae.2861: type=reproduction; outcome='computationally reproducible, robust || computationally reproducible, not checked'
- [ ] 10.1017/s1930297500005064 | 10.1080/23743603.2021.1878340: type=replication; outcome='failed || uninformative'
- [ ] 10.1073/pnas.1523698113 | 10.1098/rsos.231240: type=replication; outcome='successful || uninformative'
- [ ] 10.1080/02699931.2016.1273201 | 10.31234/osf.io/ntukz: type=replication; outcome='statistically successful but flawed || failed'
- [ ] 10.1177/1948550611410890 | 10.1037/xge0000067: type=replication; outcome='uninformative || failed'
- [ ] 10.1080/07350015.1995.10524601 | 10.1002/jae.2458: type=replication; outcome='successful || computationally reproducible, not checked'
- [ ] 10.1037/h0055756 | https://osf.io/74892/: outcome is blank
- [ ] 10.1017/xps.2014.22 | https://osf.io/8uc5z/files/jmk9d: outcome is blank

</details>

**Implausible years** (1 issue):

_year_o or year_r is outside 1890 to next year._

- [ ]  | 10.1371/journal.pone.0120644: year_o=1880

**Replication year before original year** (2 issues):

_Could indicate swapped entries or a data-entry error._

- [ ] 10.1002/bin.1836 | 10.1002/jaba.659: year_o=2021; year_r=2019
- [ ] 10.1016/j.cognition.2025.106257 | 10.31234/osf.io/j2xn3: year_o=2026; year_r=2024

<details><summary><b>Exact duplicates (by doi_o + doi_r)</b> (24 issues)</summary>

_Rows sharing the same doi_o and doi_r after deduplication._

- [ ] 10.1002/per.2062 | https://osf.io/udbnf
- [ ] 10.1002/per.2062 | https://osf.io/ye5mq
- [ ] 10.1016/j.jesp.2010.11.006 | https://osf.io/4mtgf
- [ ] 10.1016/j.jesp.2010.11.006 | https://osf.io/xr32n
- [ ] 10.1017/s0003055412000299 | https://osf.io/ceaq3
- [ ] 10.1017/s0003055412000299 | https://osf.io/ck8aq
- [ ] 10.1017/s0043887112000032 | https://osf.io/kw5tv
- [ ] 10.1017/s0043887112000032 | https://osf.io/qev3x
- [ ] 10.1037/xge0000157 | https://osf.io/5r28d
- [ ] 10.1037/xge0000157 | https://osf.io/d3s25
- [ ] 10.1086/698750 | https://osf.io/gvayj
- [ ] 10.1086/698750 | https://osf.io/rzny2
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://drive.google.com/file/d/1DDCuEgQah24QordxkRGO0EIt6Q681jyL/view
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://web.archive.org/web/20191117063540/http://www.psychfiledrawer.org/replication.php?attempt=MTQ5
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://web.archive.org/web/20191201181415/http://psychfiledrawer.org/replication.php?attempt=MTc3
- [ ] 10.1257/aer.20201550 | https://shs.hal.science/hal-05474766/
- [ ] 10.1509/jmkr.47.2.348 | https://osf.io/x9j47
- [ ] 10.1509/jmkr.47.2.348 | https://osf.io/za8vm
- [ ] 10.1509/jmr.12.0430 | https://osf.io/2vmgw
- [ ] 10.1509/jmr.12.0430 | https://osf.io/ptjqy
- [ ] 10.5465/amj.2016.0086 | https://osf.io/bjfvk
- [ ] 10.5465/amj.2016.0086 | https://osf.io/jx64p
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025/?utm_source=thekeep.eiu.edu%2Ftheses%2F5025

</details>

<details><summary><b>Missing required fields</b> (37 issues)</summary>

_title_o, title_r and doi_o must be present, plus doi_r or url_r._

- [ ]  | 10.1016/j.lindif.2015.03.029: missing doi_o
- [ ]  | 10.1177/2053168016652420: missing doi_o
- [ ]  | 10.1177/0016986220932533: missing doi_o
- [ ]  | 10.1002/aur.2496: missing doi_o
- [ ]  | 10.18148/srm/2012.v6i1.5092: missing doi_o
- [ ]  | 10.1103/physrevstper.10.010110: missing doi_o
- [ ]  | 10.1080/22243534.2015.11828344: missing doi_o
- [ ]  | 10.1177/0958928712471227: missing doi_o
- [ ]  | 10.1371/journal.pone.0120644: missing doi_o
- [ ]  | 10.5539/ibr.v5n4p71: missing doi_o
- [ ]  | 10.3390/jintelligence7010006: missing doi_o
- [ ]  | 10.1177/0098628311421328: missing doi_o
- [ ]  | 10.1177/0022466912449652: missing doi_o
- [ ]  | 10.1177/0098628320979864: missing doi_o
- [ ]  | 10.18653/v1/w19-8649: missing doi_o
- [ ]  | 10.1111/j.1949-8594.2012.00166.x: missing doi_o
- [ ]  | 10.1177/1029864913489703: missing doi_o
- [ ]  | 10.1111/jpr.12599: missing doi_o
- [ ]  | 10.36948/ijfmr.2025.v07i06.61995: missing doi_o
- [ ]  | 10.1002/jaba.706: missing doi_o
- [ ]  | https://blogs.lse.ac.uk/impactofsocialsciences/2013/09/10/bias-at-the-bbc-replicating-the-cps-analysis/: missing doi_o
- [ ]  | 10.17885/heiup.1157.c19369: missing doi_o
- [ ]  | 10.1142/s1084946721500084: missing doi_o
- [ ]  | 10.1525/collabra.163080: missing doi_o
- [ ]  | 10.15626/mp.2025.4708: missing doi_o
- [ ]  | https://hdl.handle.net/20.500.14721/40318: missing doi_o
- [ ]  | 10.48550/arxiv.2604.00848: missing doi_o

</details>

**Invalid DOI format** (9 issues):

_DOI does not match 10.NNNN/..._

- [ ] art00005 | 10.1007/s13164-018-0400-9: doi_o='art00005'
- [ ] djaerv_2018 | 10.1098/rsos.231240: doi_o='djaerv_2018'
- [ ] escholarship.orgucitem39d347bk | 10.1098/rsos.231240: doi_o='escholarship.orgucitem39d347bk'
- [ ] 10.1016/j.im.2015.06.009 | https://aisel.aisnet.org/trr/vol7/iss1/3: doi_r='https://aisel.aisnet.org/trr/vol7/iss1/3'
- [ ] https://www.jstor.org/stable/41387554 | 10.1037/0022-0663.83.4.444: doi_o='https://www.jstor.org/stable/41387554'
- [ ] scholars.indianastate.edu/etds/2136 | https://digitalcommons.memphis.edu/etd/3538/: doi_o='scholars.indianastate.edu/etds/2136'
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025: doi_o='thekeep.eiu.edu/theses/4783'
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025/?utm_source=thekeep.eiu.edu%2Ftheses%2F5025: doi_o='thekeep.eiu.edu/theses/4783'

**DOI with conflicting references** (1 issue):

_The same DOI appears with reference strings more than 20% apart._

- [ ] 10.1098/rsos.231240: 93 different reference strings (doi_r/apa_ref_r)

---

**Checks passed:**
- [x] No 'NA'/'N/A' strings
- [x] Type values valid
- [x] Source values valid


## FLoRA Network Validation Report

_Generated: 2026-09-16 14:07:38 UTC | Dataset: 2913 rows_


**Network validation status: skipped**


Only the selected checks ran: none.


## Prepared release

- File: flora.csv
- SHA-256: `394e6b2ccbf23a0a6858b410be67f857be9bb0f6e7a2a8165e72839448359d74`
- Publication: local files only; no OSF or GitHub release.

## Dataset history

The website records daily dataset counts in its shared history table. This run also writes a CSV history and a Markdown summary alongside the release.
