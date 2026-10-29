"""Build the offline guide from audited JSON. Reads code; never imports the app.

Run from anywhere: python docs/pipeline-guide/build_guide.py
Then: node docs/pipeline-guide/render_and_check.cjs
"""
import html
import json
from pathlib import Path
import re
import textwrap

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REPO = "https://github.com/forrtproject/flora-validation"
COMMIT = "2f3a89c56a44bd7b6270eb0fa94548f6d93512f8"
DATE = "29 October 2026"


def esc(value):
    return html.escape(str(value), quote=True)


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def source_link(source):
    return f"{REPO}/blob/{COMMIT}/{source['file']}#L{source['line']}"


def load_nodes():
    nodes = []
    for name in ("pipeline", "review", "admin", "comparison"):
        nodes.extend(json.loads((HERE / f"{name}-map.json").read_text(encoding="utf-8-sig"))["nodes"])
    by_id = {n["id"]: n for n in nodes}
    by_id["review-storage"]["refresh"] = [
        "Review tables change when their API transactions commit.",
        "Complete stored snapshots change during preparation; eligible FLoRA field corrections also update an existing stored row immediately. Published files have their own release step.",
    ]
    # Give each individual UI action its own deep link and plain-text README.
    action_nodes = []
    for parent in nodes:
        for action in parent.get("actions", []):
            action_id = f"{parent['id']}--{slug(action['label'])}"
            action["node_id"] = action_id
            action_nodes.append({
                "id": action_id, "title": action["label"], "tab": parent["tab"],
                "group": "admin", "parent": parent["id"], "summary": action["does"],
                "inputs": [f"The {parent['tab']} tab and the record or settings selected there."],
                "steps": [action["does"]], "saves": [action["saves"]],
                "refresh": [action["when"]], "outputs": [], "cautions": [],
                "sources": action.get("sources", []),
            })
    nodes.extend(action_nodes)
    nodes.append({
        "id": "storage-sources", "title": "Source Records: the combined input", "group": "storage",
        "summary": "source_records is the shared input collection for final preparation. It combines published sheet rows, approved extractor reviews and separately published disagreement records.",
        "inputs": ["Accepted Google entry-sheet rows.", "Approved validated rows projected by sync_validated.py.", "Explicitly published Observatory adjudication decisions, when that feature is enabled."],
        "steps": ["Keep source identity and original values so each combined record can be traced back to its input.", "Apply exclusions, duplicate decisions, outcome rules and FLoRA corrections when building the derived dataset."],
        "saves": ["PostgreSQL source_records; source_sync_runs records refresh outcomes.", "Corrections belong in flora_record_changes, rather than overwriting imported sheet values."],
        "refresh": ["The Source Records grid reloads the imported input. The FLoRA grid derives the current combined view.", "The stored complete CSV and public API update through preparation, with eligible existing-row corrections applied immediately."],
        "outputs": ["Input to transform_sources.py and the identity registry."],
        "cautions": ["Source Records, the derived FLoRA grid and stored flora_data are three distinct views of data."],
        "sources": [{"file":"db_schema.sql","line":1223,"symbol":"source_records"}, {"file":"sync_validated.py","line":139,"symbol":"sync"}, {"file":"transform_sources.py","line":574,"symbol":"load_rules"}],
    })
    ids = [n["id"] for n in nodes]
    assert len(ids) == len(set(ids)), "Duplicate node IDs"
    for node in nodes:
        assert node["sources"], f"No code evidence for {node['id']}"
        for source in node["sources"]:
            file = ROOT / source["file"]
            assert file.is_file(), file
            lines = file.read_text(encoding="utf-8-sig").splitlines()
            line = source["line"]
            assert 1 <= line <= len(lines), source
            start, end = max(0, line-2), min(len(lines), line+12)
            source["excerpt"] = "\n".join(f"{i+1:>5}  {lines[i]}" for i in range(start, end))
    return nodes


