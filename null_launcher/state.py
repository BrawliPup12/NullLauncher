from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_THEME, LANGUAGES, MC_NAME_RE, _parse_rgb
from .utils import atomic_json_write, clean_markup, default_minecraft_dir, load_json, now_iso, offline_uuid, proxy_protocol, recommended_ram_mb, safe_int, valid_proxy_host

                                                                             

DEFAULT_STATE: dict[str, Any] = {
    "schema": 6,
    "accounts": [],
    "selected_account": None,
    "proxy_profiles": [],
    "selected_proxy": None,
    "selected_version": None,
    "favorite_versions": [],
    "last_launch": {},
    "managed_versions": {},
    "version_settings": {},
    "settings": {
        "minecraft_dir": default_minecraft_dir(),
        "show_snapshots": False,
        "show_old_versions": False,
        "news_enabled": True,
        "auto_update": True,
        "mouse_enabled": True,
        "repair_before_launch": True,
        "close_launcher_on_game_start": False,
        "first_run_complete": False,
        "default_min_ram_mb": 1024,
        "default_max_ram_mb": recommended_ram_mb(),
        "custom_java_path": "",
        "language": "en",
        "theme_primary": DEFAULT_THEME["theme_primary"],
        "theme_subtitle": DEFAULT_THEME["theme_subtitle"],
        "theme_status": DEFAULT_THEME["theme_status"],
        "news_filter_color": DEFAULT_THEME["news_filter_color"],
    },
}


