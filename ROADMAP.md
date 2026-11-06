# Roadmap

Este documento preserva a visão de longo prazo do Orquestração. As possibilidades
futuras não são compromissos de implementação: cada incremento depende de
necessidade prática, benefício demonstrável, escopo e autorização próprios.

## Funcionalidades já comprovadas

- Roteamento nativo Sol → Luna comprovado e inspeção validada com históricos reais,
  conforme as validações anteriores informadas para o projeto.
- Configuração global externa ao repositório: Sol como coordenador, Luna como
  subagente padrão e limite inicial de dois filhos simultâneos. Essa configuração
  não é copiada nem modificada por esta entrega.
- `inspect_sessions.py`: inspeção somente de leitura de históricos explicitamente
  selecionados, usando apenas a biblioteca padrão do Python. Relaciona sessões e
  evidências de delegação, distingue configurações solicitadas das observadas e
  restringe a saída a metadados permitidos, sem publicar conteúdo privado.
- v0.2 (Missão 008): `--format tree` apresenta relações confirmadas em múltiplos
  níveis, sessões sem vínculo conhecido, referências incompletas, ciclos e resumo,
  preservando o JSON padrão e a allowlist. Testes usam somente dados sintéticos.
- A implementação da árvore foi delegada a um único subagente pelo mecanismo
  nativo compatível, sem modelo explícito no spawn. A interface desta execução
  não expõe seleção do tipo `worker` nem informa o modelo resolvido no retorno
  do spawn. O subagente relatou `gpt-6-luna` em seu contexto, sem verificação em
  históricos privados. A revisão identificou necessidade de corrigir a ordem dos
  ramos com vários filhos. O mecanismo nativo compatível atendeu à tarefa;
  o tipo `worker` específico não pôde ser avaliado nesta interface. Nenhuma
  configuração global foi alterada; não houve motivo para criar agente próprio.

O inspetor não é um dashboard nem um gerenciador de agentes. As capacidades
abaixo permanecem propostas de evolução.

## v0.3 — Missão 009

- `--format tree --root <session-id>` limita a visualização e o resumo à raiz
  escolhida e seus descendentes confirmados, preservando proteção contra ciclos,
  indicação de metadados ausentes e comportamento sem filtro. Raiz inexistente
  produz erro com código 2. Testes usam somente dados sintéticos.
- Primeiro especialista de desenvolvimento de software independente de linguagem:
  [`developer`](.codex/agents/developer.toml). Implementa e mantém software,
  investiga bugs e preserva contratos e interfaces, com mudanças pequenas e
  validação proporcional. Respeita AGENTS.md, sandbox e autorizações, sem
  refatorações oportunistas. Luna/low é a configuração inicial; decisões de
  escalonamento cabem ao coordenador. Conhecimento detalhado de linguagens e
  ecossistemas poderá vir de skills ou instruções de projeto.
- Agente experimental: descoberta de configuração comprovada no CLI 0.166.0
  em configuração temporária isolada, sem inferência; execução específica do
  papel ainda não comprovada.
- A sessão da avaliação original não expunha seleção de papel no spawn. Um único
  filho nativo implementou código/testes com Luna/low solicitados; modelo efetivo não
  comprovado. Revisão pediu simplificações e reforço dos testes. Delegação nativa
  útil para separar implementação e pesquisa; ganho específico do especialista
  ainda pendente de tarefa real numa nova sessão com seleção do papel disponível.
  Nenhuma configuração global foi alterada.
- [Catálogo de oito candidatas](SKILLS_CANDIDATES.md) preserva a pesquisa,
  o inventário e os critérios para adoção seletiva futura; nenhuma instalação.

## v0.4 — Missão 010

- `developer` operacionalmente validado no Codex 0.166.0: seleção explícita por
  `agent_type="developer"` na ferramenta nativa numa única auditoria documental.
  Papel confirmado pelo metadado `agent_role`; Luna/low observados em
  `turn_context` do filho, excluído o histórico herdado. Não comprova economia de
  inferência ou disponibilidade em todas as interfaces. Detalhes no README.
