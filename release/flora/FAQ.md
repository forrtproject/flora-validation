# FAQs for the FORRT Library of Reproduction and Replication Attempts (FLoRA)

## Coding and Validation

### Are replications with synthetic data included?

Yes. Although some users of the data may not find repeated tests informative, others may.

### What is the outcome based on?

We rely on what replication authors say in the abstract, or if not stated there, what is written in the report (discussion and conclusion sections). If studies are part of a meta-paper, we rely on the individual reports where available, and otherwise on the main criterion used by the meta-paper's authors. The outcome refers to whether the central finding that the replication was designed to test could be replicated and may leave out robustness checks and exploratory analyses.

### What is the outcome if the manipulation check failed so the hypothesis could not be tested?

This will be coded as failed. Also, if the manipulation check was successful and the main test failed, it is coded as failed. Equivalent to these cases are studies with a preliminary test (e.g., gender knowledge gap test before stereotype threat test).
If the replication title focuses on one specific effect, which could not be replicated, it will be coded as failed regardless of other potentially successful checks.

### How are you coding if the close replication works but the conceptual replication doesn’t?

This we would code as *mixed*.

### If the data is overlapping (e.g., “using an additional decade of data”), is it a reproduction or a replication?

Because there are *additional* observations, we are coding it as a replication.

### What if there is an error in the coding but with it, results are reproducible?

**Example**: “Replication shows that the empirical results are driven exclusively by two countries with weak upper houses, one of which (Ireland) is miscoded. If this case is recoded there is no statistically or substantially significant effect of upper houses, regardless of their power. We conclude that the two questions cannot yet be answered”

This would be coded as computational reproducible but not robust.



### Are reanalyses included if they do not test for numerical reproduction? What about multiverse analysis?

Yes, as long as they test the same claim as the original study. This could also mean that a reproduction is a multiverse analysis. Outcomes of reproductions are noted in the outcome variable and include information about computational reproducibility and robustness.

### What if a paper includes reproduction *and* replication?

Then it will be listed twice in FLoRA, once as a replication and once as a reproduction.

### What level is the database?

Each row is one pair of references. A replication study can have multiple studies but their results are aggregated (e.g., based on the authors’ judgment, who may report aggregated results or conflicting results, which we consider as mixed).

### When is a finding mixed?

We rely on what replication authors say in the abstract or the report, subject to our interpretation of this. If studies are part of a meta-paper, we still rely on the individual reports where available, and otherwise on the main criterion used by the paper authors.

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
* Websites/blog posts like [Psychfiledrawer.org](http://Psychfiledrawer.org) are not persistent and we cannot ensure that links keep working. Reports on Zenodo or OSF, however, can be included.
* Ideally, both the original and replication study should have a DOI or a stable URL (e.g., handle, URI, Pubmed ID). For example, blog post URLs can change, break, and there is no versionising.
* We are not assessing the quality of replications.


### What are inclusion criteria?
- Self-identify as a replication (e.g., “replication of Author (Year)”) before reporting results — replication must be an aim, not just a result. Identify specific target study/studies that it replicates. Replicate a study or experiment, not just a single association or finding.
- Replications can range from close/direct (same methods, same population) to conceptual (testing the same hypothesis with different methods), as long as the above criteria are met. The plugin tags replication outcomes as Successful, Failed, or Mixed, based on how the replication authors characterise their results, usually in the abstract.
