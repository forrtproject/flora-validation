'use strict';
const DATA=JSON.parse(document.getElementById('guide-data').textContent);
const byId=new Map(DATA.nodes.map(node=>[node.id,node]));
const $=selector=>document.querySelector(selector);
const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
let currentView='overview', selectedId=null, currentTab='All tabs', tourIndex=-1, currentScenario='correction';
const views=[['overview','System map'],['comparison','Source vs FLoRA'],['inputs','Inputs'],['processing','Processing'],['outputs','Outputs'],['admin','Admin tabs'],['timing','Save & update'],['code','Code index']];
function sourceURL(source){return `${DATA.repository}/blob/${DATA.commit}/${source.file}#L${source.line}`;}
function nodeLink(id){return `#view=${currentView}&node=${encodeURIComponent(id)}`;}
function selectNode(id){if(!byId.has(id))return; location.hash=`view=${currentView}&node=${encodeURIComponent(id)}`;}
function nodeButton(node){return `<button class="node-card" data-node="${escapeHTML(node.id)}"><small>${escapeHTML(node.tab||node.group)}</small><strong>${escapeHTML(node.title)}</strong><p>${escapeHTML(node.summary)}</p><span class="arrow">Open README & source →</span></button>`;}
function renderView(){
  $('.view-nav').innerHTML=views.map(([id,title],i)=>`<button data-view="${id}" ${currentView===id?'aria-current="page"':''}><span>0${i+1}</span>${title}</button>`).join('');
  const view=DATA.views[currentView];
  $('#view-kicker').textContent=view.kicker;$('#view-title').textContent=view.title;$('#view-description').textContent=view.description;
  $('#canvas-tools').innerHTML='';
  if(DATA.diagrams[currentView]){
    $('#canvas-tools').innerHTML=`<button class="quiet" id="zoom-map" aria-pressed="false">Zoom 1:1</button><a href="diagrams/${currentView}.svg" download>SVG ↧</a><a href="diagrams/${currentView}.png" download>PNG ↧</a>`;
    $('#view-content').innerHTML=`<div class="diagram-shell">${DATA.diagrams[currentView]}</div><div class="map-hint"><span>Each box opens its README. Scroll the map sideways on a small screen.</span><div class="legend"><span>Data / process</span><span>Human decision</span><span>Published output</span></div></div>${view.notes?`<div class="milestones">${view.notes.map(n=>`<div class="milestone"><b>${escapeHTML(n.title)}</b><p>${escapeHTML(n.text)}</p></div>`).join('')}</div>`:''}${currentView==='overview'?'<div class="walkthrough"><p><strong>Try a real journey.</strong> Follow one extracted paper from import to public lookup.</p><button id="tour-start">Start walkthrough →</button><button id="tour-next" hidden>Next step →</button><span id="tour-step"></span></div>':''}`;
  }else if(currentView==='admin')renderAdmin();
  else if(currentView==='timing')renderTiming();
  else renderCode();
  if(currentView==='comparison')renderComparison();
  highlightSelected();
}
function renderComparison(){
  $('#view-content').insertAdjacentHTML('beforeend',`<section class="comparison-reading" aria-labelledby="comparison-heading"><div class="comparison-heading"><div><p class="eyebrow">TWO TABS, DIFFERENT JOBS</p><h3 id="comparison-heading">Which tab should I use?</h3></div><a href="SOURCE_RECORDS_VS_FLORA.md">Read as text ↗</a></div><div class="table-scroll"><table class="timing-table comparison-table"><thead><tr><th scope="col">Your question</th><th scope="col">Source Records</th><th scope="col">FLoRA</th></tr></thead><tbody>${DATA.comparison.rows.map(row=>`<tr><th scope="row"><button data-node="${escapeHTML(row.node)}">${escapeHTML(row.question)}</button></th><td>${escapeHTML(row.source)}</td><td>${escapeHTML(row.flora)}</td></tr>`).join('')}</tbody></table></div><p class="section-note"><strong>Why the numbers can differ:</strong> several source rows may combine into one FLoRA record, while excluded rows leave the derived view. The stored dataset can also be behind that view until preparation finishes. <a href="${nodeLink('compare-tabs')}" data-node="compare-tabs">Open the full explanation and code →</a></p></section><section class="comparison-examples" aria-labelledby="example-heading"><p class="eyebrow">ILLUSTRATIVE EXAMPLES · NO LIVE DATA</p><h3 id="example-heading">Follow one action across the system.</h3><div class="scenario-options" aria-label="Choose an example">${DATA.comparison.scenarios.map(caseData=>`<button data-scenario="${caseData.id}" aria-pressed="${caseData.id===currentScenario}">${escapeHTML(caseData.title)}</button>`).join('')}</div><div id="scenario-detail"></div></section>`);
  renderScenario();
}
function renderScenario(){
  const example=DATA.comparison.scenarios.find(item=>item.id===currentScenario)||DATA.comparison.scenarios[0];
  document.querySelectorAll('[data-scenario]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.scenario===example.id)));
  $('#scenario-detail').innerHTML=`<p class="scenario-intro">${escapeHTML(example.intro)}</p><p class="scenario-action"><strong>Action:</strong> ${escapeHTML(example.action)}</p><div class="scenario-grid">${[['source','Source Records'],['flora','FLoRA view'],['stored','Full CSV + API'],['github','GitHub release']].map(([key,label],index)=>`<section class="scenario-step"><span class="eyebrow">0${index+1} / ${label}</span><p>${escapeHTML(example[key])}</p></section>`).join('')}</div><button class="quiet scenario-code" data-node="${escapeHTML(example.node)}">Open this action’s README & source →</button>`;
}
function renderAdmin(){
  const nodes=DATA.nodes.filter(n=>n.group==='admin');
  const tabs=['All tabs',...new Set(nodes.map(n=>n.tab||n.title))];
  if(!tabs.includes(currentTab))currentTab='All tabs';
  $('#view-content').innerHTML=`<div class="catalog-tabs">${tabs.map(t=>`<button data-tab="${escapeHTML(t)}" class="${t===currentTab?'active':''}" aria-pressed="${t===currentTab}">${escapeHTML(t)}</button>`).join('')}</div><div class="card-grid">${nodes.filter(n=>currentTab==='All tabs'?!n.parent:(n.tab||n.title)===currentTab).map(nodeButton).join('')}</div>`;
}
function renderTiming(){
  $('#view-content').innerHTML=`<div class="section-note"><strong>Three different meanings of “updated”.</strong> A saved decision can change the admin view before the prepared dataset changes. A file on GitHub changes only when a publishing workflow commits it.</div><div class="table-scroll"><table class="timing-table"><thead><tr><th>Action / trigger</th><th>Saved where?</th><th>When do I see it?</th><th>Next step</th></tr></thead><tbody>${DATA.timing.map(r=>`<tr><td><button data-node="${escapeHTML(r.node)}">${escapeHTML(r.action)}</button></td><td>${escapeHTML(r.where)}</td><td>${escapeHTML(r.when)}</td><td>${escapeHTML(r.next)}</td></tr>`).join('')}</tbody></table></div><h3>The clocks behind the screen</h3><p class="view-description">These are configured triggers, not completion promises. All schedules below are UTC. Berlin is UTC+1 in winter and UTC+2 in summer.</p><div class="table-scroll"><table class="timing-table"><thead><tr><th>Job</th><th>Trigger</th><th>What it changes</th></tr></thead><tbody>${DATA.schedule.map(r=>`<tr><td><button data-node="${escapeHTML(r.node)}">${escapeHTML(r.job)}</button></td><td>${escapeHTML(r.trigger)}</td><td>${escapeHTML(r.effect)}</td></tr>`).join('')}</tbody></table></div><h3>Where everything lives</h3><p class="view-description"><a href="diagrams/storage.svg" download>Storage SVG ↧</a> · <a href="diagrams/storage.png" download>Storage PNG ↧</a></p><div class="diagram-shell">${DATA.diagrams.storage}</div><div class="card-grid" style="margin-top:20px">${DATA.nodes.filter(n=>n.group==='storage').map(nodeButton).join('')}</div>`;
}
function renderCode(){
  const files=new Map();for(const node of DATA.nodes)for(const s of node.sources||[]){if(!files.has(s.file))files.set(s.file,[]);if(!files.get(s.file).some(n=>n.id===node.id))files.get(s.file).push(node);}
  $('#view-content').innerHTML=`<div class="section-note">Source links open the exact reviewed Git commit. Each README includes a short code excerpt, a line link, and a link to its complete script. The guide itself also works without an internet connection.</div><div class="code-list">${[...files].sort(([a],[b])=>a.localeCompare(b)).map(([file,nodes])=>`<section class="code-group"><h3><a href="${DATA.repository}/blob/${DATA.commit}/${file}" target="_blank" rel="noopener">${escapeHTML(file)} ↗</a></h3>${nodes.map(n=>`<button data-node="${escapeHTML(n.id)}">${escapeHTML(n.title)}</button>`).join('')}</section>`).join('')}</div>`;
}
function listSection(title,items,ordered=false){if(!items?.length)return '';const tag=ordered?'ol':'ul';return `<h3>${title}</h3><${tag}>${items.map(i=>`<li>${escapeHTML(typeof i==='string'?i:JSON.stringify(i))}</li>`).join('')}</${tag}>`;}
function renderDetail(id){
  const node=byId.get(id);selectedId=node?id:null;$('#detail-panel').hidden=!node;
  if(!node){highlightSelected();return;}
  $('#detail-content').innerHTML=`${node.parent?`<p><a href="${nodeLink(node.parent)}" data-node="${node.parent}">← ${escapeHTML(node.tab)} tab overview</a></p>`:""}<p class="eyebrow">${escapeHTML(node.tab||node.group)}</p><h2 tabindex="-1" id="detail-title">${escapeHTML(node.title)}</h2><p class="summary">${escapeHTML(node.summary)}</p><div class="detail-links"><a href="readmes/${node.id}.md">Text README ↗</a><button id="copy-link">Copy link</button></div>${listSection('What goes in',node.inputs)}${listSection('What happens',node.steps,true)}${listSection('What is saved, and where',node.saves)}${listSection('When it updates',node.refresh)}${listSection('What comes out',node.outputs)}${node.actions?.length?`<h3>Buttons & actions</h3>${node.actions.map(a=>`<section class="action-block"><h4><a href="${nodeLink(a.node_id)}" data-node="${escapeHTML(a.node_id)}">${escapeHTML(a.label)} →</a></h4><p>${escapeHTML(a.does)}</p><p><strong>Saves:</strong> ${escapeHTML(a.saves)}</p><p><strong>Visible:</strong> ${escapeHTML(a.when)}</p>${(a.sources||[]).map(s=>`<a class="code-ref" href="${sourceURL(s)}" target="_blank" rel="noopener">${escapeHTML(s.file)}:${s.line}</a>`).join('')}</section>`).join('')}`:''}${node.cautions?.length?`<div class="caution">${listSection('Keep in mind',node.cautions)}</div>`:''}<h3>Find it in the code</h3>${(node.sources||[]).map((s,i)=>`<div><a class="code-ref" href="${sourceURL(s)}" target="_blank" rel="noopener">${escapeHTML(s.file)}:${s.line} ↗<span>${escapeHTML(s.symbol||'Source implementation')}</span></a><details class="source-details"><summary>Read code here / full script</summary><p><a href="${DATA.repository}/blob/${DATA.commit}/${s.file}" target="_blank" rel="noopener">Open complete ${escapeHTML(s.file)} ↗</a></p><pre class="source-excerpt">${escapeHTML(s.excerpt||'See linked source for implementation.')}</pre></details></div>`).join('')}`;
  highlightSelected();
}
function highlightSelected(){document.querySelectorAll('.diagram-shell a[data-node]').forEach(el=>el.classList.toggle('selected',el.dataset.node===selectedId));}
function syncRoute(){const p=new URLSearchParams(location.hash.slice(1));let view=p.get('view')||'overview';if(!views.some(([id])=>id===view))view='overview';const changed=currentView!==view;currentView=view;if(changed||!$('#view-content').children.length)renderView();renderDetail(p.get('node'));}
function search(){const query=$('#search').value.trim().toLowerCase();if(!query){$('#search-results').innerHTML='';$('#search-count').textContent=`${DATA.nodes.length} element READMEs · 11 admin tabs · links to the exact code`;return;}const matches=DATA.nodes.filter(n=>JSON.stringify({...n,sources:(n.sources||[]).map(s=>({file:s.file,symbol:s.symbol}))}).toLowerCase().includes(query));$('#search-count').textContent=`${matches.length} matching element${matches.length===1?'':'s'}`;$('#search-results').innerHTML=matches.length?matches.map(n=>`<button class="search-result" data-node="${escapeHTML(n.id)}">${escapeHTML(n.title)}<span>${escapeHTML(n.tab||n.group)}</span></button>`).join(''):'<p class="no-results">No matching element. Try a tab name, button label, table or script filename.</p>';}
document.addEventListener('click',event=>{
  const target=event.target.closest('[data-view],[data-node],[data-tab],[data-scenario]');if(!target)return;
  if(target.dataset.scenario){currentScenario=target.dataset.scenario;renderScenario();return;}
  if(target.dataset.view){currentTab='All tabs';location.hash=`view=${target.dataset.view}`;return;}
  if(target.dataset.tab){currentTab=target.dataset.tab;renderAdmin();return;}
  if(target.dataset.node){event.preventDefault();selectNode(target.dataset.node);requestAnimationFrame(()=>{if(innerWidth<=1100)$('#detail-panel').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});$('#detail-title')?.focus({preventScroll:true});});}
});
document.addEventListener('click',async event=>{
  if(event.target.id==='zoom-map'){const svg=$('.diagram-shell svg');const zoom=event.target.getAttribute('aria-pressed')!=='true';svg.style.width=zoom?`${svg.viewBox.baseVal.width}px`:'100%';event.target.setAttribute('aria-pressed',String(zoom));event.target.textContent=zoom?'Fit map':'Zoom 1:1';}
  if(event.target.id==='tour-start'||event.target.id==='tour-next'){tourIndex=event.target.id==='tour-start'?0:(tourIndex+1)%DATA.tour.length;selectNode(DATA.tour[tourIndex]);$('#tour-next').hidden=false;$('#tour-start').textContent='Restart';$('#tour-step').textContent=`${tourIndex+1} / ${DATA.tour.length}`;}
  if(event.target.id==='copy-link'){try{await navigator.clipboard.writeText(location.href);event.target.textContent='Copied';}catch{event.target.textContent='Copy address from browser';}}
});
$('#close-detail').addEventListener('click',()=>{location.hash=`view=${currentView}`;});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&selectedId){location.hash=`view=${currentView}`;$('.view-nav button[aria-current=page]')?.focus();}});
$('#print-button').addEventListener('click',()=>window.print());$('#search').addEventListener('input',search);window.addEventListener('hashchange',syncRoute);
$('#snapshot-date').textContent=DATA.date;$('#commit-link').href=`${DATA.repository}/tree/${DATA.commit}`;$('#commit-link').textContent=`commit ${DATA.commit.slice(0,7)}`;syncRoute();search();
