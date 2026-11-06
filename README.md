# Orquestração

Projeto para organizar o uso incremental de agentes, modelos, skills e ferramentas, priorizando funcionalidade, velocidade e economia de recursos.

## Estado atual confirmado

Além da documentação inicial, a Missão 005 adiciona um inspetor mínimo de históricos do Codex. Não há agentes personalizados, serviços ou infraestrutura implementados.

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
python3 -B -m unittest -v test_inspect_sessions.py
```

A saída JSON contém apenas identificadores, papéis, modelos, esforços, evidências
das relações e contadores de diagnóstico. Não inclui caminhos, prompts, respostas
ou conteúdo bruto de ferramentas. `observed_settings` vem de `turn_context`;
`requested_settings` vem dos argumentos de `spawn_agent` e não comprova o modelo
usado. `null` e listas vazias indicam ausência de dados reconhecidos; não se infere
herança de modelo ou esforço. Mudanças de configuração são preservadas como
combinações distintas, sem contar turnos ou chamadas.

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
(o relatório parcial continua disponível); `2` para erro de argumentos.
Os testes criam somente históricos sintéticos em diretórios temporários.

## Visão futura

Consulte o [ROADMAP.md](ROADMAP.md) para a visão consolidada e as possibilidades de evolução.

Explorar agentes especializados e reutilizáveis para desenvolvimento, auditoria de segurança, operações de arquivos, criação de mídia e outras tarefas. Cada agente poderá utilizar modelos, skills e ferramentas diferentes, conforme o trabalho e o custo adequado.

Há interesse futuro em observabilidade e controle da orquestração, usando [cauecarneiro/Overview-Painel](https://github.com/cauecarneiro/Overview-Painel) como referência a avaliar. Isso não implica integração ou dependência atual.

Esses componentes são apenas visão futura: sua implementação depende de escopo e autorização próprios.
