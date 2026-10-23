# FLoRA preparation report

Generated: 2026-10-23T03:15:13+00:00

Status: **needs_attention**

Mode: database_pipeline

Rows: 3,097

## Stages

- final CSV: passed
- structural validation: needs_attention — 211 data issue(s)
- network validation: incomplete
- release preparation and history: passed
- store prepared FLoRA table: passed — 3097 active rows in flora_data
- publish CSV artifact: passed

## Items needing attention

- 29 rows omitted because a title is missing; see flora_export_log.csv.
- 60 merged groups with conflicting outcomes; see dup_outcome_conflicts.csv.
- 20 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates); see preprint_dedup_candidates.csv.
- Structural validation found 211 data issue(s).
- Network validation: incomplete; 81 finding(s).

## Stored dataset

- Database status: committed.
- Active rows in flora_data: 3,097.

## Transform diagnostics

- flora_export_log.csv: 29 rows omitted because a title is missing.
- dup_outcome_conflicts.csv: 60 merged groups with conflicting outcomes.
- preprint_dedup_candidates.csv: 20 preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates).

The JSON report includes the affected records and reasons.

## FLoRA Data Validation Report

_Generated: 2026-10-23 03:21:03 UTC | Dataset: 3097 rows_

Each row is identified as `doi_o | doi_r (or url_r)`, followed by check-specific detail after `:`.
Tick an item to mark it a false positive; it is suppressed on the next run.

<details><summary><b>Invalid URLs (not starting with http)</b> (126 issues)</summary>

_A url column holds something that is not a link._

- [ ] 10.1111/j.2047-2927.2012.00013.x | 10.1177/1042258720985478: url_o='10.1111/j.2047-2927.2012.00013.x'
- [ ] 10.1016/j.jml.2020.104174 | 10.31234/osf.io/kt3sw_v2: url_r='10.1037/xlm0001650'
- [ ] 10.1257/aer.20131377 | 10.1257/aer.20200679: url_o='When Does Regulation Distort Costs? Lessons from Fuel Procurement in US Electricity Generation'
- [ ] 10.3982/ecta14176 | 10.1002/jae.2850: url_o='A One Covariate at a Time, Multiple Testing Approach to Variable Selection in High-Dimensional Linear Regression Models'
- [ ] 10.1162/rest.90.4.777 | 10.1007/s00181-019-01708-2: url_o='Predicting U.S. Recessions with Dynamic Binary Response Models'
- [ ] 10.1016/s0304-3932(99)00026-4 | 10.1007/s00181-019-01778-2: url_o='Does the time-consistency problem explain the behavior of inflation in the United States?'
- [ ] 10.1257/aer.99.3.827 | 10.1002/jae.2814: url_o='Tricks with Hicks: The EASI Demand System'
- [ ] 10.1162/qjec.2009.124.2.531 | 10.1002/jae.2851: url_o='Was Weber Wrong? A Human Capital Theory of Protestant Economic History<sup>*</sup>'
- [ ] 10.1002/jae.615 | 10.1002/jae.2760: url_o='Modelling UK inflation, 1875–1991'
- [ ] 10.1257/aer.103.4.1212 | 10.1257/aer.20162011: url_o='The Dynamic Effects of Personal and Corporate Income Tax Changes in the United States'
- [ ] 10.1002/jae.2503 | 10.1002/jae.2695: url_o='Bubbles and Crises: The Role of House Prices and Credit'
- [ ] 10.1162/rest_a_00272 | 10.1002/jae.2662: url_o='Do Spillovers Matter When Estimating Private Returns to R&amp;D?'
- [ ] 10.2307/2109768 | 10.1002/jae.2648: url_o='The Solution and Estimation of Discrete Choice Dynamic Programming Models by Simulation and Interpolation: Monte Carlo Evidence'
- [ ] 10.1093/qje/qjw025 | 10.1080/19439342.2019.1666900: url_o='The Short-term Impact of Unconditional Cash Transfers to the Poor: Experimental
 Evidence from Kenya*'
