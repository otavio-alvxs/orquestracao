"""Sequential two-stage Codex app-server orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app_server import AppServerRun
from task_routing import Route, SOL, LUNA


@dataclass
class OrchestratedResult:
    planning: AppServerRun
    implementation: AppServerRun | None


async def run_orchestrated(cwd: str, planning_prompt: str, original_task: str,
                          *, run_factory: Callable = AppServerRun) -> OrchestratedResult:
    """Run planning with Sol/read-only, then implementation with Luna/write."""
    planning_input = f"{planning_prompt}\n\nTarefa original:\n{original_task}"
    planning = run_factory(cwd, planning_input, mode="reading", profile="complex")
    planning.route_override = Route("complex", SOL, "medium", "orchestration planning")
    implementation = None
    try:
        async for _ in planning.events():
            pass
        if planning.state.status != "completed" or not planning.state.final.strip():
            return OrchestratedResult(planning, None)
        implementation_prompt = (
            "Implemente a tarefa original seguindo o plano abaixo.\n\n"
            f"Tarefa original:\n{original_task}\n\nPlano produzido por Sol:\n{planning.state.final}"
        )
        implementation = run_factory(cwd, implementation_prompt, mode="development", profile="intermediate")
        implementation.route_override = Route("intermediate", LUNA, "medium", "orchestration implementation")
        async for _ in implementation.events():
            pass
        return OrchestratedResult(planning, implementation)
    finally:
        try:
            if implementation is not None:
                await implementation.close()
        finally:
            await planning.close()
