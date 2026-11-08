import unittest
from types import SimpleNamespace

from orchestrated_run import run_orchestrated
from task_routing import SOL, LUNA


class FakeRun:
    def __init__(self, cwd, prompt, **kwargs):
        self.cwd, self.prompt, self.kwargs = cwd, prompt, kwargs
        self.route_override = None
        self.state = SimpleNamespace(status="starting", final="plano útil")
        self.closed = False

    async def events(self):
        self.state.status = self.kwargs.get("finish", "completed")
        yield "done"

    async def close(self):
        self.closed = True


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
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