- [ ] 10.1016/s0140-6736(07)60312-2 | 10.1080/00220388.2018.1506580: url_o='Male circumcision for HIV prevention in young men in Kisumu, Kenya: a randomised controlled trial'
- [ ] 10.1016/j.jpubeco.2010.06.002 | 10.1080/00220388.2018.1506576: url_o='Property rights for the poor: Effects of land titling'
- [ ] 10.1016/j.ecosys.2015.04.004 | 10.1111/joes.12352: url_o='Systemic event prediction by an aggregate early warning system: An application to the Czech Republic'
- [ ] 10.1016/s0140-6736(12)60730-2 | 10.1371/journal.pone.0206677: url_o='Task shifting of antiretroviral treatment from doctors to primary-care nurses in South Africa (STRETCH): a pragmatic, parallel, cluster-randomised trial'
- [ ] 10.1257/aer.102.1.428 | 10.1257/aer.20170413: url_o='Testing Efficient Risk Sharing with Heterogeneous Risk Preferences'
- [ ] 10.1257/mac.3.3.92 | 10.5018/economics-ejournal.ja.2018-3: url_o='Education and Catch-up in the Industrial Revolution'
- [ ] 10.1002/jae.2312 | 10.1002/jae.2651: url_o='Senior Role'
- [ ] 10.1162/qjec.2009.124.2.469 | 10.1002/jae.2622: url_o='The Diffusion of Development<sup>*</sup>'
- [ ] 10.1162/rest.89.1.183 | 10.1002/jae.2591: url_o='Factor Substitution and Factor-Augmenting Technical Progress in the United States: A Normalized Supply-Side System Approach'
- [ ] 10.1257/aer.20121546 | 10.1257/aer.20150891: url_o='The Cyclicality of Sales, Regular and Effective Prices: Business Cycle and Policy Implications'
- [ ] 10.1257/aer.102.5.1927 | 10.1257/aer.20150988: url_o='The Impact of Family Income on Child Achievement: Evidence from the Earned Income Tax Credit'
- [ ] 10.1177/000312240907400203 | 10.1177/0003122417714422: url_o='Does Diversity Pay?: Race, Gender, and the Business Case for Diversity'
- [ ] 10.1257/aer.103.1.277 | 10.1007/s00181-016-1118-0: url_o='Innovation and Institutional Ownership'
- [ ] 10.1257/aer.102.2.1029 | 10.1002/jae.2554: url_o='Credit Booms Gone Bust: Monetary Policy, Leverage Cycles, and Financial Crises, 1870–2008'
- [ ] 10.1086/527495 | 10.1002/jae.2540: url_o='Corruption, Norms, and Legal Enforcement: Evidence from Diplomatic Parking Tickets'
- [ ] 10.3386/w18992 | 10.1002/jae.2526: url_o='Subjective Well-Being and Income: Is There Any Evidence of Satiation?'
- [ ] 10.1257/aer.98.4.1591 | 10.1002/jae.2563: url_o='The Cycle of Violence? An Empirical Analysis of Fatalities in the Palestinian-Israeli Conflict'
- [ ] 10.1257/aer.104.6.1630 | 10.1002/jae.2558: url_o='US Food Aid and Civil Conflict'
- [ ] 10.2139/ssrn.1756581 | 10.1111/sjoe.12209: url_o='Ethnic Diversity and Preferences for Redistribution'
- [ ] 10.2139/ssrn.1908409 | 10.1002/jae.2432: url_o='Lasso Methods for Gaussian Instrumental Variables Models'
- [ ] 10.2202/1558-3708.1832 | 10.1002/jae.2434: url_o='A Computationally Practical Robust Simulation Estimator for Dynamic Panel Tobit Models'
- [ ] 10.1016/s2214-109x(14)70025-7 | 10.1016/s2214-109x(16)30250-9: url_o='Association between economic growth and early childhood undernutrition: evidence from 121 Demographic and Health Surveys from 36 low-income and middle-income countries'
- [ ] 10.1016/j.ejpoleco.2003.10.001 | 10.1177/1091142114568659: url_o='Meta-analysis of the effect of fiscal policies on long-run growth'
- [ ] 10.1086/605565 | 10.1177/1091142115605380: url_o='Political Influence behind the Veil of Peer Review: An Analysis of Public Biomedical Research Funding in the United States'
- [ ] 10.2308/accr.2009.84.1.183 | 10.1177/1091142115580130: url_o='Why Do Cities Hoard Cash? Determinants and Implications of Municipal Cash Holdings'
- [ ] 10.1111/j.1468-0297.2011.02482.x | 10.1177/1091142114537895: url_o='Counting Chickens when they Hatch: Timing and the Effects of Aid on Growth'
- [ ] 10.1257/aer.97.5.1939 | 10.1257/aer.104.4.1446: url_o='Heterogeneity and Aggregation: Implications for Labor-Market Fluctuations'
- [ ] 10.1257/aer.101.6.2530 | 10.1257/aer.104.12.4231: url_o='Risk Matters: The Real Effects of Volatility Shocks'
- [ ] 10.1257/aer.101.4.1514 | 10.1257/aer.104.1.323: url_o='Term Premia and Inflation Uncertainty: Empirical Evidence from an International Panel Dataset'
- [ ] 10.1257/aer.98.1.519 | 10.1257/aer.104.3.1072: url_o='The Dynamic Behavior of the Real Exchange Rate in Sticky Price Models'
- [ ] 10.3386/w15639 | 10.1093/cje/bet075: url_o='Growth in a Time of Debt'
- [ ] 10.1257/0002828042002651 | 10.3386/w17034: url_o='A New Measure of Monetary Shocks: Derivation and Implications'
- [ ] 10.1257/aer.98.1.439 | 10.1257/aer.102.7.3774: url_o='Growth Dynamics: The Myth of Economic Recovery'
- [ ] 10.1257/aer.96.4.1013 | 10.1257/aer.102.4.1751: url_o='Taxes, Cigarette Consumption, and Smoking Intensity'
- [ ] 10.1257/aer.91.5.1369 | 10.1257/aer.102.6.3059: url_o='The Colonial Origins of Comparative Development: An Empirical Investigation'
- [ ] 10.3982/ecta6224 | 10.1002/jae.1277: url_o='Generalized Method of Moments With Many Weak Moment Conditions'
- [ ] 10.1162/0033553053970214 | 10.1002/jae.1262: url_o='Competition and Innovation: An Inverted-U Relationship*'
- [ ] 10.3386/w9682 | 10.1002/jae.1245: url_o='Religion and Economic Growth'
- [ ] 10.1257/mac.2.4.222 | 10.1002/jae.2265: url_o='Determinants of Economic Growth: Will Data Tell?'
- [ ] 10.1073/pnas.1002632107 | 10.1073/pnas.1202049109: url_o='Linkages among climate change, crop yields and Mexico–US cross-border migration'
- [ ] 10.1257/000282803322655554 | 10.1257/aer.101.4.1629: url_o='Returns to Education: Evidence from U.K. Twins'
- [ ] 10.1257/aer.98.3.938 | 10.1257/aer.101.2.1029: url_o='Stationary Concepts for Experimental 2x2-Games'
- [ ] 10.2202/1553-3832.1170 | 10.1080/00036841003670804: url_o='The Death Penalty: No Evidence for Deterrence'
- [ ] 10.2307/1923892 | 10.1080/09603107.2011.564130: url_o='An Empirical Demonstration of Classical Comparative Cost Theory'
- [ ] 10.1002/jae.1020 | 10.1002/jae.1180: url_o='Rough and lonely road to prosperity: a reexamination of the sources of growth in Africa using Bayesian model averaging'
- [ ] 10.1162/0034653041811770 | 10.1002/jae.1264: url_o='Estimating the Elasticity of Intertemporal Substitution When Instruments Are Weak'
- [ ] 10.1111/j.1538-4616.2008.00172.x | 10.1111/j.1538-4616.2011.00406.x: url_o='Political Regimes and the Cost of Disinflation'
- [ ] 10.1162/qjec.2010.125.2.591 | 10.1093/qje/qjr042: url_o='Estimating Marginal Returns to Medical Care: Evidence from At-Risk Newborns<sup>*</sup>'
- [ ] 10.2139/ssrn.1332768 | 10.1162/rest_a_00173: url_o='Risk and Default: Understanding Macro Drivers of Bankruptcy'
- [ ] 10.1257/00028280260344597 | 10.1257/aer.100.3.1280: url_o='Antidumping Investigations and the Pass-Through of Antidumping Duties and Exchange Rates'
- [ ] 10.1111/j.0012-9682.2008.00818.x | 10.3982/ecta7970: url_o='Experientia Docet: Professionals Play Minimax in Laboratory Experiments'
- [ ] 10.1162/154247603770383415 | 10.1002/jae.1131: url_o='An Estimated Dynamic Stochastic General Equilibrium Model of the Euro Area'
- [ ] 10.1198/016214501753168235 | 10.1002/jae.1200: url_o='Inference for Density Families Using Functional Principal Component Analysis'
- [ ] 10.1002/(sici)1099-1255(199901/02)14:1<57::aid-jae501>3.0.co;2-g | 10.1002/jae.1191: url_o='Jackknife instrumental variables estimation'
- [ ] 10.1002/jae.929 | 10.1002/jae.1186: url_o='Nonparametric estimation of a hedonic price function'
- [ ] 10.1016/j.red.2005.03.004 | 10.1016/j.red.2009.08.002: url_o='Labor market search, sticky prices, and interest rate policies'
- [ ] 10.17016/ifdp.1995.498 | 10.1257/mac.1.1.219: url_o='How Wide is the Border?'
- [ ] 10.1257/0002828043052303 | 10.1257/aer.99.4.1672: url_o='Gibrat's Law for (All) Cities'
- [ ] 10.1080/1350485032000126712 | 10.1080/00036840802112471: url_o='Economic impact of national sporting success: evidence from the London stock exchange'
- [ ] 10.1002/(sici)1099-1255(199707)12:4<435::aid-jae430>3.0.co;2-1 | 10.1002/jae.1098: url_o='Is there a unit root in the inflation rate? Evidence from sequential break and panel data models'
- [ ] 10.1257/0002828053828699 | 10.1002/jae.1093: url_o='Financial Reform: What Shakes It? What Shapes It?'
- [ ] 10.3386/w8004 | 10.1162/rest.91.1.112: url_o='The Impact of Legalized Abortion on Crime'
- [ ] 10.3386/w8004 | 10.1162/qjec.2008.123.1.407: url_o='The Impact of Legalized Abortion on Crime'
- [ ] 10.1002/(sici)1099-1255(199803/04)13:2<185::aid-jae478>3.0.co;2-1 | 10.1002/jae.876: url_o='Does more calculus improve student learning in intermediate micro- and macroeconomic theory?'
- [ ] 10.1002/(sici)1099-1255(200001/02)15:1<59::aid-jae544>3.0.co;2-1 | 10.1002/jae.895: url_o='Asymptotically perfect and relative convergence of productivity'
- [ ] 10.2307/2109893 | 10.1002/jae.861: url_o='Estimating the Economic Model of Crime with Panel Data'
- [ ] 10.1257/000282802760015757 | 10.1257/000282805775014353: url_o='Does Increasing Women's Schooling Raise the Schooling of the Next Generation?'
- [ ] 10.2307/2585837 | 10.1017/s0003055405051658: url_o='The Effects of Canvassing, Telephone Calls, and Direct Mail on Voter Turnout: A Field Experiment'
- [ ] 10.1002/jae.659 | 10.1002/jae.856: url_o='Computation and analysis of multiple structural change models'
- [ ] 10.1596/1813-9450-1690 | 10.1080/0269217032000148645: url_o='Stock Markets, Banks, and Economic Growth'
- [ ] 10.1086/260646 | 10.1002/jae.777: url_o='A Theory of Extramarital Affairs'
- [ ] 10.1002/jae.644 | 10.1002/jae.702: url_o='A structural model of US aggregate job flows'
- [ ] 10.1086/260646 | 10.1002/jae.699: url_o='A Theory of Extramarital Affairs'
- [ ] 10.2307/1913712 | 10.1198/073500102753410372: url_o='The Great Crash, the Oil Price Shock, and the Unit Root Hypothesis'
- [ ] 10.1257/aer.90.4.847 | 10.1080/713601081: url_o='Aid, Policies, and Growth'
- [ ] 10.1086/467988 | 10.1086/323313: url_o='Crime, Deterrence, and Right‐to‐Carry Concealed Handguns'
- [ ] 10.2307/1911060 | 10.1002/jae.3950050408: url_o='Efficient Estimation Using Panel Data'
- [ ] 10.2307/1925043 | 10.2307/1924234: url_o='Population and Economic Growth: A World Cross-Section Study'
- [ ] 10.1093/cje/bet075 | 10.1093/jeg/lbu005: url_o='Does high public debt consistently stifle economic growth? A critique of Reinhart and Rogoff'
- [ ] 10.1162/rest_a_00949 | 10.1002/jae.3117: url_o='Growth Expectations, Undue Optimism, and Short-Run Fluctuations'
- [ ] 10.1093/restud/rdaa074 | 10.1111/ecin.13242: url_o='Understanding Cultural Persistence and Change'
- [ ] 10.1257/pol.20170144 | 10.1515/econ-2022-0109: url_o='Carbon Taxes and CO2 Emissions: Sweden as a Case Study'
- [ ] 10.1162/rest_a_00754 | 10.1002/jae.3073: url_o='Robots at Work'
- [ ] 10.1093/qje/qjt005 | 10.1002/jae.3083: url_o='On the Origins of Gender Roles: Women and the Plough
 *'
