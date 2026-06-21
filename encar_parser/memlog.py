"""Lightweight memory sampler — log RSS at intervals during a long run.

Usage:
    from encar_parser.memlog import MemSampler
    sampler = MemSampler(interval_sec=30)
    sampler.start()
    # ... do work ...
    summary = sampler.stop()
    print(summary)

Logs each sample via structlog so it lands in encar.log. The summary
includes the peak RSS the process reached and (when available) the
container cgroup RSS.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from encar_parser.utils.log import get_logger

log = get_logger(__name__)


def process_rss_mib() -> float:
    """Process RSS in MiB via /proc/self/status (Linux). 0 on other OSes."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except FileNotFoundError:
        pass
    try:
        import psutil  # type: ignore
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def container_rss_mib() -> float | None:
    """Container RSS from cgroup (Linux). None if not in a cgroup."""
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            return int(f.read().strip()) / (1024 * 1024)
    except FileNotFoundError:
        pass
    for path in (
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
        "/sys/fs/cgroup/memory/docker/memory.usage_in_bytes",
    ):
        try:
            with open(path) as f:
                return int(f.read().strip()) / (1024 * 1024)
        except FileNotFoundError:
            continue
    return None


@dataclass(frozen=True)
class MemSummary:
    """Result of a :class:`MemSampler` run."""

    peak_rss_mib: float
    final_rss_mib: float
    final_container_mib: float | None
    samples: int

    def as_dict(self) -> dict:
        return {
            "peak_rss_mib": round(self.peak_rss_mib, 1),
            "final_rss_mib": round(self.final_rss_mib, 1),
            "final_container_mib": (
                round(self.final_container_mib, 1)
                if self.final_container_mib is not None else None
            ),
            "samples": self.samples,
        }


class MemSampler:
    """Background thread that samples RSS every ``interval_sec`` seconds.

    Cheap (one /proc read per tick). Stop with :meth:`stop` and read the
    summary.
    """

    def __init__(self, interval_sec: float = 30.0, label: str = "run") -> None:
        self.interval_sec = interval_sec
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak = 0.0
        self._samples = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"memlog-{self.label}", daemon=True
        )
        self._thread.start()

    def stop(self) -> MemSummary:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_sec + 1)
        final_rss = process_rss_mib()
        final_container = container_rss_mib()
        summary = MemSummary(
            peak_rss_mib=max(self._peak, final_rss),
            final_rss_mib=final_rss,
            final_container_mib=final_container,
            samples=self._samples,
        )
        log.info(
            "memlog_summary",
            label=self.label,
            **summary.as_dict(),
        )
        return summary

    def _run(self) -> None:
        while not self._stop.wait(self.interval_sec):
            rss = process_rss_mib()
            container = container_rss_mib()
            self._peak = max(self._peak, rss)
            self._samples += 1
            log.info(
                "memlog_tick",
                label=self.label,
                rss_mib=round(rss, 1),
                container_mib=(
                    round(container, 1) if container is not None else None
                ),
            )
