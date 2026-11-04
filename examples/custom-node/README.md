# Custom application node

This standalone C program links the pinned libcsp v2.1 library with the same
receive bounds guards as the lab. It answers standard CSP ping and returns ASCII
uppercase on port 10, preserving all other binary bytes. It runs as user 10001.

From the repository root:

```sh
git submodule update --init --recursive
docker build -f examples/custom-node/Dockerfile -t uppercase-node:dev .
uv run python - <<'PY'
from csp_lab import Lab, Node

for protocol in (1, 2):
    with Lab(protocol=protocol, build=True,
             nodes=(Node(address=5, image="uppercase-node:dev"),)) as lab:
        assert lab.ping(5).reachable
        assert lab.topology().nodes == [2, 3, 5]
        assert lab.request(5, port=10, payload=b"hello\x00world\xff") == b"HELLO\x00WORLD\xff"
        lab.stop_node(5)
        assert not lab.ping(5).reachable
        lab.start_node(5)
        assert lab.diagnose().healthy
PY
```

Run the same pytest tests for CSP v1 and v2 (Docker must be available):

```sh
uv run --locked pytest -q examples/custom-node/tests
```

The tests use `uppercase-node:dev` and build the managed native image from the
checkout. `test_uppercase` includes a fresh application identifier and checks the
complete transformed reply. Other tests cover binary boundaries and a
function-isolated stop/timeout/restart. The expected timeout is caught, so that
successful test produces no failure artifacts.

Build an intentionally incorrect image that still answers standard ping, then
run the **same assertion** against it:

```sh
docker build -f examples/custom-node/Dockerfile --build-arg INCORRECT=ON \
  -t uppercase-node:broken .
CSP_LAB_EXAMPLE_IMAGE=uppercase-node:broken uv run --locked pytest -q \
  examples/custom-node/tests/test_application.py::test_uppercase
```

Both protocol cases must fail the application assertion, with no setup errors or
skips. This variant echoes the input instead of uppercasing it. Its failure
bundles remain under `artifacts/csp-lab` after the containers are removed.
`CSP_LAB_EXAMPLE_IMAGE` selects an image; `CSP_LAB_EXAMPLE_BUILD=0` selects the
published native image matching the installed package instead of a local build.
Custom-node support requires csp-lab 0.3.0 or newer. Use the checkout and the
default local build when developing native changes.

You can also configure a test directly:

```python
import pytest
from csp_lab import Node

@pytest.mark.csp_lab(nodes=(Node(5, "uppercase-node:dev"),))
def test_my_application(csp_lab):
    assert csp_lab.request(5, port=10, payload=b"hello") == b"HELLO"
```

Session sharing uses protocol, native-build setting, and the normalized nodes
configuration (addresses and image references). Order does not matter; changing
an image reference or address creates a different lab. Identical configurations
retain their first-created image IDs. Rebuilt tags require a new lab/session.
For tests that stop nodes, use `scope="function"`. The nearest module/class/test
marker supplies the whole configuration; marker values are not merged, and all
marker levels are strictly validated.

`lab.request(address, *, port, payload, timeout_ms=1500)` sends one packet and
returns one raw `bytes` reply, including an empty reply. The address must name a
configured custom node, the port must be an integer in 10–63, payload must be
`bytes` of length 0–256, and timeout must be an integer in 100–10000 ms. Wrong
types, booleans in integer fields, and out-of-range values fail before a probe
starts. The service's content is returned for your assertion to check.

No reply raises `from csp_lab import RequestTimeout`, a `TimeoutError` subclass
with `address`, `port`, and `timeout_ms` attributes. A timeout does not prove that
the service did not execute the request. There are no application retries or
extra application headers. Calls on one lab are sequential. Include your own
request ID if you need correlation across timeouts and late replies:

```python
import secrets

payload = b"hello:" + secrets.token_bytes(8)
assert lab.request(5, port=10, payload=payload) == payload.upper()
```

The network deadline begins when the prepared probe sends. Docker setup and ZMQ
subscription settling add latency outside that deadline, with a separate
30-second setup budget plus the requested timeout and up to five seconds for
confirmed probe removal. Docker or malformed-probe failures raise `RuntimeError`.
Requests use hexadecimal stdin without a TTY, keeping payloads out of command
arguments and error messages. A result is returned only after its probe is gone;
uncertain cleanup disables the lab and supplies a scoped recovery command.

The optional Docker build argument `TESTING=ON` enables internal integration
instrumentation (request counts, dropped replies, and malformed frames). Normal
images expose only the uppercase service and standard CSP services.

On startup failure or an exception leaving `with Lab(...)`, diagnostics are
saved before cleanup under `artifacts/csp-lab/<project>/<failure>/`. A caught
expected `RequestTimeout` produces no bundle. `manifest.json` contains addresses,
resolved image IDs, service status, exception class, and the last operation's
target/port/timeout, without payloads or exception text. Service logs are verbatim
and may contain your application's data. Each capture has a five-second budget,
200 lines per service, and 2 MiB total; a lab creates at most 20 bundles. Capture
failure is reported without replacing your exception or preventing cleanup.
Set `artifacts_dir=None` to opt out, or a path to select another directory.
`lab.collect_artifacts()` also works while a lab is unusable after uncertain
probe cleanup. Pytest uses `--csp-lab-artifacts DIRECTORY` (`none` disables it).

`Node` preserves the image's ENTRYPOINT, CMD, and user. The lab supplies:

| Environment | Meaning |
| --- | --- |
| `CSP_LAB_PROTOCOL` | `1` or `2` |
| `CSP_LAB_ADDRESS` | Decimal unicast node address |
| `CSP_LAB_ZMQ_PUBLISH_ENDPOINT` | `tcp://hub:6000` |
| `CSP_LAB_ZMQ_SUBSCRIBE_ENDPOINT` | `tcp://hub:7000` |

Your application must match the wire version, configure a full-width interface
netmask (5 bits for v1, 14 for v2), set its default route, and answer CSP ping.
Readiness retries pings for up to 30 seconds after image preparation. Application
containers run trusted user-supplied code on the lab's private network.

Local images are used without pulling. Missing images are pulled before any lab
container starts. The lab pins resolved image IDs for its lifetime, and
`lab.diagnose().image_ids` records them. Stop/start retains the original container,
even if an image tag changes. To use a rebuilt image, create a new lab.

Custom addresses are distinct and limited to eight nodes: 1–30 for v1 or 1–16382
for v2, excluding 2, 3, and 4. Cleanup removes owned lab resources, retaining your
image. Recovery after a killed worker uses `csp-lab cleanup <project>`.
