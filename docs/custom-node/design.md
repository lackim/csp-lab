# Test a user-owned CSP application

> **Status:** Proposed for review

## 1. Requirements: what and why

A CubeSat developer needs to run their own CSP service in a repeatable environment and prove that it handles an application request correctly. The existing lab provides real libcsp communication, isolated lifecycle management, and pytest integration, but its public operations only inspect the built-in ping nodes.

This slice adds Docker images as custom nodes, a Python request/reply API, and an independent uppercase-service example. It covers `Lab` and the pytest fixture. Custom-node CLI persistence, topology files, multi-hop routing, HIL, transport fault injection, and arbitrary MCP requests are outside this slice.

**INV-1:** Each lab owns only its Compose project and native build tag. User images, another lab, and the interactive CLI remain untouched by cleanup.

**INV-2:** Application payloads are sent exactly once, without added application headers or automatic retries. Timeout does not prove that the service did not execute the request.

**INV-3:** Invalid configuration and request arguments fail before starting containers or a request probe. Oversized incoming frames are rejected before copying into managed native packet storage.

## 2. User experience

After building the example image, a caller can write:

```python
from csp_lab import Lab, Node

node = Node(address=5, image="uppercase-node:dev")
with Lab(protocol=2, nodes=(node,)) as lab:
    assert lab.request(5, port=10, payload=b"hello", timeout_ms=1500) == b"HELLO"
```

`Lab(..., build=True)` still builds only the managed native image from the checkout. Custom images are built by the user. An available local image is used as-is; a missing image is pulled before project startup. Image pull or architecture errors fail startup, clean project resources, and identify the failed image.

The existing demo nodes remain at 2 and 3, and the probe remains at 4. `nodes` adds up to eight custom nodes with distinct unicast addresses: 1–30 for CSP v1 or 1–16382 for v2, excluding 2, 3, and 4. `Node` is immutable and contains only `address` and `image`. `Lab` copies and sorts the supplied sequence by address. Empty images, duplicate/reserved addresses, wrong types, and excess nodes raise `ValueError` before Docker is invoked. Docker validates image-reference syntax during image resolution, before any project containers are created.

Each custom node must implement standard CSP ping. Startup waits up to 30 seconds for all configured nodes to answer, after image preparation completes. Ping retries are allowed during readiness. `topology()` lists configured addresses, including stopped nodes; `diagnose()` pings all configured nodes.

`request(address, *, port, payload, timeout_ms=1500) -> bytes` accepts a configured custom-node address, application port 10–63, a `bytes` payload of 0–256 bytes, and an integer timeout of 100–10000 ms. The reply is one packet containing 0–256 bytes. No reply within the network deadline raises exported `RequestTimeout`, a `TimeoutError` subclass containing the address, port, and timeout. Docker failures and malformed probe output raise `RuntimeError`. A response with incorrect application content is returned normally so the caller's assertion catches the defect.

The network timeout begins when the prepared probe sends its packet. Docker process/container startup and the current ZMQ subscription-settling delay add latency outside that deadline; the outer command has a separate 30-second setup budget plus the requested network timeout, followed by at most five seconds for probe removal. This API is for functional testing rather than precise timing measurements.

Pytest accepts the same immutable configuration:

```python
import pytest
from csp_lab import Node

@pytest.mark.csp_lab(nodes=(Node(address=5, image="uppercase-node:dev"),))
def test_uppercase(csp_lab):
    assert csp_lab.request(5, port=10, payload=b"hello") == b"HELLO"
```

Session sharing is keyed by protocol, native-build setting, and the normalized nodes tuple. Existing marker precedence and strict validation remain. Tests that stop a node use `scope="function"`. `lab.stop_node(5)` and `lab.start_node(5)` operate only on configured custom nodes; both are idempotent, and start repeats readiness checks. Unknown or built-in addresses are rejected. A failed restart raises an error while other lab nodes remain available until context cleanup.

## 3. Technical design and choices

### Container contract and ownership

Preserve each image's ENTRYPOINT, CMD, and configured user. Supply four reserved environment variables:

