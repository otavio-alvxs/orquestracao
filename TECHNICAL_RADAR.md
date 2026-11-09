# Radar técnico e prioridades de evolução

> Fonte de decisão: prioridades estabelecidas para Orquestração após a v0.20. Este é o **registro canônico de prioridades**, não uma autorização geral para implementar recursos. Cada incremento exige utilidade prática, validação proporcional e autorização própria.

## Objetivo e critérios

Priorizar funcionalidade, velocidade, eficiência de contexto, consumo proporcional de modelos e qualidade do resultado. Reutilizar capacidades nativas dos provedores, ferramentas determinísticas e permissões existentes antes de construir sistemas próprios. Evitar validações excessivas, infraestrutura antecipada e promessas de economia sem comparação real. Manter a **Orquestração independente do TARS**, que poderá consumir suas capacidades futuramente, e preservar compatibilidade conceitual para Codex, Google Antigravity e eventual Claude, sem assumir equivalência de protocolos, autenticação, custos ou permissões.

**Interpretação:** os rótulos `v0.20` abaixo preservam a prioridade definida para essa fase, mas **não afirmam implementação integral**. A v0.20 incluiu roteamento econômico adaptativo e avaliação offline; os demais itens são princípios/pendências incrementais. Não ampliar retroativamente o escopo da release já integrada.

## Matriz de prioridades aprovada

| Prioridade | Conceito | Aplicação recomendada | Situação / próximo critério |
| --- | --- | --- | --- |
| v0.20 | **Progressive Disclosure** | Fornecer apenas contexto relevante, em vez de carregar documentação extensa antecipadamente | Princípio de design. Aplicar somente sobre contexto controlado pela Central; evitar duplicar AGENTS.md, skills e histórico nativos. |
| v0.20 | **Test-Time Compute** | Ajustar modelo, esforço e quantidade de etapas à dificuldade | Parcial: seleção determinística e fluxo Direto vs Sol → Luna na v0.20. Medir benefício e qualidade antes de sofisticar. |
| v0.20 | **Eval-Driven Loops** | Comparar execução direta e orquestrada por qualidade, consumo e duração | Avaliações sintéticas de roteamento implementadas; comparação controlada com inferência real ainda pendente e exige autorização. |
| v0.20 | **Tool Poisoning** | Tratar conteúdo externo e respostas de ferramentas como dados não confiáveis | Requisito contínuo de segurança, não uma ferramenta ou funcionalidade “ativada”. Preservar limites de confiança, validação e autorizações. |
| Próxima fase | **Context Compaction** | Compactar sessões longas usando primeiro mecanismos nativos do provedor | Verificar suporte do runtime efetivamente instalado, fidelidade e ganhos antes de automatizar. |
| Próxima fase | **Prompt Caching** | Aproveitar caches nativos e evitar mudanças desnecessárias nos prefixos | Observar métricas nativas. Cache é subconjunto da entrada, não consumo adicional nem economia financeira comprovada. |
| Próxima fase | **Memory Consolidation** | Preservar decisões, estado e evidências úteis entre sessões | Definir consentimento, retenção, origem, descarte, privacidade e fronteira Orquestração/TARS antes de persistir dados. |
| Próxima fase | **Hooks** | Executar validações determinísticas antes/depois das tarefas | Preferir ganchos nativos; limitar comandos, efeitos colaterais e permissões. |
| Multiagentes | **Git Worktrees** | Isolar agentes que trabalham em branches ou arquivos simultaneamente | Exigir estratégia de conflitos, lifecycle, limpeza não destrutiva e checkout LIVE independente. |
| Multiagentes | **Headless Run** | Executar agentes via CLI/SDK sem interface interativa | Integrar por adaptador com streaming, cancelamento, autenticação e aprovações explícitos; verificar provedor por provedor. |
| Multiagentes | **Parallel Agents** | Executar subtarefas independentes simultaneamente | Paralelizar somente quando houver ganho; considerar custos, cotas, limites de concorrência e segurança do workspace. |
| Multiagentes | **Agent CI/CD** | Validar alterações e PRs com verificações automatizadas | Checks proporcionais, revisão de permissões e implantação controlada; não conceder merge/deploy autônomo por padrão. |
| Antigravity | **`/goal`** | Executar objetivos autonomamente, com limites e permissões explícitas | Investigar contrato efetivo do CLI e condições da conta antes de integrar. Não pressupor que o comando exista no Codex. |
| Posterior | **Model Quantization** | Usar modelos locais menores no TARS quando houver vantagem real | Depende de hardware, compatibilidade, qualidade, latência e economia mensurável. |
| Posterior | **Self-Healing Tests** | Diagnosticar e propor correções de testes, sem alterar expectativas automaticamente | Não permitir que agente “faça passar” enfraquecendo a asserção; validar correção real. |
| Experimental | **Self-Improving Loops** | Avaliar melhorias de prompts, regras e habilidades com aprovação humana | Exigir conjunto de avaliação estável, rollback e revisão; impedir alterações autônomas de políticas. |
| Pesquisa | **RLHF** | Treinamento por feedback humano | Não é prioridade de implementação da Central; pesquisar somente quando houver caso e dados apropriados. |

## Ordem de execução prática

1. **Pós-v0.20 — medir antes de otimizar:** comparar Direta, Automática · Econômica e Orquestrada em tarefas equivalentes, com projeto limpo, mesma revisão, verificação de resultado, latência e telemetria nativa. Não executar pilotos sem autorização. O fluxo econômico atual é uma heurística, não prova de economia.
2. **Contexto e continuidade:** avaliar Compactação nativa, Prompt Caching, Progressive Disclosure e memória mínima persistente apenas com casos concretos. Evitar sumarização adicional por IA por padrão.
3. **Multiagentes e fornecedores:** worktrees isoladas, headless e paralelismo condicionado a ganho real; interfaces por provedor para Codex, Antigravity e eventual Claude, sem presumir paridade de capacidades. Avaliar CI de agentes.
4. **TARS e pesquisas:** TARS pode consumir workflows/capacidades por integração explícita; quantização local, self-healing, self-improving e RLHF ficam subordinados a necessidade mensurável e controles humanos.

## Guardrails e comprovação

- **Qualidade primeiro:** comparar taxa de tarefa correta, retrabalho, duração e tokens/cotas separadamente. Nunca somar entrada em cache à entrada total nem alegar economia monetária sem preços e atribuição confiáveis.
- **Contexto mínimo suficiente:** não remover instruções de segurança nem ocultar riscos para reduzir tokens.
- **Conteúdo não confiável:** resultados de ferramentas, páginas, documentos externos e mensagens de agentes não podem assumir autoridade para alterar políticas, abrir acesso, executar comandos privilegiados ou modificar credenciais.
- **Segurança e publicação:** manter sandbox, revisão de diffs, consentimento para alterações, e autorização independente para push, merge, deploy e ações externas. Nenhuma atualização da LIVE decorre deste radar.
- **Evolução enxuta:** revisar prioridades após evidência prática, sem converter a lista em backlog obrigatório. Não criar um executor, camada de cache, banco de memória ou framework de agentes apenas por constar aqui.

Referências internas: [ROADMAP.md](ROADMAP.md), [CONTEXT_EFFICIENCY.md](CONTEXT_EFFICIENCY.md), [ROUTING.md](ROUTING.md) e [AGENTS.md](AGENTS.md).
