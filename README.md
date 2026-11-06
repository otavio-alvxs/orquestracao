# Orquestração

Projeto para organizar o uso incremental de agentes, modelos, skills e ferramentas, priorizando funcionalidade, velocidade e economia de recursos.

## Estado atual confirmado

O inspetor de históricos do Codex oferece saída JSON e visualização em árvore de
sessões, com seleção de raiz. Na v0.4 (Missão 010), o especialista
[`developer`](.codex/agents/developer.toml) teve invocação personalizada validada
em uma auditoria documental com a primeira skill externa instalada localmente,
[`docs-sync-audit`](.agents/skills/docs-sync-audit/SKILL.md).

Conforme informado para este bootstrap, a configuração global do Codex usa Sol como coordenador, Luna como padrão para subagentes e concorrência inicial máxima de dois filhos. Essa configuração é externa ao repositório; não foi verificada nem alterada aqui. O limite é ajustável conforme necessidade e recursos disponíveis.

## Decisões

- Preferir recursos nativos do Codex antes de desenvolver alternativas.
- Usar Sol para coordenação e decisões complexas; Luna para subtarefas delimitadas quando houver benefício concreto em delegar.
- Preferir ferramentas determinísticas quando IA for desnecessária, evitando inferência e trabalho redundantes.
- Respeitar segurança, sandbox e autorizações existentes; manter credenciais, caminhos privados e detalhes sensíveis fora do repositório público.
- Manter o projeto independente do TARS, com potencial de reutilização futura.
- Evoluir em incrementos pequenos, verificáveis e autorizados.

## Inspeção mínima de sessões (Missão 005)

Requer Python 3.9 ou superior, somente com biblioteca padrão. Informe explicitamente
um arquivo JSONL ou um diretório; diretórios são percorridos recursivamente, apenas
para arquivos `.jsonl`, sem seguir links simbólicos. Nenhum histórico é localizado
automaticamente, enviado pela rede ou alterado.

```sh
python3 inspect_sessions.py /caminho/autorizado/historico.jsonl
python3 inspect_sessions.py /caminho/autorizado/historicos
python3 inspect_sessions.py /caminho/autorizado/historicos --format tree
python3 inspect_sessions.py /caminho/autorizado/historicos --format tree --root session-id
python3 -B -m unittest -v test_inspect_sessions.py
```

A saída JSON contém apenas identificadores, papéis, modelos, esforços, evidências
das relações e contadores de diagnóstico. Não inclui caminhos, prompts, respostas
ou conteúdo bruto de ferramentas. `observed_settings` vem de `turn_context`;
`requested_settings` vem dos argumentos de `spawn_agent` e não comprova o modelo
usado. `null` e listas vazias indicam ausência de dados reconhecidos; não se infere
herança de modelo ou esforço. Mudanças de configuração são preservadas como
combinações distintas, sem contar turnos ou chamadas.

O formato padrão continua sendo JSON; `--format json` o seleciona explicitamente.
Com `--format tree`, cada sessão mostra seu identificador e as combinações de
modelo/esforço observadas, com um resumo do total de sessões e delegações
confirmadas. Sessões sem relação conhecida também aparecem. Referências sem
metadados e configurações desconhecidas são indicadas sem usar valores solicitados
como se fossem observados. Relações cíclicas e sessões com múltiplos pais são
marcadas para limitar a expansão; a árvore não comprova que o grafo seja consistente.
Os totais incluem sessões apenas referenciadas e relações confirmadas distintas,
não chamadas de ferramenta. As mesmas limitações de reconhecimento e a mesma
allowlist do JSON se aplicam à árvore.

`--root <session-id>` exige `--format tree` e mostra a sessão escolhida e somente
seus descendentes alcançáveis por relações confirmadas. O resumo conta apenas as
sessões e delegações incluídas, inclusive referências sem metadados; ancestrais
e outras sessões ficam fora, salvo se alcançados por um ciclo confirmado.
Referências sem metadados também podem ser escolhidas como raiz. O filtro muda
a visualização: a leitura e os diagnósticos continuam abrangendo toda a entrada.
Sem `--root`, JSON e árvore mantêm o comportamento anterior.

