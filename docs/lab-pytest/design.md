# Python Lab API and pytest fixture

> **Status:** Accepted

## 1. Requirements: what and why

Python test suites need to start the two-node CSP lab, use its diagnostics, and reliably remove it after a test run. Today the CLI offers these operations through one fixed Docker Compose project and a global state file. That works for an interactive lab but makes two test processes collide and gives a library caller no ownership boundary.

The first slice will provide a synchronous `Lab` context manager and an opt-in `csp_lab` pytest fixture. They use the existing packaged Compose file, image matching the installed package, and Pydantic result models. They support CSP v1 or v2 with the existing nodes 2 and 3. Custom nodes, topology files, fault scenarios, HIL, and concurrent calls on one `Lab` instance belong to later work. Accepting an unsupported option silently is forbidden.

**INV-1:** Each library-owned lab has a distinct Compose project, and its cleanup touches only that project. The CLI's existing `csp-lab` project and state file keep their current behavior.

**INV-2:** Startup and normal context exit attempt cleanup even when a probe, test body, or Docker operation fails.

## 2. User experience

```python
from csp_lab import Lab

with Lab(protocol=2) as lab:
    result = lab.ping(2)
    assert result.reachable
    print(result.rtt_ms)
```

`Lab(protocol=1)` selects CSP v1. `Lab(protocol=2, build=True)` builds from a source checkout with the libcsp submodule, like the CLI. Calling `ping`, `topology`, or `diagnose` before entering or after leaving the context raises a clear `RuntimeError`. Invalid protocols and ping addresses fail before a Docker probe. A ping to a nonresponding node returns `PingResult(reachable=False)`; an invalid probe response or failed Docker command raises an error. The result field remains `reachable`, matching the published CLI JSON model.

In a pytest suite that installs `csp-lab` in its test environment:

```python
import pytest

@pytest.mark.csp_lab(protocol=2)
def test_node_responds(csp_lab):
    assert csp_lab.ping(2).reachable
```

An unmarked `csp_lab` fixture uses protocol 2, the published image, and session sharing. Tests using the fixture share one lab per session and configuration by default, saving image startup time. `@pytest.mark.csp_lab(protocol=1)` gets a different lab. `@pytest.mark.csp_lab(scope="function")` gets a fresh lab for that test. A test marker overrides a class or module marker; two markers at the same level are an error. Only keyword arguments `protocol` (exact integer 1 or 2), `build` (boolean), and `scope` (`"session"` or `"function"`) are accepted. Invalid values and unsupported keys such as `topology` or `scenario` fail clearly rather than being ignored. The fixture checks Docker before startup and skips with the Docker error when the CLI, Compose plugin, or daemon is unavailable. Image pull, build, and node readiness failures fail the test, because those are lab failures.

## 3. Technical design and choices

`Lab` owns one randomly named `csp-lab-py-<16 lowercase hex digits>` Compose project for its lifetime and stores its protocol, image, build setting, and entered state in memory. It does not read or write the CLI state file. Its `__enter__` starts the hub and nodes, then checks both with CSP ping before returning. Its methods pass the instance's project and image to the existing Compose and probe functions. `__exit__` calls Compose `down --remove-orphans` exactly for that project, including when the body raises. Re-entry after exit is rejected; create another `Lab` instead. A local build uses the deterministic tag `csp-lab-native:<project>` so concurrent builds do not overwrite a shared tag. Normal exit and startup failure remove that exact tag after stopping the project, including when cleanup has to report an error. Published images and the CLI's local image are never removed by library cleanup.

Add `csp-lab cleanup <project>` for recovery. It accepts only an explicit project name matching that exact prefix and hex format, and calls Compose `down --remove-orphans` with the packaged Compose file; it never selects the CLI project. It also removes `csp-lab-native:<project>` if that tag exists. Cleanup errors include this exact command and the project name. After a killed pytest worker, the user can list project names with `docker ps -a --filter label=com.docker.compose.project --format '{{.Label "com.docker.compose.project"}}' | sort -u` and run `csp-lab cleanup <project>` for each `csp-lab-py-` project. If containers were already removed but an image tag remains, `docker image ls csp-lab-native --format '{{.Tag}}'` reveals its project suffix for the same cleanup command. No registry survives process termination.

