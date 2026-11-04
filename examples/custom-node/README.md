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
        lab.stop_node(5)
        assert not lab.ping(5).reachable
        lab.start_node(5)
        assert lab.diagnose().healthy
PY
```

The request/reply API and pytest application examples follow in the next slice.

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
