import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

import maintenance as m
from web_operations import OperationsServer


class ProjectsTests(unittest.TestCase):
    def test_discovery_refresh_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ordinary').mkdir()
            (root / 'file').touch()
            for name in ['zeta', 'Alpha']:
                subprocess.run(['git', 'init', '-q', str(root / name)], check=True)
            (root / 'link').symlink_to(root / 'zeta', target_is_directory=True)
            app = OperationsServer([], projects_root=root)
            self.assertEqual([p.name for p in app.project_catalog().values()], ['Alpha', 'zeta'])
            subprocess.run(['git', 'init', '-q', str(root / 'beta')], check=True)
            self.assertEqual([p.name for p in app.project_catalog().values()], ['Alpha', 'beta', 'zeta'])
            self.assertNotIn('../zeta', app.project_catalog())
            self.assertTrue(all('/' not in key for key in app.project_catalog()))

    def test_busy(self):
        app = OperationsServer([Path.cwd()])
        self.assertFalse(app.maintenance_busy())
        app.session.run = Mock(pending_approvals={'a': {}})
        self.assertTrue(app.maintenance_busy())
        app.session.run = None
        app.session.task = Mock(done=lambda: False)
        self.assertTrue(app.maintenance_busy())
        app.session.task = None
        asyncio.run(app.session.publish_lock.acquire())
        self.assertTrue(app.maintenance_busy())


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        m._cache = None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = patch.object(m, 'STATE', Path(self.tmp.name))
        self.state.start()
        self.addCleanup(self.state.stop)

    @patch.object(m, 'installed', return_value=True)
    @patch.object(m, 'command', return_value='active')
    def test_versions_and_network(self, command, installed):
        with patch.object(m, 'git', side_effect=['a', 'b refs/heads/main', '', 'b', '']):
            self.assertEqual(m.status()['version'], 'atualização disponível')
        m._cache = None
        with patch.object(m, 'git', side_effect=['a', 'a refs/heads/main']):
            self.assertEqual(m.status()['version'], 'atualizado')
        m._cache = None
        with patch.object(m, 'git', side_effect=['a', 'b refs/heads/main', '', 'b', subprocess.CalledProcessError(1, 'git')]):
            self.assertEqual(m.status()['version'], 'divergente ou desconhecido')
        m._cache = None
        with patch.object(m, 'git', side_effect=OSError):
            self.assertEqual(m.status()['version'], 'indisponível')

    @patch.object(m, 'installed', return_value=True)
    def test_schedule_and_systemd_failure(self, installed):
        with patch.object(m, 'command', return_value='') as cmd:
            self.assertTrue(m.schedule('restart')['accepted'])
            self.assertIn('--unit=orquestracao-maintenance', cmd.call_args.args)
        with patch.object(m, 'command', side_effect=OSError):
            self.assertFalse(m.schedule('restart')['accepted'])
        self.assertFalse(m.schedule('arbitrary')['accepted'])

    @patch.object(m, 'installed', return_value=True)
    def test_git_failure_preserves_worktree(self, installed):
        with patch.object(m, 'git', side_effect=['', 'previous', subprocess.CalledProcessError(1, 'git')]) as git, patch.object(m, 'command') as command:
            m.worker('update')
            self.assertFalse(command.called)
            self.assertFalse(any(call.args[0] == 'checkout' for call in git.call_args_list))
        result = json.loads((Path(self.tmp.name) / 'result.json').read_text())
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['previous'], 'previous')

    @patch.object(m, 'installed', return_value=True)
    def test_restart_verification(self, installed):
        with patch.object(m, 'command', return_value='active'), patch.object(m.time, 'sleep'), patch('urllib.request.urlopen') as url:
            url.return_value.__enter__.return_value.status = 200
            m.worker('restart')
        self.assertEqual(json.loads((Path(self.tmp.name) / 'result.json').read_text())['state'], 'verified')

    def test_missing_install(self):
        with patch.object(m, 'installed', return_value=False):
            self.assertFalse(m.status()['available'])
            self.assertFalse(m.schedule('restart')['accepted'])

class ProjectAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_opaque_selection_and_publish_restriction(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as directory:
            app = OperationsServer([Path(directory)])
            key = next(iter(app.project_catalog()))
            app._response = AsyncMock()
            await app._start_run(dict(prompt='x', project=key, mode='development', publish_draft_pr=True), None)
            self.assertEqual(app._response.call_args.args[1], 403)
            with patch('web_operations.CodexRun', side_effect=ValueError('test stop')) as runner:
                await app._start_run(dict(prompt='x', project=key), None)
                self.assertEqual(runner.call_args.args[0], directory)
            await app._start_run(dict(prompt='x', project='../outside'), None)
            self.assertEqual(app._response.call_args.args[1], 403)
            await app.close()

class MaintenanceHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, *, csrf=True, origin='http://127.0.0.1:8765', extra=None):
        from unittest.mock import AsyncMock
        body = json.dumps(dict(action='restart', confirm=True)).encode()
        headers = {'Host': '127.0.0.1:8765', 'Origin': origin, 'Content-Type': 'application/json', 'Content-Length': str(len(body))}
        if csrf:
            headers['X-CSRF-Token'] = app.session.token
        headers.update(extra or {})
        reader = asyncio.StreamReader()
        reader.feed_data(('POST /api/maintenance HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers.items()) + '\r\n').encode() + body)
        reader.feed_eof()
        writer = Mock()
        writer.get_extra_info.return_value = ('127.0.0.1', 1234)
        writer.is_closing.return_value = False
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        await app._client(reader, writer)
        return int(writer.write.call_args.args[0].split()[1])

    async def test_auth_busy_and_accept(self):
        app = OperationsServer([Path.cwd()])
        async def inline(function, *args):
            return function(*args)
        with patch('web_operations.asyncio.to_thread', side_effect=inline), patch.object(m, 'schedule', return_value=dict(accepted=True)) as schedule:
            self.assertEqual(await self.request(app, csrf=False), 403)
            self.assertEqual(await self.request(app, origin='http://evil.test'), 403)
            self.assertEqual(await self.request(app, extra={'Tailscale-User-Login': 'spoof'}), 403)
            self.assertFalse(schedule.called)
            app.session.task = Mock(done=lambda: False)
            self.assertEqual(await self.request(app), 409)
            app.session.task = None
            self.assertEqual(await self.request(app), 202)
            self.assertEqual(await self.request(app), 409)
            schedule.assert_called_once_with('restart')