class Diagram:
    def __init__(self, name, title, subtitle, width=1460, height=800):
        self.name, self.width, self.height = name, width, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{name}-title {name}-desc">',
                      f'<title id="{name}-title">{esc(title)}</title><desc id="{name}-desc">{esc(subtitle)} Each box links to its explanatory README.</desc>',
                      '<defs><marker id="'+name+'-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#78877a"/></marker></defs>',
                      f'<rect width="{width}" height="{height}" fill="#fffcf5"/>',
                      f'<text x="36" y="42" font-family="Georgia,serif" font-size="25" fill="#263c31">{esc(title)}</text>',
                      f'<text x="36" y="68" font-family="Segoe UI,sans-serif" font-size="13" fill="#657267">{esc(subtitle)}</text>']
        self.boxes, self.edges = [], []

    def label(self, x, y, text):
        self.parts.append(f'<text x="{x}" y="{y}" font-family="Consolas,monospace" font-size="10" letter-spacing="1.5" fill="#657267">{esc(text.upper())}</text>')

    def node(self, id, title, note, x, y, w=205, h=104, kind="process"):
        self.boxes.append((id,title,note,x,y,w,h,kind))

    def edge(self, points, label="", lx=None, ly=None, dashed=False):
        self.edges.append((points,label,lx,ly,dashed))

    def finish(self):
        for points,label,lx,ly,dashed in self.edges:
            self.parts.append(f'<polyline points="{points}" fill="none" stroke="#78877a" stroke-width="1.6" stroke-linejoin="round" marker-end="url(#{self.name}-arrow)"'+(' stroke-dasharray="5 5"' if dashed else '')+'/>')
            if label:
                self.parts.append(f'<text x="{lx}" y="{ly}" font-family="Segoe UI,sans-serif" font-size="11" fill="#657267" paint-order="stroke" stroke="#fffcf5" stroke-width="5">{esc(label)}</text>')
        for id,title,note,x,y,w,h,kind in self.boxes:
            color = {"human":"#a75032", "output":"#416b83", "process":"#284e3e", "storage":"#71682f"}[kind]
            fill = {"human":"#faf0e6", "output":"#edf3f4", "process":"#f1f4eb", "storage":"#f4f0df"}[kind]
            lines=textwrap.wrap(title,width=max(15,int((w-34)/10)))
            note_lines=textwrap.wrap(note,width=max(20,int((w-34)/6.3)))
            self.parts.append(f'<a href="../index.html#view={self.name if self.name != "storage" else "timing"}&amp;node={id}" data-node="{id}" aria-label="{esc(title)}: open README"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{fill}" stroke="#bbc8b7"/><rect x="{x}" y="{y+12}" width="3" height="{h-24}" fill="{color}"/>')
            for i,line in enumerate(lines):
                self.parts.append(f'<text x="{x+17}" y="{y+29+i*23}" font-family="Georgia,serif" font-size="19" fill="{color}">{esc(line)}</text>')
            for i,line in enumerate(note_lines):
                self.parts.append(f'<text x="{x+17}" y="{y+36+len(lines)*23+i*16}" font-family="Segoe UI,sans-serif" font-size="11.5" fill="#657267">{esc(line)}</text>')
            self.parts.append('</a>')
        self.parts.append(f'<line x1="36" y1="{self.height-43}" x2="{self.width-36}" y2="{self.height-43}" stroke="#d4dacc"/><text x="36" y="{self.height-20}" font-family="Consolas,monospace" font-size="10" fill="#657267">FLoRA FIELD GUIDE  /  {DATE.upper()}  /  CODE {COMMIT[:7]}</text><text x="{self.width-36}" y="{self.height-20}" text-anchor="end" font-family="Segoe UI,sans-serif" font-size="11" fill="#657267">Click a box for its README and code.</text></svg>')
        return "\n".join(self.parts)


