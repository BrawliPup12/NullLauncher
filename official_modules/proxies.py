from __future__ import annotations

import uuid

from null_launcher import config as cfg
from null_launcher.config import APP_VERSION, DIM, RESET, section_label, tr
from null_launcher.modules import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata
from null_launcher.terminal import MenuItem
from null_launcher.utils import clean_markup, proxy_label, valid_proxy_host


class ProxiesOfficialModule(LauncherModule):
    meta = ModuleMetadata(
        id="proxies",
        name_key="proxies",
        description_key="module_proxies_desc",
        version=APP_VERSION,
        author="NullLauncher",
        order=40,
        can_disable=True,
    )

    def get_header_lines(self, ctx: ModuleContext) -> list[str]:
        if ctx.store is None:
            return []
        proxy = ctx.store.proxy_profile()
        proxy_name = proxy["name"] if proxy else tr("none")
        return [f"{cfg.SUBTITLE_COLOR}{DIM}{tr('proxy')}:{RESET} {proxy_name}"]

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [
            MenuContribution(
                id="proxies",
                label=tr("proxies"),
                order=self.meta.order,
                module_id=self.meta.id,
            )
        ]

    def open_screen(self, ctx: ModuleContext) -> None:
        self.proxies_screen(ctx)

    def proxies_screen(self, ctx: ModuleContext) -> None:
        if ctx.store is None or ctx.menu is None:
            return
        while True:
            current = ctx.store.data.get("selected_proxy")
            direct_mark = "✓ " if current is None else "  "
            items: list[MenuItem] = [
                MenuItem(tr("create_proxy"), ("create", None)),
                MenuItem(direct_mark + tr("direct"), ("direct", None)),
            ]
            if ctx.store.data["proxy_profiles"]:
                items.append(MenuItem(section_label(tr("proxy_section")), selectable=False))
            for profile in ctx.store.data["proxy_profiles"]:
                mark = "✓ " if profile["id"] == current else "  "
                hint = f"{proxy_label(profile)} · {profile['host']}:{profile['port']}"
                items.append(MenuItem(mark + profile["name"], ("proxy", profile["id"]), hint=hint))
            items.append(MenuItem(tr("back"), ("back", None)))
            action = ctx.menu.choose(tr("proxies"), items, spacer_after_title=True, view_key="proxies")
            if not action or action[0] == "back":
                return
            if action[0] == "create":
                self.create_proxy_profile(ctx)
            elif action[0] == "direct":
                ctx.store.data["selected_proxy"] = None
                ctx.store.save()
            elif action[0] == "proxy":
                self.proxy_detail(ctx, action[1])

    def create_proxy_profile(self, ctx: ModuleContext) -> None:
        if ctx.store is None or ctx.menu is None:
            return
        raw_name = ctx.prompt(tr("proxy_name_prompt"))
        if not raw_name:
            return
        name = clean_markup(raw_name)[:32].strip()
        if not name:
            ctx.message(tr("invalid_name_title"), tr("invalid_proxy_name_body"), error=True)
            return
        if any(p["name"].lower() == name.lower() for p in ctx.store.data["proxy_profiles"]):
            ctx.message(tr("proxy_exists_title"), tr("proxy_exists_body", name=name), error=True)
            return

        protocol = ctx.menu.choose(
            tr("type_proxy"),
            [
                MenuItem("SOCKS5", "socks5"),
                MenuItem("SOCKS4", "socks4"),
                MenuItem(tr("back"), None),
            ],
        )
        if protocol not in ("socks4", "socks5"):
            return

        raw_host = ctx.prompt(tr("proxy_host_prompt"))
        if not raw_host:
            return
        host = raw_host.strip()
        if not valid_proxy_host(host):
            ctx.message(tr("invalid_proxy_host_title"), tr("invalid_proxy_host_body"), error=True)
            return
        raw_port = ctx.prompt(tr("proxy_port_prompt"), "1080")
        if not raw_port:
            return
        try:
            port = int(raw_port)
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            ctx.message(tr("invalid_proxy_port_title"), tr("invalid_proxy_port_body"), error=True)
            return

        profile = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "host": host,
            "port": port,
            "protocol": protocol,
            "version": 4 if protocol == "socks4" else 5,
        }
        ctx.store.data["proxy_profiles"].append(profile)
        ctx.store.data["selected_proxy"] = profile["id"]
        ctx.store.save()

    def proxy_detail(self, ctx: ModuleContext, profile_id: str) -> None:
        if ctx.store is None or ctx.menu is None:
            return
        while True:
            profile = next((p for p in ctx.store.data["proxy_profiles"] if p.get("id") == profile_id), None)
            if not profile:
                return
            current = ctx.store.data.get("selected_proxy") == profile_id
            items: list[MenuItem] = []
            if not current:
                items.append(MenuItem(tr("select_launch"), "select"))
            else:
                items.append(MenuItem(tr("disable_proxy"), "disable"))
            items.extend([MenuItem(tr("delete"), "delete"), MenuItem(tr("back"), "back")])
            subtitle = f"{proxy_label(profile)} · {profile['host']}:{profile['port']}"
            action = ctx.menu.choose(profile["name"], items, subtitle=subtitle)
            if not action or action == "back":
                return
            if action == "select":
                ctx.store.data["selected_proxy"] = profile_id
                ctx.store.save()
                return
            if action == "disable":
                ctx.store.data["selected_proxy"] = None
                ctx.store.save()
                return
            if action == "delete":
                if ctx.confirm(tr("delete_proxy_title"), tr("delete_proxy_body", name=profile["name"])):
                    ctx.store.data["proxy_profiles"] = [
                        p for p in ctx.store.data["proxy_profiles"] if p.get("id") != profile_id
                    ]
                    if ctx.store.data.get("selected_proxy") == profile_id:
                        ctx.store.data["selected_proxy"] = None
                    ctx.store.save()
                    return
