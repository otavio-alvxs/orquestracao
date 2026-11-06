"""Local browser interface for ephemeral, permission-scoped Codex runs."""
from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from codex_runner import CodexRun, validate_project
from app_server import AppServerRun

HOST = "127.0.0.1"
MAX_BODY = 70 * 1024
TERMINAL = {"completed", "failed", "interrupted"}
WEB_ROOT = Path(__file__).with_name("web")


@dataclass
class Session:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    run: CodexRun | None = None
    task: asyncio.Task | None = None
    events: list[tuple[int, str, dict]] = field(default_factory=list)
    next_event: int = 0
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, kind: str, data: dict) -> None:
        async with self.changed:
            self.next_event += 1
            self.events.append((self.next_event, kind, data))
            del self.events[:-500]
            self.changed.notify_all()


class OperationsServer:
    """Small HTTP/SSE adapter. The browser session lives only in process memory."""
    def __init__(self, projects: list[Path], *, codex: str | None = None,
                 process_factory: Callable | None = None):
        self.projects = tuple(dict.fromkeys(validate_project(p) for p in projects))
        if not self.projects:
            raise ValueError("Informe ao menos um diretório autorizado com --project.")
        self.codex = codex
        self.process_factory = process_factory
        self.session = Session()
        self.session.engine = "exec"
        self.server: asyncio.AbstractServer | None = None

    async def start(self, port: int = 8765) -> int:
        self.server = await asyncio.start_server(self._client, HOST, port, limit=MAX_BODY + 8192)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self.session.run and self.session.run.state.status not in TERMINAL:
            await self.session.run.cancel()
        if isinstance(self.session.run, AppServerRun):
            await self.session.run.close()
        if self.session.task and not self.session.task.done():
            try:
                await asyncio.wait_for(asyncio.gather(self.session.task, return_exceptions=True), 3)
            except asyncio.TimeoutError:
                self.session.task.cancel()
                await asyncio.gather(self.session.task, return_exceptions=True)
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), 5)
            parts = request.decode("ascii", "replace").strip().split()
            if len(parts) != 3 or parts[2] != "HTTP/1.1":
                await self._response(writer, 400, b"Bad request")
                return
            method, target, _ = parts
            headers: dict[str, str] = {}
            while line := await asyncio.wait_for(reader.readline(), 5):
                if line in (b"\r\n", b"\n"):
                    break
                key, sep, value = line.decode("latin1").partition(":")
                if not sep or key.lower() in headers:
                    await self._response(writer, 400, b"Bad headers")
                    return
                headers[key.lower()] = value.strip()
            host = headers.get("host", "")
            if not self._valid_host(host):
                await self._response(writer, 403, b"Forbidden")
                return
            path = target.split("?", 1)[0]
            if method == "GET" and path == "/":
                html = (WEB_ROOT / "index.html").read_text()
                html = html.replace("__CSRF__", self.session.token).replace(
                    "__PROJECTS_HTML__", html_lib.escape(json.dumps([str(p) for p in self.projects], ensure_ascii=False), quote=True))
                await self._response(writer, 200, html.encode(), "text/html; charset=utf-8")
                return
            if method == "GET" and path in ("/app.css", "/app.js"):
                suffix = "css" if path.endswith("css") else "js"
                content_type = "text/css; charset=utf-8" if suffix == "css" else "text/javascript; charset=utf-8"
                await self._response(writer, 200, (WEB_ROOT / f"app.{suffix}").read_bytes(), content_type)
                return
            if method == "GET" and path == "/api/status":
                if headers.get("x-csrf-token") != self.session.token:
                    await self._response(writer, 403, b"Forbidden")
                    return
                await self._response(writer, 200, json.dumps(self._snapshot()).encode(), "application/json")
                return
            if method == "GET" and path == "/api/events":
                origin = headers.get("origin")
                fetch_site = headers.get("sec-fetch-site")
                if ((origin is not None and not self._valid_origin(origin, host)) or
                        (fetch_site is not None and fetch_site != "same-origin")):
                    await self._response(writer, 403, b"Forbidden")
                    return
                if headers.get("x-csrf-token") != self.session.token:
                    await self._response(writer, 403, b"Forbidden")
                    return
                await self._stream(writer, int(headers.get("last-event-id", "0")))
                return
            if method == "POST":
                if not self._valid_origin(headers.get("origin", ""), host):
                    await self._response(writer, 403, b"Forbidden")
                    return
                if headers.get("x-csrf-token") != self.session.token:
                    await self._response(writer, 403, b"Forbidden")
                    return
                if headers.get("content-type", "").split(";", 1)[0] != "application/json":
                    await self._response(writer, 415, b"JSON required")
                    return
                length = int(headers.get("content-length", "-1"))
                if not 0 <= length <= MAX_BODY:
                    await self._response(writer, 413, b"Body too large")
                    return
                body = await asyncio.wait_for(reader.readexactly(length), 5)
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    await self._response(writer, 400, b"Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    await self._response(writer, 400, b"Invalid payload")
                    return
                if path == "/api/run":
                    await self._start_run(payload, writer)
                    return
                if path == "/api/message":
                    await self._continue_run(payload, writer)
                    return
                if path == "/api/cancel":
                    run = self.session.run
                    if run and run.state.status not in TERMINAL:
                        await run.cancel()
                    await self._response(writer, 200, b'{"ok":true}', "application/json")
                    return
                if path == "/api/approval":
                    run = self.session.run
                    approval_id, decision = payload.get("request_id"), payload.get("decision")
                    if not isinstance(run, AppServerRun) or self.session.engine != "app-server":
                        await self._response(writer, 409, "Nenhuma sessão app-server ativa".encode())
                        return
                    if not isinstance(approval_id, str) or decision not in ("accept", "decline"):
                        await self._response(writer, 400, "Decisão inválida".encode())
                        return
                    accepted = await run.decide_approval(approval_id, decision)
                    if not accepted:
                        await self._response(writer, 409, "Solicitação expirada ou decisão não suportada".encode())
                        return
                    await self.session.emit("approval-decision", {"request_id": approval_id, "decision": decision})
                    await self.session.emit("state", self._snapshot())
                    await self._response(writer, 200, b'{"ok":true}', "application/json")
                    return
            await self._response(writer, 404, b"Not found")
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError, ConnectionError):
            if not writer.is_closing():
                await self._response(writer, 400, b"Bad request")
        finally:
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

    async def _start_run(self, payload: dict, writer: asyncio.StreamWriter) -> None:
        prompt, project = payload.get("prompt"), payload.get("project")
        mode = payload.get("mode", "reading")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            await self._response(writer, 400, "A tarefa deve conter entre 1 e 16000 caracteres".encode())
            return
        if not isinstance(project, str) or project not in {str(p) for p in self.projects}:
            await self._response(writer, 403, "Projeto não autorizado".encode())
            return
        if mode not in ("reading", "development"):
            await self._response(writer, 400, "Modo inválido".encode())
            return
        if self.session.task and not self.session.task.done():
            await self._response(writer, 409, "Já existe uma execução ativa".encode())
            return
        try:
            engine = payload.get("engine", "exec")
            if engine not in ("exec", "app-server"):
                await self._response(writer, 400, "Executor inválido".encode())
                return
            if engine == "exec" and mode != "reading":
                await self._response(writer, 400, "codex exec está disponível somente no modo Leitura".encode())
                return
            kwargs = {"codex": self.codex}
            if self.process_factory:
                kwargs["process_factory"] = self.process_factory
            if engine == "exec":
                kwargs["approvals_reviewer"] = "user"
            run = AppServerRun(project, prompt, mode=mode, **kwargs) if engine == "app-server" else CodexRun(project, prompt, **kwargs)
        except ValueError:
            await self._response(writer, 400, "Não foi possível validar a tarefa ou o diretório".encode())
            return
        if isinstance(self.session.run, AppServerRun):
            await self.session.run.close()
        # Keep the event ID monotonic, but never replay a previous task into a new view.
        self.session.events.clear()
        self.session.run = run
        self.session.engine = engine
        self.session.task = asyncio.create_task(self._execute(run))
        if engine == "app-server":
            await self.session.emit("message", {"role": "user", "text": prompt})
        await self.session.emit("state", self._snapshot())
        await self._response(writer, 202, b'{"ok":true}', "application/json")

    async def _execute(self, run: CodexRun) -> None:
        assistant_message_emitted = False
        try:
            async for message in run.events():
                if isinstance(run, AppServerRun) and run.state.status in TERMINAL and run.state.final and not assistant_message_emitted:
                    await self.session.emit("message", {"role": "assistant", "text": run.state.final})
                    assistant_message_emitted = True
                await self.session.emit("event", {"message": message})
                await self.session.emit("state", self._snapshot())
            if isinstance(run, AppServerRun) and run.state.final and not assistant_message_emitted:
                await self.session.emit("message", {"role": "assistant", "text": run.state.final})
                assistant_message_emitted = True
            await self.session.emit("state", self._snapshot())
        except Exception:
            await run.cancel()
            run.state.status = "failed"
            run.state.error = "Erro interno ao acompanhar o Codex."
            run.state.finish_timing()
            await self.session.emit("state", self._snapshot())

    async def _continue_run(self, payload: dict, writer: asyncio.StreamWriter) -> None:
        run = self.session.run
        prompt = payload.get("prompt")
        if self.session.engine != "app-server" or not isinstance(run, AppServerRun) or not run.thread_id:
            await self._response(writer, 409, b"Nao existe sessao app-server ativa")
            return
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            await self._response(writer, 400, b"A mensagem deve conter entre 1 e 16000 caracteres")
            return
        if self.session.task and not self.session.task.done():
            await self._response(writer, 409, b"Ja existe um turno ativo")
            return
        run.prompt = prompt.strip()
        run.cancel_requested = False
        run.turn_id = None
        run.state.status = "starting"
        run.state.error = ""
        self.session.task = asyncio.create_task(self._execute(run))
        await self.session.emit("message", {"role": "user", "text": run.prompt})
        await self.session.emit("state", self._snapshot())
        await self._response(writer, 202, b'{"ok":true}', "application/json")

    def _snapshot(self) -> dict:
        run = self.session.run
        if not run:
            return {"status": "idle"}
        state = run.state
        elapsed = state.duration_seconds
        if elapsed is None and state.started_at is not None:
            elapsed = max(0, time.monotonic() - state.started_at)
        agents = getattr(state, "agents", {})
        result = state.final if isinstance(run, AppServerRun) or state.status in TERMINAL else ""
        approvals = []
        if isinstance(run, AppServerRun):
            approvals = [{**{k: v for k, v in item.items() if not k.startswith("_")},
                          "can_approve": (not item.get("_network") and not item.get("_grant_root") and
                                          not item.get("_additional_permissions") and
                                          (not isinstance(item.get("_available_decisions"), list) or
                                           "accept" in item["_available_decisions"]))}
                         for item in run.pending_approvals.values()]
        return {"status": state.status, "result": result, "error": state.error,
                "duration_seconds": elapsed, "input_tokens": state.input_tokens,
                "output_tokens": state.output_tokens, "cached_input_tokens": state.cached_input_tokens,
                "thread_id": getattr(run, "thread_id", None), "agents": agents,
                "mode": getattr(run, "mode", "reading"), "approvals": approvals,
                "changed_files": sorted(getattr(run, "changed_files", set()))}

    async def _stream(self, writer: asyncio.StreamWriter, last_id: int) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\nX-Content-Type-Options: nosniff\r\n\r\n")
        await writer.drain()
        cursor = max(0, last_id)
        try:
            while True:
                batch = [item for item in self.session.events if item[0] > cursor]
                for event_id, kind, data in batch:
                    writer.write(f"id: {event_id}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                    cursor = event_id
                if batch:
                    await writer.drain()
                if self.session.run and self.session.run.state.status in TERMINAL and cursor >= self.session.next_event:
                    return
                async with self.session.changed:
                    try:
                        await asyncio.wait_for(self.session.changed.wait_for(
                            lambda: self.session.next_event > cursor or
                            bool(self.session.run and self.session.run.state.status in TERMINAL)), 15)
                    except asyncio.TimeoutError:
                        writer.write(b": keepalive\n\n")
                        await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            return

    @staticmethod
    def _valid_host(host: str) -> bool:
        return host in ("127.0.0.1", "localhost", "127.0.0.1:8765", "localhost:8765") or (
            host.startswith("127.0.0.1:") and host.rsplit(":", 1)[-1].isdigit())

    @classmethod
    def _valid_origin(cls, origin: str, host: str) -> bool:
        return origin in (f"http://{host}", "http://127.0.0.1", "http://localhost")

    @staticmethod
    async def _response(writer: asyncio.StreamWriter, status: int, body: bytes,
                        content_type: str = "text/plain; charset=utf-8") -> None:
        reason = {200: "OK", 202: "Accepted", 400: "Bad Request", 403: "Forbidden",
                  404: "Not Found", 409: "Conflict", 413: "Payload Too Large", 415: "Unsupported Media Type"}.get(status, "Error")
        headers = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
                   "Connection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                   "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'\r\n"
                   "Referrer-Policy: no-referrer\r\n\r\n").encode()
        writer.write(headers + body)
        await writer.drain()


async def serve(projects: list[Path], port: int, codex: str | None) -> None:
    app = OperationsServer(projects, codex=codex)
    actual_port = await app.start(port)
    print(f"Orquestração Web disponível em http://127.0.0.1:{actual_port}", flush=True)
    try:
        async with app.server:
            await app.server.serve_forever()
    finally:
        await app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="append", default=[], help="Diretório autorizado (repetível)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--codex", help=argparse.SUPPRESS)
    args = parser.parse_args()
    projects = [Path(p) for p in (args.project or [str(Path.cwd())])]
    try:
        asyncio.run(serve(projects, args.port, args.codex))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
