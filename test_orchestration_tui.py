import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from orchestration_tui import OrchestrationApp
    from textual.widgets import Input, Static, TextArea
except ModuleNotFoundError as exc:
    if exc.name != 'textual':
        raise
    OrchestrationApp = None

from test_codex_runner import SUCCESS, controlled_run


@unittest.skipIf(OrchestrationApp is None, 'Textual opcional não instalado')
class UiTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_screen_directory_selection_and_empty_task(self):
        with tempfile.TemporaryDirectory() as root:
            app = OrchestrationApp()
            async with app.run_test(size=(120, 36)) as pilot:
                self.assertIn('idle', str(app.query_one('#status', Static).render()))
                self.assertIsNone(app.current_run)
                app.query_one('#project', Input).value = root
                await pilot.click('#choose')
                self.assertEqual(app.project, root)
                app.query_one('#project', Input).value = str(Path(root)/'missing')
                app.action_choose_project()
                self.assertEqual(app.project, root)
                app.query_one('#project', Input).value = root
                await app.action_start()
                self.assertIsNone(app.run_task)

    async def test_start_final_and_failed_response(self):
        with tempfile.TemporaryDirectory() as root:
            for suffix, expected in (('', 'completed'), ('sys.exit(7)', 'failed')):
                app = OrchestrationApp(root)
                with patch('orchestration_tui.CodexRun', side_effect=lambda cwd, task: controlled_run(cwd, SUCCESS + suffix, task)):
                    async with app.run_test(size=(120, 36)) as pilot:
                        app.query_one('#task', TextArea).load_text('analise')
                        # Drive the same keyboard shortcut available in the terminal.
                        await pilot.press('f5')
                        await asyncio.wait_for(app.run_task, 5)
                        self.assertEqual(app.current_run.state.status, expected)
                        self.assertIn('resultado', app.query_one('#result', TextArea).text)
                        summary = str(app.query_one('#status', Static).render())
                        self.assertIn('duração', summary)
                        self.assertIn('indisponível', summary)
                        self.assertIn('Resultado: resultado', summary)
                        if expected == 'failed':
                            self.assertIn('7', app.query_one('#result', TextArea).text)
                        self.assertIsNotNone(app.current_run.process.returncode)

    async def test_status_summary_fits_compact_terminal(self):
        with tempfile.TemporaryDirectory() as root:
            app = OrchestrationApp(root)
            response_script = SUCCESS.replace('resultado', 'Use [/]')
            with patch('orchestration_tui.CodexRun', side_effect=lambda cwd, task: controlled_run(cwd, response_script, task)):
                async with app.run_test(size=(80, 30)) as pilot:
                    app.query_one('#task', TextArea).load_text('analise')
                    await pilot.press('f5')
                    await asyncio.wait_for(app.run_task, 5)
                    status = app.query_one('#status', Static)
                    self.assertIn('Resultado: Use [/] ', str(status.render()) + ' ')
                    self.assertGreaterEqual(status.region.height, 3)
                    self.assertLessEqual(status.region.bottom, app.query_one('#events').region.y)

    async def test_immediate_cancel_and_quit_reap_process(self):
        with tempfile.TemporaryDirectory() as root:
            app = OrchestrationApp(root)
            with patch('orchestration_tui.CodexRun', side_effect=lambda cwd, task: controlled_run(cwd, 'import time; time.sleep(30)', task)):
                async with app.run_test(size=(120, 36)) as pilot:
                    app.query_one('#task', TextArea).load_text('analise')
                    await app.action_start()
                    await app.action_cancel()
                    self.assertEqual(app.current_run.state.status, 'interrupted')
                    self.assertTrue(app.run_task.done())
                    if app.current_run.process:
                        self.assertIsNotNone(app.current_run.process.returncode)
                    await app.action_start()
                    for _ in range(100):
                        if app.current_run.process:
                            break
                        await asyncio.sleep(.01)
                    await pilot.press('ctrl+q')
                self.assertTrue(app.run_task.done())
                self.assertIsNotNone(app.current_run.process.returncode)

    async def test_unmount_during_run_stops_execution(self):
        with tempfile.TemporaryDirectory() as root:
            app = OrchestrationApp(root)
            with patch('orchestration_tui.CodexRun', side_effect=lambda cwd, task: controlled_run(cwd, 'import time; time.sleep(30)', task)):
                async with app.run_test(size=(120, 36)):
                    app.query_one('#task', TextArea).load_text('analise')
                    await app.action_start()
                    for _ in range(100):
                        if app.current_run.process:
                            break
                        await asyncio.sleep(.01)
                self.assertTrue(app.run_task.done())
                self.assertIsNotNone(app.current_run.process.returncode)
