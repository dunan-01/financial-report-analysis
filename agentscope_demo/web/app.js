const $ = id => document.getElementById(id);
const form = $('investigation-form');
let activeJob = null;
let pollTimer = null;

function remember(id) {
  try { id ? sessionStorage.setItem('investigation-job', id) : sessionStorage.removeItem('investigation-job'); } catch (_) {}
}
function busy(value) {
  $('start-button').disabled = value;
  $('start-button').textContent = value ? '调查进行中…' : '开始调查 ↗';
}
function showState(title, message, state) {
  $('empty-state').hidden = true;
  $('job-state').hidden = false;
  $('job-title').textContent = title;
  $('job-message').textContent = message;
  $('status-badge').dataset.state = state;
  $('activity').className = 'activity' + (state === 'succeeded' ? ' done' : state === 'failed' ? ' failed' : '');
}
async function request(url, options = {}) {
  const response = await fetch(url, {...options, signal: AbortSignal.timeout(15000)});
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.error || '请求未成功，请稍后重试。'); error.status = response.status; throw error; }
  return data;
}
async function poll() {
  clearTimeout(pollTimer);
  if (!activeJob) return;
  $('retry-status').hidden = true;
  try {
    const requestedJob = activeJob;
    const job = await request(`/api/jobs/${requestedJob}`);
    if (activeJob !== requestedJob) return;
    if (job.started_at && !jobStarted) jobStarted = Date.parse(job.started_at);
    if (job.finished_at) jobEnded = Date.parse(job.finished_at);
    configureWorkflow(job);
    if (job.audit_url) { $('audit-download').href = job.audit_url; $('audit-download').hidden = false; }
    $('investigation-company').textContent = job.summary?.company_name || job.summary?.company_id || '企业调查';
    $('investigation-context').textContent = [job.summary?.report_period && `${job.summary.report_period}年度`, job.summary?.scenario].filter(Boolean).join(' · ');
    $('job-reference').textContent = `调查编号：${job.id}`;
    if (job.status === 'succeeded') {
      renderDownloads(job);
      setPhase('report');
      showState('调查已完成', job.mode === 'offline' ? '全部技能离线预览已生成。预览中的分析内容仅供演示。' : '全流程技能调查已完成，可查看合并报告。', job.status);
      $('status-badge').textContent = job.mode === 'offline' ? '预览完成' : '已完成';
      $('open-report').href = job.reports.html;
      $('financial-download').href = job.reports.financial;
      $('related-download').href = job.reports.related_party;
      $('report-actions').hidden = false;
      $('report-preview').src = job.reports.html;
      $('report-preview').hidden = false;
      busy(false);
      showNewInvestigation();
    } else if (job.status === 'failed') {
      showState('调查未完成', job.error, job.status);
      $('status-badge').textContent = '未完成';
      busy(false);
      showNewInvestigation();
    } else {
      showState(job.status === 'queued' ? '正在准备调查' : '正在开展调查', job.mode === 'offline' ? '正在按依赖执行所有节点的离线预览。' : '技能 Agent 正按依赖执行分析与报告校验，请保持本地服务运行。', 'running');
      $('status-badge').textContent = '进行中';
      busy(true);
      pollTimer = setTimeout(poll, 1800);
    }
  } catch (error) {
    showState('暂时无法获取进度', `${error.message} 如果任务已提交，请先重新检查进度，避免重复调查。`, 'failed');
    $('status-badge').textContent = '连接中断';
    $('retry-status').hidden = false;
    busy(false);
    if (error.status === 404) { eventSource?.close(); showNewInvestigation(); }
  }
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  clearTimeout(pollTimer);
  $('form-error').hidden = true;
  $('report-actions').hidden = true;
  $('all-downloads').hidden = true;
  $('report-preview').hidden = true;
  $('report-preview').removeAttribute('src');
  busy(true);
  try {
    const job = await request('/api/jobs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    activeJob = job.id;
    remember(activeJob);
    connectEvents(activeJob, true);
    await poll();
  } catch (error) {
    $('form-error').textContent = error.message;
    $('form-error').hidden = false;
    busy(false);
    if (activeJob) $('retry-status').hidden = false;
  }
});
$('retry-status').addEventListener('click', poll);
try { activeJob = sessionStorage.getItem('investigation-job'); } catch (_) {}
// 在下方事件模块初始化后恢复订阅。

