from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.utils.config import get_settings


@dataclass
class CachedUserRecord:
    user_id: str
    payload: dict[str, Any]
    dirty: bool = False
    touched_at: float = field(default=0.0)


class UserMemoryCache:
    """LRU cache for active users. Disk is touched only on initial hydrate or async flush."""

    def __init__(self) -> None:
        settings = get_settings()
        memory_cfg = settings.get("memory", {})
        self._capacity = int(memory_cfg.get("lru_cache_size", 256))
        self._data_dir = Path(memory_cfg.get("data_dir", "data/memory"))
        self._cache: OrderedDict[str, CachedUserRecord] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, user_id: str) -> dict[str, Any]:
        async with self._lock:
            if user_id in self._cache:
                record = self._cache.pop(user_id)
                self._cache[user_id] = record
                return dict(record.payload)

        payload = await asyncio.to_thread(self._load_from_disk, user_id)
        async with self._lock:
            self._put_locked(user_id, payload, dirty=False)
        return dict(payload)

    async def upsert(self, user_id: str, payload: dict[str, Any], dirty: bool = True) -> None:
        async with self._lock:
            self._put_locked(user_id, payload, dirty=dirty)

    async def list_known_user_ids(self, limit: int = 256) -> list[str]:
        max_limit = max(1, int(limit))
        async with self._lock:
            cached_ids = list(self._cache.keys())

        disk_ids = await asyncio.to_thread(self._list_user_ids_from_disk)
        seen: set[str] = set()
        out: list[str] = []

        for user_id in cached_ids + disk_ids:
            uid = str(user_id).strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            out.append(uid)
            if len(out) >= max_limit:
                break
        return out

    async def flush_dirty(self) -> int:
        async with self._lock:
            dirty_records = [(user_id, dict(record.payload)) for user_id, record in self._cache.items() if record.dirty]
            for record in self._cache.values():
                record.dirty = False

        for user_id, payload in dirty_records:
            await asyncio.to_thread(self._write_to_disk, user_id, payload)
        return len(dirty_records)

    def _put_locked(self, user_id: str, payload: dict[str, Any], dirty: bool) -> None:
        if user_id in self._cache:
            self._cache.pop(user_id)
        self._cache[user_id] = CachedUserRecord(user_id=user_id, payload=dict(payload), dirty=dirty)
        while len(self._cache) > self._capacity:
            _, evicted = self._cache.popitem(last=False)
            if evicted.dirty:
                self._write_to_disk(evicted.user_id, evicted.payload)

    def _user_file(self, user_id: str) -> Path:
        safe_user_id = ''.join(ch if ch.isalnum() or ch in ('_', '-') else '_' for ch in user_id)
        return self._data_dir / f"{safe_user_id}.json"

    def _load_from_disk(self, user_id: str) -> dict[str, Any]:
        path = self._user_file(user_id)
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _write_to_disk(self, user_id: str, payload: dict[str, Any]) -> None:
        path = self._user_file(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)

    def _list_user_ids_from_disk(self) -> list[str]:
        if not self._data_dir.exists():
            return []
        out: list[str] = []
        for path in self._data_dir.glob("*.json"):
            if path.is_file():
                out.append(path.stem)
        return out


user_memory_cache = UserMemoryCache()
