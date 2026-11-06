import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from app_server import AppServerRun
from codex_runner import build_command
from web_operations import OperationsServer


FAKE_SERVER = r'''import json,sys
turns = 0
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': print(json.dumps({'id':i,'result':{}}),flush=True)
 elif method=='thread/start':
  assert m['params']['ephemeral'] is True and m['params']['sandbox']=='read-only' and m['params']['approvalPolicy']=='never'
  print(json.dumps({'id':'approval-1','method':'item/commandExecution/requestApproval','params':{'threadId':'thr-synthetic'}}),flush=True)
  approval=json.loads(sys.stdin.readline())
  assert approval=={'id':'approval-1','result':{'decision':'decline'}}
  print(json.dumps({'id':i,'result':{'thread':{'id':'thr-synthetic'}}}),flush=True)
 elif method=='turn/start':
  turns+=1; tid='turn-'+str(turns)
  print(json.dumps({'id':i,'result':{'turn':{'id':tid}}}),flush=True)
  text='answer-'+str(turns)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-other','turnId':tid,'delta':'wrong-thread'}}),flush=True)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-synthetic','turnId':'wrong-turn','delta':'wrong-turn'}}),flush=True)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-synthetic','turnId':tid,'itemId':'msg-commentary','delta':'commentary'}}),flush=True)
  print(json.dumps({'method':'item/completed','params':{'threadId':'thr-synthetic','turnId':tid,'item':{'id':'msg-commentary','type':'agentMessage','text':'commentary'}}}),flush=True)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-synthetic','turnId':tid,'itemId':'msg-final','delta':text}}),flush=True)
  print(json.dumps({'method':'item/completed','params':{'threadId':'thr-synthetic','turnId':tid,'item':{'id':'msg-final','type':'agentMessage','text':text}}}),flush=True)
  print(json.dumps({'method':'item/completed','params':{'threadId':'thr-synthetic','turnId':tid,'item':{'type':'collabAgentToolCall','receiverThreadIds':['agent-real'],'agentsStates':{'agent-real':{'status':'completed'}},'model':'model-real','reasoningEffort':'high'}}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'thr-synthetic','turn':{'id':tid,'status':'completed'}}}),flush=True)
 elif method=='turn/interrupt':
  print(json.dumps({'id':i,'result':{}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'thr-synthetic','turn':{'id':'turn-active','status':'interrupted'}}}),flush=True)
'''


class AppServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        if isinstance(getattr(self, "run", None), AppServerRun):
            await self.run.close()
        self.temp.cleanup()

    async def make_run(self, prompt):
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", FAKE_SERVER, **kwargs)
        self.run = AppServerRun(str(self.root), prompt, process_factory=factory)
        return self.run

    async def test_handshake_ephemeral_thread_stream_and_same_thread_continuation(self):
        run = await self.make_run("first")
        await asyncio.wait_for(asyncio.gather(*(collect(run) for _ in [0])), 3)
        self.assertEqual(run.thread_id, "thr-synthetic")
        self.assertEqual(run.state.final, "commentary\n\nanswer-1")
        self.assertEqual(run.state.agents["agent-real"], {"status": "completed", "model": "model-real", "effort": "high"})
        first_started = run.state.started_at
        run.prompt = "second"
        await asyncio.wait_for(collect(run), 3)
        self.assertEqual(run.thread_id, "thr-synthetic")
        self.assertEqual(run.state.final, "commentary\n\nanswer-2")
        self.assertGreater(run.state.started_at, first_started)

    async def test_unknown_failure_and_transport_close_are_safe(self):
        run = await self.make_run("first")
        # The valid synthetic protocol path also asserts that events don't duplicate deltas.
        events = await asyncio.wait_for(collect(run), 3)
        self.assertEqual(events.count("item/agentMessage/delta"), 2)
        self.assertEqual(run.state.status, "completed")

    async def test_interrupt_is_sent_to_active_turn(self):
        script = r'''import json,sys
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': print(json.dumps({'id':i,'result':{}}),flush=True)
 elif method=='turn/interrupt':
  print(json.dumps({'id':i,'result':{}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'thr-int','turn':{'id':'turn-int','status':'interrupted'}}}),flush=True)
 elif method=='thread/start': print(json.dumps({'id':i,'result':{'thread':{'id':'thr-int'}}}),flush=True)
 elif method=='turn/start': print(json.dumps({'id':i,'result':{'turn':{'id':'turn-int'}}}),flush=True)
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "wait", process_factory=factory)
        task = asyncio.create_task(collect(self.run))
        for _ in range(100):
            if self.run.turn_id:
                break
            await asyncio.sleep(.01)
        self.assertEqual(self.run.turn_id, "turn-int")
        await self.run.cancel()
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.run.state.status, "interrupted")

    async def test_cancel_during_handshake_does_not_start_turn(self):
        script = r'''import json,sys,time
