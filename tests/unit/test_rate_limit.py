import time

import pytest

from encar_parser.utils.rate_limit import RandomDelay, TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_consumes_and_refills():
    bucket = TokenBucket(capacity=2, refill_per_sec=10)
    assert await bucket.acquire() is True
    assert await bucket.acquire() is True
    # 3rd should wait a bit
    start = time.monotonic()
    assert await bucket.acquire() is True
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # had to wait for refill


@pytest.mark.asyncio
async def test_random_delay_within_range():
    rd = RandomDelay(min_sec=0.05, max_sec=0.1)
    start = time.monotonic()
    await rd.wait()
    elapsed = time.monotonic() - start
    assert 0.04 <= elapsed <= 0.2
