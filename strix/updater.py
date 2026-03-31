from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rich.console import Console


# Ed25519 public key for verifying release signatures
RELEASE_PUBLIC_KEY = "4eD5+0E+VY67+qtYf97UjlZ8FDec4JDXs8Z+B4MsMms="
DEFAULT_REPO = "usestrix/strix"


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("strix-agent")
    except Exception:  # noqa: BLE001
        return "unknown"


def _config_path() -> Path:
    return Path.home() / ".strix" / "cli-config.json"


def _is_auto_update_disabled() -> bool:
    if os.environ.get("STRIX_NO_AUTO_UPDATE", "").strip() in ("1", "true", "yes"):
        return True
    cfg = _config_path()
    if cfg.exists():
        try:
            with cfg.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("auto_update") is False:
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def _set_auto_update(*, enabled: bool) -> None:
    cfg = _config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    if cfg.exists():
        try:
            with cfg.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    data["auto_update"] = enabled
    with cfg.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def run_auto_update(source: str | None = None) -> None:
    """Run auto-update check at startup. Non-fatal."""
    if _is_auto_update_disabled():
        return
    try:
        from strix_autoupdater import auto_update

        repo = source or DEFAULT_REPO
        console = Console()
        if source:
            console.print(
                f"[bold yellow]WARNING: Using custom update source '{source}' "
                f"(dev/testing only)[/bold yellow]"
            )

        auto_update(
            current_version=_get_version(),
            repo=repo,
            public_key=RELEASE_PUBLIC_KEY,
            disabled=_get_version() == "unknown",
        )
    except Exception:  # noqa: BLE001, S110
        pass  # Update failures are never fatal


def handle_update_command(argv: list[str]) -> None:  # noqa: PLR0912, PLR0915
    """Handle ``strix update`` subcommand."""
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
    if args.source:
        console.print(
            f"[bold yellow]WARNING: Using custom update source '{args.source}' "
            f"(dev/testing only)[/bold yellow]"
        )

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

        cfg = _config_path()
        auto_enabled = True
        if cfg.exists():
            try:
                with cfg.open("r", encoding="utf-8") as f:
                    auto_enabled = json.load(f).get("auto_update", True)
            except (json.JSONDecodeError, OSError):
                pass
        status_str = "enabled" if auto_enabled else "disabled"
        console.print(f"Auto-update: [bold]{status_str}[/bold]")
        sys.exit(0)

    # Default: run manual update
    try:
        from strix_autoupdater import AutoUpdater
        from strix_autoupdater.release.github import GitHubReleaseService
        from strix_autoupdater.signing.ed25519 import Ed25519SigningService

        current = _get_version()
        console.print(f"[dim]Checking for updates (current: v{current})...[/dim]")

        updater = AutoUpdater(
            current_version=current,
            release_service=GitHubReleaseService(repo=repo),
            signing_service=Ed25519SigningService(public_key=RELEASE_PUBLIC_KEY),
            reexec=False,
        )

        release = GitHubReleaseService(repo=repo).get_latest_release()
        if release is None:
            console.print("[dim]Update failed: could not reach GitHub.[/dim]")
            sys.exit(1)

        info = updater.check()
        if info is None:
            console.print(f"[dim]Already up to date (v{current}).[/dim]")
            sys.exit(0)

        # Check release has signing artifacts
        if "checksums.txt" not in release.assets or "checksums.txt.sig" not in release.assets:
            console.print(
                f"[dim]Update failed: release v{release.version} is missing "
                f"signature files (checksums.txt / checksums.txt.sig).[/dim]"
            )
            sys.exit(1)

        # Resolve expected asset name
        platform_name = None
        try:
            from strix_autoupdater.platform.detection import detect_platform

            platform_name = detect_platform()
        except Exception:  # noqa: BLE001, S110
            pass

        if platform_name:
            ext = ".zip" if platform_name.startswith("windows") else ".tar.gz"
            asset_name = f"strix-{release.version}-{platform_name}{ext}"
            if asset_name not in release.assets:
                console.print(
                    f"[dim]Update failed: no artifact for your platform ({platform_name}). "
                    f"Expected: {asset_name}[/dim]"
                )
                available = [a for a in release.assets if a.startswith("strix-")]
                if available:
                    console.print(f"[dim]Available: {', '.join(available)}[/dim]")
                sys.exit(1)

        console.print(f"[dim]Updating v{current} → v{info.version}...[/dim]")
        if updater.update():
            console.print(f"[green]Updated to v{info.version}[/green]")
        else:
            console.print(
                "[dim]Update failed: signature verification or checksum mismatch. "
                "The release may not be signed yet.[/dim]"
            )
    except Exception as e:  # noqa: BLE001
        console.print(f"[dim]Update failed: {e}[/dim]")

    sys.exit(0)
