"""Version-aware LRU cache for option Greeks valuation."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from real_time_greeks_engine.models import PositionGreeksResult


@dataclass(frozen=True)
class GreeksCacheKey:
    """Composite version key ensuring stale market or position versions never collide."""

    position_id: str
    position_version: int
    instrument_fingerprint: str
    quantity: float
    market_version_key: str
    valuation_time_iso: str
    pricing_method: str


class VersionAwareGreeksCache:
    """Bounded LRU cache keyed by exact position, instrument, valuation time, and market versions."""

    def __init__(self, max_entries: int = 50_000, enabled: bool = True) -> None:
        self._max_entries = max_entries
        self._enabled = enabled
        self._store: OrderedDict[GreeksCacheKey, PositionGreeksResult] = OrderedDict()
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0
        self.invalidations: int = 0

    def get(self, key: GreeksCacheKey) -> PositionGreeksResult | None:
        if not self._enabled:
            self.misses += 1
            return None
        val = self._store.get(key)
        if val is None:
            self.misses += 1
            return None
        self._store.move_to_end(key)
        self.hits += 1
        return val

    def put(self, key: GreeksCacheKey, value: PositionGreeksResult) -> None:
        if not self._enabled:
            return
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)
            self.evictions += 1

    def invalidate_position(self, position_id: str) -> int:
        keys = [k for k in self._store if k.position_id == position_id]
        for k in keys:
            del self._store[k]
        self.invalidations += len(keys)
        return len(keys)

    def clear(self) -> None:
        self.invalidations += len(self._store)
        self._store.clear()

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total > 0 else 0.0
        return {
            "enabled": int(self._enabled),
            "size": len(self._store),
            "max_entries": self._max_entries,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 6),
            "evictions": self.evictions,
            "invalidations": self.invalidations,
        }
