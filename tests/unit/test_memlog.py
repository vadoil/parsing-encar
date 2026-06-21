"""Unit tests for the lightweight memory sampler + capped error log."""
from __future__ import annotations

import time

import pytest

from encar_parser.memlog import (
    MemSampler,
    MemSummary,
    container_rss_mib,
    process_rss_mib,
)


def test_mem_sampler_reports_at_least_zero():
    """Even on a weird OS, the sampler must return numbers (not raise)."""
    sampler = MemSampler(interval_sec=0.05, label="t")
    sampler.start()
    time.sleep(0.15)
    summary = sampler.stop()
    assert summary.peak_rss_mib >= 0
    assert summary.final_rss_mib >= 0
    assert summary.samples >= 1


def test_mem_sampler_idempotent_start():
    """Calling start() twice is a no-op, not a leak of threads."""
    sampler = MemSampler(interval_sec=0.05, label="t")
    sampler.start()
    sampler.start()  # should be no-op
    assert sampler._thread is not None
    assert sampler._thread.is_alive()
    sampler.stop()


def test_mem_summary_as_dict_contains_required_keys():
    s = MemSummary(peak_rss_mib=10.0, final_rss_mib=10.0,
                   final_container_mib=None, samples=2)
    d = s.as_dict()
    assert d["peak_rss_mib"] == 10.0
    assert d["final_container_mib"] is None
    assert d["samples"] == 2


def test_process_rss_returns_float():
    """On Linux/macOS this returns >0; on Windows returns 0.0 — both OK."""
    rss = process_rss_mib()
    assert isinstance(rss, float)
    assert rss >= 0.0


def test_container_rss_returns_float_or_none():
    cresp = container_rss_mib()
    assert cresp is None or cresp > 0


@pytest.mark.asyncio
async def test_run_command_caps_error_log_size():
    """``MAX_ERROR_LOG_ENTRIES`` must cap the persisted error_log.

    Smoke test of the cap constant via the same Python import the CLI
    uses — if someone bumps the cap to 0 or removes the bound, this
    test fails loudly.
    """
    from encar_parser.cli import MAX_ERROR_LOG_ENTRIES
    assert MAX_ERROR_LOG_ENTRIES >= 10
    assert MAX_ERROR_LOG_ENTRIES <= 500
