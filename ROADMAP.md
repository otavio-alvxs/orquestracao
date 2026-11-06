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
- Sete testes existentes cobrem os formatos reconhecidos, limites da correlação,
  histórico herdado, entradas inválidas e preservação dos arquivos de entrada.

O inspetor não é um dashboard nem um gerenciador de agentes. As capacidades
abaixo permanecem propostas de evolução.

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
`docs-sync-audit`, `github-actions-hardening`, `arch-linux-triage` e
`harness-engineering`, além de operações determinísticas de arquivos e workflows
Git/GitHub. A lista não implica instalação nem disponibilidade atual.

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