- [ ] 10.1257/aer.20210369 | 10.1257/aer.20221432: url_o='RETRACTED BY THE AUTHORS: Dividend Taxes and the Allocation of Capital'
- [ ] 10.1093/ej/ueab089 | 10.1111/caje.12693: url_o='Pre-Colonial Warfare and Long-Run Development in India'
- [ ] 10.1257/mac.20170367 | 10.1111/ecin.13185: url_o='A Model of Secular Stagnation: Theory and Quantitative Evaluation'
- [ ] 10.18651/rwp2018-04 | 10.1515/econ-2022-0036: url_o='The Effect of the Conservation Reserve Program on Rural Economies: Deriving a Statistical Verdict from a Null Finding'
- [ ] 10.1111/ecoj.12493 | 10.1002/jae.3021: url_o='Paid Parental Leave and Children's Schooling Outcomes'
- [ ] 10.1086/717042 | 10.1086/725235: url_o='Cooperative Property Rights and Development: Evidence from Land Reform in El Salvador'
- [ ] 10.1257/aer.20190687 | 10.1257/aer.20210121: url_o='Methods Matter: p-Hacking and Publication Bias in Causal Analysis in Economics'
- [ ] 10.1257/0002828054201242 | 10.1002/jae.2922: url_o='Do Rural Banks Matter? Evidence from the Indian Social Banking Experiment'
- [ ] 10.2139/ssrn.2846322 | 10.1177/10911421221101690: url_o='Trade Shocks and the Provision of Local Public Goods'
- [ ] 10.1016/j.htct.2020.10.165 | 10.1007/s13209-022-00260-0: url_o='RELATO DE CASO DE PACIENTE PORTADOR DE ANEMIA FALCIFORME COM PERSISTÊNCIA DA HEMOGLOBINA FETAL E PREDISPOSIÇÃO À TROMBOSE'
- [ ] 10.1093/restud/rdv039 | 10.1093/restud/rdab037: url_o='Trade Induced Technical Change? The Impact of Chinese Imports on Innovation, IT and Productivity'
- [ ] 10.1111/j.1467-9477.2007.00176.x | 10.1086/683144: url_o='<i>E Pluribus Unum</i>: Diversity and Community in the Twenty‐first Century The 2006 Johan Skytte Prize Lecture'
- [ ] 10.1596/1813-9450-5159 | 10.1007/s00181-014-0902-y: url_o='Downward Nominal And Real Wage Rigidity: Survey Evidence From European Firms'
- [ ] 10.1016/j.jeconom.2010.03.040 | 10.1002/jae.2424: url_o='A spatio-temporal model of house prices in the USA'
- [ ] 10.1111/j.1468-0297.2007.02027.x | 10.1002/jae.2392: url_o='The International Dynamics of R&amp;D and Innovation in the Long Run and in the Short Run'
- [ ] 10.1596/1813-9450-2108 | 10.1080/01419870.2012.762106: url_o='Public Goods and Ethnic Divisions'
- [ ] 10.1007/s10683-011-9283-7 | 10.1007/s10683-013-9375-7: url_o='Dictator games: a meta study'
- [ ] 10.1111/j.1468-0297.2008.02208.x | 10.1002/jae.2366: url_o='Measuring Financial Asset Return and Volatility Spillovers, with Application to Global Equity Markets'
- [ ] 10.1002/jae.1020 | 10.1002/jae.2365: url_o='Rough and lonely road to prosperity: a reexamination of the sources of growth in Africa using Bayesian model averaging'
- [ ] 10.1086/250037 | 10.2139/ssrn.2231535: url_o='The impact of Group‐Based Credit Programs on Poor Households in Bangladesh: Does the Gender of Participants Matter?'
- [ ] 10.1515/jbnst-2013-0402 | 10.1515/jbnst-2014-0504: url_o='Unemployment Compensation and Wages: Evidence from the German Hartz Reforms'
- [ ] 10.1111/obes.12084 | 10.1007/s00181-019-01663-y: url_o='The Low‐Pay No‐Pay Cycle: Are There Systematic Differences across Demographic Groups?'
- [ ] 10.1093/acprof:oso/9780199562091.003.0013 | 10.3386/w15553: url_o='Heat engines and the second law'
- [ ]  | 10.15626/mp.2025.4708: url_o='www.proquest.com/docview/1470800730'
- [ ]  | 10.1086/719757: url_o='Is the 1918 Influenza Pandemic Over? Long‐Term Effects of <i>In Utero</i> Influenza Exposure in the Post‐1940 U.S. Population'
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025: url_o='thekeep.eiu.edu/theses/4783'
- [ ] thekeep.eiu.edu/theses/4783 | https://thekeep.eiu.edu/theses/5025/?utm_source=thekeep.eiu.edu%2Ftheses%2F5025: url_o='thekeep.eiu.edu/theses/4783'
- [ ] 10.1080/07350015.1995.10524601 | 10.1002/jae.2458: url_o='Lag Order and Critical Values of the Augmented Dickey-Fuller Test'

