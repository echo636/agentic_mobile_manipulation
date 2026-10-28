'use strict';
const $ = id => document.getElementById(id);
const esc = x => String(x ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ended = t => ['passed', 'failed', 'blocked'].includes(t.status);
const validReplay = t => typeof t?.replay_url === 'string' && /^manipulation_runs\/[A-Za-z0-9_-]+\/replay\.html$/.test(t.replay_url);
const name = t => t.name || t.task || '未命名任务';
const stage = s => ({policy_running:'模型执行中', simulator_starting:'启动仿真', waiting_for_resources:'等待资源', preflight:'启动检查', complete:'已结束'}[s] || '等待调度');
const outcome = t => t.status === 'running' ? stage(t.stage) : t.status === 'planned' ? '待开始' : t.task_success === true ? '目标成功' : t.task_success === false ? '目标未完成' : t.status === 'blocked' ? '无评分 · 资源阻塞' : '无评分 · 运行故障';
const statusClass = t => t.status === 'running' ? 'in-progress' : t.status === 'planned' ? '' : t.task_success === true ? 'success' : t.task_success === false ? 'failure' : 'unscored';
let catalog = null, selected = null, current = null, refreshing = false, taskFilter = 'all';
const datasets = new Map(), frozen = new Map();
const params = new URLSearchParams(location.search);
const attempts = d => d.tasks.flatMap(t => [t, ...(t.previous_attempts || []).map(p => ({...t, ...p, index:t.index, historical:true}))]);
const source = () => catalog.sources.find(s => s.id === selected);
const data = () => datasets.get(selected);
const selectedTask = () => data()?.tasks.find(t => t.run_id === current || (t.previous_attempts || []).some(p => p.run_id === current));
const stats = d => {
  const done = d.tasks.filter(ended);
  return {total:d.tasks.length, done:done.length, success:done.filter(t => t.task_success === true).length,
    failed:done.filter(t => t.task_success === false).length, unscored:done.filter(t => t.task_success == null).length,
    running:d.tasks.filter(t => t.status === 'running').length, planned:d.tasks.filter(t => t.status === 'planned').length};
};

async function fetchJSON(path) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
  try {
    const url = new URL(path, location.href); url.searchParams.set('t', Date.now());
    const response = await fetch(url, {signal:controller.signal, cache:'no-store'});
    if (!response.ok) throw Error(response.status);
    return await response.json();
  } finally { clearTimeout(timer); }
}

// The embedded player owns its camera layout and timeline sizing. The portal
// supplies one viewport and never injects a competing stylesheet into it.
let frameObserver = null;
function fitReplay() {
  const frame = $('replay');
  if (frame.hidden) return;
  if (!mobile.matches) { frame.style.height = ''; return; }
  try {
    const workspace = frame.contentDocument?.querySelector('.trace-workspace:not([hidden]), .replay-workspace:not([hidden])');
    if (workspace) { const toolbar = frame.contentDocument.querySelector('.replay-viewbar'); frame.style.height = Math.ceil(workspace.getBoundingClientRect().height + (toolbar?.getBoundingClientRect().height || 0) + 2) + 'px'; }
  } catch (e) { /* The independent replay link remains available for other origins. */ }
}
$('replay').addEventListener('load', () => {
  $('replay').setAttribute('aria-busy', 'false');
  frameObserver?.disconnect();
  try {
    const workspace = $('replay').contentDocument?.querySelector('.trace-workspace:not([hidden]), .replay-workspace:not([hidden])');
    if (workspace) { frameObserver = new ResizeObserver(fitReplay); for (const panel of $('replay').contentDocument.querySelectorAll('.trace-workspace,.replay-workspace')) frameObserver.observe(panel); }
  } catch (e) { /* Cross-origin players retain their own scrolling viewport. */ }
  fitReplay();
});
addEventListener('resize', fitReplay);
const mobile = matchMedia('(max-width:760px)');
function setDrawer(open, focus = false) {
  const shown = Boolean(open && mobile.matches);
  document.body.classList.toggle('sidebar-open', shown);
  $('task-drawer').setAttribute('aria-expanded', String(shown));
  $('task-sidebar').inert = mobile.matches && !shown;
  if (focus) (shown ? $('task-search') : $('task-drawer')).focus();
}
$('task-drawer').onclick = () => setDrawer(!document.body.classList.contains('sidebar-open'), true);
$('drawer-close').onclick = () => setDrawer(false, true);
$('drawer-backdrop').onclick = () => setDrawer(false, true);
addEventListener('keydown', e => { if (e.key === 'Escape' && document.body.classList.contains('sidebar-open')) setDrawer(false, true); });
mobile.addEventListener('change', () => { setDrawer(false); fitReplay(); });
setDrawer(false);