Extract the shared start, probe, and stop mechanics from `docker.py` so the CLI and `Lab` use the same code while retaining separate ownership. Preserve the CLI's fixed project, persistent state, old-checkout cleanup, and JSON interface. Generate a project identifier with enough randomness to avoid collisions across pytest workers and independent processes. No process-wide singleton or extra persistent registry is needed.

The package registers a `pytest11` entry point pointing to a small plugin module. The plugin imports pytest only when pytest loads it; ordinary CLI and library use do not require pytest. Offer a `pytest` optional dependency extra for users who want an explicit install target. Register the `csp_lab` marker and provide a function-scoped fixture backed by a session-scoped registry. The registry creates at most one shared `Lab` per `(protocol, build)` pair and closes all owned labs at session end. The `scope="function"` marker creates and closes a separate instance around that test. Each pytest-xdist worker has its own registry and unique Compose project names. The fixture does not adopt or stop a lab started by the CLI.

If startup fails, the library attempts project cleanup and then raises the startup error. If context-body and cleanup both fail, the body error stays primary; cleanup failure and the recovery command are written to stderr with reporting errors suppressed. This must not use Python warnings, which pytest can turn into exceptions. A cleanup failure without a body error raises a cleanup error. Session registry cleanup runs during pytest teardown, when no single test-body exception is active; its failure is a teardown error with the project name and recovery command. Docker checks use bounded `docker info` and `docker compose version` calls; only an unavailable executable, Compose plugin, or daemon causes a skip. The library itself raises these errors without pytest-specific skip behavior.

The main cost of isolated projects is one set of containers per distinct configuration or function-scoped test. Session sharing limits this cost; callers that need isolation request it explicitly.

## 4. Acceptance and proof

| ID | Done when | How to check |
| --- | --- | --- |
| AC-1 | `Lab` starts CSP v1 and v2, returns the existing result models, and cleans up after normal exit. | Docker integration tests for both protocols, including `ping`, `topology`, and `diagnose`; assert project removal. |
| AC-2 | Invalid protocol, address, or lifecycle use fails clearly without probing another lab. | Unit tests with a fake Docker command and an independently running CLI lab. |
| AC-3 | A failed start or body error attempts cleanup; cleanup failures remain visible without replacing the body error, including under pytest `-W error`. | Unit tests covering failed Compose up, failed readiness ping, body exception, failed Compose down, and stderr reporting; pytester teardown error case. |
| AC-4 | Pytest reuses a session lab for matching markers, separates different protocols, isolates `scope="function"`, and applies marker precedence and defaults. | A pytester suite that records Compose project names and start/stop calls for unmarked, module, class, and test markers. |
| AC-5 | The fixture skips when Docker or Compose is unavailable, fails on image or readiness errors, and rejects unsupported or malformed marker options. | Pytester cases with fake Docker outcomes and marker arguments. |
| AC-6 | Installation without pytest supports CLI and `Lab`; installation with pytest discovers the fixture automatically. | Wheel installation smoke checks in isolated environments, plus plugin discovery through its entry point. |
| AC-7 | Orphaned projects and their local image tags can be found and removed without the original Python process. | Kill a `build=True` fixture process after startup, list its project via Docker label or image tag, and remove it with `csp-lab cleanup <project>` from another directory; verify the CLI project remains. |
| INV-1 | Library cleanup never stops the CLI lab or another `Lab` project. | Concurrent-project integration test and fake Docker command log assertions. |
| INV-2 | Owned resources, including project-specific local image tags, are cleaned up on startup, test, and shutdown failures when Docker permits it. | Failure-path tests from AC-3 and AC-5, checking each owned project; repeated `build=True` function-scope tests leave no project image tags. |

## 5. Open questions

None for this first slice. Topology and scenario marker arguments will be designed with those features.
