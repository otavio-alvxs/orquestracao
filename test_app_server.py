import asyncio
import gc
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app_server import AppServerRun
from orchestrated_run import create_orchestrated_run
from codex_runner import build_command
from web_operations import OperationsServer


FAKE_SERVER = r'''import json,sys
turns = 0
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': print(json.dumps({'id':i,'result':{}}),flush=True)
 elif method=='thread/start':
  assert m['params']['ephemeral'] is True and m['params']['sandbox']=='read-only' and m['params']['approvalPolicy']=='on-request'
  print(json.dumps({'id':'approval-1','method':'item/commandExecution/requestApproval','params':{'threadId':'thr-synthetic'}}),flush=True)
  approval=json.loads(sys.stdin.readline())
  assert approval=={'id':'approval-1','result':{'decision':'decline'}}
  print(json.dumps({'id':i,'result':{'thread':{'id':'thr-synthetic'}}}),flush=True)
 elif method=='turn/start':
  assert m['params']['model'] in ('gpt-6-luna','gpt-6.1-sol') and m['params']['effort'] in ('low','medium','high')
  assert isinstance(m['params']['input'][0]['text'],str)
  turns+=1; tid='turn-'+str(turns)
  print(json.dumps({'id':i,'result':{'turn':{'id':tid}}}),flush=True)
  text='answer-'+str(turns)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-other','turnId':tid,'delta':'wrong-thread'}}),flush=True)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-synthetic','turnId':'wrong-turn','delta':'wrong-turn'}}),flush=True)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-synthetic','turnId':tid,'itemId':'msg-commentary','delta':'commentary'}}),flush=True)
  print(json.dumps({'method':'item/completed','params':{'threadId':'thr-synthetic','turnId':tid,'item':{'id':'msg-commentary','type':'agentMessage','text':'commentary'}}}),flush=True)
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thr-synthetic','turnId':tid,'itemId':'msg-final','delta':text}}),flush=True)
  print(json.dumps({'method':'item/completed','params':{'threadId':'thr-synthetic','turnId':tid,'item':{'id':'msg-final','type':'agentMessage','text':text}}}),flush=True)
  if turns == 1: print(json.dumps({'method':'item/completed','params':{'threadId':'thr-synthetic','turnId':tid,'item':{'type':'collabAgentToolCall','senderThreadId':'thr-synthetic','id':'call-1','tool':'spawnAgent','receiverThreadIds':['agent-real'],'agentsStates':{'agent-real':{'status':'completed'}},'model':'model-real','reasoningEffort':'high'}}}),flush=True)
  for total in ([10,20] if turns == 1 else [23,24]):
   print(json.dumps({'method':'thread/tokenUsage/updated','params':{'threadId':'thr-synthetic','turnId':tid,'tokenUsage':{'total':{'inputTokens':total,'outputTokens':total,'cachedInputTokens':total}}}}),flush=True)
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

    async def test_process_creation_failure_is_safe(self):
        async def factory(*args, **kwargs):
            raise OSError("SECRET /private/path credential")
        self.run = AppServerRun(str(self.root), "SECRET prompt", codex="fake",
                                process_factory=factory)
        events = await collect(self.run)
        self.assertEqual(self.run.state.status, "failed")
        self.assertEqual(self.run.state.error,
                         "Não foi possível criar o processo Codex app-server.")
        self.assertEqual(events[-1], self.run.state.error)
        self.assertNotIn("SECRET", repr(events))

    async def test_startup_rpc_failures_identify_stage_safely(self):
        stages = {
            "initialize": "Não foi possível inicializar a conexão RPC com o Codex app-server.",
            "thread/start": "Não foi possível criar a thread do Codex app-server.",
            "turn/start": "Não foi possível iniciar o turno do Codex app-server.",
        }
        for stage, expected in stages.items():
            for failure in ("error", "closed", "malformed"):
                with self.subTest(stage=stage, failure=failure):
                    script = r'''import json,sys
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if i is None: continue
 if method==STAGE:
  if FAILURE=='closed': break
  response=({'error':{'message':'SECRET /private/path credential'}} if FAILURE=='error'
            else {'result':{'thread':None,'turn':{'id':42}}})
 else:
  response={'result':{'thread':{'id':'thread-test'},'turn':{'id':'turn-test'}}}
 print(json.dumps(dict(response,id=i)),flush=True)
