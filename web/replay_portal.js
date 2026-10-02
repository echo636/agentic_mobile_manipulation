'use strict';
const $ = id => document.getElementById(id);
const esc = x => String(x ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ended = t => ['passed', 'failed', 'blocked'].includes(t.status);
const validReplay = t => typeof t?.replay_url === 'string' && /^manipulation_runs\/[A-Za-z0-9_-]+\/replay\.html$/.test(t.replay_url);
const name = t => t.name || t.task;
const stage = s => ({policy_running:'模型执行中', simulator_starting:'启动仿真', waiting_for_resources:'等待资源', preflight:'启动检查', complete:'已结束'}[s] || '等待调度');
const outcome = t => t.status === 'running' ? stage(t.stage) : t.status === 'planned' ? '待开始' : t.task_success === true ? '目标成功' : t.task_success === false ? '目标未完成' : t.status === 'blocked' ? '无评分 · 资源阻塞' : '无评分 · 运行故障';
let catalog = null, selected = null, current = null, frameObserver = null, refreshing = false;
const datasets = new Map(), frozen = new Map();
const params = new URLSearchParams(location.search);
const attempts = data => data.tasks.flatMap(t => [t, ...(t.previous_attempts || []).map(p => ({...t, ...p, index:t.index, historical:true}))]);
const source = () => catalog.sources.find(s => s.id === selected);
const data = () => datasets.get(selected);
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

function fitReplay() {
  const frame = $('replay'); if (frame.hidden) return;
  if (frame.clientWidth > 680) {
    const top = frame.getBoundingClientRect().top + scrollY;
    frame.style.height = Math.max(560, Math.min(1050, innerHeight - top - 22)) + 'px';
  } else {
    const workspace = frame.contentDocument?.querySelector('.replay-workspace');
    if (workspace) frame.style.height = Math.ceil(workspace.getBoundingClientRect().height + 2) + 'px';
  }
}

$('replay').addEventListener('load', () => {
  const doc = $('replay').contentDocument; if (!doc?.querySelector('.replay-workspace')) return;
  const style = doc.createElement('style'); style.textContent = `
    html,body{margin:0;background:transparent}body.embedded main{max-width:none;padding:0}
    .embedded .archive{display:none}.embedded .panel-heading .eyebrow{display:none}
    .embedded .panel-heading{padding:13px 16px}.embedded .panel-heading h2{font-size:16px}
    @media(min-width:681px){.embedded .replay-workspace{height:100vh;gap:14px}
      .embedded .observation-panel{display:flex;flex-direction:column;min-height:0}
      .embedded .camera-grid{height:auto;min-height:180px;flex:1}
      .embedded .observation-panel>div:not(.camera-grid){flex-shrink:0}}
  `;
  doc.head.append(style); frameObserver?.disconnect();
  frameObserver = new ResizeObserver(() => { if ($('replay').clientWidth <= 680) fitReplay(); });
  frameObserver.observe(doc.querySelector('.replay-workspace')); fitReplay();
});
addEventListener('resize', fitReplay);

function renderBatches() {
  $('batches').innerHTML = catalog.sources.map(s => {
    const d = datasets.get(s.id), n = d && stats(d);
    return `<button class="batch" data-batch="${esc(s.id)}" aria-pressed="${s.id === selected}"><strong>${esc(s.label)}</strong><small>${n ? `目标成功 ${n.success} · 已结束 ${n.done}/${n.total}${n.done === n.total ? ' · 已结束' : ''}` : '正在读取…'}</small></button>`;
  }).join('');
  $('batches').querySelectorAll('[data-batch]').forEach(b => b.onclick = () => {
    const previousTask = data()?.tasks.find(t => t.run_id === current)?.task;
    selected = b.dataset.batch; current = null;
    const match = data()?.tasks.find(t => t.task.toLowerCase() === previousTask?.toLowerCase());
    if (match) current = match.run_id;
    $('task-search').value = ''; render();
  });
}

function renderDetails() {
  if (!data()) return;
  const ts = data().tasks, q = $('task-search').value.trim().toLowerCase();
  $('task-count').textContent = ts.length + ' 条';
  $('workers').textContent = ts.filter(t => t.status === 'running').map(t => `${name(t)} · ${stage(t.stage)}`).join('；') || '当前无运行中的任务';
  $('tasks').innerHTML = ts.filter(t => !q || [t.task, t.name, t.instruction].join(' ').toLowerCase().includes(q)).map(t => `<tr><td>${t.index + 1}</td><td><button class="task-button" data-run="${esc(t.run_id)}" aria-current="${t.run_id === current}">${esc(name(t))}</button></td><td>${esc(outcome(t))}</td><td>${t.q_score == null ? '—' : Number(t.q_score).toFixed(3)}</td><td>${esc(t.actions)}</td></tr>`).join('');
  $('tasks').querySelectorAll('[data-run]').forEach(b => b.onclick = () => { choose(b.dataset.run); $('run').focus(); scrollTo({top:0, behavior:'smooth'}); });
}

function choose(id) {
  const t = attempts(data()).find(t => t.run_id === id); if (!t) return;
  current = id; $('run').value = id;
  $('result').textContent = (t.historical ? '历史尝试 · ' : '') + outcome(t) + (t.actions == null ? '' : ' · ' + t.actions + ' 次动作');
  $('result').className = 'result ' + (t.task_success === true ? 'success' : t.task_success === false ? 'failure' : 'unscored');
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
    // Never reload an unchanged episode during progress polling.
    if (frame.getAttribute('src') !== replay.href) frame.src = replay.href;
  } else {
    $('open').hidden = true; frame.hidden = true;
    // Stop an old episode if the selected task has no replay yet.
    if (frame.hasAttribute('src')) frame.removeAttribute('src');
    $('empty').hidden = false;
    $('empty').textContent = t.status === 'planned' ? '这个任务尚未开始。' : t.status === 'running' ? '任务正在运行，回放发布后会自动显示。' : '这次运行没有可用回放。';
  }
  renderDetails(); fitReplay();
}

