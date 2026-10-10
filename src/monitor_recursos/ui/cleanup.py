"""Aba de limpeza: libera espaço sem apagar nada do usuário."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core import cleanup as core
from ..util import fmt_bytes
from . import widgets as w


class CleanupPage(Gtk.Box):
    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.items: list[core.CleanupItem] = []
        self._checks: dict[str, Gtk.CheckButton] = {}
        self._loading = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.stack.add_named(self._build_loading(), "carregando")
        self.stack.add_named(self._build_list(), "lista")
        self.append(self.stack)
        self.stack.set_visible_child_name("carregando")

    def _build_loading(self) -> Gtk.Widget:
        page = Adw.StatusPage(title="Procurando espaço para liberar…")
        page.set_description("Medindo caches, lixeira e pacotes antigos")
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(42, 42)
        page.set_child(spinner)
        return page

    def _build_list(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=860)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.box.set_margin_top(16)
        self.box.set_margin_bottom(28)
        self.box.set_margin_start(14)
        self.box.set_margin_end(14)
        clamp.set_child(self.box)
        scroller.set_child(clamp)
        return scroller

    # -------------------------------------------------------- carregamento ---

    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.items = []
        self.stack.set_visible_child_name("carregando")
        core.discover_async(
            lambda item: GLib.idle_add(self._add_item, item),
            lambda: GLib.idle_add(self._discovery_done),
        )

    def _add_item(self, item: core.CleanupItem) -> bool:
        """Cada medição chega na hora em que termina."""
        self.items = [i for i in self.items if i.key != item.key] + [item]
        self.items.sort(key=lambda i: i.size, reverse=True)
        self._render()
        self.stack.set_visible_child_name("lista")
        return False

    def _discovery_done(self) -> bool:
        self._loading = False
        self._render()
        self.stack.set_visible_child_name("lista")
        return False

    # ------------------------------------------------------------- desenho ---

    def _render(self) -> None:
        child = self.box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt
        self._checks = {}

        total = sum(i.size for i in self.items)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        value = Gtk.Label(label=fmt_bytes(total))
        value.add_css_class("title-1")
        value.add_css_class("numeric")
        head.append(value)
        label = Gtk.Label(label="podem ser liberados agora")
        label.add_css_class("dim-label")
        head.append(label)
        self.lbl_selected = Gtk.Label(label="")
        self.lbl_selected.add_css_class("caption")
        self.lbl_selected.add_css_class("dim-label")
        head.append(self.lbl_selected)

        self.run_button = Gtk.Button(label="Liberar espaço")
        self.run_button.add_css_class("pill")
        self.run_button.add_css_class("suggested-action")
        self.run_button.set_halign(Gtk.Align.CENTER)
        self.run_button.set_margin_top(6)
        self.run_button.connect("clicked", lambda _b: self._confirm())
        head.append(self.run_button)
        self.box.append(w.card(head, padding=18))

        if not self.items:
            group = Adw.PreferencesGroup()
            group.add(Adw.ActionRow(
                title="Nada para limpar",
                subtitle="Seu sistema já está sem sobras conhecidas"))
            self.box.append(group)
        else:
            group = Adw.PreferencesGroup(
                title="O que pode sair",
                description="Nenhum documento, foto ou projeto seu entra nesta "
                            "lista")
            for item in self.items:
                row = Adw.ActionRow(title=item.title)
                detail = item.subtitle
                if item.note:
                    detail += f" · {item.note}"
                row.set_subtitle(detail)
                row.set_subtitle_lines(2)
                check = Gtk.CheckButton(active=item.recommended)
                check.set_valign(Gtk.Align.CENTER)
                check.connect("toggled", lambda _c: self._update_selected())
                row.add_prefix(check)
                row.add_prefix(w.icon_image(item.icon, 24))
                self._checks[item.key] = check
                size = Gtk.Label(label=fmt_bytes(item.size) if item.size else "—",
                                 xalign=1)
                size.add_css_class("numeric")
                size.set_valign(Gtk.Align.CENTER)
                row.add_suffix(size)
                if item.needs_root:
                    row.add_suffix(w.tag("senha"))
                row.set_activatable_widget(check)
                group.add(row)
            self.box.append(group)

        if self._loading:
            waiting = Adw.PreferencesGroup()
            row = Adw.ActionRow(
                title="Ainda medindo…",
                subtitle="Pastas grandes como o cache do navegador levam alguns "
                         "segundos na primeira vez")
            row.set_subtitle_lines(2)
            spinner = Gtk.Spinner(spinning=True)
            spinner.set_size_request(20, 20)
            spinner.set_valign(Gtk.Align.CENTER)
            row.add_prefix(spinner)
            waiting.add(row)
            self.box.append(waiting)

        extra = Adw.PreferencesGroup(title="Manutenção do SSD")
        row = Adw.ActionRow(
            title="Otimizar o SSD agora",
            subtitle="Avisa ao disco quais blocos estão livres (TRIM). "
                     "Mantém a velocidade de escrita ao longo do tempo.")
        row.set_subtitle_lines(2)
        row.add_prefix(w.icon_image("drive-harddisk-solidstate-symbolic", 24))
        button = Gtk.Button(label="Otimizar")
        button.add_css_class("pill")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self.window.run_privileged(
            ["otimizar-ssd"], title="Otimizando o SSD",
            success="SSD otimizado."))
        row.add_suffix(button)
        extra.add(row)
        self.box.append(extra)
        self._update_selected()

    def _selected(self) -> list[core.CleanupItem]:
        return [i for i in self.items
                if self._checks.get(i.key) and self._checks[i.key].get_active()]

    def _update_selected(self) -> None:
        chosen = self._selected()
        total = sum(i.size for i in chosen)
        self.lbl_selected.set_label(
            f"{len(chosen)} itens marcados · cerca de {fmt_bytes(total)}"
            if chosen else "nada marcado")
        self.run_button.set_sensitive(bool(chosen))

    # ------------------------------------------------------------ execução ---

    def _confirm(self) -> None:
        chosen = self._selected()
        if not chosen:
            return
        total = sum(i.size for i in chosen)
        needs_root = any(i.needs_root for i in chosen)
        body = (f"Serão liberados cerca de {fmt_bytes(total)}.\n\n"
                + "\n".join(f"• {i.title}" for i in chosen))
        if needs_root:
            body += "\n\nSeu computador vai pedir a senha de administrador."
        dialog = Adw.AlertDialog(heading="Liberar espaço?", body=body)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("go", "Liberar agora")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")

        def done(_d, result):
            if dialog.choose_finish(result) == "go":
                self._run(chosen)

        dialog.choose(self.window, None, done)

    def _run(self, chosen: list[core.CleanupItem]) -> None:
        user_items = [i for i in chosen if not i.needs_root]
        root_items = [i for i in chosen if i.needs_root]
        self.run_button.set_sensitive(False)

        def run_root_chain(index: int, freed_user: int) -> None:
            if index >= len(root_items):
                if freed_user:
                    self.window.notify(f"{fmt_bytes(freed_user)} liberados.")
                self.reload()
                return
            item = root_items[index]
            self.window.run_privileged(
                item.helper_args,
                title=item.title,
                success="",
                silent=False,
                on_finish=lambda ok, i=index: run_root_chain(i + 1, freed_user),
            )

        if not user_items:
            run_root_chain(0, 0)
            return

        paths = [p for i in user_items for p in i.paths]

        def work():
            freed, errors = core.clear_paths(paths)
            GLib.idle_add(after, freed, errors)

        def after(freed: int, errors: list[str]) -> bool:
            if errors:
                self.window.notify(errors[0])
            run_root_chain(0, freed)
            return False

        threading.Thread(target=work, daemon=True).start()
