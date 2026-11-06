"""Small JSONL client for Codex app-server, scoped to one ephemeral thread."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from codex_runner import RunState, validate_project


@dataclass
class AppServerRun:
    cwd: str
    prompt: str
    codex: str | None = None
    process_factory: Callable = asyncio.create_subprocess_exec
    process: asyncio.subprocess.Process | None = None
    state: RunState = field(default_factory=RunState)
    thread_id: str | None = None
    turn_id: str | None = None
    _reader: asyncio.Task | None = None
    _responses: dict[int, asyncio.Future] = field(default_factory=dict)
    _notifications: asyncio.Queue = field(default_factory=asyncio.Queue)
    _next_id: int = 0
    _closed: bool = False
    cancel_requested: bool = False
    _message_parts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.cwd = str(validate_project(self.cwd))

    async def _rpc(self, method: str, params: dict | None = None, *, timeout: float = 30):
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._responses[request_id] = future
        self.process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params or {}}) + "\n").encode())
        await self.process.stdin.drain()
        try:
            response = await asyncio.wait_for(future, timeout)
        finally:
            self._responses.pop(request_id, None)
        if "error" in response:
            raise RuntimeError("Codex app-server rejeitou uma solicitação.")
        return response.get("result", {})

    async def _read(self):
        failure = RuntimeError("Conexão app-server encerrada.")
        try:
            while line := await self.process.stdout.readline():
                if len(line) > 4 * 1024 * 1024:
                    raise ValueError("evento acima do limite")
                try:
                    message = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(message, dict):
                    continue
                method, request_id = message.get("method"), message.get("id")
                if isinstance(method, str):
                    if type(request_id) in (int, str):
                        result = self._safe_request_response(method)
                        response = ({"id": request_id, "result": result} if result is not None else
                                    {"id": request_id, "error": {"code": -32601, "message": "Method not supported"}})
                        self.process.stdin.write((json.dumps(response) + "\n").encode())
                        await self.process.stdin.drain()
                    else:
                        await self._notifications.put(message)
                elif type(request_id) is int and request_id in self._responses:
                    future = self._responses[request_id]
                    if not future.done():
                        future.set_result(message)
        except Exception:
            await self._notifications.put({"method": "_transport/error"})
        finally:
            for future in self._responses.values():
                if not future.done():
                    future.set_exception(failure)
            await self._notifications.put({"method": "_transport/closed"})

    @staticmethod
    def _safe_request_response(method: str):
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            return {"decision": "decline"}
        if method in ("item/permissions/requestApproval", "item/permissions/requestUserApproval"):
            return {"permissions": {}}
        if method in ("mcpServer/elicitation/request", "mcpServer/elicitation"):
            return {"action": "decline", "content": None}
        if method in ("item/tool/requestUserInput", "tool/requestUserInput"):
            return {"answers": {}}
        return None

    async def _start(self):
        executable = self.codex or shutil.which("codex")
        if not executable:
            raise ValueError("Codex CLI não encontrado no PATH.")
        self.process = await self.process_factory(executable, "app-server", "--listen", "stdio://", cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        self._reader = asyncio.create_task(self._read())
        await self._rpc("initialize", {"clientInfo": {"name": "orquestracao-web", "title": "Orquestração Web", "version": "0.11"}})
        if self.cancel_requested:
            return
        self.process.stdin.write(b'{"method":"initialized","params":{}}\n')
        await self.process.stdin.drain()
        result = await self._rpc("thread/start", {"cwd": self.cwd, "sandbox": "read-only", "ephemeral": True, "approvalPolicy": "never"})
        self.thread_id = result.get("thread", {}).get("id")
        if not self.thread_id:
            raise RuntimeError("Codex não retornou a identificação da sessão.")
        if self.cancel_requested:
            return

    async def events(self):
        self.state.started_at = time.monotonic()
        self.state.duration_seconds = None
        self.state.status = "starting"
        self.turn_id = None
        self._message_parts.clear()
        self.state.final = ""
        yield "Conectando ao Codex app-server."
        try:
            if not self.process:
                await self._start()
            if self.cancel_requested:
                self.state.status = "interrupted"
                self.state.finish_timing()
                yield "Turno Codex interrupted."
                return
            self.state.turn_completed = False
            result = await self._rpc("turn/start", {"threadId": self.thread_id, "input": [{"type": "text", "text": self.prompt}],
                                                   "sandboxPolicy": {"type": "readOnly"}, "approvalPolicy": "never"})
            self.turn_id = result.get("turn", {}).get("id")
            self.state.status = "running"
            if self.cancel_requested:
                await self._interrupt_known_turn()
            while True:
                event = await asyncio.wait_for(self._notifications.get(), 120)
                method, params = event.get("method"), event.get("params", {})
                if method.startswith("_transport/"):
                    raise RuntimeError("Conexão app-server encerrada.")
                event_thread = params.get("threadId") or params.get("thread", {}).get("id")
                event_turn = params.get("turnId") or params.get("turn", {}).get("id")
                if event_thread and event_thread != self.thread_id:
                    continue
                if event_turn and self.turn_id and event_turn != self.turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    if isinstance(delta, str):
                        item_id = params.get("itemId")
                        key = item_id if isinstance(item_id, str) else "__message__"
                        self._message_parts[key] = self._message_parts.get(key, "") + delta
                        self._refresh_final()
                elif method == "item/completed":
                    item = params.get("item", {})
                    if item.get("type") in ("agentMessage", "agent_message") and isinstance(item.get("text"), str):
                        item_id = item.get("id")
                        key = item_id if isinstance(item_id, str) else "__message__"
                        self._message_parts[key] = item["text"]
                        self._refresh_final()
                    if item.get("type") in ("collabAgentToolCall", "collab_agent_tool_call"):
                        self._observe_agents(item)
                elif method == "turn/completed":
                    self.state.turn_completed = True
                    turn = params.get("turn", {})
                    status = turn.get("status", "completed")
                    self.state.status = "completed" if status == "completed" else ("interrupted" if status == "interrupted" else "failed")
                    if self.state.status == "failed":
                        self.state.error = "O turno Codex não foi concluído."
                    self.state.finish_timing()
                    yield "Turno Codex " + self.state.status + "."
                    return
                elif method == "turn/failed":
                    self.state.status = "failed"
                    self.state.error = "O turno Codex falhou."
                    self.state.finish_timing()
                    yield self.state.error
                    return
                elif method in ("item/started", "item/updated", "item/completed"):
                    item = params.get("item", {})
                    if item.get("type") in ("collabAgentToolCall", "collab_agent_tool_call"):
                        self._observe_agents(item)
                yield method if isinstance(method, str) else "Evento Codex recebido."
        except Exception:
            self.state.status = "failed"
            self.state.error = "Não foi possível iniciar ou acompanhar o Codex app-server."
            self.state.finish_timing()
            yield self.state.error

    async def cancel(self):
        self.cancel_requested = True
        if self.process and self.thread_id and self.turn_id:
            await self._interrupt_known_turn()

    async def _interrupt_known_turn(self):
        if not (self.process and self.thread_id and self.turn_id):
            return
        try:
            await self._rpc("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=2)
        except Exception:
            pass

    def _refresh_final(self):
        self.state.final = "\n\n".join(text for text in self._message_parts.values() if text)

    def _observe_agents(self, item: dict):
        states = item.get("agentsStates")
        if isinstance(states, dict):
            for agent_id, agent in states.items():
                if isinstance(agent_id, str) and isinstance(agent, dict):
                    confirmed = {"status": agent.get("status")}
                    if isinstance(agent.get("model"), str): confirmed["model"] = agent["model"]
                    if isinstance(agent.get("reasoningEffort"), str): confirmed["effort"] = agent["reasoningEffort"]
                    self.state.agents[agent_id] = confirmed
        receiver_ids = item.get("receiverThreadIds")
        if isinstance(receiver_ids, list):
            for agent_id in receiver_ids:
                if isinstance(agent_id, str):
                    confirmed = self.state.agents.setdefault(agent_id, {"status": "unknown"})
                    if isinstance(item.get("model"), str): confirmed["model"] = item["model"]
                    if isinstance(item.get("reasoningEffort"), str): confirmed["effort"] = item["reasoningEffort"]

    async def close(self):
        self._closed = True
        if self.process:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except (asyncio.TimeoutError, ProcessLookupError):
                self.process.terminate()
                await self.process.wait()
        if self._reader:
            self._reader.cancel()