function renderBatches() {
  $('batches').hidden = catalog.sources.length < 2;
  $('summary-link').hidden = !catalog.summary_url;
  if (catalog.summary_url) $('summary-link').href = catalog.summary_url;
  $('batches').innerHTML = catalog.sources.map(s => `<button class="batch" data-batch="${esc(s.id)}" aria-pressed="${s.id === selected}">${esc(s.label)}</button>`).join('');
  $('batches').querySelectorAll('[data-batch]').forEach(b => b.onclick = () => {
    if (selected === b.dataset.batch) return;
    const previousTask = selectedTask()?.task;
    selected = b.dataset.batch; current = null;
    const match = data()?.tasks.find(t => t.task?.toLowerCase() === previousTask?.toLowerCase());
    if (match) current = match.run_id;
    $('task-search').value = ''; taskFilter = 'all'; render();
  });
}

function renderDetails() {
  if (!data()) return;
  const ts = data().tasks, q = $('task-search').value.trim().toLowerCase(), activeTask = selectedTask();
  const rows = ts.filter(t => {
    if (q && ![t.task, t.name, t.instruction, Number(t.index) + 1].join(' ').toLowerCase().includes(q)) return false;
    if (taskFilter === 'success') return ended(t) && t.task_success === true;
    if (taskFilter === 'failure') return ended(t) && t.task_success !== true;
    if (taskFilter === 'active') return !ended(t);
    return true;
  });
  $('task-count').textContent = rows.length === ts.length ? ts.length + ' 条' : rows.length + ' / ' + ts.length + ' 条';
  document.querySelectorAll('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === taskFilter)));
  $('tasks').innerHTML = rows.length ? rows.map(t => {
    const qScore = Number(t.q_score), score = t.q_score == null || !Number.isFinite(qScore) ? '' : `Q ${qScore.toFixed(qScore === 0 || qScore === 1 ? 0 : 2)}`;
    return `<button class="task-button" data-run="${esc(t.run_id)}" aria-current="${t === activeTask}" title="${esc(t.instruction || name(t))}"><span class="task-index">${String(Number(t.index) + 1).padStart(2, '0')}</span><span><span class="task-title">${esc(name(t))}</span><span class="task-meta"><span class="${statusClass(t)}"><i class="status-dot" aria-hidden="true"></i>${esc(outcome(t))}</span>${score ? `<span>${score}</span>` : ''}${t.previous_attempts?.length ? `<span>${t.previous_attempts.length + 1} 次尝试</span>` : ''}<span class="task-replay">${validReplay(t) ? '回放 ↗' : ''}</span></span></span></button>`;
  }).join('') : '<p class="no-matches">没有符合条件的任务。</p>';
  $('tasks').querySelectorAll('[data-run]').forEach(b => b.onclick = () => { choose(b.dataset.run); setDrawer(false, mobile.matches); });
}

function renderAttemptSelector(task) {
  const parent = selectedTask() || task;
  const available = [parent, ...(parent.previous_attempts || []).map(p => ({...parent, ...p, index:parent.index, historical:true}))];
  $('run').innerHTML = available.map((t, i) => `<option value="${esc(t.run_id)}">${i ? '历史' : '最新'}${t.attempt == null ? '' : ' · 第 ' + esc(t.attempt) + ' 次'} · ${esc(outcome(t))}</option>`).join('');
  $('run').value = task.run_id;
  $('run').hidden = available.length < 2;
}

function choose(id) {
  const t = attempts(data()).find(t => t.run_id === id); if (!t) return;
  current = id;
  renderAttemptSelector(t);
  $('task-title').textContent = name(t);
  $('episode-label').textContent = `TASK ${String(Number(t.index) + 1).padStart(2, '0')} / ${data().tasks.length} · ${source().label}`;
  $('result').textContent = (t.historical ? '历史尝试 · ' : '') + outcome(t) + (t.actions == null ? '' : ' · ' + t.actions + ' 次动作');
  $('result').className = 'result ' + statusClass(t);
  $('instruction').textContent = t.instruction || '';
  const incomplete = ended(t) && t.task_success === true && t.status !== 'passed';
  $('run-diagnostic').hidden = !incomplete;
  $('run-diagnostic').textContent = incomplete ? '官方目标已满足，但运行未完整通过。' : '';
  const url = new URL(location.href); url.searchParams.set('batch', selected); url.searchParams.set('run', id);
  history.replaceState({}, '', url);
  const frame = $('replay');
  if (validReplay(t)) {
    const replay = new URL(t.replay_url, new URL(source().replay_base, location.href));
    $('open').href = replay.href; $('open').hidden = false;
    replay.searchParams.set('embed', '1');
    $('empty').hidden = true; frame.hidden = false;
    // Preserve playhead, playback and scroll position during progress polling.
    if (frame.getAttribute('src') !== replay.href) {
      frame.setAttribute('aria-busy', 'true');
      frame.src = replay.href;
    }
  } else {
    $('open').hidden = true; frame.hidden = true;
    if (frame.hasAttribute('src')) frame.removeAttribute('src');
    $('empty').hidden = false;
    $('empty').textContent = t.status === 'planned' ? '这个任务尚未开始。' : t.status === 'running' ? '任务正在运行，回放发布后会自动显示。' : '这次运行没有可用回放。';
  }
  renderDetails(); fitReplay();
}

function render() {
  renderBatches();
  $('batch-label').textContent = source()?.label || '本批任务';
  if (!data()) {
    $('error').textContent = '这个批次的结果暂时读取失败，将自动重试。'; $('error').hidden = false;
    $('replay').hidden = true; $('open').hidden = true; $('run').hidden = true;
    $('replay').removeAttribute('src');
    return;
  }
  $('error').hidden = true;
  const d = data(), n = stats(d), complete = n.total > 0 && n.done === n.total;
  // Batch boundaries and counts come exclusively from the deployed catalog.
  $('batch-note').textContent = source().note || '';
  $('ended').textContent = n.done + ' / ' + n.total; $('progress').max = Math.max(1, n.total); $('progress').value = n.done;
  $('passed').textContent = n.success; $('failed').textContent = n.failed; $('blocked').textContent = n.unscored;
  $('running').textContent = n.running; $('planned').textContent = n.planned;
  $('rate').textContent = complete ? '目标成功率 ' + (100 * n.success / n.total).toFixed(2) + '%' : '评测进行中';
  const time = new Date(d.updated_at), opts = {timeZone:'Asia/Shanghai', hour12:false};
  const stamp = Number.isNaN(time.valueOf()) ? '—' : time.toLocaleString('zh-CN', opts);
  $('updated').textContent = complete ? '本批已结束' : '更新 ' + (Number.isNaN(time.valueOf()) ? '—' : time.toLocaleTimeString('zh-CN', {...opts, hour:'2-digit', minute:'2-digit'}));
  $('updated').classList.toggle('completed', complete);
  $('updated').title = '批次记录：' + stamp + '（北京时间）；目标成功依据官方评分，无评分保留在全批总数中。';
  const available = attempts(d);
  const target = available.find(t => t.run_id === current) || d.tasks.find(t => t.task_success === true && validReplay(t)) || d.tasks.find(validReplay) || d.tasks[0];
  if (target) choose(target.run_id);
  else { $('empty').hidden = false; $('empty').textContent = '本批尚未登记任务。'; renderDetails(); }
}

async function refresh() {
  if (refreshing || !catalog) return;
  refreshing = true;
  try {
    const paths = [...new Set(catalog.sources.map(s => s.data_url))];
    const responses = await Promise.allSettled(paths.map(p => frozen.has(p) ? Promise.resolve(frozen.get(p)) : fetchJSON(p)));
    let selectedFailed = false;
    for (const s of catalog.sources) {
      const result = responses[paths.indexOf(s.data_url)];
      if (result.status !== 'fulfilled') { if (s.id === selected) selectedFailed = true; continue; }
      const raw = result.value;
      const normalized = s.arm ? {updated_at:raw.arms[s.arm].updated_at || raw.updated_at, tasks:raw.tasks.map(t => t.arms[s.arm])} : raw;
      if (!Array.isArray(normalized.tasks)) throw Error('Invalid result data');
      datasets.set(s.id, normalized); if (s.closed) frozen.set(s.data_url, raw);
    }
    render();
    if (selectedFailed) {
      $('error').textContent = '进度暂未更新，正在重试；已有回放可以继续观看。'; $('error').hidden = false;
      $('updated').textContent = '更新暂停';
    }
  } catch (e) {
    $('error').textContent = '结果暂时读取失败，将自动重试。'; $('error').hidden = false;
    $('updated').textContent = '更新暂停';
  } finally { refreshing = false; }
}

$('run').onchange = e => choose(e.target.value);
$('task-search').oninput = renderDetails;
document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => { taskFilter = b.dataset.filter; renderDetails(); });
async function start() {
  try { catalog = await fetchJSON('results_sources.json'); }
  catch (e) { catalog = {default:'local', sources:[{id:'local', label:'任务结果', data_url:'behavior100/portal_progress.json', replay_base:'./'}]}; }
  const requested = catalog.sources.find(s => s.id === params.get('batch'));
  const inferred = catalog.sources.find(s => (s.run_prefixes || []).some(p => (params.get('run') || '').startsWith(p)));
  selected = requested?.id || inferred?.id || catalog.default || catalog.sources[0].id;
  current = params.get('run');
  await refresh(); setInterval(refresh, 15000);
}
start();
