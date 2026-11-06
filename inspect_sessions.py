#!/usr/bin/env python3
"""Inspect explicitly selected Codex JSONL histories without exposing content."""

import argparse
import json
import os
from pathlib import Path
import re
import sys


TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
SPAWN_NAMES = {"spawn_agent", "functions.spawn_agent", "collaboration.spawn_agent"}


def token(value):
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else None


def obj(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, dict) else {}


def settings(payload):
    effort = payload.get("effort", payload.get("reasoning_effort"))
    return {
        "model": token(payload.get("model")),
        "effort": effort if isinstance(effort, str) and effort in EFFORTS else None,
    }


def inspect(path):
    """Return allowlisted metadata only; never return paths or raw records."""
    sessions, edges = {}, {}
    counts = dict(files=0, invalid_lines=0, read_errors=0,
                  files_without_session=0, skipped_symlinks=0)

    def session(sid):
        return sessions.setdefault(sid, dict(
            id=sid, metadata_present=False, roles=[], observed_settings=[]))

    def role(sid, name):
        roles = session(sid)["roles"]
        if name not in roles:
            roles.append(name)

    def edge(parent, child, evidence, requested=None):
        if parent == child:
            return
        role(parent, "coordinator")
        role(child, "subagent")
        item = edges.setdefault((parent, child), dict(
            parent_id=parent, child_id=child, evidence=[], requested_settings=[]))
        if evidence not in item["evidence"]:
            item["evidence"].append(evidence)
        if requested and any(requested.values()) and requested not in item["requested_settings"]:
            item["requested_settings"].append(requested)

    def files():
        if path.is_symlink():
            counts["skipped_symlinks"] += 1
        elif path.is_file():
            yield path
        elif path.is_dir():
            def walk_error(_):
                counts["read_errors"] += 1
            for root, dirs, names in os.walk(path, onerror=walk_error, followlinks=False):
                for name in dirs[:]:
                    if (Path(root) / name).is_symlink():
                        dirs.remove(name)
                        counts["skipped_symlinks"] += 1
                for name in sorted(names):
                    candidate = Path(root) / name
                    if candidate.suffix == ".jsonl":
                        if candidate.is_symlink():
                            counts["skipped_symlinks"] += 1
                        elif candidate.is_file():
                            yield candidate
        else:
            counts["read_errors"] += 1

    for file in files():
        counts["files"] += 1
        current, calls = None, {}
        history_start = None
        found_session = False
        try:
            with file.open("rb") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError):
                        counts["invalid_lines"] += 1
                        continue
                    if not isinstance(record, dict):
                        counts["invalid_lines"] += 1
                        continue
                    payload = obj(record.get("payload"))
                    kind = record.get("type")
                    if history_start is not None:
                        # The initial header owns this file. Earlier ordinals are
                        # copied parent history, including a parent session_meta.
                        ordinal = record.get("ordinal")
                        if (kind == "session_meta" or type(ordinal) is not int
                                or ordinal < history_start):
                            continue
                    if kind == "session_meta":
                        current = token(payload.get("id"))
                        calls = {}
                        if current is None:
                            continue
                        start = payload.get("subagent_history_start_ordinal")
                        if type(start) is int and start >= 0:
                            history_start = start
                        found_session = True
                        session(current)["metadata_present"] = True
                        source = payload.get("source")
                        if isinstance(source, str) and source in {"cli", "exec", "vscode"}:
                            role(current, "main")
                        subagent = obj(obj(source).get("subagent"))
                        spawn = obj(subagent.get("thread_spawn"))
                        if isinstance(source, dict) and "subagent" in source:
                            role(current, "subagent")
                        parent = token(spawn.get("parent_thread_id"))
                        if parent:
                            edge(parent, current, "session_meta.source.subagent.thread_spawn")
                    elif current and kind == "turn_context":
                        observed = settings(payload)
                        values = session(current)["observed_settings"]
                        if any(observed.values()) and observed not in values:
                            values.append(observed)
                    elif current and kind == "response_item":
                        call_id = token(payload.get("call_id"))
                        if not call_id:
                            continue
                        if payload.get("type") == "function_call":
                            # Overwriting prevents reusing a previous call's identity.
                            calls.pop(call_id, None)
                            name = payload.get("name")
                            if isinstance(name, str) and name in SPAWN_NAMES:
                                calls[call_id] = settings(obj(payload.get("arguments")))
                        elif payload.get("type") == "function_call_output" and call_id in calls:
                            requested = calls.pop(call_id)
                            child = token(obj(payload.get("output")).get("agent_id"))
                            if child:
                                edge(current, child, "spawn_agent.result", requested)
        except OSError:
            counts["read_errors"] += 1
        if not found_session:
            counts["files_without_session"] += 1

    for item in sessions.values():
        item["roles"].sort()
        item["observed_settings"].sort(key=lambda s: (s["model"] or "", s["effort"] or ""))
    for item in edges.values():
        item["evidence"].sort()
        item["requested_settings"].sort(key=lambda s: (s["model"] or "", s["effort"] or ""))
    return dict(sessions=[sessions[key] for key in sorted(sessions)],
                delegations=[edges[key] for key in sorted(edges)], diagnostics=counts)


