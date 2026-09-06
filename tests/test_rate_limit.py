"""Rate limiting and client-IP resolution.

The bug being guarded against: keying on ``request.client.host`` meant that
behind Render's proxy every user in the world shared a single bucket.
"""

import pytest
from fastapi import HTTPException

from app.core import rate_limit
from app.core.config import settings
from app.core.rate_limit import check_rate_limit, reset_rate_limit_state
from app.core.request_ip import get_client_ip


class _Client:
    def __init__(self, host):
        self.host = host


class _Request:
    def __init__(self, headers=None, host="10.0.0.1"):
        self.headers = headers or {}
        self.client = _Client(host)


@pytest.fixture(autouse=True)
def _clean():
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()


# --- Client IP -------------------------------------------------------------


def test_client_ip_uses_the_proxy_appended_entry():
    """With one trusted proxy the real client is the last XFF entry."""
    request = _Request({"X-Forwarded-For": "203.0.113.9"})
    assert get_client_ip(request) == "203.0.113.9"


def test_client_ip_ignores_caller_supplied_entries():
    """A caller can prepend anything; taking parts[0] is the classic bypass."""
    request = _Request({"X-Forwarded-For": "1.1.1.1, 203.0.113.9"})
    assert get_client_ip(request) == "203.0.113.9"


def test_client_ip_falls_back_when_the_header_is_junk():
    request = _Request({"X-Forwarded-For": "not-an-ip"}, host="10.0.0.5")
    assert get_client_ip(request) == "10.0.0.5"


def test_client_ip_falls_back_when_there_is_no_header():
    assert get_client_ip(_Request(host="10.0.0.7")) == "10.0.0.7"


# --- Limits ----------------------------------------------------------------


def test_separate_clients_get_separate_buckets(monkeypatch):
    """The headline fix: one guest must not exhaust everyone else's quota."""
    monkeypatch.setattr(settings, "GUEST_RATE_LIMIT", 2)

    first = _Request({"X-Forwarded-For": "203.0.113.1"})
    second = _Request({"X-Forwarded-For": "203.0.113.2"})

    check_rate_limit(first)
    check_rate_limit(first)
    with pytest.raises(HTTPException) as exc:
        check_rate_limit(first)
    assert exc.value.status_code == 429

    # The second client is unaffected.
    check_rate_limit(second)
    check_rate_limit(second)


def test_limit_is_enforced_per_client(monkeypatch):
    monkeypatch.setattr(settings, "GUEST_RATE_LIMIT", 3)
    request = _Request({"X-Forwarded-For": "203.0.113.3"})
    for _ in range(3):
        check_rate_limit(request)
    with pytest.raises(HTTPException):
        check_rate_limit(request)


def test_per_ip_override_is_honoured(monkeypatch):
    monkeypatch.setattr(settings, "GUEST_RATE_LIMIT", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_RULES", {"203.0.113.4": 3})
    request = _Request({"X-Forwarded-For": "203.0.113.4"})
    for _ in range(3):
        check_rate_limit(request)
    with pytest.raises(HTTPException):
        check_rate_limit(request)


def test_negative_override_means_unlimited(monkeypatch):
    monkeypatch.setattr(settings, "GUEST_RATE_LIMIT", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_RULES", {"203.0.113.5": -1})
    request = _Request({"X-Forwarded-For": "203.0.113.5"})
    for _ in range(50):
        check_rate_limit(request)


def test_the_store_is_bounded(monkeypatch):
    """It used to be an unbounded defaultdict, so a scan could exhaust memory."""
    monkeypatch.setattr(rate_limit, "_MAX_TRACKED_CLIENTS", 10)
    monkeypatch.setattr(settings, "GUEST_RATE_LIMIT", 100)
    for octet in range(60):
        check_rate_limit(_Request({"X-Forwarded-For": f"203.0.113.{octet}"}))
    assert len(rate_limit._hits) <= 10


def test_raw_ips_are_not_kept_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "GUEST_RATE_LIMIT", 5)
    check_rate_limit(_Request({"X-Forwarded-For": "203.0.113.77"}))
    assert not any("203.0.113.77" in key for key in rate_limit._hits)
