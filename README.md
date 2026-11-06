# Orquestração

Projeto para organizar o uso incremental de agentes, modelos, skills e ferramentas, priorizando funcionalidade, velocidade e economia de recursos.

## Estado atual confirmado

O inspetor de históricos do Codex oferece saída JSON e visualização em árvore de
sessões, com seleção de raiz. Na v0.4 (Missão 010), o especialista
[`developer`](.codex/agents/developer.toml) teve invocação personalizada validada
em uma auditoria documental com a primeira skill externa instalada localmente,
[`docs-sync-audit`](.agents/skills/docs-sync-audit/SKILL.md). Na v0.5 (Missão 011D),
a reutilização pessoal do Developer no TARS foi **VERIFIED**, com arquivo TOML
regular e papel/modelo/esforço confirmados nos metadados próprios do filho.

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
A promoção pessoal e a reutilização no TARS foram comprovadas no fechamento da
Missão 011D; os bloqueios das tentativas anteriores estão preservados abaixo.

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

A fonte versionada é `.codex/agents/developer.toml` desta checkout. A instalação
pessoal usa uma cópia regular em `~/.codex/agents/developer.toml`, com integridade
verificada e atualização explícita. Não copiar a definição para outros projetos
nem alterar `~/.codex/config.toml`. O agente respeita o AGENTS.md e as skills
aplicáveis ao destino; a instalação não concede permissões extras.

### Developer compartilhado — Missão 011 (v0.5)

**Promoção pessoal: VERIFIED. Descoberta pessoal no TARS: VERIFIED. Delegação
no TARS: VERIFIED (Missão 011D).** Resultado final comprovado informado para este
fechamento documental:

| Evidência final | Resultado |
| --- | --- |
| Instalação pessoal | O symlink foi substituído por arquivo TOML regular em `~/.codex/agents/developer.toml`, correspondente à definição versionada do Orquestração |
| Origem no TARS | Sem definição local homônima; seleção explícita de `agent_type="developer"` funcionou |
| Papel observado | `agent_role=developer` nos metadados estruturados da sessão filha |
| Modelo e esforço observados | `gpt-6-luna` e `low` nos metadados estruturados próprios do filho, excluído o histórico herdado |
| Tarefa concluída | Developer analisou `automation reminder` somente em leitura, cobrindo validação, persistência, runtime e testes |
| Preservação do destino | Nenhum arquivo ou estado operacional do TARS alterado |

A comprovação vale para o contexto validado, sem afirmar disponibilidade em todas
as interfaces. Históricos privados, prompts, respostas internas e identificadores
de sessão não são publicados. Este fechamento apenas documenta o resultado;
não repete o piloto nem altera configurações pessoais.

#### Histórico das tentativas e incidentes resolvidos

Os estados **BLOCKED** abaixo descrevem a tentativa inicial, superada pelo
resultado da Missão 011D; não são o estado atual.

