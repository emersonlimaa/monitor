"""Janela principal e aplicativo."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .core import privileged
from .ui import widgets as w
from .ui.apps import AppsPage
from .ui.cleanup import CleanupPage
from .ui.dashboard import DashboardPage
from .ui.storage import StoragePage
from .util import APP_ID, APP_NAME, VERSION

REFRESH_MS = 1500

CSS = """
.tag {
  font-size: 0.76em;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 999px;
  background-color: alpha(currentColor, 0.13);
}
.mr-applist {
  background: transparent;
}
.mr-applist > row {
  padding: 0;
  background: transparent;
}
.mr-progress-body {
  min-width: 320px;
}
"""


class MonitorWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1000, 780)
        self.set_size_request(380, 420)
        self._loaded_pages: set[str] = set()

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        view = Adw.ToolbarView()
        self.toasts.set_child(view)

        self.stack = Adw.ViewStack()
        self.dashboard = DashboardPage(self)
        self.apps = AppsPage(self)
        self.storage = StoragePage(self)
        self.cleanup = CleanupPage(self)
        self.stack.add_titled_with_icon(
            self.dashboard, "recursos", "Recursos", "speedometer-symbolic")
        self.stack.add_titled_with_icon(
            self.apps, "apps", "Aplicativos", "view-grid-symbolic")
        self.stack.add_titled_with_icon(
            self.storage, "disco", "Disco", "drive-harddisk-symbolic")
        self.stack.add_titled_with_icon(
            self.cleanup, "limpeza", "Limpeza", "user-trash-symbolic")
        self.stack.connect("notify::visible-child-name", self._on_page_changed)

        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=self.stack,
                                    policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        menu = Gio.Menu()
        menu.append("Atualizar", "win.refresh")
        menu.append(f"Sobre o {APP_NAME}", "app.about")
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(button)
        view.add_top_bar(header)
        view.set_content(self.stack)

        switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        view.add_bottom_bar(switcher_bar)

        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 640px"))
        breakpoint_.add_setter(switcher_bar, "reveal", True)
        breakpoint_.add_setter(switcher, "visible", False)
        self.add_breakpoint(breakpoint_)

        action = Gio.SimpleAction.new("refresh", None)
        action.connect("activate", lambda *_: self.refresh_current())
        self.add_action(action)

        self._timer = GLib.timeout_add(REFRESH_MS, self._on_tick)
        self.connect("close-request", self._on_close)
        self.storage.build_targets()

    # ------------------------------------------------------------- utilidades --

    def notify(self, message: str) -> None:
        self.toasts.add_toast(w.toast(message))

    def analyze_path(self, path: str) -> None:
        self.stack.set_visible_child_name("disco")
        self.storage.start_scan(path)

    def refresh_current(self) -> None:
        name = self.stack.get_visible_child_name()
        if name == "apps":
            self.apps.reload()
        elif name == "limpeza":
            self.cleanup.reload()
        elif name == "recursos":
            self.dashboard.refresh()
        elif name == "disco" and self.storage.result:
            self.storage.start_scan(self.storage.result.root)

    def _on_page_changed(self, *_args) -> None:
        name = self.stack.get_visible_child_name()
        if name in self._loaded_pages:
            return
        self._loaded_pages.add(name)
        if name == "apps":
            self.apps.reload()
        elif name == "limpeza":
            self.cleanup.reload()

    def _on_tick(self) -> bool:
        if self.get_mapped() and \
                self.stack.get_visible_child_name() == "recursos":
            self.dashboard.refresh()
        return True

    def _on_close(self, *_args) -> bool:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.storage.cancel.set()
        return False

    # -------------------------------------------- operações com senha ---------

    def run_privileged(self, args: list[str], *, title: str, success: str = "",
                       on_finish=None, on_payload=None, on_status=None,
                       silent: bool = False) -> None:
        ok, reason = privileged.available()
        if not ok:
            self.notify(reason)
            if on_finish:
                on_finish(False)
            return

        dialog = None
        status_label = None
        progress = None
        if not silent:
            dialog = Adw.Dialog(title=title)
            dialog.set_can_close(False)
            dialog.set_content_width(420)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            box.add_css_class("mr-progress-body")
            box.set_margin_top(24)
            box.set_margin_bottom(24)
            box.set_margin_start(24)
            box.set_margin_end(24)
            heading = w.heading(title, 3)
            heading.set_halign(Gtk.Align.CENTER)
            heading.set_wrap(True)
            heading.set_justify(Gtk.Justification.CENTER)
            box.append(heading)
            progress = Gtk.ProgressBar()
            progress.set_pulse_step(0.12)
            box.append(progress)
            status_label = w.dim("Pedindo autorização…", caption=False)
            status_label.set_halign(Gtk.Align.CENTER)
            status_label.set_justify(Gtk.Justification.CENTER)
            status_label.set_max_width_chars(44)
            box.append(status_label)
            dialog.set_child(box)
            dialog.present(self)

        state = {"pulse": True, "pulse_id": 0}

        def pulse() -> bool:
            if state["pulse"] and progress is not None:
                progress.pulse()
                return True
            return False

        if progress is not None:
            state["pulse_id"] = GLib.timeout_add(120, pulse)

        def set_status(text: str) -> bool:
            if status_label is not None and text:
                status_label.set_label(text)
            if on_status:
                on_status(text)
            return False

        def set_progress(value: float) -> bool:
            if progress is None:
                return False
            if value < 0:
                state["pulse"] = True
                if not state["pulse_id"]:
                    state["pulse_id"] = GLib.timeout_add(120, pulse)
            else:
                state["pulse"] = False
                state["pulse_id"] = 0
                progress.set_fraction(min(max(value, 0.0), 1.0))
            return False

        def finished(result: privileged.OperationResult) -> bool:
            state["pulse"] = False
            state["pulse_id"] = 0
            if dialog is not None:
                dialog.set_can_close(True)
                dialog.close()
            if result.cancelled:
                self.notify("Operação cancelada.")
            elif result.error:
                self.notify(result.error)
            elif success:
                self.notify(success)
            if on_finish:
                on_finish(result.ok)
            return False

        operation = privileged.Operation(
            args,
            on_status=lambda text: GLib.idle_add(set_status, text),
            on_progress=lambda value: GLib.idle_add(set_progress, value),
            on_result=(lambda payload: GLib.idle_add(
                lambda: (on_payload(payload), False)[1])) if on_payload else None,
            on_done=lambda result: GLib.idle_add(finished, result),
        )
        operation.start()


class MonitorApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window: MonitorWindow | None = None
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("win.refresh", ["<Primary>r", "F5"])

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        display = Gtk.Widget.get_default_direction() and None
        from gi.repository import Gdk

        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MonitorWindow(self)
        self.window.present()

    def _on_about(self, *_args) -> None:
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="Emerson Lima",
            comments="Mostra os recursos do computador, o que ocupa o disco e "
                     "desinstala programas sem precisar de terminal.",
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/emersonlimaa/monitor",
        )
        dialog.present(self.window)


def main(argv: list[str] | None = None) -> int:
    import sys

    return MonitorApp().run(argv if argv is not None else sys.argv)
