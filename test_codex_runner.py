import asyncio
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_runner import CodexRun, RunState, build_command, parse_event, validate_project

SUCCESS = '''import sys
sys.stdin.read()
print('{"type":"thread.started"}', flush=True)
print('{"type":"turn.started"}', flush=True)
print('{"type":"item.completed","item":{"type":"agent_message","text":"resultado"}}', flush=True)
print('{"type":"turn.completed"}', flush=True)
'''


def controlled_run(root, script, prompt="analise"):
    async def factory(*args, **kwargs):
        assert 'shell' not in kwargs
        return await asyncio.create_subprocess_exec(sys.executable, '-u', '-c', script, **kwargs)
    return CodexRun(root, prompt, codex="codex-test", process_factory=factory)


async def collect(run):
    return [event async for event in run.events()]


class EventTests(unittest.TestCase):
    def test_root_item_and_coordinator_evidence(self):
        state = RunState()
        self.assertFalse(state.agents)
        parse_event('{"type":"thread.started"}', state)
        parse_event(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}), state)
        parse_event('{"type":"turn.completed"}', state)
        self.assertEqual(state.final, "ok")
        self.assertTrue(state.turn_completed)
        self.assertEqual(state.agents['coordinator']['model'], 'não disponível')
        self.assertNotIn('developer', state.agents)

    def test_usage_accepts_only_nonnegative_integers(self):
        state = RunState()
        parse_event(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 0, "output_tokens": 12, "cached_input_tokens": 3}}), state)
        self.assertEqual((state.input_tokens, state.output_tokens, state.cached_input_tokens), (0, 12, 3))
        state = RunState()
        parse_event(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": True, "output_tokens": -1, "cached_input_tokens": "4"}}), state)
        self.assertEqual((state.input_tokens, state.output_tokens, state.cached_input_tokens), (None, None, None))
        state = RunState()
        parse_event('{"type":"turn.completed"}', state)
        self.assertEqual((state.input_tokens, state.output_tokens, state.cached_input_tokens), (None, None, None))

    def test_private_diagnostics_and_unknown_events_are_not_echoed(self):
        state = RunState()
        for event in ({"type": "error", "message": "secret"}, {"type": "secret/path"},
                      {"type": []}, {"type": "item.completed", "item": None}):
            self.assertNotIn('secret', parse_event(json.dumps(event), state))
        self.assertIn('inválida', parse_event('{', state))
        self.assertIsNone(parse_event('', state))


class CommandTests(unittest.TestCase):
    def test_exact_safe_argv_and_directory_validation(self):
        with tempfile.TemporaryDirectory(prefix='project ; ') as root:
            self.assertEqual(build_command(root, '/usr/bin/codex'), [
                '/usr/bin/codex', '-a', 'on-request', 'exec', '--json', '--ephemeral',
                '--sandbox', 'read-only', '--cd', root, '-'])
            self.assertEqual(validate_project(root), Path(root))
            for value in (Path(root)/'missing', '', '\0', Path(root)/'file'):
                with self.assertRaises(ValueError):
                    validate_project(value)
            with patch('codex_runner.shutil.which', return_value=None):
                with self.assertRaises(ValueError):
                    build_command(root)


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_partial_lines_and_no_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            run = controlled_run(root, SUCCESS + 'sys.stdout.write("{\\\"type\\\":\\\"future\\\"}")')
            states = []
            async for _ in run.events():
                states.append(run.state.status)
            self.assertIn('starting', states)
            self.assertIn('running', states)
            self.assertEqual(run.state.status, 'completed')
            self.assertEqual(run.state.final, 'resultado')
            self.assertIsNotNone(run.state.duration_seconds)
            self.assertGreaterEqual(run.state.duration_seconds, 0)
            self.assertEqual(run.process.returncode, 0)
            self.assertEqual(list(Path(root).iterdir()), [])

    async def test_partial_response_and_nonzero_exit_preserve_failure(self):
        with tempfile.TemporaryDirectory() as root:
            run = controlled_run(root, SUCCESS + 'sys.stderr.write("secret" * 100000); sys.exit(7)')
            await asyncio.wait_for(collect(run), 5)
            self.assertEqual(run.state.status, 'failed')
            self.assertIsNotNone(run.state.duration_seconds)
            self.assertIn('7', run.state.error)
            self.assertNotIn('secret', run.state.error)
            self.assertEqual(run.state.final, 'resultado')

    async def test_unexpected_exit_and_native_error(self):
        with tempfile.TemporaryDirectory() as root:
            for script in (SUCCESS.replace('print(\'{"type":"turn.completed"}\', flush=True)', ''),
                           'print(\'{"type":"turn.failed","error":{"message":"secret"}}\')'):
                run = controlled_run(root, script)
                await collect(run)
                self.assertEqual(run.state.status, 'failed')

    async def test_spawn_failure_and_empty_prompt(self):
        async def fail(*args, **kwargs):
            raise FileNotFoundError('secret')
        with tempfile.TemporaryDirectory() as root:
            run = CodexRun(root, 'task', codex='missing', process_factory=fail)
            await collect(run)
            self.assertEqual(run.state.status, 'failed')
            self.assertNotIn('secret', run.state.error)
            self.assertIsNotNone(run.state.duration_seconds)
            run = controlled_run(root, SUCCESS, ' ')
            await collect(run)
            self.assertIsNone(run.process)
            self.assertEqual(run.state.status, 'failed')
            self.assertIsNotNone(run.state.duration_seconds)

    async def test_oversized_stream_stops_process(self):
        with tempfile.TemporaryDirectory() as root:
            run = controlled_run(root, 'import sys,time; sys.stdout.write("x" * 5000000); sys.stdout.flush(); time.sleep(30)')
            await asyncio.wait_for(collect(run), 6)
            self.assertEqual(run.state.status, 'failed')
            self.assertIsNotNone(run.state.duration_seconds)
            self.assertIsNotNone(run.process.returncode)

    async def test_cancel_running_reaps_group_even_if_child_ignores_term(self):
        with tempfile.TemporaryDirectory() as root:
            script = '''import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(1,flush=True); time.sleep(30)'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
child.stdout.readline()
print('{"type":"thread.started"}', flush=True)
time.sleep(30)
'''
            run = controlled_run(root, script)
            task = asyncio.create_task(collect(run))
            for _ in range(200):
                if run.state.agents:
                    break
                await asyncio.sleep(.01)
            self.assertTrue(run.state.agents)
            await run.cancel()
            await asyncio.wait_for(task, 5)
            self.assertEqual(run.state.status, 'interrupted')
            self.assertIsNotNone(run.state.duration_seconds)
            self.assertIsNotNone(run.process.returncode)
            # On Linux zombies are already terminated; no live members may remain.
            members = []
            for entry in Path('/proc').iterdir():
                if entry.name.isdecimal():
                    try:
                        stat = (entry/'stat').read_text().split(') ', 1)[1].split()
                        if int(stat[2]) == run.process.pid and stat[0] != 'Z':
                            members.append(entry.name)
                    except (OSError, ValueError, IndexError):
                        pass
            self.assertEqual(members, [])

    async def test_cancel_during_process_creation_and_before_start(self):
        with tempfile.TemporaryDirectory() as root:
            entered = asyncio.Event()
            release = asyncio.Event()
            async def delayed(*args, **kwargs):
                entered.set()
                await release.wait()
                return await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(30)', **kwargs)
            run = CodexRun(root, 'task', codex='codex', process_factory=delayed)
            task = asyncio.create_task(collect(run))
            await entered.wait()
            cancellation = asyncio.create_task(run.cancel())
            release.set()
            await asyncio.wait_for(asyncio.gather(task, cancellation), 5)
            self.assertEqual(run.state.status, 'interrupted')
            self.assertIsNotNone(run.process.returncode)
            run = controlled_run(root, SUCCESS)
            await run.cancel()
            await collect(run)
            self.assertIsNone(run.process)
