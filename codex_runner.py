"""Ephemeral read-only Codex execution. No shell or private history access."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable
from task_routing import Route, select_route

MAX_EVENT_BYTES = 4 * 1024 * 1024


@dataclass
class RunState:
    status: str = "idle"
    final: str = ""
    events: list[str] = field(default_factory=list)
    error: str = ""
    agents: dict[str, dict[str, str]] = field(default_factory=dict)
    turn_completed: bool = False
    duration_seconds: float | None = None
    started_at: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    requested_model: str | None = None
    requested_effort: str | None = None
    route_profile: str | None = None
    route_reason: str | None = None
    observed_model: str | None = None
    observed_effort: str | None = None
    observed_route_reason: str | None = None
    escalations: list[str] = field(default_factory=list)

    def finish_timing(self) -> None:
        if self.started_at is not None and self.duration_seconds is None:
            self.duration_seconds = max(0.0, time.monotonic() - self.started_at)


def validate_project(path: str | Path) -> Path:
    """Validate a directory explicitly selected by the user, without echoing errors."""
    try:
        if not str(path).strip():
            raise ValueError
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.X_OK):
            raise ValueError
        return resolved
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Selecione um diretório existente e acessível.") from exc


def build_command(cwd: str | Path, codex: str | None = None, *, approvals_reviewer: str | None = None,
                  route: Route | None = None) -> list[str]:
    project = validate_project(cwd)
    executable = codex or shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI não encontrado no PATH.")
    args = [executable, "-a", "on-request"]
    if approvals_reviewer is not None:
        if approvals_reviewer != "user":
            raise ValueError("Revisor de aprovações inválido.")
        args.extend(["-c", 'approvals_reviewer="user"'])
    route = route or select_route("")
    args.extend(["-m", route.model, "-c", f'model_reasoning_effort="{route.effort}"'])
    return [*args, "exec", "--json", "--ephemeral",
            "--sandbox", "read-only", "--cd", str(project), "-"]


def _coordinator(state: RunState, status: str) -> None:
    state.agents["coordinator"] = {"role": "Coordenador", "status": status,
                                   "model": "não disponível", "effort": "não disponível"}


def parse_event(line: str, state: RunState) -> str | None:
    """Consume exec JSONL; arbitrary diagnostics/types are never rendered verbatim."""
    try:
        event = json.loads(line)
    except (ValueError, TypeError, RecursionError):
        return "Linha de evento inválida recebida." if line.strip() else None
    if not isinstance(event, dict):
        return "Evento estruturado desconhecido."
    kind = event.get("type")
    if kind in ("thread.started", "turn.started"):
        _coordinator(state, "running")
        return "Execução iniciada pelo Codex." if kind == "thread.started" else "Turno do coordenador iniciado."
    if kind == "turn.completed":
        state.turn_completed = True
        usage = event.get("usage")
        if isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
                value = usage.get(key)
                setattr(state, key, value if type(value) is int and value >= 0 else None)
        _coordinator(state, "completed")
        return "Turno do coordenador concluído."
    if kind in ("turn.failed", "error"):
        state.error = "O Codex informou uma falha durante a execução."
        if kind == "turn.failed":
            _coordinator(state, "failed")
        return state.error
    if kind in ("item.started", "item.updated", "item.completed"):
        item = event.get("item")
        if not isinstance(item, dict):
            return "Evento de etapa sem detalhes reconhecidos."
        item_type = item.get("type")
        if kind == "item.completed" and item_type == "agent_message":
            if isinstance(item.get("text"), str):
                state.final = item["text"]
            return "Mensagem do coordenador recebida."
        if item_type == "command_execution":
            return "Comando concluído." if kind == "item.completed" else "Comando em andamento."
        if item_type == "mcp_tool_call":
            return "Chamada MCP concluída." if kind == "item.completed" else "Chamada MCP em andamento."
        # Unsupported collaboration formats are not evidence of role/model execution.
        return "Etapa concluída." if kind == "item.completed" else "Atividade do Codex em andamento."
    return "Evento Codex de tipo desconhecido."


class CodexRun:
    """One process group, with bounded event buffers and deterministic shutdown."""
    def __init__(self, cwd: str | Path, prompt: str, *, codex: str | None = None,
                 process_factory: Callable = asyncio.create_subprocess_exec,
                 approvals_reviewer: str | None = None, route: Route | None = None):
        self.cwd = validate_project(cwd)
        self.prompt = prompt
        self.route = route or select_route(prompt)
        self.command = build_command(self.cwd, codex, approvals_reviewer=approvals_reviewer, route=self.route)
        self.process_factory = process_factory
        self.process: asyncio.subprocess.Process | None = None
        self.state = RunState()
        self.state.requested_model = self.route.model
        self.state.requested_effort = self.route.effort
        self.state.route_profile = self.route.profile
        self.state.route_reason = self.route.reason
        self._cancel_requested = False
        self._spawn_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stop_lock = asyncio.Lock()
        self._stopped = False
        self._reading = False

    async def events(self) -> AsyncIterator[str]:
        if self.state.started_at is None:
            self.state.started_at = time.monotonic()
        if self._cancel_requested:
            self.state.status = "interrupted"
            self.state.finish_timing()
            return
        if not self.prompt.strip():
            self.state.status = "failed"
            self.state.error = "Escreva uma tarefa antes de iniciar."
            self.state.finish_timing()
            yield self.state.error
            return
        self.state.status = "starting"
        yield "Iniciando processo Codex."
        try:
            self._spawn_task = asyncio.create_task(self.process_factory(
                *self.command, cwd=str(self.cwd), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True))
            # Retain ownership if cancellation arrives while the OS creates the process.
            self.process = await asyncio.shield(self._spawn_task)
            self._stderr_task = asyncio.create_task(self._drain(self.process.stderr))
            if self._cancel_requested:
                self.state.status = "interrupted"
                return
            self.process.stdin.write(self.prompt.encode("utf-8"))
            await self.process.stdin.drain()
            self.process.stdin.close()
            self.state.status = "running"
            yield "Processo Codex iniciado; aguardando eventos."
            self._reading = True
            try:
                async for line in self._lines(self.process.stdout):
                    description = parse_event(line, self.state)
                    if description:
                        self.state.events.append(description)
                        del self.state.events[:-500]
                        yield description
            finally:
                self._reading = False
            code = await self.process.wait()
            had_stderr = await self._stderr_task
            if self._cancel_requested:
                self.state.status = "interrupted"
            elif code != 0:
                self.state.status = "failed"
                self.state.error = f"Codex encerrou com código {code}."
            elif self.state.error:
                self.state.status = "failed"
            elif not self.state.turn_completed or not self.state.final.strip():
                self.state.status = "failed"
                self.state.error = "Codex encerrou sem confirmar conclusão e resposta final."
            else:
                self.state.status = "completed"
            if self.state.status == "failed":
                if had_stderr:
                    self.state.error += " Diagnóstico em stderr omitido por privacidade."
                yield self.state.error
        except asyncio.CancelledError:
            self._cancel_requested = True
            self.state.status = "interrupted"
            raise
        except (OSError, ValueError):
            self.state.status = "interrupted" if self._cancel_requested else "failed"
            self.state.error = "Não foi possível iniciar ou acompanhar o Codex."
            yield self.state.error
        finally:
            if self.process is None and self._spawn_task is not None:
                try:
                    self.process = await asyncio.shield(self._spawn_task)
                except OSError:
                    pass
            await self._stop_process()
            if self._stderr_task is not None:
                await asyncio.gather(self._stderr_task, return_exceptions=True)
            if self._cancel_requested and "coordinator" in self.state.agents:
                _coordinator(self.state, "interrupted")
            self.state.finish_timing()

    @staticmethod
    async def _drain(stream: asyncio.StreamReader) -> bool:
        had_data = False
        while await stream.read(8192):
            had_data = True
        return had_data

    @staticmethod
    async def _lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
        pending = bytearray()
        while chunk := await stream.read(8192):
            pending.extend(chunk)
            while (newline := pending.find(b"\n")) >= 0:
                if newline > MAX_EVENT_BYTES:
                    raise ValueError("Event too large")
                raw = bytes(pending[:newline])
                del pending[:newline + 1]
                yield raw.decode("utf-8", errors="replace")
            if len(pending) > MAX_EVENT_BYTES:
                raise ValueError("Event too large")
        if pending:
            yield pending.decode("utf-8", errors="replace")

    async def cancel(self) -> None:
        self._cancel_requested = True
        self.state.status = "interrupted"
        if self.state.started_at is None:
            self.state.started_at = time.monotonic()
        if self.process is None and self._spawn_task is None:
            self.state.finish_timing()
        if self.process is None and self._spawn_task is not None:
            try:
                self.process = await asyncio.shield(self._spawn_task)
            except OSError:
                pass
        await self._stop_process()

    async def _stop_process(self) -> None:
        async with self._stop_lock:
            process = self.process
            if process is None or self._stopped:
                return
            # Resume stdout even after an oversized/malformed stream stopped parsing.
            drain = None if self._reading else asyncio.create_task(self._drain(process.stdout))
            try:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.5)
                except asyncio.TimeoutError:
                    pass
                finally:
                    # A descendant may outlive its leader, even if wait() returned.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await process.wait()
                self._stopped = True
            finally:
                if drain is not None:
                    await drain
