from __future__ import annotations

import uuid

from .config import MC_NAME_RE, section_label, tr
from .terminal import MenuItem
from .utils import clean_markup, offline_uuid, proxy_label, valid_proxy_host


class ProfileScreenMixin:
    def accounts_screen(self) -> None:
        while True:
            current = self.store.data.get("selected_account")
            items = [MenuItem(tr("create_account"), ("create", None))]
            if self.store.data["accounts"]:
                items.append(MenuItem(section_label(tr("account_section")), selectable=False))
            for account in self.store.data["accounts"]:
                mark = "✓ " if account["name"] == current else "  "
                items.append(MenuItem(mark + account["name"], ("account", account["name"])))
            items.append(MenuItem(tr("back"), ("back", None)))
            action = self.menu.choose(tr("accounts"), items, spacer_after_title=True, view_key="accounts")
            if not action or action[0] == "back":
                return
            if action[0] == "create":
                self.create_account()
            else:
                self.account_detail(action[1])

    def create_account(self) -> None:
        name = self.term.prompt(tr("account_name_prompt"))
        if not MC_NAME_RE.fullmatch(name):
            self.message(tr("invalid_name_title"), tr("invalid_account_name_body"), error=True)
            return
        if any(a["name"].lower() == name.lower() for a in self.store.data["accounts"]):
            self.message(tr("account_exists_title"), tr("account_exists_body", name=name), error=True)
            return
        account = {"name": name, "uuid": offline_uuid(name), "type": "offline"}
        self.store.data["accounts"].append(account)
        self.store.data["selected_account"] = name
        self.store.save()

    def account_detail(self, name: str) -> None:
        while True:
            current = self.store.data.get("selected_account") == name
            items = []
            if not current:
                items.append(MenuItem(tr("select_launch"), "select"))
            items.extend([MenuItem(tr("delete"), "delete"), MenuItem(tr("back"), "back")])
            action = self.menu.choose(name, items, subtitle=f"{tr('offline_uuid_label')}: {offline_uuid(name)}")
            if not action or action == "back":
                return
            if action == "select":
                self.store.data["selected_account"] = name
                self.store.save()
                return
            if action == "delete":
                if self.confirm(tr("delete_account_title"), tr("delete_account_body", name=name)):
                    self.store.data["accounts"] = [a for a in self.store.data["accounts"] if a["name"] != name]
                    if self.store.data.get("selected_account") == name:
                        self.store.data["selected_account"] = self.store.data["accounts"][0]["name"] if self.store.data["accounts"] else None
                    self.store.save()
                    return

    def proxies_screen(self) -> None:
        while True:
            current = self.store.data.get("selected_proxy")
            direct_mark = "✓ " if current is None else "  "
            items: list[MenuItem] = [
                MenuItem(tr("create_proxy"), ("create", None)),
                MenuItem(direct_mark + tr("direct"), ("direct", None)),
            ]
            if self.store.data["proxy_profiles"]:
                items.append(MenuItem(section_label(tr("proxy_section")), selectable=False))
            for profile in self.store.data["proxy_profiles"]:
                mark = "✓ " if profile["id"] == current else "  "
                hint = f"{proxy_label(profile)} · {profile['host']}:{profile['port']}"
                items.append(MenuItem(mark + profile["name"], ("proxy", profile["id"]), hint=hint))
            items.append(MenuItem(tr("back"), ("back", None)))
            action = self.menu.choose(tr("proxies"), items, spacer_after_title=True, view_key="proxies")
            if not action or action[0] == "back":
                return
            if action[0] == "create":
                self.create_proxy_profile()
            elif action[0] == "direct":
                self.store.data["selected_proxy"] = None
                self.store.save()
            elif action[0] == "proxy":
                self.proxy_detail(action[1])

    def create_proxy_profile(self) -> None:
        name = clean_markup(self.term.prompt(tr("proxy_name_prompt")))[:32]
        if not name:
            self.message(tr("invalid_name_title"), tr("invalid_proxy_name_body"), error=True)
            return
        if any(p["name"].lower() == name.lower() for p in self.store.data["proxy_profiles"]):
            self.message(tr("proxy_exists_title"), tr("proxy_exists_body", name=name), error=True)
            return

        protocol = self.menu.choose(
            tr("type_proxy"),
            [
                MenuItem("SOCKS5", "socks5"),
                MenuItem("SOCKS4", "socks4"),
                MenuItem(tr("back"), None),
            ],
        )
        if protocol not in ("socks4", "socks5"):
            return

        host = self.term.prompt(tr("proxy_host_prompt")).strip()
        if not valid_proxy_host(host):
            self.message(tr("invalid_proxy_host_title"), tr("invalid_proxy_host_body"), error=True)
            return
        raw_port = self.term.prompt(tr("proxy_port_prompt"), "1080")
        try:
            port = int(raw_port)
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            self.message(tr("invalid_proxy_port_title"), tr("invalid_proxy_port_body"), error=True)
            return

        profile = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "host": host,
            "port": port,
            "protocol": protocol,
        }
        profile["version"] = 4 if protocol == "socks4" else 5
        self.store.data["proxy_profiles"].append(profile)
        self.store.data["selected_proxy"] = profile["id"]
        self.store.save()

    def proxy_detail(self, profile_id: str) -> None:
        while True:
            profile = next((p for p in self.store.data["proxy_profiles"] if p.get("id") == profile_id), None)
            if not profile:
                return
            current = self.store.data.get("selected_proxy") == profile_id
            items: list[MenuItem] = []
            if not current:
                items.append(MenuItem(tr("select_launch"), "select"))
            else:
                items.append(MenuItem(tr("disable_proxy"), "disable"))
            items.extend([MenuItem(tr("delete"), "delete"), MenuItem(tr("back"), "back")])
            subtitle = f"{proxy_label(profile)} · {profile['host']}:{profile['port']}"
            action = self.menu.choose(profile["name"], items, subtitle=subtitle)
            if not action or action == "back":
                return
            if action == "select":
                self.store.data["selected_proxy"] = profile_id
                self.store.save()
                return
            if action == "disable":
                self.store.data["selected_proxy"] = None
                self.store.save()
                return
            if action == "delete":
                if self.confirm(tr("delete_proxy_title"), tr("delete_proxy_body", name=profile["name"])):
                    self.store.data["proxy_profiles"] = [p for p in self.store.data["proxy_profiles"] if p.get("id") != profile_id]
                    if self.store.data.get("selected_proxy") == profile_id:
                        self.store.data["selected_proxy"] = None
                    self.store.save()
                    return
