import asyncio
import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from recorder_cli.agent_ux import (
    EXIT_AUTH,
    EXIT_NOT_FOUND,
    agent_output_options,
    die,
    emit_csv,
    emit_json,
    note_showing,
    resolve_format,
    select_fields,
)

from recorder_cli.browser import login as browser_login, has_session
from recorder_cli.mcp_setup import CLIENTS
from recorder_cli.recorder import RecorderClient
from recorder_cli import skill_setup

console = Console()


@click.group()
def main():
    """Unofficial CLI for Google Recorder (recorder.google.com)."""
    pass


@main.group()
def mcp():
    """Manage the recorder MCP server and its client registrations."""
    pass


@mcp.command("install")
@click.argument("client", type=click.Choice(sorted(CLIENTS)))
def mcp_install(client):
    """Register recorder-mcp with CLIENT (idempotent)."""
    CLIENTS[client].install()


@mcp.command("uninstall")
@click.argument("client", type=click.Choice(sorted(CLIENTS)))
def mcp_uninstall(client):
    """Remove the recorder-mcp registration from CLIENT (idempotent)."""
    CLIENTS[client].uninstall()


@mcp.command("status")
def mcp_status():
    """Show recorder-mcp registration status for all supported clients."""
    for name in sorted(CLIENTS):
        CLIENTS[name].status()


@mcp.command("serve")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http"]),
    default="stdio",
    help="stdio for local clients (what registrations use); http to serve over the network.",
)
@click.option("--host", default="127.0.0.1", help="Bind address for --transport http.")
@click.option("--port", default=8420, type=int, help="Bind port for --transport http.")
def mcp_serve(transport, host, port):
    """Run the MCP server (equivalent to the recorder-mcp binary)."""
    from recorder_cli.mcp_server import run_server  # deferred: fastmcp import is heavy

    run_server(transport, host, port)


@main.group()
def skill():
    """Manage the google-recorder-cli Claude Code skill."""
    pass


@skill.command("install")
def skill_install():
    """Install the skill into ~/.claude/skills/ (idempotent)."""
    skill_setup.install()


@skill.command("uninstall")
def skill_uninstall():
    """Remove the skill from ~/.claude/skills/ (idempotent)."""
    skill_setup.uninstall()


@skill.command("status")
def skill_status():
    """Show whether the skill is installed and up to date."""
    skill_setup.status()


@main.command()
def login():
    """Open browser for Google authentication."""
    console.print("Opening browser for Google login...")
    asyncio.run(browser_login())
    console.print("[green]Session saved successfully.[/green]")


@main.command("list")
@agent_output_options()
@click.option("--id-only", is_flag=True, help="Print only recording IDs")
def list_recordings(fmt, compact, select, quiet, as_csv, id_only):
    """List all recordings."""
    _check_session()
    client = RecorderClient()
    recordings = asyncio.run(client.list_recordings())

    if id_only:
        for rec in recordings:
            click.echo(rec.id)
        return

    data = [
        {
            "id": r.id,
            "title": r.title,
            "created_at": r.created_at.isoformat(),
            "duration_seconds": r.duration_seconds,
            "has_transcript": r.has_transcript,
        }
        for r in recordings
    ]
    if compact:
        data = [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "has_transcript": r["has_transcript"],
            }
            for r in data
        ]
    data = select_fields(data, select)
    fmt = resolve_format(fmt)
    if as_csv:
        emit_csv(data if isinstance(data, list) else [data])
        return
    if fmt == "json":
        emit_json(data)
        return

    table = Table(title="Recordings")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Date", style="green")
    table.add_column("Duration", style="yellow")
    table.add_column("Transcript", style="magenta")

    for rec in recordings:
        mins, secs = divmod(rec.duration_seconds, 60)
        table.add_row(
            rec.id,
            rec.title,
            rec.created_at.strftime("%Y-%m-%d %H:%M"),
            f"{mins}m {secs}s",
            "Yes" if rec.has_transcript else "No",
        )
    console.print(table)
    note_showing(len(recordings), quiet=quiet, noun="recordings")


