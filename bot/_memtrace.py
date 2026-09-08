"""Opt-in memory-leak profiler. Completely inert unless KAIJU_MEMTRACE=1 is set in
the environment — then it starts tracemalloc and a daemon thread that logs, once a
minute, the current RSS plus the top allocation sites and (crucially) the *growth*
since the previous snapshot. The growth diff is what pinpoints a leak: the lines
whose retained bytes keep climbing between snapshots are the ones holding memory.

This is a temporary diagnostic. When off it imports tracemalloc lazily and starts
no thread, so it costs nothing in normal operation.
"""

import os
import re
import threading
import time


def _rss_mb() -> int:
    try:
        m = re.search(r"VmRSS:\s+(\d+)", open("/proc/self/status").read())
        return int(m.group(1)) // 1024 if m else -1
    except Exception:  # noqa: BLE001
        return -1


def _loop() -> None:
    import tracemalloc

    tracemalloc.start(20)
    time.sleep(45)  # let startup settle before the first snapshot
    prev = None
    while True:
        snap = tracemalloc.take_snapshot()
        print(f"[MEMTRACE] RSS={_rss_mb()}MB", flush=True)
        for stat in snap.statistics("lineno")[:12]:
            print(f"[MEMTRACE] TOP {stat}", flush=True)
        if prev is not None:
            print("[MEMTRACE] --- growth since previous snapshot ---", flush=True)
            for stat in snap.compare_to(prev, "lineno")[:12]:
                if stat.size_diff > 0:
                    print(f"[MEMTRACE] GROW {stat}", flush=True)
        prev = snap
        time.sleep(60)


if os.environ.get("KAIJU_MEMTRACE") == "1":
    threading.Thread(target=_loop, name="memtrace", daemon=True).start()
