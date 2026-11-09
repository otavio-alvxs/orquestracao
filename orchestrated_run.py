"""Sequential two-stage Codex app-server orchestration with live state."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

from app_server import AppServerRun
from codex_runner import RunState
from task_routing import Route, SOL, LUNA


@dataclass
class OrchestratedRun:
    cwd: str
    prompt: str
    planning_prompt: str
    run_factory: Callable = AppServerRun
    codex: str | None = None
    process_factory: Callable | None = None
    mode: str = "development"
    state: RunState = field(default_factory=RunState)
    planning: AppServerRun | None = None
    implementation: AppServerRun | None = None
    active_run: AppServerRun | None = None
    cancel_requested: bool = False
    thread_id: str | None = None
    pending_approvals: dict = field(default_factory=dict)
    changed_files: set[str] = field(default_factory=set)

    def __post_init__(self):
        self.state.status = "starting"
        self.state.started_at = time.monotonic()
        self.state.agents = {
            "planning": {"name": "Planejamento — Sol/medium", "role": "Etapa", "requested_model": "Sol", "requested_effort": "medium", "status": "planned"},
            "implementation": {"name": "Implementação — Luna/medium", "role": "Etapa", "requested_model": "Luna", "requested_effort": "medium", "status": "planned"},
        }

    async def events(self):
        stages = (("planning", "reading", "complex", Route("complex", SOL, "medium", "orchestration planning")),
                  ("implementation", "development", "intermediate", Route("intermediate", LUNA, "medium", "orchestration implementation")))
        try:
            for key, mode, profile, route in stages:
                if self.cancel_requested:
                    break
                if key == "implementation":
                    if (self.state.agents["planning"]["status"] != "completed" or self.planning is None or
                            not self.planning.state.final.strip()):
                        self.state.agents["implementation"]["status"] = "blocked"
                        break
                    task_prompt = ("Implemente a tarefa original seguindo o plano abaixo.\n\n"
                                   f"Tarefa original:\n{self.prompt}\n\nPlano produzido por Sol:\n{self.planning.state.final}")
                else:
                    task_prompt = f"{self.planning_prompt}\n\nTarefa original:\n{self.prompt}"
                stage = None
                failure = ""
                terminal = None
                try:
                    stage = self.run_factory(self.cwd, task_prompt, mode=mode, profile=profile, codex=self.codex,
                                             **({"process_factory": self.process_factory} if self.process_factory else {}))
                    setattr(self, key, stage)
                    stage.route_override = route
                    self.active_run = stage
                    self.thread_id = stage.thread_id
                    self.pending_approvals = stage.pending_approvals
                    self.state.agents[key]["status"] = "running"
                    self.state.status = "running"
                    yield f"Etapa iniciada: {self.state.agents[key]['name']}."
                    async for message in stage.events():
                        self.thread_id = stage.thread_id
                        self.pending_approvals = stage.pending_approvals
                        self.changed_files.update(stage.changed_files)
                        yield f"{self.state.agents[key]['name']}: {message}"
                        if self.cancel_requested:
                            break
                except asyncio.CancelledError:
                    if stage is not None:
                        try:
                            await stage.cancel()
                        except Exception:
                            pass
                    self.cancel_requested = True
                    terminal = "interrupted"
                    failure = ""
                except Exception:
                    terminal = "failed"
                    failure = "Não foi possível iniciar ou executar esta etapa."

                if stage is not None:
                    try:
                        await stage.close()
                    except asyncio.CancelledError:
                        self.cancel_requested = True
                        terminal = "interrupted"
                    except Exception:
                        terminal = "failed"
                        failure = "Não foi possível encerrar esta etapa."
                elif not failure:
                    terminal = "failed"
                    failure = "Não foi possível iniciar esta etapa."

                if stage is not None and terminal is None:
                    terminal = stage.state.status
                if terminal not in ("completed", "failed", "interrupted"):
                    terminal = "failed"
                    failure = "A etapa terminou sem estado terminal reconhecido."
                if self.cancel_requested:
                    terminal = "interrupted"
                self.state.agents[key]["status"] = terminal
                agent = self.state.agents[key]
                agent["result"] = failure or (getattr(stage.state, "error", "") if stage else "") or (
                    stage.state.final[:180] if stage and terminal == "completed" else "")
                if stage is not None:
                    stage_state = stage.state
                    for target, source in (("requested_model", "requested_model"),
                                           ("requested_effort", "requested_effort"),
                                           ("observed_model", "observed_model"),
                                           ("observed_effort", "observed_effort"),
                                           ("observed_route_reason", "observed_route_reason")):
                        value = getattr(stage_state, source, None)
                        if isinstance(value, str):
                            agent[target] = value
                    for metric in ("input_tokens", "output_tokens", "cached_input_tokens"):
                        value = getattr(stage.state, metric, None)
                        if type(value) is int and value >= 0:
                            agent[metric] = value
                if key == "planning" and terminal != "completed":
                    self.state.agents["implementation"]["status"] = "blocked" if terminal == "failed" else "cancelled"
                if terminal == "failed":
                    self.state.error = failure or (getattr(stage.state, "error", "") if stage else "Etapa não iniciada.")
                self.active_run = None
                self.pending_approvals = {}
                outcome = {"completed": "concluída", "failed": "falhou", "interrupted": "cancelada"}.get(terminal, terminal)
                yield f"Etapa {outcome}: {self.state.agents[key]['name']}."
                if terminal != "completed":
                    break
            statuses = [self.state.agents[k]["status"] for k in ("planning", "implementation")]
            if self.cancel_requested:
                for agent in self.state.agents.values():
                    if agent["status"] == "planned":
                        agent["status"] = "cancelled"
                statuses = [self.state.agents[k]["status"] for k in ("planning", "implementation")]
            self.state.status = ("interrupted" if self.cancel_requested or "interrupted" in statuses or "cancelled" in statuses else
                                 "failed" if "failed" in statuses or "blocked" in statuses else "completed" if statuses == ["completed", "completed"] else "failed")
            last = self.implementation if self.implementation else self.planning
            if last:
                self.state.final = last.state.final
                if not self.state.error:
                    self.state.error = getattr(last.state, "error", "")
            # Stage-local usage is exposed on each agent. A total would double-count
            # provider-reported cumulative usage across the two app-server threads.
            self.state.input_tokens = self.state.output_tokens = self.state.cached_input_tokens = None
            self.state.finish_timing()
        except asyncio.CancelledError:
            try:
                await self.cancel()
            except Exception:
                pass
            for key, agent in self.state.agents.items():
                if agent["status"] in ("planned", "running"):
                    agent["status"] = "cancelled"
            self.active_run = None
            self.pending_approvals = {}
            self.state.status = "interrupted"
            self.state.finish_timing()
            raise

    async def cancel(self):
        self.cancel_requested = True
        if self.active_run:
            stage = self.active_run
            try:
                await stage.cancel()
            finally:
                # Orchestrated stages are disposable; close promptly even when
                # interrupt does not produce turn/completed.
                await stage.close()

    async def decide_approval(self, request_id: str, decision: str):
        if not self.active_run:
            return False
        return await self.active_run.decide_approval(request_id, decision)

    async def close(self):
        if self.active_run:
            await self.active_run.close()


def create_orchestrated_run(cwd: str, planning_prompt: str, original_task: str, *, codex=None,
                            process_factory=None, run_factory: Callable = AppServerRun) -> OrchestratedRun:
    return OrchestratedRun(cwd, original_task, planning_prompt, run_factory, codex, process_factory)


@dataclass
class OrchestratedResult:
    planning: AppServerRun
    implementation: AppServerRun | None


async def run_orchestrated(cwd: str, planning_prompt: str, original_task: str,
                           *, run_factory: Callable = AppServerRun) -> OrchestratedResult:
    """Compatibility helper for the original non-streaming executor API."""
    run = create_orchestrated_run(cwd, planning_prompt, original_task, run_factory=run_factory)
    async for _ in run.events():
        pass
    return OrchestratedResult(run.planning, run.implementation)
