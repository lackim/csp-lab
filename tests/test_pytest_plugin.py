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
        docker.ping_with_protocol = lambda target, protocol, image, project: PingResult(
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
    result = pytester.runpytest_subprocess("-q", "-W", "error")
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
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*Session lab cleanup failed*csp-lab cleanup*"])
