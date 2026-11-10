import asyncio
import json
import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app_server import AppServerRun
from orchestrated_run import OrchestratedRun
from codex_runner import RunState
from web_operations import OperationsServer, _publish_draft_pr, PublishError

SUCCESS = '''import sys
sys.stdin.read()
print('{"type":"thread.started"}', flush=True)
print('{"type":"item.completed","item":{"type":"agent_message","text":"resposta sintética"}}', flush=True)
print('{"type":"turn.completed","usage":{"input_tokens":21,"output_tokens":8,"cached_input_tokens":2}}', flush=True)
'''


async def http(port, method, path, headers=None, body=b""):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    rows = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if body:
        rows.setdefault("Content-Length", str(len(body)))
    wire = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in rows.items()) + "\r\n"
    writer.write(wire.encode() + body)
    await writer.drain()
    status = int((await reader.readline()).split()[1])
    response_headers = {}
    while line := await reader.readline():
        if line in (b"\r\n", b"\n"):
            break
        key, value = line.decode().split(":", 1)
        response_headers[key.lower()] = value.strip()
    if response_headers.get("connection") == "keep-alive":
        chunks = []
        while True:
            frame = await asyncio.wait_for(reader.readuntil(b"\n\n"), 5)
            chunks.append(frame)
            if (b"event: state\n" in frame and
                    json.loads(frame.split(b"data: ", 1)[1])["status"] in
                    ("completed", "failed", "interrupted")):
                break
        data = b"".join(chunks)
    else:
        data = await reader.readexactly(int(response_headers.get("content-length", "0")))
    writer.close()
    await writer.wait_closed()
    return status, response_headers, data


class WebOperationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        class FakeRateLimitMonitor:
            def __init__(inner, **kwargs):
                inner.snapshot = {"status": "disconnected", "updated_at": None, "buckets": {}, "stale": True}
                inner.on_update = None
            async def start(inner, cwd):
                pass
            async def close(inner):
                pass
        self.monitor_patch = patch("web_operations.RateLimitMonitor", FakeRateLimitMonitor)
        self.monitor_patch.start()

    async def asyncTearDown(self):
        if hasattr(self, "app"):
            await self.app.close()
        self.monitor_patch.stop()
        self.temp.cleanup()

    async def test_reload_session_snapshot_and_cursor(self):
        self.app = OperationsServer([self.root])
        run = AppServerRun(str(self.root), "recuperar", mode="development")
        run.state.status = "running"
        run.state.agents = {"child": {"status": "running"}}
        run.pending_approvals["approval"] = {"id": "approval", "type": "command"}
        self.app.session.run = run
        self.app.session.engine = "app-server"
        self.app.session.publish_authorized = True
        await self.app.session.emit("message", {"role": "user", "text": "recuperar"})
        port = await self.app.start(0)
        auth = {"X-CSRF-Token": self.app.session.token}
        self.assertEqual((await http(port, "GET", "/api/session"))[0], 403)
        first = json.loads((await http(port, "GET", "/api/session", auth))[2])
        second = json.loads((await http(port, "GET", "/api/session", auth))[2])
        self.assertEqual(first["cursor"], second["cursor"])
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(first["project"], next(iter(self.app.project_catalog())))
        self.assertEqual(first["engine"], "app-server")
        self.assertEqual(first["agents"]["child"]["status"], "running")
        self.assertEqual(first["approvals"][0]["id"], "approval")
        self.assertTrue(first["publish_authorized"])
        self.assertEqual(first["prompt"], "recuperar")
        run.state.status = "completed"
        run.pending_approvals.clear()

    async def test_orchestrated_snapshot_recovers_stages_and_events_after_reload(self):
        self.app = OperationsServer([self.root])
        run = OrchestratedRun(str(self.root), "implemente", "planeje")
        run.state.status = "running"
        run.state.agents["planning"]["status"] = "completed"
        run.state.agents["implementation"]["status"] = "running"
        self.app.session.run = run
        self.app.session.engine = "app-server"
        self.app.session.workflow = "orchestrated"
        await self.app.session.emit("event", {"message": "Etapa iniciada: Implementação."})
        snapshot = self.app._snapshot()
        snapshot["events"] = list(self.app.session.events)
        snapshot["cursor"] = self.app.session.next_event
        self.assertEqual(snapshot["workflow"], "orchestrated")
        self.assertEqual(snapshot["agents"]["planning"]["status"], "completed")
        self.assertEqual(snapshot["agents"]["implementation"]["requested_model"], "Luna")
        self.assertEqual(snapshot["events"][0][2]["message"], "Etapa iniciada: Implementação.")
        planning = AppServerRun(str(self.root), "planejar", mode="reading")
        planning.pending_approvals["approval"] = {"id": "approval", "type": "command"}
        run.active_run = planning
        run.pending_approvals = planning.pending_approvals
        self.assertFalse(self.app._snapshot()["approvals"][0]["can_approve"])
        planning.pending_approvals.clear()
        run.active_run = None
        run.state.status = "completed"

    async def test_orchestrated_start_selection_and_draft_pr_block(self):
        self.app = OperationsServer([self.root])
        self.app._response = AsyncMock()
        with patch.object(self.app, "_execute", new=AsyncMock()), patch("web_operations.create_orchestrated_run") as create:
            create.return_value = OrchestratedRun(str(self.root), "tarefa", "planeje")
            await self.app._start_run_locked({"prompt": "tarefa", "project": str(self.root),
                                               "mode": "development", "engine": "app-server",
                                               "workflow": "orchestrated", "publish_draft_pr": True}, None)
            self.assertEqual(self.app._response.call_args.args[1], 400)
            create.assert_not_called()
            await self.app._start_run_locked({"prompt": "tarefa", "project": str(self.root),
                                               "mode": "development", "engine": "app-server",
                                               "workflow": "orchestrated"}, None)
            self.assertEqual(self.app._response.call_args.args[1], 202)
            create.assert_called_once()
            self.assertIn("preferencialmente em até 8 linhas", create.call_args.args[1])
            self.assertIn("Exceda essa orientação", create.call_args.args[1])
            self.assertEqual(create.call_args.args[2], "tarefa")
            self.assertEqual(self.app.session.workflow, "orchestrated")
            self.assertIsInstance(self.app.session.run, OrchestratedRun)
            if self.app.session.task:
                self.app.session.task.cancel()
                await asyncio.gather(self.app.session.task, return_exceptions=True)
            direct = SimpleNamespace(cwd=str(self.root), prompt="tarefa", state=RunState(),
                                     changed_files=set(), thread_id=None)
            with patch("web_operations.CodexRun", return_value=direct) as direct_factory:
                await self.app._start_run_locked({"prompt": "tarefa", "project": str(self.root)}, None)
                self.assertEqual(self.app._response.call_args.args[1], 202)
                direct_factory.assert_called_once()
                self.assertEqual(self.app.session.workflow, "direct")
                self.assertIs(self.app.session.run, direct)
                if self.app.session.task:
                    self.app.session.task.cancel()
                    await asyncio.gather(self.app.session.task, return_exceptions=True)
                self.app.session.run = None

    async def test_automatic_workflow_resolves_route_and_exposes_both_flows(self):
        self.app = OperationsServer([self.root])
        self.app._response = AsyncMock()
        with patch.object(self.app, "_execute", new=AsyncMock()), \
             patch("web_operations.create_orchestrated_run") as orchestrated:
            await self.app._start_run_locked({"prompt": "Resuma este arquivo.", "project": str(self.root),
                                               "mode": "development", "engine": "app-server",
                                               "workflow": "automatic"}, None)
            self.assertIsInstance(self.app.session.run, AppServerRun)
            self.assertEqual(self.app.session.run.profile, "auto")
            self.assertEqual(self.app._snapshot()["requested_workflow"], "automatic")
            self.assertEqual(self.app._snapshot()["effective_workflow"], "direct")
            self.assertIn("tarefa curta e delimitada", self.app._snapshot()["workflow_reason"])
            if self.app.session.task:
                self.app.session.task.cancel()
                await asyncio.gather(self.app.session.task, return_exceptions=True)

            orchestrated.return_value = OrchestratedRun(str(self.root), "tarefa", "plano")
            await self.app._start_run_locked({"prompt": "Adicione uma função para o projeto.",
                                               "project": str(self.root), "mode": "development",
                                               "engine": "app-server", "workflow": "automatic"}, None)
            orchestrated.assert_called_once()
            self.assertIn("preferencialmente em até 8 linhas", orchestrated.call_args.args[1])
            self.assertIn("Exceda essa orientação", orchestrated.call_args.args[1])
            self.assertEqual(self.app._snapshot()["effective_workflow"], "orchestrated")
            self.assertEqual(self.app._snapshot()["workflow"], "automatic")
            if self.app.session.task:
                self.app.session.task.cancel()
                await asyncio.gather(self.app.session.task, return_exceptions=True)

    async def test_start_gate_blocks_second_run_and_maintenance(self):
        self.app = OperationsServer([self.root])
        self.app._response = AsyncMock()
        async with self.app.run_start_lock:
            self.assertTrue(self.app.maintenance_busy())
            await self.app._start_run({"prompt": "second", "project": str(self.root)}, None)
            self.assertEqual(self.app._response.call_args.args[1], 409)
            await self.app._continue_run({"prompt": "second"}, None)
            self.assertEqual(self.app._response.call_args.args[1], 409)
        self.assertIsNone(self.app.session.run)

    def test_publisher_real_git_snapshot_offline(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("Git indisponível")
        def local_git(*args, **kwargs):
            return subprocess.run([git, *args], cwd=self.root, check=True,
                                  capture_output=True, text=True, **kwargs).stdout.strip()
        local_git("init", "--initial-branch=feat/offline")
        local_git("config", "user.name", "Offline Test")
        local_git("config", "user.email", "offline@example.invalid")
        local_git("remote", "add", "origin", "https://github.com/otavio-alvxs/orquestracao.git")
        tracked = self.root / "example.txt"
        tracked.write_text("baseline\n")
        local_git("add", ".")
        local_git("commit", "-m", "baseline")
        baseline = local_git("rev-parse", "HEAD")
        tracked.write_text("updated\n")
        local_git("commit", "-am", "update")
        head = local_git("rev-parse", "HEAD")
        real_run = subprocess.run
        network = []
        def offline_run(args, **kwargs):
            if args[0] == str(Path(sys.executable).resolve()):
                network.append("github")
                self.assertIn("list", args)
                return SimpleNamespace(returncode=0, stdout=json.dumps([{
                    "url": "https://github.com/otavio-alvxs/orquestracao/pull/8",
                    "isDraft": True, "baseRefName": "main"}]))
            if "push" in args:
                network.append("push")
                snapshot = Path(kwargs["env"]["GIT_DIR"])
                self.assertNotEqual(snapshot, self.root / ".git")
                self.assertEqual(real_run([git, "rev-parse", "HEAD"], cwd=snapshot,
                                         env=kwargs["env"], capture_output=True,
                                         text=True, check=True).stdout.strip(), head)
                self.assertIn(head + ":refs/heads/feat/offline", args)
                return SimpleNamespace(returncode=0, stdout="")
            return real_run(args, **kwargs)
        with patch("web_operations.PUBLISH_PROJECT", self.root), \
             patch("web_operations.shutil.which", side_effect=lambda name: git if name == "git" else sys.executable), \
             patch("web_operations.subprocess.run", side_effect=offline_run):
            self.assertIn("Draft PR existente", _publish_draft_pr(str(self.root), baseline))
            self.assertEqual(network, ["github", "push"])
            network.clear()
            with self.assertRaisesRegex(PublishError, "commits novos"):
                _publish_draft_pr(str(self.root), head)
            tracked.write_text("dirty\n")
            with self.assertRaisesRegex(PublishError, "limpo"):
                _publish_draft_pr(str(self.root), baseline)
            self.assertEqual(network, [])
        self.assertEqual(local_git("rev-parse", "HEAD"), head)

    async def test_cancel_during_publication_preserves_real_result(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def dispatch(callback, *args):
            entered.set()
            await release.wait()
            return "Draft PR criado: resultado real"
        self.app = OperationsServer([self.root], publish_dispatch=dispatch)
        self.port = await self.app.start(0)
        self.origin = f"http://127.0.0.1:{self.port}"
        self.auth = {"Origin": self.origin, "X-CSRF-Token": self.app.session.token}
        run = AppServerRun(str(self.root), "ok", mode="development")
        async def events():
            run.state.status = "completed"
            yield "done"
        run.events = events
        run.cancel = AsyncMock()
        self.app.session.run = run
        self.app.session.publish_authorized = True
        self.app.session.publish_baseline = "a" * 40
        with patch("web_operations.PUBLISH_PROJECT", self.root):
            self.app.session.task = asyncio.create_task(self.app._execute(run))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                status, _, body = await self.post("/api/cancel", {})
                self.assertEqual(status, 409)
                self.assertIn("Publicação já iniciada", body.decode())
                self.assertEqual(self.app.session.publish_result, "Criando Draft PR…")
                run.cancel.assert_not_awaited()
            finally:
                release.set()
                await asyncio.wait_for(self.app.session.task, 3)
        self.assertEqual(self.app.session.publish_result, "Draft PR criado: resultado real")
        self.assertEqual(run.state.status, "completed")

    def test_publisher_pushes_snapshot_and_confirms_pr_url(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text('[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\nfetch=+refs/heads/*:refs/remotes/origin/*\n')
        calls = []
        outputs = [
            SimpleNamespace(returncode=0, stdout=str(self.root / ".git")),
            SimpleNamespace(returncode=0, stdout=str(self.root)),
            SimpleNamespace(returncode=0, stdout="[]"),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout=json.dumps({"url":"https://github.com/otavio-alvxs/orquestracao/pull/8"})),
        ]
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if "--absolute-git-dir" in args: return SimpleNamespace(returncode=0, stdout=str(self.root / ".git"))
            if "--show-toplevel" in args: return SimpleNamespace(returncode=0, stdout=str(self.root))
            if "--is-inside-work-tree" in args: return SimpleNamespace(returncode=0, stdout="true")
            if "symbolic-ref" in args: return SimpleNamespace(returncode=0, stdout="feat/test")
            if "status" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-parse" in args: return SimpleNamespace(returncode=0, stdout="a" * 40)
            if "merge-base" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-list" in args: return SimpleNamespace(returncode=0, stdout="2")
            if "show" in args: return SimpleNamespace(returncode=0, stdout="ignored subject")
            if args[0] == "/usr/bin/gh" and "list" in args:
                return SimpleNamespace(returncode=0, stdout="[]")
            if args[0] == "/usr/bin/gh" and "create" in args:
                return SimpleNamespace(returncode=0, stdout="")
            if args[0] == "/usr/bin/gh" and "view" in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"url":"https://github.com/otavio-alvxs/orquestracao/pull/8"}))
            if "push" in args: return SimpleNamespace(returncode=0, stdout="")
            return outputs.pop(0)
        with patch("web_operations.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
             patch("web_operations.subprocess.run", side_effect=fake_run), \
             patch("web_operations.fcntl.flock"), \
             patch("web_operations.PUBLISH_PROJECT", self.root):
            with patch("web_operations.Path.resolve", autospec=True, side_effect=lambda path, **kw: path):
                result = _publish_draft_pr(str(self.root), "b" * 40)
        self.assertEqual(result, "Draft PR criado: https://github.com/otavio-alvxs/orquestracao/pull/8")
        push = next(args for args, _ in calls if args[0] == "/usr/bin/git" and "push" in args)
        self.assertIn("a" * 40 + ":refs/heads/feat/test", push)
        push_kwargs = next(kwargs for args, kwargs in calls if args == push)
        self.assertEqual(str(push_kwargs["cwd"]), push_kwargs["env"]["GIT_DIR"])
        self.assertNotEqual(push_kwargs["cwd"], str(self.root))
        self.assertEqual(push_kwargs["env"].get("GIT_NO_REPLACE_OBJECTS"), "1")
        self.assertTrue(all(kwargs.get("env", {}).get("GIT_NO_REPLACE_OBJECTS") == "1"
                            for args, kwargs in calls if args[0] == "/usr/bin/git"))
        gh_create = next(args for args, _ in calls if args[0] == "/usr/bin/gh" and "create" in args)
        self.assertIn("--draft", gh_create)
        self.assertIn("Atualização da branch feat/test", gh_create)

    def test_publisher_returns_existing_validated_pr_url(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text('[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n')
        outputs = [
            SimpleNamespace(returncode=0, stdout=str(self.root / ".git")),
            SimpleNamespace(returncode=0, stdout=str(self.root)),
        ]
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if "--absolute-git-dir" in args: return SimpleNamespace(returncode=0, stdout=str(self.root / ".git"))
            if "--show-toplevel" in args: return SimpleNamespace(returncode=0, stdout=str(self.root))
            if "--is-inside-work-tree" in args: return SimpleNamespace(returncode=0, stdout="true")
            if "symbolic-ref" in args: return SimpleNamespace(returncode=0, stdout="feat/test")
            if "status" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-parse" in args: return SimpleNamespace(returncode=0, stdout="c" * 40)
            if args[0] == "/usr/bin/gh" and "list" in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps([{"url":"https://github.com/otavio-alvxs/orquestracao/pull/9", "isDraft": True, "baseRefName": "main"}]))
            if "merge-base" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-list" in args: return SimpleNamespace(returncode=0, stdout="1")
            if "push" in args: return SimpleNamespace(returncode=0, stdout="")
            if args[0] == "/usr/bin/gh" and "list" in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps([{"url":"https://github.com/otavio-alvxs/orquestracao/pull/9", "isDraft": True, "baseRefName": "main"}]))
            return outputs.pop(0)
        with patch("web_operations.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
             patch("web_operations.subprocess.run", side_effect=fake_run), \
             patch("web_operations.Path.resolve", autospec=True, side_effect=lambda path, **kw: path), \
             patch("web_operations.fcntl.flock"), \
             patch("web_operations.PUBLISH_PROJECT", self.root):
            result = _publish_draft_pr(str(self.root), "b" * 40)
        self.assertEqual(result, "Draft PR existente: https://github.com/otavio-alvxs/orquestracao/pull/9")
        self.assertTrue(any("push" in args for args, _ in calls))
        list_call = next(args for args, _ in calls if args[0] == "/usr/bin/gh" and "list" in args)
        self.assertNotIn("--base", list_call)
        self.assertIn("url,isDraft,baseRefName", list_call)

    def test_publisher_rejects_existing_non_draft_without_push(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text('[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n')
        calls = []
        def fake_run(args, **kwargs):
            calls.append(args)
            if "--absolute-git-dir" in args: return SimpleNamespace(returncode=0, stdout=str(self.root / ".git"))
            if "--show-toplevel" in args: return SimpleNamespace(returncode=0, stdout=str(self.root))
            if "--is-inside-work-tree" in args: return SimpleNamespace(returncode=0, stdout="true")
            if "symbolic-ref" in args: return SimpleNamespace(returncode=0, stdout="feat/test")
            if "status" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-parse" in args: return SimpleNamespace(returncode=0, stdout="c" * 40)
            if "merge-base" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-list" in args: return SimpleNamespace(returncode=0, stdout="1")
            if args[0] == "/usr/bin/gh" and "list" in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps([{"url":"https://github.com/otavio-alvxs/orquestracao/pull/9", "isDraft": False, "baseRefName": "main"}]))
            raise AssertionError("Unexpected command after non-draft PR")
        with patch("web_operations.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
             patch("web_operations.subprocess.run", side_effect=fake_run), \
             patch("web_operations.Path.resolve", autospec=True, side_effect=lambda path, **kw: path), \
             patch("web_operations.fcntl.flock"), \
             patch("web_operations.PUBLISH_PROJECT", self.root):
            with self.assertRaises(PublishError):
                _publish_draft_pr(str(self.root), "b" * 40)
        self.assertFalse(any("push" in args for args in calls))

    def test_publisher_rejects_existing_pr_with_other_base_or_multiple_prs(self):
        for rows in (
            [{"url": "https://github.com/otavio-alvxs/orquestracao/pull/10", "isDraft": True, "baseRefName": "release"}],
            [{"url": "https://github.com/otavio-alvxs/orquestracao/pull/10", "isDraft": True, "baseRefName": "main"},
             {"url": "https://github.com/otavio-alvxs/orquestracao/pull/11", "isDraft": True, "baseRefName": "main"}],
        ):
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".git").mkdir()
                (root / ".git" / "config").write_text('[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n')
                calls = []
                def fake_run(args, **kwargs):
                    calls.append(args)
                    if "--absolute-git-dir" in args: return SimpleNamespace(returncode=0, stdout=str(root / ".git"))
                    if "--show-toplevel" in args: return SimpleNamespace(returncode=0, stdout=str(root))
                    if "--is-inside-work-tree" in args: return SimpleNamespace(returncode=0, stdout="true")
                    if "symbolic-ref" in args: return SimpleNamespace(returncode=0, stdout="feat/test")
                    if "status" in args: return SimpleNamespace(returncode=0, stdout="")
                    if "rev-parse" in args: return SimpleNamespace(returncode=0, stdout="c" * 40)
                    if "merge-base" in args: return SimpleNamespace(returncode=0, stdout="")
                    if "rev-list" in args: return SimpleNamespace(returncode=0, stdout="1")
                    if args[0] == "/usr/bin/gh" and "list" in args:
                        return SimpleNamespace(returncode=0, stdout=json.dumps(rows))
                    raise AssertionError("Unexpected command before rejecting unsafe existing PR")
                with patch("web_operations.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
                     patch("web_operations.subprocess.run", side_effect=fake_run), \
                     patch("web_operations.Path.resolve", autospec=True, side_effect=lambda path, **kw: path), \
                     patch("web_operations.fcntl.flock"), \
                     patch("web_operations.PUBLISH_PROJECT", root):
                    with self.assertRaises(PublishError):
                        _publish_draft_pr(str(root), "b" * 40)
                self.assertFalse(any("push" in args for args in calls))

    def test_publisher_rejects_non_configured_checkout(self):
        with patch("web_operations.PUBLISH_PROJECT", self.root / "another-checkout"):
            with self.assertRaises(PublishError):
                _publish_draft_pr(str(self.root), "b" * 40)

    def test_publisher_rejects_invalid_existing_pr_url(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text('[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n')
        def fake_run(args, **kwargs):
            if "--absolute-git-dir" in args: return SimpleNamespace(returncode=0, stdout=str(self.root / ".git"))
            if "--show-toplevel" in args: return SimpleNamespace(returncode=0, stdout=str(self.root))
            if "--is-inside-work-tree" in args: return SimpleNamespace(returncode=0, stdout="true")
            if "symbolic-ref" in args: return SimpleNamespace(returncode=0, stdout="feat/test")
            if "status" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-parse" in args: return SimpleNamespace(returncode=0, stdout="c" * 40)
            if "merge-base" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-list" in args: return SimpleNamespace(returncode=0, stdout="1")
            if args[0] == "/usr/bin/gh" and "list" in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps([{"url":"https://evil.test/pull/1", "isDraft": True, "baseRefName": "main"}]))
            raise AssertionError("Unexpected command after invalid PR URL")
        with patch("web_operations.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
             patch("web_operations.subprocess.run", side_effect=fake_run), \
             patch("web_operations.Path.resolve", autospec=True, side_effect=lambda path, **kw: path), \
             patch("web_operations.fcntl.flock"), \
             patch("web_operations.PUBLISH_PROJECT", self.root):
            with self.assertRaises(PublishError):
                _publish_draft_pr(str(self.root), "b" * 40)

    def test_publisher_blocks_when_gh_cannot_validate_authentication(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text('[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n')
        calls = []
        def fake_run(args, **kwargs):
            calls.append(args)
            if "--absolute-git-dir" in args: return SimpleNamespace(returncode=0, stdout=str(self.root / ".git"))
            if "--show-toplevel" in args: return SimpleNamespace(returncode=0, stdout=str(self.root))
            if "--is-inside-work-tree" in args: return SimpleNamespace(returncode=0, stdout="true")
            if "symbolic-ref" in args: return SimpleNamespace(returncode=0, stdout="feat/test")
            if "status" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-parse" in args: return SimpleNamespace(returncode=0, stdout="c" * 40)
            if "merge-base" in args: return SimpleNamespace(returncode=0, stdout="")
            if "rev-list" in args: return SimpleNamespace(returncode=0, stdout="1")
            if args[0] == "/usr/bin/gh" and "list" in args:
                return SimpleNamespace(returncode=1, stdout="")
            raise AssertionError("Unexpected command after gh authentication failure")
        with patch("web_operations.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
             patch("web_operations.subprocess.run", side_effect=fake_run), \
             patch("web_operations.Path.resolve", autospec=True, side_effect=lambda path, **kw: path), \
             patch("web_operations.fcntl.flock"), \
             patch("web_operations.PUBLISH_PROJECT", self.root):
            with self.assertRaises(PublishError):
                _publish_draft_pr(str(self.root), "b" * 40)
        self.assertFalse(any("push" in args for args in calls))

    def test_publisher_rejects_protected_branch_dirty_tree_and_bad_local_config(self):
        cases = [
            ("protected branch", '[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "main", ""),
            ("dirty tree", '[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", " M file"),
            ("wrong remote", '[remote "origin"]\nurl=https://example.com/attacker/repo.git\n', "feat/test", ""),
            ("config include", '[include]\npath=/tmp/untrusted\n[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", ""),
            ("executable config", '[core]\nsshCommand=/tmp/attacker\n[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", ""),
            ("attribute filter", '[core]\nattributesFile=/tmp/attacker-attributes\n[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", ""),
            ("hooks path", '[core]\nhooksPath=/tmp/attacker-hooks\n[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", ""),
            ("fsmonitor", '[core]\nfsmonitor=/tmp/attacker-fsmonitor\n[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", ""),
            ("credential helper", '[credential]\nhelper=/tmp/attacker-credential\n[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", ""),
            ("url rewrite", '[url "https://example.com/attacker/"]\ninsteadOf=https://github.com/otavio-alvxs/\n[remote "origin"]\nurl=https://github.com/otavio-alvxs/orquestracao.git\n', "feat/test", ""),
        ]
        for label, config, branch, status in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".git").mkdir()
                (root / ".git" / "config").write_text(config)
                gh_calls = []
                def fake_run(args, **kwargs):
                    if "--absolute-git-dir" in args: return SimpleNamespace(returncode=0, stdout=str(root / ".git"))
                    if "--show-toplevel" in args: return SimpleNamespace(returncode=0, stdout=str(root))
                    if args[0] == "/usr/bin/gh": gh_calls.append(args)
                    if "--is-inside-work-tree" in args: return SimpleNamespace(returncode=0, stdout="true")
                    if "symbolic-ref" in args: return SimpleNamespace(returncode=0, stdout=branch)
                    if "status" in args: return SimpleNamespace(returncode=0, stdout=status)
                    return SimpleNamespace(returncode=1, stdout="")
                with patch("web_operations.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
                     patch("web_operations.subprocess.run", side_effect=fake_run), \
                     patch("web_operations.Path.resolve", autospec=True, side_effect=lambda path, **kw: path), \
                     patch("web_operations.PUBLISH_PROJECT", root):
                    with self.assertRaises(PublishError):
                        _publish_draft_pr(str(root), "d" * 40)
                self.assertEqual(gh_calls, [])

    def test_publish_checkbox_is_opt_in_and_reset_after_mode_change_and_submission(self):
        import html as html_module
        import http.server
        import threading

        html = (Path(__file__).with_name("web") / "index.html").read_text()
        checkbox = html.split('id="publish-draft-pr"', 1)[1].split(">", 1)[0]
        self.assertNotIn(" checked", checkbox)
        self.assertIn("O Codex precisa criar um commit novo em feat/* e deixar a árvore de trabalho limpa.", html)
        firefox = shutil.which("firefox")
        if not firefox:
            self.skipTest("Firefox não está disponível no PATH")

        script = r'''
        const requests = [];
        window.fetch = async (url, options = {}) => {
          if (url === '/api/session') return new Response(JSON.stringify({status: 'idle', events: [], cursor: 0}));
          if (url === '/api/events') return new Response(new ReadableStream({start() {}}));
          if (url === '/api/run') requests.push(JSON.parse(options.body));
          return new Response('{}');
        };
        window.addEventListener('load', async () => {
          const el = id => document.getElementById(id);
          const change = id => el(id).dispatchEvent(new Event('change'));
          const check = (condition, message) => { if (!condition) throw Error(message); };
          try {
            await new Promise(resolve => setTimeout(resolve, 100));
            const box = el('publish-draft-pr');
            check(!box.checked, 'publication must be opt-in');
            el('project').value = 'allowed'; change('project');
            el('mode').value = 'development'; change('mode');
            check(!el('publish-option').classList.contains('hidden'), 'direct development should allow publication');
            for (const flow of ['automatic', 'orchestrated']) {
              box.checked = true; el('workflow').value = flow; change('workflow');
              check(!box.checked && el('publish-option').classList.contains('hidden'), flow + ' must clear publication');
              el('workflow').value = 'direct'; change('workflow');
            }
            box.checked = true; el('mode').value = 'reading'; change('mode');
            check(!box.checked && el('publish-option').classList.contains('hidden'), 'reading must clear publication');
            el('mode').value = 'development'; change('mode');
            box.checked = true; el('project').value = 'denied'; change('project');
            check(!box.checked && el('publish-option').classList.contains('hidden'), 'disallowed project must clear publication');
            el('project').value = 'allowed'; change('project');
            box.checked = true; el('prompt').value = 'task'; el('start').click();
            await new Promise(resolve => setTimeout(resolve, 100));
            check(requests.length === 1 && requests[0].publish_draft_pr === true, 'submission must send opt-in');
            check(!box.checked, 'submission must clear publication');
            navigator.sendBeacon('/result', 'ok');
          } catch (error) { navigator.sendBeacon('/result', error.stack); }
        });
        '''
        projects = html_module.escape(json.dumps([
            {"id": "allowed", "name": "allowed", "publish": True},
            {"id": "denied", "name": "denied", "publish": False},
        ]), quote=True)
        page = (html.replace('__CSRF__', 'test').replace('__PROJECTS_HTML__', projects)
                .replace('<script src="/app.js" defer></script>',
                         '<script>' + script + '</script><script src="/app.js" defer></script>'))
        result = []
        received = threading.Event()
        js = (Path(__file__).with_name("web") / "app.js").read_bytes()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                data = js if self.path == '/app.js' else page.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/javascript' if self.path == '/app.js' else 'text/html')
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                result.append(self.rfile.read(int(self.headers['Content-Length'])).decode())
                self.send_response(200)
                self.end_headers()
                received.set()

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)
        try:
            thread.start()
            with tempfile.TemporaryDirectory() as profile:
                process = subprocess.Popen([firefox, '--headless', '--no-remote', '--profile', profile,
                                            f'http://127.0.0.1:{server.server_port}/'],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    self.assertTrue(received.wait(8), 'Firefox não enviou o resultado em 8 segundos')
                    self.assertEqual(result, ['ok'])
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=5)
            server.server_close()

    async def test_continue_consumes_publish_authorization(self):
        self.app = OperationsServer([self.root])
        run = AppServerRun(str(self.root), "original", mode="development")
        run.thread_id = "thread-1"
        run.events = lambda: None
        self.app.session.run = run
        self.app.session.engine = "app-server"
        self.app.session.publish_authorized = True
        async def response(writer, status, body, content_type="text/plain"):
            self.assertEqual(status, 202)
        self.app._response = response
        await self.app._continue_run({"prompt": "follow-up"}, None)
        self.assertFalse(self.app.session.publish_authorized)
        if self.app.session.task:
            self.app.session.task.cancel()
            await asyncio.gather(self.app.session.task, return_exceptions=True)

    async def test_cancel_consumes_publish_authorization_immediately(self):
        self.app = OperationsServer([self.root])
        run = SimpleNamespace(state=SimpleNamespace(status="running"), cancel=AsyncMock())
        self.app.session.run = run
        self.app.session.publish_authorized = True

        class Writer:
            def __init__(self): self.buffer = bytearray(); self.closed = False
            def write(self, data): self.buffer.extend(data)
            async def drain(self): pass
            def is_closing(self): return self.closed
            def close(self): self.closed = True
            async def wait_closed(self): pass
            def get_extra_info(self, name): return ("127.0.0.1", 5555) if name == "peername" else None

        body = b"{}"
        reader = asyncio.StreamReader()
        reader.feed_data(b"POST /api/cancel HTTP/1.1\r\nHost: 127.0.0.1:1234\r\nOrigin: http://127.0.0.1:1234\r\nX-CSRF-Token: " + self.app.session.token.encode() + b"\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n" + body)
        reader.feed_eof()
        writer = Writer()
        await self.app._client(reader, writer)
        self.assertFalse(self.app.session.publish_authorized)
        self.assertTrue(self.app.session.publish_attempted)
        self.assertIn("cancelada", self.app.session.publish_result)
        run.cancel.assert_awaited_once()

    def test_publisher_blocks_when_gh_auth_helper_is_unavailable(self):
        with patch("web_operations.shutil.which", side_effect=lambda name: "/usr/bin/git" if name == "git" else None):
            with self.assertRaises(PublishError):
                _publish_draft_pr(str(self.root), "e" * 40)

    async def app_start(self, script):
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.app = OperationsServer([self.root], process_factory=factory)
        self.port = await self.app.start(0)
        self.origin = f"http://127.0.0.1:{self.port}"
        self.auth = {"Origin": self.origin, "X-CSRF-Token": self.app.session.token}

    async def post(self, path, payload, auth=None):
        headers = {"Content-Type": "application/json", **(auth or self.auth)}
        return await http(self.port, "POST", path, headers, json.dumps(payload).encode())

    async def test_page_security_and_rejects_untrusted_requests(self):
        await self.app_start(SUCCESS)
        status, headers, body = await http(self.port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"content-security-policy", b" ".join(k.encode() for k in headers))
        self.assertIn(self.app.session.token.encode(), body)
        self.assertEqual((await http(self.port, "GET", "/", {"Host": "evil.test"}))[0], 403)
        self.assertEqual((await self.post("/api/run", {"prompt": "x", "project": str(self.root)}, {"Origin": "http://evil.test", "X-CSRF-Token": self.app.session.token, "Content-Type": "application/json"}))[0], 403)
        self.assertEqual((await self.post("/api/run", {"prompt": "x", "project": str(self.root)}, {"Origin": self.origin, "X-CSRF-Token": "bad", "Content-Type": "application/json"}))[0], 403)
        self.assertEqual((await self.post("/api/run", {"prompt": "x", "project": str(self.root / "..") }))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/api/events", {"X-CSRF-Token": self.app.session.token, "Sec-Fetch-Site": "cross-site"}))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/api/events", {"Origin": "http://evil.test", "X-CSRF-Token": self.app.session.token}))[0], 403)

    async def test_draft_pr_requires_development_and_csrf(self):
        await self.app_start(SUCCESS)
        self.assertEqual((await self.post("/api/run", {"prompt": "x", "project": str(self.root), "publish_draft_pr": True}))[0], 400)
        self.assertEqual((await self.post("/api/run", {"prompt": "x", "project": str(self.root), "mode": "development", "publish_draft_pr": "yes"}))[0], 400)
        self.assertEqual((await self.post("/api/publish", {}, {"Origin": self.origin, "X-CSRF-Token": "bad", "Content-Type": "application/json"}))[0], 403)

    async def test_draft_pr_one_shot_after_success_and_not_reused(self):
        calls = []
        def publisher(path, baseline):
            calls.append((path, baseline))
            return "Draft PR criado: https://github.com/otavio-alvxs/orquestracao/pull/1"
        async def inline_dispatch(callback, *args):
            return callback(*args)
        self.app = OperationsServer([self.root], publish_runner=publisher, publish_dispatch=inline_dispatch)
        run = AppServerRun(str(self.root), "ok", mode="development")
        async def events():
            run.state.status = "completed"
            yield "done"
        run.events = events
        self.app.session.run = run
        self.app.session.engine = "app-server"
        self.app.session.publish_authorized = True
        self.app.session.publish_baseline = "a" * 40
        from unittest.mock import patch
        with patch("web_operations.PUBLISH_PROJECT", self.root):
            self.app.session.task = asyncio.create_task(self.app._execute(run))
            await asyncio.wait_for(self.app.session.task, 3)
        self.assertEqual(self.app.session.run.state.status, "completed")
        self.assertEqual(self.app.session.publish_result, "Draft PR criado: https://github.com/otavio-alvxs/orquestracao/pull/1")
        self.assertEqual(calls, [(str(self.root), "a" * 40)])
        self.assertTrue(self.app.session.publish_attempted)
        self.assertFalse(self.app.session.publish_authorized)
        del self.app

    async def test_draft_pr_reuses_existing_result_without_republishing(self):
        calls = []
        self.app = OperationsServer([self.root], publish_runner=lambda path, baseline: calls.append((path, baseline)))
        self.app.session.publish_authorized = True
        self.app.session.publish_attempted = True
        self.app.session.publish_result = "Draft PR existente: https://github.com/otavio-alvxs/orquestracao/pull/7"
        async def response(writer, status, body, content_type="text/plain"):
            self.assertEqual(status, 200)
            self.assertIn(b"/pull/7", body)
        self.app._response = response
        await self.app._publish(None)
        self.assertEqual(calls, [])

    async def test_draft_pr_attempt_failure_consumes_authorization(self):
        calls = []
        def publisher(path, baseline):
            calls.append((path, baseline))
            raise RuntimeError("sem auth")
        async def inline_dispatch(callback, *args):
            return callback(*args)
        self.app = OperationsServer([self.root], publish_runner=publisher, publish_dispatch=inline_dispatch)
        run = AppServerRun(str(self.root), "ok", mode="development")
        async def events():
            run.state.status = "completed"
            yield "done"
        run.events = events
        self.app.session.run = run
        self.app.session.engine = "app-server"
        self.app.session.publish_authorized = True
        self.app.session.publish_baseline = "b" * 40
        with patch("web_operations.PUBLISH_PROJECT", self.root):
            self.app.session.task = asyncio.create_task(self.app._execute(run))
            await asyncio.wait_for(self.app.session.task, 3)
        self.assertTrue(self.app.session.publish_attempted)
        self.assertFalse(self.app.session.publish_authorized)
        self.assertEqual(calls, [(str(self.root), "b" * 40)])
        del self.app

    async def test_draft_pr_is_not_attempted_after_interrupted_turn(self):
        calls = []
        async def inline_dispatch(callback, *args):
            return callback(*args)
        self.app = OperationsServer([self.root],
                                    publish_runner=lambda *args: calls.append(args),
                                    publish_dispatch=inline_dispatch)
        run = AppServerRun(str(self.root), "ok", mode="development")
        async def events():
            run.state.status = "interrupted"
            yield "interrupted"
        run.events = events
        self.app.session.run = run
        self.app.session.engine = "app-server"
        self.app.session.publish_authorized = True
        self.app.session.publish_baseline = "f" * 40
        with patch("web_operations.PUBLISH_PROJECT", self.root):
            self.app.session.task = asyncio.create_task(self.app._execute(run))
            await asyncio.wait_for(self.app.session.task, 3)
        self.assertTrue(self.app.session.publish_attempted)
        self.assertFalse(self.app.session.publish_authorized)
        self.assertIn("não concluiu", self.app.session.publish_result)
        self.assertEqual(calls, [])
        del self.app

    async def test_draft_pr_authorization_is_consumed_on_execution_exception(self):
        async def inline_dispatch(callback, *args):
            return callback(*args)
        self.app = OperationsServer([self.root], publish_runner=lambda *args: None,
                                    publish_dispatch=inline_dispatch)
        run = AppServerRun(str(self.root), "ok", mode="development")
        async def events():
            raise RuntimeError("failed event stream")
            yield "unreachable"
        run.events = events
        self.app.session.run = run
        self.app.session.publish_authorized = True
        self.app.session.publish_baseline = "b" * 40
        await self.app._execute(run)
        self.assertEqual(run.state.status, "failed")
        self.assertFalse(self.app.session.publish_authorized)
        self.assertTrue(self.app.session.publish_attempted)
        self.assertIn("não concluiu", self.app.session.publish_result)
        del self.app

    async def test_remote_origin_requires_serve_identity_for_every_route(self):
        script = r'''import json,sys,time
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id'); p=m.get('params',{})
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start':
  assert p['sandbox']=='workspace-write' and p['approvalPolicy']=='on-request'
  send({'id':i,'result':{'thread':{'id':'remote-thread'}}})
 elif method=='turn/start':
  assert p['sandboxPolicy']['type']=='workspaceWrite' and p['sandboxPolicy']['networkAccess'] is False
  assert p['approvalPolicy']=='on-request'
  send({'id':i,'result':{'turn':{'id':'remote-turn'}}})
  send({'method':'turn/completed','params':{'threadId':'remote-thread','turn':{'id':'remote-turn','status':'completed'}}})
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.app = OperationsServer([self.root], process_factory=factory,
                                    remote_origin="https://device.tailnet.ts.net",
                                    remote_logins={"operator@example.com", "usuário@example.com"})
        self.port = await self.app.start(0)
        host = "device.tailnet.ts.net"
        remote = {"Host": host, "Tailscale-User-Login": "operator@example.com"}
        # Local access remains enabled, while proxy headers cannot be smuggled through it.
        self.assertEqual((await http(self.port, "GET", "/"))[0], 200)
        for path in ("/", "/app.js", "/api/status", "/api/events"):
            with self.subTest(path=path):
                self.assertEqual((await http(self.port, "GET", path, {"Host": host}))[0], 403)
                denied = await http(self.port, "GET", path, {
                    **remote, "Tailscale-User-Login": "intruder@example.com",
                    "X-CSRF-Token": self.app.session.token,
                })
                self.assertEqual(denied[0], 403)
                self.assertNotIn(self.app.session.token.encode(), denied[2])
                self.assertNotIn(str(self.root).encode(), denied[2])
        self.assertEqual((await http(self.port, "GET", "/", {
            "Host": "127.0.0.1", "Tailscale-User-Login": "operator@example.com",
        }))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/", {
            "Host": "127.0.0.1", "X-Forwarded-Proto": "https",
        }))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/", {
            **remote, "X-Forwarded-Host": host,
        }))[0], 200)
        self.assertEqual((await http(self.port, "GET", "/", {
            **remote, "Tailscale-Funnel-Request": "?1",
        }))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/", {
            **remote, "Origin": "https://attacker.ts.net",
        }))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/api/events", {
            **remote, "X-CSRF-Token": self.app.session.token, "Origin": "https://device.tailnet.ts.net",
            "Sec-Fetch-Site": "none",
        }))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/api/status", {
            **remote, "X-CSRF-Token": self.app.session.token,
        }))[0], 200)
        for path in ("/", "/app.css", "/api/status"):
            self.assertEqual((await http(self.port, "GET", path, {
                **remote, "Origin": "https://device.tailnet.ts.net", "Sec-Fetch-Site": "cross-site",
                "X-CSRF-Token": self.app.session.token,
            }))[0], 403)
        self.assertEqual((await http(self.port, "GET", "/", {
            "Host": host, "Tailscale-User-Login": "=?utf-8?q?usu=C3=A1rio?=@example.com",
        }))[0], 200)
        self.assertEqual((await self.post("/api/approval", {"request_id": "approval", "decision": "accept"}, {
            "Host": host, "Origin": "https://device.tailnet.ts.net",
            "Tailscale-User-Login": "intruder@example.com", "X-CSRF-Token": self.app.session.token,
        }))[0], 403)
        self.assertEqual((await self.post("/api/approval", {"request_id": "approval", "decision": "accept"}, {
            "Host": host, "Origin": "https://device.tailnet.ts.net",
            "X-CSRF-Token": self.app.session.token,
        }))[0], 403)
        self.assertEqual((await self.post("/api/run", {
            "prompt": "Analyze", "project": str(self.root), "mode": "development",
        }, {"Host": host, "Origin": "https://wrong.ts.net",
            "Tailscale-User-Login": "operator@example.com", "X-CSRF-Token": self.app.session.token,
            "Content-Type": "application/json"}))[0], 403)
        self.assertEqual((await self.post("/api/run", {
            "prompt": "Analyze", "project": str(self.root), "engine": "app-server", "mode": "development",
        }, {"Host": host, "Origin": "https://device.tailnet.ts.net",
            "Tailscale-User-Login": "operator@example.com", "X-CSRF-Token": self.app.session.token,
            "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}))[0], 202)
        await asyncio.wait_for(self.app.session.task, 3)
        self.assertEqual(self.app.session.run.mode, "development")
        self.assertEqual(self.app.session.run.state.status, "completed")
        status, headers, events = await http(self.port, "GET", "/api/events", {
            **remote, "Origin": "https://device.tailnet.ts.net",
            "X-CSRF-Token": self.app.session.token, "Sec-Fetch-Site": "same-origin",
        })
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertIn(b"event: state", events)

    async def test_remote_origin_is_disabled_without_explicit_configuration(self):
        self.app = OperationsServer([self.root])
        self.port = await self.app.start(0)
        self.assertEqual((await http(self.port, "GET", "/"))[0], 200)
        denied = await http(self.port, "GET", "/", {
            "Host": "device.tailnet.ts.net", "Tailscale-User-Login": "operator@example.com",
        })
        self.assertEqual(denied[0], 403)
        self.assertNotIn(self.app.session.token.encode(), denied[2])

    def test_remote_configuration_is_strict_and_paired(self):
        with self.assertRaises(ValueError):
            OperationsServer([self.root], remote_origin="https://device.tailnet.ts.net")
        for origin in ("http://device.tailnet.ts.net", "https://*.tailnet.ts.net",
                       "https://device.tailnet.ts.net/path", "https://device.example.com",
                       "https://a.b.c.ts.net", "https://user@device.tailnet.ts.net"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                OperationsServer([self.root], remote_origin=origin, remote_logins={"operator@example.com"})

    async def test_create_stream_result_duration_and_tokens(self):
        await self.app_start(SUCCESS)
        status, _, _ = await self.post("/api/run", {"prompt": "Analise", "project": str(self.root)})
        self.assertEqual(status, 202)
        status, headers, data = await http(self.port, "GET", "/api/events", {**self.auth, "Last-Event-ID": "0"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertIn(b"resposta sint\xc3\xa9tica", data)
        states = [json.loads(block.split(b"data: ", 1)[1]) for block in data.split(b"\n\n") if b"event: state" in block]
        final = states[-1]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["mode"], "reading")
        self.assertIn("approvals_reviewer=\"user\"", self.app.session.run.command)
        self.assertEqual((final["input_tokens"], final["output_tokens"], final["cached_input_tokens"]), (21, 8, 2))
        self.assertGreaterEqual(final["duration_seconds"], 0)
        self.assertEqual((await self.post("/api/run", {
            "prompt": "alteração", "project": str(self.root), "engine": "exec", "mode": "development",
        }))[0], 400)

    async def test_turn_route_escalation_is_explicit_and_keeps_session(self):
        script = r'''import json,sys
def send(value): print(json.dumps(value), flush=True)
turns=0
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id'); p=m.get('params',{})
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start': send({'id':i,'result':{'thread':{'id':'route-thread'}}})
 elif method=='turn/start':
  turns+=1
  assert p['threadId']=='route-thread' and isinstance(p['input'][0]['text'],str)
  send({'id':i,'result':{'turn':{'id':'route-turn-'+str(turns)}}})
  time.sleep(.25)
  send({'method':'turn/completed','params':{'threadId':'route-thread','turn':{'id':'route-turn-'+str(turns),'status':'completed'}}})
'''
        await self.app_start(script)
        self.assertEqual((await self.post("/api/run", {
            "prompt": "Resuma", "project": str(self.root), "engine": "app-server", "escalate": True,
        }))[0], 400)
        self.assertEqual((await self.post("/api/run", {
            "prompt": "Resuma", "project": str(self.root), "engine": "app-server",
        }))[0], 202)
        self.assertEqual((await self.post("/api/message", {"prompt": "turno concorrente"}))[0], 409)
        await asyncio.wait_for(self.app.session.task, 3)
        run = self.app.session.run
        self.assertEqual((run.state.requested_model, run.state.requested_effort), ("gpt-6-luna", "low"))
        self.assertEqual((await self.post("/api/message", {
            "prompt": "mensagem", "escalate": True, "escalation_reason": "   ",
        }))[0], 400)
        self.assertEqual((await self.post("/api/message", {
            "prompt": "  explique a resposta  ", "escalate": True,
            "escalation_reason": "resposta insuficiente",
        }))[0], 202)
        await asyncio.wait_for(self.app.session.task, 3)
        self.assertEqual(run.thread_id, "route-thread")
        self.assertEqual(run.prompt, "  explique a resposta  ")
        self.assertEqual((run.state.requested_model, run.state.requested_effort), ("gpt-6.1-sol", "medium"))
        self.assertEqual(len(run.state.escalations), 1)
        run.state.status = "interrupted"
        self.assertEqual((await self.post("/api/message", {
            "prompt": "cancelado", "escalate": True, "escalation_reason": "insuficiente",
        }))[0], 409)

    async def test_development_mode_and_native_approval_http_endpoint(self):
        script = r'''import json,sys
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id'); p=m.get('params',{})
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start':
  assert p['sandbox']=='workspace-write' and p['approvalPolicy']=='on-request' and p['approvalsReviewer']=='user'
  send({'id':i,'result':{'thread':{'id':'http-thread'}}})
 elif method=='turn/start':
  assert p['sandboxPolicy']['type']=='workspaceWrite' and p['sandboxPolicy']['networkAccess'] is False
  assert p['approvalPolicy']=='on-request' and p['approvalsReviewer']=='user'
  send({'id':i,'result':{'turn':{'id':'http-turn'}}})
  send({'id':'rpc-http','method':'item/commandExecution/requestApproval','params':{'threadId':'http-thread','turnId':'http-turn','itemId':'cmd-http','command':'python -m unittest','reason':'Run project tests'}})
  decision=json.loads(sys.stdin.readline()); assert decision=={'id':'rpc-http','result':{'decision':'accept'}}
  send({'method':'turn/completed','params':{'threadId':'http-thread','turn':{'id':'http-turn','status':'completed'}}})
'''
        await self.app_start(script)
        status, _, _ = await self.post("/api/run", {
            "prompt": "Run tests", "project": str(self.root), "engine": "app-server", "mode": "development",
        })
        self.assertEqual(status, 202)
        for _ in range(200):
            run = self.app.session.run
            if isinstance(run, AppServerRun) and run.pending_approvals:
                break
            await asyncio.sleep(.01)
        self.assertEqual(run.mode, "development")
        approval_id = next(iter(run.pending_approvals))
        self.assertTrue(self.app._snapshot()["approvals"][0]["can_approve"])
        self.assertEqual((await self.post("/api/approval", {
            "request_id": approval_id, "decision": "accept",
        }))[0], 200)
        self.assertEqual((await self.post("/api/approval", {
            "request_id": approval_id, "decision": "accept",
        }))[0], 409)
        await asyncio.wait_for(self.app.session.task, 3)
        self.assertEqual(run.state.status, "completed")

    async def test_reading_rejects_native_approval_and_allows_decline(self):
        await self.app_start(SUCCESS)
        run = AppServerRun(str(self.root), "Inspect", mode="reading")
        run.thread_id, run.turn_id, run.state.status = "thread", "turn", "running"
        run._write_response = AsyncMock(return_value=True)
        self.app.session.run = run
        self.app.session.engine = "app-server"
        for kind in ("commandExecution", "fileChange"):
            with self.subTest(kind=kind):
                await run._approval_request("rpc-" + kind, "item/" + kind + "/requestApproval", {
                    "threadId": "thread", "turnId": "turn", "itemId": kind,
                })
                approval_id = next(iter(run.pending_approvals))
                self.assertFalse(self.app._snapshot()["approvals"][0]["can_approve"])
                run._write_response.reset_mock()
                self.assertFalse(await run.decide_approval(approval_id, "accept"))
                self.assertEqual((await self.post("/api/approval", {
                    "request_id": approval_id, "decision": "accept",
                }))[0], 409)
                run._write_response.assert_not_awaited()
                self.assertIn(approval_id, run.pending_approvals)
                self.assertEqual((await self.post("/api/approval", {
                    "request_id": approval_id, "decision": "decline",
                }))[0], 200)
                run._write_response.assert_awaited_once_with({
                    "id": "rpc-" + kind, "result": {"decision": "decline"},
                })
                self.assertFalse(run.pending_approvals)

    async def test_same_origin_fetch_without_origin_and_no_cross_run_replay(self):
        script = '''import json,sys
prompt = sys.stdin.read().strip()
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"resultado " + prompt}}), flush=True)
print('{"type":"turn.completed"}', flush=True)
'''
        await self.app_start(script)
        for task in ("primeira", "segunda"):
            self.assertEqual((await self.post("/api/run", {"prompt": task, "project": str(self.root)}))[0], 202)
            await asyncio.wait_for(self.app.session.task, 3)
        status, _, data = await http(self.port, "GET", "/api/events", {"X-CSRF-Token": self.app.session.token})
        self.assertEqual(status, 200)
        self.assertIn("resultado segunda".encode(), data)
        self.assertNotIn("resultado primeira".encode(), data)

    async def test_invalid_engine_does_not_close_existing_app_server_session(self):
        class Writer:
            def __init__(self):
                self.data = bytearray()

            def write(self, value):
                self.data.extend(value)

            async def drain(self):
                pass

        self.app = OperationsServer([self.root])
        existing = AppServerRun(str(self.root), "sessão anterior")
        existing.close = AsyncMock()
        self.app.session.run = existing
        self.app.session.engine = "app-server"
        writer = Writer()

        await self.app._start_run({
            "prompt": "nova tarefa", "project": str(self.root), "engine": "invalido",
        }, writer)

        self.assertTrue(writer.data.startswith(b"HTTP/1.1 400 Bad Request"))
        self.assertIn("Executor inválido".encode(), writer.data)
        self.assertIs(self.app.session.run, existing)
        existing.close.assert_not_awaited()

    async def test_sse_event_published_during_drain_is_not_lost(self):
        app = OperationsServer([self.root])
        self.app = app
        app.session.run = SimpleNamespace(state=SimpleNamespace(status="running"))
        await app.session.emit("state", {"status": "running"})

        class Writer:
            def __init__(self):
                self.data = bytearray()
                self.drains = 0

            def write(self, value):
                self.data.extend(value)

            async def drain(inner):
                inner.drains += 1
                if inner.drains == 2:
                    await app.session.emit("event", {"message": "chegou durante drain"})
                elif inner.drains == 3:
                    app.session.run.state.status = "completed"
                    await app.session.emit("state", {"status": "completed"})

        writer = Writer()
        await asyncio.wait_for(app._stream(writer, 0), .2)
        self.assertIn(b"chegou durante drain", writer.data)

    async def test_sse_stays_open_after_terminal_state_until_client_closes(self):
        app = OperationsServer([self.root])
        self.app = app
        app.session.run = SimpleNamespace(state=SimpleNamespace(status="completed"))
        await app.session.emit("state", {"status": "completed"})
        writer = SimpleNamespace(data=bytearray(), write=lambda value: writer.data.extend(value), drain=AsyncMock())
        task = asyncio.create_task(app._stream(writer, 0))
        await asyncio.sleep(.02)
        self.assertFalse(task.done())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_controlled_failure_is_streamed_without_private_stderr(self):
        await self.app_start("import sys; sys.stdin.read(); sys.stderr.write('private diagnostic'); sys.exit(9)")
        self.assertEqual((await self.post("/api/run", {"prompt": "Falhe", "project": str(self.root)}))[0], 202)
        _, _, data = await http(self.port, "GET", "/api/events", self.auth)
        self.assertIn(b"failed", data)
        self.assertNotIn(b"private diagnostic", data)

    async def test_cancel_stops_controlled_subprocess_and_active_run_conflicts(self):
        script = "import sys,time; sys.stdin.read(); print('{\"type\":\"thread.started\"}', flush=True); time.sleep(30)"
        await self.app_start(script)
        self.assertEqual((await self.post("/api/run", {"prompt": "Aguarde", "project": str(self.root)}))[0], 202)
        for _ in range(100):
            if self.app.session.run and self.app.session.run.process:
                break
            await asyncio.sleep(.01)
        self.assertEqual((await self.post("/api/run", {"prompt": "outra", "project": str(self.root)}))[0], 409)
        self.assertEqual((await self.post("/api/cancel", {}))[0], 200)
        await asyncio.wait_for(self.app.session.task, 3)
        run = self.app.session.run
        self.assertEqual(run.state.status, "interrupted")
        self.assertIsNotNone(run.process.returncode)


if __name__ == "__main__":
    unittest.main()
