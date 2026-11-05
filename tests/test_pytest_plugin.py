"""Observable fixture behavior in separate pytest runs."""

import json

import pytest


def test_fixture_shares_by_configuration_and_obeys_marker_precedence(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest(
        """
        import json
        from pathlib import Path
        from csp_lab import docker
        from csp_lab.models import PingResult

        started = []
        cleaned = []
        docker.run = lambda *args, **kwargs: 'available'
        docker.start_project = lambda protocol, image, project, build: started.append(
            (protocol, project, build)
        )
        docker.cleanup_owned_project = cleaned.append
        docker.ping_with_protocol = lambda target, protocol, image, project, **kwargs: PingResult(
            target=target, reachable=True, rttMs=1
        )

        def pytest_sessionfinish(session, exitstatus):
            Path('projects.json').write_text(json.dumps({'started': started, 'cleaned': cleaned}))
        """
    )
    pytester.makepyfile(
        test_defaults="""
        def test_default_a(csp_lab):
            assert csp_lab.protocol == 2
            assert csp_lab.ping(2).reachable

        def test_default_b(csp_lab):
            assert csp_lab.protocol == 2
        """,
        test_markers="""
        import pytest
        pytestmark = pytest.mark.csp_lab(protocol=1)

        def test_module_a(csp_lab):
            assert csp_lab.protocol == 1

        def test_module_b(csp_lab):
            assert csp_lab.protocol == 1

        class TestClass:
            pytestmark = pytest.mark.csp_lab(protocol=2)

            def test_class_a(self, csp_lab):
                assert csp_lab.protocol == 2

            def test_class_b(self, csp_lab):
                assert csp_lab.protocol == 2

        @pytest.mark.csp_lab(protocol=1, scope='function')
        def test_function(csp_lab):
            assert csp_lab.protocol == 1
        """,
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=7)
    projects = json.loads((pytester.path / "projects.json").read_text())
    started = projects["started"]
    assert len(started) == 3
    assert sorted(protocol for protocol, _, _ in started) == [1, 1, 2]
    assert len({project for _, project, _ in started}) == 3
    assert {project for _, project, _ in started} == set(projects["cleaned"])


def test_invalid_marker_options_fail_before_start(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(
        """
        from csp_lab import docker
        docker.run = lambda *args, **kwargs: 'available'
        docker.start_project = lambda *args: (_ for _ in ()).throw(AssertionError('started'))
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.csp_lab(protocol=True)
        def test_bool_protocol(csp_lab): pass

        @pytest.mark.csp_lab(topology='soon.yaml')
        def test_unsupported(csp_lab): pass

        @pytest.mark.csp_lab(scope='module')
        def test_scope(csp_lab): pass

        @pytest.mark.csp_lab(protocol=1)
        @pytest.mark.csp_lab(protocol=2)
        def test_duplicate(csp_lab): pass
        """
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(errors=4)
    result.stdout.fnmatch_lines(
        [
            "*csp_lab protocol must be integer 1 or 2*",
            "*Unsupported csp_lab marker options: topology*",
            "*csp_lab scope must be 'session' or 'function'*",
            "*Only one csp_lab marker is allowed at each level*",
        ]
    )


@pytest.mark.parametrize("unavailable", ["info", "compose"])
def test_fixture_skips_when_docker_unavailable(pytester: pytest.Pytester, unavailable: str) -> None:
    pytester.makeconftest(
        f"""
        from csp_lab import docker

        def fake_run(command, args, **kwargs):
            if args[0] == {unavailable!r}:
                raise RuntimeError('{unavailable} unavailable')
            return 'available'

        docker.run = fake_run
        """
    )
    pytester.makepyfile("def test_lab(csp_lab): pass")
    result = pytester.runpytest_subprocess("-q", "-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*Docker with Compose is unavailable*"])


def test_body_error_is_not_masked_by_function_lab_cleanup_under_warnings_as_errors(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest(
        """
        from csp_lab import docker
        docker.run = lambda *args, **kwargs: 'available'
        docker.start_project = lambda *args: None

        def fail_cleanup(project):
            raise RuntimeError(f"Could not clean {project}. Run 'csp-lab cleanup {project}'.")

        docker.cleanup_owned_project = fail_cleanup
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.csp_lab(scope='function')
        def test_body(csp_lab):
            raise ValueError('body failed')
        """
    )
    result = pytester.runpytest_subprocess("-q", "-W", "error", "--csp-lab-artifacts", "none")
    result.assert_outcomes(failed=1, errors=0)
    result.stdout.fnmatch_lines(["*ValueError: body failed*", "*csp-lab cleanup*"])


def test_session_cleanup_failure_is_teardown_error(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(
        """
        from csp_lab import docker
        docker.run = lambda *args, **kwargs: 'available'
        docker.start_project = lambda *args: None
        docker.cleanup_owned_project = lambda project: (_ for _ in ()).throw(
            RuntimeError(f"Could not clean {project}. Run 'csp-lab cleanup {project}'.")
        )
        """
    )
    pytester.makepyfile("def test_lab(csp_lab): assert csp_lab.protocol == 2")
    result = pytester.runpytest_subprocess("-q", "--csp-lab-artifacts", "none")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*Session lab cleanup failed*csp-lab cleanup*"])


def test_failed_tests_capture_before_cleanup_and_shared_lab_survives(pytester):
    pytester.makeconftest(
        """
        import json
        from pathlib import Path
        from csp_lab import artifacts, docker
        events = []
        docker.run = lambda *args, **kwargs: 'available'
        docker.start_project = lambda *args: events.append(('start', args[2]))
        docker.cleanup_owned_project = lambda project: events.append(('cleanup', project))

        def capture(root, project, protocol, addresses, images, operation, error):
            destination = root / project / str(len(events))
            destination.mkdir(parents=True)
            (destination / 'manifest.json').write_text(
                json.dumps({'exception': type(error).__name__})
            )
            events.append(('capture', project))
            return destination

        artifacts.collect_bundle = capture

        def pytest_sessionfinish(session, exitstatus):
            Path('events.json').write_text(json.dumps(events))
        """
    )
    pytester.makepyfile(
        """
        import pytest
        import conftest

        def test_shared_failure(csp_lab):
            raise AssertionError('assertion secret')

        def test_same_shared_lab_remains_usable(csp_lab):
            assert [event[0] for event in conftest.events] == ['start', 'capture']
            assert csp_lab._active

        @pytest.fixture
        def reset_application(csp_lab):
            yield csp_lab
            # A real fixture can remove/reset the application container here.
            conftest.events.append(('reset', csp_lab.project))

        @pytest.mark.csp_lab(scope='function')
        def test_function_failure(reset_application):
            raise ValueError('body secret')

        @pytest.fixture
        def broken_fixture(csp_lab):
            raise RuntimeError('setup secret')

        @pytest.mark.csp_lab(scope='function')
        def test_setup_failure(broken_fixture): pass
        """
    )
    result = pytester.runpytest_subprocess("-q", "--csp-lab-artifacts", "saved")
    result.assert_outcomes(failed=2, passed=1, errors=1)
    events = json.loads((pytester.path / "events.json").read_text())
    assert [item[0] for item in events] == [
        "start",
        "capture",
        "start",
        "capture",
        "reset",
        "cleanup",
        "start",
        "capture",
        "cleanup",
        "cleanup",
    ]
    manifests = list((pytester.path / "saved").rglob("manifest.json"))
    assert len(manifests) == 3
    assert sorted(json.loads(p.read_text())["exception"] for p in manifests) == [
        "AssertionError",
        "RuntimeError",
        "ValueError",
    ]
    assert all("secret" not in p.read_text() for p in manifests)


def test_custom_nodes_share_only_normalized_identical_configuration(pytester):
    pytester.makeconftest(
        """
        import json
        from pathlib import Path
        from csp_lab import docker
        projects = {}
        started = []
        cleaned = []
        docker.run = lambda *args, **kwargs: 'available'
        docker.resolve_node_images = lambda nodes: {
            node.address: 'sha256:' + 'a' * 64 for node in nodes
        }
        docker.start_custom_project = lambda *args: started.append(args[2])
        docker.cleanup_owned_project = cleaned.append

        def pytest_sessionfinish(session, exitstatus):
            Path('projects.json').write_text(json.dumps({
                'cases': projects, 'started': started, 'cleaned': cleaned,
            }))
        """
    )
    pytester.makepyfile(
        """
        import pytest
        import conftest
        from csp_lab import Node
        normal = (Node(5, 'a'), Node(6, 'b'))
        cases = [
            ('a', {'nodes': normal}),
            ('b', {'nodes': tuple(reversed(normal))}),
            ('image', {'nodes': (Node(5, 'different'), Node(6, 'b'))}),
            ('address', {'nodes': (Node(5, 'a'), Node(7, 'b'))}),
            ('protocol', {'nodes': normal, 'protocol': 1}),
            ('build', {'nodes': normal, 'build': True}),
            ('function-a', {'nodes': normal, 'scope': 'function'}),
            ('function-b', {'nodes': normal, 'scope': 'function'}),
        ]

        @pytest.mark.parametrize('case', [pytest.param(name, marks=pytest.mark.csp_lab(**options))
                                        for name, options in cases])
        def test_configuration(case, csp_lab):
            assert tuple(node.address for node in csp_lab.nodes) == (
                (5, 7) if case == 'address' else (5, 6)
            )
            conftest.projects[case] = csp_lab.project
        """
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=8)
    report = json.loads((pytester.path / "projects.json").read_text())
    projects = report["cases"]
    assert projects["a"] == projects["b"]
    assert len(set(projects.values())) == 7
    assert sorted(report["started"]) == sorted(report["cleaned"]) == sorted(set(projects.values()))


def test_invalid_custom_node_markers_fail_before_docker_even_with_valid_nearer_marker(pytester):
    pytester.makeconftest(
        """
        from csp_lab import docker
        def fail(*args, **kwargs):
            raise AssertionError('Docker invoked before node validation')
        docker.run = fail
        """
    )
    pytester.makepyfile(
        test_invalid="""
        import pytest
        from csp_lab import Node

        @pytest.mark.csp_lab(nodes=None)
        def test_none(csp_lab): pass

        @pytest.mark.csp_lab(nodes=[5])
        def test_non_node(csp_lab): pass

        @pytest.mark.csp_lab(nodes=(Node(5, 'a'), Node(5, 'b')))
        def test_duplicate(csp_lab): pass

        @pytest.mark.csp_lab(protocol=1, nodes=(Node(31, 'a'),))
        def test_broadcast(csp_lab): pass

        @pytest.mark.csp_lab(nodes=tuple(Node(i, 'a') for i in range(5, 14)))
        def test_too_many(csp_lab): pass
        """,
        test_outer_marker="""
        import pytest
        from csp_lab import Node
        pytestmark = pytest.mark.csp_lab(protocol=1, nodes=(Node(31, 'outer'),))

        @pytest.mark.csp_lab(protocol=2, nodes=(Node(5, 'valid'),))
        def test_all_levels_are_validated(csp_lab): pass
        """,
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(errors=6)
    assert "Docker invoked" not in result.stdout.str()
    result.stdout.fnmatch_lines(["*Invalid csp_lab nodes*"])


@pytest.mark.parametrize("scope", ["function", "session"])
def test_artifacts_follow_final_pytest_outcome_before_fixture_teardown(pytester, scope):
    pytester.makeconftest(
        """
        import json
        from pathlib import Path
        import pytest
        from csp_lab import artifacts, docker
        events = []
        docker.run = lambda *args, **kwargs: 'available'
        docker.start_project = lambda *args: events.append(['start', args[2]])
        docker.cleanup_owned_project = lambda project: events.append(['cleanup', project])

        def capture(root, project, protocol, addresses, images, operation, error):
            events.append(['capture', type(error).__name__, project])
            return root

        artifacts.collect_bundle = capture

        @pytest.fixture
        def application(csp_lab, request):
            yield csp_lab
            events.append(['reset', request.node.name, csp_lab.project])

        def pytest_sessionfinish(session, exitstatus):
            Path('events.json').write_text(json.dumps(events))
        """
    )
    pytester.makepyfile(
        f"""
        import pytest
        pytestmark = pytest.mark.csp_lab(scope={scope!r})

        def test_skip(application):
            pytest.skip('expected skip')

        @pytest.mark.xfail(reason='expected failure')
        def test_expected_failure(application):
            assert False

        @pytest.mark.xfail(reason='unexpected success', strict=True)
        def test_strict_xpass(application):
            pass

        @pytest.mark.xfail(reason='allowed success', strict=False)
        def test_nonstrict_xpass(application):
            pass
        """
    )
    result = pytester.runpytest_subprocess("-q", "-W", "error")
    result.assert_outcomes(failed=1, skipped=1, xfailed=1, xpassed=1)
    events = json.loads((pytester.path / "events.json").read_text())
    captures = [event for event in events if event[0] == "capture"]
    assert len(captures) == 1, events
    capture = captures[0]
    assert capture[1] == "_PytestFailure"
    reset = next(event for event in events if event[:2] == ["reset", "test_strict_xpass"])
    cleanup = next(event for event in events if event == ["cleanup", capture[2]])
    assert events.index(capture) < events.index(reset) < events.index(cleanup)


def test_strict_xpass_failure_is_not_replaced_by_function_cleanup_error(pytester):
    pytester.makeconftest(
        """
        from pathlib import Path
        from csp_lab import artifacts, docker
        docker.run = lambda *args, **kwargs: 'available'
        docker.start_project = lambda *args: None
        artifacts.collect_bundle = lambda *args: Path('saved')

        def fail_cleanup(project):
            raise RuntimeError(f'Cleanup failed; run csp-lab cleanup {project}')

        docker.cleanup_owned_project = fail_cleanup
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.csp_lab(scope='function')
        @pytest.mark.xfail(reason='unexpected success', strict=True)
        def test_strict_xpass(csp_lab): pass
        """
    )
    result = pytester.runpytest_subprocess("-q", "-W", "error")
    result.assert_outcomes(failed=1, errors=0)
    result.stdout.fnmatch_lines(["*XPASS(strict)*unexpected success*"])
