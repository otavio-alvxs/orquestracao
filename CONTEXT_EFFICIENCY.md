# v0.9 — eficiência de contexto (Missão 016)

## v0.20 — política adaptativa e radar técnico

`Automática · Econômica` é opt-in e usa o roteador local existente. Uma execução
única atende às classes reconhecidas como simples/intermediárias; demais classes
mantêm a orquestração conservadora já disponível. O plano transferido pelo
aplicativo tem limite de oito linhas, sem resumidor adicional. Nenhuma métrica
de piloto demonstra economia: cache é subconjunto da entrada e não deve ser
somado; tokens não equivalem a custo ou cota. A Central apresenta apenas os
valores reais informados pelo executor, com totais da orquestração indisponíveis
quando não atribuíveis.

Na validação foi observado o aviso não fatal `Future exception was never
retrieved` em teste de encerramento do app-server. Registrado para referência;
sem refatoração relacionada nesta missão.

### Radar (ordem por prioridade e aplicabilidade)

| Grupo / prioridade | Técnicas | Pré-requisitos e aplicabilidade | Riscos / decisão |
| --- | --- | --- | --- |
| Agora / alta | Eval-Driven Loops; Context Compaction; Progressive Disclosure | Avaliações offline reproduzíveis; compactação só com limites e contexto controlado pela aplicação; divulgação progressiva apenas para conteúdo que a aplicação possui | Perder instruções, omitir contexto ou confundir benchmark sintético com economia real. O roteamento recebe uma avaliação pequena; sem compactação geral nem carregador próprio de AGENTS.md, skills ou histórico Codex. |
| Próximo / média | Prompt Caching; Git Worktrees; Hooks; Headless Run; Agent CI/CD; Self-Healing Tests | Telemetria/provider com atribuição; isolamento Git validado; contratos de hooks; execução não interativa e CI autorizados | Cache não comprova custo reduzido; worktrees e hooks podem executar ações inesperadas; headless exige contratos explícitos de permissão, cancelamento e logs; reparo automático pode mascarar falhas. |
| Explorar / baixa | `/goal`; Parallel Agents; Test-Time Compute; Memory Consolidation; Self-Improving Loops | Suporte nativo disponível no runtime; benefício mensurável; memória controlada e avaliação antes/depois | `/goal` não é contrato da Central; paralelismo eleva contexto e complexidade; mais compute pode aumentar custo; memória/autoaperfeiçoamento podem persistir dados ou reforçar erros. Não implementados. |
| Pesquisa / fora da missão | Model Quantization; RLHF; Tool Poisoning | Quantização exige escolher/operar modelos locais; RLHF exige dados, pipeline e governança; análise de poisoning exige modelo de ameaça e superfícies de ferramenta | Mudam comportamento, segurança ou operação de modelos; não há benefício concreto para esta Central nesta missão. Tool Poisoning é ameaça a mitigar, não recurso a ativar. |