function render() {
  renderBatches();
  if (!data()) {
    $('error').textContent = '这个批次的结果暂时读取失败，将自动重试。'; $('error').hidden = false;
    $('replay').hidden = true; $('open').hidden = true; $('run').disabled = true;
    return;
  }
  $('error').hidden = true;
  const d = data(), n = stats(d), complete = n.done === n.total;
  let note = source().note || '';
  const historical = datasets.get('history32'), original = datasets.get('original100');
  if (historical && original && ['history32', 'original100'].includes(selected)) {
    const previous = new Set(historical.tasks.map(t => t.task.toLowerCase()));
    const matched = original.tasks.filter(t => previous.has(t.task.toLowerCase()));
    if (matched.length === historical.tasks.length && matched.every(ended)) {
      note += ` 同样的 ${matched.length} 条任务：历史成功 ${stats(historical).success} 条，本轮原版成功 ${matched.filter(t => t.task_success === true).length} 条。`;
    }
  }
  $('batch-note').textContent = note;
  $('ended').textContent = n.done + ' / ' + n.total; $('progress').max = n.total; $('progress').value = n.done;
  $('passed').textContent = n.success; $('failed').textContent = n.failed; $('blocked').textContent = n.unscored;
  $('running').textContent = n.running; $('planned').textContent = n.planned;
  $('rate').textContent = complete ? '全批成功率 ' + (100 * n.success / n.total).toFixed(2) + '%' : '评测未结束';
  const time = new Date(d.updated_at), opts = {timeZone:'Asia/Shanghai', hour12:false};
  const stamp = Number.isNaN(time.valueOf()) ? '—' : time.toLocaleString('zh-CN', opts);
  $('updated').textContent = complete ? '本批已结束' : '更新 ' + time.toLocaleTimeString('zh-CN', {...opts, hour:'2-digit', minute:'2-digit'});
  $('updated').classList.toggle('completed', complete);
  $('record-time').textContent = (complete ? '本批最终记录：' : '批次记录更新：') + stamp + '（北京时间）。目标成功依据官方评分；无评分计入全批总数。';
  let available = [...d.tasks];
  const historic = attempts(d).find(t => t.run_id === current && t.historical);
  if (historic) available.push(historic);
  $('run').innerHTML = available.map(t => `<option value="${esc(t.run_id)}">${t.index + 1}. ${esc(name(t))} · ${t.historical ? '历史尝试 · ' : ''}${esc(outcome(t))}</option>`).join('');
  $('run').disabled = !available.length;
  const target = available.find(t => t.run_id === current) || available.find(t => t.task_success === true && validReplay(t)) || available.find(validReplay) || available[0];
  if (target) choose(target.run_id);
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