def render_tree(result, root_id=None):
    """Render allowlisted session metadata as a bounded, iterative tree."""
    sessions = {item["id"]: item for item in result["sessions"]}
    children = {sid: [] for sid in sessions}
    referenced = set()
    for relation in result["delegations"]:
        parent, child = relation["parent_id"], relation["child_id"]
        children.setdefault(parent, []).append(child)
        referenced.add(child)
        children.setdefault(child, [])
    for values in children.values():
        values.sort()

    if root_id is not None:
        if root_id not in sessions:
            raise ValueError("ID de sessão raiz inexistente")
        reachable, pending = set(), [root_id]
        while pending:
            sid = pending.pop()
            if sid in reachable:
                continue
            reachable.add(sid)
            pending.extend(children.get(sid, ()))
        sessions = {sid: sessions[sid] for sid in reachable if sid in sessions}
        result = dict(result, delegations=[relation for relation in result["delegations"]
                      if relation["parent_id"] in reachable and relation["child_id"] in reachable])

    def label(sid):
        session = sessions.get(sid)
        if session is None:
            return f"{sid} [sessão referenciada sem metadados]"
        settings = session["observed_settings"]
        if settings:
            observed = ", ".join(
                f"modelo={item['model'] or '?'} esforço={item['effort'] or '?'}"
                for item in settings)
        else:
            observed = "modelo=? esforço=?"
        missing = " [metadados ausentes]" if not session["metadata_present"] else ""
        return f"{sid} [{observed}]{missing}"

    roots = [root_id] if root_id is not None else [sid for sid in sorted(sessions) if sid not in referenced]
    # A pure cycle has no root. Starting its least ID makes it visible; the
    # visited set below renders its back edge once and cannot recurse forever.
    if root_id is None:
        roots.extend(sid for sid in sorted(sessions) if sid in referenced)
    lines = []
    expanded = set()
    for root in roots:
        if root in expanded:
            continue
        lines.append(label(root))
        expanded.add(root)
        stack = []
        descendants = children.get(root, [])
        for index in range(len(descendants) - 1, -1, -1):
            last = index == len(descendants) - 1
            stack.append((descendants[index], "", "└── " if last else "├── "))
        while stack:
            sid, prefix, connector = stack.pop()
            lines.append(prefix + connector + label(sid))
            if sid in expanded:
                lines[-1] += " (ciclo ou referência repetida)"
                continue
            expanded.add(sid)
            descendants = children.get(sid, [])
            for index in range(len(descendants) - 1, -1, -1):
                last = index == len(descendants) - 1
                stack.append((descendants[index], prefix + ("    " if connector == "└── " else "│   "),
                              "└── " if last else "├── "))
    lines.append("")
    lines.append(f"Resumo: {len(sessions)} sessões, {len(result['delegations'])} delegações")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path, help="JSONL file or directory (recursive)")
    parser.add_argument("--format", choices=("json", "tree"), default="json",
                        help="output format (default: json)")
    parser.add_argument("--root", dest="root_id", help="show this session and reachable descendants (tree only)")
    args = parser.parse_args(argv)
    if args.root_id is not None and args.format != "tree":
        parser.error("--root exige --format tree")
    result = inspect(args.history)
    if args.format == "tree":
        if args.root_id is not None and args.root_id not in {s["id"] for s in result["sessions"]}:
            print("Erro: ID de sessão raiz inexistente", file=sys.stderr)
            return 2
        print(render_tree(result, args.root_id))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    diagnostics = result["diagnostics"]
    return int(not diagnostics["files"] or any(
        diagnostics[key] for key in ("invalid_lines", "read_errors", "files_without_session")))


if __name__ == "__main__":
    sys.exit(main())