</details>

**Invalid outcome values** (3 issues):

_An outcome outside the allowed set for the row's type, or missing._

- [ ] 10.1002/j.2164-585x.1979.tb00097.x | 10.1353/csd.2014.0018: outcome is blank
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
- [ ]  | 10.1287/mnsc.2022.4463: missing doi_o
- [ ]  | 10.1257/aer.101.4.1616: missing doi_o
- [ ]  | 10.1561/104.00000126: missing doi_o
- [ ]  | 10.1007/s10683-014-9405-0: missing doi_o
- [ ]  | 10.1007/bf01205784: missing doi_o
- [ ]  | 10.1016/s0272-7757(98)00038-7: missing doi_o
- [ ]  | 10.15626/mp.2025.4708: missing doi_o
- [ ]  | https://hdl.handle.net/20.500.14721/40318: missing doi_o
- [ ]  | 10.48550/arxiv.2604.00848: missing doi_o
- [ ]  | 10.3233/jem-2006-0262: missing doi_o
- [ ]  | 10.1111/j.1465-7295.1998.tb01693.x: missing doi_o
- [ ]  | 10.1086/260511: missing doi_o
- [ ]  | 10.1086/719757: missing doi_o
- [ ]  | 10.5406/bulcouresmusedu.193.0045: missing doi_o

