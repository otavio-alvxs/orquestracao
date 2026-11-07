# Roteamento v0.16 — Missão 024

## Referência e diagnóstico

A base é `b77248c` (main após PR #16). Os 32 exemplos de
`routing_reference.json` foram escritos e classificados manualmente antes da
implementação, sem inferência ou históricos privados. São solicitações
sintéticas semelhantes às operações documentadas no projeto, em português e
inglês. Cada linha preserva prompt, seleção anterior, seleção desejada e motivo.
`simple` = Luna/low; `intermediate` = Luna/medium; `complex` = Sol/medium.

A política anterior acertou 17/32: sete subestimações e oito seleções acima do
nível desejado. Subestimação significa nível abaixo da referência, incluindo
ambiguidade; seleção conservadora significa nível acima dela. Esses números
medem somente este conjunto pequeno, não qualidade ou economia em produção.

Problemas concretos: cortesia, contexto antes do verbo e inglês caíam no Sol;
conteúdo literal de arquivo recebia medium; presença de “função” ou “arquivo”
permitia medium sem comportamento definido; riscos de autenticação/permissões
e operações adicionais não tinham precedência suficiente. O conjunto inclui
leitura, texto, arquivos, implementação, bugs, investigação, revisão, segurança,
arquitetura, refatoração, ambiguidade e múltiplas operações.

## Escalonamento e integração

Não foi adicionado escalonamento automático. `app_server.py:_rpc` converte
rejeições RPC em erro genérico; falhas de turno podem ocorrer após efeitos.
`codex_runner.py:parse_event` também não estabelece rejeição inequívoca de
modelo/esforço antes de ferramentas. A evidência disponível não demonstra uma
condição segura para repetir. Uma resposta textual insuficiente não dispara
nada. O escalonamento explícito continua exigindo motivo e próxima mensagem.

O ajuste limita-se à seleção determinística e testes. Campos nativos,
continuidade, especialistas, métricas, aprovações, modos, TUI, fallback exec e
acesso remoto conservam suas implementações. As regressões existentes cobrem
seus contratos; não foi repetido piloto de capacidades anteriores.

## Piloto único

Foi tentada uma execução pela classe real `AppServerRun`, modo Desenvolvimento,
em diretório temporário isolado. Prompt sem instrução de modelo/agente:
“Please implement a function is_even in numbers.py returning whether an integer
is even.” A seleção solicitada mudou de Sol/medium para Luna/medium.

A tentativa terminou `failed` em 0,13 s, com a mensagem genérica “Não foi
possível iniciar ou acompanhar o Codex app-server.” Não há diagnóstico suficiente
para atribuir a causa. Nenhum arquivo foi criado, nenhuma resposta final ou
atividade de filho foi observada; tokens e modelo/esforço de inferência ficaram
indisponíveis. A continuação preparada não foi enviada após a falha: continuidade
e conclusão reais não foram validadas. Não houve repetição nem escalonamento.
Não há evidência para afirmar economia financeira ou sucesso prático.

## Revisão e limites

Uma única revisão independente encontrou subestimações fora da referência:
conteúdo de arquivo com lógica confundido com literal, segunda operação ignorada,
partículas “que/para” confundidas com comportamento e lacunas de risco em inglês,
autorização e criptografia. Os achados foram corrigidos com testes negativos, sem segunda rodada independente.
A referência original permaneceu intacta; os casos de regressão ficam separados.

A política continua sendo reconhecimento de formas, sem compreensão semântica.
Novas formulações podem exigir perfil explícito ou evolução baseada em evidência.
Não foi criado classificador por IA, novo runtime ou chamada extra de inferência.
O próximo passo prático é diagnosticar a conexão do app-server em escopo próprio
antes de uma nova validação real autorizada; esta missão conserva uma só tentativa.

## Comparação e verificações finais

| Medida na referência (32 casos) | Antes | Depois |
| --- | ---: | ---: |
| Acertos | 17 | 32 |
| Subestimações | 7 | 0 |
| Seleções conservadoras desnecessárias | 8 | 0 |

Perfis explícitos conservam seus pares model/effort mesmo diante de sinais de
risco. Estes números não estimam desempenho em produção. Regressões adicionais
exercitam casos encontrados na revisão, fora da referência congelada.

| Exemplo | Antes | Depois |
| --- | --- | --- |
| Por favor, resuma o README em três frases. | complex | simple |
| Summarize README.md in three sentences. | complex | simple |
| Crie o arquivo nota.txt com o texto Olá. | intermediate | simple |
| Please implement a function is_even in numbers.py returning whether an integer is even. | complex | intermediate |
| No arquivo utils.py, implemente uma função para somar dois números. | complex | intermediate |
| Corrija o bug no arquivo app.py. | intermediate | complex |
| Crie uma função para validar permissões de administrador. | intermediate | complex |
| Implement a function to authenticate users. | complex | complex |
| Adicione uma função. | intermediate | complex |
| Liste os arquivos e publique uma release. | simple | complex |

Validação: `.venv/bin/python -B -m unittest discover -q` passou com **72 testes**.
A primeira execução restrita foi impedida em dez testes HTTP por proibição de
sockets; a execução com permissão para sockets locais passou. Após restaurar
explicitamente os sinais anteriores de complexidade e múltiplas fontes, os seis
testes do seletor passaram novamente. `git diff --check` passou. Não houve mudança
na interface nem em JavaScript, configurações globais ou regras de outros projetos.
