import asyncio
import unittest
from types import SimpleNamespace

from orchestrated_run import run_orchestrated, create_orchestrated_run
from task_routing import SOL, LUNA


class FakeRun:
    def __init__(self, cwd, prompt, **kwargs):
        self.cwd, self.prompt, self.kwargs = cwd, prompt, kwargs
        self.route_override = None
        self.state = SimpleNamespace(status="starting", final="plano útil")
        self.closed = False
        self.pending_approvals = {"approval": {"id": "approval"}}
        self.changed_files = set()
        self.thread_id = "fake-thread"
        self.cancelled = False
        self.state.input_tokens = self.kwargs.get("input_tokens")
        self.state.output_tokens = self.kwargs.get("output_tokens")
        self.state.cached_input_tokens = self.kwargs.get("cached_input_tokens")

    async def events(self):
        if self.kwargs.get("event_error"):
            self.state.status = "running"
            raise RuntimeError("simulated event failure")
        self.state.status = self.kwargs.get("finish", "completed")
        yield "done"

    async def close(self):
        self.closed = True
        if self.kwargs.get("close_error"):
            raise RuntimeError("simulated close failure")

    async def cancel(self):
        self.cancelled = True

    async def decide_approval(self, request_id, decision):
        return request_id == "approval" and decision in ("accept", "decline")


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_is_per_stage_and_orchestration_total_stays_unknown(self):
        def factory(*args, **kwargs):
            kwargs.update(input_tokens=10, output_tokens=4, cached_input_tokens=2)
            run = FakeRun(*args, **kwargs)
            run.state.requested_model = "resolved-request"
            run.state.requested_effort = "medium"
            run.state.observed_model = "rerouted-model"
            run.state.observed_effort = "high"
            run.state.observed_route_reason = "capacity"
            return run
        run = create_orchestrated_run(".", "planeje", "implemente", run_factory=factory)
        [message async for message in run.events()]
        self.assertEqual(run.state.agents["planning"]["input_tokens"], 10)
        self.assertEqual(run.state.agents["implementation"]["output_tokens"], 4)
        implementation = run.state.agents["implementation"]
        self.assertEqual((implementation["requested_model"], implementation["requested_effort"]),
                         ("resolved-request", "medium"))
        self.assertEqual((implementation["observed_model"], implementation["observed_effort"],
                          implementation["observed_route_reason"]),
                         ("rerouted-model", "high", "capacity"))
        self.assertIsNone(run.state.input_tokens)
        self.assertIsNone(run.state.output_tokens)
        self.assertIsNone(run.state.cached_input_tokens)

    async def test_stage_start_execution_and_close_exceptions_are_terminal(self):
        scenarios = (
            ("start", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("start")), "failed", "blocked"),
            ("events", lambda *args, **kwargs: FakeRun(*args, event_error=True, **kwargs), "failed", "blocked"),
            ("close", lambda *args, **kwargs: FakeRun(*args, close_error=True, **kwargs), "failed", "blocked"),
        )
        for name, factory, planning_status, implementation_status in scenarios:
            with self.subTest(name=name):
                run = create_orchestrated_run(".", "planeje", "implemente", run_factory=factory)
                [message async for message in run.events()]
                self.assertEqual(run.state.status, "failed")
                self.assertEqual(run.state.agents["planning"]["status"], planning_status)
                self.assertEqual(run.state.agents["implementation"]["status"], implementation_status)
                self.assertIsNone(run.active_run)
                self.assertEqual(run.pending_approvals, {})

    async def test_streams_both_stages_and_preserves_approval_contract(self):
        runs = []
        def factory(*args, **kwargs):
            run = FakeRun(*args, **kwargs)
            runs.append(run)
            return run
        run = create_orchestrated_run(".", "planeje", "implemente", run_factory=factory)
        self.assertEqual([a["status"] for a in run.state.agents.values()], ["planned", "planned"])
        iterator = run.events()
        first_message = await anext(iterator)
        self.assertIn("Planejamento", first_message)
        self.assertTrue(await run.decide_approval("approval", "accept"))
        messages = [message async for message in iterator]
        self.assertEqual(len(runs), 2)
        self.assertTrue(any("Planejamento" in m for m in messages))
        self.assertTrue(any("Implementação" in m for m in messages))
        self.assertEqual(run.state.status, "completed")
        self.assertEqual([a["status"] for a in run.state.agents.values()], ["completed", "completed"])
        self.assertTrue(all(r.closed for r in runs))

    async def test_cancel_forwards_to_active_stage_and_prevents_next_stage(self):
        runs = []
        def factory(*args, **kwargs):
            run = FakeRun(*args, **kwargs)
            async def events():
                run.state.status = "running"
                yield "started"
                await asyncio.sleep(0)
                run.state.status = "interrupted"
            run.events = events
            runs.append(run)
            return run
        run = create_orchestrated_run(".", "planeje", "implemente", run_factory=factory)
        iterator = run.events()
        await anext(iterator)
        await run.cancel()
        await anext(iterator)
        self.assertIn("cancelada", await anext(iterator))
        with self.assertRaises(StopAsyncIteration):
            await anext(iterator)
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].cancelled)
        self.assertEqual(run.state.status, "interrupted")

    async def test_cancelled_planning_never_constructs_implementation(self):
        runs = []
        def factory(*args, **kwargs):
            run = FakeRun(*args, **kwargs)
            released = asyncio.Event()
            async def events():
                run.state.status = "running"
                yield "started"
                await released.wait()
                run.state.status = "interrupted"
            async def cancel():
                run.cancelled = True
                released.set()
            run.cancel = cancel
            run.events = events
            runs.append(run)
            return run
        run = create_orchestrated_run(".", "planeje", "implemente", run_factory=factory)
        iterator = run.events()
        await anext(iterator)
        await run.cancel()
        await anext(iterator)
        await anext(iterator)
        self.assertEqual(run.state.agents["planning"]["status"], "interrupted")
        self.assertEqual(run.state.agents["implementation"]["status"], "cancelled")
        self.assertEqual(len(runs), 1)

    async def test_plan_is_transferred_and_processes_closed(self):
        runs = []
        def factory(*args, **kwargs):
            run = FakeRun(*args, **kwargs)
            runs.append(run)
            return run
        result = await run_orchestrated(".", "plan", "tarefa original", run_factory=factory)
        self.assertEqual(len(runs), 2)
        self.assertIs(result.planning, runs[0])
        self.assertIs(result.implementation, runs[1])
        self.assertEqual((runs[0].route_override.model, runs[0].route_override.effort), (SOL, "medium"))
        self.assertEqual((runs[1].route_override.model, runs[1].route_override.effort), (LUNA, "medium"))
        self.assertEqual([r.kwargs["mode"] for r in runs], ["reading", "development"])
        self.assertIn("tarefa original", runs[1].prompt)
        self.assertIn("plano útil", runs[1].prompt)
        self.assertTrue(all(r.closed for r in runs))

    async def test_failed_planning_prevents_implementation(self):
        runs = []
        def factory(*args, **kwargs):
            kwargs["finish"] = "failed"
            run = FakeRun(*args, **kwargs)
            runs.append(run)
            return run
        result = await run_orchestrated(".", "plan", "implement", run_factory=factory)
        self.assertEqual(result.planning.state.status, "failed")
        self.assertIsNone(result.implementation)
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].closed)

    async def test_empty_plan_prevents_implementation(self):
        runs = []
        def factory(*args, **kwargs):
            run = FakeRun(*args, **kwargs)
            run.state.final = "  \n"
            runs.append(run)
            return run
        result = await run_orchestrated(".", "plan", "task", run_factory=factory)
        self.assertIsNone(result.implementation)
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].closed)


if __name__ == "__main__":
    unittest.main()