Preparação: árvore local limpa, [PR #4](https://github.com/otavio-alvxs/orquestracao/pull/4)
confirmada integrada em `ff5380f4e3433d6302e03da1933c46cf9e18dc8e`, `main`
atualizada por fast-forward e branch `feat/v0.5-shared-developer` criada.

| Verificação | Resultado observado |
| --- | --- |
| Fonte | Caminho absoluto resolvido, TOML válido e bytes idênticos à definição versionada no HEAD |
| Integridade SHA-256 | `4420d5755db88a80fe4897ef44884f1b98a3a6c635a9cc459b663aef783aef10` |
| Configuração da fonte | `name=developer`, `model=gpt-6-luna`, `model_reasoning_effort=low` |
| Destino pessoal | Diretório `~/.codex` regular, sem override de CODEX_HOME; `agents/` e o link inexistentes antes e depois da tentativa |
| Diretório esperado | Documentação oficial indica `~/.codex/agents/`; CLI instalada: 0.166.0. A descoberta nesta instalação não pôde ser validada |
| Tentativa de promoção | Criação normal do diretório pessoal recusada com `Read-only file system`; nenhum link criado, sem elevação ou sobrescrita |
| Contexto novo no TARS | `codex debug prompt-input` executado a partir do destino, sem inferência; exit 1, filesystem somente de leitura e catálogo indisponível |
| Conflito local no TARS | `.codex/` inexistente; nenhuma definição local do Developer |
| Papel/modelo/esforço do piloto | Não observados: nenhum filho criado no TARS e nenhum histórico privado lido |

Na tentativa inicial, o catálogo do Orquestração comprovava apenas a
disponibilidade local da Missão 010. Luna/low na tabela histórica são valores da
fonte, sem observação de filho naquela tentativa. Não se copiou a definição nem
se criou configuração temporária para simular promoção nessa etapa.

Na continuação, o link simbólico mostrou-se incompatível com o carregamento do
TOML pelo **Codex 0.166.0** neste ambiente. A substituição por arquivo regular
resolveu o carregamento e permitiu a validação final. Não recomendar symlink como
método de instalação para essa versão; não extrapolar o incidente a outras versões.
O erro de expansão de glob do Zsh também foi **resolvido**, sem mudanças globais
no shell. Os procedimentos abaixo usam caminhos explícitos, sem glob.

#### Análise de leitura no TARS

No piloto final, o **Developer** concluiu a análise somente de leitura de
`automation reminder`, cobrindo validação, persistência, runtime e testes, sem
alterar arquivos ou estado operacional do TARS.

Para preservar a procedência, o levantamento detalhado abaixo pertence à análise
inicial realizada diretamente pelo coordenador, **sem atribuí-la ao Developer**,
na checkout `8ca19bf8368df99d99b782f9d1aab2cfd5fa51a2`. Foram lidos AGENTS.md,
estado/plano vigente, contrato de notificações, README e código/testes pertinentes.
Nenhum arquivo do TARS foi modificado; nenhum comando operacional, teste, serviço,
notificação, lembrete ou banco real foi executado/acessado.
As referências abaixo são relativas ao repositório
[`otavio-alvxs/TARS`](https://github.com/otavio-alvxs/TARS/tree/8ca19bf8368df99d99b782f9d1aab2cfd5fa51a2).

- Entrada: `src/tars/main.py:71` (`main`) encaminha `automation` para
  `src/tars/automation_command.py:37` (`processar_comando_automation`), que
  seleciona `_agendar_lembrete` (`:131`). Exige exatamente data/hora e mensagem.
- Validação: `_parse_scheduled_for` (`automation_command.py:149`) exige ISO 8601
  com timezone explícito. `src/tars/local_notification_tool.py:12`
  (`validate_local_notification_message`) rejeita texto vazio, categorias Unicode
  Cc/Cf/Cs e mais de 1024 bytes UTF-8, preservando o texto literal.
  A validação precede criação de workspace e abertura do store.
- Persistência: `_agendar_lembrete` cria `Automation` inicialmente `SCHEDULED`
  com `ToolCall("notify_local", {"message": texto})`; chama
  `src/tars/sqlite_automation_store.py:27` (`save`). Não autoriza nem executa a
  Tool ou inicia runtime. A confirmação informa ID e horário, sem a mensagem.
- Execução posterior: `automation run --enable-local-notifications` compõe
  `src/tars/builtin_automation_runtime.py` com registro de `notify_local`,
  allowlist e política auditada. `automation_runtime.py` faz recovery e polling;
  `automation_processor.py` reivindica uma intenção vencida e
  `automation_runner.py` passa pelo executor autorizado e grava o estado terminal.
- Testes relevantes, lidos e não executados: `tests/test_automation_reminder.py`
  cobre literalidade, validação sem efeitos, persistência e erro sem retry;
  `tests/test_automation_local_notifications.py` cobre opt-in/negação/recovery;
  `tests/test_local_reminder_acceptance.py` cobre atraso, restart e cancelamento.
  `tests/test_sqlite_automation_store.py` e `tests/test_automation_recovery.py`
  cobrem store e recuperação.
- Pontos de atenção do contrato existente: sem opt-in, a intenção pode falhar por
  negação e não é reexecutada ao habilitar depois. Efeito externo e checkpoint
  não são atômicos; recovery não reenvia notificações de resultado desconhecido.
  O texto fica no SQLite e pode aparecer no histórico do shell/processos.
  Não foi identificado defeito novo confirmado neste escopo de leitura.

#### Escopo das skills

| Skill | Escopo encontrado | Conflitos e uso futuro |
| --- | --- | --- |
| `docs-sync-audit` | Local ao Orquestração, `.agents/skills/docs-sync-audit/` | Sem homônimo pessoal/no TARS; candidata pessoal se auditorias em outros projetos demonstrarem uso recorrente |
| `open-pr` | Pessoal, `~/.agents/skills/open-pr/` | Sem homônimo local; reutilizada na publicação desta missão, manter escopo pessoal |
| `tars-small-pr` | Local ao TARS, `.agents/skills/tars-small-pr/` | Específica dos contratos do TARS; manter local. Sobrepõe etapas Git/PR de `open-pr`, sem colisão de nome; não se aplica ao piloto somente de leitura |

Nenhuma skill instalada, promovida ou copiada. `docs-sync-audit` não foi aplicada
no TARS; a inspeção das skills limitou-se a escopo e instruções existentes.

#### Instalação de arquivo regular, atualização e reversão segura

O procedimento abaixo é uma orientação para uma instalação futura, **não uma
etapa a repetir no destino já validado**. Executar somente com autorização para
alterar a configuração pessoal, a partir da raiz desta checkout, após revisar a
definição e o commit. Recusa fonte não regular, diretórios pessoais simbólicos e
qualquer destino existente, inclusive symlink pendente. A criação exclusiva (`xb`)
impede sobrescrita. Não converte nem remove links antigos automaticamente.

```sh
python3 - <<'PYINSTALL'
import hashlib
from pathlib import Path
import subprocess

repo = Path.cwd()
source = repo / ".codex/agents/developer.toml"
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
expected = subprocess.check_output([
    "git", "show", f"{revision}:.codex/agents/developer.toml"
])
assert source.is_file() and not source.is_symlink(), "Fonte não regular"
assert source.read_bytes() == expected, "Fonte difere do commit revisado"
base = Path.home() / ".codex"
directory = base / "agents"
for path in (base, directory):
    assert not path.is_symlink(), "Diretório simbólico recusado"
    path.mkdir(exist_ok=True)
    assert path.is_dir(), "Diretório inválido"
target = directory / "developer.toml"
with target.open("xb") as output:
    output.write(expected)
assert not target.is_symlink() and target.read_bytes() == expected
print("Instalação regular verificada; guarde este registro privado:")
print("Origem: otavio-alvxs/orquestracao, .codex/agents/developer.toml")
print("Destino: ~/.codex/agents/developer.toml")
print("Commit:", revision)
print("SHA-256:", hashlib.sha256(expected).hexdigest())
PYINSTALL
```

Guardar o registro de instalação em local privado: origem, destino, commit e
SHA-256. Em caso de erro, parar e inspecionar o resultado; não repetir com
sobrescrita. Depois de uma instalação, abrir nova sessão no projeto consumidor,
confirmar ausência de definição local homônima e selecionar explicitamente
`agent_type="developer"`. Comprovar `agent_role`, modelo e esforço nos metadados
próprios do filho, excluindo histórico herdado; a cópia sozinha não comprova uso.

A cópia não acompanha alterações da checkout. Para atualizar, revisar o novo
commit e a definição, verificar a instalação anterior pelo seu registro privado,
removê-la pelo procedimento abaixo e instalar a versão revisada sem sobrescrita.
Guardar o novo registro e validar em nova sessão. Não atualizar silenciosamente.

Para rollback, **primeiro confirmar pelo registro privado que a cópia foi
instalada por este procedimento**. Igualdade de conteúdo, sozinha, não comprova
procedência. Sem esse registro, não remover. Preencher os dois valores abaixo
com o commit e o SHA-256 da instalação a remover, não com os da versão atual.
Executar na checkout do Orquestração que contém esse commit:

```sh
python3 - <<'PYROLLBACK'
import hashlib
from pathlib import Path
import re
import subprocess

installed_revision = "PREENCHER_COMMIT_DO_REGISTRO"
installed_sha256 = "PREENCHER_SHA256_DO_REGISTRO"
assert re.fullmatch(r"[0-9a-f]{40}", installed_revision), "Commit inválido"
assert re.fullmatch(r"[0-9a-f]{64}", installed_sha256), "SHA-256 inválido"
expected = subprocess.check_output([
    "git", "show", f"{installed_revision}:.codex/agents/developer.toml"
])
assert hashlib.sha256(expected).hexdigest() == installed_sha256
base = Path.home() / ".codex"
directory = base / "agents"
target = directory / "developer.toml"
assert not base.is_symlink() and not directory.is_symlink()
assert target.is_file() and not target.is_symlink(), "Destino não regular"
assert target.read_bytes() == expected, "Conteúdo diferente; nada removido"
target.unlink()
print("Somente a cópia pessoal verificada foi removida.")
PYROLLBACK
```

Executar sem alterações concorrentes no destino. O rollback recusa symlinks e
arquivos divergentes; não usa `readlink` nem o procedimento do antigo link.
Não remover a definição versionada, diretórios ou agentes preexistentes, nem
alterar configuração global para reverter.

Validação da Missão 011D: revisão do diff, `git diff --check`, sintaxe Python
dos exemplos analisada sem execução e SHA-256 histórico conferido com a fonte
versionada.
Os procedimentos acima não foram executados neste fechamento; nenhuma configuração
pessoal ou arquivo do TARS foi acessado ou alterado. Testes funcionais dispensados:
somente README e ROADMAP foram alterados, sem mudanças em código, agente ou skills.

## Visão futura

Consulte o [ROADMAP.md](ROADMAP.md) para a visão consolidada e as possibilidades de evolução.

Explorar agentes especializados e reutilizáveis para desenvolvimento, auditoria de segurança, operações de arquivos, criação de mídia e outras tarefas. Cada agente poderá utilizar modelos, skills e ferramentas diferentes, conforme o trabalho e o custo adequado.

Há interesse futuro em observabilidade e controle da orquestração, usando [cauecarneiro/Overview-Painel](https://github.com/cauecarneiro/Overview-Painel) como referência a avaliar. Isso não implica integração ou dependência atual.

Esses componentes são apenas visão futura: sua implementação depende de escopo e autorização próprios.