- Primeira skill externa instalada somente neste projeto:
  [docs-sync-audit](.agents/skills/docs-sync-audit/SKILL.md), com pacote e licença
  revisados e [procedência fixada](.agents/skills/docs-sync-audit/PROVENANCE.md).
  As referências à ausência de instalação na v0.3 descrevem a missão anterior.
- Piloto comparou documentação com CLI, testes e definição do agente. Comandos,
  argumentos e códigos de saída coerentes; corrigidos nome antigo e estados do
  catálogo/validação. Inspetor preservado; 19 testes existentes passaram.
- Na v0.4, reutilização local e futura promoção pessoal por link foram documentadas;
  naquela missão não houve promoção global, mudança de outro projeto ou nova
  infraestrutura. A recomendação de link foi substituída na v0.5.

## v0.5 — Missão 011 (fechamento 011D)

- **Promoção pessoal e reutilização do Developer no TARS: VERIFIED.** O symlink
  foi substituído por TOML regular em `~/.codex/agents/developer.toml`, com conteúdo
  correspondente à definição versionada do Orquestração.
- Sem definição local homônima no TARS, a seleção explícita de
  `agent_type="developer"` funcionou. Metadados estruturados próprios da sessão
  filha confirmaram `agent_role=developer`, `gpt-6-luna` e esforço `low`, excluindo
  histórico herdado. Não se publicam históricos privados.
- O Developer concluiu a análise somente de leitura de `automation reminder`,
  cobrindo validação, persistência, runtime e testes. Nenhum arquivo ou estado
  operacional do TARS foi alterado. O levantamento inicial do coordenador e o
  inventário de skills permanecem no README com sua procedência original.
- Histórico preservado: a tentativa inicial ficou **BLOCKED** por filesystem
  somente de leitura; esses bloqueios foram superados. Na continuação, o symlink
  mostrou incompatibilidade com o carregamento do TOML no Codex 0.166.0 neste
  ambiente, resolvida com arquivo regular. Erro de glob do Zsh resolvido sem
  mudanças globais no shell.
- README recomenda instalação regular sem sobrescrita, verificação de integridade,
  registro privado de procedência e atualização explícita. Rollback remove apenas
  a cópia pessoal comprovadamente instalada, após conferir conteúdo e procedência;
  o procedimento destinado ao antigo link foi retirado.
- O fechamento é documental, baseado no resultado final comprovado informado;
  não repete o piloto nem altera código, agentes, skills, configurações pessoais
  ou outros repositórios. Nenhuma infraestrutura nova, merge, tag ou release.

## v0.6 — Missão 012

- `observed_agent_roles` no JSON identifica papéis personalizados observados em
  `session_meta.payload.source.subagent.thread_spawn.agent_role`; árvore mostra
  `papel=...` quando disponível. Preserva `roles`, entradas anteriores, filtros,
  ciclos e resumo. Allowlist de identificadores existente; ausência ou valor
  inválido produz lista vazia, sem inferir dos argumentos solicitados ou do
  histórico herdado. Somente biblioteca padrão e testes sintéticos.
- Primeiro [`reviewer`](.codex/agents/reviewer.toml) local, independente de linguagem:
  bugs, regressões, contratos, privacidade e cobertura, com achados por impacto e
  evidências de arquivo/linha. Não edita durante a revisão, publica ou altera
  permissões; evita auditorias extensas e preferências de estilo. Terra/medium
  disponível no catálogo instalado; nenhum ajuste pessoal ou no Developer/Sol.
- PR #5 confirmada integrada; main sincronizada e branch da v0.6 criada. Developer
  implementou código/testes com seleção explícita e metadados próprios confirmando
  `developer`, Luna/low, excluído histórico herdado.
