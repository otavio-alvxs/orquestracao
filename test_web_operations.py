import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app_server import AppServerRun
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
        data = await asyncio.wait_for(reader.read(), 5)
    else:
        data = await reader.readexactly(int(response_headers.get("content-length", "0")))
    writer.close()
    await writer.wait_closed()
    return status, response_headers, data


class WebOperationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        if hasattr(self, "app"):
            await self.app.close()
        self.temp.cleanup()

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
        html = (Path(__file__).with_name("web") / "index.html").read_text()
        js = (Path(__file__).with_name("web") / "app.js").read_text()
        checkbox = html.split('id="publish-draft-pr"', 1)[1].split(">", 1)[0]
        self.assertNotIn(" checked", checkbox)
        self.assertIn("if (!dev) $('publish-draft-pr').checked = false;", js)
        self.assertIn("$('publish-draft-pr').checked = false;", js)

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
