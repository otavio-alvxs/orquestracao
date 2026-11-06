"""Synthetic histories only; no access to Codex's private session directory."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from inspect_sessions import inspect, main, render_tree


def event(kind, **payload):
    return {"type": kind, "payload": payload}


def meta(sid, source="cli"):
    return event("session_meta", id=sid, source=source)


def call(cid="call-1", **args):
    return event("response_item", type="function_call", name="spawn_agent",
                 call_id=cid, arguments=json.dumps(args))


def output(cid="call-1", **result):
    return event("response_item", type="function_call_output", call_id=cid,
                 output=json.dumps(result))


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, records):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def test_confirmed_relations_settings_changes_and_read_only(self):
        self.write("parent.jsonl", [meta("parent"),
                   event("turn_context", model="gpt-6.1-sol", effort="high"),
                   call(model="gpt-6-luna", reasoning_effort="low", message="SECRET"),
                   output(agent_id="child", response="SECRET")])
        self.write("nested/child.jsonl", [meta("child", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "parent", "depth": 1}}}),
                   event("turn_context", model="gpt-6-luna", effort="medium"),
                   event("turn_context", model="gpt-6-luna", effort="high")])
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*.jsonl")}
        result = inspect(self.root)
        sessions = {s["id"]: s for s in result["sessions"]}
        self.assertEqual(sessions["parent"]["roles"], ["coordinator", "main"])
        self.assertEqual(sessions["child"]["roles"], ["subagent"])
        self.assertEqual(len(sessions["child"]["observed_settings"]), 2)
        relation, = result["delegations"]
        self.assertEqual(len(relation["evidence"]), 2)
        self.assertEqual(relation["requested_settings"], [{"model": "gpt-6-luna", "effort": "low"}])
        self.assertNotIn("SECRET", json.dumps(result))
        for path, snapshot in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)

    def test_no_inference_from_prompts_failed_or_unmatched_calls(self):
        path = self.write("single.jsonl", [meta("alone", "unknown"),
                          call(model="gpt-6-luna"), output(error="SECRET"),
                          output("unmatched", agent_id="invented"),
                          event("event_msg", message="spawn_agent agent_id=ghost SECRET"),
                          call("unfinished", model="gpt-6-luna")])
        result = inspect(path)
        self.assertEqual(result["delegations"], [])
        self.assertEqual(result["sessions"], [{"id": "alone", "metadata_present": True,
                                              "roles": [], "observed_settings": []}])

    def test_missing_child_file_does_not_confirm_requested_model(self):
        path = self.write("single.jsonl", [meta("parent"), call(model="gpt-6-luna"),
                                          output(agent_id="child")])
        child = inspect(path)["sessions"][0]
        self.assertEqual(child["id"], "child")
        self.assertFalse(child["metadata_present"])
        self.assertEqual(child["observed_settings"], [])

    def test_malformed_and_sensitive_content_never_echoed(self):
        path = self.write("SECRET.jsonl", [meta("safe"),
                          event("turn_context", model="SECRET with spaces", effort={}),
                          event("response_item", type="message", content="SECRET"),
                          event("response_item", type="function_call_output", output="SECRET"),
                          ["SECRET"]])
        with path.open("ab") as stream:
            stream.write(b'{"SECRET":\n\xff\n')
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main([str(path)])
        self.assertEqual(status, 1)
        self.assertNotIn("SECRET", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["diagnostics"]["invalid_lines"], 3)

    def test_unidentified_files_missing_input_and_symlinks(self):
        self.write("empty.jsonl", [event("turn_context", model="gpt-6-luna")])
        self.assertEqual(inspect(self.root)["diagnostics"]["files_without_session"], 1)
        self.assertEqual(inspect(self.root / "missing")["diagnostics"]["read_errors"], 1)
        (self.root / "link.jsonl").symlink_to(self.root / "empty.jsonl")
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        result = inspect(self.root)
        self.assertEqual(result["diagnostics"]["skipped_symlinks"], 2)
        self.assertEqual(result["sessions"], [])

    def test_session_boundary_resets_call_correlation(self):
        path = self.write("two.jsonl", [meta("parent"), call(), meta("other"),
                                       output(agent_id="wrong-child")])
        self.assertEqual(inspect(path)["delegations"], [])

    def test_subagent_excludes_inherited_parent_history_by_ordinal(self):
        header = meta("child", {"subagent": {"thread_spawn": {
            "parent_thread_id": "parent"}}})
        header["payload"]["subagent_history_start_ordinal"] = 5
        records = [header, meta("parent"),
                   event("turn_context", model="gpt-6.1-sol", effort="medium"),
                   call(model="gpt-6-luna", reasoning_effort="high"),
                   output(agent_id="inherited-child"),
                   event("turn_context", model="gpt-6-luna", effort="low")]
        for ordinal, record in enumerate(records):
            record["ordinal"] = ordinal
        path = self.write("child.jsonl", records)
        result = inspect(path)
        sessions = {s["id"]: s for s in result["sessions"]}
        self.assertEqual(set(sessions), {"child", "parent"})
        self.assertEqual(sessions["child"]["observed_settings"], [
            {"model": "gpt-6-luna", "effort": "low"}])
        self.assertFalse(sessions["parent"]["metadata_present"])
        self.assertEqual(sessions["parent"]["observed_settings"], [])
        relation, = result["delegations"]
        self.assertEqual(relation["parent_id"], "parent")
        self.assertEqual(relation["child_id"], "child")
        self.assertEqual(relation["requested_settings"], [])

    def test_tree_renders_confirmed_multilevel_relations_and_orphans(self):
        self.write("main.jsonl", [meta("main"),
                   event("turn_context", model="gpt-6.1-sol", effort="high"),
                   call(), output(agent_id="worker")])
        self.write("worker.jsonl", [meta("worker", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "main"}}}),
                   event("turn_context", model="gpt-6-luna", effort="low"),
                   call("nested", model="gpt-6-luna"),
                   output("nested", agent_id="leaf")])
        self.write("orphan.jsonl", [meta("orphan", "unknown")])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main([str(self.root), "--format", "tree"])
        rendered = stdout.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("main [modelo=gpt-6.1-sol esforço=high]", rendered)
        self.assertIn("worker [modelo=gpt-6-luna esforço=low]", rendered)
        self.assertIn("leaf [modelo=? esforço=?] [metadados ausentes]", rendered)
        self.assertIn("orphan [modelo=? esforço=?]", rendered)
        self.assertIn("Resumo: 4 sessões, 2 delegações", rendered)
        self.assertNotIn("SECRET", rendered)

    def test_tree_is_iterative_for_cycles_and_json_default_is_unchanged(self):
        self.write("a.jsonl", [meta("a", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "b"}}})])
        self.write("b.jsonl", [meta("b", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "a"}}})])
        result = inspect(self.root)
        self.assertEqual(len(result["delegations"]), 2)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main([str(self.root), "--format", "tree"])
        self.assertEqual(status, 0)
        self.assertIn("ciclo ou referência repetida", stdout.getvalue())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main([str(self.root)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)

    def test_tree_preserves_branch_order_and_indentation(self):
        self.write("main.jsonl", [meta("main"), call("a-call"),
                   output("a-call", agent_id="a"), call("b-call"),
                   output("b-call", agent_id="b")])
        self.write("a.jsonl", [meta("a", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "main"}}}), call("a-leaf"),
                   output("a-leaf", agent_id="a-leaf")])
        self.write("b.jsonl", [meta("b", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "main"}}}), call("b-leaf"),
                   output("b-leaf", agent_id="b-leaf")])
        self.write("a-leaf.jsonl", [meta("a-leaf", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "a"}}})])
        self.write("b-leaf.jsonl", [meta("b-leaf", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "b"}}})])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main([str(self.root), "--format", "tree"])
        self.assertEqual(stdout.getvalue(), (
            "main [modelo=? esforço=?]\n"
            "├── a [modelo=? esforço=?]\n"
            "│   └── a-leaf [modelo=? esforço=?]\n"
            "└── b [modelo=? esforço=?]\n"
            "    └── b-leaf [modelo=? esforço=?]\n"
            "\nResumo: 5 sessões, 4 delegações\n"))

    def test_tree_handles_multiple_parents_without_repeating_subtree(self):
        self.write("a.jsonl", [meta("a"), call("spawn"), output("spawn", agent_id="shared")])
        self.write("b.jsonl", [meta("b"), call("spawn"), output("spawn", agent_id="shared")])
        self.write("shared.jsonl", [meta("shared", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "a"}}})])
        rendered = render_tree(inspect(self.root))
        self.assertEqual(rendered.count("shared ["), 2)
        self.assertIn("ciclo ou referência repetida", rendered)

    def test_tree_handles_a_chain_beyond_python_recursion_limit(self):
        size = 1100
        sessions = [{"id": str(index), "metadata_present": True,
                     "observed_settings": []} for index in range(size)]
        delegations = [{"parent_id": str(index), "child_id": str(index + 1)}
                       for index in range(size - 1)]
        rendered = render_tree({"sessions": sessions, "delegations": delegations})
        self.assertTrue(rendered.startswith("0 [modelo=? esforço=?]\n"))
        self.assertIn("Resumo: 1100 sessões, 1099 delegações", rendered)

    def test_tree_never_prints_untrusted_content(self):
        path = self.write("PRIVATE-PATH/safe.jsonl", [meta("safe"),
                          call(message="SECRET PROMPT", model="SECRET MODEL"),
                          output(agent_id="child", response="SECRET RESPONSE"),
                          event("response_item", type="message", content="SECRET MESSAGE"),
                          event("response_item", type="function_call", name="read_file",
                                arguments=json.dumps({"path": "/SECRET/PATH"})),
                          event("response_item", type="function_call_output",
                                output="SECRET TOOL RESULT")])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main([str(path), "--format", "tree"])
        self.assertNotIn("SECRET", stdout.getvalue())
        self.assertIn("child [modelo=? esforço=?] [metadados ausentes]", stdout.getvalue())
        self.assertNotIn("PRIVATE-PATH", stdout.getvalue())

    def test_root_filter_excludes_ancestors_siblings_and_summarizes_subgraph(self):
        self.write("main.jsonl", [meta("main"), call("a"), output("a", agent_id="chosen"),
                   call("b"), output("b", agent_id="sibling")])
        self.write("chosen.jsonl", [meta("chosen", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "main"}}}), call("leaf"), output("leaf", agent_id="missing"),
                   call("unfinished", message="agent_id=ghost SECRET")])
        self.write("sibling.jsonl", [meta("sibling", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "main"}}})])
        self.write("unrelated.jsonl", [meta("unrelated")])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main([str(self.root), "--format", "tree", "--root", "chosen"])
        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), (
            "chosen [modelo=? esforço=?]\n"
            "└── missing [modelo=? esforço=?] [metadados ausentes]\n"
            "\nResumo: 2 sessões, 1 delegações\n"))

    def test_root_filter_handles_cycles_and_multiple_parents(self):
        self.write("a.jsonl", [meta("a"), call("x"), output("x", agent_id="shared")])
        self.write("b.jsonl", [meta("b"), call("x"), output("x", agent_id="shared")])
        self.write("shared.jsonl", [meta("shared", {"subagent": {"thread_spawn": {
                   "parent_thread_id": "a"}}}), call("back"), output("back", agent_id="a")])
        rendered = render_tree(inspect(self.root), "b")
        self.assertIn("b [", rendered)
        self.assertIn("shared [", rendered)
        self.assertIn("a [", rendered)  # Reached through the confirmed cycle.
        self.assertIn("ciclo ou referência repetida", rendered)
        self.assertIn("Resumo: 3 sessões, 3 delegações", rendered)

    def test_root_filter_shared_descendant_from_two_included_branches(self):
        self.write("root.jsonl", [meta("root"), call("a-call"), output("a-call", agent_id="a"),
                   call("b-call"), output("b-call", agent_id="b")])
        self.write("a.jsonl", [meta("a"), call("shared-call"),
                   output("shared-call", agent_id="shared")])
        self.write("b.jsonl", [meta("b"), call("shared-call"),
                   output("shared-call", agent_id="shared")])
        rendered = render_tree(inspect(self.root), "root")
        self.assertIn("shared [", rendered)
        self.assertIn("ciclo ou referência repetida", rendered)
        self.assertIn("Resumo: 4 sessões, 4 delegações", rendered)

    def test_root_cli_validation_and_missing_id_are_safe(self):
        path = self.write("safe.jsonl", [meta("safe"), call(message="SECRET"),
                                         output(agent_id="child")])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main([str(path), "--format", "tree", "--root", "absent SECRET"])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ID de sessão raiz inexistente", stderr.getvalue())
        self.assertNotIn("SECRET", stderr.getvalue())
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main([str(path), "--root", "safe"])
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn("SECRET", stderr.getvalue())

    def test_root_cli_can_select_missing_metadata_reference(self):
        path = self.write("safe.jsonl", [meta("safe"), call(message="SECRET"),
                                         output(agent_id="child", response="SECRET")])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main([str(path), "--format", "tree", "--root", "child"])
        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), (
            "child [modelo=? esforço=?] [metadados ausentes]\n\nResumo: 1 sessões, 0 delegações\n"))
        self.assertNotIn("SECRET", stdout.getvalue())

    def test_root_filter_handles_long_chain(self):
        size = 1100
        sessions = [{"id": str(index), "metadata_present": False,
                     "observed_settings": []} for index in range(size)]
        delegations = [{"parent_id": str(index), "child_id": str(index + 1)}
                       for index in range(size - 1)]
        rendered = render_tree({"sessions": sessions, "delegations": delegations}, "500")
        self.assertTrue(rendered.startswith("500 ["))
        self.assertIn("Resumo: 600 sessões, 599 delegações", rendered)


if __name__ == "__main__":
    unittest.main()
