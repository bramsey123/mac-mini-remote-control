from __future__ import annotations

import urllib.error

import pytest

from macagent.config import NotifyConfig
from macagent.notify import NotifyError, NtfyNotifier, NullNotifier, RateLimitedNotifier, create_notifier


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, status=200, error: Exception | None = None):
        self.requests = []
        self.status = status
        self.error = error

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return FakeResponse(self.status)


def test_ntfy_request_shape():
    opener = FakeOpener()
    NtfyNotifier("https://ntfy.example/", "topic123", token="tok", opener=opener).send(
        "Title\nwith newline", "body", priority="high", tags=("warning",)
    )
    [req] = opener.requests
    assert req.full_url == "https://ntfy.example/topic123"
    assert req.get_method() == "POST"
    assert req.data == b"body"
    assert req.get_header("Title") == "Title with newline"
    assert req.get_header("Priority") == "high"
    assert req.get_header("Tags") == "warning"
    assert req.get_header("Authorization") == "Bearer tok"


def test_ntfy_without_token_sends_no_auth():
    opener = FakeOpener()
    NtfyNotifier("https://ntfy.example", "t", opener=opener).send("a", "b")
    assert opener.requests[0].get_header("Authorization") is None


@pytest.mark.parametrize(
    "opener",
    [
        FakeOpener(status=500),
        FakeOpener(error=urllib.error.URLError("down")),
        FakeOpener(error=TimeoutError()),
    ],
)
def test_ntfy_failures_raise_notify_error(opener):
    with pytest.raises(NotifyError):
        NtfyNotifier("https://x", "t", opener=opener).send("a", "b")


def test_invalid_priority_rejected():
    with pytest.raises(ValueError):
        NtfyNotifier("https://x", "t", opener=FakeOpener()).send("a", "b", priority="loud")


def test_rate_limiter_drops_repeats_per_key(notifier):
    now = [0.0]
    limited = RateLimitedNotifier(notifier, interval=60, clock=lambda: now[0])
    assert limited.send_keyed("k", "t", "m")
    assert not limited.send_keyed("k", "t", "m")
    assert limited.send_keyed("other", "t", "m")
    now[0] = 61
    assert limited.send_keyed("k", "t", "m")
    assert len(notifier.sent) == 3


def test_create_notifier(tmp_path):
    assert isinstance(create_notifier(NotifyConfig()), NullNotifier)
    token = tmp_path / "token"
    token.write_text("secret\n")
    notifier = create_notifier(NotifyConfig(backend="ntfy", topic="t", token_file=str(token)))
    assert isinstance(notifier, NtfyNotifier)
    assert notifier.token == "secret"