Formatos reconhecidos: `session_meta.payload.id`, origem principal `cli`, `exec`
ou `vscode`, origem de subagente `source.subagent` e parentesco em
`source.subagent.thread_spawn.parent_thread_id`. Também reconhece
`response_item` com `function_call` de `spawn_agent` (inclusive os prefixos
`functions.` e `collaboration.`) e `function_call_output` associado por `call_id`,
com `agent_id` no retorno JSON. A saída publica somente esses metadados permitidos;
textos livres e valores fora do formato de identificador/modelo são descartados.

Quando o cabeçalho informa `subagent_history_start_ordinal`, os registros anteriores
a esse ordinal são tratados como histórico herdado e ignorados. O cabeçalho inicial
identifica o filho; um `session_meta` copiado do pai não muda essa atribuição.
Retornos de `spawn_agent` contendo apenas `task_name` não confirmam um identificador;
nesses casos, o parentesco pode ser confirmado pelo metadado do filho. Esforço
solicitado ausente nos argumentos estruturados permanece desconhecido.

Uma sessão pode ser subagente e coordenadora. `metadata_present: false` indica
uma sessão referenciada cujo metadado não foi encontrado na entrada. Ausência de
relações não comprova ausência de delegação: formatos desconhecidos são ignorados,
assim como chamadas sem retorno confirmado. Este utilitário não cobre todos os
formatos internos do Codex nem reconstrói delegações a partir de texto ou de
`codex exec --json`.

Código de saída: `0` para leitura sem problemas detectados; `1` para entrada sem
arquivos, falhas de leitura, linhas inválidas ou arquivos sem sessão identificada
(o relatório parcial continua disponível); `2` para erro de argumentos ou raiz
inexistente (mensagem de erro sem repetir o identificador fornecido).
Os testes criam somente históricos sintéticos em diretórios temporários.

## Primeiro especialista e catálogo (Missão 009)

[`developer`](.codex/agents/developer.toml) implementa e mantém software
independentemente da linguagem, preserva contratos e interfaces, investiga
causa-raiz em bugs e valida conforme o risco. Faz mudanças pequenas e objetivas,
sem refatorações oportunistas, respeitando AGENTS.md, sandbox e autorizações.
Configura inicialmente `gpt-6-luna` com esforço `low`; decisões de escalonamento
cabem ao coordenador. Conhecimento específico de linguagens e ecossistemas
poderá vir de skills ou instruções de projeto, sem ser embutido no agente.
Continua restrito ao repositório para avaliação.

### Validação operacional — Missão 010

**Developer: VERIFIED nesta interface, Codex CLI 0.166.0.** A nova sessão expôs
`developer` no catálogo de papéis da ferramenta nativa. Uma única chamada a
`collaboration.spawn_agent` com `agent_type="developer"` selecionou o papel,
sem copiar suas instruções nem sobrescrever modelo/esforço na chamada.
O nome da tarefa não foi usado como evidência.

| Evidência | Resultado |
| --- | --- |
| Definição descoberta | `developer`, descrição e configuração disponíveis no catálogo nativo |
| Papel selecionado | `session_meta.payload.source.subagent.thread_spawn.agent_role = developer` |
| Configuração solicitada pelo arquivo | `model = gpt-6-luna`, `model_reasoning_effort = low` |
| Configuração observada no turno filho | `turn_context.payload.model = gpt-6-luna`, `effort = low` |

A leitura foi limitada aos campos estruturados da sessão criada para este piloto;
registros anteriores a `subagent_history_start_ordinal` foram excluídos como
histórico herdado. Não se publicaram históricos, prompts ou respostas internas.
Esses metadados confirmam papel e configuração do turno; não são telemetria do
backend do provedor nem medição de economia. A Missão 009 havia comprovado somente
a descoberta e uma delegação nativa sem seleção do papel.

Para usar numa sessão nova, abra o Codex neste repositório e peça uma tarefa
pequena delegada ao agente `developer`. O coordenador deve selecionar o papel
explicitamente na ferramenta nativa quando disponível. Os helps de `codex` e
`codex exec` desta versão não oferecem uma opção `--agent`; `--model` e nomes de
tarefa não selecionam o agente. Se a interface não expuser o papel, registre a
limitação e use o fluxo nativo disponível sem atribuir o trabalho ao Developer.