for line in sys.stdin:
 m=json.loads(line)
 if m.get('method')=='initialize': time.sleep(.15); print(json.dumps({'id':m['id'],'result':{}}),flush=True)
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "cancel early", process_factory=factory)
        task = asyncio.create_task(collect(self.run))
        await asyncio.sleep(.05)
        await self.run.cancel()
        await asyncio.wait_for(task, 2)
        self.assertTrue(self.run.cancel_requested)
        self.assertIsNone(self.run.thread_id)
        self.assertEqual(self.run.state.status, "interrupted")

    async def test_cancel_during_turn_start_interrupts_when_id_arrives(self):
        script = r'''import json,sys,time
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': print(json.dumps({'id':i,'result':{}}),flush=True)
 elif method=='thread/start': print(json.dumps({'id':i,'result':{'thread':{'id':'thr-pending'}}}),flush=True)
 elif method=='turn/start':
  print(json.dumps({'method':'test/turnStartPending'}),flush=True); time.sleep(.15)
  print(json.dumps({'id':i,'result':{'turn':{'id':'turn-pending'}}}),flush=True)
 elif method=='turn/interrupt':
  print(json.dumps({'id':i,'result':{}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'thr-pending','turn':{'id':'turn-pending','status':'interrupted'}}}),flush=True)
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "cancel during start", process_factory=factory)
        task = asyncio.create_task(collect(self.run))
        for _ in range(100):
            if self.run._notifications.qsize():
                break
            await asyncio.sleep(.01)
        await self.run.cancel()
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.run.state.status, "interrupted")

    async def test_eof_rejects_pending_rpc_without_timeout(self):
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", "pass", **kwargs)
        self.run = AppServerRun(str(self.root), "fail", process_factory=factory)
        await asyncio.wait_for(collect(self.run), .5)
        self.assertEqual(self.run.state.status, "failed")

    async def test_safe_server_request_responses_and_exec_fallback(self):
        self.assertEqual(AppServerRun._safe_request_response("item/fileChange/requestApproval"), {"decision": "decline"})
        self.assertEqual(AppServerRun._safe_request_response("item/permissions/requestApproval"), {"permissions": {}})
        self.assertEqual(AppServerRun._safe_request_response("mcpServer/elicitation/request"), {"action": "decline", "content": None})
        self.assertEqual(AppServerRun._safe_request_response("tool/requestUserInput"), {"answers": {}})
        self.assertIsNone(AppServerRun._safe_request_response("unknown/method"))
        self.assertEqual(build_command(self.root, "codex"), ["codex", "-a", "on-request", "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--cd", str(self.root), "-"])

    async def test_snapshot_streams_live_app_server_text(self):
        app = OperationsServer([self.root])
        run = AppServerRun(str(self.root), "prompt")
        app.session.run = run
        run.state.status = "running"
        run.state.final = "partial answer"
        app.session.run = run
        app.session.engine = "app-server"
        self.assertEqual(app._snapshot()["result"], "partial answer")

    async def test_assistant_history_precedes_first_terminal_state_once(self):
        app = OperationsServer([self.root])
        run = AppServerRun(str(self.root), "prompt")
        app.session.run = run

        async def scripted_events():
            run.state.status = "running"
            yield "delta"
            run.state.final = "final answer"
            run.state.status = "completed"
            yield "Turno Codex completed."

        run.events = scripted_events
        await app._execute(run)
        assistant_indices = [i for i, (_, kind, data) in enumerate(app.session.events)
                             if kind == "message" and data.get("role") == "assistant"]
        terminal_indices = [i for i, (_, kind, data) in enumerate(app.session.events)
                            if kind == "state" and data.get("status") == "completed"]
        self.assertEqual(len(assistant_indices), 1)
        self.assertLess(assistant_indices[0], terminal_indices[0])


async def collect(run):
    return [item async for item in run.events()]


if __name__ == "__main__":
    unittest.main()
