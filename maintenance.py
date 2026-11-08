"""Fixed LIVE maintenance helper. No browser-supplied paths or commands."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

LIVE = Path.home() / '.local/share/orquestracao/live'
HELPER = Path.home() / '.local/lib/orquestracao/maintenance.py'
STATE = Path.home() / '.local/state/orquestracao'
REMOTE = 'https://github.com/otavio-alvxs/orquestracao.git'
UNIT = 'orquestracao.service'
_cache = None


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=90, check=True).stdout.strip()


def git(*args):
    return command('/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-C', str(LIVE), *args)


def installed():
    return HELPER.is_file() and LIVE.is_dir() and LIVE.resolve() == LIVE


def status():
    global _cache
    if _cache and time.time() - _cache['checked_at'] < 120:
        result = dict(_cache)
        try:
            result['operation'] = json.loads((STATE / 'result.json').read_text())
        except (OSError, ValueError):
            pass
        return result
    result = dict(available=installed(), checked_at=time.time(), version='indisponível', live='desconhecido', deployed=None, remote=None)
    if not result['available']:
        result['message'] = 'Instale o auxiliar conforme MAINTENANCE.md.'
        return result
    try:
        result['live'] = command('/usr/bin/systemctl', '--user', 'is-active', UNIT)
    except (OSError, subprocess.SubprocessError):
        result['live'] = 'inativo ou indisponível'
    try:
        result['deployed'] = git('rev-parse', 'HEAD')
        remote = git('ls-remote', REMOTE, 'refs/heads/main').split()[0]
        result['remote'] = remote
        if remote == result['deployed']:
            result['version'] = 'atualizado'
        else:
            # Fetch only into this LIVE worktree; never use a configurable origin.
            git('fetch', '--no-tags', REMOTE, 'refs/heads/main')
            if git('rev-parse', 'FETCH_HEAD') != remote:
                raise ValueError('Remote changed')
            try:
                git('merge-base', '--is-ancestor', result['deployed'], remote)
                result['version'] = 'atualização disponível'
            except subprocess.CalledProcessError:
                result['version'] = 'divergente ou desconhecido'
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        result['version'] = 'indisponível'
    try:
        result['operation'] = json.loads((STATE / 'result.json').read_text())
    except (OSError, ValueError):
        pass
    _cache = result
    return dict(result)


def lock():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = (STATE / 'lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError('Manutenção em andamento')
    return handle


def schedule(action):
    if action not in ('restart', 'update') or not installed():
        return dict(accepted=False, message='Manutenção indisponível; consulte MAINTENANCE.md.')
    try:
        if action == 'update' and status()['version'] != 'atualização disponível':
            return dict(accepted=False, message='Atualização ainda não confirmada.')
        with lock():
            # A fixed transient unit prevents duplicate requests across web restarts.
            command('/usr/bin/systemd-run', '--user', '--unit=orquestracao-maintenance',
                    '--collect', '--on-active=3s', '/usr/bin/python', str(HELPER), 'worker', action)
        return dict(accepted=True, message='Solicitação agendada; conclusão ainda não verificada.')
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(accepted=False, message='Não foi possível agendar; verifique systemd --user e a unidade de manutenção.')


def worker(action):
    if action not in ('restart', 'update') or not installed():
        raise ValueError('Ação ou instalação inválida')
    with lock():
        result = dict(action=action, state='failed', previous=None, current=None)
        try:
            if action == 'update':
                if git('status', '--porcelain', '--untracked-files=all'):
                    raise ValueError('Worktree sujo')
                result['previous'] = git('rev-parse', 'HEAD')
                git('fetch', '--no-tags', REMOTE, 'refs/heads/main')
                target = git('rev-parse', 'FETCH_HEAD')
                if target == result['previous']:
                    raise ValueError('Já atualizado')
                git('merge-base', '--is-ancestor', result['previous'], target)
                # Detached checkout does not move a shared DEV branch.
                git('checkout', '--detach', target)
                result['current'] = target
            command('/usr/bin/systemctl', '--user', 'restart', UNIT)
            for _ in range(15):
                time.sleep(2)
                try:
                    if command('/usr/bin/systemctl', '--user', 'is-active', UNIT) == 'active':
                        # Also verify HTTP health: active alone can precede startup failure.
                        import urllib.request
                        with urllib.request.urlopen('http://127.0.0.1:8765/', timeout=2) as response:
                            if response.status == 200:
                                result['state'] = 'verified'
                                break
                except (OSError, subprocess.SubprocessError):
                    continue
            if result['state'] != 'verified':
                raise ValueError('Health check failed')
        except (OSError, ValueError, subprocess.SubprocessError):
            result['message'] = 'Falha; consulte o journal. Se houve troca, use o SHA previous para rollback conforme MAINTENANCE.md.'
        result['finished_at'] = time.time()
        temporary = STATE / 'result.tmp'
        temporary.write_text(json.dumps(result))
        temporary.replace(STATE / 'result.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'restart', 'update', 'worker'])
    parser.add_argument('operation', nargs='?', choices=['restart', 'update'])
    args = parser.parse_args()
    if args.action == 'worker':
        worker(args.operation)
    else:
        if args.action == 'status':
            result = status()
        else:
            # Route terminal actions through the same busy/auth/CSRF gate as the UI.
            import re
            import urllib.request
            try:
                base = 'http://127.0.0.1:8765'
                with urllib.request.urlopen(base + '/', timeout=5) as response:
                    page = response.read().decode()
                token = re.search(r'name="csrf-token" content="([A-Za-z0-9_-]+)"', page).group(1)
                request = urllib.request.Request(base + '/api/maintenance',
                    data=json.dumps(dict(action=args.action, confirm=True)).encode(),
                    headers={'Content-Type': 'application/json', 'X-CSRF-Token': token,
                             'Origin': base})
                with urllib.request.urlopen(request, timeout=100) as response:
                    result = json.load(response)
            except (OSError, ValueError, AttributeError):
                result = dict(accepted=False, message='Central indisponível ou operação recusada; consulte a interface.')
        print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