- Na tentativa inicial, a seleção do Reviewer personalizado retornou
  `unknown agent_type 'reviewer'`. CLI 0.166.0 descobriu o arquivo no catálogo em
  configuração temporária isolada, sem inferência; essa descoberta não validava
  a invocação. A pendência foi resolvida na Missão 012B abaixo.
- Revisão independente realizada pelo fluxo nativo disponível: `default` com
  Terra/medium confirmados em metadados próprios, sem fork de histórico e sem
  avaliação pronta do Developer. Nenhum defeito confirmado, nenhuma correção
  necessária. Coordenador integrou; 21 testes sintéticos e `git diff --check`
  passaram. Evidências e exemplo sintético no README, sem publicar históricos.
- Separação útil para segunda análise independente; ganho de qualidade, tempo e
  custo não medido. Sem infraestrutura adicional nem promoção global nesta missão.

### Missão 012B — Reviewer personalizado VERIFIED

- Nova sessão expôs `reviewer`; seleção explícita de `agent_type="reviewer"` aceita,
  com `fork_turns="none"`, sem sobrescrever modelo/esforço.
- Cabeçalho do filho, vinculado à sessão atual, confirmou `agent_role=reviewer`;
  `turn_context` próprio confirmou `gpt-6.1-terra` / `medium`. Sem histórico herdado
  na comprovação; somente metadados estruturados do filho recém-criado consultados.
- Revisão independente somente de leitura de `main...HEAD` no commit
  `d7793e4dc16cfa49342340ff21eab3b65334a209`: nenhum defeito confirmado em bugs,
  privacidade, compatibilidade e testes. Os 21 testes sintéticos passaram.
- Nenhuma correção de código; README, ROADMAP e descrição da PR #6 registram
  VERIFIED, preservando a tentativa anterior como histórico. PR mantida em draft;
  configurações pessoais e outros repositórios preservados. Evidências e limites
  no README; sem publicar históricos ou medir ganho de qualidade, tempo ou custo.

## v0.7 — Missão 014

- Primeira TUI em Python/Textual com execução real via `codex exec --json`,
  somente leitura, sessão efêmera, streaming, resultado, cancelamento e seleção
  explícita de diretório. Dependência opcional da TUI em ambiente virtual;
  inspetor preservado sem dependências externas.
- Eventos e estados são apresentados conforme as evidências disponíveis, sem
  inferir modelo/esforço de TOMLs nem consultar históricos privados. Especialistas
  continuam sob o mecanismo nativo do Codex; a TUI não cria runtime de agentes.
- Smoke real pela TUI com CLI 0.166.0 concluído: 11 eventos, resposta não vazia,
  saída 0 e processo recolhido. Testes sintéticos de processo/interface e revisão
  independente executados; sem inferir agentes ou modelos de configurações.
- Aprovações interativas via app-server, sessões persistentes, métricas reais de
  tokens e duração, configuração de especialistas, histórico de tarefas,
  visualização avançada, roteamento automático e interface web local (se
  justificada) permanecem possibilidades futuras com escopo e autorização próprios.

## Próximas capacidades prioritárias

- Evoluir a observabilidade somente de leitura a partir das evidências existentes,
  respeitando as limitações dos formatos reconhecidos e a privacidade dos históricos.
- Medir tempo e tokens consumidos, taxa de sucesso, retrabalho, uso dos modelos
  mais potentes, custos e manutenção. Comparar com execução direta e ferramentas
  sem IA antes de justificar novas camadas de orquestração.
- Desenvolver agentes e workflows reutilizáveis apenas quando tarefas reais
  demonstrarem necessidade; reutilizar recursos nativos, skills e ferramentas
  existentes antes de construir alternativas.

Priorizar funcionalidade, velocidade, economia e segurança proporcional. A ordem
dos incrementos será definida pela utilidade observada, sem benchmarks ou novas
inferências apenas para demonstração.

