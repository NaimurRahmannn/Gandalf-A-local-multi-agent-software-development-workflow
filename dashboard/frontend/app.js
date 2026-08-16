const app = document.querySelector('#app');
const title = document.querySelector('#page-title');
const flashBox = document.querySelector('#flash');
let stream;
const eventCursors = {};

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const statusPill = value => `<span class="status ${esc(value)}">${esc(value)}</span>`;
const fmt = value => value ? new Date(value).toLocaleString() : '—';
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json', ...(options.headers||{})}, ...options});
  if (!response.ok) { const body = await response.json().catch(()=>({detail:response.statusText})); throw new Error(body.detail || response.statusText); }
  return response.json();
}
function flash(message, error=false) { flashBox.innerHTML = `<div class="flash ${error?'error':''}">${esc(message)}</div>`; setTimeout(()=>flashBox.innerHTML='', 5000); }
function setTitle(value) { title.textContent = value; }
function phaseRow(p) { return `<div class="list-item"><div><a href="#/phases/${p.id}">${esc(p.prompt)}</a><div class="subtle">${fmt(p.updated_at)} · ${esc(p.current_agent || 'No active agent')}</div></div>${statusPill(p.status)}</div>`; }

async function loadChrome() {
  const [agents, notices] = await Promise.all([api('/agents/status'), api('/notifications?unread_only=true')]);
  document.querySelector('#agent-status').innerHTML = agents.map(a=>`<div class="agent-row"><span class="dot ${a.enabled&&a.installed?'online':''}"></span>${esc(a.name)}<span class="subtle">${a.installed?'ready':'missing'}</span></div>`).join('');
  document.querySelector('#notice-count').textContent = notices.length;
}
async function home() {
  setTitle('Engineering overview');
  const [projects, phases, notices] = await Promise.all([api('/projects'), api('/phases'), api('/notifications?unread_only=true')]);
  const active = phases.filter(p=>!['COMPLETED','FAILED'].includes(p.status));
  const completed = phases.filter(p=>p.status==='COMPLETED');
  const failed = phases.filter(p=>p.status==='FAILED');
  app.innerHTML = `<div class="grid">
    ${[['Projects',projects.length],['Active phases',active.length],['Completed',completed.length],['Failed',failed.length]].map(([n,v])=>`<div class="card span-4 metric"><p class="eyebrow">${n}</p><strong>${v}</strong></div>`).join('')}
    <div class="card span-8"><h2>Active work</h2><div class="list">${active.map(phaseRow).join('') || '<div class="empty">No active phases</div>'}</div></div>
    <div class="card span-4"><h2>Notifications</h2><div class="list">${notices.slice(0,6).map(n=>`<div class="list-item"><div><strong>${esc(n.message)}</strong><div class="subtle">${fmt(n.created_at)}</div></div></div>`).join('') || '<div class="empty">All clear</div>'}</div></div>
    <div class="card span-12"><h2>Projects</h2><div class="list">${projects.map(p=>`<div class="list-item"><div><a href="#/projects/${p.id}">${esc(p.name)}</a><div class="subtle">${p.phase_count} phases · ${esc(p.root_path)}</div></div><a href="#/projects/${p.id}">Open →</a></div>`).join('') || '<div class="empty">Create your first project from Projects.</div>'}</div></div>
  </div>`;
}
async function projectsPage() {
  setTitle('Projects'); const projects = await api('/projects');
  app.innerHTML = `<div class="grid"><div class="card span-8"><h2>Managed projects</h2><div class="list">${projects.map(p=>`<div class="list-item"><div><a href="#/projects/${p.id}">${esc(p.name)}</a><div class="subtle">${esc(p.root_path)}</div></div><span>${p.phase_count} phases</span></div>`).join('') || '<div class="empty">No projects yet</div>'}</div></div>
  <form id="create-project" class="card span-4 form"><h2>Create project</h2><label>Name<input name="name" maxlength="120" required placeholder="Project Atlas"></label><button>Create project</button><p class="subtle">Creates an isolated folder, memory store, and Git repository.</p></form></div>`;
  document.querySelector('#create-project').onsubmit = async e => { e.preventDefault(); try { const p=await api('/projects',{method:'POST',body:JSON.stringify({name:new FormData(e.target).get('name')})}); location.hash=`#/projects/${p.id}`; } catch(err){flash(err.message,true);} };
}
async function projectPage(id) {
  const project = await api(`/projects/${id}`); setTitle(project.name);
  const memoryNames = Object.keys(project.memory);
  app.innerHTML = `<div class="grid"><div class="card span-8"><p class="eyebrow">Project root</p><p>${esc(project.root_path)}</p><h2>Phases</h2><div class="list">${project.phases.map(phaseRow).join('') || '<div class="empty">No phases yet</div>'}</div></div>
  <form id="start-phase" class="card span-4 form"><h2>Start a phase</h2><label>Goal<textarea name="prompt" required placeholder="Build the authentication system"></textarea></label><button>Start AI team</button></form>
  <div class="card span-12"><h2>Project memory</h2><div class="tabs">${memoryNames.map((n,i)=>`<button data-memory="${esc(n)}" class="${i?'':'active'}">${esc(n)}</button>`).join('')}</div><pre id="memory-view">${esc(project.memory[memoryNames[0]])}</pre></div></div>`;
  document.querySelectorAll('[data-memory]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-memory]').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelector('#memory-view').textContent=project.memory[b.dataset.memory];});
  document.querySelector('#start-phase').onsubmit = async e => { e.preventDefault(); try { const p=await api('/phases/start',{method:'POST',body:JSON.stringify({project_id:id,prompt:new FormData(e.target).get('prompt')})}); location.hash=`#/phases/${p.id}`; } catch(err){flash(err.message,true);} };
}
async function phasePage(id) {
  const phase = await api(`/phases/${id}`); setTitle('Phase control');
  if (phase.events.length) eventCursors[id] = Number(phase.events[phase.events.length - 1].id);
  const pending = phase.approvals.find(a=>a.status==='pending');
  const artifactNames = Object.keys(phase.artifacts||{}).filter(n=>phase.artifacts[n]);
  app.innerHTML = `<div class="grid">
    <div class="card span-8"><p class="eyebrow">Goal</p><h2>${esc(phase.prompt)}</h2><div class="actions">${statusPill(phase.status)}<span class="subtle">Current agent: ${esc(phase.current_agent||'none')}</span></div></div>
    <div class="card span-4 metric"><p class="eyebrow">Last update</p><strong style="font-size:20px">${fmt(phase.updated_at)}</strong><small>${esc(phase.error||'No errors')}</small></div>
    ${phase.status==='FAILED' ? `<div class="card span-12 approval"><p class="eyebrow">Recoverable failure</p><h2>Continue from the failed step</h2><p>The workflow will reuse completed planning and implementation handoffs, then retry its persisted next action.</p><div class="actions"><button id="resume-phase">Resume from failure</button></div></div>`:''}
    ${pending ? `<div class="card span-12 approval"><p class="eyebrow">Approval gate · ${esc(pending.gate)}</p><h2>Human decision required</h2><p>Review the changes, Cursor findings, Antigravity decision, and test results below.</p><label>Feedback<textarea id="approval-feedback" placeholder="Optional for approve/reject; required when requesting changes"></textarea></label><div class="actions"><button data-decision="approve">Approve</button><button class="warn" data-decision="request-changes">Request changes</button><button class="danger" data-decision="reject">Reject</button></div></div>`:''}
    <div class="card span-6"><h2>Activity</h2><div class="timeline">${phase.events.map(e=>`<div class="event"><strong>${esc(e.status)}</strong><p>${esc(e.message)}</p><small class="subtle">${fmt(e.created_at)} · ${esc(e.current_agent||'system')}</small></div>`).join('')}</div></div>
    <div class="card span-6"><h2>Generated files</h2><div class="tabs">${artifactNames.map((n,i)=>`<button data-artifact="${esc(n)}" class="${i?'':'active'}">${esc(n)}</button>`).join('')}</div><pre id="artifact-view">${esc(artifactNames.length?phase.artifacts[artifactNames[0]]:'No artifacts yet.')}</pre></div>
    <div class="card span-12"><div class="list-item"><h2>Execution logs</h2><button id="load-logs" class="secondary">Load logs</button></div><pre id="logs-view">Logs load on request and refresh while this page is open.</pre></div>
  </div>`;
  document.querySelectorAll('[data-artifact]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-artifact]').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelector('#artifact-view').textContent=phase.artifacts[b.dataset.artifact];});
  document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=async()=>{const action=b.dataset.decision, feedback=document.querySelector('#approval-feedback').value; try{await api(`/phases/${id}/${action}`,{method:'POST',body:JSON.stringify({feedback})});flash(`Decision recorded: ${action}`);setTimeout(route,500);}catch(err){flash(err.message,true);}});
  const resumeButton=document.querySelector('#resume-phase');
  if(resumeButton) resumeButton.onclick=async()=>{resumeButton.disabled=true;try{await api(`/phases/${id}/resume`,{method:'POST'});flash('Resume scheduled from the failed step');setTimeout(route,500);}catch(err){resumeButton.disabled=false;flash(err.message,true);}};
  const loadLogs=async()=>{try{const logs=await api(`/logs/${id}`);document.querySelector('#logs-view').textContent=Object.entries(logs).map(([n,v])=>`===== ${n} =====\n${v}`).join('\n\n')||'No logs yet.';}catch(err){flash(err.message,true);}};
  document.querySelector('#load-logs').onclick=loadLogs;
  if(stream) stream.close();
  stream = new EventSource(`/events/${id}?after_id=${eventCursors[id] || 0}`);
  stream.addEventListener('phase', event => {
    eventCursors[id] = Number(event.lastEventId) || eventCursors[id] || 0;
    route(false);
  });
}
async function notificationsPage(){setTitle('Notifications');const notices=await api('/notifications');app.innerHTML=`<div class="card"><h2>Activity inbox</h2><div class="list">${notices.map(n=>`<div class="list-item"><div><strong>${esc(n.message)}</strong><div class="subtle">${esc(n.level)} · ${fmt(n.created_at)}</div></div>${n.is_read?'':`<button data-read="${n.id}" class="secondary">Mark read</button>`}</div>`).join('')||'<div class="empty">No notifications</div>'}</div></div>`;document.querySelectorAll('[data-read]').forEach(b=>b.onclick=async()=>{await api(`/notifications/${b.dataset.read}/read`,{method:'POST'});notificationsPage();});}
async function route(showLoading=true){if(stream){stream.close();stream=null;}if(showLoading)app.innerHTML='<div class="loading">Loading…</div>';const parts=location.hash.replace(/^#\/?/,'').split('/').filter(Boolean);try{if(parts[0]==='projects'&&parts[1])await projectPage(parts[1]);else if(parts[0]==='projects')await projectsPage();else if(parts[0]==='phases'&&parts[1])await phasePage(parts[1]);else if(parts[0]==='notifications')await notificationsPage();else await home();await loadChrome();}catch(err){app.innerHTML=`<div class="flash error">${esc(err.message)}</div>`;}}
window.addEventListener('hashchange',()=>route());document.querySelector('#refresh').onclick=()=>route();route();
