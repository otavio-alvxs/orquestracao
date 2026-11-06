# Orquestração

Projeto para organizar o uso incremental de agentes, modelos, skills e ferramentas, priorizando funcionalidade, velocidade e economia de recursos.

## Estado atual confirmado

O inspetor de históricos do Codex oferece saída JSON e visualização em árvore de
sessões, com seleção de raiz (v0.3, Missão 009). O primeiro especialista é um
agente experimental de desenvolvimento de software independente de linguagem,
definido localmente em `.codex/agents/developer.toml`.

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

O agente é experimental: descoberta de configuração comprovada, execução
específica do papel ainda não comprovada.

A estrutura segue a [documentação oficial de subagentes](https://learn.chatgpt.com/docs/agent-configuration/subagents).
O CLI 0.166.0 reconheceu o nome, a descrição e as configurações do agente via
`codex debug prompt-input`, com `CODEX_HOME` temporário e confiança temporária
neste projeto, sem chamada de inferência nem alteração global.
Na avaliação original da Missão 009, a ferramenta de spawn não tinha seletor de
papel; não foi possível invocar o papel personalizado. A implementação foi delegada pelo mecanismo
nativo a um único filho, com Luna/low solicitados explicitamente e as instruções
do especialista como orientação de tarefa. O retorno não comprovou modelo
executado; nenhum histórico privado foi consultado para tentar comprová-lo.

A delegação produziu código e testes enquanto o coordenador preparava o catálogo.
A revisão pediu simplificações e reforço dos testes. Isso comprova utilidade do
mecanismo nativo; o ganho específico do papel e sua economia de inferência ainda
não foram medidos. Pendente: em uma nova sessão que exponha seleção de papel,
invocar `developer` numa tarefa real e verificar papel/modelo resolvidos.

[`SKILLS_CANDIDATES.md`](SKILLS_CANDIDATES.md) registra o inventário observado,
oito candidatas, fontes, limitações e próximos passos. Nenhuma foi instalada;
`open-pr` e `tars-small-pr` devem ser preservadas.

## Visão futura

Consulte o [ROADMAP.md](ROADMAP.md) para a visão consolidada e as possibilidades de evolução.

Explorar agentes especializados e reutilizáveis para desenvolvimento, auditoria de segurança, operações de arquivos, criação de mídia e outras tarefas. Cada agente poderá utilizar modelos, skills e ferramentas diferentes, conforme o trabalho e o custo adequado.

Há interesse futuro em observabilidade e controle da orquestração, usando [cauecarneiro/Overview-Painel](https://github.com/cauecarneiro/Overview-Painel) como referência a avaliar. Isso não implica integração ou dependência atual.

Esses componentes são apenas visão futura: sua implementação depende de escopo e autorização próprios.