## Ideias futuras

### Orquestração inteligente e econômica

- Usar Sol para coordenação e tarefas complexas; Luna e outros modelos rápidos
  para tarefas delimitadas, com delegação somente quando houver benefício concreto.
- Executar operações determinísticas sem consumo de LLM sempre que possível.
- Evoluir para roteamento automático por complexidade, custo, capacidade e contexto.
- Considerar modelos locais via Ollama e múltiplos provedores, sem introduzir
  dependências antecipadas.
- Manter paralelismo controlado, isolamento de tarefas e prevenção de conflitos.
  A quantidade de especialistas disponíveis na biblioteca é independente do limite
  de concorrência: ter muitos especialistas não exige executá-los simultaneamente.

### Biblioteca de agentes especializados e reutilizáveis

Construir progressivamente, conforme demanda prática, agentes instruídos para:

- Desenvolvimento e implementação.
- Testes, debugging e revisão.
- Auditoria de segurança.
- Documentação e Git/GitHub.
- Manipulação de arquivos.
- Pesquisa e investigação.
- Geração e processamento de vídeos, imagens e voz.
- Automações operacionais.

Cada agente poderá ter modelo, instruções, skills, ferramentas, permissões e
critérios de validação próprios. Não criar agentes sem necessidade prática.

### Skills e ferramentas

Possibilidades a avaliar: `systematic-debugging`, `post-patch-validation`,
`github-actions-hardening`, `arch-linux-triage` e
`harness-engineering`, além de operações determinísticas de arquivos e workflows
Git/GitHub. A lista não implica instalação nem disponibilidade atual; `docs-sync-audit`
já foi adotada localmente na v0.4.

Reutilizar capacidades existentes antes de criar outras. Examinar proveniência,
licenças e segurança de componentes de terceiros antes de adotá-los.

### Observabilidade e gerenciamento

Usar [cauecarneiro/Overview-Painel](https://github.com/cauecarneiro/Overview-Painel)
como referência conceitual a avaliar, sem integração ou dependência atual, para:

- Visualização da árvore de agentes, estado de execução e tarefas.
- Modelos utilizados, tempo, tokens, cotas e custos.
- Histórico e evidências de delegação.
- Associação entre tarefas, branches, PRs e CI.
- Dependências, pendências e aprovações humanas.
- Futuro gerenciamento de agentes e tarefas.

Começar por observabilidade somente de leitura. Controle ativo dependerá de
mecanismos de autorização seguros e de escopo próprio.

## Integração futura com o TARS

O Orquestração permanece independente do TARS. Futuramente, o TARS poderá consumir
capacidades, agentes e workflows aqui desenvolvidos, sem acoplamento antecipado.

Preservar a visão de execução rápida para tarefas simples, raciocínio deliberativo
para tarefas complexas, contexto e memória persistentes, delegação estruturada,
tarefas supervisionadas e evolução controlada.

O TARS Core permanece como autoridade central. Nenhum agente externo recebe
permissões adicionais por delegação.

## Decisões e princípios que não devem ser perdidos

- Preferir recursos nativos do Codex e ferramentas determinísticas; evitar
  inferência, infraestrutura e trabalho redundantes.
- Trabalhar em incrementos pequenos, verificáveis e autorizados, com validação
  proporcional à mudança e critérios de sucesso ligados à utilidade real.
- Respeitar sandbox, permissões e aprovações humanas. Não publicar credenciais,
  históricos privados, caminhos privados ou configurações globais do Codex.
- Ajustar concorrência conforme necessidade e recursos, sem alterar configurações
  globais sem autorização explícita.
- Preservar reutilização futura e independência do TARS. Agentes, skills, serviços,
  dashboards e integrações exigem escopo próprio antes de serem implementados.
- Avaliar o custo de manutenção junto aos ganhos de tempo, tokens e qualidade;
  adotar execução direta quando a orquestração não trouxer benefício.
