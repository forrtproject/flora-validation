# FLoRA preparation report

Generated: 2026-09-15T17:51:26+00:00

Status: **needs_attention**

Mode: existing_csv

Rows: 2,914

## Stages

- final CSV: passed
- structural validation: needs_attention — 557 data issue(s)
- network validation: skipped
- release preparation and history: passed

## Items needing attention

- Structural validation found 557 data issue(s).
- Network validation: skipped; 0 finding(s).

## FLoRA Data Validation Report

_Generated: 2026-09-15 17:52:05 UTC | Dataset: 2914 rows_

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
- [ ] 10.2307/749455 | 10.5964/jnc.6063: url_r='10.31234/osf.io/wej8z'
- [ ] 10.1080/07350015.1995.10524601 | 10.1002/jae.2458: url_o='Lag Order and Critical Values of the Augmented Dickey-Fuller Test'
- [ ]  | 10.15626/mp.2025.4708: url_o='www.proquest.com/docview/1470800730'

</details>

<details><summary><b>Invalid outcome values</b> (354 issues)</summary>

_An outcome outside the allowed set for the row's type, or missing._

- [ ] 10.1002/j.2164-585x.1979.tb00097.x | 10.1353/csd.2014.0018: outcome is blank
- [ ] 10.1093/qje/qjac034 | 10.10419/295247: outcome is blank
- [ ] 10.1162/rest_a_01182 | 10.17605/osf.io/c3k6f: outcome is blank
- [ ] 10.2307/2586119 | 10.2307/2586120: outcome is blank
- [ ] 10.2139/ssrn.1158663 | 10.1177/2053168015577969: outcome is blank
- [ ] 10.1111/j.1540-5907.2006.00220.x | 10.1177/2053168014559094: outcome is blank
- [ ] 10.1073/pnas.2216614120 | 10.5281/zenodo.17705056: outcome is blank
- [ ] 10.15195/v11.a32 | 10.31235/osf.io/zk2ar_v2: outcome is blank
- [ ] 10.1037/edu0001022 | 10.31234/osf.io/weu9m_v1: outcome is blank
- [ ] 10.1126/science.1195996 | 10.1177/0956797620923587: outcome is blank
- [ ] 10.1145/2382448.2382450 | 10.17879/replicationresearch-2026-9414: outcome is blank
- [ ] 10.1016/j.jesp.2014.05.002 | 10.17605/osf.io/akubt: outcome is blank
- [ ] 10.1017/s0007123412000191 | 10.17605/osf.io/fpuh3: outcome is blank
- [ ] 10.1177/0956797618756862 | 10.17605/osf.io/vndky: outcome is blank
- [ ] 10.1002/per.2157 | 10.17605/osf.io/yrh4v: outcome is blank
- [ ] 10.1086/684582 | 10.17605/osf.io/35tgu: outcome is blank
- [ ] 10.1017/s0043887110000286 | 10.17605/osf.io/zsptj: outcome is blank
- [ ] 10.1017/s0003055413000415 | 10.17605/osf.io/4y8ja: outcome is blank
- [ ] 10.1111/j.1745-9125.2011.00251.x | 10.17605/osf.io/nhecx: outcome is blank
- [ ] 10.1017/s0003055413000567 | 10.17605/osf.io/qu3p2: outcome is blank
- [ ] 10.1509/jm.14.0091 | 10.17605/osf.io/3mr7g: outcome is blank
- [ ] 10.1037/pspi0000065 | 10.17605/osf.io/axtg4: outcome is blank
- [ ] 10.1093/jcr/ucw044 | 10.17605/osf.io/jzfs2: outcome is blank
- [ ] 10.1017/xps.2017.15 | 10.17605/osf.io/s57jr: outcome is blank
- [ ] 10.1016/j.jenvp.2014.10.008 | 10.17605/osf.io/v9ykq: outcome is blank
- [ ] 10.1017/s0003055410000262 | 10.17605/osf.io/q5ka2: outcome is blank
- [ ] 10.1016/j.evolhumbehav.2014.02.004 | 10.17605/osf.io/y47pr: outcome is blank
- [ ] 10.1086/597030 | 10.17605/osf.io/x9pd3: outcome is blank
- [ ] 10.1017/s0007123413000070 | 10.17605/osf.io/dcez6: outcome is blank
- [ ] 10.1177/0022002714547903 | 10.17605/osf.io/jcbfw: outcome is blank
- [ ] 10.1257/aer.20130267 | 10.17605/osf.io/vk2a3: outcome is blank
- [ ] 10.3982/ecta11380 | 10.17605/osf.io/5fc4n: outcome is blank
- [ ] 10.1017/s0007123416000144 | 10.17605/osf.io/hyqpr: outcome is blank
- [ ] 10.1177/2167702618785339 | 10.17605/osf.io/57pn2: outcome is blank
- [ ] 10.1017/s0043887117000363 | 10.17605/osf.io/urfmp: outcome is blank
- [ ] 10.1016/j.cognition.2017.04.004 | 10.17605/osf.io/vc8hs: outcome is blank
- [ ] 10.1002/job.2118 | 10.17605/osf.io/c4ar6: outcome is blank
- [ ] 10.1257/app.4.1.85 | 10.17605/osf.io/v2yaq: outcome is blank
- [ ] 10.1086/689189 | 10.17605/osf.io/8eqk6: outcome is blank
- [ ] 10.1007/s10683-016-9484-1 | 10.17605/osf.io/3a5u7: outcome is blank
- [ ] 10.1017/s0043887114000227 | 10.17605/osf.io/u3y9w: outcome is blank
- [ ] 10.1017/xps.2017.21 | 10.17605/osf.io/dxmrb: outcome is blank
- [ ] 10.1287/mnsc.2013.1794 | 10.17605/osf.io/sc87r: outcome is blank
- [ ] 10.1017/s000305541600071x | 10.17605/osf.io/wnv8y: outcome is blank
- [ ] 10.1017/s0007123415000356 | 10.17605/osf.io/5a7qp: outcome is blank
- [ ] 10.1257/aer.99.4.1384 | 10.17605/osf.io/563d4: outcome is blank
- [ ] 10.1177/0022002716680266 | 10.17605/osf.io/frc3x: outcome is blank
- [ ] 10.3982/ecta10813 | 10.17605/osf.io/qk9v3: outcome is blank
- [ ] 10.1086/605604 | 10.17605/osf.io/namvy: outcome is blank
- [ ] 10.1017/s0007123414000520 | 10.17605/osf.io/gk6mh: outcome is blank
- [ ] 10.1016/j.jesp.2018.08.007 | 10.17605/osf.io/w8n3s: outcome is blank
- [ ] 10.3982/ecta12987 | 10.17605/osf.io/gpyu7: outcome is blank
- [ ] 10.1086/676333 | 10.17605/osf.io/sv9tm: outcome is blank
- [ ] 10.1111/j.1745-9125.2010.00212.x | 10.17605/osf.io/qd3rm: outcome is blank
- [ ] 10.1111/j.1467-9280.2009.02447.x | 10.17605/osf.io/vh5u6: outcome is blank
- [ ] 10.1086/677392 | 10.17605/osf.io/6mdxr: outcome is blank
- [ ] 10.1111/1745-9125.12035 | 10.17605/osf.io/g2jua: outcome is blank
- [ ] 10.1257/app.3.1.239 | 10.17605/osf.io/h2u96: outcome is blank
- [ ] 10.1257/app.20150517 | 10.17605/osf.io/qs827: outcome is blank
- [ ] 10.1086/691703 | 10.17605/osf.io/tgkhv: outcome is blank
- [ ] 10.1111/ajps.12240 | 10.17605/osf.io/d4p59: outcome is blank
- [ ] 10.1177/0022002710371671 | 10.17605/osf.io/vfyxz: outcome is blank
- [ ] 10.1086/675910 | 10.17605/osf.io/ujmvw: outcome is blank
- [ ] 10.1111/j.1540-5907.2012.00628.x | 10.17605/osf.io/p7tb4: outcome is blank
- [ ] 10.1177/0003122410368927 | 10.17605/osf.io/ks6ut: outcome is blank
- [ ] 10.1257/aer.101.2.493 | 10.17605/osf.io/qnmrj: outcome is blank
- [ ] 10.1016/j.cognition.2012.11.003 | 10.17605/osf.io/7ak4n: outcome is blank
- [ ] 10.1017/s0007123413000422 | 10.17605/osf.io/5vntp: outcome is blank
- [ ] 10.1177/0022002712449328 | 10.17605/osf.io/jp3qb: outcome is blank
- [ ] 10.1257/app.5.3.91 | 10.17605/osf.io/ezx2k: outcome is blank
- [ ] 10.1111/ajps.12394 | 10.17605/osf.io/tmg3z: outcome is blank
- [ ] 10.1037/a0019432 | 10.17605/osf.io/g83kx: outcome is blank
- [ ] 10.1177/0956797609359908 | 10.17605/osf.io/4fvzq: outcome is blank
- [ ] 10.1086/666746 | 10.17605/osf.io/jhe6q: outcome is blank
- [ ] 10.1111/ajps.12074 | 10.17605/osf.io/swkur: outcome is blank
- [ ] 10.1086/670272 | 10.17605/osf.io/xue5d: outcome is blank
- [ ] 10.1177/0022002715612575 | 10.17605/osf.io/f3p2m: outcome is blank
- [ ] 10.1177/0022002708330289 | 10.17605/osf.io/xshrn: outcome is blank
- [ ] 10.1177/0022002710381064 | 10.17605/osf.io/wb524: outcome is blank
- [ ] 10.1177/0010414009332147 | 10.17605/osf.io/zfxk9: outcome is blank
- [ ] 10.1086/698750 | 10.17605/osf.io/eqyhz: outcome is blank
- [ ] 10.1177/0022002714553105 | 10.17605/osf.io/adxj2: outcome is blank
- [ ] 10.1111/cdev.12603 | 10.17605/osf.io/yfvzr: outcome is blank
- [ ] 10.1177/0010414015626446 | 10.17605/osf.io/rpc54: outcome is blank
- [ ] 10.1017/s0003055418000485 | 10.17605/osf.io/38zs9: outcome is blank
- [ ] 10.1086/693869 | 10.17605/osf.io/8xyhb: outcome is blank
- [ ] 10.1177/0010414014546331 | 10.17605/osf.io/6e9ka: outcome is blank
- [ ] 10.1111/j.1540-5907.2011.00567.x | 10.17605/osf.io/fspwc: outcome is blank
- [ ] 10.1093/sf/sou103 | 10.17605/osf.io/5ywth: outcome is blank
- [ ] 10.1007/s13524-018-0725-0 | 10.17605/osf.io/kup8x: outcome is blank
- [ ] 10.1111/ajps.12164 | 10.17605/osf.io/j6c8g: outcome is blank
- [ ] 10.1016/j.evolhumbehav.2016.06.001 | 10.17605/osf.io/anfk6: outcome is blank
- [ ] 10.1037/a0035615 | 10.17605/osf.io/7p5tw: outcome is blank
- [ ] 10.1177/0003122418785371 | 10.17605/osf.io/6ye5m: outcome is blank
- [ ] 10.1007/s10683-012-9342-8 | 10.17605/osf.io/4axu6: outcome is blank
- [ ] 10.1257/app.6.3.133 | 10.17605/osf.io/4p892: outcome is blank
- [ ] 10.1017/s0043887117000107 | 10.17605/osf.io/kzpf8: outcome is blank
- [ ] 10.31234/osf.io/jkfu3 | 10.17605/osf.io/zehky: outcome is blank
- [ ] 10.1101/2020.04.03.20052373 | 10.17605/osf.io/mshda: outcome is blank
- [ ] 10.31234/osf.io/k23cv | 10.17605/osf.io/pkwgx: outcome is blank
- [ ] 10.31234/osf.io/5gskw | 10.17605/osf.io/da26y: outcome is blank
- [ ] 10.31234/osf.io/rz78k | 10.17605/osf.io/54syu: outcome is blank
- [ ] 10.31234/osf.io/6a2cx | 10.17605/osf.io/w5b24: outcome is blank
- [ ] 10.1101/2020.05.12.20098699 | 10.17605/osf.io/2e5wr: outcome is blank
- [ ] 10.31234/osf.io/jrpfd | 10.17605/osf.io/xrymd: outcome is blank
- [ ] 10.1016/j.cognition.2017.12.004 | 10.17605/osf.io/yh25j: outcome is blank
- [ ] 10.1017/xps.2014.22 | 10.17605/osf.io/5vx27: outcome is blank
- [ ] 10.1037/xge0000466 | 10.17605/osf.io/u8zk5: outcome is blank
- [ ] 10.1177/0022002715587053 | 10.17605/osf.io/2uxrt: outcome is blank
- [ ] 10.31234/osf.io/m3vth | 10.17605/osf.io/8sae9: outcome is blank
- [ ] 10.1016/j.cognition.2016.03.003 | 10.17605/osf.io/8btme: outcome is blank
- [ ] 10.1086/684032 | 10.17605/osf.io/nekdp: outcome is blank
- [ ] 10.31234/osf.io/yb2h8 | 10.17605/osf.io/rkh2a: outcome is blank
- [ ] 10.3982/ecta8852 | 10.17605/osf.io/y5uwg: outcome is blank
- [ ] 10.1007/s10683-011-9295-3 | 10.17605/osf.io/2vust: outcome is blank
- [ ] 10.1177/0010414015621080 | 10.17605/osf.io/5k8m4: outcome is blank
- [ ] 10.1177/0956797620939054 | 10.17605/osf.io/9c5sm: outcome is blank
- [ ] 10.31234/osf.io/e3kcw | 10.17605/osf.io/r9bdx: outcome is blank
- [ ] 10.1086/662130 | 10.17605/osf.io/mjb97: outcome is blank
- [ ] 10.3982/ecta14370 | 10.17605/osf.io/8wf3h: outcome is blank
- [ ] 10.31234/osf.io/m7f8a | 10.17605/osf.io/jvwer: outcome is blank
- [ ] 10.1177/0022002712445741 | 10.17605/osf.io/6wfmz: outcome is blank
- [ ] 10.1016/j.foodqual.2023.105010 | 10.52825/gjae.v75i.3085: outcome is blank
- [ ] 10.1257/aer.20210369 | 10.1257/aer.20221432: outcome is blank
- [ ] 10.1257/0002828054201242 | 10.1002/jae.2922: outcome is blank
- [ ] 10.3982/ecta6248 | 10.1002/jae.2861: outcome is blank
- [ ] 10.1038/s41562-023-01655-0 | https://osf.io/7jy4e/files/u93pc: outcome is blank
- [ ] 10.1093/ej/ueab096 | https://osf.io/7qv89: outcome is blank
- [ ] 10.1093/restud/rdab056 | https://osf.io/8rb5k: outcome is blank
- [ ] 10.1177/0193723504273121 | https://econjwatch.org/File+download/538/FindlaySantosMay2012.pdf: outcome is blank
- [ ]  | 10.48550/arxiv.2604.00848: outcome is blank
- [ ] 10.1037/xge0000157 | https://osf.io/d3s25: outcome is blank
- [ ] 10.1007/s10683-012-9342-8 | https://osf.io/6xce3: outcome is blank
- [ ] 10.1016/j.jesp.2015.11.008 | https://osf.io/w5eb3: outcome is blank
- [ ] 10.1093/esr/jcq033 | https://osf.io/tsvz4: outcome is blank
- [ ] 10.1509/jmkr.47.2.348 | https://osf.io/za8vm: outcome is blank
- [ ] 10.1509/jmr.12.0430 | https://osf.io/ptjqy: outcome is blank
- [ ] 10.1016/j.jesp.2010.11.006 | https://osf.io/4mtgf: outcome is blank
- [ ] 10.1002/per.2062 | https://osf.io/ye5mq: outcome is blank
- [ ] 10.1037/pspi0000065 | https://osf.io/ayvzd: outcome is blank
- [ ] 10.1016/j.jesp.2016.11.014 | https://osf.io/ptzma: outcome is blank
- [ ] 10.5465/amj.2016.0086 | https://osf.io/bjfvk: outcome is blank
- [ ] 10.1101/2020.03.30.20048090 | https://osf.io/x7jrb: outcome is blank
- [ ] 10.1101/2020.05.14.20093633 | https://osf.io/rsn3b: outcome is blank
- [ ] 10.1086/700235 | https://osf.io/63bzr: outcome is blank
- [ ] 10.1093/restud/rdab087 | https://hdl.handle.net/10419/272845: outcome is blank
- [ ] 10.1093/restud/rdac011 | https://EconPapers.repec.org/RePEc:zbw:i4rdps:40: outcome is blank
- [ ] 10.1093/restud/rdab043 | https://EconPapers.repec.org/RePEc:zbw:i4rdps:42: outcome is blank
- [ ] 10.1093/qje/qjac001 | https://osf.io/a672c/: outcome is blank
- [ ] 10.1093/qje/qjab042 | https://github.com/atyho/Ottawa-Replication-Games-2023/blob/main/Ho_Huynh_Rea_Replication_Report.pdf: outcome is blank
- [ ] 10.1093/qje/qjac035 | https://osf.io/jgksb/: outcome is blank
- [ ] 10.1093/qje/qjac037 | https://osf.io/xadb6/: outcome is blank
- [ ] 10.1093/qje/qjac039 | https://hdl.handle.net/10419/303190: outcome is blank
- [ ] 10.1093/qje/qjab036 | https://hdl.handle.net/10419/268753: outcome is blank
- [ ] 10.1038/s41562-023-01538-4 | https://osf.io/cyb24/: outcome is blank
- [ ] 10.1038/s41562-023-01636-3 | https://osf.io/rt48s/: outcome is blank
- [ ] 10.1038/s41562-023-01712-8 | https://osf.io/9y47b/: outcome is blank
- [ ] 10.1086/717453 | https://hdl.handle.net/10419/286135: outcome is blank
- [ ] 10.1086/719277 | https://hdl.handle.net/10419/276960: outcome is blank
- [ ] 10.1086/717042 | https://hdl.handle.net/10419/268943: outcome is blank
- [ ] 10.1086/715252 | https://hdl.handle.net/10419/266181: outcome is blank
- [ ] 10.1086/714924 | https://hdl.handle.net/10419/280748: outcome is blank
- [ ] 10.1093/ej/uead061 | https://hdl.handle.net/10419/302899: outcome is blank
- [ ] 10.1093/ej/ueab076 | https://osf.io/bysa7/: outcome is blank
- [ ] 10.1017/s0003055422000107 | https://hdl.handle.net/10419/302283: outcome is blank
- [ ] 10.1017/s0003055423000308 | https://hdl.handle.net/10419/303906: outcome is blank
- [ ] 10.1017/s0003055421000691 | https://hdl.handle.net/10419/271676: outcome is blank
- [ ] 10.1017/s0003055421000940 | https://hdl.handle.net/10419/279959: outcome is blank
- [ ] 10.1017/s0003055421000800 | https://hdl.handle.net/10419/307157: outcome is blank
- [ ] 10.1111/ajps.12833 | https://hdl.handle.net/10419/306279: outcome is blank
- [ ] 10.1111/ajps.12583 | https://hdl.handle.net/10419/266385: outcome is blank
- [ ] 10.1111/ajps.12594 | https://hdl.handle.net/10419/280689: outcome is blank
- [ ] 10.1111/ajps.12624 | https://hdl.handle.net/10419/278117: outcome is blank
- [ ] 10.1111/ajps.12739 | https://hdl.handle.net/10419/305226: outcome is blank
- [ ] 10.1257/aer.20190433 | https://hdl.handle.net/10419/276252: outcome is blank
- [ ] 10.1257/aer.20190565 | https://hdl.handle.net/10419/279522: outcome is blank
- [ ] 10.1257/aer.20191927 | https://hdl.handle.net/10419/278108: outcome is blank
- [ ] 10.1257/aer.20161385 | https://hdl.handle.net/10419/302282: outcome is blank
- [ ] 10.1257/aer.20200751 | https://hdl.handle.net/10419/274139: outcome is blank
- [ ] 10.1257/aer.20202045 | https://hdl.handle.net/10419/302901: outcome is blank
- [ ] 10.1257/aer.103.4.1507 | https://osf.io/4qnf7/: outcome is blank
- [ ] 10.1257/pol.20180594 | https://hdl.handle.net/10419/280230: outcome is blank
- [ ] 10.1257/pol.20210812 | https://hdl.handle.net/10419/302900: outcome is blank
- [ ] 10.1257/pol.20200559 | https://hdl.handle.net/10419/267007: outcome is blank
- [ ] 10.1257/pol.20210129 | https://hdl.handle.net/10419/305228: outcome is blank
- [ ] 10.1257/pol.20200008 | https://osf.io/5yhxc/: outcome is blank
- [ ] 10.1177/09567976241231506 | https://osf.io/s4et7/: outcome is blank
- [ ] 10.1093/ej/ueae060 | https://osf.io/2ac69/: outcome is blank
- [ ] 10.1177/09567976231222288 | https://osf.io/ts2mp/: outcome is blank
- [ ] 10.1038/s41562-023-01691-w | https://osf.io/f5p78/: outcome is blank
- [ ] 10.1257/app.20210571 | https://osf.io/p9twk/: outcome is blank
- [ ] 10.1257/app.20130126 | https://osf.io/exb67/: outcome is blank
- [ ] 10.1017/xps.2014.22 | https://osf.io/8uc5z/files/jmk9d: outcome is blank

