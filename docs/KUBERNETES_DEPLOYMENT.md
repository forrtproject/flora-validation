# Deploying FLoRA to Uni Cloud Münster Kubernetes ("Kube")

This is the companion to [SETUP.md](SETUP.md) for running FLoRA on the
University of Münster's Kubernetes cluster instead of locally/Heroku-style.
It covers two things: how to fill in the [Kubernetes project request
form](https://cloud.uni-muenster.de/docs/kubernetes/project_request_form/),
and the manifests in [`k8s/`](../k8s) that deploy the app once the project is
approved.

Manifests: [`k8s/base`](../k8s/base) (shared) + [`k8s/overlays/production`](../k8s/overlays/production)
and [`k8s/overlays/staging`](../k8s/overlays/staging) (per-environment sizing).
Built and validated with `kubectl kustomize`.

---

## About the GPU request

**The manifests here request no GPU, and the form guidance below asks for
none either.** Two independent reasons:

1. **Nothing in this codebase uses one.** `google-genai` (see
   [llm_validator.py](../llm_validator.py)) calls the remote Gemini API over
   HTTPS — there's no local model inference to accelerate.
2. **The cluster can't attach one to a service like this anyway.** Per
   [cloud.uni-muenster.de/docs/kubernetes/gpus/](https://cloud.uni-muenster.de/docs/kubernetes/gpus/):
   GPUs on this cluster are currently fully allocated to JupyterHub, can only
   be requested by emailing `cloud@uni-muenster.de` with a justification (not
   via the form's quota field), and — most importantly — **"GPUs in
   Kubernetes cannot be used for long-running processes and should be
   allocated within Jobs"**, because each GPU pod spins up its own VM.
   `flora-app` is a long-running `Deployment`, so a GPU could not attach to it
   under this cluster's own rules regardless of what the form says.

Leave **GPUs (nvidia.com/gpu)** at `0` on the form. If a future feature
genuinely needs local inference (e.g. a batch embedding job), that would be a
separate Kubernetes `Job`, requested and justified separately by email — not
part of this web service's namespace quota.

---

## 1. Filling in the project request form

The form is at `/forms/kubernetes-project-application` on
cloud.uni-muenster.de. It must be **submitted by a University employee**
(here: Dr. Lukas Röseler) and digitally signed — a student assistant cannot
be the submitter, per the Uni Cloud team's reply. You (Hamidreza) get access
afterwards as a namespace **admin**, not as the form's responsible contact.

### Allgemeines (General)

| Field | What to put |
| --- | --- |
| Antragsteller | Dr. Lukas Röseler (must submit; a Uni Münster employee) |
| Projektname | e.g. `FLoRA Validation` |
| Projektbeschreibung | One or two sentences: a validation platform for the FORRT replication-study project (MCOS), moving off non-EU commercial hosting because it stores validators' names/emails |
| Fachbereich | Your actual faculty/department code — fill in whatever MCOS is organizationally under; I don't have this |
| IVV | Your IVV / cost-center code — university-internal, fill in yours |
| Organisationseinheit | MCOS's organizational unit string, as your department uses it |

### Verantwortliche (Responsible parties)

| Field | What to put |
| --- | --- |
| Leitend Verantwortlicher | Dr. Lukas Röseler |
| Technisch Verantwortlicher | Dr. Lukas Röseler, unless another University employee handles day-to-day ops — **this field also must be a University employee**, so it cannot be Hamidreza |

Add Hamidreza under **Administratoren** in the *Namespace* section below
instead — that's what actually grants `kubectl` access to the namespace.

### Software und Dienste

List (click "Dienst hinzufügen" once per line):

- Python 3.12 / FastAPI / Uvicorn (web service + static frontend)
- PostgreSQL 16 (self-hosted in-namespace — no operator is offered, so it
  runs as a plain Deployment; see `k8s/base/postgres.yaml`)
- APScheduler (in-process nightly job, no separate container)
- Outbound HTTPS to: `generativelanguage.googleapis.com` (Google Gemini API),
  `raw.githubusercontent.com` (nightly CSV sync from `forrtproject/flora-extractor`),
  `api.resend.com` (transactional email)

### Datenschutz und Regularien

- ✅ **Check** "In diesem Projekt werden personenbezogene Daten gespeichert
  oder verarbeitet." — the app stores validators' names and email addresses
  (`sessions.py`, `admin_auth.py`); this is the whole reason for the move.
- ✅ **Check** the IT-Administrator*innen Ordnung confirmation. It's a formal
  commitment that applies to every person you list as an admin, so make sure
  Hamidreza has actually read
  [the linked policy](https://cloud.uni-muenster.de/docs/kubernetes/security_policies/)
  before signing off on it.

### Namespaces und Ressourcen — create **two** namespace blocks

Click "Namespace hinzufügen" once, so you end up with a Produktion and a
Test/Staging block (matches `k8s/overlays/production` and
`k8s/overlays/staging`).

**Namespace #1 — Produktion**

| Field | Value |
| --- | --- |
| Umgebung | Produktion |
| Regionen | MS1 Einsteinstraße (the main location; MS2 is backup/multi-cluster, MS3 wasn't in the docs I could check — leave unchecked unless support tells you otherwise) |
| Namespace-Name | e.g. `flora-validation-prod` (whatever they assign, paste it into both `k8s/overlays/production/kustomization.yaml` and the two `REPLACE_WITH_PROD_NAMESPACE--flora-certificate` spots) |
| DNS-Einträge | `validation.forrt.org` (your existing domain — see §3 below) |
| Administratoren | Hamidreza's University IdM username, plus Dr. Röseler's |
| Administratorgruppen | leave empty unless MCOS has an IdM group |
| Sicherheitsprofil | `default` works; `hardened_default` also works — the manifests already run as non-root with a read-only root filesystem and all capabilities dropped, so either profile is satisfied without changes |
| External Secrets Operator | leave **unchecked** for now — plain `kubectl create secret` is enough at this scale; the manifests don't assume ESO |
| ArgoCD-Unterstützung | leave **unchecked** initially; the `k8s/` layout here is already ArgoCD-compatible if you want GitOps later |
| CPUs (Limit) | `4` |
| Arbeitsspeicher in Gigabyte | `8` |
| GPUs (nvidia.com/gpu) | `0` — see above |
| Ephemeral Storage in Gigabyte | `10` |
| CPUs (Request) | `1` |
| Storage-Klasse #1 | `cindergold` (SSD, for the live Postgres volume) |
| Anzahl Persistent Volumes | `1` |
| Speichergröße in Gigabyte | `60` |
| + Storage-Klasse hinzufügen | add a second entry: `manilabronze`, 1 volume, `10` GB (the shared `EXTRACTOR_DATA_DIR`, needs ReadWriteMany) |

These quota numbers give roughly 2–4x the resources of the two containers'
combined `limits` in `k8s/base`, so normal pod scheduling and a rolling
update (both pods briefly alive) fit inside quota. The 60GB Postgres volume
is generous headroom over the current ~1GB database.

**Namespace #2 — Test/Staging**

Same as above, except:

| Field | Value |
| --- | --- |
| Umgebung | Test/Staging |
| Namespace-Name | e.g. `flora-validation-staging` (paste into `k8s/overlays/staging/kustomization.yaml`) |
| DNS-Einträge | `staging.validation.forrt.org` |
| CPUs (Limit) | `1` |
| Arbeitsspeicher in Gigabyte | `2` |
| GPUs | `0` |
| Ephemeral Storage in Gigabyte | `2` |
| CPUs (Request) | `0.2` |
| Storage-Klasse #1 | `cinderbronze` (HDD is fine for staging), 1 volume, `10` GB |
| Storage-Klasse #2 | `manilabronze`, 1 volume, `5` GB |

### Verschiedenes

Optional comment field — worth noting there: "GPU intentionally not
requested; workload is CPU-only, calls external LLM API over HTTPS."
Submitting that context up front heads off a reviewer asking why a GPU field
is filled in when nothing needs it (moot here since we're leaving it at 0,
but worth a one-liner explaining the two namespaces / egress needs if there's
room).

---

## 2. Build and push the image

```bash
docker build -t REPLACE_WITH_YOUR_REGISTRY/flora-validation:latest .
docker push REPLACE_WITH_YOUR_REGISTRY/flora-validation:latest
```

Check [cloud.uni-muenster.de/docs/kubernetes/services/](https://cloud.uni-muenster.de/docs/kubernetes/services/)
for the image-validation/security-scanner requirements before picking a
registry — some clusters restrict which registries pods may pull from. GHCR
tied to this GitHub repo is a reasonable default if there's no constraint.

Then update the `image:` line in `k8s/base/app.yaml` (or patch it per-overlay
with `kustomize edit set image`).

## 3. Fill in the placeholders

Before applying anything, replace every `REPLACE_WITH_*` placeholder:

- `k8s/overlays/production/kustomization.yaml` — `REPLACE_WITH_PROD_NAMESPACE` (×2, including inside the `credentialName` patch)
- `k8s/overlays/staging/kustomization.yaml` — `REPLACE_WITH_STAGING_NAMESPACE` (×2)
- `k8s/base/app.yaml` — the `image:` line
- `k8s/base/certificate.yaml` — double-check the exact `subject:` block against
  the Uni's own example once you can see it (their certificate-management
  docs mention German institution details in the Subject that weren't fully
  reproduced where I could read them)

**Hostname note:** `validation.forrt.org` is an *external* domain, not a
`*.uni-muenster.de` one. The NIC-integration auto-CNAME feature in the docs
is described for `uni-muenster.de` hostnames; for `validation.forrt.org`
you'll likely need to create the CNAME yourself in forrt.org's own DNS once
`kubernetes@uni-muenster.de` tells you the Istio ingress hostname to point
at. Confirm this with them when the project is approved — it wasn't fully
spelled out for external domains in what I could read of the docs.

## 4. Create secrets (not committed to git)

See the commands documented in `k8s/base/secret.example.yaml` — run them
against each namespace once it exists:

```bash
kubectl create secret generic flora-postgres-secrets -n flora-validation-prod \
  --from-literal=POSTGRES_USER=flora \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 32)" \
  --from-literal=POSTGRES_DB=flora

kubectl create secret generic flora-secrets -n flora-validation-prod \
  --from-literal=DATABASE_URL="postgresql://flora:<same-password-as-above>@flora-postgres:5432/flora" \
  --from-literal=ADMIN_PASSWORD="$(openssl rand -base64 32)" \
  --from-literal=GEMINI_API_KEY="AIzaSy..." \
  --from-literal=RESEND_API_KEY="re_..." \
  --from-literal=GITHUB_TOKEN=""
```

Repeat for the staging namespace with its own passwords/keys. Keep the
Gemini/Resend keys the same as your current deployment if you want the two
environments to share quota, or issue separate ones — your call.

## 5. Deploy

```bash
kubectl kustomize k8s/overlays/production   # sanity-check the rendered YAML first
kubectl apply -k k8s/overlays/production

kubectl apply -k k8s/overlays/staging
```

## 6. Egress allowlist — do this before the app can reach anything external

The `flora-app` pods already carry the required
`egress.k8s.uni-muenster.de/enabled: "true"` label (per
[externalrouting docs](https://cloud.uni-muenster.de/docs/kubernetes/network/externalrouting/)),
but that page didn't publish the actual Cilium/Istio egress-policy resource
needed to allow specific FQDNs — it points to
[wwukube-examples](https://zivgitlab.uni-muenster.de/wwuit-sys/wwukube/wwukube-examples/-/tree/master/egress)
and to their Istio egress-gateway docs instead. Before relying on this in
production:

1. Pull the example from that GitLab repo (or ask `kubernetes@uni-muenster.de`
   directly) for the exact policy resource.
2. Apply it allowing exactly: `generativelanguage.googleapis.com`,
   `raw.githubusercontent.com`, `api.resend.com`.

Until that's in place, the Gemini tie-breaker, nightly GitHub sync, and email
notifications will all fail outbound — everything else (serving the app,
validator logins, Postgres) works fine without it.

## 7. First deploy: seed the database

A fresh Postgres + empty `EXTRACTOR_DATA_DIR` volume has no data yet. The app
auto-seeds from `data/extracted_latest.csv` *if that file is present* on
first boot — on a brand-new PVC it won't be. Run the sync once manually
after the egress allowlist (step 6) is live:

```bash
kubectl exec -n flora-validation-prod deploy/flora-app -- python sync_csv.py
```

This pulls the current CSV from `forrtproject/flora-extractor` on GitHub, the
same audited path described in [SETUP.md](SETUP.md#step-5--load-initial-data).

## 8. S3 for database backups

Per [cloud.uni-muenster.de/docs/s3-storage/](https://cloud.uni-muenster.de/docs/s3-storage/tutorial/):
request a bucket by emailing `cloud@uni-muenster.de`. Endpoint:
`radosgw.public.os.wwu.de` (or `s3.uni-muenster.de` if you want it replicated
across both datacenters — worth asking for, since **the docs explicitly say
there are no automatic backups of PersistentVolumes**, so the 60GB Postgres
volume itself is not backed up by the platform). Once you have credentials, a
simple approach is a `CronJob` in the same namespace running `pg_dump` piped
to `s3cmd put` — not included in `k8s/` yet since it depends on credentials
you don't have until the S3 request is approved; ask if you want this added
once the bucket exists.

## 9. Data protection

Not something I can advise on — per the Uni Cloud team's reply, this needs
sign-off from the University's Data Protection Officer, since the app stores
validators' names and emails. Loop them in separately from this
infrastructure request.

---

## Manifest reference

| File | Purpose |
| --- | --- |
| `k8s/base/configmap.yaml` | Non-secret env vars |
| `k8s/base/secret.example.yaml` | Documents required secret keys — not applied directly |
| `k8s/base/postgres.yaml` | Postgres PVC + Service + Deployment (`Recreate` strategy, no operator available) |
| `k8s/base/app.yaml` | FLoRA app Deployment + Service |
| `k8s/base/app-data-pvc.yaml` | Shared RWX volume for `EXTRACTOR_DATA_DIR` |
| `k8s/base/gateway.yaml` | Istio Gateway (TLS termination, NIC integration) |
| `k8s/base/virtualservice.yaml` | Routes the Gateway's traffic to the app Service |
| `k8s/base/certificate.yaml` | cert-manager `Certificate` via the `wwuit-acme` issuer |
| `k8s/overlays/production/` | Namespace + prod-sized patches |
| `k8s/overlays/staging/` | Namespace + smaller-sized patches |