class StateStore:
    def __init__(self, base: Path):
        self.base = base
        self.path = base / "state.json"
        self.cache_path = base / "cache.json"
        self.data: dict[str, Any] = {}
        self.cache: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        raw = load_json(self.path, {})
        self.data = json.loads(json.dumps(DEFAULT_STATE))
        self._deep_merge(self.data, raw if isinstance(raw, dict) else {})
        if isinstance(raw, dict) and raw:
            old_settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
            if "first_run_complete" not in old_settings:
                self.data["settings"]["first_run_complete"] = True
        self._normalize()
        cache = load_json(self.cache_path, {})
        self.cache = cache if isinstance(cache, dict) else {}

    @staticmethod
    def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                StateStore._deep_merge(dst[key], value)
            else:
                dst[key] = value

    def _normalize(self) -> None:
        d = self.data
        d["schema"] = 6
        if not isinstance(d.get("accounts"), list):
            d["accounts"] = []
        good_accounts = []
        seen = set()
        for item in d["accounts"]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not MC_NAME_RE.fullmatch(name) or name.lower() in seen:
                continue
            seen.add(name.lower())
            good_accounts.append({"name": name, "uuid": offline_uuid(name), "type": "offline"})
        d["accounts"] = good_accounts
        names = {a["name"] for a in good_accounts}
        if d.get("selected_account") not in names:
            d["selected_account"] = good_accounts[0]["name"] if good_accounts else None

        if not isinstance(d.get("proxy_profiles"), list):
            d["proxy_profiles"] = []
        good_proxies: list[dict[str, Any]] = []
        seen_proxy_ids: set[str] = set()
        seen_proxy_names: set[str] = set()
        for item in d["proxy_profiles"]:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("id", "")).strip()
            name = clean_markup(item.get("name", ""))[:32]
            host = str(item.get("host", "")).strip()
            protocol = proxy_protocol(item)
            default_port = 1080
            try:
                port = int(item.get("port", default_port))
            except (TypeError, ValueError):
                continue
            if not pid or pid in seen_proxy_ids or not name or name.lower() in seen_proxy_names:
                continue
            if protocol not in ("socks4", "socks5") or not valid_proxy_host(host) or not 1 <= port <= 65535:
                continue
            seen_proxy_ids.add(pid)
            seen_proxy_names.add(name.lower())
            normalized = {"id": pid, "name": name, "host": host, "port": port, "protocol": protocol}
            if protocol.startswith("socks"):
                normalized["version"] = 4 if protocol == "socks4" else 5
            good_proxies.append(normalized)
        d["proxy_profiles"] = good_proxies
        proxy_ids = {p["id"] for p in good_proxies}
        if d.get("selected_proxy") not in proxy_ids:
            d["selected_proxy"] = None

        favorites = d.get("favorite_versions")
        if not isinstance(favorites, list):
            favorites = []
        clean_favorites: list[str] = []
        for value in favorites:
            version = str(value or "").strip()[:200]
            if version and version not in clean_favorites:
                clean_favorites.append(version)
            if len(clean_favorites) >= 3:
                break
        d["favorite_versions"] = clean_favorites

        last_launch = d.get("last_launch")
        if not isinstance(last_launch, dict):
            last_launch = {}
        version = str(last_launch.get("version") or "").strip()[:200]
        account = str(last_launch.get("account") or "").strip()[:32]
        launched_at = str(last_launch.get("launched_at") or "").strip()[:64]
        d["last_launch"] = ({"version": version, "account": account, "launched_at": launched_at} if version else {})

        if not isinstance(d.get("managed_versions"), dict):
            d["managed_versions"] = {}
        if not isinstance(d.get("version_settings"), dict):
            d["version_settings"] = {}
        if not isinstance(d.get("settings"), dict):
            d["settings"] = json.loads(json.dumps(DEFAULT_STATE["settings"]))
        s = d["settings"]
        s["minecraft_dir"] = str(Path(str(s.get("minecraft_dir") or default_minecraft_dir())).expanduser())
        for key in ("show_snapshots", "show_old_versions", "news_enabled", "auto_update", "mouse_enabled", "repair_before_launch", "close_launcher_on_game_start", "first_run_complete"):
            s[key] = bool(s.get(key, DEFAULT_STATE["settings"][key]))
        s["default_min_ram_mb"] = safe_int(s.get("default_min_ram_mb"), 1024, 256, 65536)
        s["default_max_ram_mb"] = safe_int(s.get("default_max_ram_mb"), recommended_ram_mb(), 512, 131072)
        if s["default_max_ram_mb"] < s["default_min_ram_mb"]:
            s["default_max_ram_mb"] = s["default_min_ram_mb"]
        s["custom_java_path"] = str(s.get("custom_java_path") or "")
        language = str(s.get("language") or "en")
        s["language"] = language if language in LANGUAGES else "en"
        for key in ("theme_primary", "theme_subtitle", "theme_status", "news_filter_color"):
            normalized, _ = _parse_rgb(s.get(key), DEFAULT_THEME[key])
            s[key] = normalized

    def save(self) -> None:
        self._normalize()
        atomic_json_write(self.path, self.data)

    def save_cache(self) -> None:
        atomic_json_write(self.cache_path, self.cache)

    def reset_launcher_settings(self) -> None:
        """Restore launcher preferences only; accounts and versions are untouched."""
        self.data["settings"] = json.loads(json.dumps(DEFAULT_STATE["settings"]))
        self.data["settings"]["first_run_complete"] = True
        self.save()

    def reset_version_settings(self, installed_id: str) -> None:
        self.data["version_settings"].pop(installed_id, None)
        self.save()

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    @property
    def minecraft_dir(self) -> Path:
        return Path(self.settings["minecraft_dir"]).expanduser()

    def account(self) -> Optional[dict[str, Any]]:
        name = self.data.get("selected_account")
        for item in self.data["accounts"]:
            if item["name"] == name:
                return item
        return None

    def proxy_profile(self) -> Optional[dict[str, Any]]:
        selected = self.data.get("selected_proxy")
        for item in self.data["proxy_profiles"]:
            if item.get("id") == selected:
                return item
        return None

    def version_config(self, installed_id: str) -> dict[str, Any]:
        defaults = {
            "min_ram_mb": self.settings["default_min_ram_mb"],
            "max_ram_mb": self.settings["default_max_ram_mb"],
            "custom_resolution": False,
            "resolution_width": 1280,
            "resolution_height": 720,
            "separate_game_dir": False,
        }
        current = self.data["version_settings"].get(installed_id, {})
        if isinstance(current, dict):
            defaults.update(current)
        defaults["min_ram_mb"] = safe_int(defaults["min_ram_mb"], 1024, 256, 65536)
        defaults["max_ram_mb"] = safe_int(defaults["max_ram_mb"], 4096, 512, 131072)
        if defaults["max_ram_mb"] < defaults["min_ram_mb"]:
            defaults["max_ram_mb"] = defaults["min_ram_mb"]
        defaults["resolution_width"] = safe_int(defaults["resolution_width"], 1280, 320, 16384)
        defaults["resolution_height"] = safe_int(defaults["resolution_height"], 720, 240, 16384)
        defaults["custom_resolution"] = bool(defaults["custom_resolution"])
        defaults["separate_game_dir"] = bool(defaults["separate_game_dir"])
        return defaults
    def favorite_versions(self) -> list[str]:
        return list(self.data.get("favorite_versions") or [])[:3]

    def is_favorite_version(self, installed_id: str) -> bool:
        return str(installed_id) in self.favorite_versions()

    def toggle_favorite_version(self, installed_id: str) -> bool:
        version = str(installed_id or "").strip()
        if not version:
            return False
        favorites = self.favorite_versions()
        if version in favorites:
            favorites.remove(version)
            enabled = False
        else:
            favorites = [version] + [item for item in favorites if item != version]
            favorites = favorites[:3]
            enabled = True
        self.data["favorite_versions"] = favorites
        self.save()
        return enabled

    def record_launch(self, installed_id: str, account_name: str = "") -> None:
        self.data["last_launch"] = {
            "version": str(installed_id or "").strip(),
            "account": str(account_name or "").strip(),
            "launched_at": now_iso(),
        }
        self.save()

    def last_launch(self) -> dict[str, str]:
        value = self.data.get("last_launch")
        return dict(value) if isinstance(value, dict) else {}