</details>

**Implausible years** (1 issue):

_year_o or year_r is outside 1890 to next year._

- [ ]  | 10.1371/journal.pone.0120644: year_o=1880

**Replication year before original year** (2 issues):

_Could indicate swapped entries or a data-entry error._

- [ ] 10.1002/bin.1836 | 10.1002/jaba.659: year_o=2021; year_r=2019
- [ ] 10.1016/j.cognition.2025.106257 | 10.31234/osf.io/j2xn3: year_o=2026; year_r=2024

<details><summary><b>Exact duplicates (by doi_o + doi_r)</b> (28 issues)</summary>

_Rows sharing the same doi_o and doi_r after deduplication._

- [ ] 10.1002/per.2062 | https://osf.io/udbnf
- [ ] 10.1002/per.2062 | https://osf.io/ye5mq
- [ ] 10.1016/j.jesp.2010.11.006 | https://osf.io/4mtgf
- [ ] 10.1016/j.jesp.2010.11.006 | https://osf.io/xr32n
- [ ] 10.1017/s0003055412000299 | https://osf.io/ceaq3
- [ ] 10.1017/s0003055412000299 | https://osf.io/ck8aq
- [ ] 10.1017/s0043887112000032 | https://osf.io/kw5tv
- [ ] 10.1017/s0043887112000032 | https://osf.io/qev3x
- [ ] 10.1037/a0012833 | 10.1126/science.aac4716
- [ ] 10.1037/xge0000157 | https://osf.io/5r28d
- [ ] 10.1037/xge0000157 | https://osf.io/d3s25
- [ ] 10.1086/698750 | https://osf.io/gvayj
- [ ] 10.1086/698750 | https://osf.io/rzny2
- [ ] 10.1111/j.1467-9280.2007.01909.x | 10.1098/rsos.231240
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://drive.google.com/file/d/1DDCuEgQah24QordxkRGO0EIt6Q681jyL/view
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://web.archive.org/web/20191117063540/http://www.psychfiledrawer.org/replication.php?attempt=MTQ5
- [ ] 10.1111/j.1467-9280.2008.02227.x | https://web.archive.org/web/20191201181415/http://psychfiledrawer.org/replication.php?attempt=MTc3
- [ ] 10.1111/j.1468-0068.2010.00795.x | 10.1007/s13164-018-0400-9
- [ ] 10.1163/156853706776931222 | 10.1007/s13164-018-0400-9
- [ ] 10.1177/0956797615624492 | 10.1098/rsos.231240
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

- [ ] 10.1098/rsos.231240: 95 different reference strings (doi_r/apa_ref_r)

---

**Checks passed:**
- [x] No 'NA'/'N/A' strings
- [x] Type values valid
- [x] Source values valid


## FLoRA Network Validation Report

_Generated: 2026-09-15 17:52:05 UTC | Dataset: 2914 rows_


**Network validation status: skipped**


Only the selected checks ran: none.


## Prepared release

- File: flora.csv
- SHA-256: `d7a54f14acf016260199e3b5bcb0104e175cb0d0c5ee7e5e53b5477d84ebb6e4`
- Publication: local files only; no OSF or GitHub release.

## Dataset history

The website records daily dataset counts in its shared history table. This run also writes a CSV history and a Markdown summary alongside the release.
