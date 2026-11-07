"""Terminal interface for real, ephemeral read-only Codex tasks."""
from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static, TextArea

from codex_runner import CodexRun, validate_project


class OrchestrationApp(App):
    TITLE = "Orquestração · Codex"
    SUB_TITLE = "Execução local somente leitura"
    CSS = """
    Screen { background: #101318; color: #e4e9f1; }
    #body { height: 1fr; padding: 0 2; }
    #project-row { height: 3; }
    #project { width: 1fr; }
    #choose { width: 16; margin-left: 1; }
    #main { height: 1fr; }
    #left { width: 2fr; margin-right: 1; }
    #right { width: 1fr; min-width: 30; }
    .panel { border: round #34404f; padding: 0 1; margin-bottom: 1; }
    #task-label, .section-label { height: 1; color: #8fb8ff; }
    #task { height: 7; margin-bottom: 1; }
    #actions { height: 3; }
    #start { width: 1fr; background: #3569bd; }
    #cancel { width: 16; margin-left: 1; }
    #status { height: auto; color: #8fb8ff; }
    #events { height: 1fr; }
    #agent { height: 9; }
    #result { height: 1fr; overflow-y: auto; }
    #right-title { color: #8fb8ff; height: 2; }
    #hint { height: 2; color: #8491a2; }
    """
    BINDINGS = [Binding("ctrl+enter", "start", "Iniciar", priority=True), Binding("f5", "start", "Iniciar", show=False), Binding("ctrl+c", "cancel", "Cancelar", priority=True), Binding("ctrl+q", "quit", "Sair", priority=True)]

    def __init__(self, project: str | Path | None = None):
        super().__init__()
        self.project = str(project or Path.cwd())
        self.current_run: CodexRun | None = None
        self.run_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Projeto: {Path(self.project).name}", id="project-name", markup=False)
        with Vertical(id="body"):
            with Horizontal(id="project-row"):
                yield Input(value=self.project, placeholder="Diretório autorizado", id="project")
                yield Button("Usar diretório", id="choose")
            with Horizontal(id="main"):
                with Vertical(id="left", classes="panel"):
                    yield Label("TAREFA · modo somente leitura", id="task-label")
                    yield TextArea("", id="task", show_line_numbers=False)
                    with Horizontal(id="actions"):
                        yield Button("▶  Iniciar tarefa", id="start", variant="primary")
                        yield Button("Cancelar", id="cancel", disabled=True)
                    yield Static("Estado: idle", id="status", markup=False)
                    yield Label("EVENTOS", classes="section-label")
                    yield RichLog(id="events", auto_scroll=True, highlight=False, markup=False, wrap=True, max_lines=500)
                with Vertical(id="right", classes="panel"):
                    yield Label("AGENTES OBSERVADOS", id="right-title")
                    yield Static("Coordenador · não disponível\nModelo / esforço · não disponível\nDeveloper · não disponível\nReviewer · não disponível", id="agent")
                    yield Label("RESULTADO", classes="section-label")
                    yield TextArea("A resposta final aparecerá aqui.", id="result", read_only=True, show_line_numbers=False)
            yield Static("F5 ou Ctrl+Enter iniciar · Ctrl+C cancelar · Ctrl+Q sair", id="hint")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#task", TextArea).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "choose":
            self.action_choose_project()
        elif event.button.id == "start":
            await self.action_start()
        elif event.button.id == "cancel":
            await self.action_cancel()

    def action_choose_project(self) -> None:
        field = self.query_one("#project", Input)
        try:
            self.project = str(validate_project(field.value))
            field.value = self.project
            self.query_one("#project-name", Static).update(f"Projeto: {Path(self.project).name}")
            self.notify("Diretório selecionado.")
        except ValueError as exc:
            self.notify(str(exc), severity="error")

    async def action_start(self) -> None:
        if self.run_task and not self.run_task.done():
            return
        task = self.query_one("#task", TextArea).text
        try:
            self.project = str(validate_project(self.query_one("#project", Input).value))
            self.current_run = CodexRun(self.project, task)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        if not task.strip():
            self.notify("Escreva uma tarefa antes de iniciar.", severity="error")
            return
        self.query_one("#events", RichLog).clear()
        self.query_one("#result", TextArea).load_text("Aguardando resposta do Codex…")
        self.current_run.state.status = "starting"
        self._update_agent()
        self.query_one("#project-name", Static).update(f"Projeto: {Path(self.project).name}")
        self.query_one("#hint", Static).update("Tarefa enviada · F5 ou Ctrl+Enter iniciar · Ctrl+C cancelar · Ctrl+Q sair")
        self._status("starting")
        self.run_task = asyncio.create_task(self._consume())
        await asyncio.sleep(0)

    async def _consume(self) -> None:
        assert self.current_run is not None
        try:
            async for message in self.current_run.events():
                self.query_one("#events", RichLog).write(message)
                self._status(self.current_run.state.status)
                self._update_agent()
                self._update_result()
            self._status(self.current_run.state.status)
            self._update_agent()
            self._update_result()
        except asyncio.CancelledError:
            pass
        except Exception:
            await self.current_run.cancel()
            self.current_run.state.status = "failed"
            self._status("failed")
            self.query_one("#result", TextArea).load_text("Erro interno ao acompanhar a execução.")

    def _update_result(self) -> None:
        state = self.current_run.state
        text = state.final
        if state.error:
            text = state.error + ("\n\nResposta parcial:\n" + state.final if state.final else "")
        result = self.query_one("#result", TextArea)
        if text and result.text != text:
            result.load_text(text)

    def _status(self, status: str) -> None:
        state = self.current_run.state if self.current_run else None
        duration = "indisponível"
        if state:
            seconds = state.duration_seconds
            if seconds is None and state.started_at is not None:
                import time
                seconds = max(0.0, time.monotonic() - state.started_at)
            if seconds is not None:
                duration = f"{seconds:.1f}s"
        def metric(value: int | None) -> str:
            return str(value) if value is not None else "indisponível"
        consumption = "indisponível"
        result_summary = "Resultado: indisponível"
        if state:
            consumption = (f"tokens entrada {metric(state.input_tokens)} · saída {metric(state.output_tokens)} · "
                           f"cache {metric(state.cached_input_tokens)}")
            outcome = (state.error or state.final) if state.status == "failed" else (state.final or state.error)
            if outcome:
                result_summary = "Resultado: " + " ".join(outcome.split())[:100]
        self.query_one("#status", Static).update(
            f"Estado: {status} · duração {duration}\n{consumption}\n{result_summary}")
        self.query_one("#cancel", Button).disabled = status not in {"starting", "running"}
        self.query_one("#start", Button).disabled = status in {"starting", "running"}
        for selector in ("#task", "#project", "#choose"):
            self.query_one(selector).disabled = status in {"starting", "running"}

    def _update_agent(self) -> None:
        if not self.current_run:
            return
        coordinator = self.current_run.state.agents.get("coordinator")
        lines = [f"Coordenador · {coordinator['status'] if coordinator else 'não disponível'}",
                 "Modelo / esforço observado · não disponível", "Developer · não disponível", "Reviewer · não disponível", "Delegação · não disponível neste fluxo"]
        self.query_one("#agent", Static).update("\n".join(lines))

    async def action_cancel(self) -> None:
        if self.current_run and self.run_task and not self.run_task.done():
            await self.current_run.cancel()
            self.run_task.cancel()
            await asyncio.gather(self.run_task, return_exceptions=True)
            self._status("interrupted")
            self._update_agent()

    async def action_quit(self) -> None:
        await self.action_cancel()
        self.exit()

    async def on_unmount(self) -> None:
        # Widgets may already be removed; shutdown must not query the DOM.
        if self.current_run and self.run_task and not self.run_task.done():
            await self.current_run.cancel()
            self.run_task.cancel()
            await asyncio.gather(self.run_task, return_exceptions=True)


def main() -> None:
    OrchestrationApp().run()


if __name__ == "__main__":
    main()
