from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Optional
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
import webbrowser

from null_launcher.config import APP_NAME, APP_VERSION, section_label, tr
from null_launcher.modules import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata
from null_launcher.terminal import MenuItem
from null_launcher.utils import clean_markup, clip, slug

MODRINTH_API_BASE = "https://api.modrinth.com/v2"
MODRINTH_WEB_MOD = "https://modrinth.com/mod/"
MODRINTH_USER_AGENT = f"{APP_NAME}/{APP_VERSION} (https://github.com/BrawliPup12/NullLauncher)"
MAX_MOD_JAR_BYTES = 256 * 1024 * 1024
ALLOWED_MODRINTH_HOSTS = {
    "api.modrinth.com",
    "cdn.modrinth.com",
    "cdn-raw.modrinth.com",
}


def _validate_modrinth_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host in ALLOWED_MODRINTH_HOSTS or host.endswith(".modrinth.com")
    ):
        raise ValueError(f"Untrusted Modrinth URL: {url}")
    return parsed.geturl()


def _http_get_json(url: str, timeout: float = 12.0) -> Any:
    safe_url = _validate_modrinth_url(url)
    req = Request(
        safe_url,
        headers={
            "User-Agent": MODRINTH_USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read(8 * 1024 * 1024)
    return json.loads(raw.decode("utf-8", errors="replace"))


def _format_downloads(count: Any) -> str:
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _format_size(num_bytes: Any) -> str:
    try:
        b = int(num_bytes or 0)
    except (TypeError, ValueError):
        return "? MB"
    if b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b} B"


def _safe_jar_filename(raw_name: str) -> str:
    name = Path(str(raw_name or "").strip()).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    if not name.lower().endswith(".jar"):
        name += ".jar"
    if name in {".jar", "..jar"} or ".." in name:
        raise ValueError(f"Unsafe mod filename: {raw_name}")
    return name


def detect_version_context(ctx: ModuleContext) -> tuple[str, str, Path]:
    if ctx.store is None:
        return "", "fabric", Path("mods")

    selected_id = str(ctx.store.data.get("selected_version") or "").strip()
    managed = ctx.store.data.get("managed_versions", {})
    mc_version = ""
    loader_id = ""

    if selected_id and isinstance(managed, dict):
        for item in managed.values():
            if isinstance(item, dict) and str(item.get("installed_id") or "") == selected_id:
                mc_version = str(item.get("mc_version") or "").strip()
                loader_id = str(item.get("loader_id") or "").strip().lower()
                break

    if selected_id and not loader_id:
        low = selected_id.lower()
        for candidate in ("neoforge", "forge", "fabric", "quilt"):
            if candidate in low:
                loader_id = candidate
                break

    if selected_id and not mc_version:
        m = re.search(r"\b(1\.\d+(?:\.\d+)?)\b", selected_id)
        if m:
            mc_version = m.group(1)

    if selected_id and ctx.store.version_config(selected_id).get("separate_game_dir"):
        mods_dir = ctx.store.base / "instances" / slug(selected_id) / "mods"
    else:
        mods_dir = ctx.store.minecraft_dir / "mods"

    return mc_version, loader_id, mods_dir


def search_modrinth_mods(
    query: str = "",
    *,
    loader: str = "",
    mc_version: str = "",
    limit: int = 24,
) -> list[dict[str, Any]]:
    facets: list[list[str]] = [["project_type:mod"]]
    if loader and loader not in ("all", "vanilla"):
        facets.append([f"categories:{loader.lower()}"])
    if mc_version and mc_version != "all":
        facets.append([f"versions:{mc_version}"])

    params = {
        "query": query.strip(),
        "limit": str(max(1, min(50, int(limit)))),
        "index": "relevance" if query.strip() else "downloads",
        "facets": json.dumps(facets, separators=(",", ":")),
    }
    url = f"{MODRINTH_API_BASE}/search?{urlencode(params)}"
    payload = _http_get_json(url)
    hits = payload.get("hits", []) if isinstance(payload, dict) else []
    return [h for h in hits if isinstance(h, dict)]


def fetch_modrinth_versions(
    project_id_or_slug: str,
    *,
    loader: str = "",
    mc_version: str = "",
) -> list[dict[str, Any]]:
    pid = quote(str(project_id_or_slug).strip(), safe="")
    params: dict[str, str] = {}
    if loader and loader not in ("all", "vanilla"):
        params["loaders"] = json.dumps([loader.lower()])
    if mc_version and mc_version != "all":
        params["game_versions"] = json.dumps([mc_version])
    query_str = f"?{urlencode(params)}" if params else ""
    url = f"{MODRINTH_API_BASE}/project/{pid}/version{query_str}"
    data = _http_get_json(url)
    if isinstance(data, list) and data:
        return [v for v in data if isinstance(v, dict)]
    if params.get("loaders"):
        fallback_params = {"game_versions": params["game_versions"]} if "game_versions" in params else {}
        fallback_qs = f"?{urlencode(fallback_params)}" if fallback_params else ""
        data2 = _http_get_json(f"{MODRINTH_API_BASE}/project/{pid}/version{fallback_qs}")
        if isinstance(data2, list):
            return [v for v in data2 if isinstance(v, dict)]
    return []


def pick_primary_jar_file(version_obj: dict[str, Any]) -> Optional[dict[str, Any]]:
    files = version_obj.get("files", [])
    if not isinstance(files, list) or not files:
        return None
    jar_files = [
        f
        for f in files
        if isinstance(f, dict)
        and str(f.get("filename") or "").lower().endswith(".jar")
        and str(f.get("url") or "").startswith("https://")
    ]
    if not jar_files:
        return None
    return next((f for f in jar_files if f.get("primary")), jar_files[0])


def _download_jar_verified(
    file_info: dict[str, Any],
    mods_dir: Path,
    callbacks: dict[str, Callable],
    status_label: str,
) -> Path:
    url = _validate_modrinth_url(str(file_info.get("url") or ""))
    filename = _safe_jar_filename(str(file_info.get("filename") or "mod.jar"))
    hashes = file_info.get("hashes") if isinstance(file_info.get("hashes"), dict) else {}
    expected_sha512 = str(hashes.get("sha512") or "").strip().lower()
    expected_sha1 = str(hashes.get("sha1") or "").strip().lower()

    mods_root = mods_dir.expanduser().resolve()
    mods_root.mkdir(parents=True, exist_ok=True)
    target = (mods_root / filename).resolve()
    if target.parent != mods_root:
        raise RuntimeError(f"Unsafe target path: {target}")
    part = target.with_suffix(target.suffix + ".part")

    req = Request(url, headers={"User-Agent": MODRINTH_USER_AGENT})
    h512 = hashlib.sha512()
    h1 = hashlib.sha1()
    total_read = 0

    callbacks["setStatus"](status_label)
    with urlopen(req, timeout=25.0) as resp:
        total_size = int(resp.headers.get("Content-Length") or file_info.get("size") or 0)
        callbacks["setMax"](max(0, total_size))
        callbacks["setProgress"](0)
        with part.open("wb") as out:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > MAX_MOD_JAR_BYTES:
                    raise RuntimeError("Mod file exceeds maximum allowed size")
                out.write(chunk)
                h512.update(chunk)
                h1.update(chunk)
                if total_size > 0:
                    callbacks["setProgress"](min(total_read, total_size))

    if expected_sha512 and h512.hexdigest().lower() != expected_sha512:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-512 mismatch for {filename}")
    if expected_sha1 and h1.hexdigest().lower() != expected_sha1:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-1 mismatch for {filename}")

    os.replace(part, target)
    return target


class ModrinthModule(LauncherModule):
    meta = ModuleMetadata(
        id="modrinth",
        name_key="modrinth",
        description_key="module_modrinth_desc",
        version=APP_VERSION,
        author="Community",
        order=25,
        can_disable=True,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [
            MenuContribution(
                id="modrinth",
                label=tr("modrinth"),
                order=self.meta.order,
                module_id=self.meta.id,
            )
        ]

    def _list_local_mod_files(self, mods_dir: Path) -> list[Path]:
        if not mods_dir.is_dir():
            return []
        files: list[Path] = []
        for item in sorted(mods_dir.iterdir(), key=lambda p: p.name.lower()):
            if item.is_file() and (
                item.name.lower().endswith(".jar") or item.name.lower().endswith(".jar.disabled")
            ):
                files.append(item)
        return files

    def _installed_project_map(self, ctx: ModuleContext, mods_dir: Path) -> dict[str, str]:
        state = ctx.module_state("modrinth")
        raw = state.get("installed_projects", {})
        if not isinstance(raw, dict):
            raw = {}
            state["installed_projects"] = raw
        existing_names = {p.name for p in self._list_local_mod_files(mods_dir)}
        cleaned: dict[str, str] = {}
        for pid, fname in list(raw.items()):
            fname_str = str(fname or "")
            if fname_str in existing_names or (fname_str + ".disabled") in existing_names:
                cleaned[str(pid)] = fname_str
        return cleaned

    def _is_hit_installed(
        self,
        hit: dict[str, Any],
        installed_map: dict[str, str],
        local_files: list[Path],
    ) -> Optional[Path]:
        pid = str(hit.get("project_id") or hit.get("id") or "")
        mod_slug = str(hit.get("slug") or "").lower()
        if pid in installed_map:
            fname = installed_map[pid]
            for f in local_files:
                if f.name == fname or f.name == fname + ".disabled":
                    return f
        if mod_slug:
            norm_slug = mod_slug.replace("-", "").replace("_", "")
            for f in local_files:
                stem = f.name.lower().removesuffix(".disabled").removesuffix(".jar")
                norm_stem = stem.replace("-", "").replace("_", "")
                if norm_stem.startswith(norm_slug):
                    return f
        return None

    def _open_folder(self, ctx: ModuleContext, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if ctx.launcher is not None and hasattr(ctx.launcher, "_open_path"):
                ctx.launcher._open_path(folder)
            elif os.name == "nt":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            ctx.message(tr("open_folder_title"), str(exc), error=True)

    def open_screen(self, ctx: ModuleContext) -> None:
        if ctx.menu is None:
            return
        state = ctx.module_state("modrinth")
        query = str(state.get("last_query") or "")
        loader_mode = str(state.get("loader_mode") or "auto")
        mc_mode = str(state.get("mc_mode") or "auto")
        loader_cycle = ["auto", "fabric", "forge", "neoforge", "quilt", "all"]
        if loader_mode not in loader_cycle:
            loader_mode = "auto"

        cached_hits: Optional[list[dict[str, Any]]] = None
        last_search_key: Optional[tuple[str, str, str]] = None
        last_error = ""

        while True:
            auto_mc, auto_loader, mods_dir = detect_version_context(ctx)
            effective_loader = (auto_loader or "fabric") if loader_mode == "auto" else loader_mode
            effective_mc = auto_mc if mc_mode == "auto" else ("" if mc_mode == "all" else mc_mode)

            search_key = (query, effective_loader, effective_mc)
            if cached_hits is None or search_key != last_search_key:
                last_error = ""
                try:
                    cached_hits = ctx.progress_task(
                        tr("modrinth_loading"),
                        lambda _cb, q=query, ld=effective_loader, mv=effective_mc: search_modrinth_mods(
                            q, loader=ld, mc_version=mv
                        ),
                    )
                except Exception as exc:
                    ctx.log.warning("Modrinth search failed: %s", exc)
                    cached_hits = []
                    last_error = clean_markup(exc)
                last_search_key = search_key

            local_files = self._list_local_mod_files(mods_dir)
            installed_map = self._installed_project_map(ctx, mods_dir)

            loader_display = (
                tr("modrinth_auto_fmt", value=(auto_loader or "fabric").capitalize())
                if loader_mode == "auto"
                else (tr("all") if loader_mode == "all" else loader_mode.capitalize())
            )
            mc_display = (
                tr("modrinth_auto_fmt", value=auto_mc or tr("all"))
                if mc_mode == "auto"
                else (tr("all") if mc_mode == "all" else mc_mode)
            )
            query_display = query or tr("modrinth_search_popular")

            items: list[MenuItem] = [
                MenuItem(f"{tr('search')}: {query_display}", ("search", None)),
                MenuItem(f"{tr('modrinth_loader_filter')}: {loader_display}", ("loader", None)),
                MenuItem(f"{tr('modrinth_mc_filter')}: {mc_display}", ("mc_ver", None)),
                MenuItem(tr("modrinth_installed_btn", count=len(local_files)), ("installed", None)),
                MenuItem(tr("modrinth_open_mods_folder"), ("open_folder", None)),
                MenuItem(tr("modrinth_refresh"), ("refresh", None)),
                MenuItem(section_label(tr("modrinth_section_catalog")), selectable=False),
            ]

            if last_error:
                items.append(
                    MenuItem(
                        tr("modrinth_network_error", error=clip(last_error, 60)),
                        selectable=False,
                    )
                )
            elif not cached_hits:
                items.append(MenuItem(tr("modrinth_no_results"), selectable=False))
            else:
                for idx, hit in enumerate(cached_hits):
                    title = clean_markup(hit.get("title") or hit.get("slug") or f"Mod {idx + 1}")
                    installed_file = self._is_hit_installed(hit, installed_map, local_files)
                    mark = "✓ " if installed_file is not None else "  "
                    dl = _format_downloads(hit.get("downloads"))
                    desc = clip(clean_markup(hit.get("description") or ""), 52)
                    hint = f"↓{dl} · {desc}" if desc else f"↓{dl}"
                    items.append(MenuItem(f"{mark}{title}", ("hit", idx), hint=hint))

            items.append(MenuItem(tr("back"), ("back", None)))
            subtitle = tr(
                "modrinth_subtitle",
                mc=effective_mc or tr("all"),
                loader=effective_loader.capitalize() if effective_loader != "all" else tr("all"),
                installed=len(local_files),
            )
            action = ctx.menu.choose(
                tr("modrinth"),
                items,
                subtitle=subtitle,
                spacer_after_title=True,
                view_key="modrinth-catalog",
            )
            if not action or action[0] == "back":
                return
            if action[0] == "search":
                new_q = ctx.prompt(tr("search"), query).strip()
                query = "" if new_q == "-" else new_q
                state["last_query"] = query
                ctx.save_state()
            elif action[0] == "loader":
                loader_mode = loader_cycle[(loader_cycle.index(loader_mode) + 1) % len(loader_cycle)]
                state["loader_mode"] = loader_mode
                ctx.save_state()
            elif action[0] == "mc_ver":
                raw_mc = ctx.prompt(tr("modrinth_mc_prompt"), mc_mode if mc_mode not in ("auto", "all") else "").strip()
                if not raw_mc or raw_mc.lower() in ("-", "auto"):
                    mc_mode = "auto"
                elif raw_mc in ("*", "all"):
                    mc_mode = "all"
                else:
                    mc_mode = raw_mc
                state["mc_mode"] = mc_mode
                ctx.save_state()
            elif action[0] == "refresh":
                cached_hits = None
            elif action[0] == "open_folder":
                self._open_folder(ctx, mods_dir)
            elif action[0] == "installed":
                self.installed_mods_screen(ctx, mods_dir)
            elif action[0] == "hit" and cached_hits and 0 <= action[1] < len(cached_hits):
                self.mod_detail_screen(
                    ctx,
                    cached_hits[action[1]],
                    loader=effective_loader,
                    mc_version=effective_mc,
                    mods_dir=mods_dir,
                )

    def mod_detail_screen(
        self,
        ctx: ModuleContext,
        hit: dict[str, Any],
        *,
        loader: str,
        mc_version: str,
        mods_dir: Path,
    ) -> None:
        if ctx.menu is None:
            return
        pid = str(hit.get("project_id") or hit.get("id") or hit.get("slug") or "")
        mod_slug = str(hit.get("slug") or pid)
        title = clean_markup(hit.get("title") or mod_slug)
        author = clean_markup(hit.get("author") or "Modrinth")
        downloads = _format_downloads(hit.get("downloads"))
        summary = clean_markup(hit.get("description") or "")

        versions: list[dict[str, Any]] = []
        try:
            versions = ctx.progress_task(
                tr("modrinth_loading_versions"),
                lambda _cb: fetch_modrinth_versions(pid, loader=loader, mc_version=mc_version),
            )
        except Exception as exc:
            ctx.log.warning("Failed to fetch Modrinth versions for %s: %s", pid, exc)

        best_version = versions[0] if versions else None
        primary_file = pick_primary_jar_file(best_version) if best_version else None

        while True:
            local_files = self._list_local_mod_files(mods_dir)
            installed_map = self._installed_project_map(ctx, mods_dir)
            installed_path = self._is_hit_installed(hit, installed_map, local_files)

            sub_lines = [
                tr("modrinth_downloads_label", downloads=downloads, author=author),
            ]
            if primary_file is not None:
                fname = str(primary_file.get("filename") or "mod.jar")
                fsize = _format_size(primary_file.get("size"))
                sub_lines.append(tr("modrinth_compatible_file", file=fname, size=fsize))
            else:
                sub_lines.append(
                    tr(
                        "modrinth_no_compatible_version",
                        mc=mc_version or tr("all"),
                        loader=loader.capitalize() if loader != "all" else tr("all"),
                    )
                )
            if summary:
                sub_lines.extend(["", summary])

            items: list[MenuItem] = []
            if primary_file is not None and best_version is not None:
                fname = str(primary_file.get("filename") or "mod.jar")
                btn_key = "modrinth_reinstall_btn" if installed_path is not None else "modrinth_install_btn"
                items.append(MenuItem(tr(btn_key, file=fname), "install"))
            if installed_path is not None:
                items.append(MenuItem(tr("modrinth_delete_btn", file=installed_path.name), "delete"))
            items.append(MenuItem(tr("modrinth_open_web"), "web"))
            items.append(MenuItem(tr("back"), "back"))

            action = ctx.menu.choose(
                title,
                items,
                subtitle="\n".join(sub_lines),
                spacer_after_title=True,
                view_key=f"modrinth-detail:{pid}",
            )
            if not action or action == "back":
                return
            if action == "install" and best_version is not None and primary_file is not None:
                self._install_mod_and_dependencies(
                    ctx,
                    project_id=pid,
                    title=title,
                    version_obj=best_version,
                    primary_file=primary_file,
                    loader=loader,
                    mc_version=mc_version,
                    mods_dir=mods_dir,
                )
            elif action == "delete" and installed_path is not None:
                if ctx.confirm(title, tr("modrinth_delete_confirm", file=installed_path.name)):
                    installed_path.unlink(missing_ok=True)
                    ctx.module_state("modrinth").setdefault("installed_projects", {}).pop(pid, None)
                    ctx.save_state()
            elif action == "web":
                web_url = f"{MODRINTH_WEB_MOD}{quote(mod_slug, safe='')}"
                try:
                    if not webbrowser.open(web_url):
                        raise RuntimeError(tr("browser_open_failed"))
                except Exception as exc:
                    ctx.message(tr("browser"), f"{exc}\n\n{web_url}", error=True)

    def _install_mod_and_dependencies(
        self,
        ctx: ModuleContext,
        *,
        project_id: str,
        title: str,
        version_obj: dict[str, Any],
        primary_file: dict[str, Any],
        loader: str,
        mc_version: str,
        mods_dir: Path,
    ) -> None:
        installed_records = ctx.module_state("modrinth").setdefault("installed_projects", {})
        if not isinstance(installed_records, dict):
            installed_records = {}
            ctx.module_state("modrinth")["installed_projects"] = installed_records

        def worker(callbacks: dict[str, Callable]) -> list[str]:
            downloaded_names: list[str] = []
            main_path = _download_jar_verified(
                primary_file,
                mods_dir,
                callbacks,
                tr("modrinth_downloading", name=title),
            )
            installed_records[project_id] = main_path.name
            downloaded_names.append(main_path.name)

            raw_deps = version_obj.get("dependencies", [])
            req_pids: list[str] = []
            if isinstance(raw_deps, list):
                for dep in raw_deps:
                    if isinstance(dep, dict) and dep.get("dependency_type") == "required":
                        dep_pid = str(dep.get("project_id") or "").strip()
                        if dep_pid and dep_pid not in req_pids and dep_pid != project_id:
                            req_pids.append(dep_pid)

            for idx, dep_pid in enumerate(req_pids, 1):
                if dep_pid in installed_records and (mods_dir / installed_records[dep_pid]).exists():
                    continue
                try:
                    dep_versions = fetch_modrinth_versions(dep_pid, loader=loader, mc_version=mc_version)
                    if not dep_versions:
                        continue
                    dep_file = pick_primary_jar_file(dep_versions[0])
                    if dep_file is None:
                        continue
                    dep_path = _download_jar_verified(
                        dep_file,
                        mods_dir,
                        callbacks,
                        tr("modrinth_downloading_dep", index=idx, total=len(req_pids)),
                    )
                    installed_records[dep_pid] = dep_path.name
                    downloaded_names.append(dep_path.name)
                except Exception as dep_exc:
                    ctx.log.warning("Could not install required dependency %s: %s", dep_pid, dep_exc)
            return downloaded_names

        try:
            installed_files = ctx.progress_task(tr("modrinth_downloading", name=title), worker)
            ctx.save_state()
            files_bullet = "\n".join(f"• {name}" for name in installed_files)
            ctx.message(
                tr("modrinth_install_done_title"),
                tr("modrinth_install_done_body", folder=str(mods_dir), files=files_bullet),
            )
        except Exception as exc:
            ctx.log.exception("Modrinth download failed for %s", project_id)
            ctx.message(tr("install_error_title"), f"{type(exc).__name__}: {exc}", error=True)

    def installed_mods_screen(self, ctx: ModuleContext, mods_dir: Path) -> None:
        if ctx.menu is None:
            return
        while True:
            files = self._list_local_mod_files(mods_dir)
            items: list[MenuItem] = [
                MenuItem(tr("modrinth_open_mods_folder"), ("open_folder", None)),
                MenuItem(section_label(tr("modrinth_section_installed")), selectable=False),
            ]
            if not files:
                items.append(MenuItem(tr("modrinth_empty_folder"), selectable=False))
            else:
                for idx, fpath in enumerate(files):
                    disabled = fpath.name.lower().endswith(".disabled")
                    mark = "✕ " if disabled else "✓ "
                    state_str = tr("modrinth_state_disabled") if disabled else tr("modrinth_state_enabled")
                    size_str = _format_size(fpath.stat().st_size if fpath.exists() else 0)
                    items.append(MenuItem(f"{mark}{fpath.name}", ("file", idx), hint=f"{state_str} · {size_str}"))
            items.append(MenuItem(tr("back"), ("back", None)))

            action = ctx.menu.choose(
                tr("modrinth_installed_btn", count=len(files)),
                items,
                subtitle=str(mods_dir),
                spacer_after_title=True,
                view_key="modrinth-installed",
            )
            if not action or action == "back":
                return
            if action[0] == "open_folder":
                self._open_folder(ctx, mods_dir)
            elif action[0] == "file" and 0 <= action[1] < len(files):
                self._local_mod_file_detail(ctx, files[action[1]])

    def _local_mod_file_detail(self, ctx: ModuleContext, fpath: Path) -> None:
        if ctx.menu is None or not fpath.exists():
            return
        disabled = fpath.name.lower().endswith(".disabled")
        toggle_label = tr("modrinth_enable_mod") if disabled else tr("modrinth_disable_mod")
        items = [
            MenuItem(toggle_label, "toggle"),
            MenuItem(tr("delete"), "delete"),
            MenuItem(tr("back"), "back"),
        ]
        action = ctx.menu.choose(
            fpath.name,
            items,
            subtitle=f"{_format_size(fpath.stat().st_size)} · {fpath.parent}",
            spacer_after_title=True,
        )
        if not action or action == "back":
            return
        if action == "toggle":
            if disabled:
                target = fpath.with_name(fpath.name[: -len(".disabled")])
            else:
                target = fpath.with_name(fpath.name + ".disabled")
            try:
                os.replace(fpath, target)
            except OSError as exc:
                ctx.message(tr("delete_failed_title"), str(exc), error=True)
        elif action == "delete":
            if ctx.confirm(fpath.name, tr("modrinth_delete_confirm", file=fpath.name)):
                try:
                    fpath.unlink(missing_ok=True)
                except OSError as exc:
                    ctx.message(tr("delete_failed_title"), str(exc), error=True)
