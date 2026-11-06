(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const projects = JSON.parse(document.querySelector('meta[name="projects"]').content);
  const $ = (id) => document.getElementById(id);
  const project = $('project'), prompt = $('prompt'), start = $('start');
  const statusText = { idle: 'Aguardando', starting: 'Preparando execução', running: 'Em andamento', completed: 'Concluída', failed: 'Falhou', interrupted: 'Cancelada' };
  let lastId = 0, active = false, clock;
  projects.forEach((path) => { const option = document.createElement('option'); option.value = path; option.textContent = path; project.append(option); });
  prompt.addEventListener('input', () => $('char-count').textContent = `${prompt.value.length.toLocaleString('pt-BR')} / 16.000`);
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
    const entries = Object.entries(agents || {}); $('agents').classList.toggle('hidden', !entries.length); $('agent-list').replaceChildren();
    for (const [id, agent] of entries) { const li = document.createElement('li'); li.textContent = `${id} · ${agent.status || 'estado desconhecido'}${agent.model ? ` · ${agent.model}` : ''}${agent.effort ? ` · ${agent.effort}` : ''}`; $('agent-list').append(li); }
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
    active = true; lastId = 0; $('event-list').replaceChildren(); $('event-count').textContent = '0 eventos';
    $('execution').classList.remove('hidden'); $('run-title').textContent = task;
    $('conversation').replaceChildren(); $('session-id').classList.add('hidden'); $('agents').classList.add('hidden');
    $('project-label').textContent = project.value; $('elapsed').textContent = 'Em andamento';
    $('result-panel').classList.add('hidden'); $('error').classList.add('hidden'); $('progress').style.width = '';
    start.disabled = true; start.textContent = 'Iniciando…';
    render({ status: 'starting' });
    try {
      const response = await api('/api/run', { prompt: task, project: project.value, engine: $('engine').value });
      if (!response.ok) { const detail = await response.text(); throw new Error(detail || 'Não foi possível iniciar.'); }
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
  $('new-task').addEventListener('click', () => { prompt.value = ''; prompt.dispatchEvent(new Event('input')); prompt.focus(); $('execution').classList.add('hidden'); });
})();
