# FLoRA preparation report

Generated: 2026-10-20T03:15:18+00:00

Status: **needs_attention**

Mode: database_pipeline

Rows: 3,080

## Stages

- final CSV: passed
- structural validation: needs_attention — 215 data issue(s)
- network validation: needs_attention
- release preparation and history: passed
- store prepared FLoRA table: passed — 3080 active rows in flora_data
- publish CSV artifact: passed

## Items needing attention

- 29 rows omitted because a title is missing; see flora_export_log.csv.
- 60 merged groups with conflicting outcomes; see dup_outcome_conflicts.csv.
- 20 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates); see preprint_dedup_candidates.csv.
- Structural validation found 215 data issue(s).
- Network validation: needs_attention; 96 finding(s).

## Stored dataset

- Database status: committed.
- Active rows in flora_data: 3,080.

## Transform diagnostics

- flora_export_log.csv: 29 rows omitted because a title is missing.
- dup_outcome_conflicts.csv: 60 merged groups with conflicting outcomes.
- preprint_dedup_candidates.csv: 20 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates).

The JSON report includes the affected records and reasons.

## FLoRA Data Validation Report

_Generated: 2026-10-20 03:22:33 UTC | Dataset: 3080 rows_

Each row is identified as `doi_o | doi_r (or url_r)`, followed by check-specific detail after `:`.
Tick an item to mark it a false positive; it is suppressed on the next run.

<details><summary><b>Invalid URLs (not starting with http)</b> (126 issues)</summary>

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
- [ ] 10.1080/07350015.1995.10524601 | 10.1002/jae.2458: url_o='Lag Order and Critical Values of the Augmented Dickey-Fuller Test'

</details>

**Invalid outcome values** (7 issues):

_An outcome outside the allowed set for the row's type, or missing._

- [ ] 10.1002/j.2164-585x.1979.tb00097.x | 10.1353/csd.2014.0018: outcome is blank
- [ ] 10.1017/s1930297500005064 | 10.1080/23743603.2021.1878340: type=replication; outcome='failed || uninformative'
- [ ] 10.1073/pnas.1523698113 | 10.1098/rsos.231240: type=replication; outcome='successful || uninformative'
- [ ] 10.1080/02699931.2016.1273201 | 10.31234/osf.io/ntukz: type=replication; outcome='statistically successful but flawed || failed'
- [ ] 10.1177/1948550611410890 | 10.1037/xge0000067: type=replication; outcome='uninformative || failed'
- [ ] 10.1037/h0055756 | https://osf.io/74892/: outcome is blank
- [ ] 10.1017/xps.2014.22 | https://osf.io/8uc5z/files/jmk9d: outcome is blank

**Implausible years** (1 issue):

_year_o or year_r is outside 1890 to next year._

- [ ]  | 10.1371/journal.pone.0120644: year_o=1880

**Replication year before original year** (3 issues):

_Could indicate swapped entries or a data-entry error._

- [ ] 10.1002/bin.1836 | 10.1002/jaba.659: year_o=2021; year_r=2019
- [ ] 10.1016/j.cognition.2025.106257 | 10.31234/osf.io/j2xn3: year_o=2026; year_r=2024
- [ ] 10.1086/740225 | https://hdl.handle.net/10419/280434: year_o=2026; year_r=2023

<details><summary><b>Exact duplicates (by doi_o + doi_r)</b> (29 issues)</summary>

_Rows sharing the same doi_o and doi_r after deduplication._

- [ ] 10.1002/per.2062 | https://osf.io/udbnf
- [ ] 10.1002/per.2062 | https://osf.io/ye5mq
- [ ] 10.1016/j.jesp.2010.11.006 | https://osf.io/4mtgf
- [ ] 10.1016/j.jesp.2010.11.006 | https://osf.io/xr32n
- [ ] 10.1016/s0883-9026(02)00097-6 | 10.1177/10422587251322409
- [ ] 10.1017/s0003055412000299 | https://osf.io/ceaq3
- [ ] 10.1017/s0003055412000299 | https://osf.io/ck8aq
- [ ] 10.1017/s0043887112000032 | https://osf.io/kw5tv
- [ ] 10.1017/s0043887112000032 | https://osf.io/qev3x
- [ ] 10.1037/xge0000157 | https://osf.io/5r28d
- [ ] 10.1037/xge0000157 | https://osf.io/d3s25
- [ ] 10.1080/07350015.1995.10524601 | 10.1002/jae.2458
- [ ] 10.1086/511995 | 10.5018/economics-ejournal.ja.2017-13
- [ ] 10.1086/669122 | 10.31235/osf.io/2nwft
- [ ] 10.1086/698750 | https://osf.io/gvayj
- [ ] 10.1086/698750 | https://osf.io/rzny2
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://drive.google.com/file/d/1DDCuEgQah24QordxkRGO0EIt6Q681jyL/view
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://web.archive.org/web/20191117063540/http://www.psychfiledrawer.org/replication.php?attempt=MTQ5
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://web.archive.org/web/20191201181415/http://psychfiledrawer.org/replication.php?attempt=MTc3
- [ ] 10.1257/0002828054201242 | 10.1002/jae.2922
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

