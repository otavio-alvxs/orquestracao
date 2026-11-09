# Roteamento por tarefa — v0.16 / Missão 024

## v0.20 — fluxo opt-in Automática · Econômica

Na Central, `workflow=automatic` usa `profile=auto` e o mesmo roteador
determinístico. Tarefas `simple` e `intermediate` seguem em execução única
Luna/low ou Luna/medium; `complex`, ambíguas, desconhecidas ou com sinais de
risco seguem pela orquestração Sol/medium → Luna/medium. A decisão não chama
outro modelo. O perfil continua heurístico, sem garantia semântica de risco.

Uma forma adicional reconhecida como `intermediate` é a criação de exatamente
dois arquivos Python correspondentes (`modulo.py` e `test_modulo.py`), com
retorno literal especificado, verificação `unittest` e pedido para executar só
esse teste. A regra é estrutural, não vinculada a nomes de arquivo. Builds com
comportamento vago, mais arquivos, testes sem relação explícita ou outro runner
permanecem conservadores; esta pequena regra não generaliza para todo par de
implementação e teste.

O modo exige Desenvolvimento e app-server, como a orquestração atual. A Central
expõe fluxo pedido, fluxo efetivo e motivo, além das métricas nativas disponíveis.
Métricas orquestradas permanecem por etapa; o total agregado não é apresentado
porque pode duplicar uso cumulativo entre threads. Direta e Orquestrada continuam
seleções manuais separadas. Draft PR permanece incompatível com orquestração;
cancelamento, aprovações, sandbox e sessão efêmera permanecem nos contratos dos
executores existentes.

No fluxo automático que chega à orquestração, a instrução ao planejador limita o
plano a oito linhas e pede decisões/arquivos, riscos, verificações e incertezas.
Isso reduz o texto explicitamente transferido sem remover a tarefa original ou
adicionar chamada. Não mede economia real de contexto.

Uma avaliação offline pequena cobre tarefa simples, intermediária, ambígua e de
alto risco, além de contratos HTTP de escolha e observabilidade. É sintética:
não valida qualidade do modelo, custo, economia de tokens ou acurácia em produção.

## Capacidade nativa verificada

Inspeção local em 2026-11-06 com Codex CLI **0.167.0**:
`codex app-server generate-json-schema --out <diretório temporário>`,
`codex exec --help` e consulta JSON-RPC `model/list` sem inferência.
Os schemas são gerados pelo binário instalado; não foram copiados ao projeto.

| Operação | Evidência e limite |
| --- | --- |
| Selecionar modelo/esforço | `TurnStartParams.model` e `.effort` substituem os padrões para o turno e os seguintes. Não exigem reinício da thread. |
| Fixar durante a execução | O cliente envia uma escolha por `turn/start`. `TurnSteerParams` só aceita entrada e identificação do turno; não expõe modelo/esforço. Não há troca por etapa implementada. |
| Subagentes próprios | Agentes existentes configuram modelo e esforço. O schema `subAgentActivity` oferece modelo/esforço resolvidos na criação; `collabAgentToolCall` oferece valores solicitados. A Missão 021 já observou Developer Luna/low e Reviewer Terra/medium. Isso não garante liberdade de override em todos os modos de herança ou runtimes. |
| Escalonar | Novo `turn/start` na mesma thread permite uma escolha superior depois de encerrar o turno anterior. Não há repetição automática de comandos ou edições. Não se promete migração segura no meio de uma operação. |
| Sessão e cache | Trocar modelo entre turnos não exige nova conversa no protocolo. Não há garantia de reaproveitamento do cache entre modelos. Cache observado vem exclusivamente das métricas nativas. |
| Nova sessão | Nova tarefa na central cria thread efêmera; `codex exec`/TUI continuam isolados por execução. Encerrar o processo da central perde sua thread efêmera. Filhos têm contextos próprios; herança/cache não são economia garantida. |

O catálogo local anunciou `gpt-6-luna` e `gpt-6.1-sol`, ambos com `low`,
`medium` e `high`, entre outras opções. Catálogo não comprova acesso efetivo;
rejeição de modelo/esforço deve aparecer como falha, sem troca silenciosa.
Mudanças globais de configuração não são usadas para rotear uma thread ativa.

