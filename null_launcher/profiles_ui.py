from __future__ import annotations


class ProfileScreenMixin:
    def accounts_screen(self) -> None:
        modules = getattr(self, "modules", None)
        if modules is not None and hasattr(self, "module_context"):
            mod = modules.get_active_module("accounts")
            if mod is not None:
                mod.open_screen(self.module_context())
                return
        from .modules.accounts_module import AccountsModule
        from .modules.base import ModuleContext

        AccountsModule().accounts_screen(ModuleContext(self))

    def create_account(self) -> None:
        from .modules.accounts_module import AccountsModule
        from .modules.base import ModuleContext

        AccountsModule().create_account(ModuleContext(self))

    def account_detail(self, name: str) -> None:
        from .modules.accounts_module import AccountsModule
        from .modules.base import ModuleContext

        AccountsModule().account_detail(ModuleContext(self), name)

    def proxies_screen(self) -> None:
        modules = getattr(self, "modules", None)
        if modules is not None and hasattr(self, "module_context"):
            mod = modules.get_active_module("proxies")
            if mod is not None:
                mod.open_screen(self.module_context())

    def create_proxy_profile(self) -> None:
        modules = getattr(self, "modules", None)
        if modules is not None and hasattr(self, "module_context"):
            mod = modules.get_active_module("proxies")
            if mod is not None and hasattr(mod, "create_proxy_profile"):
                mod.create_proxy_profile(self.module_context())

    def proxy_detail(self, profile_id: str) -> None:
        modules = getattr(self, "modules", None)
        if modules is not None and hasattr(self, "module_context"):
            mod = modules.get_active_module("proxies")
            if mod is not None and hasattr(mod, "proxy_detail"):
                mod.proxy_detail(self.module_context(), profile_id)
