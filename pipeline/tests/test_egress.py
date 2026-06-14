"""Egress-proxy config: how MASSIVE_* / *_PROXY_URL env resolves to a proxy URL,
and that an injected test transport always bypasses the proxy (hermetic tests).
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from pipeline.providers.util import egress_proxy, make_client

_VARS = (
    "MASSIVE_PROXY_URL", "EGRESS_PROXY_URL", "MASSIVE_KEY", "MASSIVE_API_KEY",
    "MASSIVE_USERNAME", "MASSIVE_PROXY_USERNAME", "MASSIVE_USER",
    "MASSIVE_PROXY_HOST", "MASSIVE_PROXY_PORT",
)


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_unconfigured_is_none() -> None:
    assert egress_proxy() is None


def test_full_url_used_verbatim(monkeypatch) -> None:
    monkeypatch.setenv("MASSIVE_PROXY_URL", "http://u:p@example.com:1234")
    assert egress_proxy() == "http://u:p@example.com:1234"


def test_assembled_from_username_and_key(monkeypatch) -> None:
    monkeypatch.setenv("MASSIVE_KEY", "s3cr3t/key")
    monkeypatch.setenv("MASSIVE_USERNAME", "acct@x")
    expected = (
        f"http://{quote('acct@x', safe='')}:{quote('s3cr3t/key', safe='')}"
        "@network.joinmassive.com:65534"
    )
    assert egress_proxy() == expected


def test_key_without_username_is_none(monkeypatch) -> None:
    # the user set MASSIVE_KEY but no username — can't authenticate, stay direct
    monkeypatch.setenv("MASSIVE_KEY", "k")
    assert egress_proxy() is None


def test_injected_transport_bypasses_proxy(monkeypatch) -> None:
    monkeypatch.setenv("MASSIVE_PROXY_URL", "http://u:p@example.com:1234")
    client = make_client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="ok")),
        use_proxy=True,
    )
    assert client.get("https://finance.example.com").status_code == 200  # uses transport, not proxy
