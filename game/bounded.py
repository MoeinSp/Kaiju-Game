"""A dict with a size cap, for per-player in-memory caches that would otherwise grow with
every player the bot ever saw (the bot runs for days between restarts). Past `cap` entries
the oldest quarter is dropped; callers already treat a missing key as a cache miss."""

from __future__ import annotations


class BoundedDict(dict):
    def __init__(self, cap: int):
        super().__init__()
        self.cap = cap

    def __setitem__(self, key, value) -> None:
        if key not in self and len(self) >= self.cap:
            for old in list(self)[: max(1, self.cap // 4)]:
                self.pop(old, None)
        super().__setitem__(key, value)