@main.command()
@click.argument("recording_id")
@click.option("--output", "-o", type=click.Path(), default=".", help="Output directory")
@click.option("--quiet", "-q", is_flag=True)
def transcript(recording_id, output, quiet):
    """Save transcript as a text file."""
    _check_session()
    client = RecorderClient()
    t = asyncio.run(client.get_transcript(recording_id))
    out_path = Path(output)
    out_path.mkdir(parents=True, exist_ok=True)
    filepath = out_path / f"{recording_id}.txt"
    filepath.write_text(t.full_text)
    if quiet:
        click.echo(str(filepath))
    else:
        console.print(f"[green]Transcript saved to {filepath}[/green]")


@main.command()
@click.argument("recording_id")
@click.option("--output", "-o", type=click.Path(), default=".", help="Output directory")
@click.option("--quiet", "-q", is_flag=True)
def download(recording_id, output, quiet):
    """Download audio file."""
    _check_session()
    client = RecorderClient()
    filepath = asyncio.run(client.download_audio(recording_id, Path(output)))
    if quiet:
        click.echo(str(filepath))
    else:
        console.print(f"[green]Audio saved to {filepath}[/green]")


@main.command()
@click.argument("recording_id")
@agent_output_options()
def info(recording_id, fmt, compact, select, quiet, as_csv):
    """Show metadata for a recording."""
    _check_session()
    client = RecorderClient()
    rec = asyncio.run(client.get_recording_info(recording_id))
    if not rec:
        die(f"No recording {recording_id}", EXIT_NOT_FOUND, hint="Run: recorder list --id-only")
    data = {
        "id": rec.id,
        "title": rec.title,
        "created_at": rec.created_at.isoformat(),
        "duration_seconds": rec.duration_seconds,
        "has_transcript": rec.has_transcript,
    }
    if compact:
        data = {k: data[k] for k in ("id", "title", "created_at", "has_transcript")}
    data = select_fields(data, select)
    fmt = resolve_format(fmt)
    if as_csv:
        emit_csv([data] if isinstance(data, dict) else data)
        return
    if fmt == "json":
        emit_json(data)
        return
    console.print(f"[bold]ID:[/bold] {rec.id}")
    console.print(f"[bold]Title:[/bold] {rec.title}")
    console.print(f"[bold]Created:[/bold] {rec.created_at.strftime('%Y-%m-%d %H:%M')}")
    mins, secs = divmod(rec.duration_seconds, 60)
    console.print(f"[bold]Duration:[/bold] {mins}m {secs}s")
    console.print(f"[bold]Transcript:[/bold] {'Yes' if rec.has_transcript else 'No'}")


@main.command()
@click.argument("query")
@agent_output_options()
@click.option("--id-only", is_flag=True)
def search(query, fmt, compact, select, quiet, as_csv, id_only):
    """Search recordings by keyword."""
    _check_session()
    client = RecorderClient()
    results = asyncio.run(client.search_recordings(query))
    if id_only:
        for rec in results:
            click.echo(rec.id)
        return
    data = [
        {
            "id": r.id,
            "title": r.title,
            "created_at": r.created_at.isoformat(),
        }
        for r in results
    ]
    data = select_fields(data, select)
    fmt = resolve_format(fmt)
    if as_csv:
        emit_csv(data)
        return
    if fmt == "json":
        emit_json(data)
        return
    if not results:
        if not quiet:
            console.print("[yellow]No recordings found.[/yellow]")
        return
    table = Table(title=f"Search: {query}")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Date", style="green")
    for rec in results:
        table.add_row(rec.id, rec.title, rec.created_at.strftime("%Y-%m-%d %H:%M"))
    console.print(table)
    note_showing(len(results), quiet=quiet, noun="recordings")


@main.command()
@click.option("--output", "-o", type=click.Path(), default="./recorder-backup", help="Output directory")
def sync(output):
    """Bulk download all transcripts and audio."""
    _check_session()
    client = RecorderClient()
    results = asyncio.run(client.sync_all(Path(output)))
    console.print(f"[green]Transcripts saved: {len(results['transcripts'])}[/green]")
    console.print(f"[green]Audio files saved: {len(results['audio'])}[/green]")
    if results["errors"]:
        console.print(f"[red]Errors: {len(results['errors'])}[/red]")
        for err in results["errors"]:
            console.print(f"  [red]- {err}[/red]")


def _check_session():
    """Check for valid session, exit with message if missing."""
    if not has_session():
        die("No session found.", EXIT_AUTH, hint="Run: recorder login")
