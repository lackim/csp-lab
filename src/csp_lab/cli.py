"""Command-line interface for the local CSP lab."""

import json
import sys
from collections.abc import Callable
from importlib.metadata import version
from typing import TypeVar

import typer
from pydantic import BaseModel

from csp_lab import docker
from csp_lab.models import PingResult

ResultT = TypeVar("ResultT")

app = typer.Typer(
    name="csp-lab",
    help="Local CubeSat Space Protocol lab",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _show_version(value: bool) -> bool:
    if value:
        print(version("csp-lab"))
        raise typer.Exit()
    return value


@app.callback()
def main(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    show_version: bool = typer.Option(
        False, "-V", "--version", callback=_show_version, is_eager=True
    ),
) -> None:
    """Global output options."""
    ctx.obj = json_output


def _json(ctx: typer.Context, local: bool) -> bool:
    return bool(ctx.obj) or local


def _emit(value: BaseModel | dict[str, object], json_output: bool) -> None:
    data = value.model_dump(by_alias=True) if isinstance(value, BaseModel) else value
    print(
        json.dumps(
            data,
            indent=None if json_output else 2,
            separators=(",", ":") if json_output else None,
            ensure_ascii=False,
        )
    )


def _action(operation: Callable[[], ResultT], json_output: bool) -> ResultT:
    try:
        return operation()
    except Exception as exc:
        message = str(exc)
        if json_output:
            print(json.dumps({"error": message}, separators=(",", ":"), ensure_ascii=False))
        else:
            print(f"csp-lab: {message}", file=sys.stderr)
        raise typer.Exit(code=1) from exc


@app.command()
def up(
    ctx: typer.Context,
    protocol: str = typer.Option("2", "--protocol", help="CSP protocol version (1 or 2)"),
    build: bool = typer.Option(False, "--build", help="Build the native image from this checkout"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
    show_version: bool = typer.Option(
        False, "-V", "--version", callback=_show_version, is_eager=True, hidden=True
    ),
) -> None:
    """Start two libcsp nodes and a ZMQ hub."""
    json_output = _json(ctx, json_output)
    state = _action(lambda: docker.up(docker.parse_protocol(protocol), build), json_output)
    data: dict[str, object] = {"started": True}
    data.update(state.as_json())
    _emit(data, json_output)


@app.command()
def down(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
    show_version: bool = typer.Option(
        False, "-V", "--version", callback=_show_version, is_eager=True, hidden=True
    ),
) -> None:
    """Stop the lab."""
    json_output = _json(ctx, json_output)
    _action(docker.down, json_output)
    _emit({"stopped": True}, json_output)


@app.command()
def cleanup(
    ctx: typer.Context,
    project: str = typer.Argument(..., help="Python Lab project to remove"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
) -> None:
    """Remove an orphaned Python Lab project and its local image."""
    json_output = _json(ctx, json_output)
    _action(lambda: docker.cleanup_owned_project(project), json_output)
    _emit({"cleaned": project}, json_output)


@app.command()
def ping(
    ctx: typer.Context,
    address: str = typer.Argument(..., help="CSP address to ping"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
    show_version: bool = typer.Option(
        False, "-V", "--version", callback=_show_version, is_eager=True, hidden=True
    ),
) -> None:
    """Ping a node using libcsp."""
    json_output = _json(ctx, json_output)

    def probe() -> PingResult:
        state = docker.require_state()
        return docker.ping(docker.parse_address(address, state.protocol))

    result = _action(probe, json_output)
    _emit(result, json_output)
    if not result.reachable:
        raise typer.Exit(code=1)


@app.command()
def topology(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
    show_version: bool = typer.Option(
        False, "-V", "--version", callback=_show_version, is_eager=True, hidden=True
    ),
) -> None:
    """Show the configured network."""
    json_output = _json(ctx, json_output)
    _emit(_action(docker.topology, json_output), json_output)


@app.command()
def doctor(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
    show_version: bool = typer.Option(
        False, "-V", "--version", callback=_show_version, is_eager=True, hidden=True
    ),
) -> None:
    """Test both nodes."""
    json_output = _json(ctx, json_output)
    result = _action(docker.diagnose, json_output)
    _emit(result, json_output)
    if not result.healthy:
        raise typer.Exit(code=1)


@app.command()
def status(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
    show_version: bool = typer.Option(
        False, "-V", "--version", callback=_show_version, is_eager=True, hidden=True
    ),
) -> None:
    """Show local lab state."""
    json_output = _json(ctx, json_output)
    _emit(_action(docker.status, json_output), json_output)


@app.command()
def mcp() -> None:
    """Serve read-only CSP diagnostics over MCP stdio."""
    from csp_lab.mcp_server import server

    server.run(transport="stdio")


def run_cli() -> None:
    """Map legacy help syntax and parser exit codes to the original CLI."""
    args = sys.argv[1:]
    if args and args[0] == "help":
        args = [*args[1:], "--help"]
    try:
        app(args=args)
    except SystemExit as exc:
        if exc.code == 2:
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    run_cli()
