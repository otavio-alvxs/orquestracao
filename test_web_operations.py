import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        self.assertEqual((final["input_tokens"], final["output_tokens"], final["cached_input_tokens"]), (21, 8, 2))
        self.assertGreaterEqual(final["duration_seconds"], 0)

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
