"""Aba de componentes: as peças do computador, uma a uma."""

from __future__ import annotations

import json
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ..core import hardware as hw
from . import widgets as w


class HardwarePage(Gtk.ScrolledWindow):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sections: list[hw.Section] = []
        self.modules: list[dict] | None = None
        self._loading = False

        clamp = Adw.Clamp(maximum_size=940, tightening_threshold=820)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.box.set_margin_top(16)
        self.box.set_margin_bottom(28)
        self.box.set_margin_start(14)
        self.box.set_margin_end(14)
        clamp.set_child(self.box)
        self.set_child(clamp)

    # -------------------------------------------------------- carregamento --

    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True

        def work():
            try:
                sections = hw.collect(self.modules)
            except Exception:
                sections = []
            GLib.idle_add(self._loaded, sections)

        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, sections: list[hw.Section]) -> bool:
        self._loading = False
        self.sections = sections
        self._render()
        return False

    # -------------------------------------------------------------- desenho --

    def _clear(self) -> None:
        child = self.box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt

    def _render(self) -> None:
        self._clear()
        if not self.sections:
            self.box.append(Adw.StatusPage(
                title="Não foi possível ler as peças",
                icon_name="computer-symbolic"))
            return
        self.box.append(self._header())
        for section in self.sections:
            self.box.append(self._group(section))

    def _header(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        resumo = self._resumo_curto()
        title = w.heading(resumo[0], 2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        texts.append(title)
        texts.append(w.dim(resumo[1]))
        box.append(texts)

        copiar = Gtk.Button(icon_name="edit-copy-symbolic")
        copiar.add_css_class("flat")
        copiar.set_valign(Gtk.Align.CENTER)
        copiar.set_tooltip_text("Copiar a lista inteira de componentes")
        copiar.connect("clicked", lambda _b: self._copiar())
        box.append(copiar)
        return w.card(box)

    def _resumo_curto(self) -> tuple[str, str]:
        por_chave = {s.key: s for s in self.sections}
        maquina = por_chave.get("maquina")
        titulo = maquina.rows[0].title if maquina and maquina.rows else "Este computador"
        partes = []
        cpu = por_chave.get("cpu")
        if cpu and cpu.rows:
            partes.append(cpu.rows[0].title)
        memoria = por_chave.get("memoria")
        if memoria and memoria.rows:
            partes.append(memoria.rows[0].title)
        video = por_chave.get("video")
        if video and video.rows:
            partes.append(video.rows[-1].title)
        return titulo, " · ".join(partes)

    def _group(self, section: hw.Section) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=section.title)
        for row in section.rows:
            group.add(self._row(row))
        if section.note == "pedir-modulos":
            group.add(self._memory_button())
        elif section.note == "sem-modulos":
            group.add(Adw.ActionRow(
                title="Os slots de memória não puderam ser lidos",
                subtitle="A placa-mãe não informa esses dados ao sistema"))
        return group

    def _row(self, row: hw.Row) -> Gtk.Widget:
        if not row.details:
            item = Adw.ActionRow(title=GLib.markup_escape_text(row.title))
            if row.subtitle:
                item.set_subtitle(GLib.markup_escape_text(row.subtitle))
            if row.icon:
                item.add_prefix(w.icon_image(row.icon, 24))
            return item
        item = Adw.ExpanderRow(title=GLib.markup_escape_text(row.title))
        if row.subtitle:
            item.set_subtitle(GLib.markup_escape_text(row.subtitle))
        if row.icon:
            item.add_prefix(w.icon_image(row.icon, 24))
        for chave, valor in row.details:
            linha = Adw.ActionRow(title=GLib.markup_escape_text(chave))
            rotulo = Gtk.Label(label=valor, xalign=1)
            rotulo.add_css_class("dim-label")
            rotulo.set_selectable(True)
            rotulo.set_wrap(True)
            rotulo.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            rotulo.set_max_width_chars(42)
            rotulo.set_valign(Gtk.Align.CENTER)
            linha.add_suffix(rotulo)
            item.add_row(linha)
        return item

    def _memory_button(self) -> Gtk.Widget:
        row = Adw.ActionRow(
            title="Ver os pentes de memória",
            subtitle="Quantidade, slots, velocidade e fabricante. Só o "
                     "administrador do sistema enxerga esses dados.")
        row.set_subtitle_lines(2)
        row.add_prefix(w.icon_image("application-x-firmware-symbolic", 24))
        button = Gtk.Button(label="Ver")
        button.add_css_class("pill")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self._load_modules())
        row.add_suffix(button)
        return row

    def _load_modules(self) -> None:
        def on_payload(payload: str) -> None:
            try:
                self.modules = json.loads(payload)
            except ValueError:
                self.modules = []
            GLib.idle_add(self._after_modules)

        self.window.run_privileged(
            ["memoria"], title="Lendo os pentes de memória", success="",
            on_payload=on_payload,
            on_finish=lambda ok: None if ok else GLib.idle_add(self._after_modules),
        )

    def _after_modules(self) -> bool:
        if self.modules is None:
            self.modules = []
        self.reload()
        return False

    # -------------------------------------------------------------- copiar --

    def _texto_completo(self) -> str:
        linhas = []
        for section in self.sections:
            linhas.append(f"== {section.title} ==")
            for row in section.rows:
                linhas.append(f"- {row.title}"
                              + (f" ({row.subtitle})" if row.subtitle else ""))
                for chave, valor in row.details:
                    linhas.append(f"    {chave}: {valor}")
            linhas.append("")
        return "\n".join(linhas).strip()

    def _copiar(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set(self._texto_completo())
        self.window.notify("Lista de componentes copiada.")