// SSE 按事件编号断线续传；界面状态仅依据事件，不模拟百分比或模型思考。
const agentNames = {financial: '财报分析', related_party: '关联交易识别', system: '调查任务'};
const stateNames = {waiting: '等待执行', running: '执行中', succeeded: '已完成', failed: '失败', retrying: '正在修正', cancelled: '已取消', skipped: '不适用或未执行', limited: '已执行 · 资料不足'};
const agents = new Map();
const auditEvents = [];
let selectedAgent = 'industry', eventSource = null, eventCursor = 0, jobStarted = null, jobEnded = null, lastActivity = null;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function ensureAgent(name) {
  if (!agents.has(name)) agents.set(name, {status:'waiting', steps:new Map(), plan:[]});
  return agents.get(name);
}
function renderAgents() {
  const list = $('agent-list'); list.replaceChildren();
  for (const [name, agent] of agents) {
    if (name === 'system' && agents.size > 1) continue;
    const button = node('button', undefined, 'agent-card' + (name === selectedAgent ? ' selected' : ''));
    button.type = 'button'; button.dataset.state = agent.status;
    button.setAttribute('aria-pressed', name === selectedAgent ? 'true' : 'false');
    button.append(node('strong', agentNames[name] || name), node('span', (stateNames[agent.status] || agent.status) + (agent.duration && agent.duration > 0 ? ` · ${agent.duration}s` : '')));
    if (agent.dependencies?.length) button.append(node('small', '上游：' + agent.dependencies.map(key=>agentNames[key] || key).join('、')));
    // 进度统计已有最终结果的步骤；成功与资料不足等结果另列，避免把执行完成误作核验成功。
    const steps = [...agent.steps.values()];
    const count = steps.filter(event => ['succeeded', 'limited', 'skipped', 'failed', 'cancelled'].includes(event.status)).length;
    const progress = node('small', `执行进度：${count} / ${steps.length}`);
    progress.title = '统计已有最终结果的步骤，包括成功、资料不足、跳过、失败和取消；具体结果见下方。';
    button.append(progress);
    const outcomes = [['succeeded', '成功'], ['limited', '资料不足'], ['skipped', '跳过'], ['failed', '失败'], ['cancelled', '取消']]
      .map(([status, label]) => [label, steps.filter(event => event.status === status).length])
      .filter(([, amount]) => amount > 0)
      .map(([label, amount]) => `${label} ${amount}`);
    if (outcomes.length) button.append(node('small', outcomes.join(' · ')));
    button.onclick = () => { selectedAgent = name; renderAgents(); renderExecutionLog(); };
    list.append(button);
  }
  const agent = ensureAgent(selectedAgent);
  $('agent-detail-title').textContent = agentNames[selectedAgent] || selectedAgent;
  const current = [...agent.steps.entries()].find(([key, value]) => key.startsWith('skill_step:') && value.status === 'running');
  $('agent-summary').textContent = current ? '当前执行：' + current[1].message : agent.status === 'waiting' ? '等待管道派发任务' : (agent.message || stateNames[agent.status]);
  $('agent-steps').replaceChildren();
  for (const [step, event] of agent.steps) {
    const item = node('li', event.message); item.dataset.state = event.status;
    item.dataset.started = event.status === 'running' ? event.time || '' : '';
    item.append(node('small', (stateNames[event.status] || event.status) + (event.duration && event.duration > 0 ? ` · ${event.duration}s` : '') + (event.attempt ? ` · 第${event.attempt}轮` : '')));
    if (event.summary) item.append(node('p', event.summary, 'step-summary'));
    if (event.evidence_refs?.length) item.append(node('small', '证据引用：' + event.evidence_refs.join('、')));
    if (event.summary_origin === 'model_reported') item.append(node('small', '模型步骤结果摘要，需结合证据复核'));
    $('agent-steps').append(item);
  }
  if (!agent.steps.size) $('agent-steps').append(node('li', '收到执行事件后展示实际步骤'));
}
function renderExecutionLog() {
  const log = $('execution-log'); const previousTop = log.scrollTop;
  const atBottom = log.scrollHeight - previousTop - log.clientHeight < 65;
  log.replaceChildren();
  const all = $('audit-scope').value === 'all';
  for (const event of auditEvents) {
    if (!all && event.agent !== selectedAgent) continue;
    const audit = event.audit;
    if (!audit) continue;
    const entry = node('div', undefined, 'log-entry'); entry.dataset.state = audit.status;
    entry.append(node('time', new Date(audit.time).toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false})),
      node('strong', `${agentNames[event.agent] || event.agent} · ${audit.category}`), node('div', audit.action));
    const facts = [audit.operator && `操作人：${audit.operator}`, audit.company_name && `企业：${audit.company_name}`,
      `状态：${stateNames[audit.status] || audit.status}`, audit.duration !== undefined && `耗时：${audit.duration}s`,
      audit.attempt && `第${audit.attempt}轮`].filter(Boolean);
    entry.append(node('small', facts.join(' · ')));
    entry.append(node('small', `技能：${audit.skill} · 版本：${audit.skill_version || '未声明'}`));
    if (audit.model) entry.append(node('small', '执行模型：' + audit.model));
    if (audit.persistence === 'failed') entry.append(node('div', '审计文件写入失败，当前记录仅保存在服务内存，请及时导出。'));
    if (audit.provider && audit.source_summary) entry.append(node('small', '来源接口：' + audit.provider));
    if (audit.source_summary && Object.keys(audit.source_summary).length) entry.append(node('div', '数据摘要：' + JSON.stringify(audit.source_summary), 'audit-facts'));
    if (audit.input_refs) entry.append(node('div', '输入留痕：' + JSON.stringify(audit.input_refs), 'audit-facts'));
    if (audit.summary) entry.append(node('p', '步骤结果（模型摘要）：' + audit.summary));
    if (audit.evidence_refs?.length) entry.append(node('div', '证据引用：' + audit.evidence_refs.join('、')));
    if (audit.tool_result_sha256) entry.append(node('small', '工具结果摘要指纹：' + audit.tool_result_sha256));
    if (audit.parse_method) entry.append(node('small', '结果解析：' + audit.parse_method));
    if (audit.failure_type) entry.append(node('small', '异常类型：' + audit.failure_type));
    if (audit.artifact_ref) entry.append(node('small', '步骤记录：' + audit.artifact_ref));
    if (audit.errors?.length) {
      const errors = node('ul'); for (const text of audit.errors) errors.append(node('li', text)); entry.append(errors);
    }
    entry.append(node('small', '审计编号：' + audit.audit_id));
    log.append(entry);
  }
  if (!log.children.length) log.append(node('p', '当前范围暂无审计记录；业务步骤开始后自动追加。', 'live-note'));
  log.scrollTop = atBottom ? log.scrollHeight : previousTop;
}
function applyEvent(event) {
  if (event.id <= eventCursor) return;
  eventCursor = event.id;
  if (event.audit) auditEvents.push(event);
  lastActivity = Date.parse(event.time);
  const agent = ensureAgent(event.agent);
  agent.message = event.message;
  if (event.step === 'agent') {
    agent.status = event.status; agent.duration = event.duration;
    if (['succeeded','failed','cancelled'].includes(event.status)) {
      for (const [key, value] of agent.steps) {
        if (['waiting','running','retrying'].includes(value.status)) agent.steps.set(key, {...value, status: value.status === 'waiting' ? 'skipped' : 'cancelled'});
      }
    }
  }
  else agent.steps.set(event.step, event);
  renderExecutionLog();
  renderAgents();
  if (event.step === 'job' && ['succeeded','failed'].includes(event.status)) { eventSource?.close(); setConnection('finished'); jobEnded = Date.parse(event.time); poll(); }
}
function connectEvents(id, reset = false) {
  eventSource?.close();
  if (reset) { selectedAgent = 'industry'; eventCursor = 0; agents.clear(); auditEvents.length = 0; $('execution-log').replaceChildren(); jobStarted = Date.now(); jobEnded = null; lastActivity = null; }
  ensureAgent('industry');
  setPhase('execution');
  $('investigation-overview').hidden = false;
  setConnection('connecting');
  document.body.classList.add('is-investigating'); $('live-workspace').hidden = false; renderAgents();
  eventSource = new EventSource(`/api/jobs/${id}/events?after=${eventCursor}`);
  eventSource.onopen = () => { if (id === activeJob) setConnection('connected'); };
  eventSource.onmessage = message => {
    if (id !== activeJob) return;
    let event; try { event = JSON.parse(message.data); } catch (_) { return; }
    applyEvent(event);
  };
  eventSource.onerror = () => { if (id !== activeJob) return; setConnection('retrying'); $('job-message').textContent = '实时连接暂时中断，正在重连；后台调查继续执行。'; };
}
setInterval(() => {
  if (!jobStarted) return;
  const seconds = Math.max(0, Math.floor(((jobEnded || Date.now()) - jobStarted) / 1000));
  if (lastActivity) $('event-freshness').textContent = jobEnded ? '事件记录已完整接收' : `最近活动 ${Math.max(0, Math.floor((Date.now() - lastActivity) / 1000))} 秒前`;
  for (const item of $('agent-steps').querySelectorAll('[data-started]')) {
    if (!item.dataset.started) continue;
    const small = item.querySelector('small');
    if (small) small.textContent = `执行中 · 已等待 ${Math.max(0, Math.floor((Date.now() - Date.parse(item.dataset.started)) / 1000))} 秒`;
  }
  $('elapsed').textContent = `已用时 ${Math.floor(seconds/60)}分${seconds%60}秒`;
}, 1000);