| Variable | Value and meaning |
| --- | --- |
| `CSP_LAB_PROTOCOL` | Decimal `1` or `2` |
| `CSP_LAB_ADDRESS` | The node's decimal unicast address |
| `CSP_LAB_ZMQ_PUBLISH_ENDPOINT` | `tcp://hub:6000`; the node's publisher connects here |
| `CSP_LAB_ZMQ_SUBSCRIBE_ENDPOINT` | `tcp://hub:7000`; the node's subscriber connects here |

The image configures matching libcsp wire version, ZMQ framing, node address, full-width interface netmask, and default route. The example demonstrates this using the pinned libcsp v2.1. Node images are trusted user-supplied executable code. No host mounts, Docker socket, published host ports, host networking, or privileged mode can be requested through `Node`.

Generate an additional Compose document with services named `custom<address>` on the project's existing private network. Keep that document for the lifetime of `Lab` and use it for startup, probes, stop/start, diagnostics, and logs. Generate structured JSON rather than interpolating user input into shell or YAML text. Existing CLI commands continue using the packaged demo configuration and state schema.

Keep the created custom containers for the context lifetime, including during stop/start, so restarting uses the original image ID even if its tag changes. Session sharing likewise retains the first-created lab. Record resolved image IDs in diagnostics; callers needing a new image create a new lab/session. Cleanup uses the packaged base configuration with `down --remove-orphans`, which can also remove custom services after a worker is killed and its temporary document is gone. Never remove user image tags.

### Request probe and native bounds

Extend the managed native executable with a request mode using explicit `csp_connect`, `csp_send`, and `csp_read`, so an empty reply remains distinguishable from timeout. Use `CSP_O_NONE`, with no RDP or automatic retransmission. Check reply source address and service port against the connection. Send raw application bytes without a token envelope. Connection filtering alone cannot distinguish a late reply after a timeout and subsequent port reuse; applications needing transaction correlation must include and validate their own request IDs. The example test includes a fresh identifier in its payload and checks the complete transformed response. Calls on one `Lab` remain sequential, as specified by the accepted [Lab design](../lab-pytest/design.md).

Transport payloads through the probe's stdin as one bounded hexadecimal line, using Compose without a TTY. Pass protocol, destination, port, and timeout as separate arguments. Return a validated internal JSON result containing either reply hex or a timeout/error status. Raw request bytes are not added to command arguments or error messages. Free buffers and close the connection on every outcome.

Every probe, including readiness pings, has a unique name derived from its project plus a random identifier. In a `finally` path after success, timeout, error, or cancellation, confirm that the one-off container is absent, forcibly removing it when necessary within five seconds. Verify project/service ownership labels before removal. Killing the Docker CLI alone is insufficient. Return a reply only after removal is confirmed. An outer deadline or unconfirmed removal raises `RuntimeError` with the project and `csp-lab cleanup <project>` recovery command; if removal cannot be confirmed, mark the `Lab` unusable for further public operations except artifact collection and context cleanup. Cancellation keeps its original exception and reports cleanup failure separately. Ordinary network `RequestTimeout` with confirmed probe removal leaves the lab usable. No failure path retries an application send. Project recovery also explicitly finds and removes its labeled one-off probe containers, including after a killed worker, without relying solely on Compose's orphan handling.

Keep the managed payload limit at the pinned build's `CSP_BUFFER_SIZE=256`. Guard every managed CSP decoding path, including the ZMQ receive driver and the hub's separate diagnostic receiver in `examples/zmqproxy.c`. Both currently copy frames before application validation. Include documented build-time bounds patches that check the version-specific minimum header size and maximum header-plus-payload size before copying, and use correctly sized `csp_packet_t` storage rather than the hub's fixed 1024-byte allocation. Apply the guards to the example build too. The source submodule revision remains pinned.

Native tests must directly prove rejection before any copy for 257-byte payloads and much larger frames, plus acceptance at 256 bytes, for both protocol headers. Use a rejection result/copy spy and destination canaries; sanitizer checks alone cannot prove the small boundary because libcsp packet storage is a shared static pool. Run AddressSanitizer as additional evidence, including the hub diagnostic path. A larger peer response is dropped, the hub and nodes remain healthy, and the request times out without a probe or hub crash.

