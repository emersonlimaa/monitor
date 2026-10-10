"""Aba de recursos: processador, memória, discos, rede e processos."""

from __future__ import annotations

import os
import signal

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ..core import sysinfo
from ..util import fmt_bytes, fmt_duration, fmt_int, fmt_pct
from . import widgets as w

PROC_ROWS = 6


class DashboardPage(Gtk.ScrolledWindow):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.cpu = sysinfo.CpuMonitor()
        self.net = sysinfo.NetMonitor()
        self.procs = sysinfo.ProcessMonitor()
        self._disk_rows: dict[str, tuple] = {}
        self._tick = 0

        clamp = Adw.Clamp(maximum_size=1000, tightening_threshold=860)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(28)
        box.set_margin_start(14)
        box.set_margin_end(14)
        clamp.set_child(box)
        self.set_child(clamp)

        box.append(self._build_gauges())
        box.append(self._build_cpu())
        box.append(self._build_memory())
        box.append(self._build_disks())
        box.append(self._build_network())
        box.append(self._build_processes())
        box.append(self._build_system())

        self.refresh()

    # ------------------------------------------------------------ medidores --

    def _build_gauges(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                      homogeneous=True)
        self.g_cpu = w.RingGauge("Processador")
        self.g_ram = w.RingGauge("Memória")
        self.g_disk = w.RingGauge("Disco do sistema")
        self.g_swap = w.RingGauge("Memória virtual")
        for gauge in (self.g_cpu, self.g_ram, self.g_disk, self.g_swap):
            row.append(gauge)
        return w.card(row, padding=16)

    # ----------------------------------------------------------- processador --

    def _build_cpu(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title="Processador")
        self.row_cpu = Adw.ActionRow(title=self.cpu.model)
        self.row_cpu.set_subtitle("—")
        self.row_cpu.add_prefix(w.icon_image("computer-symbolic", 24))
        group.add(self.row_cpu)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label="Uso por núcleo", xalign=0)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        label.set_hexpand(True)
        self.lbl_temp = w.value_label("—")
        head.append(label)
        head.append(self.lbl_temp)
        inner.append(head)
        self.cores = w.CoreBars()
        inner.append(self.cores)
        self.spark_cpu = w.Sparkline(points=90, height=46)
        inner.append(self.spark_cpu)
        group.add(w.group_row(inner))
        return group

    # --------------------------------------------------------------- memória --

    def _build_memory(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title="Memória")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.ram_box, _, self.ram_bar, self.ram_val = w.bar_row("Memória em uso")
        inner.append(self.ram_box)
        self.swap_box, _, self.swap_bar, self.swap_val = w.bar_row(
            "Memória virtual (swap)")
        inner.append(self.swap_box)
        self.lbl_mem_detail = w.dim("—")
        inner.append(self.lbl_mem_detail)
        group.add(w.group_row(inner))
        return group

    # ---------------------------------------------------------------- discos --

    def _build_disks(self) -> Gtk.Widget:
        self.group_disks = Adw.PreferencesGroup(
            title="Discos",
            description="Clique em analisar para ver o que ocupa o espaço")
        self.disks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        return self.group_disks

    def _disk_row(self, disk: sysinfo.Disk) -> tuple:
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = w.icon_image(
            "drive-removable-media-symbolic" if disk.removable
            else "drive-harddisk-symbolic", 26)
        icon.set_valign(Gtk.Align.CENTER)
        top.append(icon)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(xalign=0)
        name.add_css_class("heading")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        texts.append(name)
        detail = Gtk.Label(xalign=0)
        detail.add_css_class("caption")
        detail.add_css_class("dim-label")
        detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        texts.append(detail)
        top.append(texts)

        free = Gtk.Label(xalign=1)
        free.add_css_class("numeric")
        free.set_valign(Gtk.Align.CENTER)
        top.append(free)

        button = Gtk.Button(icon_name="edit-find-symbolic")
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text("Ver o que ocupa espaço neste disco")
        button.connect("clicked", lambda _b, p=disk.mountpoint:
                       self.window.analyze_path(p))
        top.append(button)
        inner.append(top)

        meter = w.BarMeter(height=9)
        inner.append(meter)
        row = w.group_row(inner, pad=12)
        return row, meter, name, detail, free

    # ------------------------------------------------------------------ rede --

    def _build_network(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title="Rede")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.lbl_down = Gtk.Label(label="↓ —", xalign=0)
        self.lbl_down.add_css_class("numeric")
        self.lbl_up = Gtk.Label(label="↑ —", xalign=0)
        self.lbl_up.add_css_class("numeric")
        self.lbl_iface = w.dim("—")
        self.lbl_iface.set_hexpand(True)
        self.lbl_iface.set_xalign(1)
        top.append(self.lbl_down)
        top.append(self.lbl_up)
        top.append(self.lbl_iface)
        inner.append(top)
        self.spark_net = w.Sparkline(points=90, height=40, color="#4a7fc1")
        inner.append(self.spark_net)
        group.add(w.group_row(inner))
        return group

    # ------------------------------------------------------------- processos --

    def _build_processes(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="Quem está consumindo agora",
            description="Atualiza a cada poucos segundos")
        self.proc_rows = []
        for _ in range(PROC_ROWS):
            row = Adw.ActionRow()
            row.set_use_markup(False)
            cpu = w.value_label("—")
            cpu.set_size_request(64, -1)
            ram = w.value_label("—")
            ram.set_size_request(82, -1)
            kill = Gtk.Button(icon_name="window-close-symbolic")
            kill.add_css_class("flat")
            kill.set_valign(Gtk.Align.CENTER)
            kill.set_tooltip_text("Encerrar este programa")
            row.add_suffix(cpu)
            row.add_suffix(ram)
            row.add_suffix(kill)
            row.set_visible(False)
            group.add(row)
            self.proc_rows.append((row, cpu, ram, kill))
        return group

    def _ask_kill(self, pid: int, name: str) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Encerrar “{name}”?",
            body="O programa será fechado. Trabalhos não salvos podem ser "
                 "perdidos.")
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("kill", "Encerrar")
        dialog.set_response_appearance("kill", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def done(_d, response):
            if dialog.choose_finish(response) != "kill":
                return
            try:
                os.kill(pid, signal.SIGTERM)
                self.window.notify(f"“{name}” foi encerrado.")
            except (OSError, ProcessLookupError):
                self.window.notify("Não foi possível encerrar o programa.")

        dialog.choose(self.window, None, done)

    # --------------------------------------------------------------- sistema --

    def _build_system(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title="Sistema")
        self.row_os = Adw.ActionRow(title="Sistema", subtitle=sysinfo.distro())
        self.row_os.add_prefix(w.icon_image("emblem-system-symbolic", 24))
        group.add(self.row_os)
        self.row_up = Adw.ActionRow(title="Ligado há", subtitle="—")
        self.row_up.add_prefix(w.icon_image("alarm-symbolic", 24))
        group.add(self.row_up)
        self.row_kernel = Adw.ActionRow(
            title="Núcleo e ambiente",
            subtitle=f"Linux {sysinfo.kernel()} · {sysinfo.desktop()}")
        self.row_kernel.add_prefix(w.icon_image("application-x-firmware-symbolic", 24))
        group.add(self.row_kernel)
        self.row_bat = Adw.ActionRow(title="Bateria", subtitle="—")
        self.row_bat.add_prefix(w.icon_image("battery-symbolic", 24))
        self.row_bat.set_visible(False)
        group.add(self.row_bat)
        return group

    # ------------------------------------------------------------ atualização --

    def refresh(self) -> None:
        self._tick += 1
        cpu = self.cpu.sample()
        self.g_cpu.update(cpu.total, fmt_pct(cpu.total),
                          f"{self.cpu.cores} núcleos · {self.cpu.threads} threads",
                          w.usage_color(cpu.total))
        self.cores.update(cpu.per_core)
        self.spark_cpu.push(cpu.total)
        load = sysinfo.load_average()
        pcount, tcount = (0, 0)
        if self._tick % 4 == 1:
            pcount, tcount = sysinfo.process_count()
            self._counts = (pcount, tcount)
        pcount, tcount = getattr(self, "_counts", (0, 0))
        freq = f"{cpu.freq_mhz / 1000:.1f} GHz".replace(".", ",") \
            if cpu.freq_mhz else "—"
        self.row_cpu.set_subtitle(
            f"{freq} · carga {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f} · "
            f"{fmt_int(pcount)} processos · {fmt_int(tcount)} threads"
            .replace(".", ",", 3) if False else
            f"{freq} · carga {load[0]:.2f} · {fmt_int(pcount)} processos · "
            f"{fmt_int(tcount)} threads")
        temp = sysinfo.cpu_temperature()
        self.lbl_temp.set_label(f"{temp:.0f} °C" if temp else "—")

        mem = sysinfo.memory()
        self.g_ram.update(mem.frac, fmt_pct(mem.frac),
                          f"{fmt_bytes(mem.used)} de {fmt_bytes(mem.total)}",
                          w.usage_color(mem.frac))
        self.ram_bar.update(mem.frac, w.usage_color(mem.frac))
        self.ram_val.set_label(f"{fmt_bytes(mem.used)} / {fmt_bytes(mem.total)}")
        self.swap_bar.update(mem.swap_frac, w.usage_color(mem.swap_frac))
        self.swap_val.set_label(
            f"{fmt_bytes(mem.swap_used)} / {fmt_bytes(mem.swap_total)}"
            if mem.swap_total else "não configurada")
        self.g_swap.update(mem.swap_frac,
                           fmt_pct(mem.swap_frac) if mem.swap_total else "—",
                           fmt_bytes(mem.swap_used) if mem.swap_total
                           else "sem swap", w.usage_color(mem.swap_frac))
        self.lbl_mem_detail.set_label(
            f"Disponível {fmt_bytes(mem.available)} · em cache "
            f"{fmt_bytes(mem.cache_total)} · livre {fmt_bytes(mem.free)}")

        if self._tick % 2 == 1:
            self._refresh_disks()
        net = self.net.sample()
        self.lbl_down.set_label(f"↓ {fmt_bytes(net.rx_rate)}/s")
        self.lbl_up.set_label(f"↑ {fmt_bytes(net.tx_rate)}/s")
        self.lbl_iface.set_label(
            f"{', '.join(net.interfaces[:3]) or 'sem conexão'} · "
            f"total {fmt_bytes(net.rx_total)} recebidos")
        self.spark_net.push(net.rx_rate + net.tx_rate)

        if self._tick % 2 == 1:
            self._refresh_procs()
        self.row_up.set_subtitle(fmt_duration(sysinfo.uptime_seconds()))
        bat = sysinfo.battery()
        if bat:
            self.row_bat.set_visible(True)
            left = f" · {fmt_duration(bat.time_left)} restantes" \
                if bat.time_left else ""
            self.row_bat.set_subtitle(
                f"{fmt_pct(bat.percent)} · {bat.status}{left}")

    def _refresh_disks(self) -> None:
        disks = sysinfo.disks()
        keys = {d.mountpoint for d in disks}
        for key in list(self._disk_rows):
            if key not in keys:
                self.group_disks.remove(self._disk_rows[key][0])
                del self._disk_rows[key]
        for disk in disks:
            entry = self._disk_rows.get(disk.mountpoint)
            if entry is None:
                entry = self._disk_row(disk)
                self.group_disks.add(entry[0])
                self._disk_rows[disk.mountpoint] = entry
            row, meter, name, detail, free = entry
            name.set_label(disk.title)
            detail.set_label(f"{disk.kind} · {disk.fs_label} · {disk.mountpoint}")
            free.set_label(f"{fmt_bytes(disk.free)} livres de "
                           f"{fmt_bytes(disk.total)}")
            meter.update(disk.frac, w.usage_color(disk.frac))
            if disk.mountpoint == "/":
                self.g_disk.update(
                    disk.frac, fmt_pct(disk.frac),
                    f"{fmt_bytes(disk.free)} livres", w.usage_color(disk.frac))

    def _refresh_procs(self) -> None:
        procs = self.procs.sample(limit=PROC_ROWS)
        for index, (row, cpu_lbl, ram_lbl, kill) in enumerate(self.proc_rows):
            if index >= len(procs):
                row.set_visible(False)
                continue
            proc = procs[index]
            row.set_visible(True)
            row.set_title(GLib.markup_escape_text(proc.name))
            row.set_subtitle(GLib.markup_escape_text(
                proc.cmdline[:110] if proc.cmdline != proc.name else f"PID {proc.pid}"))
            cpu_lbl.set_label(fmt_pct(proc.cpu, 1))
            ram_lbl.set_label(fmt_bytes(proc.rss))
            kill.set_visible(proc.own)
            if proc.own:
                for handler in getattr(kill, "_handlers", []):
                    kill.disconnect(handler)
                handler = kill.connect(
                    "clicked",
                    lambda _b, pid=proc.pid, name=proc.name: self._ask_kill(pid, name))
                kill._handlers = [handler]