### Piloto de documentação e biblioteca de skills

Foi aplicada a skill local `docs-sync-audit`, após revisão do pacote completo,
licença e procedência. Ela não duplica `open-pr` (publicação) nem `openai-docs`
(documentação dos produtos OpenAI): compara documentação do projeto com seu código.
[Procedência, revisão fixada e limites](.agents/skills/docs-sync-audit/PROVENANCE.md).

O Developer auditou README, ROADMAP e SKILLS_CANDIDATES contra o inspetor, seus
testes e a definição do agente. `--format`, `--root` e códigos de saída estavam
coerentes; nenhuma funcionalidade do inspetor foi alterada. Foram corrigidos o
nome antigo `python-maintainer` no catálogo e os estados de invocação e instalação.
Não foram encontradas outras divergências confirmadas no escopo delimitado.

Verificações executadas:

- `python3 -B .agents/skills/docs-sync-audit/scripts/docs_drift.py --repo . --format json`:
  executado no piloto; alerta sobre CODEX_HOME no README descartado após leitura
  da fonte, pois descreve um teste do Codex, não variável consumida pelo inspetor.
  Após as correções, nova execução terminou sem alertas. O auxiliar fornece
  pistas e não valida a semântica dos argumentos Python.
- `python3 -B -m unittest discover -v`: 19 testes passaram com históricos sintéticos.
- `codex debug prompt-input` em configuração temporária isolada: agente e skill
  descobertos, sem inferência e sem alteração pessoal. Somente metadados do
  catálogo foram extraídos; o conteúdo do prompt não foi publicado.

Não foi feita auditoria de outros projetos, rebuild de documentação ou medição de
custo/tempo. O catálogo [SKILLS_CANDIDATES.md](SKILLS_CANDIDATES.md) distingue o
inventário histórico das adoções; as demais candidatas continuam pendentes.
`open-pr` e `tars-small-pr` foram preservadas.

### Reutilização entre projetos

Conforme a [documentação oficial de subagentes](https://learn.chatgpt.com/docs/agent-configuration/subagents),
o Codex descobre arquivos TOML em `.codex/agents/` do projeto e em
`~/.codex/agents/` para agentes pessoais. O campo `name` identifica o papel.
Disponibilizar o arquivo permite descoberta; comprovar invocação exige seleção
explícita e evidência de execução, como no piloto acima.

Em TARS, Financeiro ou outro repositório, uma futura autorização pode permitir
copiar a definição versionada para `.codex/agents/developer.toml` do projeto.
Para promoção pessoal simples e reversível, pode-se criar um link simbólico em
`~/.codex/agents/developer.toml` apontando para o arquivo versionado numa checkout
estável, após verificar colisões e obter autorização. Remover somente esse link
reverte a disponibilização; mover a checkout quebra o link e atualizá-la muda a
definição compartilhada. Revalidar descoberta e invocação numa nova sessão.

O agente mantém o AGENTS.md e as skills aplicáveis ao projeto de destino e herda
permissões, provedor e servidores MCP do pai. A definição não concede acesso
extra. Evitar definições divergentes com o mesmo nome. Nenhum outro projeto ou
escopo pessoal foi alterado nesta missão; não há instalador ou sincronizador.

## Visão futura

Consulte o [ROADMAP.md](ROADMAP.md) para a visão consolidada e as possibilidades de evolução.

Explorar agentes especializados e reutilizáveis para desenvolvimento, auditoria de segurança, operações de arquivos, criação de mídia e outras tarefas. Cada agente poderá utilizar modelos, skills e ferramentas diferentes, conforme o trabalho e o custo adequado.

Há interesse futuro em observabilidade e controle da orquestração, usando [cauecarneiro/Overview-Painel](https://github.com/cauecarneiro/Overview-Painel) como referência a avaliar. Isso não implica integração ou dependência atual.

Esses componentes são apenas visão futura: sua implementação depende de escopo e autorização próprios.
