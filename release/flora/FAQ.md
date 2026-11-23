# FAQs for the FORRT Library of Reproduction and Replication Attempts (FLoRA)

## Coding and Validation

### Are replications with synthetic data included?

Yes. Although some users of the data may not find repeated tests informative, others may. 

### What is the outcome based on?

First establish which central empirical claim or claims of the original study the replication was designed to re-test. Re-testing **a** central empirical claim is sufficient: the replication need not cover every central claim of the original paper, and replication may be one of several aims of the new paper.

Within that intended replication scope, we rely on the replication authors' explicit overall assessment, usually in the abstract or otherwise in the discussion or conclusion. That assessment takes precedence over our own reading of individual tests, including their statistical significance. A statement confirming one effect is an overall verdict if that is the only claim being replicated; it is not an overall verdict on a broader attempt that also targets other claims. A generally positive statement about the paper or its contribution is not a replication verdict.

If there is no relevant overall assessment, use the authors' reported comparisons with the original within the intended scope: agreement throughout is successful, disagreement throughout is failed, and a combination is mixed. Agreement concerns what the original reported, so confirming an original null finding can be successful. Missing evidence is not evidence of success or failure.

Robustness checks, exploratory analyses, and extensions outside the intended replication scope do not change this outcome merely because the paper compares them with the original. When coding from comparisons because no relevant overall assessment is available, include an interaction or another finding that is part of the intended replication scope. This does not override an explicit overall author assessment of that scope. Establish the scope from the stated aims and design, not by selecting whichever results agree; preregistration is not required.

If studies are part of a meta-paper, use the individual reports where available, and otherwise the main criterion used by the meta-paper's authors. Each original–replication reference pair has its own outcome; do not transfer another original's verdict to it.

Use *statistically successful but flawed* when the authors obtain the original effect but argue that their own or the original's method does not validly test the claim, for example because the effect can be explained by regression to the mean. Use it only when that critique is the paper's main message; a replication that merely notes limitations is successful.

### What is the outcome if the manipulation check failed so the hypothesis could not be tested?

Apply the authors' explicit assessment of the intended replication scope first, including an explicit assessment that the attempt was uninformative. Where no such assessment is available, a failed manipulation or preliminary test that prevents the hypothesis from being tested is coded as failed. The same fallback applies if the manipulation check succeeded but the main test failed. Equivalent cases include a failed preliminary gender-knowledge-gap test before a stereotype-threat test.

Where there is no relevant overall assessment and the intended replication scope covers one title-focused effect that did not replicate, code failed regardless of successful checks outside that scope. If the stated aims target additional claims, include their outcomes in the comparison rather than let the title alone override them. These are fallback rules, not reasons to override an explicit relevant author verdict using our own significance criterion.

### How are you coding if the close replication works but the conceptual replication doesn’t?

Where both attempts fall within the intended replication scope for the same original and there is no explicit overall author assessment, code *mixed*. An explicit overall assessment of that scope takes precedence, as described above. A conceptual extension outside that scope does not automatically change the replication outcome.

### If the data is overlapping (e.g., “using an additional decade of data”), is it a reproduction or a replication?

Because there are *additional* observations, we are coding it as a replication.

### What if there is an error in the coding but with it, results are reproducible?

**Example**: “Replication shows that the empirical results are driven exclusively by two countries with weak upper houses, one of which (Ireland) is miscoded. If this case is recoded there is no statistically or substantially significant effect of upper houses, regardless of their power. We conclude that the two questions cannot yet be answered”

This would be coded as computational reproducible but not robust.



### Are reanalyses included if they do not test for numerical reproduction? What about multiverse analysis?

Yes, as long as they test the same claim as the original study. This could also mean that a reproduction is a multiverse analysis. Reproduction outcomes are recorded separately in `outcome_computation` and `outcome_robustness`. The `outcome` field records replication outcomes and is `NA` for reproduction rows.

### What if a paper includes reproduction *and* replication?

Then it will be listed twice in FLoRA, once as a replication and once as a reproduction.

### What level is the database?

Each row is one pair of references. A replication study can have multiple studies but their results are aggregated (e.g., based on the authors’ judgment, who may report aggregated results or conflicting results, which we consider as mixed).

### When is a finding mixed?

Use *mixed* when the authors' overall assessment of the intended replication scope is partial or mixed. Without an overall assessment, use mixed when their comparisons within that scope contain both agreement and disagreement. A smaller effect size, a failed exploratory analysis, or a robustness difference outside that scope is not by itself grounds for mixed.

For example:

| Evidence | Outcome |
| --- | --- |
| The stated replication aims cover a main effect and an interaction; the authors report that only the main effect replicated and give no other overall verdict. | Mixed. |
| The stated replication aim covers a main effect, which replicated; an exploratory interaction did not. | Successful; the exploratory result is outside the replication scope. |
| The authors explicitly conclude that the intended replication supports the target claim, despite an individual nonsignificant test. | Successful; record their relevant verdict rather than substitute a significance rule. |
| The title and aims target an effect that failed; other checks succeeded, but there is no relevant overall verdict. | Failed. |
| The authors explicitly conclude that the attempt cannot adjudicate the target claim because its sample was too small. | Uninformative; this is the authors' assessment, not missing evidence on the coder's side. |

