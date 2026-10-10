"""Aba de armazenamento: a porcentagem do disco por tipo de arquivo.

Responde "o que tem dentro do meu SSD": quanto é filme, música, documento,
projeto de programação, jogo, cache. Só leitura de tamanhos, nada de abrir
o conteúdo dos arquivos.
"""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from ..core import scanner
from ..util import fmt_bytes, fmt_elapsed, fmt_int, fmt_pct, shorten_path
from . import widgets as w


class StoragePage(Gtk.Box):
    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.result: scanner.ScanResult | None = None
        self.cancel = threading.Event()
        self._scanning = False
        self._targets_built = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.stack.add_named(self._build_targets(), "alvos")
        self.stack.add_named(self._build_progress(), "varrendo")
        self.stack.add_named(self._build_results(), "resultado")
        self.append(self.stack)
        self.stack.set_visible_child_name("alvos")

    # ------------------------------------------------------- escolha do alvo --

    def _build_targets(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=760)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(14)
        box.set_margin_end(14)

        title = w.heading("O que tem dentro do seu disco", 2)
        title.set_halign(Gtk.Align.CENTER)
        box.append(title)
        sub = w.dim("Escolha onde olhar. A análise só mede o tamanho dos "
                    "arquivos: nunca abre o conteúdo deles.", caption=False)
        sub.set_halign(Gtk.Align.CENTER)
        sub.set_justify(Gtk.Justification.CENTER)
        box.append(sub)

        self.targets_group = Adw.PreferencesGroup()
        box.append(self.targets_group)

        other = Adw.PreferencesGroup()
        row = Adw.ActionRow(title="Escolher outra pasta…",
                            subtitle="Analisa qualquer lugar do computador")
        row.add_prefix(w.icon_image("folder-open-symbolic", 24))
        row.set_activatable(True)
        row.connect("activated", lambda _r: self._choose_folder())
        other.add(row)
        box.append(other)

        clamp.set_child(box)
        scroller.set_child(clamp)
        return scroller

    def build_targets(self) -> None:
        if self._targets_built:
            return
        self._targets_built = True
        for target in scanner.suggested_targets():
            row = Adw.ActionRow(title=target.title, subtitle=target.subtitle)
            row.add_prefix(w.icon_image(target.icon, 28))
            row.set_activatable(True)
            if target.needs_root:
                switch = Gtk.Switch(active=False, valign=Gtk.Align.CENTER)
                switch.set_tooltip_text("Inclui as pastas do sistema; pede senha")
                hint = Gtk.Label(label="com senha")
                hint.add_css_class("caption")
                hint.add_css_class("dim-label")
                hint.set_valign(Gtk.Align.CENTER)
                row.add_suffix(hint)
                row.add_suffix(switch)
                row.connect("activated", lambda _r, p=target.path, s=switch:
                            self.start_scan(p, as_root=s.get_active()))
            else:
                row.connect("activated",
                            lambda _r, p=target.path: self.start_scan(p))
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            self.targets_group.add(row)

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Escolher pasta para analisar")
        dialog.set_initial_folder(Gio.File.new_for_path(os.path.expanduser("~")))

        def done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self.start_scan(folder.get_path())

        dialog.select_folder(self.window, None, done)

    # ------------------------------------------------------------ progresso ---

    def _build_progress(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_start(24)
        box.set_margin_end(24)

        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(52, 52)
        box.append(spinner)

        self.prog_title = w.heading("Analisando…", 3)
        self.prog_title.set_halign(Gtk.Align.CENTER)
        box.append(self.prog_title)

        self.prog_counts = Gtk.Label(label="—")
        self.prog_counts.add_css_class("title-1")
        self.prog_counts.add_css_class("numeric")
        box.append(self.prog_counts)

        self.prog_path = Gtk.Label(label="")
        self.prog_path.add_css_class("caption")
        self.prog_path.add_css_class("dim-label")
        self.prog_path.set_max_width_chars(56)
        self.prog_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.append(self.prog_path)

        hint = w.dim("A primeira análise é a mais demorada: o sistema precisa "
                     "ler a lista de arquivos do disco.")
        hint.set_halign(Gtk.Align.CENTER)
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_max_width_chars(50)
        box.append(hint)

        button = Gtk.Button(label="Cancelar")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self.cancel.set())
        box.append(button)
        return box

    # ------------------------------------------------------------ varredura ---

    def start_scan(self, path: str, *, as_root: bool = False) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.cancel.clear()
        self.prog_title.set_label(f"Analisando {shorten_path(path)}")
        self.prog_counts.set_label("0 arquivos")
        self.prog_path.set_label("")
        self.stack.set_visible_child_name("varrendo")

        if as_root:
            self._scan_as_root(path)
            return

        def progress(files: int, size: int, current: str) -> None:
            GLib.idle_add(self._on_progress, files, size, current)

        def work():
            try:
                result = scanner.scan(path, cancel=self.cancel, progress=progress)
            except Exception:
                GLib.idle_add(self._scan_failed)
                return
            GLib.idle_add(self._scan_done, result)

        threading.Thread(target=work, daemon=True).start()

    def _scan_as_root(self, path: str) -> None:
        def on_payload(payload: str) -> None:
            try:
                result = scanner.ScanResult.from_json(payload)
            except Exception:
                return
            result.as_root = True
            GLib.idle_add(self._scan_done, result)

        def on_status(text: str) -> None:
            parts = text.split(" · ", 1)
            GLib.idle_add(self._on_status, parts[0],
                          parts[1] if len(parts) > 1 else "")

        self.window.run_privileged(
            ["varrer", path],
            title=f"Analisando {shorten_path(path)}",
            success="",
            silent=True,
            on_status=on_status,
            on_payload=on_payload,
            on_finish=lambda ok: None if ok else self._scan_cancelled(),
        )

    def _on_status(self, counts: str, path: str) -> bool:
        self.prog_counts.set_label(counts)
        self.prog_path.set_label(path)
        return False

    def _on_progress(self, files: int, size: int, current: str) -> bool:
        if not self._scanning:
            return False
        self.prog_counts.set_label(f"{fmt_int(files)} arquivos · {fmt_bytes(size)}")
        self.prog_path.set_label(shorten_path(current, keep=4))
        return False

    def _scan_failed(self) -> bool:
        self._scanning = False
        self.stack.set_visible_child_name("alvos")
        self.window.notify("Não foi possível analisar esta pasta.")
        return False

    def _scan_cancelled(self) -> bool:
        self._scanning = False
        self.stack.set_visible_child_name("alvos")
        return False

    def _scan_done(self, result: scanner.ScanResult) -> bool:
        self._scanning = False
        self.result = result
        self._render()
        self.stack.set_visible_child_name("resultado")
        return False

    # ------------------------------------------------------------ resultado ---

    def _build_results(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=940, tightening_threshold=820)
        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.results_box.set_margin_top(16)
        self.results_box.set_margin_bottom(28)
        self.results_box.set_margin_start(14)
        self.results_box.set_margin_end(14)
        clamp.set_child(self.results_box)
        scroller.set_child(clamp)
        return scroller

    def _render(self) -> None:
        res = self.result
        if res is None:
            return
        child = self.results_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.results_box.remove(child)
            child = nxt

        self.results_box.append(self._render_header(res))
        self.results_box.append(self._render_bar(res))
        self.results_box.append(self._render_categories(res))

    def _render_header(self, res: scanner.ScanResult) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        title = w.heading(shorten_path(res.root, keep=2), 2)
        title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        texts.append(title)
        detail = (f"{fmt_int(res.files)} arquivos em {fmt_int(res.dirs)} pastas · "
                  f"analisado em {fmt_elapsed(res.elapsed)}")
        if res.errors:
            detail += f" · {fmt_int(res.errors)} pastas sem permissão"
        texts.append(w.dim(detail))
        top.append(texts)
        total = Gtk.Label(label=fmt_bytes(res.size), xalign=1)
        total.add_css_class("title-1")
        total.add_css_class("numeric")
        total.set_valign(Gtk.Align.CENTER)
        top.append(total)
        box.append(top)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        again = Gtk.Button(label="Analisar outra pasta")
        again.add_css_class("pill")
        again.connect("clicked",
                      lambda _b: self.stack.set_visible_child_name("alvos"))
        buttons.append(again)
        redo = Gtk.Button(label="Analisar de novo")
        redo.add_css_class("pill")
        redo.connect("clicked", lambda _b: self.start_scan(res.root))
        buttons.append(redo)
        box.append(buttons)

        if res.partial:
            banner = Adw.Banner(
                title="Análise interrompida: os números estão incompletos.")
            banner.set_revealed(True)
            box.append(banner)
        elif res.errors and not res.as_root:
            banner = Adw.Banner(
                title="Algumas pastas exigem administrador e ficaram de fora.")
            banner.set_revealed(True)
            box.append(banner)
        return w.card(box)

    def _render_bar(self, res: scanner.ScanResult) -> Gtk.Widget:
        cats = res.ordered_categories()
        total = res.size or 1
        bar = w.StackedBar(height=30)
        bar.set_parts([(c.size / total, scanner.category_color(c.key))
                       for c in cats])
        return w.card(bar)

    def _render_categories(self, res: scanner.ScanResult) -> Gtk.Widget:
        total = res.size or 1
        group = Adw.PreferencesGroup(
            title="Por tipo de arquivo",
            description="Quanto cada tipo de conteúdo ocupa do total analisado")
        cats = res.ordered_categories()
        if not cats:
            group.add(Adw.ActionRow(title="Nenhum arquivo encontrado"))
            return group
        for cat in cats:
            label, icon, desc, color = scanner.CATEGORY_INFO[cat.key]
            frac = cat.size / total
            row = Adw.ActionRow(title=label)
            row.set_subtitle(f"{fmt_bytes(cat.size)} · {fmt_int(cat.files)} "
                             f"arquivos · {desc}")
            row.set_subtitle_lines(2)
            row.add_prefix(w.category_swatch(color, 16))

            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            right.set_valign(Gtk.Align.CENTER)
            pct = Gtk.Label(label=fmt_pct(frac, 1), xalign=1)
            pct.add_css_class("title-4")
            pct.add_css_class("numeric")
            right.append(pct)
            meter = w.BarMeter(height=6, color=color)
            meter.set_size_request(130, -1)
            meter.update(frac)
            right.append(meter)
            row.add_suffix(right)
            group.add(row)
        return group
