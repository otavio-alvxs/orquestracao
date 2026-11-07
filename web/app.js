(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const projects = JSON.parse(document.querySelector('meta[name="projects"]').content);
  const $ = (id) => document.getElementById(id);
  const project = $('project'), prompt = $('prompt'), start = $('start'), mode = $('mode');
  const statusText = { idle: 'Aguardando', starting: 'Preparando execução', running: 'Em andamento', completed: 'Concluída', failed: 'Falhou', interrupted: 'Cancelada' };
  let lastId = 0, active = false, clock;
  projects.forEach((path) => { const option = document.createElement('option'); option.value = path; option.textContent = path; project.append(option); });
  prompt.addEventListener('input', () => $('char-count').textContent = `${prompt.value.length.toLocaleString('pt-BR')} / 16.000`);
  mode.addEventListener('change', () => {
    const dev = mode.value === 'development';
    $('mode-help').textContent = dev ? 'Desenvolvimento usa workspaceWrite: pode alterar arquivos do projeto sem pedir autorização individual para cada edição.' : 'Leitura usa sandbox readOnly.';
    $('mode-hint').textContent = dev ? 'Aprovações adicionais aparecem quando o Codex solicitar.' : 'A execução não altera arquivos.';
    $('engine').disabled = dev;
    if (dev) $('engine').value = 'app-server';
    $('effective-mode').textContent = dev ? 'Desenvolvimento' : 'Leitura';
  });
  function lockSelection(locked) {
    project.disabled = locked;
    mode.disabled = locked;
    $('engine').disabled = locked || mode.value === 'development';
  }
  prompt.addEventListener('keydown', (event) => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') start.click(); });

  async function api(path, body) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
  }
  function render(state) {
    const status = state.status || 'idle';
    $('status').className = `status ${status}`;
    $('status').querySelector('span').textContent = statusText[status] || 'Aguardando';
    $('progress').classList.toggle('active', status === 'running' || status === 'starting');
    if (status === 'completed') $('progress').style.width = '100%';
    $('cancel').classList.toggle('hidden', ['completed', 'failed', 'interrupted'].includes(status));
    $('new-task').classList.toggle('hidden', !['completed', 'failed', 'interrupted'].includes(status));
    $('result-panel').classList.toggle('hidden', !state.result);
    $('result').textContent = state.result || '';
    if (state.mode === 'development') $('effective-mode').textContent = 'Desenvolvimento · workspaceWrite';
    else if (state.mode === 'reading') $('effective-mode').textContent = 'Leitura · readOnly';
    lockSelection(['starting', 'running'].includes(status) || Boolean(state.thread_id));
    renderApprovals(state.approvals || []);
    const files = state.changed_files || [];
    $('changed-files').classList.toggle('hidden', files.length === 0);
    $('changed-files').textContent = files.length ? `Alterações locais observadas: ${files.join(', ')}` : '';
    $('error').classList.toggle('hidden', !state.error);
    $('error').textContent = state.error || '';
    if (state.thread_id) { $('session-id').textContent = `Sessão ${state.thread_id}`; $('session-id').classList.remove('hidden'); }
    $('composer').classList.toggle('hidden', $('engine').value !== 'app-server' || !['completed','failed','interrupted'].includes(status));
    renderAgents(state.agents);
    const metric = (value) => Number.isInteger(value) ? `${value.toLocaleString('pt-BR')} tokens` : 'tokens indisponíveis';
    $('tokens').textContent = state.input_tokens == null && state.output_tokens == null ? 'Tokens indisponíveis' : `Entrada ${metric(state.input_tokens)} · saída ${metric(state.output_tokens)}`;
    if (state.duration_seconds != null) $('elapsed').textContent = `${state.duration_seconds.toFixed(1)} s`;
    if (['completed', 'failed', 'interrupted'].includes(status)) {
      active = false; clearInterval(clock); start.disabled = false; start.innerHTML = '<span class="play">▶</span> Iniciar tarefa <kbd>⌘ ↵</kbd>';
    }
  }
  function renderApprovals(approvals) {
    $('approval-list').replaceChildren();
    $('approval-panel').classList.toggle('hidden', !approvals.length);
    for (const item of approvals) {
      const card = document.createElement('article'); card.className = 'approval-card';
      const title = document.createElement('strong'); title.textContent = item.type === 'command' ? 'Comando' : 'Alteração de arquivos'; card.append(title);
      if (item.reason) { const reason = document.createElement('p'); reason.textContent = item.reason; card.append(reason); }
      if (item.command) { const command = document.createElement('code'); command.textContent = item.command; card.append(command); }
      if (item.files?.length) { const files = document.createElement('p'); files.textContent = item.files.join(', '); card.append(files); }
      const actions = document.createElement('div'); actions.className = 'approval-actions';
      for (const [decision, label] of [['accept','Aprovar uma vez'],['decline','Recusar']]) {
        if (decision === 'accept' && !item.can_approve) continue;
        const button = document.createElement('button'); button.textContent = label; button.disabled = Boolean(item.sending);
        button.className = decision === 'accept' ? 'primary' : 'cancel';
        button.addEventListener('click', async () => {
          button.disabled = true;
          try {
            const response = await api('/api/approval', { request_id: item.id, decision });
            if (!response.ok) throw new Error((await response.text()) || 'Decisão não enviada.');
            $('approval-feedback').textContent = decision === 'accept' ? 'Aprovação enviada uma vez' : 'Recusa enviada';
            $('approval-feedback').classList.remove('hidden');
          } catch (error) { $('approval-feedback').textContent = error.message; $('approval-feedback').classList.remove('hidden'); button.disabled = false; }
        });
        actions.append(button);
      }
      card.append(actions); $('approval-list').append(card);
    }
  }
  function addEvent(message) {
    const li = document.createElement('li'), time = document.createElement('time'), text = document.createElement('span');
    time.textContent = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    text.textContent = message; li.append(time, text); $('event-list').append(li);
    while ($('event-list').children.length > 500) $('event-list').firstElementChild.remove();
    const count = $('event-list').children.length; $('event-count').textContent = `${count} ${count === 1 ? 'evento' : 'eventos'}`;
  }
  function addMessage(role, text) {
    const li = document.createElement('li'); li.className = `message ${role}`;
    const label = document.createElement('strong'); label.textContent = role === 'user' ? 'Você' : 'Codex';
    const body = document.createElement('p'); body.textContent = text; li.append(label, body); $('conversation').append(li);
  }
  function renderAgents(agents) {
    const entries = Object.entries(agents || {}).filter(([, agent]) => agent.role !== 'Coordenador');
    const expanded = new Map([...$('agent-list').querySelectorAll('details')].map((node) => [node.dataset.agent, node.open]));
    $('agents').classList.toggle('hidden', !entries.length); $('agent-list').replaceChildren();
    const labels = { running: 'Em atividade', completed: 'Concluído', failed: 'Falhou', interrupted: 'Interrompido', shutdown: 'Encerrado', unknown: 'Estado desconhecido' };
    for (const [id, agent] of entries) {
      const li = document.createElement('li'), heading = document.createElement('span');
      const observedName = typeof agent.name === 'string' && agent.name ? agent.name :
        (id.length > 18 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id);
      heading.textContent = `${observedName} · ${labels[agent.status] || labels.unknown}`;
      li.append(heading);
      const summaryText = agent.result || agent.activity;
      if (summaryText) { const activity = document.createElement('span'); activity.className = 'agent-activity'; activity.textContent = summaryText; li.append(activity); }
      if (agent.model || agent.effort) {
        const technical = document.createElement('details'), summary = document.createElement('summary'), detail = document.createElement('span');
        technical.className = 'agent-technical'; technical.dataset.agent = id; technical.open = expanded.get(id) || false; summary.textContent = 'Detalhes';
        detail.textContent = [agent.model, agent.effort].filter(Boolean).join(' · '); technical.append(summary, detail); li.append(technical);
      }
      $('agent-list').append(li);
    }
  }
  async function stream() {
    const response = await fetch('/api/events', { headers: { 'X-CSRF-Token': csrf, 'Last-Event-ID': String(lastId) } });
    if (!response.ok || !response.body) throw new Error('Não foi possível acompanhar os eventos.');
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    while (active) {
      const { value, done } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split;
      while ((split = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, split); buffer = buffer.slice(split + 2);
        if (!block || block.startsWith(':')) continue;
        let id = null, kind = 'message', data = '';
        for (const line of block.split('\n')) {
          if (line.startsWith('id: ')) id = Number(line.slice(4));
          else if (line.startsWith('event: ')) kind = line.slice(7);
          else if (line.startsWith('data: ')) data += line.slice(6);
        }
        if (Number.isInteger(id)) lastId = id;
        if (!data) continue;
        const parsed = JSON.parse(data);
        if (kind === 'state') render(parsed);
        else if (kind === 'event') addEvent(parsed.message);
        else if (kind === 'message') addMessage(parsed.role, parsed.text);
      }
    }
  }
  start.addEventListener('click', async () => {
    const task = prompt.value.trim(); if (!task || active) return;
    $('approval-feedback').textContent = ''; $('approval-feedback').classList.add('hidden');
    active = true; lastId = 0; $('event-list').replaceChildren(); $('event-count').textContent = '0 eventos';
    $('execution').classList.remove('hidden'); $('run-title').textContent = task;
    $('conversation').replaceChildren(); $('session-id').classList.add('hidden'); $('agents').classList.add('hidden');
    $('project-label').textContent = project.value; $('elapsed').textContent = 'Em andamento';
    $('result-panel').classList.add('hidden'); $('error').classList.add('hidden'); $('progress').style.width = '';
    start.disabled = true; start.textContent = 'Iniciando…';
    render({ status: 'starting', mode: mode.value });
    try {
      const response = await api('/api/run', { prompt: task, project: project.value, engine: $('engine').value, mode: mode.value });
      if (!response.ok) { const detail = await response.text(); throw new Error(detail || 'Não foi possível iniciar.'); }
      lockSelection(true);
      stream().catch((error) => { if (active) { addEvent(error.message); active = false; render({ status: 'failed', error: error.message }); } });
      clock = setInterval(async () => {
        const response = await fetch('/api/status', { headers: { 'X-CSRF-Token': csrf } });
        if (response.ok) render(await response.json());
      }, 1000);
    } catch (error) { active = false; render({ status: 'failed', error: error.message }); }
  });
  $('cancel').addEventListener('click', async () => {
    $('cancel').disabled = true;
    try { await api('/api/cancel', {}); } finally { $('cancel').disabled = false; }
  });
  $('send-message').addEventListener('click', async () => {
    const text = $('message').value.trim(); if (!text || active) return;
    active = true; $('message').value = ''; $('send-message').disabled = true;
    try {
      const response = await api('/api/message', { prompt: text });
      if (!response.ok) throw new Error((await response.text()) || 'Não foi possível enviar a mensagem.');
      render({ status: 'starting' });
      stream().catch((error) => { if (active) { addEvent(error.message); active = false; render({ status: 'failed', error: error.message }); } });
    } catch (error) { active = false; render({ status: 'failed', error: error.message }); }
    finally { $('send-message').disabled = false; }
  });
  $('new-task').addEventListener('click', () => {
    prompt.value = ''; prompt.dispatchEvent(new Event('input')); prompt.focus(); $('execution').classList.add('hidden');
    $('approval-feedback').textContent = ''; $('approval-feedback').classList.add('hidden'); lockSelection(false);
  });
})();
