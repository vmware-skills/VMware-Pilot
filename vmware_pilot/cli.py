"""Typer CLI for vmware-pilot.

Pilot is an orchestration server with no vCenter connection of its own, so this
CLI is deliberately thin: its job is to give MCP clients a launch command that
lives on ``PATH``.

Why this exists rather than ``uvx --from vmware-pilot vmware-pilot-mcp``: ``uvx``
re-resolves the package against PyPI on every start, which fails behind a
corporate TLS-inspecting proxy (``invalid peer certificate: UnknownIssuer``).
An installed entry point touches the network zero times. See the family
convention — every sibling skill exposes the same ``<skill> mcp`` subcommand.
"""

from __future__ import annotations

import typer

from vmware_pilot import __version__
import sys


def _harden_console_encoding() -> None:
    """Never let one unrepresentable glyph kill a command.

    On a console whose encoding cannot carry the characters we print -- cp936 on
    the Chinese Windows boxes this family is tested on, or any ASCII locale --
    ``print`` raises ``UnicodeEncodeError`` and the whole command dies with a
    traceback. ``--help`` died that way in four repos. A mangled dash is a
    cosmetic loss; a dead ``--help`` is an outage, so the error handler is
    relaxed rather than the vocabulary narrowed.

    Best effort: ``reconfigure`` is absent when stdout has been replaced by a
    plain object (pytest capture, some MCP hosts), and losing the hardening
    there is not worth an exception at import.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


_harden_console_encoding()

app = typer.Typer(
    name="vmware-pilot",
    help="VMware workflow orchestration — multi-step state machine with approval gates.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """VMware Pilot.

    Workflow authoring, approval gates and rollback happen over MCP. Point your
    MCP client at ``vmware-pilot mcp``.
    """


@app.command("version")
def version_cmd() -> None:
    """Print the installed vmware-pilot version."""
    typer.echo(f"vmware-pilot {__version__}")


@app.command("mcp")
def mcp_cmd() -> None:
    """Start the MCP server (stdio transport).

    Single-command entry point for MCP clients:
        vmware-pilot mcp

    Equivalent to the legacy `vmware-pilot-mcp` console script.
    """
    import sys

    # noqa rationale: ruff reads target-version=py310 and calls this block dead.
    # It is not — pip/uv can and do install onto an older interpreter despite
    # requires-python, and the failure then surfaces deep inside FastMCP's
    # schema build instead of here. Keep the guard.
    if sys.version_info < (3, 10):  # noqa: UP036
        msg = (
            f"ERROR: vmware-pilot MCP server requires Python >= 3.10 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}).\n"
            f"Interpreter: {sys.executable}\n"
            "Fix: uv python install 3.12 && "
            "uv tool install --python 3.12 --force vmware-pilot"
        )
        typer.echo(msg, err=True)
        raise typer.Exit(2)

    from vmware_pilot.mcp_server.server import main as _mcp_main

    _mcp_main()


if __name__ == "__main__":  # pragma: no cover
    app()