'''
                    # An initialize result has no required fields; use transport errors there.
                    if stage == "initialize" and failure == "malformed":
                        continue
                    script = "STAGE=" + repr(stage) + "\nFAILURE=" + repr(failure) + "\n" + script
                    async def factory(*args, **kwargs):
                        return await asyncio.create_subprocess_exec(
                            sys.executable, "-u", "-c", script, **kwargs)
                    self.run = AppServerRun(str(self.root), "SECRET prompt", codex="fake",
                                            process_factory=factory)
                    try:
                        events = await asyncio.wait_for(collect(self.run), 3)
                        self.assertEqual(self.run.state.status, "failed")
                        self.assertEqual(self.run.state.error, expected)
                        self.assertEqual(events[-1], expected)
                        self.assertNotIn("SECRET", repr(events))
                    finally:
                        await self.run.close()
                        self.run = None

    async def test_handshake_ephemeral_thread_stream_and_same_thread_continuation(self):
        run = await self.make_run(" first ")
        await asyncio.wait_for(asyncio.gather(*(collect(run) for _ in [0])), 3)
        self.assertEqual(run.prompt, " first ")
        self.assertEqual(run.thread_id, "thr-synthetic")
        self.assertEqual(run.state.final, "commentary\n\nanswer-1")
        self.assertEqual((run.state.input_tokens, run.state.output_tokens, run.state.cached_input_tokens), (20, 20, 20))
        # The collab call reports requested model/effort; these are not execution telemetry.
        self.assertEqual(run.state.agents["agent-real"], {"status": "completed", "parent": "coordinator", "requested_model": "model-real", "requested_effort": "high"})
        first_started = run.state.started_at
        run.prompt = " second "
        await asyncio.wait_for(collect(run), 3)
        self.assertEqual(run.prompt, " second ")
        self.assertEqual(run.thread_id, "thr-synthetic")
        self.assertEqual(run.state.final, "commentary\n\nanswer-2")
        self.assertEqual((run.state.input_tokens, run.state.output_tokens, run.state.cached_input_tokens), (4, 4, 4))
        self.assertEqual(run.state.agents, {})
        self.assertGreater(run.state.started_at, first_started)

    async def test_unknown_failure_and_transport_close_are_safe(self):
        run = await self.make_run("first")
        # The valid synthetic protocol path also asserts that events don't duplicate deltas.
        events = await asyncio.wait_for(collect(run), 3)
        self.assertEqual(events.count("item/agentMessage/delta"), 2)
        self.assertEqual(run.state.status, "completed")

    async def test_phase_aware_planning_handoff_preserves_transcript_and_task(self):
        # Exercise the real parser and orchestrator using only a synthetic server.
        cases = (
            ("classified", {"phase": "commentary"}, {"phase": "final_answer"}, "answer-1"),
            ("legacy", {}, {}, "commentary\n\nanswer-1"),
            ("null", {"phase": None}, {"phase": None}, "commentary\n\nanswer-1"),
            ("mixed", {}, {"phase": "final_answer"}, "commentary\n\nanswer-1"),
            ("unknown", {"phase": "future_phase"}, {"phase": "final_answer"}, "commentary\n\nanswer-1"),
            ("partial_then_final", {"phase": "partial_answer"}, {"phase": "final_answer"}, "answer-1"),
            ("no_final", {"phase": "commentary"}, {"phase": "partial_answer"}, ""),
            ("empty_final", {"phase": "commentary"}, {"phase": "final_answer"}, "  "),
            ("unfinished_item", {"phase": "commentary"}, {"phase": "final_answer"}, "commentary\n\nanswer-1"),
        )
        for name, commentary, final, expected in cases:
            with self.subTest(name=name):
                script = FAKE_SERVER.replace(
                    "m['params']['sandbox']=='read-only'", "m['params']['sandbox'] in ('read-only','workspace-write')")
                script = script.replace("'text':'commentary'", "'text':'commentary',**" + repr(commentary))
                script = script.replace("'text':text", "'text':text,**" + repr(final))
                if name == "empty_final":
                    script = script.replace("text='answer-'+str(turns)", "text='  '")
                if name == "unfinished_item":
                    script = "\n".join(line for line in script.splitlines()
                                       if not ("'method':'item/completed'" in line and "'msg-final'" in line))

                async def process_factory(*args, **kwargs):
                    return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)

                stages = []
                def run_factory(*args, **kwargs):
                    stage = AppServerRun(*args, **kwargs)
                    stages.append(stage)
                    return stage

                task = "Tarefa integral\ncom decisões, riscos e verificações."
                run = create_orchestrated_run(str(self.root), "planeje", task,
                                             process_factory=process_factory, run_factory=run_factory)
                await asyncio.wait_for(collect(run), 5)
                self.assertEqual(stages[0].handoff_text, expected)
                transcript = "commentary\n\n" + ("  " if name == "empty_final" else "answer-1")
                self.assertEqual(stages[0].state.final, transcript)
                self.assertEqual(stages[0].state.input_tokens, 20)
                if expected.strip():
                    self.assertEqual(len(stages), 2)
                    self.assertEqual(stages[1].prompt,
                                     "Implemente a tarefa original seguindo o plano abaixo.\n\n"
                                     f"Tarefa original:\n{task}\n\nPlano produzido por Sol:\n{expected}")
                    self.assertEqual(run.state.status, "completed")
                else:
                    self.assertEqual(len(stages), 1)
                    self.assertEqual(run.state.agents["implementation"]["status"], "blocked")
                    self.assertEqual(run.state.status, "failed")
                # The Central continues to expose the original aggregate response.
                app = OperationsServer([self.root])
                app.session.run = run
                self.assertEqual(app._snapshot()["result"], run.state.final)
                self.assertEqual(run.state.final, stages[-1].state.final)

    async def test_handoff_phase_metadata_is_reset_on_continuation(self):
        script = FAKE_SERVER.replace("'text':'commentary'", "'text':'commentary','phase':'commentary'")
        script = script.replace("'text':text", "'text':text,'phase':('final_answer' if turns == 1 else None)")
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "first", process_factory=factory)
        await asyncio.wait_for(collect(self.run), 3)
        self.assertEqual(self.run.handoff_text, "answer-1")
        self.run.prompt = "continue"
        await asyncio.wait_for(collect(self.run), 3)
        self.assertEqual(self.run.handoff_text, "commentary\n\nanswer-2")

    async def test_usage_regression_and_unknown_baseline_are_unavailable(self):
        run = await self.make_run("Inspect")
        run.usage_baseline = (0, 0, 0)
        run._apply_usage_total({"inputTokens": 100, "outputTokens": 100, "cachedInputTokens": 10})
        self.assertEqual(run.state.input_tokens, 100)
        run._apply_usage_total({"inputTokens": 90, "outputTokens": 110, "cachedInputTokens": 9})
        self.assertEqual((run.state.input_tokens, run.state.output_tokens, run.state.cached_input_tokens), (None, None, None))
        run.usage_baseline = None
        run.usage_regressed = False
        run._apply_usage_total({"inputTokens": 110, "outputTokens": 110, "cachedInputTokens": 11})
        run._apply_usage_total(None)
        self.assertFalse(run._usage_turn_had_event)
        run._apply_usage_total({"inputTokens": 120, "outputTokens": 120, "cachedInputTokens": 12})
        self.assertEqual((run.state.input_tokens, run.state.output_tokens, run.state.cached_input_tokens), (None, None, None))

    async def test_collaboration_activity_uses_resolved_metadata_and_terminal_state(self):
        run = await self.make_run("first")
        run.thread_id, run.turn_id = "local-thread", "local-turn"
        run._observe_agents({"type": "collabAgentToolCall", "id": "call-1", "tool": "spawnAgent",
                             "senderThreadId": "local-thread", "receiverThreadIds": ["agent-thread"],
                             "status": "completed", "model": "requested-model", "reasoningEffort": "high",
                             "agentsStates": {}})
        self.assertEqual(run.state.agents["agent-thread"], {
            "status": "unknown", "activity": "Delegação observada", "parent": "coordinator",
            "requested_model": "requested-model", "requested_effort": "high"})
        run._observe_agents({"type": "subAgentActivity", "id": "activity-1", "agentThreadId": "agent-thread",
                             "kind": "started", "model": "resolved-model", "reasoningEffort": "medium",
                             "agentPath": "/root/developer"})
        run._observe_agents({"type": "subAgentActivity", "id": "activity-2", "agentThreadId": "agent-thread",
                             "kind": "completed", "startedAtMs": 300})
        run._observe_agents({"type": "subAgentActivity", "id": "activity-1", "agentThreadId": "agent-thread",
                             "kind": "started", "startedAtMs": 200})
        self.assertEqual(run.state.agents["agent-thread"], {
            "status": "completed", "activity": "Atividade concluída",
            "model": "resolved-model", "effort": "medium", "name": "developer",
            "parent": "coordinator", "requested_model": "requested-model", "requested_effort": "high"})
        run._observe_agents({"type": "subAgentActivity", "id": "late-start", "agentThreadId": "late-agent",
                             "kind": "started", "startedAtMs": 20})
        run._observe_agents({"type": "subAgentActivity", "id": "late-complete", "agentThreadId": "late-agent",
                             "kind": "completed", "startedAtMs": 10})
        self.assertEqual(run.state.agents["late-agent"]["status"], "running")
        self.assertNotIn("late-agent", run._agent_terminal)
        run._observe_agents({"type": "subAgentActivity", "id": "no-clock-complete", "agentThreadId": "no-clock-agent",
                             "kind": "completed"})
        run._observe_agents({"type": "subAgentActivity", "id": "no-clock-start", "agentThreadId": "no-clock-agent",
                             "kind": "started", "startedAtMs": 50})
        self.assertEqual(run.state.agents["no-clock-agent"]["status"], "completed")
        run._observe_agents({"type": "subAgentActivity", "id": "no-clock-interrupt", "agentThreadId": "no-clock-agent",
                             "kind": "interrupted"})
        self.assertEqual(run.state.agents["no-clock-agent"]["status"], "completed")
        run._observe_agents({"type": "subAgentActivity", "id": "late-terminal", "agentThreadId": "agent-thread",
                             "kind": "interrupted", "startedAtMs": 250})
        self.assertEqual(run.state.agents["agent-thread"]["status"], "completed")
        run._observe_agents({"type": "subAgentActivity", "id": "fresh-terminal", "agentThreadId": "agent-thread",
                             "kind": "interrupted", "startedAtMs": 325})
        self.assertEqual(run.state.agents["agent-thread"]["status"], "interrupted")
        run._observe_agents({"type": "subAgentActivity", "id": "late-start", "agentThreadId": "late-agent",
                             "kind": "started", "startedAtMs": 20})
        run._observe_agents({"type": "subAgentActivity", "id": "late-complete", "agentThreadId": "late-agent",
                             "kind": "completed", "startedAtMs": 10})
        self.assertEqual(run.state.agents["late-agent"]["status"], "running")
        self.assertNotIn("late-agent", run._agent_terminal)
        run._observe_agents({"type": "collabAgentToolCall", "id": "resume-1", "tool": "resumeAgent", "startedAtMs": 350,
                             "senderThreadId": "local-thread", "receiverThreadIds": ["agent-thread"],
                             "agentsStates": {"agent-thread": {"status": "running"}}})
        run._observe_agents({"type": "subAgentActivity", "id": "activity-3", "agentThreadId": "agent-thread",
                             "kind": "started", "startedAtMs": 400})
        self.assertEqual(run.state.agents["agent-thread"]["status"], "running")
        self.assertNotIn("result", run.state.agents["agent-thread"])
        run._observe_agents({"type": "subAgentActivity", "id": "failed", "agentThreadId": "failed-agent", "kind": "started"})
        run._observe_agents({"type": "subAgentActivity", "id": "failed-done", "agentThreadId": "failed-agent", "kind": "interrupted"})
        self.assertEqual(run.state.agents["failed-agent"]["status"], "interrupted")
        run._observe_agents({"type": "collabAgentToolCall", "id": "failed-state", "tool": "wait",
                             "senderThreadId": "local-thread", "receiverThreadIds": ["errored-agent"],
                             "agentsStates": {"errored-agent": {"status": "errored", "message": "Falha resumida"}}})
        self.assertEqual(run.state.agents["errored-agent"]["status"], "failed")
        self.assertEqual(run.state.agents["errored-agent"]["result"], "Falha resumida")
        run._observe_agents({"type": "collabAgentToolCall", "id": "shutdown-state", "tool": "wait",
                             "senderThreadId": "local-thread", "receiverThreadIds": ["shutdown-agent"],
                             "agentsStates": {"shutdown-agent": {"status": "shutdown"}}})
        self.assertEqual(run.state.agents["shutdown-agent"]["status"], "shutdown")
        run._observe_agents({"type": "collabAgentToolCall", "id": "other-parent", "tool": "spawnAgent",
                             "senderThreadId": "other-thread", "receiverThreadIds": ["foreign-agent"], "agentsStates": {}})
        self.assertNotIn("foreign-agent", run.state.agents)
        run.state.agents.clear()
        run._agent_terminal.clear()
        item = {"type": "subAgentActivity"}
        self.assertTrue(AppServerRun._collaboration_event_matches(item, "local-thread", "local-turn", "local-thread", "local-turn"))
        self.assertFalse(AppServerRun._collaboration_event_matches(item, "foreign-thread", "local-turn", "local-thread", "local-turn"))
        self.assertFalse(AppServerRun._collaboration_event_matches(item, "local-thread", None, "local-thread", "local-turn"))
        self.assertTrue(AppServerRun._collaboration_event_matches({"type": "agentMessage"}, None, None, "local-thread", "local-turn"))

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

    async def test_same_thread_continues_after_direct_cancel(self):
        script = r'''import json,sys
