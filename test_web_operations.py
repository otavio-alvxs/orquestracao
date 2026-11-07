import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app_server import AppServerRun
from web_operations import OperationsServer

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

    async def test_remote_origin_requires_serve_identity_for_every_route(self):
        script = r'''import json,sys
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