def diagrams():
    diagrams = {}
    d = Diagram("comparison", "Source Records and FLoRA: inputs, current view, stored output", "Source Records preserves the imported inputs. FLoRA combines and corrects them. Full CSV and API read the stored dataset.", width=1600, height=900)
    d.label(40,125,"01 · Inspect the input")
    d.label(620,125,"02 · Apply the rules")
    d.label(1200,125,"03 · Review the combined view")
    d.node("admin-sources","Source Records tab","source_records / original imported values",40,160,w=360,h=125,kind="storage")
    d.node("process-transform","Derive current records","Rules / duplicates / metadata / identities",620,160,w=360,h=125)
    d.node("admin-flora","FLoRA tab","Current combined view + saved corrections",1200,160,w=360,h=125,kind="human")
    d.edge("400,222 620,222","read imported inputs",435,206)
    d.edge("980,222 1200,222","build / reuse current view",1000,206)
    d.node("process-save","Save a field correction","flora_record_changes / versioned history",1200,355,w=360,h=125,kind="human")
    d.edge("1380,285 1380,355","Save changes",1393,326)
    d.node("process-queue","Run pipeline","Refresh inputs, build and check the CSV",40,580,w=360,h=125)
    d.edge("220,285 220,580","Source Records button",234,446)
    d.node("storage-dataset","Stored dataset","flora_data / snapshot + eligible corrections",620,580,w=360,h=125,kind="storage")
    d.edge("400,642 620,642","prepare + commit",448,625)
    d.node("output-downloads","Full CSV + public API","Read the active stored rows",1200,580,w=360,h=125,kind="output")
    d.edge("980,642 1200,642","read stored output",1038,625)
    d.edge("1380,480 1380,527 800,527 800,580","Save updates only eligible existing stored rows",870,514,dashed=True)
    d.label(40,770,"The tabs may have different counts: several inputs can combine into one record; exclusions can remove others.")
    d.label(40,803,"Refresh reloads a view. Run pipeline rebuilds the full stored dataset. GitHub publication is a separate step.")
    diagrams["comparison"] = d.finish()
    d = Diagram("overview", "The complete FLoRA pipeline", "Two input routes meet in Source Records. Review approval, preparation and repository publication are separate steps.", height=850)
    for x,t in [(36,"01 · Gather"),(276,"02 · Import"),(516,"03 · Review / combine"),(756,"04 · Decide / prepare"),(996,"05 · Approve / store"),(1236,"06 · Share")]: d.label(x,120,t)
    for id,title,note,x,kind in [
        ("input-extractor","Extractor output","External project / CSV",36,"process"),
        ("process-extractor-maintenance","Import candidates","unvalidated + review slots",276,"process"),
        ("review-submit","Human reviews","Two people submit answers",516,"human"),
        ("review-consensus","Consensus + Gemini","Agreement or review needed",756,"human"),
        ("admin-entries","Admin decision","Approve / resolve",996,"human"),
        ("process-validated-bridge","Approved records","validated → source_records",1236,"process")]: d.node(id,title,note,x,150,w=188,h=125,kind=kind)
    for x in [224,464,704,944,1184]: d.edge(f"{x},212 {x+52},212")
    for id,title,note,x,kind in [
        ("input-sheets","Entry sheets","Published Google CSV inputs",36,"process"),
        ("storage-sources","Source Records","Shared imported input",276,"storage"),
        ("process-transform","Combine + clean","Normalize / deduplicate",516,"process"),
        ("process-prepare","Prepare + check","One candidate CSV + reports",756,"process"),
        ("storage-dataset","Stored dataset","PostgreSQL flora_data",996,"storage")]: d.node(id,title,note,x,410,w=188,h=125,kind=kind)
    for x in [224,464,704,944]:d.edge(f"{x},472 {x+52},472")
    d.edge("1330,275 1330,326 370,326 370,410","approved input is synchronized during the FLoRA pipeline",575,317)
    d.edge("850,275 850,296 1330,296 1330,275","eligible automatic approval",1020,288,dashed=True)
    d.node("output-downloads","CSV downloads","Full dataset / filtered / run",1236,355,w=188,h=125,kind="output")
    d.node("output-api","Public API","DOI / title / ID-hash lookup",1236,515,w=188,h=125,kind="output")
    d.edge("1184,450 1209,450 1209,417 1236,417")
    d.edge("1184,495 1209,495 1209,577 1236,577")
    d.node("input-rules-metadata","Rules + metadata","Exclusions / aliases / DOIs",516,660,w=188,h=125)
    d.edge("610,660 610,535","supporting inputs",621,609)
    d.node("process-save","FLoRA corrections","History + eligible stored row",756,660,w=188,h=125,kind="human")
    d.edge("944,722 1090,722 1090,535","eligible row: immediate",965,710,dashed=True)
    d.node("output-publication","Repository release","Publish FLoRA → GitHub",1236,675,w=188,h=110,kind="output")
    d.edge("1184,510 1198,510 1198,730 1236,730","",dashed=True)
    d.label(36,606,"Also explore");d.node("review-adjudication","Disagreements","Optional separate review lane",36,630,w=230,h=125,kind="human")
    d.edge("151,630 151,585 370,585 370,535","Publish to Source Records",172,575,dashed=True)
    diagrams["overview"] = d.finish()

    d=Diagram("inputs","Input schema · how data is gathered","The rows entering this repository come from two main routes, plus an optional adjudication lane.",width=1420,height=840)
    for x,t in [(40,"Outside the application"),(390,"Acceptance / import"),(750,"Shared database"),(1100,"Next destination")]:d.label(x,118,t)
    for row,items in enumerate([
        [("input-extractor","Extractor CSV","Candidate pair + provenance","process"),("process-extractor-maintenance","Check + import","Schema, identity, safety gates","process"),("review-storage","Candidate tables","unvalidated + queue + metadata","storage"),("review-claim","Human review","A validator claims a slot","human")],
        [("input-sheets","Published sheets","Source URLs in sources.yml","process"),("input-sheets","Validate + insert","New accepted UUIDs only","process"),("storage-sources","source_records","Imported fields + raw row","storage"),("process-queue","Run FLoRA pipeline","Combine with approved reviews","process")],
        [("review-adjudication","Observatory CSV","Optional disagreement input","process"),("admin-disagreements","Import + adjudicate","Approve, then explicitly publish","human"),("storage-sources","source_records","Published adjudication rows","storage"),("process-transform","Final transform","Same combined output path","process")],
    ]):
        y=155+row*205
        for x,item in zip([40,390,750,1100],items):
            id,title,note,kind=item;d.node(id,title,note,x,y,w=270,h=128,kind=kind)
        for a,b in [(310,390),(660,750),(1020,1100)]:d.edge(f"{a},{y+64} {b},{y+64}")
    d.node("input-rules-metadata","Supporting inputs","Exclusions, vocabulary rules, OpenAlex metadata, saved duplicate decisions",390,735,w=710,h=60)
    # The compact supporting-input strip uses a separate text layout.
    d.boxes.pop();d.label(40,757,"Supporting inputs →");d.node("input-rules-metadata","Rules & metadata","Exclusions · aliases · OpenAlex · duplicate rulings",310,725,w=700,h=65)
    diagrams["inputs"]=d.finish()

    d=Diagram("processing","Processing schema · review, then prepare","Human judgement becomes an approved input; preparation turns combined inputs into a stored public dataset.",height=900)
    d.label(36,120,"A · Review one extractor pair")
    row=[("review-claim","Claim a study","Reserve a human slot"),("review-draft","Draft answers","Browser-only autosave"),("review-submit","Submit review","Server commit required"),("review-consensus","Two-review decision","Gemini / rules / admin"),("admin-entries","Approve or resolve","Write approved validated row")]
    for x,(id,title,note) in zip([36,322,608,894,1180],row):d.node(id,title,note,x,150,w=240,h=125,kind="human")
    for x in [276,562,848,1134]:d.edge(f"{x},212 {x+46},212")
    d.node("review-skip","Skip / restricted","Release or route to an assignee",36,320,w=240,h=120,kind="human")
    d.edge("156,275 156,320",dashed=True)
    d.node("process-validated-bridge","Sync approved rows","validated → source_records",1180,320,w=240,h=120)
    d.edge("1300,275 1300,320")
    d.edge("1014,275 1014,299 1300,299 1300,320","eligible auto-approval",1080,291,dashed=True)
    d.node("review-consensus","Excluded record","Rejected; no approved output",608,320,w=240,h=120,kind="human")
    d.edge("1014,275 1014,380 848,380","rejection branch",866,368,dashed=True)
    d.label(36,500,"B · Prepare the combined dataset")
    row=[("process-queue","Queue the run","source_sync_jobs"),("storage-sources","Refresh inputs","Sheets + validated + rules"),("process-transform","Transform + dedup","Codebook / identities / edits"),("process-prepare","Check exact CSV","Structure + network evidence"),("storage-dataset","Commit snapshot","flora_data + revision")]
    for x,(id,title,note) in zip([36,322,608,894,1180],row):d.node(id,title,note,x,535,w=240,h=125)
    for x in [276,562,848,1134]:d.edge(f"{x},597 {x+46},597")
    d.edge("1300,440 1300,474 442,474 442,535","joins source collection",670,464)
    d.node("process-identities","Permanent identities","flora_records registry",608,720,w=240,h=125,kind="storage")
    d.edge("728,720 728,660")
    d.node("process-save","Saved corrections","Versioned correction history",322,720,w=240,h=125,kind="human")
    d.edge("562,782 585,782 585,688 728,688 728,660",dashed=True)
    d.node("process-entry-checks","Recheck stored rows","Structural / daily network",1180,720,w=240,h=125)
    d.edge("1300,660 1300,720")
    diagrams["processing"]=d.finish()

    d=Diagram("outputs","Output schema · which result am I looking at?","A live view, an exact run artifact and a committed repository release can represent different moments.",width=1420,height=870)
    d.node("storage-sources","Current inputs","source_records + decisions",40,150,w=260,h=125,kind="storage")
    d.node("admin-flora","Current FLoRA grid","Derived view / filtered export",410,150,w=290,h=125,kind="output")
    d.edge("300,212 410,212","derive on read",318,196)
    d.node("process-prepare","Prepare snapshot","Build / validate / store",40,380,w=260,h=125)
    d.edge("170,275 170,380","Run pipeline",185,332)
    d.node("storage-dataset","Stored flora_data","Permanent prepared rows",410,380,w=290,h=125,kind="storage")
    d.edge("300,442 410,442","commit",337,425)
    d.node("output-downloads","Download full CSV","Current stored active rows",810,320,w=260,h=125,kind="output")
    d.node("output-api","Public API","Stored rows; HTTP caching",810,500,w=260,h=125,kind="output")
    d.edge("700,420 752,420 752,382 810,382")
    d.edge("700,470 752,470 752,562 810,562")
    d.node("output-publication","GitHub release","output/flora.csv + manifest",1110,380,w=270,h=125,kind="output")
    d.edge("700,486 770,486 770,672 1245,672 1245,505","Publish FLoRA or successful nightly build",837,662)
    d.node("output-downloads","This run's CSV","source_sync_jobs + reports",40,680,w=260,h=125,kind="output")
    d.edge("170,505 170,680","exact run snapshot",186,599)
    d.node("output-validated-export","Validated-only CSV","data/validated_export.csv",410,680,w=290,h=125,kind="output")
    d.label(415,650,"Separate daily export of validated")
    d.node("process-save","Save a field correction","Updates eligible stored row",810,150,w=340,h=108,kind="human")
    d.edge("810,204 760,204 760,295 555,295 555,380","existing eligible rows",572,286,dashed=True)
    diagrams["outputs"]=d.finish()

    d=Diagram("storage","Storage schema · where a save goes","Logical relationships. Different tables represent candidates, decisions, source inputs and public output.",width=1420,height=760)
    row=[("review-draft","Browser storage","Draft + pending submit"),("review-storage","Review tables","unvalidated / queue / validated"),("storage-sources","Source tables","source_records + sync history"),("storage-dataset","Prepared dataset","flora_data + metadata")]
    for x,(id,title,note) in zip([40,395,750,1105],row):d.node(id,title,note,x,180,w=270,h=130,kind="storage")
    for a,b,label in [(310,395,"Submit"),(665,750,"Sync"),(1020,1105,"Prepare")]:d.edge(f"{a},245 {b},245",label,a+12,230)
    d.node("review-identities","Identity registry","flora_records / stable public IDs",750,420,w=270,h=130,kind="storage")
    d.edge("885,420 885,310",dashed=True)
    d.node("process-save","Correction history","flora_record_changes",1105,420,w=270,h=130,kind="human")
    d.edge("1240,420 1240,310","eligible row",1250,365)
    d.node("process-queue","Durable run artifacts","source_sync_jobs CSV + reports",395,420,w=270,h=130,kind="storage")
    d.node("output-publication","Files & repository","Server output ≠ GitHub commit",40,420,w=270,h=130,kind="output")
    d.edge("395,485 310,485","report",325,468,dashed=True)
    d.edge("1240,310 1240,361 175,361 175,420","export stored data, then commit release",470,351)
    d.label(40,638,"No save writes corrections back into the source Google Sheet or flora-extractor.")
    d.label(40,666,"A database exception rolls back that transaction; a lost HTTP reply does not prove no save occurred. Pipelines have multiple commits.")
    diagrams["storage"]=d.finish()
    return diagrams


