# Projetos e manutenção LIVE — v0.18

`--projects-root "$HOME/repos"` descobre Git nas subpastas imediatas, por nome,
novamente a cada carregamento. Symlinks são ignorados. `--project` continua
compatível. Publicação de Draft PR permanece exclusiva do checkout DEV autorizado.

## Instalação após merge

Nada é instalado automaticamente. O auxiliar exige worktree LIVE dedicado em
`$HOME/.local/share/orquestracao/live`, sem symlink. Se a LIVE atual usa outro
caminho, planeje a migração antes de habilitar manutenção. Não execute estes
passos durante desenvolvimento.

Na main integrada do DEV, crie o worktree **somente se o destino não existir**:

```sh
mkdir -p "$HOME/.local/share/orquestracao"
git fetch https://github.com/otavio-alvxs/orquestracao.git main
git worktree add --detach "$HOME/.local/share/orquestracao/live" FETCH_HEAD
```

Instale uma cópia estável fora do checkout atualizado. O comando recusa
sobrescrita; se já existir, pare e compare antes de planejar sua substituição:

```sh
mkdir -p "$HOME/.local/lib/orquestracao"
(set -C; cat maintenance.py > "$HOME/.local/lib/orquestracao/maintenance.py")
/usr/bin/python "$HOME/.local/lib/orquestracao/maintenance.py" status
systemctl --user cat orquestracao.service
```

Crie um novo drop-in sem sobrescrever os existentes. Primeiro copie a linha
ExecStart efetiva mostrada acima e preserve seus argumentos Tailscale, allowlist,
porta e ambiente Python. Altere o script para o worktree LIVE e acrescente
`--projects-root %h/repos`. Exemplo apenas para instalação local com Python do
sistema (o health check do auxiliar requer porta 8765):

```sh
mkdir -p "$HOME/.config/systemd/user/orquestracao.service.d"
(set -C; cat > "$HOME/.config/systemd/user/orquestracao.service.d/90-v018-projects.conf" <<'UNIT'
[Service]
WorkingDirectory=%h/.local/share/orquestracao/live
ExecStart=
ExecStart=/usr/bin/python %h/.local/share/orquestracao/live/web_operations.py --projects-root %h/repos --port 8765
UNIT
)
```

Antes de aplicar, edite **esse novo arquivo** para recolocar os argumentos
remotos atuais. Não aplique o exemplo sem eles se Tailscale estiver habilitado.
Revise com `systemctl --user cat orquestracao.service`. Somente quando a migração
estiver autorizada:

```sh
systemctl --user daemon-reload
systemctl --user restart orquestracao.service
```

Sem instalação, a Central segue funcional com manutenção indisponível.
Agendamento não confirma conclusão. Sessões em memória são perdidas. A consulta
usa cache de 120 segundos; falha de rede não significa atualizado. A atualização
exige árvore limpa e ancestralidade confirmada no remoto fixo. O checkout
detached preserva branches compartilhadas com DEV. Não há reset/clean/merge.

## Terminal e recuperação

```sh
/usr/bin/python "$HOME/.local/lib/orquestracao/maintenance.py" status
/usr/bin/python "$HOME/.local/lib/orquestracao/maintenance.py" restart
/usr/bin/python "$HOME/.local/lib/orquestracao/maintenance.py" update
```

A unidade transiente fixa `orquestracao-maintenance` aguarda três segundos e usa
lock. Resultado em `~/.local/state/orquestracao/result.json`, exibido também no
painel: `verified` somente após systemd e HTTP. Consulte
`journalctl --user -u orquestracao-maintenance.service` em caso de falha.
Se uma unidade falha permanecer, confira o resultado antes de usar
`systemctl --user reset-failed orquestracao-maintenance.service`.

Rollback manual: encerre tarefas, confira árvore limpa e substitua SHA_ANTERIOR
pelo campo `previous` registrado no resultado. Se houver alterações, preserve-as
e investigue antes de continuar:

```sh
git -C "$HOME/.local/share/orquestracao/live" status --porcelain
git -C "$HOME/.local/share/orquestracao/live" checkout --detach SHA_ANTERIOR
systemctl --user restart orquestracao.service
systemctl --user is-active orquestracao.service
```

A autenticação Git do usuário é reutilizada. Tokens e stderr bruto não são
expostos. Revise a cópia instalada quando versões futuras mudarem o protocolo.

Recarregar com F5 recupera a sessão do backend, incluindo aprovações e publicação
autorizada, e reproduz os últimos 500 eventos SSE disponíveis sem repetir IDs.
Isso requer que o processo da Central permaneça vivo; não há persistência de
sessões após reinicialização do backend. Manutenção é recusada durante execução,
aprovações pendentes, início de tarefa ou publicação.
