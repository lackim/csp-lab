"""Run the same assertions against a correct or deliberately broken image."""

import os
import secrets

import pytest

from csp_lab import Node, RequestTimeout

IMAGE = os.environ.get("CSP_LAB_EXAMPLE_IMAGE", "uppercase-node:dev")
BUILD = os.environ.get("CSP_LAB_EXAMPLE_BUILD", "1")
if BUILD not in ("0", "1"):
    raise ValueError("CSP_LAB_EXAMPLE_BUILD must be 0 or 1.")


def configurations(scope="session"):
    return [
        pytest.param(
            protocol,
            marks=pytest.mark.csp_lab(
                protocol=protocol,
                build=BUILD == "1",
                scope=scope,
                nodes=(Node(address=5, image=IMAGE),),
            ),
        )
        for protocol in (1, 2)
    ]


@pytest.mark.parametrize("protocol", configurations())
def test_uppercase(csp_lab, protocol):
    assert csp_lab.protocol == protocol
    # Standard ping alone cannot prove the application behaves correctly.
    assert csp_lab.ping(5).reachable
    payload = b"hello:" + secrets.token_bytes(8)
    assert csp_lab.request(5, port=10, payload=payload) == payload.upper()


@pytest.mark.parametrize("protocol", configurations())
def test_binary_boundaries(csp_lab, protocol):
    for payload in (b"", bytes(range(256))):
        assert csp_lab.request(5, port=10, payload=payload) == payload.upper()


@pytest.mark.parametrize("protocol", configurations("function"))
def test_restart(csp_lab, protocol):
    csp_lab.stop_node(5)
    with pytest.raises(RequestTimeout):
        csp_lab.request(5, port=10, payload=b"stopped", timeout_ms=100)
    assert csp_lab.ping(2).reachable
    csp_lab.start_node(5)
    payload = b"restarted:" + secrets.token_bytes(8)
    assert csp_lab.request(5, port=10, payload=payload) == payload.upper()