</details>

**Invalid DOI format** (9 issues):

_DOI does not match 10.NNNN/..._

- [ ] 0.1162/0033553053327524 | 10.1002/jae.1078: doi_o='0.1162/0033553053327524'
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
- [x] Text fields hold text
- [x] Type values valid
- [x] Source values valid


## FLoRA Network Validation Report

_Generated: 2026-10-23 03:21:03 UTC | Dataset: 3097 rows_

**Retracted papers** (1 issue):

- [ ] SKIPPED: could not download the Retraction Watch database (HTTP Error 502: Bad Gateway)

<details><summary><b>DOIs that failed to resolve</b> (35 issues)</summary>

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
- [ ] `10.17705/1atrr.00061`

</details>

<details><summary><b>URLs that failed to resolve</b> (45 issues)</summary>

- [ ] `https://hrcak.srce.hr/file/334421`
- [ ] `http://hdl.handle.net/1820/9055`
- [ ] `http://orbilu.uni.lu/handle/10993/18672`
- [ ] `https://dash.harvard.edu/bitstreams/7312037d-191b-6bd4-e053-0100007fdf3b/download`
- [ ] `http://www.collabra.org/articles/10.1525/collabra.216/galley/263/download/`
- [ ] `http://www.collabra.org/articles/10.1525/collabra.117/galley/146/download/`
- [ ] `http://e-spacio.uned.es/fez/view/bibliuned:DptoMIDEII-FEDU-Articulos-Jcperez-0006`
- [ ] `https://openalex.org/W7113264659`
- [ ] `http://elibrary.aisnet.org/Default.aspx?url=https://aisel.aisnet.org/cgi/viewcontent.cgi?article=1006&context=trr`
- [ ] `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0198620/1/pone.0198620.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Credential=wombat-sa%40plos-prod.iam.gserviceaccount.com%2F20210216%2Fauto%2Fstorage%2Fgoog4_request&X-Goog-Date=20210216T121734Z&X-Goog-Expires=3600&X-Goog-SignedHeaders=host&X-Goog-Signature=3a730412736db07da92d2cbb40223611403deceedf4c4ab529ddf4e5b8a860d786570225c2de9600d35dc629d57a660246ac9253b8f99072ab5dc9a80567f81f485d411e250713b8be101c0dee2513a00e2d03ecc80f33de21a761f1fedc45ebdea3ddc53fe6517f06eca371a7f7adaec25056b2c6917bb4f238ed81004578297007e6bef4bcc3af13b93508ccfecf2555ec254babd57c8b15e92a9cfd500a5fd8b1c81f92fdb70874012974d676dbc65974e25bf291f0851a6ac186cb1c5681c9326808c4baf9a0ddfd938c18c571d3e245c17df563554cef21ac564e57881f9f9d9c849efa122d536b4441caa7b213777510d9c7b43b16dfccb17381a049e3`
- [ ] `https://biblio.ugent.be/publication/8707609`
- [ ] `http://orbilu.uni.lu/handle/10993/45413`
- [ ] `https://cdr.lib.unc.edu/downloads/7w62fg56x`
- [ ] `http://libres.uncg.edu/ir/asu/f/Eshenbaugh_Brooklyn Spring 2018 Thesis.pdf`
- [ ] `https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0209263/1/pone.0209263.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Credential=wombat-sa%40plos-prod.iam.gserviceaccount.com%2F20210221%2Fauto%2Fstorage%2Fgoog4_request&X-Goog-Date=20210221T170527Z&X-Goog-Expires=3600&X-Goog-SignedHeaders=host&X-Goog-Signature=738cc051df73116169d6a637a6e0d613b3b424dd19f9cc1a2a0652ef9604ca8fb5d4d361df382b5d7c408c77fb4f6509545dcdfe2486dfa37634a35d560bbd4a57d917d70d2ab3ca1c3cd77168c0c56f976f4fa1508305c5e0213370e05fc7f24e3297fcd30822a3f663542a8c941c479901db91978ed30b7f2fe9d211537ee88bc41071cefffe9a060d0fa72322d2d91a19a2d55034266269fe25ef495d4b5c182c0b67d21bc7986eac8096381703773fcae76c9865505177b5f854ea0d25080e05671cc0510339cc237b5ff5a1509b8ac9703d3c6b51bb6dc7a9528bbcd13f1c9195ed9db5302e257d52915013cf67847abac8dfa22d54e5b829452b1e261e`
- [ ] `http://hdl.handle.net/2328/35450`
- [ ] `http://www.economics-ejournal.org/dataset/PDFs/journalarticles_2019-51.pdf`
- [ ] `http://eprints.keele.ac.uk/2958/1/M%20Noor%20-%20Intergroup%20forgiveness%20and%20reconciliation.pdf`
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


**Network validation status: incomplete**

- dois: 5129 targets; 5064 cached; 65 checked; 0 deferred.
- urls: 3298 targets; 3239 cached; 59 checked; 0 deferred.


## Prepared release

- File: flora.csv
- SHA-256: `e9ef7c1ba1a6dcf3263a76f9069664441ad509cb135b5d6705a04639b85c3717`
- Publication: local files only; no OSF or GitHub release.

## Dataset history

The website records daily dataset counts in its shared history table. This run also writes a CSV history and a Markdown summary alongside the release.