**Antigravity CLI:** a documentação oficial descreve execução headless (`-p` /
`--print`) com formatos JSON e `stream-json` ([Headless mode](https://antigravity.google/docs/cli/headless/),
[referência CLI](https://antigravity.google/docs/cli/reference)). Uma futura
integração deverá obedecer aos contratos próprios de autenticação, permissões,
cancelamento e métricas; esta nota não implica suporte ou integração no Codex
nem neste repositório.

**Comparação futura mínima:** fixar a mesma revisão, projeto limpo, tarefa e
condições de sandbox; sortear ou alternar Direct, Automatic e Orchestrated em
execuções isoladas repetidas; registrar sucesso de tarefa por critério definido,
duração e métricas reportadas separadamente (entrada, cache como subconjunto,
saída, modelo observado quando disponível). Comparar medianas e falhas sem
misturar tokens, custo financeiro ou cota. Fazer apenas com autorização para o
piloto real; a avaliação sintética atual não substitui esse experimento.

## Diagnóstico delimitado

Execução informada pelo usuário: 9,5 s; entrada 32.249 tokens; cache de entrada
22.912; saída 133. Não houve reprodução nem nova inferência para diagnóstico.

**Fatos por inspeção:** `codex_runner.py:build_command` inicia `codex exec`
com JSON, modo efêmero, sandbox somente leitura e diretório do projeto, sem
selecionar modelo ou perfil. `CodexRun.events` envia a tarefa por stdin.
`parse_event` copia métricas agregadas de `turn.completed.usage`; não fornece
decomposição por fonte. Os agentes locais configuram instruções próprias:
Developer Luna/low e Reviewer Terra/medium. AGENTS.md e o catálogo da skill
são outras superfícies de instruções disponíveis, cuja presença não comprova
carregamento integral em cada execução.

**Hipóteses:** instruções do projeto/ambiente, descrições de ferramentas e skills,
configurações efetivas do CLI e leituras realizadas durante a tarefa podem
contribuir para a entrada. Em tarefas delegadas, contexto herdado e retornos
extensos podem ampliar o trabalho do coordenador. Não há atribuição quantitativa
nem confirmação dessas fontes na execução informada.

**Não observável nesta inspeção:** prompt efetivo completo, distribuição de tokens
por componente, configuração global efetiva e participação de subagentes nessa
execução. Não foram lidos históricos privados, credenciais ou prompts anteriores.
Cache não foi somado à entrada como consumo adicional. Economia de tokens,
tempo ou custo permanece não medida, sem comparação válida antes/depois.

## Fluxo e documentação

AGENTS.md concentra as regras e o retorno recomendado: o especialista lê o
necessário e entrega evidências compactas; Sol decide e busca detalhes apenas
quando necessários. Isso reduz releituras e transferência de logs por orientação,
sem prometer redução medida de consumo.

A skill local `docs-sync-audit` cobre auditoria documental e permite atualização
quando autorizada; o Developer atual pode executar ajustes simples no escopo
delegado. São suficientes para esse fluxo, por inspeção das instruções, sem novo
piloto. Documenter Luna/low faria sentido com demanda documental recorrente e
volume que justificasse responsabilidade própria. Não foi criado nem instalado.

Verificação desta missão: revisão do diff e `git diff --check`. Testes funcionais
não executados porque somente documentação foi alterada; TUI e agentes preservados.

## v0.15 — Missão 023: diagnóstico sobre a PR #15

Base inspecionada: merge `4700656`, em 2026-11-06. Usamos primeiro o piloto
registrado em [ROUTING.md](ROUTING.md), sem ler rollouts privados, segredos ou
históricos externos. Não executamos novas inferências para preencher métricas.

| Tarefa | Entrada total | Cache (subconjunto) | Entrada sem cache¹ | Saída | Duração | Resultado |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Resumo | 30.932 | 15.232 | 15.700 | 63 | 5,12 s | completed; resposta correta para conteúdo sintético |
| Tradução | 16.632 | 15.360 | 1.272 | 24 | 1,37 s | completed; resposta correta para conteúdo sintético |

¹ Subtração de entrada menos cache, não uma medição independente. Nos dois
turnos a seleção solicitada foi Luna/low; modelo/esforço da inferência observados
ficaram indisponíveis. Nenhum subagente foi observado. O piloto manteve a mesma
thread, sem escalonamento. São tarefas distintas: a diferença entre linhas não
é comparação antes/depois nem prova de economia de custo.

### Observações confirmadas e limites de atribuição

- **Envio e histórico:** `AppServerRun._start` faz handshake e cria thread uma
  vez por processo. `events` reutiliza o processo e envia em `turn/start` apenas
  o novo prompt, thread e parâmetros do turno. `/api/message` reutiliza o mesmo
  objeto; não concatena o histórico da interface ao prompt. O piloto anterior
  confirmou continuidade. Isso evita reenvio pelo cliente, mas não elimina o
  processamento do histórico pelo runtime.
- **Inicialização:** novas tarefas criam sessões efêmeras; encerrar a central
  perde a thread. Exec/TUI iniciam execução isolada com prompt por stdin.
  São limites do fluxo atual, sem evidência de reinicialização redundante nas
  continuações. Persistência adicional não foi implementada.
- **Instruções e ferramentas:** AGENTS.md, a skill local e os TOMLs de Developer
  Luna/low e Reviewer Terra/medium são superfícies disponíveis no projeto.
  A aplicação não copia esses arquivos nem catálogos de ferramentas para o
  prompt. O Codex controla sua disponibilização. Existência, tamanho em bytes
  ou descrição disponível não medem tokens efetivamente usados pelo modelo.
  A configuração global efetiva não foi lida nem alterada.
- **Chamadas e delegação:** o roteador é determinístico e não chama modelo para
  classificar. A central não agenda especialistas nem retransmite contexto de
  filhos por conta própria. AGENTS.md já orienta escopo limitado e retornos de
  5–8 linhas. O piloto não oferece transferência de contexto de filhos para
  medir, pois nenhum foi observado. Não se atribui seu consumo a delegação.
- **Telemetria:** eventos nativos fornecem entrada/cache/saída agregadas; o
  app-server usa deltas acumulados com base conhecida e invalida regressões.
  Duração é local e resultado é estado terminal, não avaliação automática de
  qualidade. Estado/interface já distinguem seleção solicitada e observação de
  agentes. Não há decomposição de tokens por instrução, ferramenta, histórico,
  inicialização ou chamada interna; total de tokens não conta chamadas de modelo.

A documentação oficial do [app-server](https://learn.chatgpt.com/docs/app-server),
consultada nesta missão, confirma inicialização por conexão, continuidade por
thread e eventos de uso. Ela não oferece atribuição dessas métricas por fonte.
A inspeção da implementação local sustenta as observações de envio acima.

**Hipóteses não comprovadas:** instruções do ambiente, catálogos de skills e
ferramentas, resultados de leituras, histórico nativo e processamento interno
podem contribuir para a entrada. Herança e retornos de especialistas podem
ampliar contexto em tarefas delegadas. Não conhecemos a participação quantitativa
de cada componente, nem demonstramos que o overhead predominante esteja fora
ou dentro do controle da aplicação. Cache não garante redução de custo efetiva.

**Decisão:** nenhuma otimização funcional implementada; não encontramos consumo
redundante controlado pela aplicação com evidência suficiente. Não removemos
instruções, skills, ferramentas ou permissões para melhorar números. Não houve
par antes/depois, pois não há alteração funcional a comparar: eficiência ainda
não comprovada. O próximo gargalo é a ausência de atribuição nativa por fonte;
uma futura mudança deve partir de redundância reproduzível, não de estimativas.

**Verificação:** passaram os testes existentes
`test_handshake_ephemeral_thread_stream_and_same_thread_continuation` e
`test_turn_route_escalation_is_explicit_and_keeps_session`, com servidor sintético,
sem inferência. O teste HTTP inicialmente não pôde abrir socket no sandbox;
reexecutado isoladamente com permissão, passou. `git diff --check` passou.
Não houve mudança funcional, nova suíte ou revisão independente de código;
Developer investigou, e o coordenador revisou o diff documental.