def metadata(nodes):
    action=lambda parent,label:next(n["id"] for n in nodes if n.get("parent")==parent and n["title"]==label)
    return {
        "repository":REPO,"commit":COMMIT,"date":DATE,"nodes":nodes,
        "comparison":json.loads((HERE/"comparison-map.json").read_text(encoding="utf-8")),
        "views": {
            "overview":{"kicker":"01 / THE BIG PICTURE","title":"One system. Two main input routes.","description":"Start at the top left and follow the arrows. Click a stage to see its inputs, steps, saves, update timing and source code.","notes":[{"title":"Review approval","text":"A judgement or admin approval changes the review database. The approved record still needs to enter the combined FLoRA pipeline."},{"title":"Stored dataset","text":"Preparation builds the complete dataset. Some corrections to existing stored rows also take effect immediately."},{"title":"Repository publication","text":"The website can save data before GitHub changes. A separate workflow commits the release files."}]},
            "inputs":{"kicker":"02 / INPUT SCHEMA","title":"Where records enter.","description":"Source definitions, acceptance gates, imported tables and the destination of each input. The upstream extractor is a separate project; this guide traces its contract with this repository."},
            "processing":{"kicker":"03 / PROCESSING SCHEMA","title":"How evidence becomes a dataset.","description":"The review lane approves extracted pairs. The preparation lane combines those approved rows with the other sources, applies rules, validates a candidate CSV and stores it."},
            "outputs":{"kicker":"04 / OUTPUT SCHEMA","title":"One record. Several views of it.","description":"Use this map to distinguish the current grid, stored full CSV, exact run download, public API, repository release and validated-only export."},
            "admin":{"kicker":"05 / EVERY ADMIN TAB","title":"What happens when I click?","description":"All 11 tabs and 59 action groups, traced from the browser to their server handlers and storage. Choose a tab, then open any button's README. Disagreements appears only when enabled."},
            "timing":{"kicker":"06 / SAVE LOCATIONS & UPDATE TIMING","title":"Saved is a specific place.","description":"Read across a row to see what an action changes now, what remains local, and which later step makes it visible elsewhere."},
            "code":{"kicker":"07 / SOURCE CODE INDEX","title":"Go straight to the implementation.","description":"Browse the scripts behind the diagrams and buttons. File links open the complete source at the reviewed commit; explanation links open the related README."},
            "comparison":{"kicker":"02 / SOURCE RECORDS VS FLORA","title":"The input, the view, and the stored result.","description":"Use Source Records to inspect what arrived. Use FLoRA to review and correct the combined record. Follow the arrows below, then compare the tabs and try the three examples."},
        },
        "tour":["input-extractor","process-extractor-maintenance","review-claim","review-draft","review-submit","review-consensus","admin-entries","process-validated-bridge","storage-sources","process-transform","process-prepare","storage-dataset","output-api","output-publication"],
        "timing":[
            {"node":"review-draft","action":"Answer a review question","where":"Browser: flora.draft.{pair_id}","when":"Draft backup every 10 seconds in this browser.","next":"Submit and wait for the server to confirm."},
            {"node":"review-submit","action":"Submit a judgement","where":"Pending payload locally, then validation_queue + unvalidated + validator totals.","when":"Durable after the server transaction commits. The next card can appear earlier.","next":"Second review → consensus / admin decision."},
            {"node":action("admin-entries","Save Note"),"action":"Entries → Save Note","where":"unvalidated.admin_notes and author/time.","when":"After successful request; other admins see it on reload.","next":"Resolve/approve separately if a scientific decision is needed."},
            {"node":action("admin-entries","Mark as Resolved"),"action":"Approve / Mark as Resolved","where":"unvalidated final decision + validated + admin_decisions.","when":"Entries updates after success.","next":"FLoRA pipeline syncs approved inputs, then prepares the stored dataset."},
            {"node":"process-save","action":"FLoRA → Save changes","where":"flora_record_changes; eligible existing active flora_data row + revision.","when":"Current view and eligible stored row after commit; public HTTP caches can lag.","next":"New/non-stored rows need preparation. GitHub needs publication."},
            {"node":action("admin-sources","Duplicates → Duplicate of …"),"action":"Duplicate / preprint ruling","where":"Source duplicate fields or preprint_dedup_decisions.","when":"Derived FLoRA view on reload.","next":"Run pipeline for full CSV / API; publish for GitHub."},
            {"node":"process-queue","action":"Source Records → Run pipeline","where":"source_sync_jobs first, then input tables and flora_data; exact run artifacts.","when":"Queued immediately; dispatcher checks every 5 seconds; completion can take minutes.","next":"Review reports. Use Publish FLoRA for GitHub sooner."},
            {"node":"output-downloads","action":"Download / Export CSV","where":"A file on your computer.","when":"At download time; full, filtered, raw and run exports have different sources.","next":"Downloading does not refresh sources or publish anything."},
            {"node":"output-publication","action":"Publish FLoRA (GitHub)","where":"Repository output/flora.csv and release evidence.","when":"When the workflow successfully commits changed files.","next":"Consumers reading GitHub can fetch the committed release."},
            {"node":action("admin-priority","Reset to defaults"),"action":"Pool Priority → Reset to defaults","where":"Only the current form until Save.","when":"The controls change immediately; serving rules do not.","next":"Click Save to persist serving_config."},
            {"node":action("admin-disagreements","Publish to FLoRA"),"action":"Disagreements → Publish to FLoRA","where":"source_records + adjudication audit/publication state.","when":"After the explicit publish request succeeds.","next":"Run the FLoRA pipeline to include it in stored CSV / API."},
        ],
        "schedule":[
            {"node":"schedule-updates","job":"Gemini error retry","trigger":"00:22 UTC daily · app scheduler","effect":"Retries eligible failed LLM calls; not every unresolved disagreement."},
            {"node":"process-extractor-maintenance","job":"Extractor full sync","trigger":"02:00 UTC daily · app scheduler","effect":"Imports candidates, reports orphans and eligible explicit retirements."},
            {"node":"process-queue","job":"Sync entry sheets","trigger":"03:00 UTC daily · GitHub Actions","effect":"Refreshes source inputs, prepares/stores and commits a successful combined release."},
            {"node":"output-publication","job":"Publish FLoRA","trigger":"After Sync entry sheets completes, or manual workflow dispatch","effect":"Writes current stored data to repository files, including saved corrections."},
            {"node":"output-validated-export","job":"Daily validated export","trigger":"04:00 UTC daily · GitHub Actions","effect":"Updates the separate data/validated_export.csv."},
            {"node":"process-entry-checks","job":"FLoRA entry checks","trigger":"Due check every 5 minutes; network checks due daily from 04:00 UTC","effect":"Structural checks when revision changes; network findings on their own schedule."},
            {"node":"schedule-updates","job":"Queued jobs / stale claims","trigger":"Source jobs 5 sec; extractor jobs 10 sec; stale claims 2 min","effect":"Dispatch intervals are not completion promises. Claims have separate age thresholds."},
        ],
    }