turns=0
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start': send({'id':i,'result':{'thread':{'id':'reusable-thread'}}})
 elif method=='turn/start':
  turns+=1; tid='reusable-turn-'+str(turns)
  send({'id':i,'result':{'turn':{'id':tid}}})
  if turns==2:
   send({'method':'item/agentMessage/delta','params':{'threadId':'reusable-thread','turnId':tid,'delta':'continued'}})
   send({'method':'turn/completed','params':{'threadId':'reusable-thread','turn':{'id':tid,'status':'completed'}}})
 elif method=='turn/interrupt':
  send({'id':i,'result':{}})
  send({'method':'turn/completed','params':{'threadId':'reusable-thread','turn':{'id':'reusable-turn-1','status':'interrupted'}}})
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "first", process_factory=factory)
        first = asyncio.create_task(collect(self.run))
        for _ in range(100):
            if self.run.turn_id:
                break
            await asyncio.sleep(.01)
        thread_id = self.run.thread_id
        await self.run.cancel()
        await asyncio.wait_for(first, 2)
        self.assertEqual(self.run.state.status, "interrupted")
        self.assertIsNone(self.run.process.returncode)

        self.run.prompt = "second"
        self.run.cancel_requested = False
        await asyncio.wait_for(collect(self.run), 2)
        self.assertEqual(self.run.thread_id, thread_id)
        self.assertEqual(self.run.state.final, "continued")
        self.assertEqual(self.run.state.status, "completed")

    async def test_cancel_before_first_events_iteration_does_not_start_codex(self):
        started = False

        async def factory(*args, **kwargs):
            nonlocal started
            started = True
            raise AssertionError("Codex não deveria ser iniciado após cancelamento")

        self.run = AppServerRun(str(self.root), "cancel before start", process_factory=factory)
        await self.run.cancel()
        await asyncio.wait_for(collect(self.run), 1)

        self.assertTrue(self.run.cancel_requested)
        self.assertFalse(started)
        self.assertIsNone(self.run.process)
        self.assertEqual(self.run.state.status, "interrupted")

    async def test_model_reroute_is_observed_from_protocol(self):
        script = r'''import json,sys
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': print(json.dumps({'id':i,'result':{}}),flush=True)
 elif method=='thread/start': print(json.dumps({'id':i,'result':{'thread':{'id':'reroute-thread'}}}),flush=True)
 elif method=='turn/start':
  print(json.dumps({'id':i,'result':{'turn':{'id':'reroute-turn'}}}),flush=True)
  print(json.dumps({'method':'model/rerouted','params':{'threadId':'reroute-thread','turnId':'reroute-turn','toModel':'observed-model','toEffort':'high','reason':'capacity'}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'reroute-thread','turn':{'id':'reroute-turn','status':'completed'}}}),flush=True)
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "reroute", process_factory=factory)
        await asyncio.wait_for(collect(self.run), 2)
        self.assertEqual((self.run.state.observed_model, self.run.state.observed_effort,
                          self.run.state.observed_route_reason),
                         ("observed-model", "high", "capacity"))

    async def test_cancel_without_interrupt_rpc_response_reaps_child(self):
        script = r'''import json,sys,time
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': print(json.dumps({'id':i,'result':{}}),flush=True)
 elif method=='thread/start': print(json.dumps({'id':i,'result':{'thread':{'id':'hang-thread'}}}),flush=True)
 elif method=='turn/start': print(json.dumps({'id':i,'result':{'turn':{'id':'hang-turn'}}}),flush=True)
 elif method=='turn/interrupt': time.sleep(30)
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "cancel", process_factory=factory)
        task = asyncio.create_task(collect(self.run))
        for _ in range(200):
            if self.run.turn_id:
                break
            await asyncio.sleep(.01)
        await asyncio.wait_for(self.run.cancel(), 4)
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.run.state.status, "interrupted")
        self.assertIsNotNone(self.run.process.returncode)

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

    async def test_rpc_send_failure_cleans_future_even_if_reader_closes(self):
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        unhandled = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        try:
            for failure in ("write", "drain", "eof_drain", "cancel", "eof_cancel"):
                with self.subTest(failure=failure):
                    self.run = AppServerRun(str(self.root), "send failure")
                    process = Mock()
                    process.stdout.readline = AsyncMock(return_value=b"")
                    error = (asyncio.CancelledError() if "cancel" in failure
                             else BrokenPipeError("transport closed"))

                    async def drain():
                        if failure.startswith("eof_"):
                            # The reader can reject the RPC before drain unwinds.
                            await self.run._read()
                        raise error

                    process.stdin.drain = drain
                    if failure == "write":
                        process.stdin.write.side_effect = error
                    self.run.process = process
                    try:
                        with self.assertRaises(type(error)) as caught:
                            await self.run._rpc("turn/interrupt")
                        self.assertIs(caught.exception, error)
                        leaked = bool(self.run._responses)
                        self.run._responses.clear()
                        gc.collect()
                        self.assertFalse(unhandled, "RPC left an unretrieved exception")
                        self.assertFalse(leaked, "RPC left a registered future")
                    finally:
                        self.run.process = None
        finally:
            loop.set_exception_handler(previous_handler)

    async def test_safe_server_request_responses_and_exec_fallback(self):
        self.assertEqual(AppServerRun._safe_request_response("item/fileChange/requestApproval"), {"decision": "decline"})
        self.assertEqual(AppServerRun._safe_request_response("item/permissions/requestApproval"), {"permissions": {}})
        self.assertEqual(AppServerRun._safe_request_response("mcpServer/elicitation/request"), {"action": "decline", "content": None})
        self.assertEqual(AppServerRun._safe_request_response("tool/requestUserInput"), {"answers": {}})
        self.assertIsNone(AppServerRun._safe_request_response("unknown/method"))
        self.assertEqual(build_command(self.root, "codex"), ["codex", "-a", "on-request", "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="medium"', "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--cd", str(self.root), "-"])

    async def test_development_mode_uses_restricted_workspace_and_one_shot_approvals(self):
        script = r'''import json,sys
def send(obj): print(json.dumps(obj),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id'); p=m.get('params',{})
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start':
  assert p['sandbox']=='workspace-write' and p['approvalPolicy']=='on-request' and p['approvalsReviewer']=='user'
  send({'id':i,'result':{'thread':{'id':'dev-thread'}}})
 elif method=='turn/start':
  assert p['sandboxPolicy']=={'type':'workspaceWrite','networkAccess':False,'writableRoots':[p['input'][0].get('cwd','')]} or p['sandboxPolicy']['type']=='workspaceWrite'
  assert p['approvalPolicy']=='on-request' and p['approvalsReviewer']=='user'
  send({'id':i,'result':{'turn':{'id':'dev-turn'}}})
  send({'id':'rpc-command','method':'item/commandExecution/requestApproval','params':{'threadId':'dev-thread','turnId':'dev-turn','itemId':'cmd-1','command':['python','-m','unittest'],'cwd':'.','reason':'Run tests'}})
  send({'id':'rpc-file','method':'item/fileChange/requestApproval','params':{'threadId':'dev-thread','turnId':'dev-turn','itemId':'file-1','reason':'Update fixture'}})
  decisions=[]
  for _ in range(2):
   answer=json.loads(sys.stdin.readline()); decisions.append((answer['id'],answer['result']['decision']))
  assert sorted(decisions)==[('rpc-command','accept'),('rpc-file','decline')]
  send({'id':'rpc-stale','method':'item/commandExecution/requestApproval','params':{'threadId':'dev-thread','turnId':'old-turn','itemId':'cmd-old','command':'git push'}})
  stale=json.loads(sys.stdin.readline()); assert stale=={'id':'rpc-stale','result':{'decision':'decline'}}
  send({'method':'item/completed','params':{'threadId':'dev-thread','turnId':'dev-turn','item':{'id':'file-1','type':'fileChange','status':'failed','changes':[{'path':'declined.txt'}]}}})
  send({'method':'item/completed','params':{'threadId':'dev-thread','turnId':'dev-turn','item':{'id':'file-2','type':'fileChange','status':'completed','changes':[{'path':'fixture.txt'}]}}})
  send({'method':'turn/completed','params':{'threadId':'dev-thread','turn':{'id':'dev-turn','status':'completed'}}})
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "edit fixture", mode="development", process_factory=factory)
        task = asyncio.create_task(collect(self.run))
        for _ in range(200):
            if len(self.run.pending_approvals) == 2:
                break
            await asyncio.sleep(.01)
        approvals = list(self.run.pending_approvals.values())
        self.assertEqual({a['type'] for a in approvals}, {'command', 'fileChange'})
        command = next(a for a in approvals if a['type'] == 'command')
        file_change = next(a for a in approvals if a['type'] == 'fileChange')
        self.assertTrue(await self.run.decide_approval(command['id'], 'accept'))
        self.assertTrue(await self.run.decide_approval(file_change['id'], 'decline'))
        self.assertFalse(await self.run.decide_approval(command['id'], 'accept'))
        await asyncio.wait_for(task, 3)
        self.assertEqual(self.run.state.status, 'completed')
        self.assertEqual(self.run.changed_files, {'fixture.txt'})

    async def test_reading_is_default_and_development_is_explicit(self):
        self.assertEqual(AppServerRun(str(self.root), 'inspect').mode, 'reading')
        with self.assertRaises(ValueError):
            AppServerRun(str(self.root), 'inspect', mode='bypass')

    async def test_prolonged_approval_wait_and_terminal_actions(self):
        script = r'''import json,os,sys
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start': send({'id':i,'result':{'thread':{'id':'wait-thread'}}})
 elif method=='turn/start':
  send({'id':i,'result':{'turn':{'id':'wait-turn'}}})
  send({'id':'approval-wait','method':'item/commandExecution/requestApproval','params':{'threadId':'wait-thread','turnId':'wait-turn','itemId':'cmd-wait','command':'synthetic'}})
 elif method=='turn/interrupt':
  send({'id':i,'result':{}})
  send({'method':'turn/completed','params':{'threadId':'wait-thread','turn':{'id':'wait-turn','status':'interrupted'}}})
 elif method=='test/disconnect': os.close(1)
 elif i=='approval-wait':
  assert m['result']['decision']==EXPECTED_DECISION
  send({'method':'turn/completed','params':{'threadId':'wait-thread','turn':{'id':'wait-turn','status':'completed'}}})
'''
        async def factory(*args, **kwargs):
            expected = 'accept' if action == 'accept' else 'decline'
            server = script.replace('EXPECTED_DECISION', repr(expected))
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", server, **kwargs)

        for action in ('accept', 'decline', 'cancel', 'exit', 'disconnect'):
            with self.subTest(action=action), patch('app_server._EVENT_IDLE_TIMEOUT', .05), patch('app_server._PROCESS_POLL_INTERVAL', .01):
                self.run = AppServerRun(str(self.root), 'wait', mode='development', codex='fake',
                                        process_factory=factory)
                pending = asyncio.Event()
                async def consume():
                    async for event in self.run.events():
                        if event == 'approval/pending':
                            pending.set()
                task = asyncio.create_task(consume())
                try:
                    await asyncio.wait_for(pending.wait(), 2)
                    approval_id = next(iter(self.run.pending_approvals))
                    # Cross several idle deadlines without any server events.
                    await asyncio.sleep(.18)
                    self.assertFalse(task.done())
                    self.assertEqual(self.run.state.status, 'running')
                    self.assertIn(approval_id, self.run.pending_approvals)
                    if action in ('accept', 'decline'):
                        self.assertFalse(await self.run.decide_approval(approval_id, 'acceptForSession'))
                        self.assertFalse(await self.run.decide_approval('unknown', 'accept'))
                        self.assertTrue(await self.run.decide_approval(approval_id, action))
                    elif action == 'cancel':
                        await self.run.cancel()
                    elif action == 'exit':
                        self.run.process.terminate()
                    else:
                        self.run.process.stdin.write(b'{"method":"test/disconnect"}\n')
                        await self.run.process.stdin.drain()
                    await asyncio.wait_for(task, 2)
                    expected = ('completed' if action in ('accept', 'decline') else
                                'interrupted' if action == 'cancel' else 'failed')
                    self.assertEqual(self.run.state.status, expected)
                    self.assertFalse(self.run.pending_approvals)
                    self.assertFalse(await self.run.decide_approval(approval_id, 'accept'))
                    if action == 'disconnect':
                        self.assertIsNone(self.run.process.returncode)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await self.run.close()

    async def test_idle_wait_checks_health_and_keeps_timeout_without_approval(self):
        run = AppServerRun(str(self.root), 'synthetic')
        run.process = Mock(returncode=None)
        run.process.stdin.is_closing.return_value = False
        run._reader = Mock()
        run._reader.done.return_value = False
        with patch('app_server._EVENT_IDLE_TIMEOUT', .03), patch('app_server._PROCESS_POLL_INTERVAL', .005):
            with self.assertRaises(asyncio.TimeoutError):
                await run._next_notification()
            run.pending_approvals['synthetic'] = {}
            waiting = asyncio.create_task(run._next_notification())
            try:
                await asyncio.sleep(.1)
                self.assertFalse(waiting.done())
                run.pending_approvals.clear()
                await asyncio.sleep(.08)
                self.assertTrue(waiting.done())
                with self.assertRaises(asyncio.TimeoutError):
                    await waiting
            finally:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            run.pending_approvals['synthetic'] = {}
            for failure in ('process', 'reader', 'stdin'):
                with self.subTest(failure=failure):
                    run.process.returncode = 1 if failure == 'process' else None
                    run._reader.done.return_value = failure == 'reader'
                    run.process.stdin.is_closing.return_value = failure == 'stdin'
                    with self.assertRaises(RuntimeError):
                        await asyncio.wait_for(run._next_notification(), .2)

    async def test_cancel_with_pending_approval_declines_and_invalidates_it(self):
        script = r'''import json,sys
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start': send({'id':i,'result':{'thread':{'id':'cancel-thread'}}})
 elif method=='turn/start':
  send({'id':i,'result':{'turn':{'id':'cancel-turn'}}})
  send({'id':'approval-wait','method':'item/commandExecution/requestApproval','params':{'threadId':'cancel-thread','turnId':'cancel-turn','itemId':'cmd-wait','command':'pytest'}})
 elif method=='turn/interrupt':
  send({'id':i,'result':{}})
  send({'method':'turn/completed','params':{'threadId':'cancel-thread','turn':{'id':'cancel-turn','status':'interrupted'}}})
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "wait", mode="development", process_factory=factory)
        task = asyncio.create_task(collect(self.run))
        for _ in range(200):
            if self.run.pending_approvals:
                break
            await asyncio.sleep(.01)
        approval_id = next(iter(self.run.pending_approvals))
        await self.run.cancel()
        await asyncio.wait_for(task, 3)
        self.assertFalse(self.run.pending_approvals)
        self.assertFalse(await self.run.decide_approval(approval_id, "accept"))
        self.assertEqual(self.run.state.status, "interrupted")

    async def test_transport_loss_invalidates_pending_approval(self):
        script = r'''import json,sys
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start': send({'id':i,'result':{'thread':{'id':'lost-thread'}}})
 elif method=='turn/start':
  send({'id':i,'result':{'turn':{'id':'lost-turn'}}})
  send({'id':'approval-lost','method':'item/commandExecution/requestApproval','params':{'threadId':'lost-thread','turnId':'lost-turn','itemId':'cmd-lost','command':'make check'}})
  break
'''
        async def factory(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", script, **kwargs)
        self.run = AppServerRun(str(self.root), "wait", mode="development", process_factory=factory)
        await asyncio.wait_for(collect(self.run), 3)
        self.assertFalse(self.run.pending_approvals)
        self.assertEqual(self.run.state.status, "failed")

    async def test_completion_event_invalidates_request_before_event_consumer_catches_up(self):
        class Pipe:
            def __init__(self): self.writes = []
            def is_closing(self): return False
            def write(self, data): self.writes.append(json.loads(data))
            async def drain(self): pass
        class Process:
            def __init__(self): self.stdin = Pipe()
        run = AppServerRun(str(self.root), "task", mode="development")
        run.process = Process()
        run.thread_id, run.turn_id, run.state.status = "thread", "turn", "running"
        await run._approval_request("rpc-1", "item/commandExecution/requestApproval", {
            "threadId": "thread", "turnId": "turn", "itemId": "item", "command": "make test"})
        self.assertEqual(len(run.pending_approvals), 1)
        approval_id = next(iter(run.pending_approvals))
        await run._mark_turn_ended({"threadId": "thread", "turn": {"id": "turn", "status": "completed"}})
        self.assertFalse(run.pending_approvals)
        self.assertFalse(await run.decide_approval(approval_id, "accept"))
        self.assertEqual(run.process.stdin.writes[-1], {"id": "rpc-1", "result": {"decision": "decline"}})

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