### Failure artifacts

`Lab(..., artifacts_dir="artifacts/csp-lab")` collects diagnostics on startup failure or an exception escaping its context; `None` disables collection. The pytest plugin snapshots the lab for a failed test before teardown, including session-shared labs. A caught expected timeout does not produce artifacts. The plugin option `--csp-lab-artifacts` selects its output directory, with the same default.

Each bundle has a unique project/failure directory containing `manifest.json` and scoped service logs. The manifest records protocol, configured addresses, resolved image IDs, container status, exception class, and operation metadata such as target, port, and timeout. Do not fetch host/container environments, persist probe stdout, or add payloads, exception text, or assertion representations to collector metadata. User-service logs are captured verbatim and can contain application data; document that boundary and the opt-out. Capture at most the last 200 log lines per service and 2 MiB total, within five seconds; create at most 20 bundles per lab. Report truncation or capture failure without replacing the primary exception or delaying mandatory cleanup. Print the saved directory and retain it after cleanup; ignore `/artifacts/` in Git. PCAP capture is a later extension.

### Independent example

`examples/custom-node` contains a standalone C application, Dockerfile, pytest tests, and copyable build/run instructions. It builds against pinned libcsp, implements ping plus ASCII uppercase on port 10, and preserves binary bytes outside `a`–`z`. It does not reuse the lab's demo-server executable. Build a second intentionally incorrect image that still answers ping but returns incorrect application content, proving that the same pytest assertion catches an application regression. A function-isolated test stops and restarts its node, checks timeout and recovery, and a deliberately failing run proves artifact retention after cleanup.

## 4. Acceptance and proof

| ID | Done when | How to check |
| --- | --- | --- |
| AC-1 | A separately built application image receives the documented environment and serves correct data in CSP v1 and v2. | CI builds the example image and runs the unchanged pytest request test for both protocols. |
| AC-2 | Binary and empty payload/reply cases work; invalid addresses, types, sizes, ports, timeouts, and node configurations fail before a probe/container starts. | Native integration boundary cases plus unit command logs showing no Docker call for invalid public inputs. |
| AC-3 | Wrong application behavior fails the same assertion even though readiness ping succeeds. | CI runs the test against the intentionally incorrect image and requires an assertion failure, not a setup/transport error. |
| AC-4 | A stopped custom node times out, can restart, and does not affect other projects or demo nodes. | Function-scope integration test using stop/start; ping an independently running lab before and after. |
| AC-5 | Pytest shares identical node configurations and separates different images, addresses, protocols, and function-scoped tests. | Pytester cases recording projects and arguments, plus wheel-install entry-point smoke check. |
| AC-6 | Startup failure and failed tests preserve bounded, useful artifacts before cleanup, including session-shared tests. | Failing image/start/readiness/test cases assert manifest/log content and retained paths; simulated capture failure preserves the primary error. |
| AC-7 | Outer deadlines and cancellation remove one-off probes; uncertain removal disables further lab operations and exposes recovery instructions. | Fake-runner cases for every exit path plus a real interrupted Compose probe; verify no late send, explicit unusable-state errors, and project recovery after a killed worker with an active probe. |
| INV-1 | Cleanup and killed-worker recovery remove only owned project resources and native build tags. | Docker integration starts an unrelated lab and checks its health and user-image existence after normal, failed, and recovery flows. |
| INV-2 | One API call causes at most one application send, including timeout and malformed replies. | Instrument the example's request counter and check it after an intentionally dropped response; verify zero sends for invalid inputs. |
| INV-3 | Public limits and every managed native receive path prevent oversized copies or argument truncation. | Unit validation and direct copy/rejection/canary tests at 256, 257, and much larger payload sizes for both protocols, including the hub receiver; additional AddressSanitizer runs. |

## 5. Open questions

None blocking this slice. Broader topology and transport contracts will be designed when those features are selected.
