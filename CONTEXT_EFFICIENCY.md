# v0.9 — eficiência de contexto (Missão 016)

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