A [documentação oficial do app-server](https://learn.chatgpt.com/docs/app-server)
confirma os overrides por turno, a continuidade de threads e as limitações de
`turn/steer`. A implementação se limita ao schema instalado. Troca de modelo
por chamada interna, migração de uma inferência em andamento e benefícios de
cache entre modelos permanecem **não confirmados**, sem simulação.

## Política implementada

A seleção inicial usa regras locais limitadas e perfis explícitos. Não há
inferência para classificar tarefas, coordenação obrigatória pelo Sol nem
scheduler de especialistas. O prompt original segue como entrada de usuário,
sem prefixos ou reescrita. As regras do projeto, configurações dos especialistas,
sandbox e aprovações continuam sob controle do Codex.

| Perfil | Seleção solicitada |
| --- | --- |
| `simple` | `gpt-6-luna`, `low` |
| `intermediate` | `gpt-6-luna`, `medium` |
| `complex` | `gpt-6.1-sol`, `medium` |

`auto` reconhece formas limitadas em português e inglês, inclusive com
cortesia ou contexto antes da intenção. Usa Luna/low para leitura, edição textual
e conteúdo literal reconhecidos; Luna/medium para implementação local com
comportamento especificado. Indicadores de risco têm precedência. Pedidos
ambíguos, compostos, longos ou desconhecidos ficam em Sol/medium. A avaliação
manual, suas limitações e a validação estão em [ROUTING_QUALITY.md](ROUTING_QUALITY.md).
O código exato está em `task_routing.py`; não é um classificador semântico.
Perfis explícitos substituem essa heurística. A escolha é enviada por campos
nativos `model`/`effort`; no fallback exec/TUI, por `--model` e
`model_reasoning_effort`. Não há turno de coordenação prévio no Sol.

Na API, `/api/run` aceita `profile` (`auto` por padrão). A continuação em
`/api/message` aceita `escalate: true` e `escalation_reason`: a ação do usuário
registra falha ou insuficiência informada e eleva a rota no próximo turno.
Luna sobe para Sol/medium; Sol/medium sobe para high, teto desta política.
Novo pedido de elevação no teto é rejeitado; turnos cancelados não permitem
escalonamento. O usuário precisa fornecer a próxima mensagem, que não é reescrita nem substituída pelo motivo registrado.
A central não verifica automaticamente se uma resposta está correta.

Operações determinísticas da central continuam em Python/JavaScript. Para
trabalho de projeto, a escolha de ferramentas e eventual delegação pertence ao
modelo e às instruções locais: ferramentas convencionais quando suficientes,
Developer para implementação delimitada com benefício concreto e Reviewer
conforme risco. A central não interpreta comandos livres para executá-los fora
do sandbox, nem cria agentes para tarefas simples.

A política de seleção se aplica a qualquer diretório autorizado, sem editar seu
AGENTS.md ou configurações. As regras são heurísticas transparentes, não uma
comprovação de complexidade; o usuário pode escolher um perfil explicitamente.
Não existe detecção semântica automática de qualidade ou de insuficiência.

## Observabilidade

O estado da execução e a interface existente mostram a seleção solicitada,
esforço e motivo. Um pedido aceito ou modelo configurado não comprova o modelo
que realizou a inferência. Campos sem evidência permanecem indisponíveis.
Eventos de filhos resolvidos sustentam observação de criação, não atribuição
de todas as chamadas de modelo feitas pelo especialista.

Tokens de entrada, cache e saída vêm de eventos nativos com thread e turno
confirmados. O total acumulado é comparado com a base do início do turno.
Lacunas, regressões ou dados inválidos deixam o consumo indisponível quando
não há base confiável para atribuição. Cache é parte da entrada, não consumo adicional. Duração é medida localmente; resultado é o
estado terminal informado pelo Codex. Sem evento válido, métricas são nulas.
Não se estimam custos, percentuais de economia ou consumo dos filhos a partir
da configuração. A central mantém os dados somente na memória; não adiciona
histórico, dashboard ou leitura de rollouts privados.

## Validação da Missão 022

Um único piloto real usou um projeto temporário com `nota.txt`, pela API HTTP
local da central, em Leitura e perfil `auto`. O primeiro turno recebeu
`Resuma o arquivo nota.txt em uma frase.` (com espaços e quebra de linha
preservados); o segundo pediu `Traduza sua frase anterior para inglês.`.
Não houve instrução para usar Developer ou Reviewer.

| Turno | Seleção solicitada | Estado | Duração | Entrada | Cache de entrada | Saída |
| --- | --- | --- | --- | --- | --- | --- |
| Resumo do arquivo | Luna/low | completed | 5,12 s | 30.932 | 15.232 | 63 |
| Tradução da resposta | Luna/low | completed | 1,37 s | 16.632 | 15.360 | 24 |

As duas respostas corresponderam ao conteúdo sintético. A thread permaneceu
igual e os prompts permaneceram íntegros em ambos os turnos. Nenhum subagente
foi observado; nenhum escalonamento ocorreu. O modelo/esforço de inferência
observado permaneceu indisponível: sucesso com uma seleção solicitada não foi
rebatizado como telemetria de modelo. Os totais são deltas dos eventos nativos,
sem estimativa de custos ou economia. O piloto não comparou modelos nem testou
troca real de modelo, falha real ou acesso remoto. Não houve inspeção visual de
navegador. Não foram publicados identificadores de sessão nem caminhos locais.

O Reviewer fez uma única revisão independente, sem editar arquivos ou repetir
o piloto. Encontrou falhas de atribuição/validação das métricas, elevação sem
mudança no teto, controles de escalonamento/perfil e remoção de espaços na TUI.
Também identificou que casos desconhecidos precisavam de rota conservadora.
As correções e seus testes foram realizados pelo Developer; o coordenador
verificou o diff final. Não houve segunda rodada de revisão independente.

Verificações finais: `.venv/bin/python -B -m unittest discover -q` passou com
**68 testes**, incluindo seleção por perfil, parâmetros RPC, continuidade,
prompt íntegro, escalonamento e suas recusas, tokens acumulados/regressivos e
base desconhecida, além dos controles existentes de permissões e acesso remoto.
`git diff --check` passou. `node --check web/app.js` não pôde ser executado porque
Node não está instalado; a alteração JavaScript foi inspecionada no diff.
