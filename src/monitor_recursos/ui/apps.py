"""Aba de aplicativos: lista o que está instalado e desinstala com um clique.

Nenhum comando aparece na tela. O que o usuário vê é o nome do programa, o
espaço que ele ocupa e o que mais sai junto com ele.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from ..core import packages as pk
from ..util import fmt_bytes, fmt_int
from . import widgets as w

MODES = [
    ("apps", "Aplicativos", "Programas com atalho no menu"),
    ("todos", "Todos os pacotes", "Tudo que o sistema tem instalado"),
    ("snap", "Snap", "Aplicativos instalados pela loja Snap"),
    ("flatpak", "Flatpak", "Aplicativos instalados pelo Flatpak"),
    ("sistema", "Componentes do sistema", "Base do Linux e plataformas"),
]
SORTS = [("tamanho", "Maior espaço"), ("nome", "Nome")]


class AppItem(GObject.Object):
    __gtype_name__ = "MrAppItem"

    def __init__(self, app: pk.App) -> None:
        super().__init__()
        self.app = app


class AppsPage(Gtk.Box):
    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.inventory: pk.Inventory | None = None
        self._loading = False

        self.store = Gio_list_store()
        self.filter = Gtk.CustomFilter.new(self._match)
        filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sorter = Gtk.CustomSorter.new(self._compare)
        sorted_model = Gtk.SortListModel(model=filtered, sorter=self.sorter)
        self.selection = Gtk.NoSelection(model=sorted_model)
        sorted_model.connect("items-changed", lambda *_: self._update_summary())

        self.append(self._build_toolbar())

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.stack.add_named(self._build_loading(), "carregando")
        self.stack.add_named(self._build_list(), "lista")
        self.stack.add_named(self._build_empty(), "vazio")
        self.append(self.stack)
        self.stack.set_visible_child_name("carregando")

    # ------------------------------------------------------------- barras ----

    def _build_toolbar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(10)
        box.set_margin_bottom(6)
        box.set_margin_start(14)
        box.set_margin_end(14)

        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Procurar aplicativo…")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search)
        line.append(self.search)

        self.mode_drop = Gtk.DropDown.new_from_strings([m[1] for m in MODES])
        self.mode_drop.set_tooltip_text("O que mostrar na lista")
        self.mode_drop.connect("notify::selected", self._on_mode)
        line.append(self.mode_drop)

        self.sort_drop = Gtk.DropDown.new_from_strings([s[1] for s in SORTS])
        self.sort_drop.set_tooltip_text("Ordenação")
        self.sort_drop.connect("notify::selected",
                               lambda *_: self.sorter.changed(
                                   Gtk.FilterChange.DIFFERENT))
        line.append(self.sort_drop)

        reload_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        reload_btn.add_css_class("flat")
        reload_btn.set_tooltip_text("Atualizar a lista")
        reload_btn.connect("clicked", lambda _b: self.reload())
        line.append(reload_btn)
        box.append(line)

        self.summary = w.dim("—")
        box.append(self.summary)
        return box

    def _build_loading(self) -> Gtk.Widget:
        page = Adw.StatusPage(title="Lendo a lista de aplicativos…")
        page.set_description("Consultando os programas instalados no sistema")
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(42, 42)
        page.set_child(spinner)
        return page

    def _build_empty(self) -> Gtk.Widget:
        return Adw.StatusPage(
            title="Nada encontrado",
            description="Tente outro termo ou troque o filtro.",
            icon_name="system-search-symbolic")

    def _build_list(self) -> Gtk.Widget:
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.add_css_class("mr-applist")
        self.list_view.set_show_separators(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        clamp = Adw.Clamp(maximum_size=1000, tightening_threshold=860)
        clamp.set_child(self.list_view)
        scroller.set_child(clamp)
        return scroller

    # --------------------------------------------------------------- linhas --

    def _setup_row(self, _factory, item: Gtk.ListItem) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(9)
        row.set_margin_bottom(9)
        row.set_margin_start(10)
        row.set_margin_end(10)

        icon = Gtk.Image()
        icon.set_pixel_size(40)
        row.append(icon)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        title_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(xalign=0)
        name.add_css_class("heading")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        title_line.append(name)
        kind = w.tag("")
        title_line.append(kind)
        texts.append(title_line)
        summary = Gtk.Label(xalign=0)
        summary.add_css_class("caption")
        summary.add_css_class("dim-label")
        summary.set_ellipsize(Pango.EllipsizeMode.END)
        summary.set_max_width_chars(60)
        texts.append(summary)
        row.append(texts)

        size = Gtk.Label(xalign=1)
        size.add_css_class("numeric")
        size.set_size_request(86, -1)
        row.append(size)

        button = Gtk.Button(icon_name="user-trash-symbolic")
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)
        row.append(button)

        item.set_child(row)
        item._parts = (icon, name, kind, summary, size, button)

    def _bind_row(self, _factory, item: Gtk.ListItem) -> None:
        icon, name, kind, summary, size, button = item._parts
        app: pk.App = item.get_item().app
        icon.set_from_icon_name("application-x-executable-symbolic")
        if app.icon:
            new_icon = w.icon_image(app.icon, 40)
            if app.icon.startswith("/"):
                icon.set_from_gicon(new_icon.get_gicon())
            else:
                icon.set_from_icon_name(new_icon.get_icon_name()
                                        or "application-x-executable-symbolic")
        elif app.kind == pk.APT:
            icon.set_from_icon_name("package-x-generic-symbolic")
        name.set_label(app.name)
        label = {pk.SNAP: "Snap", pk.FLATPAK: "Flatpak",
                 pk.MANUAL: "Atalho"}.get(app.kind, "")
        if app.is_system:
            label = "Base do sistema"
        elif app.shared:
            label = "Plataforma"
        kind.set_label(label)
        kind.set_visible(bool(label))
        detail = app.summary or "Sem descrição"
        if app.extra:
            detail = f"{detail} · {app.extra}"
        summary.set_label(detail)
        size.set_label(fmt_bytes(app.size) if app.size else "—")
        for handler in getattr(button, "_handler", []):
            button.disconnect(handler)
        button._handler = [button.connect(
            "clicked", lambda _b, a=app: self.ask_remove(a))]
        if app.protected:
            button.set_icon_name("changes-prevent-symbolic")
            button.set_tooltip_text("Faz parte do sistema: não pode ser removido")
        else:
            button.set_icon_name("user-trash-symbolic")
            button.set_tooltip_text(f"Desinstalar {app.name}")

    # --------------------------------------------------------- carregamento --

    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.stack.set_visible_child_name("carregando")

        def work():
            try:
                inv = pk.build_inventory()
            except Exception as exc:  # pragma: no cover
                GLib.idle_add(self._load_failed, str(exc))
                return
            GLib.idle_add(self._load_done, inv)

        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, message: str) -> None:
        self._loading = False
        self.window.notify("Não foi possível ler a lista de aplicativos.")
        self.stack.set_visible_child_name("vazio")

    def _load_done(self, inv: pk.Inventory) -> None:
        self._loading = False
        self.inventory = inv
        self._fill()

    def _fill(self) -> None:
        if self.inventory is None:
            return
        mode = MODES[self.mode_drop.get_selected()][0]
        inv = self.inventory
        if mode == "apps":
            items = [a for a in inv.apps if not a.is_system]
        elif mode == "todos":
            items = inv.packages
        elif mode == "snap":
            items = inv.snaps
        elif mode == "flatpak":
            items = inv.flatpaks
        else:
            items = [a for a in inv.packages + inv.snaps
                     if a.protected or a.shared]
        self.store.remove_all()
        for app in items:
            self.store.append(AppItem(app))
        self.stack.set_visible_child_name("lista" if items else "vazio")
        self._update_summary()

    def _update_summary(self) -> None:
        total = self.selection.get_n_items()
        size = sum(self.selection.get_item(i).app.size for i in range(total))
        mode = MODES[self.mode_drop.get_selected()]
        self.summary.set_label(
            f"{mode[2]} · {fmt_int(total)} itens ocupando {fmt_bytes(size)}")
        if total == 0 and self.store.get_n_items():
            self.stack.set_visible_child_name("vazio")
        elif total:
            self.stack.set_visible_child_name("lista")

    # ------------------------------------------------------------- filtros ---

    def _match(self, item: AppItem, *_extra) -> bool:
        text = self.search.get_text().strip().casefold()
        if not text:
            return True
        app = item.app
        return (text in app.name.casefold() or text in app.ident.casefold()
                or text in app.summary.casefold())

    def _compare(self, a: AppItem, b: AppItem, *_extra) -> int:
        key = SORTS[self.sort_drop.get_selected()][0]
        if key == "tamanho":
            if a.app.size != b.app.size:
                return -1 if a.app.size > b.app.size else 1
        if a.app.sort_name == b.app.sort_name:
            return 0
        return -1 if a.app.sort_name < b.app.sort_name else 1

    def _on_search(self, _entry) -> None:
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_summary()

    def _on_mode(self, *_args) -> None:
        self._fill()

    # ------------------------------------------------------- desinstalação ---

    def ask_remove(self, app: pk.App) -> None:
        if app.protected:
            dialog = Adw.AlertDialog(
                heading=f"“{app.name}” faz parte do sistema",
                body="Remover este item deixaria o Linux ou a área de trabalho "
                     "sem funcionar, então ele está bloqueado.")
            dialog.add_response("ok", "Entendi")
            dialog.present(self.window)
            return
        if app.kind == pk.MANUAL:
            self._remove_shortcut(app)
            return
        if app.kind == pk.APT:
            self._ask_remove_apt(app)
        else:
            self._ask_remove_simple(app)

    def _remove_shortcut(self, app: pk.App) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Remover o atalho de “{app.name}”?",
            body="O atalho sai do menu. Os arquivos do programa continuam onde "
                 "estão.")
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("go", "Remover atalho")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, result):
            if dialog.choose_finish(result) != "go":
                return
            try:
                import os

                os.unlink(app.desktop_file)
                self.window.notify(f"Atalho de “{app.name}” removido.")
                self.reload()
            except OSError:
                self.window.notify("Não foi possível remover o atalho.")

        dialog.choose(self.window, None, done)

    def _ask_remove_apt(self, app: pk.App) -> None:
        dialog = Adw.AlertDialog(heading=f"Desinstalar “{app.name}”?")
        dialog.set_body("Conferindo o que será removido…")
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(28, 28)
        dialog.set_extra_child(spinner)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("go", "Desinstalar")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_enabled("go", False)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def done(_d, result):
            if dialog.choose_finish(result) == "go":
                self._run_removal(app, ["remover-apt", app.ident])

        dialog.choose(self.window, None, done)

        def work():
            plan = pk.simulate_apt_removal([app])
            GLib.idle_add(apply_plan, plan)

        def apply_plan(plan: pk.RemovalPlan):
            dialog.set_extra_child(None)
            if plan.blocked:
                dialog.set_body(
                    "Esta remoção levaria embora itens que o sistema precisa "
                    f"({', '.join(plan.blocked[:3])}), então ela foi impedida.")
                dialog.set_response_enabled("go", False)
                return False
            if plan.error:
                dialog.set_body(f"Não foi possível conferir a remoção: {plan.error}")
                dialog.set_response_enabled("go", False)
                return False
            extra = plan.extra_count
            parts = [f"Libera cerca de {fmt_bytes(plan.freed)}."]
            if extra:
                parts.append(
                    f"Saem também {extra} dependências que ficariam sem uso.")
            dialog.set_body(" ".join(parts))
            names = [n for n, _ in sorted(plan.removed, key=lambda t: -t[1])]
            if len(names) > 1:
                listing = Gtk.Label(
                    label="Serão removidos: " + ", ".join(names[:12])
                    + ("…" if len(names) > 12 else ""), xalign=0)
                listing.set_wrap(True)
                listing.add_css_class("caption")
                listing.add_css_class("dim-label")
                listing.set_max_width_chars(46)
                dialog.set_extra_child(listing)
            dialog.set_response_enabled("go", True)
            return False

        threading.Thread(target=work, daemon=True).start()

    def _ask_remove_simple(self, app: pk.App) -> None:
        body = f"Libera cerca de {fmt_bytes(app.size)}." if app.size else ""
        if app.shared:
            body += ("\n\nEste item é uma plataforma compartilhada: outros "
                     "aplicativos podem parar de abrir sem ele.")
        dialog = Adw.AlertDialog(heading=f"Desinstalar “{app.name}”?", body=body)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("go", "Desinstalar")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def done(_d, result):
            if dialog.choose_finish(result) != "go":
                return
            action = "remover-snap" if app.kind == pk.SNAP else "remover-flatpak"
            self._run_removal(app, [action, app.ident])

        dialog.choose(self.window, None, done)

    def _run_removal(self, app: pk.App, args: list[str]) -> None:
        self.window.run_privileged(
            args,
            title=f"Desinstalando {app.name}",
            success=f"“{app.name}” foi desinstalado.",
            on_finish=lambda ok: self.reload() if ok else None,
        )


def Gio_list_store() -> "Gio.ListStore":
    from gi.repository import Gio

    return Gio.ListStore(item_type=AppItem)
