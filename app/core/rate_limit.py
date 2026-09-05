"""Guest rate limiting.

Caveat worth knowing: these counters live in process memory, and the free Render
tier spins the instance down after a period of inactivity, which wipes them. The
limit is therefore a per-instance-lifetime speed bump rather than a true daily
quota — a guest who waits out a spindown starts fresh. That is an acceptable
trade for not running Redis, but the user-facing message must not claim to be
enforcing a daily cap, because it isn't.
"""

import time
from collections import OrderedDict
from typing import Dict, List

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.logger import logger
from app.core.request_ip import client_key, get_client_ip

_WINDOW_SECONDS = 24 * 60 * 60

# Bounded: the previous defaultdict grew one entry per distinct IP forever, so a
# scan could exhaust memory on a small instance.
_MAX_TRACKED_CLIENTS = 5000

_hits: "OrderedDict[str, List[float]]" = OrderedDict()


def reset_rate_limit_state() -> None:
    """Clear all counters. Used by tests."""
    _hits.clear()


def _limit_for(ip: str) -> int:
    """Look up the configured limit for an IP, falling back to the default."""
    rules: Dict[str, int] = settings.RATE_LIMIT_RULES
    if ip in rules:
        return rules[ip]
    return settings.GUEST_RATE_LIMIT


def check_rate_limit(request: Request) -> None:
    """Record a request and raise 429 if the caller is over their limit.

    Rules are matched on the real IP so operators can allowlist a specific
    address, but the counter is stored under a digest of it rather than the
    address itself.
    """
    ip = get_client_ip(request)
    limit = _limit_for(ip)
    if limit < 0:  # unlimited
        return

    key = client_key(request)
    now = time.time()

    timestamps = [t for t in _hits.get(key, []) if now - t < _WINDOW_SECONDS]

    if len(timestamps) >= limit:
        _hits[key] = timestamps
        _hits.move_to_end(key)
        logger.info("Rate limit reached for a guest client.")
        raise HTTPException(
            status_code=429,
            detail=(
                "You've reached the guest usage limit. "
                "Create a free account for unlimited access."
            ),
        )

    timestamps.append(now)
    _hits[key] = timestamps
    _hits.move_to_end(key)

    while len(_hits) > _MAX_TRACKED_CLIENTS:
        _hits.popitem(last=False)