For meta-papers, use individual reports where available and otherwise the main criterion used by the meta-paper's authors.

Use *descriptive only* only when the authors state an intended re-test of a central claim but explicitly present the results descriptively without assessing agreement with the original. Missing comparison evidence alone does not justify an outcome code; flag the pair for source review or re-coding. Pure method reuse without an intended re-test does not qualify for inclusion.

### How are you dealing with Preprints?

We focus on the version of the record (e.g., version published in a journal), but code alternative identifiers (e.g. preprints) in alt\_identifier\_o and alt\_identifier\_r columns so that these can still be discovered in the database. For original studies, we always choose the reference from the article. If that is not available, we choose the most recent version.

### What if a study is published individually and as part of a meta-paper?

We have multiple identifiers for original and replication studies and our main reference will be to \#1 the published version and \#2 to the more specific version. Studies that have been published individually and in a meta-paper will have the specific published version in doi\_r and the other alt\_identifier\_r. For example, [https://doi.org/10.1017/xps.2022.35](https://doi.org/10.1017/xps.2022.35) is published individually but also as part of the SCORE meta-paper and as a single SCORE report. It is entered as the published version into the database with the SCORE DOI and the report URL as alternative identifiers.

### How are meta-papers treated?

If multiple entries (e.g., studies) are part of a meta paper

* We still have 1 row per original \- replication combination  
* For that, we prefer published versions for doi\_r; if individual studies have been published, then we add these as doi\_r and the meta-paper into alt\_identifier\_r. Otherwise, the main publication is added as doi\_r, and the individual reports or preprints as alt\_identifier\_r.

### How is the peer review status determined?

Crossref is extracting the journal. Cases where a journal could be found are coded as “journal article”, all NAs and publications in “SSRN Electronic Journal” are coded as “working papers”.  

### Can meta-analyses that replicate previous meta-analysis also be included?

Yes. If they use different data (e.g., a larger sample of studies) then they should be listed as replications.

## Search Strategy

### Where do the FLoRA entries come from?
One part of FLoRA is made up of the FORRT Replication Database (FReD), which includes the work of hundreds of people over many years. The FORRT Replication Database is a crowdsourced effort, which aimed to gather unpublished and published replication results to estimate and track replicability in social sciences. Studies were manually found or submitted and then double-coded by humans. For more information and to explore the database, click [here](https://forrt-replications.shinyapps.io/fred_explorer/).

Additional studies are included in FLoRA that are not in the FORRT Replication Database. A systematic search of OpenAlex has been conducted, using the keyword “replication” with automated extraction with R code and manual validation by a human of extracted variables. This is continuing over time. Reproductions are taken from the [Replication Network list](https://replicationnetwork.com/), and from the [Institute for Replication reports](https://i4replication.org/reports/?cpt=replication-report).


### What are the quality criteria for including pairs of studies? 

* We are not including **registrations** as they cannot reliably be linked to a publication and may lead to duplications. Also, they do not have enough info.  
* We are not including reports that are on Google docs.  
* Blog posts and other web pages (e.g., [Psychfiledrawer.org](http://Psychfiledrawer.org)) are included. Because such pages can change or disappear, every link in FLoRA is checked automatically and broken links are flagged for repair. We encourage authors to deposit their reports in a repository such as Zenodo or OSF, which gives them a DOI and a stable, citable version.
* Ideally, both the original and replication study should have a DOI or a stable URL (e.g., handle, URI, PubMed ID).  
* We are not assessing the quality of replications.


### What are inclusion criteria?
- The paper must explicitly set out, before reporting results, to re-test **a central empirical claim** of an identifiable earlier study. This may be one of several central claims, and replication may be one of several aims of the new paper. It need not repeat the whole original study or every claim. The literal word “replication” is not required if the intention to check the earlier study is explicit.
- Identify the specific target study or studies. Repeating a central association can qualify when that association is a central empirical claim of the original study. Merely checking a tangential association, reusing a method or instrument, or noticing agreement in the discussion does not qualify. Translating or re-validating an instrument qualifies only when it explicitly re-tests such a claim of an earlier study; adaptation alone is insufficient.
- Replications can range from close/direct (same methods, same population) to conceptual (testing the same hypothesis with different methods), as long as the above criteria are met. Outcomes follow the authors' assessment and the rules above: successful, failed, mixed, or an applicable special category such as uninformative or statistically successful but flawed. The release codebook lists the complete vocabulary present in the snapshot.
- Automated screening may retain uncertain candidates for fuller reading, but final inclusion requires evidence of the intended re-test and its target. Insufficient evidence is a reason to investigate or flag uncertainty, not to assume eligibility.