def markdown_node(node):
    lines=[f"# {node['title']}","",node["summary"],"",f"[Open interactive guide](../index.html#view={'admin' if node['group']=='admin' else 'overview'}&node={node['id']})","",f"Code snapshot: {DATE}, `{COMMIT[:7]}`.",""]
    for key,title in [("inputs","What goes in"),("steps","What happens"),("saves","What is saved, and where"),("refresh","When it updates"),("outputs","What comes out"),("cautions","Keep in mind")]:
        if node.get(key):lines += [f"## {title}",""]+[f"{str(i+1)+'.' if key=='steps' else '-'} {value}" for i,value in enumerate(node[key])]+[""]
    if node.get("actions"):
        lines += ["## Buttons and actions",""]
        for a in node["actions"]:lines += [f"### [{a['label']}]({a['node_id']}.md)","",a["does"],"",f"**Saves:** {a['saves']}","",f"**Visible:** {a['when']}",""]
    lines += ["## Find it in the code",""]
    for s in node["sources"]:lines += [f"- [{s['file']}:{s['line']} — {s.get('symbol','source')}]({source_link(s)}) · [complete script]({REPO}/blob/{COMMIT}/{s['file']})"]
    return "\n".join(lines)+"\n"


def build():
    nodes=load_nodes();data=metadata(nodes);data["diagrams"]=diagrams()
    for index,name in enumerate(("overview","comparison","inputs","processing","outputs","admin","timing","code"),1):
        data["views"][name]["kicker"]=f"{index:02} / "+data["views"][name]["kicker"].split(" / ",1)[1]
    known={n["id"] for n in nodes}
    for name,svg in data["diagrams"].items():
        assert set(re.findall(r'data-node="([^"]+)"',svg)) <= known, name
    for row in data["timing"]+data["schedule"]:assert row["node"] in known
    for row in data["comparison"]["rows"]+data["comparison"]["scenarios"]:assert row["node"] in known
    (HERE/"diagrams").mkdir(exist_ok=True);(HERE/"readmes").mkdir(exist_ok=True)
    for name,svg in data["diagrams"].items(): (HERE/"diagrams"/f"{name}.svg").write_text(svg,encoding="utf-8")
    for node in nodes:(HERE/"readmes"/f"{node['id']}.md").write_text(markdown_node(node),encoding="utf-8")
    template=(HERE/"src"/"guide.html").read_text(encoding="utf-8")
    template=template.replace("/*GUIDE_CSS*/",(HERE/"src"/"guide.css").read_text(encoding="utf-8"))
    data["diagrams"]={name:svg.replace('href="../index.html','href="index.html') for name,svg in data["diagrams"].items()}
    template=template.replace("/*GUIDE_DATA*/",json.dumps(data,ensure_ascii=False).replace("<","\\u003c"))
    template=template.replace("/*GUIDE_JS*/",(HERE/"src"/"guide.js").read_text(encoding="utf-8"))
    (HERE/"index.html").write_text(template,encoding="utf-8")
    readme=["# FLoRA pipeline field guide","","[Open the clickable guide](index.html)","","Double-click `index.html` to open it in a browser. The guide, explanations and code excerpts work offline. Full script links open GitHub at the reviewed commit.","",f"Verified against local code on {DATE}, commit `{COMMIT}`. This documents configured behavior, not the live deployment's health.","","## Start here","","1. Open **System map** for the complete pipeline, or use **Start walkthrough** to follow one extracted paper.","2. Open **Inputs**, **Processing**, and **Outputs** for separate diagrams.","3. Choose **Admin tabs**, pick a tab, and click a button card for its simple README, save location and exact code.","4. Use **Save & update** for timing and persistence, or **Code index** to open a whole script.","5. Search for any tab, action, table or filename. Each element has a shareable link and a plain Markdown README.","","## Diagram pictures for slides","","SVG files stay sharp when resized; PNG files can be pasted into PowerPoint or Google Slides. SVG boxes link back to the interactive guide when opened alongside this folder.",""]
    for name,title in [("overview","Complete system"),("comparison","Source Records vs FLoRA"),("inputs","Input schema"),("processing","Processing schema"),("outputs","Output schema"),("storage","Storage schema")]:readme += [f"- **{title}:** [PNG](diagrams/{name}.png) · [SVG](diagrams/{name}.svg)"]
    readme += ["","## Source Records vs FLoRA","","[Open the comparison slide](index.html#view=comparison) · [Read the comparison](SOURCE_RECORDS_VS_FLORA.md)","","A side-by-side comparison explains inputs, corrections, duplicate decisions, downloads, update timing and provenance. Three illustrative examples follow a DOI correction, a duplicate ruling and a new sheet row across the tabs, stored CSV/API and GitHub."]
    comparison_text=["# Source Records vs FLoRA","","[Open interactive comparison](index.html#view=comparison)","",data["comparison"]["nodes"][0]["summary"],"","| Question | Source Records tab | FLoRA tab |","| --- | --- | --- |"]
    for row in data["comparison"]["rows"]:
        comparison_text.append(f"| [{row['question']}](readmes/{row['node']}.md) | {row['source']} | {row['flora']} |")
    comparison_text += ["","## Follow an example","","These are illustrative scenarios, not live production records."]
    for case in data["comparison"]["scenarios"]:
        comparison_text += ["",f"### {case['title']}","",case["intro"],"",f"**Action:** {case['action']}",""]
        for key,label in [("source","Source Records"),("flora","FLoRA view"),("stored","Full CSV and API"),("github","GitHub")]:comparison_text += [f"- **{label}:** {case[key]}"]
        comparison_text += ["",f"[Explanation and exact code](readmes/{case['node']}.md)"]
    comparison_text += ["","[Complete comparison README and code references](readmes/compare-tabs.md)","","[Slide PNG](diagrams/comparison.png) · [Slide SVG](diagrams/comparison.svg)",""]
    (HERE/"SOURCE_RECORDS_VS_FLORA.md").write_text("\n".join(comparison_text),encoding="utf-8")
    readme += ["","## Important distinctions","","- Draft answers stay in the browser until submitted. Advancing to the next card does not prove the server saved the previous review.","- `validated` is the approved extractor-review table. It is not the complete prepared dataset.","- `source_records` is the combined input. The FLoRA grid derives a current view from it and saved decisions.","- `flora_data` is the stored prepared dataset used by the full CSV and public API.","- Saving eligible existing FLoRA fields updates their stored row immediately; duplicate rulings require preparation to update the full snapshot.","- Run artifacts, the current full export, and the GitHub CSV can differ because they capture different moments.","- The separate upstream extractor's internal discovery scripts are outside this checkout. This guide covers its input contract and everything downstream here.","","## Admin tabs and actions",""]
    for node in nodes:
        if node["group"]=="admin" and not node.get("parent"):
            readme += [f"### [{node['title']}](readmes/{node['id']}.md)","",node["summary"],""]
            readme += [f"- [{a['label']}](readmes/{a['node_id']}.md)" for a in node.get("actions",[])]+[""]
    readme += ["## Pipeline and storage READMEs",""]+[f"- [{n['title']}](readmes/{n['id']}.md)" for n in nodes if n["group"]!="admin"]
    readme += ["","## Maintain the guide","","The audited content lives in `admin-map.json`, `pipeline-map.json`, `review-map.json` and `comparison-map.json`. The build script pins source links to the reviewed commit; update both content and that commit after a new code audit. JSON content does not automatically stay accurate when the application changes.","","```powershell","python docs/pipeline-guide/build_guide.py","node docs/pipeline-guide/render_and_check.cjs","```","","The build reads source as text and never imports `app.py`, connects to PostgreSQL, runs a pipeline or edits production data. The renderer uses the repository's Playwright dependency to check navigation and create PNGs. Keep the complete folder for image downloads and individual Markdown files; `index.html` alone retains the interactive text and diagrams.",""]
    (HERE/"README.md").write_text("\n".join(readme),encoding="utf-8")
    print(f"Built {len(nodes)} READMEs, {len(data['diagrams'])} SVG diagrams and offline index.html.")


if __name__=="__main__":build()
