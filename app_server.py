"""Small JSONL client for Codex app-server, scoped to one ephemeral thread."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from codex_runner import RunState, validate_project
from task_routing import Route, select_route, escalate_route


@dataclass
class AppServerRun:
    cwd: str
    prompt: str
    mode: str = "reading"
    profile: str = "auto"
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
    _failure_message: str = "Não foi possível acompanhar o Codex app-server."
    cancel_requested: bool = False
    _message_parts: dict[str, str] = field(default_factory=dict)
    pending_approvals: dict[str, dict] = field(default_factory=dict)
    _approval_by_rpc: dict[object, str] = field(default_factory=dict)
    _early_requests: list[dict] = field(default_factory=list)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    changed_files: set[str] = field(default_factory=set)
    _item_files: dict[str, list[str]] = field(default_factory=dict)
    _completed_turn_ids: set[str] = field(default_factory=set)
    _agent_terminal: set[str] = field(default_factory=set)
    _sub_activity_items: dict[str, str] = field(default_factory=dict)
    _agent_parent: dict[str, str] = field(default_factory=dict)
    _agent_activity_time: dict[str, int] = field(default_factory=dict)
    _agent_terminal_time: dict[str, int | None] = field(default_factory=dict)
    _collab_call_ids: set[str] = field(default_factory=set)
    route: Route | None = None
    route_override: Route | None = None
    usage_baseline: tuple[int, int, int] | None = None
    usage_total: tuple[int, int, int] | None = None
    usage_regressed: bool = False
    turn_count: int = 0
    _usage_turn_had_event: bool = False

    def __post_init__(self):
        self.cwd = str(validate_project(self.cwd))
        if self.mode not in ("reading", "development"):
            raise ValueError("Modo de execução inválido.")
        self.route = select_route(self.prompt, self.profile)
        self.state.requested_model = self.route.model
        self.state.requested_effort = self.route.effort
        self.state.route_profile = self.route.profile
        self.state.route_reason = self.route.reason

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
                        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                            await self._approval_request(request_id, method, message.get("params", {}))
                        else:
                            result = self._safe_request_response(method)
                            response = ({"id": request_id, "result": result} if result is not None else
                                        {"id": request_id, "error": {"code": -32601, "message": "Method not supported"}})
                            await self._write_response(response)
                    else:
                        if method in ("turn/completed", "turn/failed"):
                            await self._mark_turn_ended(message.get("params", {}))
                        await self._notifications.put(message)
                elif type(request_id) is int and request_id in self._responses:
                    future = self._responses[request_id]
                    if not future.done():
                        future.set_result(message)
        except Exception:
            await self._notifications.put({"method": "_transport/error"})
        finally:
            await self.invalidate_approvals()
            await self._decline_early_requests()
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

    async def _write_response(self, response: dict) -> bool:
        if not self.process or not self.process.stdin or self.process.stdin.is_closing():
            return False
        async with self._write_lock:
            try:
                self.process.stdin.write((json.dumps(response) + "\n").encode())
                await self.process.stdin.drain()
                return True
            except (BrokenPipeError, ConnectionError, OSError, RuntimeError):
                return False

    async def _mark_turn_ended(self, params: dict) -> None:
        if not isinstance(params, dict):
            return
        turn = params.get("turn", {})
        ended_id = params.get("turnId") or (turn.get("id") if isinstance(turn, dict) else None)
        if not isinstance(ended_id, str):
            return
        self._completed_turn_ids.add(ended_id)
        for opaque_id, approval in list(self.pending_approvals.items()):
            if approval.get("turn_id") == ended_id:
                await self._finish_approval(opaque_id, "decline")

    async def _approval_request(self, rpc_id, method: str, params: dict) -> None:
        if not isinstance(params, dict):
            await self._write_response({"id": rpc_id, "result": {"decision": "decline"}})
            return
        thread_id, turn_id = params.get("threadId"), params.get("turnId")
        if self.cancel_requested or self._closed:
            await self._write_response({"id": rpc_id, "result": {"decision": "decline"}})
            return
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            await self._write_response({"id": rpc_id, "result": {"decision": "decline"}})
            return
        if thread_id != self.thread_id:
            await self._write_response({"id": rpc_id, "result": {"decision": "decline"}})
            return
        if self.turn_id is None:
            self._early_requests.append({"id": rpc_id, "method": method, "params": params})
            return
        if (turn_id != self.turn_id or turn_id in self._completed_turn_ids or self.cancel_requested or
                self._closed or self.state.status in ("completed", "failed", "interrupted")):
            await self._write_response({"id": rpc_id, "result": {"decision": "decline"}})
            return
        if rpc_id in self._approval_by_rpc:
            await self._finish_approval(self._approval_by_rpc[rpc_id], "decline")
            return
        opaque_id = uuid.uuid4().hex
        approval = {"id": opaque_id, "type": "command" if "commandExecution" in method else "fileChange",
                    "thread_id": thread_id, "turn_id": turn_id, "item_id": params.get("itemId"),
                    "reason": params.get("reason"), "command": params.get("command"), "cwd": params.get("cwd"),
                    "files": self._item_files.get(params.get("itemId"), []),
                    "_rpc_id": rpc_id, "_method": method,
                    "_network": bool(params.get("networkApprovalContext")),
                    "_grant_root": bool(params.get("grantRoot")),
                    "_additional_permissions": params.get("additionalPermissions"),
                    "_available_decisions": params.get("availableDecisions")}
        self.pending_approvals[opaque_id] = approval
        self._approval_by_rpc[rpc_id] = opaque_id
        await self._notifications.put({"method": "approval/pending", "params": {k: v for k, v in approval.items() if not k.startswith("_")}})

    async def decide_approval(self, opaque_id: str, decision: str) -> bool:
        approval = self.pending_approvals.get(opaque_id)
        if decision not in ("accept", "decline") or not approval:
            return False
        if (self._closed or self.cancel_requested or approval["turn_id"] in self._completed_turn_ids or
                self.state.status in ("completed", "failed", "interrupted") or
                approval["thread_id"] != self.thread_id or approval["turn_id"] != self.turn_id):
            await self._finish_approval(opaque_id, "decline")
            return False
        if decision == "accept":
            if self.mode == "reading":
                return False
            if approval.get("_network") or approval.get("_grant_root") or approval.get("_additional_permissions"):
                return False
            available = approval.get("_available_decisions")
            if isinstance(available, list) and decision not in available:
                return False
        elif isinstance(approval.get("_available_decisions"), list) and decision not in approval["_available_decisions"]:
            return False
        return await self._finish_approval(opaque_id, decision)

    async def _finish_approval(self, opaque_id: str, decision: str) -> bool:
        approval = self.pending_approvals.pop(opaque_id, None)
        if not approval:
            return False
        rpc_id = approval["_rpc_id"]
        self._approval_by_rpc.pop(rpc_id, None)
        return await self._write_response({"id": rpc_id, "result": {"decision": decision}})

    async def invalidate_approvals(self) -> None:
        for opaque_id in list(self.pending_approvals):
            await self._finish_approval(opaque_id, "decline")
        await self._decline_early_requests()

    async def _decline_early_requests(self) -> None:
        early, self._early_requests = self._early_requests, []
        for request in early:
            await self._write_response({"id": request["id"], "result": {"decision": "decline"}})

    async def _start(self):
        self._failure_message = "Não foi possível criar o processo Codex app-server."
        executable = self.codex or shutil.which("codex")
        if not executable:
            raise ValueError("Codex CLI não encontrado no PATH.")
        self.process = await self.process_factory(executable, "app-server", "--listen", "stdio://", cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        self._failure_message = "Não foi possível inicializar a conexão RPC com o Codex app-server."
        self._reader = asyncio.create_task(self._read())
        await self._rpc("initialize", {"clientInfo": {"name": "orquestracao-web", "title": "Orquestração Web", "version": "0.14"}})
        if self.cancel_requested:
            return
        self.process.stdin.write(b'{"method":"initialized","params":{}}\n')
        await self.process.stdin.drain()
        self._failure_message = "Não foi possível criar a thread do Codex app-server."
        sandbox = "workspace-write" if self.mode == "development" else "read-only"
        result = await self._rpc("thread/start", {"cwd": self.cwd, "sandbox": sandbox, "ephemeral": True,
            "approvalPolicy": "on-request", "approvalsReviewer": "user"})
        self.thread_id = result.get("thread", {}).get("id")
        if not isinstance(self.thread_id, str) or not self.thread_id:
            raise RuntimeError("Codex não retornou a identificação da sessão.")
        if self.cancel_requested:
            return

    async def events(self):
        self._failure_message = "Não foi possível acompanhar o Codex app-server."
        self.state.started_at = time.monotonic()
        self.state.duration_seconds = None
        self.state.status = "starting"
        self.turn_id = None
        self.state.agents.clear()
        self._agent_terminal.clear()
        self._sub_activity_items.clear()
        self._agent_parent.clear()
        self._agent_activity_time.clear()
        self._agent_terminal_time.clear()
        self._collab_call_ids.clear()
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
            self.route = self.route_override or select_route(self.prompt, self.profile)
            self.route_override = None
            self.state.requested_model = self.route.model
            self.state.requested_effort = self.route.effort
            self.state.route_profile = self.route.profile
            self.state.route_reason = self.route.reason
            self.state.input_tokens = self.state.output_tokens = self.state.cached_input_tokens = None
            self.state.observed_model = self.state.observed_effort = self.state.observed_route_reason = None
            self.usage_baseline = ((0, 0, 0) if self.turn_count == 0 else
                                   self.usage_total if self._usage_turn_had_event else None)
            self._usage_turn_had_event = False
            self.turn_count += 1
            self.usage_regressed = False
            self.state.turn_completed = False
            sandbox_policy = ({"type": "workspaceWrite", "networkAccess": False, "writableRoots": [self.cwd]}
                              if self.mode == "development" else {"type": "readOnly", "networkAccess": False})
            self._failure_message = "Não foi possível iniciar o turno do Codex app-server."
            result = await self._rpc("turn/start", {"threadId": self.thread_id, "input": [{"type": "text", "text": self.prompt}],
                "model": self.route.model, "effort": self.route.effort,
                "sandboxPolicy": sandbox_policy, "approvalPolicy": "on-request",
                "approvalsReviewer": "user"})
            self.turn_id = result.get("turn", {}).get("id")
            if not isinstance(self.turn_id, str) or not self.turn_id:
                raise RuntimeError("Codex não retornou a identificação do turno.")
            self._failure_message = "Não foi possível acompanhar o Codex app-server."
            early, self._early_requests = self._early_requests, []
            for request in early:
                await self._approval_request(request["id"], request["method"], request["params"])
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
                if method == "thread/tokenUsage/updated":
                    if event_thread != self.thread_id or event_turn != self.turn_id:
                        continue
                    token_usage = params.get("tokenUsage")
                    usage = token_usage.get("total") if isinstance(token_usage, dict) else None
                    self._apply_usage_total(usage)
                    continue
                if method == "model/rerouted":
                    if event_thread != self.thread_id or event_turn != self.turn_id:
                        continue
                    self.state.observed_model = params.get("toModel") if isinstance(params.get("toModel"), str) else None
                    self.state.observed_effort = params.get("toEffort") if isinstance(params.get("toEffort"), str) else None
                    self.state.observed_route_reason = params.get("reason") if isinstance(params.get("reason"), str) else None
                    continue
                event_item = params.get("item")
                if not self._collaboration_event_matches(event_item, event_thread, event_turn,
                                                         self.thread_id, self.turn_id):
                    continue
                if isinstance(event_item, dict) and event_item.get("type") == "subAgentActivity":
                    agent_id = event_item.get("agentThreadId")
                    if isinstance(agent_id, str) and event_thread == self.thread_id:
                        self._agent_parent[agent_id] = self.thread_id
                        self.state.agents.setdefault(agent_id, {})["parent"] = "coordinator"
                if method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    if isinstance(delta, str):
                        item_id = params.get("itemId")
                        key = item_id if isinstance(item_id, str) else "__message__"
                        self._message_parts[key] = self._message_parts.get(key, "") + delta
                        self._refresh_final()
                elif method == "item/completed":
                    item = params.get("item", {})
                    if (item.get("type") in ("fileChange", "file_change", "fileChangeItem", "file_change_item") and
                            item.get("status") == "completed"):
                        files = self._paths_from_item(item)
                        self.changed_files.update(files)
                        if isinstance(item.get("id"), str):
                            self._item_files[item["id"]] = files
                    if item.get("type") in ("agentMessage", "agent_message") and isinstance(item.get("text"), str):
                        item_id = item.get("id")
                        key = item_id if isinstance(item_id, str) else "__message__"
                        self._message_parts[key] = item["text"]
                        self._refresh_final()
                    if item.get("type") in ("collabAgentToolCall", "collab_agent_tool_call"):
                        self._observe_agents(item)
                    elif item.get("type") == "subAgentActivity":
                        self._observe_agents(item)
                elif method == "turn/completed":
                    await self.invalidate_approvals()
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
                    await self.invalidate_approvals()
                    self.state.status = "failed"
                    self.state.error = "O turno Codex falhou."
                    self.state.finish_timing()
                    yield self.state.error
                    return
                elif method in ("item/started", "item/updated", "item/completed"):
                    item = params.get("item", {})
                    if item.get("type") in ("fileChange", "file_change", "fileChangeItem", "file_change_item"):
                        files = self._paths_from_item(item)
                        if isinstance(item.get("id"), str):
                            self._item_files[item["id"]] = files
                    if item.get("type") in ("collabAgentToolCall", "collab_agent_tool_call"):
                        self._observe_agents(item)
                    elif item.get("type") == "subAgentActivity":
                        self._observe_agents(item)
                yield method if isinstance(method, str) else "Evento Codex recebido."
        except Exception:
            await self.invalidate_approvals()
            self.state.status = "interrupted" if self.cancel_requested else "failed"
            self.state.error = "" if self.cancel_requested else self._failure_message
            self.state.finish_timing()
            if self.state.error:
                yield self.state.error

    async def cancel(self):
        self.cancel_requested = True
        await self.invalidate_approvals()
        if self.process and self.thread_id and self.turn_id:
            await self._interrupt_known_turn()
        # The protocol may never emit turn/completed after an interrupt. Reap the
        # child so the event consumer wakes and the orchestrator can finish.
        if self.process:
            await self.close()
        self.state.status = "interrupted"
        self.state.finish_timing()

    async def _interrupt_known_turn(self):
        if not (self.process and self.thread_id and self.turn_id):
            return
        try:
            await self._rpc("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=2)
        except Exception:
            pass

    def _apply_usage_total(self, usage: object) -> None:
        if not isinstance(usage, dict):
            self.state.input_tokens = self.state.output_tokens = self.state.cached_input_tokens = None
            self._usage_turn_had_event = False
            return
        values = (usage.get("inputTokens"), usage.get("outputTokens"), usage.get("cachedInputTokens"))
        if not all(type(value) is int and value >= 0 for value in values):
            self.state.input_tokens = self.state.output_tokens = self.state.cached_input_tokens = None
            self._usage_turn_had_event = False
            return
        previous = self.usage_total
        self.usage_total = values
        self._usage_turn_had_event = True
        if self.usage_baseline is None:
            return
        if ((previous is not None and any(value < prior for value, prior in zip(values, previous))) or
                any(value < prior for value, prior in zip(values, self.usage_baseline))):
            self.usage_regressed = True
            self.state.input_tokens = self.state.output_tokens = self.state.cached_input_tokens = None
        elif not self.usage_regressed:
            delta = tuple(value - prior for value, prior in zip(values, self.usage_baseline))
            self.state.input_tokens, self.state.output_tokens, self.state.cached_input_tokens = delta

    def _refresh_final(self):
        self.state.final = "\n\n".join(text for text in self._message_parts.values() if text)

    @staticmethod
    def _collaboration_event_matches(item: dict, thread_id, turn_id, expected_thread, expected_turn) -> bool:
        if not isinstance(item, dict) or item.get("type") not in (
                "collabAgentToolCall", "collab_agent_tool_call", "subAgentActivity"):
            return True
        return (isinstance(thread_id, str) and thread_id == expected_thread and
                isinstance(turn_id, str) and turn_id == expected_turn)

    def _observe_agents(self, item: dict):
        """Normalize only collaboration fields present in app-server items."""
        if not isinstance(item, dict):
            return
        if item.get("type") == "subAgentActivity":
            agent_id = item.get("agentThreadId")
            kind = item.get("kind")
            if not isinstance(agent_id, str) or kind not in ("started", "interacted", "interrupted", "completed"):
                return
            item_id = item.get("id")
            fingerprint = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if isinstance(item_id, str) and self._sub_activity_items.get(item_id) == fingerprint:
                return
            if isinstance(item_id, str):
                self._sub_activity_items[item_id] = fingerprint
                if len(self._sub_activity_items) > 512:
                    self._sub_activity_items.pop(next(iter(self._sub_activity_items)))
            status = {"started": "running", "interacted": "running", "completed": "completed",
                      "interrupted": "interrupted"}[kind]
            agent = self.state.agents.setdefault(agent_id, {})
            timestamp = item.get("startedAtMs")
            timestamp = timestamp if type(timestamp) is int and timestamp >= 0 else None
            is_stale = timestamp is not None and timestamp < self._agent_activity_time.get(agent_id, -1)
            if isinstance(item.get("model"), str) and (not is_stale or "model" not in agent):
                agent["model"] = item["model"]
            if isinstance(item.get("reasoningEffort"), str) and (not is_stale or "effort" not in agent):
                agent["effort"] = item["reasoningEffort"]
            if isinstance(item.get("agentPath"), str):
                agent["name"] = item["agentPath"].rstrip("/").rsplit("/", 1)[-1][:80]
            terminal_time = self._agent_terminal_time.get(agent_id)
            newer_than_terminal = (timestamp is not None and terminal_time is not None and
                                   timestamp > terminal_time and (status != "running" or kind == "started"))
            if agent_id not in self._agent_terminal:
                accepted = not is_stale
            elif status == agent.get("status"):
                accepted = not is_stale
            else:
                # Terminal transitions and restarts need an observed, strictly newer clock.
                accepted = newer_than_terminal and not is_stale
            if accepted:
                agent["status"] = status
                agent["activity"] = {"started": "Iniciou atividade", "interacted": "Atualizou atividade",
                                      "completed": "Atividade concluída", "interrupted": "Atividade interrompida"}[kind]
                if status == "running":
                    self._agent_terminal.discard(agent_id)
                    agent.pop("result", None)
            if accepted and status in ("completed", "interrupted"):
                self._agent_terminal.add(agent_id)
                self._agent_terminal_time[agent_id] = timestamp
            if timestamp is not None and not is_stale:
                self._agent_activity_time[agent_id] = timestamp
            return
        sender_thread = item.get("senderThreadId")
        if isinstance(sender_thread, str) and self.thread_id and sender_thread != self.thread_id:
            return
        states = item.get("agentsStates")
        receiver_ids = item.get("receiverThreadIds")
        activating_ids = set()
        call_id = item.get("id")
        call_time = item.get("startedAtMs")
        call_time = call_time if type(call_time) is int and call_time >= 0 else None
        if (isinstance(receiver_ids, list) and item.get("tool") in ("resumeAgent", "followupTask") and
                isinstance(call_id, str) and call_id not in self._collab_call_ids):
            self._collab_call_ids.add(call_id)
            if len(self._collab_call_ids) > 512:
                self._collab_call_ids.pop()
            if call_time is not None:
                for agent_id in receiver_ids:
                    terminal_time = self._agent_terminal_time.get(agent_id) if isinstance(agent_id, str) else None
                    if terminal_time is not None and call_time > terminal_time:
                        activating_ids.add(agent_id)
                        self._agent_terminal.discard(agent_id)
                        self.state.agents.get(agent_id, {}).pop("result", None)
                        self._agent_activity_time[agent_id] = max(call_time, self._agent_activity_time.get(agent_id, -1))
        if isinstance(states, dict):
            for agent_id, agent in states.items():
                if isinstance(agent_id, str) and isinstance(agent, dict):
                    confirmed = self.state.agents.setdefault(agent_id, {})
                    status = agent.get("status")
                    status = {"errored": "failed", "pendingInit": "unknown",
                              "notFound": "unknown"}.get(status, status)
                    accepted = True
                    if status in ("running", "completed", "failed", "interrupted", "shutdown", "unknown"):
                        accepted = agent_id not in self._agent_terminal or agent_id in activating_ids or status == confirmed.get("status")
                        if accepted:
                            confirmed["status"] = status
                            if status in ("completed", "failed", "interrupted"):
                                self._agent_terminal.add(agent_id)
                                self._agent_terminal_time[agent_id] = None
                    if accepted and isinstance(agent.get("message"), str):
                        confirmed["activity"] = agent["message"][:180]
                        if confirmed.get("status") in ("completed", "failed", "interrupted"):
                            confirmed["result"] = agent["message"][:180]
        if isinstance(receiver_ids, list):
            for agent_id in receiver_ids:
                if isinstance(agent_id, str):
                    confirmed = self.state.agents.setdefault(agent_id, {"status": "unknown", "activity": "Delegação observada"})
                    if (isinstance(sender_thread, str) and sender_thread == self.thread_id
                            and item.get("tool") == "spawnAgent"):
                        self._agent_parent[agent_id] = sender_thread
                        confirmed["parent"] = "coordinator"
                        if isinstance(item.get("model"), str):
                            confirmed["requested_model"] = item["model"]
                        if isinstance(item.get("reasoningEffort"), str):
                            confirmed["requested_effort"] = item["reasoningEffort"]

    async def close(self):
        self._closed = True
        await self.invalidate_approvals()
        if self.process:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), .25 if self.cancel_requested else 2)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), .5)
                except asyncio.TimeoutError:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
                    await self.process.wait()
        if self._reader:
            self._reader.cancel()

    def _paths_from_item(self, item: dict) -> list[str]:
        changes = item.get("changes", [])
        paths = []
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    path = change.get("path") or change.get("filePath") or change.get("file_path")
                    if isinstance(path, str) and path not in paths:
                        paths.append(path)
        return paths