$('audit-scope').addEventListener('change', renderExecutionLog);
if (activeJob) { busy(true); connectEvents(activeJob); poll(); }

function showNewInvestigation() {
  if ($('new-investigation')) return;
  const button = node('button', '新建调查', 'secondary'); button.id = 'new-investigation'; button.type = 'button';
  button.onclick = () => { eventSource?.close(); clearTimeout(pollTimer); document.body.classList.remove('is-investigating'); setPhase('input'); activeJob = null; remember(null); button.remove(); };
  $('job-state').append(button);
}

function setPhase(phase) {
  for (const name of ['input', 'execution', 'report']) $('phase-' + name).classList.toggle('active', name === phase);
}
function setConnection(state) {
  const element = $('event-connection'); element.dataset.state = state;
  element.textContent = {connecting:'连接实时事件中', connected:'实时事件已连接', retrying:'连接中断，自动重连中', finished:'事件接收完成'}[state];
}
function configureWorkflow(job) {
  $('workflow-note').textContent = job.mode === 'offline' ? '全图模拟执行，不调用业务模型' : '无上游节点并行启动；下游等待所有必需上游完成';
  for (const meta of job.nodes || []) {
    agentNames[meta.key] = meta.label;
    const agent = ensureAgent(meta.key); agent.dependencies = meta.upstream;
  }
  if (job.nodes?.length && !job.nodes.some(item => item.key === selectedAgent)) selectedAgent = job.nodes[0].key;
  if (job.nodes?.length) {
    const ordered = job.nodes.map(meta => [meta.key, ensureAgent(meta.key)]);
    const system = agents.get('system'); agents.clear();
    for (const [name, agent] of ordered) agents.set(name, agent);
    if (system) agents.set('system', system);
  }
  for (const [name, steps] of Object.entries(job.workflow || {})) {
    const agent = ensureAgent(name); agent.plan = steps;
    const ordered = new Map();
    for (const [key, label] of steps) ordered.set(key, agent.steps.get(key) || {message:label, status:['succeeded','failed','cancelled'].includes(agent.status) ? 'skipped' : 'waiting'});
    for (const [key, event] of agent.steps) if (!ordered.has(key)) ordered.set(key, event);
    agent.steps = ordered;
  }
  renderAgents();
  renderExecutionLog();
}
function renderDownloads(job) {
  const container = $('all-downloads'); container.replaceChildren(); container.hidden = false;
  for (const [key, url] of Object.entries(job.reports || {})) {
    if (['html','financial','related_party'].includes(key)) continue;
    const link = node('a', '下载' + (agentNames[key] || key)); link.href = url; link.download = key + '.md'; container.append(link);
  }
}
