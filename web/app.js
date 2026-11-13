(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const projects = JSON.parse(document.querySelector('meta[name="projects"]').content);
  const $ = (id) => document.getElementById(id);
  const project = $('project'), prompt = $('prompt'), start = $('start'), mode = $('mode'), profile = $('profile'), workflow = $('workflow');
  const statusText = { idle: 'Aguardando', starting: 'Preparando execução', running: 'Em andamento', completed: 'Concluída', failed: 'Falhou', interrupted: 'Cancelada' };
  let lastId = 0, active = true, streamPromise = null;
  let quotaSnapshot = null, drafting = false, latestState = null, handoffDraft = null;
  const quotaFreshnessSeconds = 180;
  setInterval(() => { if (quotaSnapshot) renderQuotas(quotaSnapshot); }, 30000);
  projects.forEach((item) => { const option = document.createElement('option'); option.value = item.id; option.textContent = item.name; project.append(option); });
  function publicationAllowed() { return !handoffDraft && projects.find(p => p.id === project.value)?.publish; }
  project.addEventListener('change', () => mode.dispatchEvent(new Event('change')));
  prompt.addEventListener('input', () => $('char-count').textContent = `${prompt.value.length.toLocaleString('pt-BR')} / 16.000`);
  mode.addEventListener('change', () => {
    const dev = mode.value === 'development';
    const nonDirect = ['orchestrated', 'automatic'].includes(workflow.value);
    $('mode-help').textContent = dev ? 'Desenvolvimento usa workspaceWrite: pode alterar arquivos do projeto sem pedir autorização individual para cada edição.' : 'Leitura usa sandbox readOnly.';
    $('mode-hint').textContent = dev ? 'Aprovações adicionais aparecem quando o Codex solicitar.' : 'A execução não altera arquivos.';
    $('engine').disabled = dev;
    if (dev) $('engine').value = 'app-server';
    $('effective-mode').textContent = dev ? 'Desenvolvimento' : 'Leitura';
    $('publish-option').classList.toggle('hidden', nonDirect || !dev || !publicationAllowed());
    if (nonDirect || !dev || !publicationAllowed()) $('publish-draft-pr').checked = false;
    workflow.disabled = !dev;
    if (!dev) workflow.value = 'direct';
    profile.disabled = ['orchestrated', 'automatic'].includes(workflow.value);
  });
  workflow.addEventListener('change', () => {
    const orchestrated = ['orchestrated', 'automatic'].includes(workflow.value);
    if (orchestrated) { mode.value = 'development'; $('engine').value = 'app-server'; }
    $('engine').disabled = orchestrated || mode.value === 'development';
    profile.disabled = orchestrated;
    $('publish-option').classList.toggle('hidden', orchestrated || mode.value !== 'development' || !publicationAllowed());
    if (orchestrated) $('publish-draft-pr').checked = false;
  });
  function lockSelection(locked) {
    project.disabled = locked;
    mode.disabled = locked;
    $('engine').disabled = locked || mode.value === 'development';
    profile.disabled = locked;
    workflow.disabled = locked || mode.value !== 'development';
  }
  prompt.addEventListener('keydown', (event) => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') start.click(); });

  async function api(path, body) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
  }
  function render(state) {
    latestState = state.project ? state : latestState;
    if (drafting) return;
    if (Object.hasOwn(state, 'rate_limits')) renderQuotas(state.rate_limits);
    const status = state.status || 'idle';
    if (['starting', 'running'].includes(status)) { $('report-preview').classList.add('hidden'); $('report-text').value = ''; }
    active = Boolean(state.busy) || ['starting', 'running'].includes(status);
    start.disabled = active;
    $('new-task').disabled = active;
    if (state.project) {
      if (![...project.options].some(option => option.value === state.project)) {
        const option = document.createElement('option'); option.value = state.project;
        option.textContent = state.project_name; project.append(option);
      }
      project.value = state.project;
      mode.value = state.mode;
      workflow.value = state.workflow || 'direct';
      $('engine').value = state.engine;
      profile.value = state.requested_profile || state.route_profile || 'auto';
      mode.dispatchEvent(new Event('change'));
      workflow.dispatchEvent(new Event('change'));
      $('execution').classList.remove('hidden');
      $('run-title').textContent = state.prompt;
      $('project-label').textContent = state.project_name;
    }
    $('publish-draft-pr').disabled = active;
    $('status').className = `status ${status}`;
    $('status').querySelector('span').textContent = statusText[status] || 'Aguardando';
    $('progress').classList.toggle('active', status === 'running' || status === 'starting');
    if (status === 'completed') $('progress').style.width = '100%';
    $('cancel').classList.toggle('hidden', ['completed', 'failed', 'interrupted'].includes(status));
    $('new-task').classList.toggle('hidden', !['completed', 'failed', 'interrupted'].includes(status));
    $('report-section').classList.toggle('hidden', !['completed','failed','interrupted'].includes(status) || !state.project);
    $('result-panel').classList.toggle('hidden', !state.result);
    $('result').textContent = state.result || '';
    if (state.mode === 'development') $('effective-mode').textContent = 'Desenvolvimento · workspaceWrite';
    else if (state.mode === 'reading') $('effective-mode').textContent = 'Leitura · readOnly';
    lockSelection(active || Boolean(state.thread_id));
    renderApprovals(state.approvals || []);
    $('publish-panel').classList.toggle('hidden', !state.publish_authorized && !state.publish_attempted && !state.publish_result);
    $('publish-status').textContent = state.publish_result || (state.publish_authorized ? 'Publicação de Draft PR autorizada ao concluir.' : '');
    const files = state.changed_files || [];
    $('changed-files').classList.toggle('hidden', files.length === 0);
    $('changed-files').textContent = files.length ? `Alterações locais observadas: ${files.join(', ')}` : '';
    $('error').classList.toggle('hidden', !state.error);
    $('error').textContent = state.error || '';
    if (state.thread_id) { $('session-id').textContent = `Sessão ${state.thread_id}`; $('session-id').classList.remove('hidden'); }
    const effectiveWorkflow = state.effective_workflow || state.workflow || 'direct';
    const sameThread = state.same_thread_available ?? Boolean(state.thread_id);
    $('continuation-help').classList.toggle('hidden', !sameThread || effectiveWorkflow === 'orchestrated' || active);
    $('continue-work').classList.toggle('hidden', !state.handoff || active);
    $('continue-work').disabled = active;
    $('continue-work').textContent = effectiveWorkflow === 'orchestrated' ? 'Continuar trabalho com resumo ↗' : 'Continuar em outro fluxo com resumo ↗';
    $('composer').classList.toggle('hidden', !sameThread || $('engine').value !== 'app-server' || effectiveWorkflow === 'orchestrated' || !['completed','failed','interrupted'].includes(status));
    $('escalation').classList.toggle('hidden', !sameThread || $('engine').value !== 'app-server' || effectiveWorkflow === 'orchestrated' || !['completed','failed'].includes(status));
    const flowName = (name) => ({ direct: 'Direta', orchestrated: 'Orquestrada', automatic: 'Automática · Econômica' }[name] || name);
    const flow = `Fluxo: ${flowName(state.requested_workflow || state.workflow || 'direct')} → ${flowName(effectiveWorkflow)}${state.workflow_reason ? ` · ${state.workflow_reason}` : ''}`;
    $('route-info').textContent = `${flow}${state.requested_model ? ` · Modelo/esforço solicitado: ${state.requested_model}/${state.requested_effort}${state.observed_model ? ` · rerota observada: ${state.observed_model}${state.observed_route_reason ? ` (${state.observed_route_reason})` : ''}` : ''}${state.workflow_reason ? '' : ` · ${state.route_reason || ''}`}${state.escalations?.length ? ` · Escalonamentos: ${state.escalations.length}` : ''}` : ''}`;
    renderAgents(state.agents, state);
    const metric = (value) => Number.isInteger(value) ? `${value.toLocaleString('pt-BR')} tokens` : 'tokens indisponíveis';
    $('tokens').textContent = effectiveWorkflow === 'orchestrated' ? 'Total da orquestração indisponível · métricas por etapa no painel' : state.input_tokens == null && state.output_tokens == null ? 'Tokens indisponíveis' : `Entrada ${metric(state.input_tokens)} · cache ${metric(state.cached_input_tokens)} · saída ${metric(state.output_tokens)}`;
    if (state.duration_seconds != null) $('elapsed').textContent = `${state.duration_seconds.toFixed(1)} s`;
    if (['completed', 'failed', 'interrupted'].includes(status)) {
      start.disabled = active; start.innerHTML = '<span class="play">▶</span> Iniciar nova tarefa <kbd>⌘ ↵</kbd>';
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
  function renderQuotas(data) {
    quotaSnapshot = data;
    const root = $('quota-cards'); root.replaceChildren();
    const buckets = Object.entries(data?.buckets || {}).flatMap(([id, bucket]) => [300, 10080].map((minutes) => {
      const windowKey = ['primary', 'secondary'].find((window) => bucket?.[window]?.window_minutes === minutes);
      const value = bucket?.[windowKey];
      const duration = minutes === 300 ? '5 h' : 'semanal';
      return {id, ...(value || {}), bucket, windowKey, minutes, label: `${bucket?.limit_name || id} · ${duration}`};
    }));
    if (!buckets.length) {
      const empty = document.createElement('p'); empty.className = 'empty-state'; empty.textContent = data ? 'Cotas indisponíveis' : 'Aguardando dados'; root.append(empty);
      $('quota-updated').textContent = data?.status === 'disconnected' ? `Conta desconectada · ${data?.stale ? 'dados desatualizados' : 'última atualização indisponível'}` : data?.stale ? 'Dados desatualizados' : 'Atualização indisponível'; return;
    }
    for (const bucket of buckets) {
      const card = document.createElement('article'); card.className = 'quota-card';
      const heading = document.createElement('div'); heading.className = 'quota-heading';
      const name = document.createElement('strong'); name.textContent = bucket.label || bucket.name || 'Cota';
      const usedPercent = Number.isFinite(bucket.used_percent) ? Math.max(0, Math.min(100, bucket.used_percent)) : null;
      const remainingPercent = usedPercent != null ? 100 - usedPercent : null;
      const remaining = document.createElement('span'); remaining.textContent = usedPercent != null ? `${Math.round(usedPercent)}% usado · ${Math.round(remainingPercent)}% restante` : 'Uso indisponível';
      heading.append(name, remaining);
      const meter = document.createElement('div'); meter.className = 'quota-meter'; meter.setAttribute('role', 'progressbar'); meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100');
      const fill = document.createElement('span');
      if (usedPercent != null) { fill.style.width = `${usedPercent}%`; meter.setAttribute('aria-valuenow', String(usedPercent)); }
      else meter.setAttribute('aria-valuetext', 'Indisponível');
      meter.append(fill); card.append(heading, meter);
      const meta = document.createElement('p'); meta.className = 'quota-meta';
      const reset = bucket.resets_at ? new Date(typeof bucket.resets_at === 'number' ? bucket.resets_at * 1000 : bucket.resets_at) : null;
      const windowUpdated = bucket.bucket.window_updated_at?.[bucket.windowKey];
      const updatedAt = Number.isFinite(windowUpdated) ? windowUpdated : null;
      const ageSeconds = updatedAt == null ? null : Date.now() / 1000 - updatedAt;
      const stale = Boolean(data?.status === 'disconnected' || bucket.bucket.stale || data?.stale || ageSeconds == null || ageSeconds > quotaFreshnessSeconds);
      const updatedText = updatedAt != null ? `Atualizado ${new Date(updatedAt * 1000).toLocaleTimeString('pt-BR', {hour:'2-digit',minute:'2-digit'})}` : 'Última atualização indisponível';
      meta.textContent = `${stale ? 'Dados desatualizados · ' : ''}${updatedText} · Reinicia ${reset && !Number.isNaN(reset.valueOf()) ? reset.toLocaleString('pt-BR', {dateStyle:'short',timeStyle:'short'}) : 'indisponível'}`;
      card.append(meta); root.append(card);
    }
    const globalUpdated = data?.updated_at ? `última atualização ${new Date(data.updated_at * 1000).toLocaleTimeString('pt-BR', {hour:'2-digit',minute:'2-digit'})}` : 'última atualização indisponível';
    $('quota-updated').textContent = data?.status === 'disconnected' ? `Conta desconectada · dados desatualizados · ${globalUpdated}` : data?.stale ? `Dados desatualizados · ${globalUpdated}` : globalUpdated;
  }
  function renderAgents(agents, state = {}) {
    const entries = Object.entries(agents || {}).filter(([id, agent]) => id !== 'coordinator' && agent.role !== 'Coordenador');
    const expanded = new Map([...$('agent-list').querySelectorAll('details')].map((node) => [node.dataset.agent, node.open]));
    $('agent-list').replaceChildren();
    const labels = { planned: 'Planejada', running: 'Em atividade', completed: 'Concluída', failed: 'Falhou', blocked: 'Bloqueada', interrupted: 'Cancelada', cancelled: 'Cancelada', shutdown: 'Encerrado', unknown: 'Estado desconhecido' };
    const runningCount = entries.filter(([, agent]) => agent.status === 'running').length + (['running','starting'].includes(state.status) ? 1 : 0);
    $('agent-count').textContent = `${runningCount} ativo${runningCount === 1 ? '' : 's'}`;
    const coordinator = document.createElement('li'); coordinator.className = 'agent-row coordinator';
    const coordHeading = document.createElement('span'); coordHeading.className = 'agent-name'; coordHeading.textContent = `Coordenador · ${labels[state.status] || 'Aguardando'}`; coordinator.append(coordHeading);
    const requested = [state.requested_model, state.requested_effort].filter(Boolean).join(' / ');
    const observed = [state.observed_model, state.observed_effort].filter(Boolean).join(' / ');
    const coordDetail = document.createElement('span'); coordDetail.className = 'agent-activity';
    coordDetail.textContent = `Solicitado: ${requested || 'indisponível'} · Observado: ${observed || 'indisponível'}`; coordinator.append(coordDetail); $('agent-list').append(coordinator);
    for (const [id, agent] of entries) {
      const li = document.createElement('li'), heading = document.createElement('span');
      const observedName = typeof agent.name === 'string' && agent.name ? agent.name :
        (id.length > 18 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id);
      heading.className = 'agent-name'; heading.textContent = `${observedName} · ${labels[agent.status] || labels.unknown}`;
      const parent = agent.parent ? entries.find(([parentId]) => parentId === agent.parent)?.[1] : null;
      li.classList.toggle('agent-child', Boolean(agent.parent));
      if (agent.parent) { const hierarchy = document.createElement('span'); hierarchy.className = 'agent-parent'; hierarchy.textContent = `↳ ${parent?.name || agent.parent}`; li.append(hierarchy); }
      li.append(heading);
      const summaryText = agent.result || agent.activity;
      if (summaryText) { const activity = document.createElement('span'); activity.className = 'agent-activity'; activity.textContent = summaryText; li.append(activity); }
      {
        const technical = document.createElement('details'), summary = document.createElement('summary'), detail = document.createElement('span');
        technical.className = 'agent-technical'; technical.dataset.agent = id; technical.open = expanded.get(id) || false; summary.textContent = 'Detalhes';
        const stageUsage = [['Entrada', agent.input_tokens], ['Cache', agent.cached_input_tokens], ['Saída', agent.output_tokens]]
          .filter(([, value]) => Number.isInteger(value)).map(([label, value]) => `${label} ${Number(value).toLocaleString('pt-BR')}`);
        const requestedSettings = [agent.requested_model, agent.requested_effort].filter(Boolean).join(' / ') || 'indisponível';
        const observedSettings = [agent.observed_model, agent.observed_effort].filter(Boolean).join(' / ');
        const redirect = agent.observed_route_reason ? ` · Redirecionamento informado: ${agent.observed_route_reason}` : '';
        detail.textContent = agent.role === 'Etapa' ? `Solicitado: ${requestedSettings} · Observado: ${observedSettings || 'indisponível'}${redirect} · ${stageUsage.length ? stageUsage.join(' · ') + ' tokens' : 'métricas indisponíveis'}` : `Solicitado: ${requestedSettings} · Observado: ${[agent.observed_model || agent.model, agent.observed_effort || agent.effort].filter(Boolean).join(' / ') || 'indisponível'}`; technical.append(summary, detail); li.append(technical);
      }
      $('agent-list').append(li);
    }
  }
  async function consumeStream() {
    const response = await fetch('/api/events', { headers: { 'X-CSRF-Token': csrf, 'Last-Event-ID': String(lastId) } });
    if (!response.ok || !response.body) throw new Error('Não foi possível acompanhar os eventos.');
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    while (true) {
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
        if (Number.isInteger(id)) { if (id <= lastId) continue; lastId = id; }
        if (!data) continue;
        const parsed = JSON.parse(data);
        if (kind === 'state') render(parsed);
        else if (kind === 'event') addEvent(parsed.message);
        else if (kind === 'message') addMessage(parsed.role, parsed.text);
        else if (kind === 'rate_limits') renderQuotas(parsed);
      }
    }
  }
  function stream() {
    if (streamPromise) return streamPromise;
    streamPromise = (async () => {
      while (true) {
        try { await consumeStream(); }
        catch (error) { if (active) addEvent(error.message); }
        await new Promise((resolve) => setTimeout(resolve, 900));
      }
    })().finally(() => { streamPromise = null; });
    return streamPromise;
  }
  start.addEventListener('click', async () => {
    const task = prompt.value; if (!task.trim() || active) return;
    if (handoffDraft && (!$('handoff-confirm').checked || !$('handoff-summary').value.trim())) {
      $('handoff-confirm').reportValidity();
      $('handoff-summary').focus();
      return;
    }
    $('approval-feedback').textContent = ''; $('approval-feedback').classList.add('hidden');
    drafting = false;
    active = true; $('event-list').replaceChildren(); $('event-count').textContent = '0 eventos';
    $('execution').classList.remove('hidden'); $('run-title').textContent = task;
    $('conversation').replaceChildren(); $('session-id').classList.add('hidden');
    $('project-label').textContent = project.selectedOptions[0]?.textContent; $('elapsed').textContent = 'Em andamento';
    $('result-panel').classList.add('hidden'); $('error').classList.add('hidden'); $('progress').style.width = '';
    start.disabled = true; start.textContent = 'Iniciando…';
    render({ status: 'starting', mode: mode.value });
    try {
      const handoff = handoffDraft ? { source: handoffDraft.source, summary: $('handoff-summary').value, confirmed: $('handoff-confirm').checked } : null;
      const publishDraftPr = !handoff && $('publish-draft-pr').checked;
      $('publish-draft-pr').checked = false;
      const response = await api('/api/run', { prompt: task, project: project.value, engine: $('engine').value, mode: mode.value, workflow: workflow.value, profile: profile.value, publish_draft_pr: publishDraftPr, ...(handoff ? { handoff } : {}) });
      if (!response.ok) { const detail = await response.text(); throw new Error(detail || 'Não foi possível iniciar.'); }
      handoffDraft = null; $('handoff-preview').classList.add('hidden');
      lockSelection(true);
      stream();
    } catch (error) {
      active = false;
      if (handoffDraft) {
        drafting = true; start.disabled = false; start.textContent = 'Continuar com resumo';
        $('error').classList.remove('hidden'); $('error').textContent = error.message;
      } else render({ status: 'failed', error: error.message });
    }
  });
  function reportText(state) {
    const choice = (value, allowed) => allowed.includes(value) ? value : 'indisponível';
    const projectName = typeof state.project_name === 'string' && /^[\p{L}\p{N}_. -]{1,80}$/u.test(state.project_name) && !/^(?:sk|ghp|github_pat|bearer)[-_]/i.test(state.project_name) ? state.project_name : 'indisponível';
    const model = (value) => typeof value === 'string' && /^(?:gpt-[0-9]+(?:\.[0-9]+)?-(?:sol|luna|terra|astra)|Sol|Luna|Terra)$/.test(value) ? value : 'indisponível';
    const effort = (value) => choice(value, ['low','medium','high','xhigh','max','ultra']);
    const number = (v) => Number.isFinite(v) && v >= 0 ? String(v) : 'indisponível';
    const clip = (v, limit) => typeof v === 'string' && v ? (v.length > limit ? v.slice(0, limit) + '\n[trecho omitido]' : v) : 'indisponível';
    const flow = (v) => ({direct:'Direta', automatic:'Automática · Econômica', orchestrated:'Orquestrada'}[v] || 'indisponível');
    const status = (v) => ({completed:'Concluída', failed:'Falhou', interrupted:'Cancelada', running:'Em atividade', planned:'Planejada', blocked:'Bloqueada', cancelled:'Cancelada', shutdown:'Encerrada', unknown:'Desconhecida'}[v] || 'indisponível');
    const stages = Object.entries(state.agents || {}).slice(0, 20).map(([id, agent]) => {
      if (id === 'coordinator' || agent.role === 'Coordenador') return null;
      const knownRoles = ['developer','reviewer','default','worker','explorer'];
      const observedRole = knownRoles.includes(agent.role) ? `papel ${agent.role}` : knownRoles.includes(agent.name) ? `nome ${agent.name}; papel indisponível` : 'papel indisponível';
      const name = id === 'planning' && agent.role === 'Etapa' ? 'Planejamento' : id === 'implementation' && agent.role === 'Etapa' ? 'Implementação' : `Agente · ${observedRole}`;
      const metrics = agent.role === 'Etapa' ? `; entrada ${number(agent.input_tokens)}, cache ${number(agent.cached_input_tokens)}, saída ${number(agent.output_tokens)} tokens (informados pela etapa)` : '';
      return `- ${name}: ${status(agent.status)}${metrics}`;
    }).filter(Boolean).join('\n') || 'Não informado.';
    const total = state.effective_workflow === 'orchestrated' ? 'Total da orquestração indisponível.' : `Entrada ${number(state.input_tokens)}, cache ${number(state.cached_input_tokens)}, saída ${number(state.output_tokens)} tokens (snapshot do executor).`;
    const report = `# Relatório da execução\n\n## Identificação\nGerado em: ${new Date().toLocaleString('pt-BR')}\nProjeto: ${projectName}\nEstado: ${status(state.status)}\n\n## Execução\nFluxo solicitado: ${flow(state.requested_workflow || state.workflow)}\nFluxo efetivo: ${flow(state.effective_workflow)}\nModo: ${choice(state.mode, ['reading','development'])}\nModelo/esforço solicitado: ${model(state.requested_model)} / ${effort(state.requested_effort)}\nModelo/esforço observado: ${model(state.observed_model)} / ${effort(state.observed_effort)}\nDuração: ${number(state.duration_seconds)} s\n${total}\n\n## Objetivo\nNão incluído.\n\n## Resultado gerado pelo executor\n${clip(state.result, 12000)}\n\n## Mudanças e verificações\nArquivos observados: não disponibilizados para exportação verificada. Testes e comandos: não estruturados.\n\n## Bloqueios e próximas ações\n${clip(state.error, 2000)}\nPróximas ações: não estruturadas.\n\n## Etapas e agentes\n${stages}`;
    return clip(report, 18000);
  }
  $('report-preview-button').addEventListener('click', () => {
    if (!latestState || !['completed','failed','interrupted'].includes(latestState.status)) return;
    $('report-text').value = reportText(latestState);
    $('report-feedback').textContent = '';
    $('report-preview').classList.remove('hidden');
  });
  $('report-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('report-text').value); $('report-feedback').textContent = 'Relatório copiado.'; }
    catch (_) { $('report-feedback').textContent = 'Não foi possível copiar. Selecione o texto para copiá-lo manualmente.'; }
  });
  $('cancel').addEventListener('click', async () => {
    $('cancel').disabled = true;
    try { await api('/api/cancel', {}); } finally { $('cancel').disabled = false; }
  });
  $('send-message').addEventListener('click', async () => {
    const text = $('message').value; if (!text.trim() || active) return;
    active = true; $('message').value = ''; $('send-message').disabled = true;
    try {
      const response = await api('/api/message', { prompt: text });
      if (!response.ok) throw new Error((await response.text()) || 'Não foi possível enviar a mensagem.');
      render({ status: 'starting' });
      stream();
    } catch (error) { active = false; render({ status: 'failed', error: error.message }); }
    finally { $('send-message').disabled = false; }
  });
  $('escalate').addEventListener('click', async () => {
    const text = $('message').value, reason = $('escalation-reason').value;
    if (!text.trim() || !reason.trim() || active) return;
    active = true; $('escalate').disabled = true;
    try {
      const response = await api('/api/message', { prompt: text, escalate: true, escalation_reason: reason });
      if (!response.ok) throw new Error((await response.text()) || 'Escalonamento não enviado.');
      $('message').value = ''; $('escalation-reason').value = ''; render({ status: 'starting' }); stream();
    } catch (error) { active = false; addEvent(error.message); }
    finally { $('escalate').disabled = false; }
  });
  function newTask() {
    drafting = true; handoffDraft = null; $('report-preview').classList.add('hidden'); $('report-text').value = '';
    $('handoff-preview').classList.add('hidden'); $('handoff-confirm').checked = false;
    $('publish-draft-pr').checked = false;
    start.textContent = 'Iniciar nova tarefa'; start.disabled = false;
    prompt.value = ''; prompt.dispatchEvent(new Event('input')); prompt.focus(); $('execution').classList.add('hidden');
    $('approval-feedback').textContent = ''; $('approval-feedback').classList.add('hidden'); lockSelection(false);
  }
  $('new-task').addEventListener('click', newTask);
  $('discard-handoff').addEventListener('click', newTask);
  $('continue-work').addEventListener('click', () => {
    if (active || !latestState?.handoff) return;
    const state = latestState;
    newTask();
    handoffDraft = state.handoff;
    project.value = state.project;
    // Read-only is the default for a new handoff; development is a fresh choice.
    mode.value = 'reading'; mode.dispatchEvent(new Event('change'));
    project.disabled = true;
    $('handoff-summary').value = handoffDraft.summary;
    $('handoff-confirm').required = true;
    $('handoff-preview').classList.remove('hidden');
    $('handoff-summary').focus(); start.textContent = 'Continuar com resumo';
  });
  async function recover() {
    start.disabled = true;
    try {
      const response = await fetch('/api/session', { headers: { 'X-CSRF-Token': csrf } });
      if (!response.ok) throw new Error('Não foi possível recuperar a sessão.');
      const state = await response.json();
      $('conversation').replaceChildren(); $('event-list').replaceChildren();
      for (const [, kind, data] of state.events) {
        if (kind === 'message') addMessage(data.role, data.text);
        else if (kind === 'event') addEvent(data.message);
      }
      lastId = state.cursor;
      render(state);
      stream();
    } catch (error) {
      $('execution').classList.remove('hidden');
      $('error').classList.remove('hidden'); $('error').textContent = error.message;
      setTimeout(recover, 2000);
    }
  }
  recover();
  setInterval(async () => {
    if (!active || !streamPromise) return;
    try {
      const response = await fetch('/api/status', {headers: {'X-CSRF-Token': csrf}});
      if (response.ok) render(await response.json());
    } catch (_) { /* SSE reconnects independently. */ }
  }, 1000);
  let maintenanceWaiting = false;
  async function maintenanceStatus() {
    try {
      const response = await fetch('/api/maintenance', {headers: {'X-CSRF-Token': csrf}});
      if (response.status === 403 && maintenanceWaiting) { location.reload(); return; }
      if (!response.ok) return;
      const data = await response.json();
      if (maintenanceWaiting && data.instance !== csrf) { location.reload(); return; }
      if (maintenanceWaiting && !data.operation?.finished_at) return;
      $('maintenance-status').textContent = `LIVE: ${data.live} · ${data.version}\nImplantado: ${data.deployed || '—'}\nMain: ${data.remote || '—'}\nVerificação: ${new Date(data.checked_at * 1000).toLocaleString()}\n${data.message || ''}\n${data.operation ? JSON.stringify(data.operation) : ''}`;
      $('maintenance-restart').disabled = !data.available || data.busy || active;
      $('maintenance-update').disabled = !data.available || data.busy || active;
      $('maintenance-update').hidden = data.version !== 'atualização disponível';
    } catch (_) { /* Poll again while the service restarts. */ }
  }
  for (const action of ['restart', 'update']) {
    $(`maintenance-${action}`).addEventListener('click', async () => {
      if (!confirm(`${action === 'update' ? 'Atualizar e reiniciar' : 'Reiniciar'} a LIVE? Sessões em memória serão perdidas.`)) return;
      const response = await api('/api/maintenance', {action, confirm: true});
      const result = await response.json();
      $('maintenance-status').textContent = result.message;
      if (result.accepted) {
        $('maintenance-restart').disabled = true;
        $('maintenance-update').disabled = true;
        setTimeout(() => { maintenanceWaiting = true; }, 6000);
      }
    });
  }
  maintenanceStatus();
  setInterval(maintenanceStatus, 10000);
})();
