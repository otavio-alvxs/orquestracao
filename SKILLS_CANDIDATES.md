# Catálogo de candidatas — Missão 009

Pesquisa pontual para adoção futura, sem instalar ferramentas. Nomes e arquivos
upstream foram consultados nesta missão; compatibilidade operacional só é
comprovada quando houver teste no Codex e revisão do pacote completo.

## Inventário observado

- Codex CLI 0.166.0. Nenhum agente pessoal encontrado no diretório padrão;
  este repositório passa a ter somente `python-maintainer`. A documentação
  [oficial de subagentes](https://learn.chatgpt.com/docs/agent-configuration/subagents)
  descreve os papéis nativos `default`, `worker` e `explorer`; a interface desta
  sessão oferece delegação nativa sem seletor de papel.
- Skills expostas: `imagegen`, `openai-docs`, `skill-creator`, `skill-installer`,
  `open-pr`, `plugin-management`, `sites`, `sites-remix` e as três de Work Pets.
  Há ainda `review-agent` e templates de artefatos no disco/cache; presença de
  arquivos não implica disponibilidade nesta sessão.
- Integrações expostas: GitHub, Google Calendar, Sites, Work Pets, Plugin
  Management, Document Control, busca, Hotline e Safety Settings, além das
  ferramentas nativas do Codex. GitHub funcionou na consulta à PR #2.
- As seis skills candidatas abaixo não foram encontradas nos diretórios de
  skills pessoais, de sistema, do projeto ou no cache de plugins inventariado.
  `open-pr` está presente. `tars-small-pr` não foi encontrado nesses locais;
  não se conclui ausência em outros projetos. Nenhum deles foi modificado.

## Plugins e integrações

### Codex Security

- Finalidade: investigação de vulnerabilidades, revisão de alterações e
  correções com evidências quando existir uma demanda de segurança concreta.
- Fonte: [quickstart oficial do plugin](https://learn.chatgpt.com/docs/security/plugin).
  Produto e documentação confirmados; acesso da conta e compatibilidade de
  execução pendentes. Não aparece nas ferramentas expostas nem no cache consultado.
- Escopo: uso seletivo por projeto. Riscos: leitura de código, execução de
  verificações, custo e sobreposição com revisões existentes. Próxima ação:
  conferir disponibilidade, permissões e tratamento de dados antes de um piloto
  autorizado; não confundir plugin local com serviço Security Cloud.

### GitHub

- Finalidade: consultar repositórios/PRs e executar operações autorizadas.
  Fonte: [integração oficial](https://learn.chatgpt.com/docs/third-party/github)
  e ferramentas do plugin já expostas; leitura da PR #2 comprovada nesta missão.
- Escopo: conexão existente, com autorizações por operação. Não duplicar a
  conexão GitHub se a existente já atender. Escrita, comentários e publicação
  exigem respeitar o pedido e as políticas do projeto; presença não concede
  todas as permissões administrativas. Próxima ação: reutilizar a conexão.

## Skills candidatas globais

### `docs-sync-audit`

- Finalidade: verificar divergências entre documentação e código após mudanças
  de CLI, API ou configuração. Fonte confirmada:
  [GitHub Awesome Copilot](https://github.com/github/awesome-copilot/blob/main/skills/docs-sync-audit/SKILL.md).
  Declara portabilidade entre agentes; execução no Codex pendente; ausente no inventário.
- Escopo: global, acionamento explícito por mudança. Inclui `scripts/docs_drift.py`;
  revisar esse auxiliar e evitar o padrão de auditoria ampla sem necessidade.
  A instrução sobre tratar arquivos como evidência não substitui os AGENTS.md.
  Próxima ação: revisar pacote/licença e testar em um diff pequeno.

### `github-actions-hardening`

- Finalidade: revisar gatilhos, injeção em scripts, tokens, referências de actions
  e runners quando houver workflows relevantes. Fonte confirmada:
  [GitHub Awesome Copilot](https://github.com/github/awesome-copilot/blob/main/skills/github-actions-hardening/SKILL.md).
  Instruções e referências existem; teste no Codex pendente; ausente no inventário.
- Escopo: global, somente projetos com GitHub Actions; este repo não tem workflows.
  Riscos: aplicar recomendações sem entender limites de confiança, permissões e
  semântica dos gatilhos. Próxima ação: revisar `references/` e validar num workflow
  real antes de adotar; não criar CI apenas para usar a skill.

### `systematic-debugging`

- Finalidade: reproduzir falhas, formular hipóteses e investigar causa-raiz;
  usar somente quando houver diagnóstico de bug ou falha. Fonte confirmada:
  [obra/superpowers](https://github.com/obra/superpowers/blob/main/skills/systematic-debugging/SKILL.md).
  Compatibilidade operacional pendente; ausente no inventário.
- Escopo: global sob demanda. Referencia outras skills do Superpowers e arquivos
  auxiliares de diagnóstico; não importar toda a suíte automaticamente. Revisar
  exemplos e scripts para evitar registro de segredos e processo desproporcional.
  Próxima ação: revisar dependências e testar num bug delimitado.

### `post-patch-validation`

- Finalidade: validar patches, especialmente correções de segurança, comparando
  base e versão corrigida com reprodução, variantes e regressões. Fonte confirmada:
  [Trail of Bits](https://github.com/trailofbits/skills/blob/main/plugins/post-patch-validation/skills/post-patch-validation/SKILL.md).
  Ausente no inventário; adaptação/execução no Codex pendentes.
- Escopo: global sob demanda. Pacote inclui runner Python, invoca `uv` e descreve
  workflow específico de Claude; esse workflow não está comprovado no Codex.
  Executa código local: isolamento do runner não equivale a sandbox do sistema.
  Próxima ação: revisar runner, referências, licença e requisitos; piloto isolado
  com patch de segurança autorizado, sem substituir a suíte normal por inferência.

### `harness-engineering`

- Finalidade: transformar erros recorrentes de agentes em regras e verificações
  duráveis quando houver evidência de repetição. Fonte confirmada:
  [GitHub Awesome Copilot](https://github.com/github/awesome-copilot/blob/main/skills/harness-engineering/SKILL.md).
  Aplicação no Codex pendente; ausente no inventário.
- Escopo: candidata global, alterações sempre locais e explicitamente autorizadas.
  Riscos: proliferar instruções, scripts, memória e relatórios conflitantes com
  AGENTS.md. Próxima ação: manter em espera até um problema recorrente justificar
  um incremento mínimo; não implantar um framework.

## Skill candidata local dos dotfiles

### `arch-linux-triage`

- Finalidade proposta: diagnóstico de problemas Arch Linux e configuração local,
  preferencialmente no escopo dos dotfiles. Fonte original local ainda pendente:
  o nome não foi localizado no inventário nem na busca pública exata; não há URL,
  caminho ou versão canônica comprovada. Ausência confirmada apenas nos locais
  inventariados; os arquivos internos dos dotfiles não foram inspecionados.
- Compatibilidade e licença pendentes. Riscos: comandos de administração,
  exposição de logs/configurações pessoais e instruções específicas da máquina.
  Próxima ação: localizar a fonte nos dotfiles numa tarefa própria e revisar seus
  arquivos antes de qualquer ativação; não promovê-la globalmente por padrão.

## Critérios de adoção

- Não instalar skills externas sem revisão de procedência, licença e arquivos
  auxiliares. Fixar uma revisão upstream na futura avaliação e revalidar caminhos:
  links para `main` são referências mutáveis, não versões aprovadas.
- O antigo [openai/skills](https://github.com/openai/skills) se declara descontinuado
  e aponta para [openai/plugins](https://github.com/openai/plugins), cujo README
  confirma o catálogo de exemplos atual. Não presumir que candidatas comunitárias
  sejam oficiais ou compatíveis só por usarem `SKILL.md`.
- Preservar `open-pr` e `tars-small-pr`, respeitar os AGENTS.md dos projetos e
  evitar duplicação de fluxos. Implantação futura seletiva e reversível, uma
  candidata por vez, com autorização própria e possibilidade de remoção.
- Prioridade prática: reutilizar GitHub; avaliar `docs-sync-audit` numa mudança
  documental e `systematic-debugging` no próximo bug. As demais dependem dos
  gatilhos descritos acima. Nenhuma candidata foi instalada nesta missão.
