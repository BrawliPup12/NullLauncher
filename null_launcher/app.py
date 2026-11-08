from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

from . import config as cfg
from .config import APP_NAME, APP_VERSION, BOLD, DIM, LANGUAGES, MC_NAME_RE, RED, RESET, SPLASH_LINES, YELLOW, apply_runtime_preferences, tr
from .utils import brand_logo_lines, center_ansi, centered_splash_progress, clean_markup, clip, offline_uuid, proxy_game_arguments, proxy_jvm_arguments, proxy_label, safe_int, slug, tail_text, wrap_plain
from .updater import check_github_update, consume_pending_update, download_github_update, running_artifact_path, signal_update_health, spawn_update_replacer
from .state import StateStore
from .download_cache import DownloadCache
from .terminal import Menu, MenuItem, Terminal
from .catalog import LauncherData, VersionEntry
from .crash_assistant import diagnose_minecraft_crash
from .modules import ModuleContext, ModulesManagerModule, SettingsModule, VersionsModule, create_default_registry
from .news_ui import NewsScreenMixin
from .profiles_ui import ProfileScreenMixin


class NullLauncher(NewsScreenMixin, ProfileScreenMixin):
    def __init__(self, store: StateStore, term: Terminal, mll: Any, logger: logging.Logger):
        self.store = store
        apply_runtime_preferences(self.store.settings)
        self.term = term
        self.menu = Menu(term)
        self.mll = mll
        self.log = logger
        self.data = LauncherData(store, logger)
        self.running = True
        self._news_image_cache: dict[str, Any] = {}
        self.download_cache = DownloadCache(self.store.base / "download-cache", max_entries=96, max_age_seconds=30 * 24 * 60 * 60)
        self.modules = create_default_registry(
            log=logger,
            external_dirs=[self.store.base / "modules"],
        )
        self.modules.reload_all(self.module_context())

    def module_context(self) -> ModuleContext:
        return ModuleContext(self)

    def splash(self) -> None:
        import random

        tagline = random.choice(SPLASH_LINES.get(cfg.CURRENT_LANGUAGE, SPLASH_LINES["en"]))
        state = {"text": tr("preparing"), "value": 0, "max": 5}
        skip_auto_update = os.environ.pop("NULLLAUNCHER_SKIP_AUTO_UPDATE", "").strip().lower() in {"1", "true", "yes", "on"}
        auto_update_enabled = bool(self.store.settings.get("auto_update", True)) and not skip_auto_update
        update_state: dict[str, Any] = {
            "active": auto_update_enabled,
            "text": tr("checking_updates") if auto_update_enabled else "",
            "ready": None,
        }
        state_lock = threading.Lock()
        errors: list[BaseException] = []

        def on_progress(value: int, maximum: int, text: str) -> None:
            with state_lock:
                state.update(text=str(text), value=int(value), max=max(1, int(maximum)))

        def preload_worker() -> None:
            try:
                news_active = hasattr(self, "modules") and self.modules.get_active_module("news") is not None
                self.data.preload(self.mll, on_progress, load_news_module_active=news_active)
                if hasattr(self, "modules"):
                    extra_tasks = self.modules.collect_preload_tasks(self.module_context())
                    for idx, (label, task_fn) in enumerate(extra_tasks, 1):
                        on_progress(idx, max(1, len(extra_tasks)), label)
                        task_fn()
            except BaseException as exc:
                errors.append(exc)

        def update_worker() -> None:
            if not auto_update_enabled:
                return
            try:
                info = check_github_update()
                if info is None:
                    with state_lock:
                        update_state["active"] = False
                        update_state["text"] = ""
                    return
                with state_lock:
                    update_state["text"] = tr("update_downloading", version=info.version)
                downloaded = download_github_update(info, running_artifact_path())
                with state_lock:
                    update_state["ready"] = (info, downloaded)
                    update_state["text"] = tr("update_restarting", version=info.version)
            except Exception as exc:
                self.log.warning("Automatic GitHub update check failed: %s", exc)
                with state_lock:
                    update_state["active"] = False
                    update_state["text"] = ""

        def render_splash(*, animate: bool = False, warning: str = "") -> None:
            size = self.term.size()
            w = max(20, int(size.columns))
            h = max(8, int(size.lines))
            with state_lock:
                value = int(state["value"])
                maximum = int(state["max"])
                status = str(state["text"])
                update_text = str(update_state.get("text") or "") if update_state.get("active") or update_state.get("ready") else ""
            logo = brand_logo_lines(w, h)
            content_w = max(16, min(140, w - 4))
            activity = status
            if update_text:
                activity = f"{activity}  ·  {update_text}" if activity else update_text
            activity_lines = wrap_plain(activity, max(12, content_w), max_lines=3)
            content = [center_ansi(line, w) for line in logo] + [""]
            content.extend(center_ansi(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}", w) for line in wrap_plain(tagline, content_w, max_lines=2))
            content.append("")
                                                                                 
                                                                                 
            content.append(centered_splash_progress(w, value, maximum))
            for line in activity_lines:
                content.append(center_ansi(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}", w))
            if warning:
                content.extend(["", center_ansi(f"{YELLOW}{clip(warning, content_w)}{RESET}", w)])

            frame = [""] * h
            start_row = max(0, (h - len(content)) // 2)
            for i, line in enumerate(content):
                row = start_row + i
                if row < h:
                    frame[row] = line
            self.term.render(frame, animate=animate)

        self.term.clear()
        render_splash(animate=True)
        preload_thread = threading.Thread(target=preload_worker, name="NullPreloadUI", daemon=True)
        preload_thread.start()
        update_thread: Optional[threading.Thread] = None
        if auto_update_enabled:
            update_thread = threading.Thread(target=update_worker, name="NullUpdate", daemon=True)
            update_thread.start()

        last_signature: Optional[tuple[Any, ...]] = None
        while preload_thread.is_alive() or (update_thread is not None and update_thread.is_alive()):
            size = self.term.size()
            with state_lock:
                signature = (
                    size.columns, size.lines, state["value"], state["max"], state["text"],
                    update_state.get("text"), bool(update_state.get("ready")),
                )
            if signature != last_signature:
                render_splash()
                last_signature = signature
            time.sleep(0.04)
        preload_thread.join()
        if update_thread is not None:
            update_thread.join()
        if errors:
            raise errors[0]

        with state_lock:
            ready_update = update_state.get("ready")
        if isinstance(ready_update, tuple) and len(ready_update) == 2:
            info, downloaded = ready_update
            render_splash()
            time.sleep(0.20)
            self.term.restore()
            spawn_update_replacer(Path(downloaded), running_artifact_path(), info)
            raise SystemExit(0)

        warning = tr("cache_warning") if self.data.network_errors else ""
        render_splash(warning=warning)
        time.sleep(0.20)
        self.term._view_key = "__splash__"
        signal_update_health()

    def run(self) -> int:
        self.first_run_setup()
        self.splash()
        self.show_post_update()
        while self.running:
            apply_runtime_preferences(self.store.settings)
            ctx = self.module_context()
            header = self.modules.collect_header_lines(ctx)
            items, bottom_item = self.modules.build_main_menu(ctx)
            action = self.menu.choose(
                "",
                items,
                header=header,
                allow_escape=False,
                footer=tr("footer_main"),
                bottom_item=bottom_item,
                view_key="main",
            )
            try:
                if action == "exit":
                    self.running = False
                elif not self.modules.dispatch_action(ctx, action):
                    if action == "play":
                        self.play()
                    elif isinstance(action, tuple) and action and action[0] == "quick-play":
                        self.store.data["selected_version"] = str(action[1])
                        self.store.save()
                        self.play()
                    elif action == "versions":
                        self.versions_screen()
                    elif action == "accounts":
                        self.accounts_screen()
                    elif action == "proxies":
                        self.proxies_screen()
                    elif action == "modules":
                        self.modules_screen()
                    elif action == "settings":
                        self.settings_screen()
                    elif action == "news":
                        self.news_screen()
            except SystemExit:
                raise
            except Exception as exc:
                self.log.exception("Screen failed: %s", action)
                self.crash_screen(
                    tr("launcher_crash_title"),
                    f"{type(exc).__name__}: {exc}",
                    self.store.base / "null_launcher.log",
                )
        return 0

    def first_run_setup(self) -> None:
        s = self.store.settings
        if s.get("first_run_complete"):
            return
        language_items = [MenuItem(info["name"], code) for code, info in LANGUAGES.items()]
        selected_language = self.menu.choose(
            tr("first_run_title"),
            language_items,
            subtitle=tr("first_run_language"),
            allow_escape=False,
            spacer_after_title=True,
            view_key="first-run-language",
        )
        if selected_language in LANGUAGES:
            s["language"] = selected_language
            apply_runtime_preferences(s)

        raw_path = self.term.prompt(tr("first_run_minecraft_dir"), s["minecraft_dir"]).strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            if path.is_absolute():
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    s["minecraft_dir"] = str(path)
                except OSError:
                    pass

        min_ram = safe_int(self.term.prompt(tr("first_run_ram_min"), str(s["default_min_ram_mb"])), s["default_min_ram_mb"], 256, 65536)
        max_ram = safe_int(self.term.prompt(tr("first_run_ram_max"), str(s["default_max_ram_mb"])), s["default_max_ram_mb"], 512, 131072)
        s["default_min_ram_mb"] = min_ram
        s["default_max_ram_mb"] = max(min_ram, max_ram)

        while True:
            name = self.term.prompt(tr("first_run_account"), "").strip()
            if not name:
                break
            if MC_NAME_RE.fullmatch(name):
                if not any(a["name"].lower() == name.lower() for a in self.store.data["accounts"]):
                    self.store.data["accounts"].append({"name": name, "uuid": offline_uuid(name), "type": "offline"})
                self.store.data["selected_account"] = name
                break
            self.message(tr("first_run_title"), tr("first_run_invalid_account"), error=True)

        s["first_run_complete"] = True
        self.store.save()
        apply_runtime_preferences(s)
        self.message(tr("first_run_done_title"), tr("first_run_done_body"))

    def show_post_update(self) -> None:
        payload = consume_pending_update(APP_VERSION)
        if not payload:
            return
        notes = str(payload.get("notes") or "").replace("\r", "").strip()
        if notes:
            cleaned_lines: list[str] = []
            for raw in notes.splitlines():
                line = raw.strip()
                if not line:
                    if cleaned_lines and cleaned_lines[-1] != "":
                        cleaned_lines.append("")
                    continue
                line = re.sub(r"^#{1,6}\s*", "", line)
                line = re.sub(r"^[*+-]\s+", "• ", line)
                line = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", line)
                line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
                line = line.replace("`", "")
                cleaned_lines.append(line)
            notes = "\n".join(cleaned_lines[:40]).strip()
        if not notes:
            notes = tr("no_release_notes")
        old = clean_markup(payload.get("previous_version") or "?")
        new = clean_markup(payload.get("version") or APP_VERSION)
        subtitle = tr("updated_from", old=old, new=new) + "\n\n" + notes
        self.menu.choose(
            tr("whats_new_title", version=new),
            [MenuItem(tr("whats_new_continue"), "continue")],
            subtitle=subtitle,
            allow_escape=False,
            spacer_after_title=True,
            content_width_limit=120,
            view_key=f"whats-new:{new}",
        )

    def _open_path(self, path: Path) -> None:
        path = Path(path)
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def crash_screen(self, title: str, details: str, log_path: Optional[Path] = None) -> None:
        log = Path(log_path) if log_path else None
        while True:
            items: list[MenuItem] = []
            if log and log.exists():
                items.append(MenuItem(tr("open_log"), "open-log"))
                items.append(MenuItem(tr("open_log_folder"), "open-folder"))
            items.append(MenuItem(tr("return_menu"), "back"))
            subtitle = f"{details}\n\n{tr('crash_hint')}"
            action = self.menu.choose(
                f"{RED}{title}{RESET}",
                items,
                subtitle=subtitle,
                allow_escape=True,
                spacer_after_title=True,
                content_width_limit=140,
                view_key="crash-screen",
            )
            if not action or action == "back":
                return
            try:
                if action == "open-log" and log:
                    self._open_path(log)
                elif action == "open-folder" and log:
                    self._open_path(log.parent)
            except Exception as exc:
                self.log.warning("Could not open crash log: %s", exc)
                self.message(tr("open_folder_title"), str(exc), error=True)

    def manual_update_check(self) -> None:
        """Check GitHub Releases on demand; install a newer release immediately."""
        def worker(callbacks: dict[str, Callable]) -> Any:
            callbacks["setStatus"](tr("checking_updates"))
            info = check_github_update(timeout=7.0, allow_failed_retry=True)
            if info is None:
                return None
            callbacks["setStatus"](tr("update_downloading", version=info.version))
            downloaded = download_github_update(info, running_artifact_path(), timeout=20.0)
            callbacks["setStatus"](tr("update_installing", version=info.version))
            return info, downloaded

        try:
            result = self.progress_task(tr("settings_updates"), worker)
        except Exception as exc:
            self.log.warning("Manual GitHub update check failed: %s", exc)
            self.message(tr("settings_updates"), tr("update_failed", error=clean_markup(exc)), error=True)
            return
        if not result:
            self.message(tr("settings_updates"), tr("update_none"))
            return
        info, downloaded = result
        self.term.restore()
        spawn_update_replacer(Path(downloaded), running_artifact_path(), info)
        raise SystemExit(0)

                                                                             

    def message(self, title: str, text: str, *, error: bool = False) -> None:
        items = [MenuItem(tr("back"), "back")]
        color = RED if error else cfg.PRIMARY_COLOR
                                                                               
        self.menu.choose(
            f"{color}{title}{RESET}",
            items,
            subtitle=str(text),
            allow_escape=True,
            content_width_limit=120,
        )

    def confirm(self, title: str, text: str) -> bool:
        result = self.menu.choose(title, [MenuItem(tr("yes"), True), MenuItem(tr("no"), False)], subtitle=text)
        return bool(result)

    def progress_task(self, title: str, fn: Callable[[dict[str, Callable]], Any]) -> Any:
        q: queue.Queue[tuple[str, Any]] = queue.Queue()
        state = {"status": tr("preparing"), "progress": 0, "max": 0, "done": False, "error": None, "result": None}

        def set_status(value: Any) -> None:
            q.put(("status", str(value)))

        def set_progress(value: Any) -> None:
            q.put(("progress", int(value)))

        def set_max(value: Any) -> None:
            q.put(("max", int(value)))

        callbacks = {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

        def worker() -> None:
            try:
                result = fn(callbacks)
                q.put(("result", result))
            except BaseException as exc:
                q.put(("error", exc))
            finally:
                q.put(("done", True))

        thread = threading.Thread(target=worker, name="NullTask", daemon=True)
        thread.start()
        self.term._view_key = "progress:" + clean_markup(title)
        first_frame = True
        while not state["done"]:
            try:
                while True:
                    key, value = q.get_nowait()
                    state[key] = value
            except queue.Empty:
                pass
            size = self.term.size()
            w = max(20, int(size.columns))
            h = max(8, int(size.lines))
            content_w = max(16, min(140, w - 4))
            bar_w = max(8, min(60, content_w - 12))
            maxv = int(state["max"] or 0)
            prog = int(state["progress"] or 0)
            if maxv > 0:
                ratio = max(0.0, min(1.0, prog / maxv))
                done = int(bar_w * ratio)
                bar = "█" * done + "░" * (bar_w - done)
                metric = f"{prog}/{maxv}"
            else:
                phase = int(time.monotonic() * 10) % bar_w
                bar = "░" * phase + "█" + "░" * max(0, bar_w - phase - 1)
                metric = "…"
            lines = [""] * h
            content: list[str] = brand_logo_lines(w, h) + [""]
            content.extend(f"{BOLD}{line}{RESET}" for line in wrap_plain(title, content_w, max_lines=2))
            content.append("")
            content.append(f"{cfg.PRIMARY_COLOR}{bar}{RESET}  {metric}")
            content.extend(f"{DIM}{line}{RESET}" for line in wrap_plain(str(state['status']), content_w, max_lines=2))
            if h >= 16:
                content.extend(["", f"{cfg.SUBTITLE_COLOR}{DIM}{tr("do_not_close")}{RESET}"])
            start = max(0, (len(lines) - len(content)) // 2)
            for i, line in enumerate(content):
                if start + i < len(lines):
                    lines[start + i] = center_ansi(line, w)
            self.term.render(lines, animate=first_frame)
            first_frame = False
            time.sleep(0.05)
        thread.join()
        while not q.empty():
            key, value = q.get_nowait()
            state[key] = value
        if state["error"] is not None:
            raise state["error"]
        return state["result"]

                                                                            




                                                                             




                                                                             

    def versions_screen(self) -> None:
        ctx = self.module_context()
        modules = getattr(self, "modules", None)
        if modules is not None:
            mod = modules.get_active_module("versions")
            if mod is not None:
                mod.open_screen(ctx)
                return
        VersionsModule().versions_screen(ctx)

    def version_detail(self, entry: VersionEntry) -> None:
        VersionsModule().version_detail(self.module_context(), entry)

    def install_version(self, entry: VersionEntry) -> bool:
        return VersionsModule().install_version(self.module_context(), entry)

    def delete_version(self, entry: VersionEntry) -> bool:
        return VersionsModule().delete_version(self.module_context(), entry)

    def version_settings_screen(self, installed_id: str) -> None:
        VersionsModule().version_settings_screen(self.module_context(), installed_id)

    def modules_screen(self) -> None:
        ctx = self.module_context()
        modules = getattr(self, "modules", None)
        if modules is not None:
            mod = modules.get_active_module("modules")
            if mod is not None:
                mod.open_screen(ctx)
                return
        ModulesManagerModule().modules_screen(ctx)

    def settings_screen(self) -> None:
        ctx = self.module_context()
        modules = getattr(self, "modules", None)
        if modules is not None:
            mod = modules.get_active_module("settings")
            if mod is not None:
                mod.open_screen(ctx)
                return
        SettingsModule().settings_screen(ctx)
                                                                                  
                                                                              


    def _build_launch_options(self, account: dict[str, Any], version_id: str, auto_java_path: str = "") -> dict[str, Any]:
        cfg = self.store.version_config(version_id)
        if cfg["separate_game_dir"]:
            game_dir = self.store.base / "instances" / slug(version_id)
            game_dir.mkdir(parents=True, exist_ok=True)
        else:
            game_dir = self.store.minecraft_dir
        jvm_args = [f"-Xms{cfg['min_ram_mb']}M", f"-Xmx{cfg['max_ram_mb']}M", "-Dfile.encoding=UTF-8"]
        if os.name == "nt":
            jvm_args.extend(["-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8"])
        jvm_args.extend(proxy_jvm_arguments(self.store.proxy_profile()))
        options: dict[str, Any] = {
            "username": account["name"],
            "uuid": account["uuid"],
            "token": "0",
            "launcherName": APP_NAME,
            "launcherVersion": APP_VERSION,
            "jvmArguments": jvm_args,
            "gameDirectory": str(game_dir),
            "customResolution": bool(cfg["custom_resolution"]),
            "resolutionWidth": str(cfg["resolution_width"]),
            "resolutionHeight": str(cfg["resolution_height"]),
        }
        java = self.store.settings.get("custom_java_path") or auto_java_path
        if java:
            options["executablePath"] = java
            options["defaultExecutablePath"] = java
        return options

    def _render_launch_progress(
        self,
        version_id: str,
        stage: int,
        total: int,
        stage_title: str,
        status: str = "",
        progress: int = 0,
        maximum: int = 0,
        *,
        animate: bool = False,
    ) -> None:
        size = self.term.size()
        w = max(20, int(size.columns))
        h = max(8, int(size.lines))
        content_w = max(16, min(140, w - 4))
        overall_w = max(10, min(54, content_w - 18))
        overall_ratio = max(0.0, min(1.0, stage / max(1, total)))
        overall_done = int(overall_w * overall_ratio)
        overall = "█" * overall_done + "░" * (overall_w - overall_done)
        content: list[str] = brand_logo_lines(w, h) + [""]
        content.extend(f"{BOLD}{line}{RESET}" for line in wrap_plain(tr("launching_title", version=version_id), content_w, max_lines=2))
        content.append(f"{cfg.SUBTITLE_COLOR}{DIM}{tr('launch_step', current=stage, total=total)}{RESET}")
        content.append(f"{cfg.PRIMARY_COLOR}{overall}{RESET}")
        content.append("")
        content.extend(f"{BOLD}{line}{RESET}" for line in wrap_plain(stage_title, content_w, max_lines=2))

        if maximum > 0:
            file_w = max(8, min(64, content_w - 18))
            ratio = max(0.0, min(1.0, int(progress) / max(1, int(maximum))))
            done = int(file_w * ratio)
            bar = "█" * done + "░" * (file_w - done)
            percent = int(round(ratio * 100))
            content.append(f"{cfg.PRIMARY_COLOR}{bar}{RESET}  {percent:3d}%")
        else:
            spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.monotonic() * 10) % 10]
            content.append(f"{cfg.PRIMARY_COLOR}{spinner}{RESET}")
        if status:
            content.extend(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}" for line in wrap_plain(status, content_w, max_lines=3))
        if h >= 18:
            content.extend(["", f"{cfg.SUBTITLE_COLOR}{DIM}{tr('do_not_close')}{RESET}"])
        frame = [""] * h
        start = max(0, (h - len(content)) // 2)
        for i, line in enumerate(content):
            if start + i < h:
                frame[start + i] = center_ansi(line, w)
        self.term._view_key = f"launch:{version_id}:{stage}"
        self.term.render(frame, animate=animate)

    def _launch_progress_task(
        self,
        version_id: str,
        stage: int,
        total: int,
        stage_title: str,
        fn: Callable[[dict[str, Callable]], Any],
    ) -> Any:
        q: queue.Queue[tuple[str, Any]] = queue.Queue()
        state = {"status": stage_title, "progress": 0, "max": 0, "done": False, "error": None, "result": None}

        callbacks = {
            "setStatus": lambda value: q.put(("status", str(value))),
            "setProgress": lambda value: q.put(("progress", int(value))),
            "setMax": lambda value: q.put(("max", int(value))),
        }

        def worker() -> None:
            try:
                q.put(("result", fn(callbacks)))
            except BaseException as exc:
                q.put(("error", exc))
            finally:
                q.put(("done", True))

        thread = threading.Thread(target=worker, name="NullLaunchTask", daemon=True)
        thread.start()
        first = True
        while not state["done"]:
            try:
                while True:
                    key, value = q.get_nowait()
                    state[key] = value
            except queue.Empty:
                pass
            self._render_launch_progress(
                version_id, stage, total, stage_title,
                str(state["status"]), int(state["progress"]), int(state["max"]),
                animate=first,
            )
            first = False
            time.sleep(0.05)
        thread.join()
        while not q.empty():
            key, value = q.get_nowait()
            state[key] = value
        if state["error"] is not None:
            raise state["error"]
        return state["result"]

    def _ensure_legacy_java(self, version_id: str, launch_stage: Optional[tuple[int, int]] = None) -> str:
        if self.store.settings.get("custom_java_path"):
            return ""
        try:
            info = self.mll.runtime.get_version_runtime_information(version_id, str(self.store.minecraft_dir))
            runtime_name = str(info.get("name") or "") if isinstance(info, dict) else "jre-legacy"
        except Exception:
            runtime_name = "jre-legacy"
        if not runtime_name:
            runtime_name = "jre-legacy"
        try:
            path = self.mll.runtime.get_executable_path(runtime_name, str(self.store.minecraft_dir))
            if path:
                return str(path)
            available = list(self.mll.runtime.get_jvm_runtimes())
            if runtime_name not in available:
                return ""
            if launch_stage:
                stage, total = launch_stage
                self._launch_progress_task(
                    version_id, stage, total, tr("launch_stage_java"),
                    lambda cb: self.mll.runtime.install_jvm_runtime(runtime_name, str(self.store.minecraft_dir), callback=cb),
                )
            else:
                self.progress_task(
                    tr("install_java_runtime"),
                    lambda cb: self.mll.runtime.install_jvm_runtime(runtime_name, str(self.store.minecraft_dir), callback=cb),
                )
            path = self.mll.runtime.get_executable_path(runtime_name, str(self.store.minecraft_dir))
            return str(path or "")
        except Exception as exc:
            self.log.warning("Java runtime auto-install unavailable: %s", exc)
            return ""

    def _execute_play(self) -> None:
        self.play(_bypass_override=True)

    def play(self, *, _bypass_override: bool = False) -> None:
        modules = getattr(self, "modules", None)
        if not _bypass_override and modules is not None and modules.is_replaced("play"):
            mod = modules.get_active_module("play")
            if mod is not None:
                mod.open_screen(self.module_context())
                return

        account = self.store.account()
        version_id = self.store.data.get("selected_version")
        if not account:
            self.message(tr("no_account_title"), tr("no_account_body"), error=True)
            return
        if not version_id:
            self.message(tr("no_version_title"), tr("no_version_body"), error=True)
            return
        version_json = self.store.minecraft_dir / "versions" / version_id / f"{version_id}.json"
        if not version_json.exists():
            # Do not leave stale Quick Play entries pointing at a deleted/moved version.
            self.store.data["selected_version"] = None
            self.store.data["favorite_versions"] = [
                item for item in self.store.favorite_versions() if item != version_id
            ]
            if self.store.last_launch().get("version") == version_id:
                self.store.data["last_launch"] = {}
            self.store.save()
            self.message(tr("version_missing_title"), tr("version_missing_body", version=version_id), error=True)
            return

        ctx = self.module_context()
        log_path = self.store.base / "game-latest.log"
        launcher_log = self.store.base / "null_launcher.log"
        total_stages = 5
        try:
            self._render_launch_progress(version_id, 1, total_stages, tr("launch_stage_prepare"), tr("preparing"), animate=True)

            if self.store.settings["repair_before_launch"]:
                self._launch_progress_task(
                    version_id, 2, total_stages, tr("launch_stage_verify"),
                    lambda cb: self.mll.install.install_minecraft_version(version_id, str(self.store.minecraft_dir), callback=cb),
                )
            else:
                self._render_launch_progress(version_id, 2, total_stages, tr("launch_stage_verify"), tr("launch_skipped"))

            self._render_launch_progress(version_id, 3, total_stages, tr("launch_stage_java"), tr("preparing"))
            auto_java = self._ensure_legacy_java(version_id, (3, total_stages))

            self._render_launch_progress(version_id, 4, total_stages, tr("launch_stage_command"), tr("preparing"))
            options = self._build_launch_options(account, version_id, auto_java)
            if modules is not None:
                modules.run_before_launch(ctx, version_id, options)
            command = self._launch_progress_task(
                version_id, 4, total_stages, tr("launch_stage_command"),
                lambda cb: self.mll.command.get_minecraft_command(version_id, str(self.store.minecraft_dir), options),
            )
            if not command:
                raise RuntimeError(tr("empty_launch_command"))
            parts = re.findall(r"\d+", version_id)
            if parts and len(parts) >= 2:
                try:
                    major, minor = int(parts[0]), int(parts[1])
                    if major < 1 or (major == 1 and minor < 13):
                        command.extend(proxy_game_arguments(self.store.proxy_profile()))
                except (ValueError, IndexError):
                    pass
            if modules is not None:
                command = modules.run_modify_command(ctx, version_id, command, options)

            self._render_launch_progress(version_id, 5, total_stages, tr("launch_stage_start"), tr("preparing"))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            game_cwd = Path(options["gameDirectory"])
            game_cwd.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("w", encoding="utf-8", errors="replace")
            creationflags = 0
            if os.name == "nt":
                # The launcher itself is a GUI executable now.  Starting java.exe
                # without CREATE_NO_WINDOW makes Windows allocate a stray CMD
                # window even though Minecraft renders its own LWJGL window.
                creationflags = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(game_cwd),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            finally:
                log_file.close()

            self.store.record_launch(version_id, account.get("name", ""))
            if self.store.settings["close_launcher_on_game_start"]:
                self.running = False
                return

            active_proxy = self.store.proxy_profile()
            proxy_line = f"{tr('proxy_label')}: {active_proxy['name']} · {proxy_label(active_proxy)}" if active_proxy else f"{tr('proxy_label')}: {tr('none')}"
            self.term._view_key = f"game-running:{version_id}"
            first_game_frame = True
            while process.poll() is None:
                if hasattr(self.term, "_pump"):
                    try:
                        self.term._pump()
                    except KeyboardInterrupt:
                        with contextlib.suppress(Exception):
                            process.terminate()
                        raise
                size = self.term.size()
                w, h = max(20, int(size.columns)), max(8, int(size.lines))
                content_w = max(16, min(140, w - 4))
                content: list[str] = brand_logo_lines(w, h) + [""]
                content.extend(f"{BOLD}{line}{RESET}" for line in wrap_plain(tr("minecraft_running", version=version_id), content_w, max_lines=2))
                for text in (f"{tr('player_label')}: {account['name']}", proxy_line, f"PID {process.pid} · {tr('log_label')}: {log_path}"):
                    content.extend(wrap_plain(text, content_w, max_lines=2))
                if h >= 14:
                    content.extend(["", tr("launcher_returns")])
                frame = [""] * h
                start = max(0, (h - len(content)) // 2)
                for i, line in enumerate(content):
                    if start + i < h:
                        frame[start + i] = center_ansi(line, w)
                self.term.render(frame, animate=first_game_frame)
                first_game_frame = False
                time.sleep(0.10)

            code = int(process.returncode or 0)
            if modules is not None:
                modules.run_after_launch(ctx, version_id, code)
            if code != 0:
                crash_log_path = log_path
                hs_errs = list(game_cwd.glob(f"hs_err_pid{process.pid}.log")) or list(game_cwd.glob("hs_err_pid*.log"))
                if hs_errs:
                    hs_errs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
                    crash_log_path = hs_errs[0]
                else:
                    crash_reports_dir = game_cwd / "crash-reports"
                    if crash_reports_dir.is_dir():
                        reports = list(crash_reports_dir.glob("crash-*.txt"))
                        if reports:
                            reports.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
                            if time.time() - reports[0].stat().st_mtime < 120:
                                crash_log_path = reports[0]

                diagnostic_tail = tail_text(crash_log_path, 300)
                hints = diagnose_minecraft_crash(diagnostic_tail)
                if not hints and crash_log_path != log_path:
                    hints = diagnose_minecraft_crash(tail_text(log_path, 300))
                assistant = ""
                if hints:
                    assistant = "\n\n" + tr("crash_assistant_title") + ":\n" + "\n".join(f"• {tr(item.message_key)}" for item in hints)
                tail = tail_text(crash_log_path, 28)
                details = f"{tr('exit_code')}: {code}{assistant}\n\n{tr('last_log_lines')} ({crash_log_path.name}):\n{tail or tr('empty_log')}"
                self.crash_screen(tr("game_crash_title"), details, crash_log_path)
        except Exception as exc:
            self.log.exception("Launch failed")
            self.crash_screen(
                tr("launch_error_title"),
                f"{type(exc).__name__}: {exc}\n\n{tr('see_launcher_log')}",
                launcher_log,
            )

