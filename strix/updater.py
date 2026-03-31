from __future__ import annotations

import argparse
import os
import sys

from rich.console import Console

from strix.config.config import Config


# Ed25519 public key for verifying release signatures
RELEASE_PUBLIC_KEY = "4eD5+0E+VY67+qtYf97UjlZ8FDec4JDXs8Z+B4MsMms="
DEFAULT_REPO = "usestrix/strix"


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("strix-agent")
    except Exception:  # noqa: BLE001
        return "unknown"


def _is_auto_update_disabled() -> bool:
    if os.environ.get("STRIX_NO_AUTO_UPDATE", "").strip() in ("1", "true", "yes"):
        return True
    return Config.load().get("auto_update") is False


def _set_auto_update(*, enabled: bool) -> None:
    data = Config.load()
    data["auto_update"] = enabled
    Config.save(data)


def run_auto_update(source: str | None = None) -> None:
    if _is_auto_update_disabled():
        return
    try:
        from strix_autoupdater import auto_update

        auto_update(
            current_version=_get_version(),
            repo=source or DEFAULT_REPO,
            public_key=RELEASE_PUBLIC_KEY,
            disabled=_get_version() == "unknown",
        )
    except Exception:  # noqa: BLE001, S110
        pass


def handle_update_command(argv: list[str]) -> None:  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser(prog="strix update", description="Manage Strix updates")
    parser.add_argument("--disable", action="store_true", help="Disable auto-updates")
    parser.add_argument("--enable", action="store_true", help="Enable auto-updates")
    parser.add_argument("--status", action="store_true", help="Show update status")
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="GitHub repo to check (e.g. 'user/repo'). Dev/testing only.",
    )

    args = parser.parse_args(argv)
    console = Console()

    if args.disable:
        _set_auto_update(enabled=False)
        console.print("[dim]Auto-updates disabled.[/dim]")
        sys.exit(0)

    if args.enable:
        _set_auto_update(enabled=True)
        console.print("[dim]Auto-updates enabled.[/dim]")
        sys.exit(0)

    repo = args.source or DEFAULT_REPO

    if args.status:
        current = _get_version()
        console.print(f"[bold]Strix[/bold] v{current}")

        try:
            from strix_autoupdater.release.github import GitHubReleaseService

            release = GitHubReleaseService(repo=repo).get_latest_release()
            if release:
                if release.version == current:
                    console.print("[green]Up to date[/green]")
                else:
                    console.print(f"[yellow]Update available: v{release.version}[/yellow]")
            else:
                console.print("[dim]Could not check for updates[/dim]")
        except Exception:  # noqa: BLE001
            console.print("[dim]Could not check for updates[/dim]")

        auto_enabled = Config.load().get("auto_update", True)
        console.print(f"Auto-update: [bold]{'enabled' if auto_enabled else 'disabled'}[/bold]")
        sys.exit(0)

    # Default: run manual update
    try:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TextColumn,
            TransferSpeedColumn,
        )
        from strix_autoupdater import AutoUpdater
        from strix_autoupdater.release.github import GitHubReleaseService
        from strix_autoupdater.signing.ed25519 import Ed25519SigningService

        current = _get_version()
        console.print(f"[dim]Checking for updates (current: v{current})...[/dim]")

        release_service = GitHubReleaseService(repo=repo)
        updater = AutoUpdater(
            current_version=current,
            release_service=release_service,
            signing_service=Ed25519SigningService(public_key=RELEASE_PUBLIC_KEY),
            reexec=False,
        )

        release = release_service.get_latest_release()
        if release is None:
            console.print("[dim]Update failed: could not reach GitHub.[/dim]")
            sys.exit(1)

        info = updater.check()
        if info is None:
            console.print(f"[dim]Already up to date (v{current}).[/dim]")
            sys.exit(0)

        if "checksums.txt" not in release.assets or "checksums.txt.sig" not in release.assets:
            console.print(
                f"[dim]Update failed: release v{release.version} is missing "
                f"signature files (checksums.txt / checksums.txt.sig).[/dim]"
            )
            sys.exit(1)

        console.print(f"[dim]Downloading v{info.version}...[/dim]")

        progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=console,
        )

        def _make_progress_callback(task_id: object) -> object:
            def _callback(downloaded: int, total: int | None) -> None:
                if total is not None:
                    progress.update(task_id, total=total, completed=downloaded)
                else:
                    progress.update(task_id, completed=downloaded)

            return _callback

        with progress:
            task = progress.add_task("Updating", total=None)
            release_service.set_progress_callback(_make_progress_callback(task))
            updated = updater.update()

        if updated:
            console.print(f"[green]Updated to v{info.version}[/green]")
        else:
            console.print(
                "[dim]Update failed: signature verification or checksum mismatch. "
                "The release may not be signed yet.[/dim]"
            )
    except Exception as e:  # noqa: BLE001
        console.print(f"[dim]Update failed: {e}[/dim]")

    sys.exit(0)
