"""Pytest fixture for isolated CSP labs."""

from collections.abc import Generator, Iterator
from dataclasses import dataclass
from typing import cast

import pytest

from csp_lab import docker
from csp_lab.lab import Lab
from csp_lab.models import Protocol

_TEST_ERROR = pytest.StashKey[BaseException]()
_LAB = pytest.StashKey[Lab]()


class _PytestFailure(Exception):
    """A failed pytest report without an exception, such as strict XPASS."""


@dataclass(frozen=True)
class _Options:
    protocol: Protocol = 2
    build: bool = False
    scope: str = "session"


def _options_for(node: pytest.Item) -> _Options:
    found: list[_Options] = []
    seen_nodes: set[int] = set()
    for source, mark in node.iter_markers_with_node(name="csp_lab"):
        if id(source) in seen_nodes:
            raise pytest.UsageError("Only one csp_lab marker is allowed at each level.")
        seen_nodes.add(id(source))
        if mark.args:
            raise pytest.UsageError("csp_lab marker accepts keyword arguments only.")
        unknown = set(mark.kwargs) - {"protocol", "build", "scope"}
        if unknown:
            raise pytest.UsageError(
                f"Unsupported csp_lab marker options: {', '.join(sorted(unknown))}."
            )
        protocol = mark.kwargs.get("protocol", 2)
        build = mark.kwargs.get("build", False)
        scope = mark.kwargs.get("scope", "session")
        if type(protocol) is not int or protocol not in (1, 2):
            raise pytest.UsageError("csp_lab protocol must be integer 1 or 2.")
        if type(build) is not bool:
            raise pytest.UsageError("csp_lab build must be a boolean.")
        if type(scope) is not str or scope not in ("session", "function"):
            raise pytest.UsageError("csp_lab scope must be 'session' or 'function'.")
        found.append(_Options(cast(Protocol, protocol), build, scope))
    return found[0] if found else _Options()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "csp_lab(protocol=2, build=False, scope='session'): configure the csp_lab fixture",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--csp-lab-artifacts",
        default="artifacts/csp-lab",
        metavar="DIRECTORY",
        help="Save failed-test CSP diagnostics here; use 'none' to disable.",
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    # Run after pytest has classified xfail/skip/strict-XPASS outcomes.
    report = yield
    if call.when in ("setup", "call") and report.failed:
        error = call.excinfo.value if call.excinfo is not None else _PytestFailure()
        item.stash[_TEST_ERROR] = error
        lab = item.stash.get(_LAB, None)
        if lab is not None:
            lab.collect_artifacts(error)
            if _options_for(item).scope == "function":
                # Keep the original error for cleanup semantics while avoiding
                # a second snapshot from Lab.__exit__ after fixture teardown.
                lab._captured_context_error = error
    elif call.when in ("setup", "call") and _TEST_ERROR in item.stash:
        del item.stash[_TEST_ERROR]
    return report


class _Registry:
    def __init__(self, artifacts_dir: str | None) -> None:
        self.labs: dict[tuple[Protocol, bool], Lab] = {}
        self.docker_checked = False
        self.artifacts_dir = artifacts_dir

    def ensure_docker(self) -> None:
        if self.docker_checked:
            return
        try:
            docker.run("docker", ["info", "--format", "{{.ServerVersion}}"], timeout=10)
            docker.run("docker", ["compose", "version"], timeout=10)
        except Exception as exc:
            pytest.skip(f"Docker with Compose is unavailable: {exc}")
        self.docker_checked = True

    def get(self, options: _Options) -> Lab:
        key = (options.protocol, options.build)
        if key not in self.labs:
            self.ensure_docker()
            lab = Lab(options.protocol, options.build, artifacts_dir=self.artifacts_dir)
            lab.__enter__()
            self.labs[key] = lab
        return self.labs[key]

    def close(self) -> None:
        errors: list[str] = []
        for lab in reversed(list(self.labs.values())):
            try:
                lab.__exit__(None, None, None)
            except Exception as exc:
                errors.append(str(exc))
        self.labs.clear()
        if errors:
            raise RuntimeError("Session lab cleanup failed: " + " ".join(errors))


@pytest.fixture(scope="session")
def _csp_lab_registry(pytestconfig: pytest.Config) -> Iterator[_Registry]:
    selected = str(pytestconfig.getoption("--csp-lab-artifacts"))
    registry = _Registry(None if selected == "none" else selected)
    yield registry
    registry.close()


@pytest.fixture
def csp_lab(request: pytest.FixtureRequest, _csp_lab_registry: _Registry) -> Iterator[Lab]:
    options = _options_for(request.node)
    if options.scope == "session":
        lab = _csp_lab_registry.get(options)
        request.node.stash[_LAB] = lab
        yield lab
        return
    _csp_lab_registry.ensure_docker()
    lab = Lab(options.protocol, options.build, artifacts_dir=_csp_lab_registry.artifacts_dir)
    lab.__enter__()
    request.node.stash[_LAB] = lab
    try:
        yield lab
    finally:
        error = request.node.stash.get(_TEST_ERROR, None)
        lab.__exit__(
            type(error) if error is not None else None,
            error,
            error.__traceback__ if error is not None else None,
        )
