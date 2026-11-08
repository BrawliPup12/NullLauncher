from __future__ import annotations

from ..config import MC_NAME_RE, section_label, tr
from ..terminal import MenuItem
from ..utils import offline_uuid
from .base import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata


class AccountsModule(LauncherModule):
    meta = ModuleMetadata(
        id="accounts",
        name_key="accounts",
        description_key="module_accounts_desc",
        order=30,
        can_disable=True,
        builtin=True,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [
            MenuContribution(
                id="accounts",
                label=tr("accounts"),
                order=self.meta.order,
                module_id=self.meta.id,
            )
        ]

    def open_screen(self, ctx: ModuleContext) -> None:
        self.accounts_screen(ctx)

    def accounts_screen(self, ctx: ModuleContext) -> None:
        if ctx.store is None or ctx.menu is None:
            return
        while True:
            current = ctx.store.data.get("selected_account")
            items = [MenuItem(tr("create_account"), ("create", None))]
            if ctx.store.data["accounts"]:
                items.append(MenuItem(section_label(tr("account_section")), selectable=False))
            for account in ctx.store.data["accounts"]:
                mark = "✓ " if account["name"] == current else "  "
                items.append(MenuItem(mark + account["name"], ("account", account["name"])))
            items.append(MenuItem(tr("back"), ("back", None)))
            action = ctx.menu.choose(tr("accounts"), items, spacer_after_title=True, view_key="accounts")
            if not action or action[0] == "back":
                return
            if action[0] == "create":
                self.create_account(ctx)
            else:
                self.account_detail(ctx, action[1])

    def create_account(self, ctx: ModuleContext) -> None:
        if ctx.store is None:
            return
        name = ctx.prompt(tr("account_name_prompt"))
        if not name:
            return
        if not MC_NAME_RE.fullmatch(name):
            ctx.message(tr("invalid_name_title"), tr("invalid_account_name_body"), error=True)
            return
        if any(a["name"].lower() == name.lower() for a in ctx.store.data["accounts"]):
            ctx.message(tr("account_exists_title"), tr("account_exists_body", name=name), error=True)
            return
        account = {"name": name, "uuid": offline_uuid(name), "type": "offline"}
        ctx.store.data["accounts"].append(account)
        ctx.store.data["selected_account"] = name
        ctx.store.save()

    def account_detail(self, ctx: ModuleContext, name: str) -> None:
        if ctx.store is None or ctx.menu is None:
            return
        while True:
            current = ctx.store.data.get("selected_account") == name
            items = []
            if not current:
                items.append(MenuItem(tr("select_launch"), "select"))
            items.extend([MenuItem(tr("delete"), "delete"), MenuItem(tr("back"), "back")])
            action = ctx.menu.choose(name, items, subtitle=f"{tr('offline_uuid_label')}: {offline_uuid(name)}")
            if not action or action == "back":
                return
            if action == "select":
                ctx.store.data["selected_account"] = name
                ctx.store.save()
                return
            if action == "delete":
                if ctx.confirm(tr("delete_account_title"), tr("delete_account_body", name=name)):
                    ctx.store.data["accounts"] = [a for a in ctx.store.data["accounts"] if a["name"] != name]
                    if ctx.store.data.get("selected_account") == name:
                        ctx.store.data["selected_account"] = (
                            ctx.store.data["accounts"][0]["name"] if ctx.store.data["accounts"] else None
                        )
                    ctx.store.save()
                    return