<details><summary><b>Missing required fields</b> (38 issues)</summary>

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
- [ ]  | 10.5406/bulcouresmusedu.193.0045: missing doi_o

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

**DOI with conflicting references** (2 issues):

_The same DOI appears with reference strings more than 20% apart._

- [ ] 10.1037/e519682015-069: 2 different reference strings (doi_o/apa_ref_o)
- [ ] 10.1098/rsos.231240: 93 different reference strings (doi_r/apa_ref_r)

---

**Checks passed:**
- [x] No 'NA'/'N/A' strings
- [x] Type values valid
- [x] Source values valid


## FLoRA Network Validation Report

_Generated: 2026-10-20 03:22:33 UTC | Dataset: 3080 rows_

<details><summary><b>Retracted papers</b> (13 issues)</summary>

- [ ] `10.1126/science.abh4340`: retracted (doi_o) — 10.1126/science.abh4340 | 10.1126/sciadv.adl0999
- [ ] `10.1001/jamapsychiatry.2014.763`: retracted (doi_o) — 10.1001/jamapsychiatry.2014.763 | 10.1037/pas0000664
- [ ] `10.1016/j.cell.2013.04.008`: retracted (doi_o) — 10.1016/j.cell.2013.04.008 | 10.2337/db13-1435
- [ ] `10.1038/sdata.2016.120`: retracted (doi_r) — 10.1073/pnas.1118373109 | 10.1038/sdata.2016.120
- [ ] `10.1073/pnas.1209746109`: retracted (doi_o) — 10.1073/pnas.1209746109 | 10.1073/pnas.1911695117
- [ ] `10.1257/aer.20210369`: retracted (doi_o) — 10.1257/aer.20210369 | 10.1257/aer.20221432
- [ ] `10.1086/661890`: retracted (doi_o) — 10.1086/661890 | 10.15626/mp.2020.2538
- [ ] `10.1111/j.0956-7976.2004.00757.x`: retracted (doi_o) — 10.1111/j.0956-7976.2004.00757.x | 10.1525/collabra.32572
- [ ] `10.1177/1948550611410890`: retracted (doi_o) — 10.1177/1948550611410890 | 10.1037/xge0000067
- [ ] `10.1509/jmkr.45.6.633`: retracted (doi_o) — 10.1509/jmkr.45.6.633 | 10.1177/2515245918781032
- [ ] `10.1177/0956797611432497`: retracted (doi_o) — 10.1177/0956797611432497 | 10.1027/1864-9335/a000191

</details>

<details><summary><b>DOIs that failed to resolve</b> (34 issues)</summary>

- [ ] `10.1101/gr.126516.111`
- [ ] `10.18718/81781.7`
- [ ] `10.17615/q9na-cr13`
- [ ] `10.1111/j.1540-6261.2004.00679`
- [ ] `10.1111/j.1540-6261.1990.tb03731`
- [ ] `10.1177/0956797626146077`
- [ ] `10.10419/295247`
- [ ] `10.1162/0033553053970214`
- [ ] `10.1596/1813-9450-1690`
- [ ] `0.1162/0033553053327524`
- [ ] `10.1596/1813-9450-2108`
- [ ] `10.18718/81781.27`
- [ ] `10.18718/81781.23`
- [ ] `10.18718/81781.38`
- [ ] `10.17605/osf.io/82bwv`
- [ ] `10.17605/osf.io/ehjdm`
- [ ] `10.17605/osf.io/ictp5`
- [ ] `10.18718/81781.29`
- [ ] `10.1177/2f10986111211035002`
- [ ] `art00005`
- [ ] `djaerv_2018`
- [ ] `escholarship.orgucitem39d347bk`
- [ ] `https://aisel.aisnet.org/trr/vol7/iss1/3`
- [ ] `10.31542/r.gm:2129`
- [ ] `10.17705/1atrr.00067`
- [ ] `10.17705/1atrr.00046`
- [ ] `10.17705/1atrr.00066`
- [ ] `10.4995/muse.2019.10390`
- [ ] `10.17705/1atrr.00053`
- [ ] `https://www.jstor.org/stable/41387554`
- [ ] `10.17705/1atrr.00064`
- [ ] `scholars.indianastate.edu/etds/2136`
- [ ] `thekeep.eiu.edu/theses/4783`
- [ ] `10.18718/81781.40`

