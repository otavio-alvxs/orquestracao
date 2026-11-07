"""Read-only, process-local Codex account quota monitor."""
from __future__ import annotations

import asyncio
import json
import math
import shutil
import time
from typing import Callable


def _window(value):
    if not isinstance(value, dict):
        return None
    duration = value.get("windowDurationMins")
    used = value.get("usedPercent")
    return {"window_minutes": duration if type(duration) is int and duration > 0 else None,
            "used_percent": max(0, min(100, used)) if type(used) is int or (
                type(used) is float and math.isfinite(used)) else None,
            "resets_at": value.get("resetsAt") if type(value.get("resetsAt")) is int else None}


def _bucket(value):
    if not isinstance(value, dict):
        return None
    return {"limit_id": value.get("limitId") if isinstance(value.get("limitId"), str) else None,
            "limit_name": value.get("limitName") if isinstance(value.get("limitName"), str) else None,
            "plan_type": value.get("planType") if isinstance(value.get("planType"), str) else None,
            "primary": _window(value.get("primary")), "secondary": _window(value.get("secondary"))}


def sanitize_limits(result, previous=None):
    """Normalize a full response or sparse update, merging available buckets."""
    previous = previous or {}
    if not isinstance(result, dict):
        return dict(previous)
    raw = result.get("rateLimitsByLimitId")
    if not isinstance(raw, dict) or not raw:
        legacy = result.get("rateLimits")
        key = legacy.get("limitId") if isinstance(legacy, dict) and isinstance(legacy.get("limitId"), str) else "codex"
        raw = {key: legacy} if isinstance(legacy, dict) else {}
    buckets = {key: dict(value) for key, value in previous.get("buckets", {}).items()}
    for key, raw_bucket in raw.items():
        if not isinstance(key, str) or not isinstance(raw_bucket, dict):
            continue
        parsed = _bucket(raw_bucket)
        old = buckets.get(key, {})
        # Sparse nullable metadata must not erase previously observed metadata/windows.
        for field in ("limit_id", "limit_name", "plan_type", "primary", "secondary"):
            if parsed[field] is None:
                parsed[field] = old.get(field)
            elif field in ("primary", "secondary") and isinstance(old.get(field), dict):
                parsed[field] = {key: old[field].get(key) if value is None else value
                                 for key, value in parsed[field].items()}
        parsed["window_updated_at"] = dict(old.get("window_updated_at", {}))
        for window in ("primary", "secondary"):
            if isinstance(raw_bucket.get(window), dict):
                parsed["window_updated_at"][window] = int(time.time())
        parsed["last_updated_at"] = int(time.time())
        buckets[key] = parsed
    return {"buckets": buckets}


class RateLimitMonitor:
    POLL_SECONDS = 60

    def __init__(self, *, codex=None, process_factory: Callable | None = None, interval=60):
        self.codex = codex
        self.process_factory = process_factory or asyncio.create_subprocess_exec
        self.interval = interval
        self.process = None
        self.reader = None
        self.poller = None
        self.responses = {}
        self.next_id = 0
        self.write_lock = asyncio.Lock()
        self.snapshot = {"status": "disconnected", "updated_at": None, "buckets": {}, "stale": True}
        self.on_update = None

    async def _rpc(self, method, params=None, timeout=15):
        if method not in ("initialize", "account/rateLimits/read"):
            raise ValueError("RPC não permitido no monitor de cotas.")
        self.next_id += 1
        ident = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.responses[ident] = future
        async with self.write_lock:
            self.process.stdin.write((json.dumps({"id": ident, "method": method, "params": params or {}})+"\n").encode())
            await self.process.stdin.drain()
        try:
            response = await asyncio.wait_for(future, timeout)
        finally:
            self.responses.pop(ident, None)
        if "error" in response:
            raise RuntimeError("RPC de leitura de cotas falhou.")
        return response.get("result", {})

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                if len(line) > 4 * 1024 * 1024:
                    continue
                try:
                    msg = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(msg, dict):
                    continue
                ident = msg.get("id")
                if type(ident) is int and ident in self.responses:
                    future = self.responses[ident]
                    if not future.done():
                        future.set_result(msg)
                elif msg.get("method") == "account/rateLimits/updated":
                    params = msg.get("params")
                    if isinstance(params, dict):
                        self._merge_update(params)
        finally:
            for future in self.responses.values():
                if not future.done():
                    future.set_exception(ConnectionError("app-server desconectado"))
            self.snapshot = {**self.snapshot, "status": "disconnected", "stale": True}
            if self.on_update:
                self.on_update(dict(self.snapshot))

    def _merge_update(self, params):
        existing = self.snapshot.get("buckets", {})
        buckets = dict(existing)
        raw = params.get("rateLimits")
        if isinstance(raw, dict):
            key = raw.get("limitId") if isinstance(raw.get("limitId"), str) else "codex"
            merged = sanitize_limits({"rateLimitsByLimitId": {key: raw}}, {"buckets": buckets})
            buckets = merged["buckets"]
            self._publish(buckets, "connected")

    def _publish(self, buckets, status):
        now = int(time.time())
        buckets = {key: {**bucket, "stale": now - bucket.get("last_updated_at", now) > self.interval * 3}
                   for key, bucket in buckets.items()}
        self.snapshot = {"status": status, "updated_at": int(time.time()), "buckets": buckets, "stale": False}
        if self.on_update:
            self.on_update(dict(self.snapshot))

    async def _poll(self):
        while True:
            try:
                if self.process is None or (self.process.returncode is not None):
                    await self._connect()
                result = await self._rpc("account/rateLimits/read")
                normalized = sanitize_limits(result, {"buckets": self.snapshot.get("buckets", {})})
                self._publish(normalized["buckets"], "connected")
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._disconnect()
                self.snapshot = {**self.snapshot, "status": "disconnected", "stale": True}
                if self.on_update:
                    self.on_update(dict(self.snapshot))
            await asyncio.sleep(self.interval)

    async def _connect(self):
        executable = self.codex or shutil.which("codex")
        if not executable:
            self.snapshot.update(status="disconnected", stale=True)
            raise RuntimeError("Codex CLI indisponível")
        self.process = await self.process_factory(executable, "app-server", "--listen", "stdio://", cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        self.reader = asyncio.create_task(self._read())
        try:
            await self._rpc("initialize", {"clientInfo": {"name": "orquestracao-quota-monitor", "title": "Quota monitor", "version": "1"}})
            self.process.stdin.write(b'{"method":"initialized","params":{}}\n')
            await self.process.stdin.drain()
        except Exception:
            await self._disconnect()
            raise

    async def _disconnect(self):
        process, reader = self.process, self.reader
        self.process = self.reader = None
        if reader and reader is not asyncio.current_task() and not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if process:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except (asyncio.TimeoutError, ProcessLookupError):
                    process.kill()
                    await process.wait()

    async def start(self, cwd):
        self.cwd = cwd
        self.poller = asyncio.create_task(self._poll())

    async def close(self):
        for task in (self.poller, self.reader):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (self.poller, self.reader) if t), return_exceptions=True)
        await self._disconnect()
        self.snapshot = {**self.snapshot, "status": "disconnected", "stale": True}
