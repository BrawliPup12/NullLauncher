from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sys
from typing import Any, Optional

from .config import APP_NAME, APP_VERSION, BOLD, MIN_PYTHON, RED, RESET, apply_runtime_preferences, tr
from .utils import app_data_dir
from .state import StateStore
from .terminal import Terminal
from .minecraft import ensure_image_library, ensure_minecraft_library
from .app import NullLauncher
from .updater import run_update_helper, schedule_cleanup_path, signal_update_health
from .instance import SingleInstanceGuard, show_already_running
from .diagnostics import diagnose, setup_logging

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog=APP_NAME, description="Single-file terminal Minecraft launcher")
    p.add_argument("--diagnose", action="store_true", help="print environment diagnostics")
    p.add_argument("--version", action="store_true", help="print version")
    p.add_argument("--update-health-file", default="", help=argparse.SUPPRESS)
    p.add_argument("--cleanup-update-helper", default="", help=argparse.SUPPRESS)
    p.add_argument("--apply-update", default="", help=argparse.SUPPRESS)
    p.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--expected-sha256", default="", help=argparse.SUPPRESS)
    p.add_argument("--update-source", default="", help=argparse.SUPPRESS)
    p.add_argument("--update-metadata", default="", help=argparse.SUPPRESS)
    p.add_argument("--update-log", default="", help=argparse.SUPPRESS)
    p.add_argument("--skip-update-once", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if args.apply_update:
        return run_update_helper(
            args.apply_update,
            args.wait_pid,
            args.expected_sha256,
            args.update_source,
            args.update_metadata,
            args.update_log,
        )
    if args.version:
        if sys.stdout is not None:
            print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if args.diagnose:
        base = app_data_dir()
        try:
            diag_store = StateStore(base)
            apply_runtime_preferences(diag_store.settings)
        except Exception:
            pass
        return diagnose()
    if sys.version_info < MIN_PYTHON:
        print(tr("python_required", app=APP_NAME, major=MIN_PYTHON[0], minor=MIN_PYTHON[1]), file=sys.stderr)
        return 2

    if args.update_health_file:
        os.environ["NULLLAUNCHER_UPDATE_HEALTH_FILE"] = str(args.update_health_file)
    if args.skip_update_once:
        os.environ["NULLLAUNCHER_SKIP_AUTO_UPDATE"] = "1"
    if args.cleanup_update_helper:
        schedule_cleanup_path(args.cleanup_update_helper)

    base = app_data_dir()
    logger = setup_logging(base)
    store = StateStore(base)
    apply_runtime_preferences(store.settings)
    guard = SingleInstanceGuard(APP_NAME)
    if not guard.acquire():
        show_already_running(APP_NAME, tr("already_running"))
        return 0

    if os.name == "nt" and getattr(sys, "frozen", False):
        from .window_terminal import WindowTerminal
        term = WindowTerminal(mouse_enabled=store.settings["mouse_enabled"])
    else:
        term = Terminal(mouse_enabled=store.settings["mouse_enabled"])

    def restore_on_signal(signum: int, frame: Any) -> None:
        term.restore()
        raise KeyboardInterrupt

    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, restore_on_signal)
    try:
        mll = ensure_minecraft_library(term)
        ensure_image_library(term)
        app = NullLauncher(store, term, mll, logger)
        signal_update_health()
        return app.run()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.exception("Fatal error")
        message = f"{type(exc).__name__}: {exc}\n\n{tr('log')}: {base / 'null_launcher.log'}"
        if hasattr(term, "show_fatal"):
            term.show_fatal(tr("fatal"), message)
        else:
            term.restore()
            term.clear()
            print(f"{RED}{BOLD}{tr('fatal')}{RESET}\n")
            print(message)
            try:
                input("\n" + tr("press_enter"))
            except Exception:
                pass
        return 1
    finally:
        term.restore()
        guard.release()