</details>

<details><summary><b>URLs that failed to resolve</b> (49 issues)</summary>

- [ ] `https://hrcak.srce.hr/file/334421`
- [ ] `http://hdl.handle.net/1820/9055`
- [ ] `http://orbilu.uni.lu/handle/10993/18672`
- [ ] `https://dash.harvard.edu/bitstreams/7312037d-191b-6bd4-e053-0100007fdf3b/download`
- [ ] `http://www.collabra.org/articles/10.1525/collabra.216/galley/263/download/`
- [ ] `http://www.collabra.org/articles/10.1525/collabra.117/galley/146/download/`
- [ ] `http://e-spacio.uned.es/fez/view/bibliuned:DptoMIDEII-FEDU-Articulos-Jcperez-0006`
- [ ] `http://hdl.handle.net/10722/228801`
- [ ] `https://openalex.org/W7113264659`
- [ ] `http://elibrary.aisnet.org/Default.aspx?url=https://aisel.aisnet.org/cgi/viewcontent.cgi?article=1006&context=trr`
- [ ] `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0198620/1/pone.0198620.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Credential=wombat-sa%40plos-prod.iam.gserviceaccount.com%2F20210216%2Fauto%2Fstorage%2Fgoog4_request&X-Goog-Date=20210216T121734Z&X-Goog-Expires=3600&X-Goog-SignedHeaders=host&X-Goog-Signature=3a730412736db07da92d2cbb40223611403deceedf4c4ab529ddf4e5b8a860d786570225c2de9600d35dc629d57a660246ac9253b8f99072ab5dc9a80567f81f485d411e250713b8be101c0dee2513a00e2d03ecc80f33de21a761f1fedc45ebdea3ddc53fe6517f06eca371a7f7adaec25056b2c6917bb4f238ed81004578297007e6bef4bcc3af13b93508ccfecf2555ec254babd57c8b15e92a9cfd500a5fd8b1c81f92fdb70874012974d676dbc65974e25bf291f0851a6ac186cb1c5681c9326808c4baf9a0ddfd938c18c571d3e245c17df563554cef21ac564e57881f9f9d9c849efa122d536b4441caa7b213777510d9c7b43b16dfccb17381a049e3`
- [ ] `https://biblio.ugent.be/publication/8707609`
- [ ] `http://orbilu.uni.lu/handle/10993/45413`
- [ ] `https://cdr.lib.unc.edu/downloads/7w62fg56x`
- [ ] `https://storkjournals.org/index.php/cik/article/download/39/23`
- [ ] `http://libres.uncg.edu/ir/asu/f/Eshenbaugh_Brooklyn Spring 2018 Thesis.pdf`
- [ ] `http://libres.uncg.edu/ir/uncg/f/E_Jones_Livestock_2012.pdf`
- [ ] `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0209263/1/pone.0209263.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Credential=wombat-sa%40plos-prod.iam.gserviceaccount.com%2F20210221%2Fauto%2Fstorage%2Fgoog4_request&X-Goog-Date=20210221T170527Z&X-Goog-Expires=3600&X-Goog-SignedHeaders=host&X-Goog-Signature=738cc051df73116169d6a637a6e0d613b3b424dd19f9cc1a2a0652ef9604ca8fb5d4d361df382b5d7c408c77fb4f6509545dcdfe2486dfa37634a35d560bbd4a57d917d70d2ab3ca1c3cd77168c0c56f976f4fa1508305c5e0213370e05fc7f24e3297fcd30822a3f663542a8c941c479901db91978ed30b7f2fe9d211537ee88bc41071cefffe9a060d0fa72322d2d91a19a2d55034266269fe25ef495d4b5c182c0b67d21bc7986eac8096381703773fcae76c9865505177b5f854ea0d25080e05671cc0510339cc237b5ff5a1509b8ac9703d3c6b51bb6dc7a9528bbcd13f1c9195ed9db5302e257d52915013cf67847abac8dfa22d54e5b829452b1e261e`
- [ ] `http://hdl.handle.net/2328/35450`
- [ ] `http://www.economics-ejournal.org/dataset/PDFs/journalarticles_2019-51.pdf`
- [ ] `http://eprints.keele.ac.uk/2958/1/M%20Noor%20-%20Intergroup%20forgiveness%20and%20reconciliation.pdf`
- [ ] `http://libres.uncg.edu/ir/uncg/f/P_Silvia_Remotely_2018.pdf`
- [ ] `http://orbilu.uni.lu/handle/10993/27870`
- [ ] `https://autopapers.ssrn.com/sol3/papers.cfm?abstract_id=1324778`
- [ ] `https://autopapers.ssrn.com/sol3/papers.cfm?abstract_id=929932`
- [ ] `http://hdl.handle.net/10026.1/3213`
- [ ] `http://libres.uncg.edu/ir/uncg/f/P_Delaney_Intentional_2008(MULTI UNCG AUTHORS).pdf`
- [ ] `https://edoc.unibas.ch/30945/3/Reinhard-Greifeneder-Scharmach_2013_Unconscious_processes_improve_lie_detection_manuscript.pdf`
- [ ] `http://hdl.handle.net/10366/159754`
- [ ] `http://www.collabra.org/articles/10.1525/collabra.192/galley/251/download/`
- [ ] `http://orbilu.uni.lu/handle/10993/17967`
- [ ] `https://osf.io/v6zk8 or http://rpubs.com/catherinecthomas/final_replication`
- [ ] `https://tesol-dev.journals.cdrs.columbia.edu/wp-content/uploads/sites/12/2015/06/3.-Seol-2005.pdf`
- [ ] `http://www.collabra.org/articles/10.1525/collabra.87/galley/208/download/`
- [ ] `https://polipapers.upv.es/index.php/MUSE/article/download/10390/11334`
- [ ] `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0197268/1/pone.0197268.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Credential=wombat-sa%40plos-prod.iam.gserviceaccount.com%2F20210222%2Fauto%2Fstorage%2Fgoog4_request&X-Goog-Date=20210222T203614Z&X-Goog-Expires=3600&X-Goog-SignedHeaders=host&X-Goog-Signature=820ffd9e10ee38c554f355d3e14bbbb25b37a99925b05aa62b29fa7c5bf6a6e395fa60956416392212597a68db7f035ee4d20055162de4a7c3959a865cc8327a680e63be2076a25952b9831cd50df888c6c653e28f5c376e340d7ca53ed8e648e7f3a9e16aac2440df564814224cb8ff7c302192de66421bdc172001bb613b91fa38c2498c90bba7caae3a58508316ce71b6593f47220a73f5c648f7287730aa3771bebe0e83349076f2a3803bf17c918e45ad49a2486e3746cc828b12388bc2f1e4f56055ef43037c4839564da2b5cb7ebd9941e2f2db4e87bb39e0c84fe54c11d8e627d4fb83bda04510195c02b5228a898fe2a6d051258a467835d9cfdcb0`
- [ ] `https://www.termedia.pl/Journal/-75/pdf-45660-10?filename=Williams Conceptual.pdf`
- [ ] `https://openalex.org/W3122646001`
- [ ] `http://hdl.handle.net/10902/7358`
- [ ] `https://openalex.org/W7110675912`
- [ ] `https://openalex.org/W7110491536`
- [ ] `https://www.proquest.com/docview/221001969?pq-origsite=gscholar&fromopenview=true&sourcetype=Scholarly%20Journals`
- [ ] `http://ira.lib.polyu.edu.hk/bitstream/10397/103007/1/Park_Fu_Positive_Relation.pdf`
- [ ] `https://EconPapers.repec.org/RePEc:zbw:i4rdps:40`
- [ ] `https://EconPapers.repec.org/RePEc:zbw:i4rdps:42`
- [ ] `https://docs.google.com/document/d/10OKLI0-Kw2D7SDyLU_Ieql88gLtbnYGSyZhCBqsEAyk`
- [ ] `http://ira.lib.polyu.edu.hk/bitstream/10397/97105/1/Huang_Influence_Product_Anthropomorphism.pdf`
- [ ] `https://journals.sagepub.com/doi/full/10.1177/2515…`
- [ ] `http://dergipark.gov.tr/download/article-file/536686`

</details>


**Network validation status: needs_attention**

- dois: 5098 targets; 5064 cached; 34 checked; 0 deferred.
- urls: 3286 targets; 3237 cached; 49 checked; 0 deferred.


## Prepared release

- File: flora.csv
- SHA-256: `0b2a39550fb71c2aa73a48e0ffede61fb11f6d18c448eff485bf8a0e63e34d72`
- Publication: local files only; no OSF or GitHub release.

## Dataset history

The website records daily dataset counts in its shared history table. This run also writes a CSV history and a Markdown summary alongside the release.
