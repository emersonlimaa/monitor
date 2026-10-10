"""Widgets desenhados à mão: medidores, barras e mini gráficos.

O desenho usa Gsk (a camada de renderização do próprio GTK 4) em vez de
cairo: assim o pacote não precisa do python3-gi-cairo e tudo é desenhado
pela GPU junto com o resto da interface.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Graphene, Gsk, Gtk, Pango  # noqa: E402

from ..util import fmt_bytes, fmt_pct

# Limiares de uso: abaixo de 75% é tranquilo, acima de 90% é aperto.
OK_COLOR = "#3a9a74"
WARN_COLOR = "#c88a2e"
CRIT_COLOR = "#c4475b"


def rgba(color: str, alpha: float = 1.0) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(color)
    c.alpha = alpha
    return c


def fade(color: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
    out = Gdk.RGBA()
    out.red, out.green, out.blue = color.red, color.green, color.blue
    out.alpha = alpha
    return out


def usage_color(frac: float) -> str:
    if frac >= 0.90:
        return CRIT_COLOR
    if frac >= 0.75:
        return WARN_COLOR
    return OK_COLOR


def accent() -> Gdk.RGBA:
    try:
        return Adw.StyleManager.get_default().get_accent_color_rgba()
    except Exception:
        return rgba("#3584e4")


def is_dark() -> bool:
    try:
        return Adw.StyleManager.get_default().get_dark()
    except Exception:
        return False


def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, max(w, 0), max(h, 0))


def _rounded_path(x: float, y: float, w: float, h: float, r: float,
                  left: bool = True, right: bool = True) -> Gsk.Path:
    """Retângulo de cantos arredondados montado à mão.

    O Gsk.RoundedRect não sobrevive à introspecção em Python (sai um
    retângulo degenerado), então o caminho é desenhado aqui.
    """
    builder = Gsk.PathBuilder.new()
    if w <= 0 or h <= 0:
        return builder.to_path()
    r = max(min(r, w / 2, h / 2), 0.0)
    if r < 0.6:
        builder.add_rect(_rect(x, y, w, h))
        return builder.to_path()
    rl = r if left else 0.0
    rr = r if right else 0.0
    builder.move_to(x + rl, y)
    builder.line_to(x + w - rr, y)
    if rr:
        builder.svg_arc_to(rr, rr, 0.0, False, True, x + w, y + rr)
    builder.line_to(x + w, y + h - rr)
    if rr:
        builder.svg_arc_to(rr, rr, 0.0, False, True, x + w - rr, y + h)
    builder.line_to(x + rl, y + h)
    if rl:
        builder.svg_arc_to(rl, rl, 0.0, False, True, x, y + h - rl)
    builder.line_to(x, y + rl)
    if rl:
        builder.svg_arc_to(rl, rl, 0.0, False, True, x + rl, y)
    builder.close()
    return builder.to_path()


def _fill_rounded(snapshot: Gtk.Snapshot, x: float, y: float, w: float,
                  h: float, r: float, color: Gdk.RGBA,
                  left: bool = True, right: bool = True) -> None:
    if w <= 0 or h <= 0:
        return
    snapshot.append_fill(_rounded_path(x, y, w, h, r, left, right),
                         Gsk.FillRule.WINDING, color)


def _arc_path(cx: float, cy: float, radius: float, value: float) -> Gsk.Path:
    """Arco começando no topo e girando no sentido do relógio."""
    builder = Gsk.PathBuilder.new()
    if value >= 0.9995:
        builder.add_circle(Graphene.Point().init(cx, cy), radius)
        return builder.to_path()
    end = -math.pi / 2 + 2 * math.pi * value
    builder.move_to(cx, cy - radius)
    builder.svg_arc_to(radius, radius, 0.0, value > 0.5, True,
                       cx + radius * math.cos(end), cy + radius * math.sin(end))
    return builder.to_path()


def _layout(widget: Gtk.Widget, text: str, size: float,
            weight: str = "Bold") -> Pango.Layout:
    layout = widget.create_pango_layout(text)
    layout.set_font_description(Pango.FontDescription(f"{weight} {size:.0f}px"))
    return layout


def _draw_text(widget: Gtk.Widget, snapshot: Gtk.Snapshot, text: str,
               size: float, cx: float, cy: float, color: Gdk.RGBA) -> None:
    layout = _layout(widget, text, size)
    tw, th = layout.get_pixel_size()
    snapshot.save()
    snapshot.translate(Graphene.Point().init(cx - tw / 2, cy - th / 2))
    snapshot.append_layout(layout, color)
    snapshot.restore()


# ------------------------------------------------------------- medidores -----


class _RingArea(Gtk.Widget):
    __gtype_name__ = "MrRingArea"

    def __init__(self, size: int) -> None:
        super().__init__()
        self._size = size
        self.value = 0.0
        self.center = "—"
        self.color: str | None = None

    def do_measure(self, orientation, for_size):
        return (self._size, self._size, -1, -1)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        fg = self.get_color()
        radius = min(width, height) / 2
        thickness = max(8.0, radius * 0.22)
        cx, cy = width / 2, height / 2
        r = radius - thickness / 2 - 1
        if r <= 0:
            return
        stroke = Gsk.Stroke.new(thickness)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(_arc_path(cx, cy, r, 1.0), stroke, fade(fg, 0.13))
        if self.value > 0.002:
            color = rgba(self.color) if self.color else accent()
            snapshot.append_stroke(_arc_path(cx, cy, r, self.value), stroke, color)
        _draw_text(self, snapshot, self.center, radius * 0.44, cx, cy, fg)


class RingGauge(Gtk.Box):
    """Anel de progresso com o valor no centro."""

    def __init__(self, title: str, *, size: int = 116, subtitle: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_halign(Gtk.Align.CENTER)
        self.area = _RingArea(size)
        self.append(self.area)

        self.label = Gtk.Label(label=title)
        self.label.add_css_class("heading")
        self.append(self.label)

        self.sub = Gtk.Label(label=subtitle)
        self.sub.add_css_class("caption")
        self.sub.add_css_class("dim-label")
        self.sub.set_wrap(True)
        self.sub.set_justify(Gtk.Justification.CENTER)
        self.sub.set_max_width_chars(18)
        self.append(self.sub)

    def update(self, value: float, center: str, subtitle: str = "",
               color: str | None = None) -> None:
        self.area.value = max(0.0, min(1.0, value or 0.0))
        self.area.center = center
        self.area.color = color
        if subtitle:
            self.sub.set_label(subtitle)
        self.area.queue_draw()


class BarMeter(Gtk.Widget):
    """Barra fina de uso, com cor conforme a folga."""

    __gtype_name__ = "MrBarMeter"

    def __init__(self, height: int = 10, *, color: str | None = None,
                 radius: float = 6.0) -> None:
        super().__init__()
        self._height = height
        self._min_width = 40
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self._value = 0.0
        self._color = color
        self._radius = radius

    def update(self, value: float, color: str | None = None) -> None:
        self._value = max(0.0, min(1.0, value or 0.0))
        if color:
            self._color = color
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (self._min_width, self._min_width, -1, -1)


    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        fg = self.get_color()
        _fill_rounded(snapshot, 0, 0, width, height, self._radius, fade(fg, 0.13))
        if self._value <= 0:
            return
        color = rgba(self._color) if self._color else accent()
        filled = max(width * self._value, min(height * 0.9, width))
        _fill_rounded(snapshot, 0, 0, filled, height, self._radius, color)


class StackedBar(Gtk.Widget):
    """Barra composta: a proporção de cada categoria."""

    __gtype_name__ = "MrStackedBar"

    def __init__(self, height: int = 26) -> None:
        super().__init__()
        self._height = height
        self._min_width = 80
        self.set_hexpand(True)
        self._parts: list[tuple[float, str]] = []

    def set_parts(self, parts: list[tuple[float, str]]) -> None:
        self._parts = [(f, c) for f, c in parts if f > 0]
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (self._min_width, self._min_width, -1, -1)


    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        fg = self.get_color()
        radius = height / 2
        _fill_rounded(snapshot, 0, 0, width, height, radius, fade(fg, 0.10))
        if not self._parts:
            return
        x = 0.0
        gap = 1.5
        last = len(self._parts) - 1
        for index, (frac, color) in enumerate(self._parts):
            w = width * frac
            if w <= 0:
                continue
            segment = max(w - gap, 0.8) if index < last else max(w, 0.8)
            _fill_rounded(snapshot, x, 0, min(segment, width - x), height,
                          radius, rgba(color),
                          left=index == 0, right=index == last)
            x += w


class Sparkline(Gtk.Widget):
    """Histórico recente de um valor (CPU, rede…)."""

    __gtype_name__ = "MrSparkline"

    def __init__(self, points: int = 60, height: int = 42,
                 color: str | None = None, fill: bool = True) -> None:
        super().__init__()
        self._height = height
        self._min_width = 80
        self.set_hexpand(True)
        self._points = points
        # Começa com a linha deitada no zero: o gráfico entra pela direita.
        self._values: list[float] = [0.0] * points
        self._color = color
        self._fill = fill

    def push(self, value: float) -> None:
        self._values.append(max(0.0, value))
        if len(self._values) > self._points:
            del self._values[:-self._points]
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (self._min_width, self._min_width, -1, -1)


    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        fg = self.get_color()
        snapshot.append_color(fade(fg, 0.08), _rect(0, height - 1, width, 1))
        values = self._values
        if len(values) < 2:
            return
        peak = max(max(values), 0.0001)
        color = rgba(self._color) if self._color else accent()
        step = width / max(self._points - 1, 1)
        offset = width - step * (len(values) - 1)
        pts = [(offset + i * step, height - 2 - (v / peak) * (height - 5))
               for i, v in enumerate(values)]

        if self._fill:
            builder = Gsk.PathBuilder.new()
            builder.move_to(pts[0][0], height)
            for x, y in pts:
                builder.line_to(x, y)
            builder.line_to(pts[-1][0], height)
            builder.close()
            snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING,
                                 fade(color, 0.16))

        builder = Gsk.PathBuilder.new()
        builder.move_to(*pts[0])
        for x, y in pts[1:]:
            builder.line_to(x, y)
        stroke = Gsk.Stroke.new(1.8)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(builder.to_path(), stroke, color)


class CoreBars(Gtk.Widget):
    """Uma barra vertical por núcleo do processador."""

    __gtype_name__ = "MrCoreBars"

    def __init__(self, height: int = 54) -> None:
        super().__init__()
        self._height = height
        self._min_width = 80
        self.set_hexpand(True)
        self._values: list[float] = []

    def update(self, values: list[float]) -> None:
        self._values = values
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (self._min_width, self._min_width, -1, -1)


    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        n = len(self._values)
        if n == 0 or width <= 0 or height <= 0:
            return
        fg = self.get_color()
        gap = 3.0 if n <= 32 else 2.0
        bar = max((width - gap * (n - 1)) / n, 1.5)
        radius = min(bar / 2, 3)
        for index, value in enumerate(self._values):
            x = index * (bar + gap)
            _fill_rounded(snapshot, x, 0, bar, height, radius, fade(fg, 0.13))
            h = max(height * max(0.0, min(1.0, value)), 2.0)
            _fill_rounded(snapshot, x, height - h, bar, h, radius,
                          rgba(usage_color(value)))


class Swatch(Gtk.Widget):
    """Quadradinho colorido da legenda de categorias."""

    __gtype_name__ = "MrSwatch"

    def __init__(self, color: str, size: int) -> None:
        super().__init__()
        self._height = size
        self._min_width = size
        self.set_valign(Gtk.Align.CENTER)
        self._color = color

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (self._min_width, self._min_width, -1, -1)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        _fill_rounded(snapshot, 0, 0, width, height, 3, rgba(self._color))


# ------------------------------------------------------- blocos prontos ------


def card(child: Gtk.Widget, *, padding: int = 14) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.add_css_class("card")
    child.set_margin_top(padding)
    child.set_margin_bottom(padding)
    child.set_margin_start(padding)
    child.set_margin_end(padding)
    box.append(child)
    return box


def tag(text: str, style: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class("tag")
    if style:
        label.add_css_class(style)
    label.set_valign(Gtk.Align.CENTER)
    return label


def heading(text: str, level: int = 4) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class(f"title-{level}")
    return label


def dim(text: str, caption: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("dim-label")
    if caption:
        label.add_css_class("caption")
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return label


def value_label(text: str, numeric: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=1)
    label.add_css_class("dim-label")
    if numeric:
        label.add_css_class("numeric")
    return label


def icon_image(name: str, size: int = 32) -> Gtk.Image:
    """Aceita nome de ícone do tema ou caminho absoluto (snaps e flatpaks)."""
    image = Gtk.Image()
    image.set_pixel_size(size)
    if name and name.startswith("/"):
        try:
            image.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(name)))
            return image
        except Exception:
            pass
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display else None
    if name and theme and theme.has_icon(name):
        image.set_from_icon_name(name)
    else:
        image.set_from_icon_name("application-x-executable-symbolic")
    return image


def category_swatch(color: str, size: int = 12) -> Gtk.Widget:
    return Swatch(color, size)


def bar_row(title: str, *, subtitle: str = "", color: str | None = None
            ) -> tuple[Gtk.Box, Gtk.Label, BarMeter, Gtk.Label]:
    """Linha com título, valor à direita e barra de uso embaixo."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    name = Gtk.Label(label=title, xalign=0)
    name.add_css_class("body")
    name.set_hexpand(True)
    name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    val = Gtk.Label(label="—", xalign=1)
    val.add_css_class("numeric")
    val.add_css_class("dim-label")
    top.append(name)
    top.append(val)
    box.append(top)
    meter = BarMeter(color=color)
    box.append(meter)
    if subtitle:
        box.append(dim(subtitle))
    return box, name, meter, val


def group_row(child: Gtk.Widget, *, pad: int = 12) -> Adw.PreferencesRow:
    """Encaixa um widget qualquer dentro de uma lista de preferências."""
    row = Adw.PreferencesRow(activatable=False, selectable=False)
    row.set_focusable(False)
    child.set_margin_top(pad)
    child.set_margin_bottom(pad)
    child.set_margin_start(pad)
    child.set_margin_end(pad)
    row.set_child(child)
    return row


def toast(text: str, timeout: int = 4) -> Adw.Toast:
    t = Adw.Toast.new(text)
    t.set_timeout(timeout)
    return t


def size_with_pct(size: int, total: int) -> str:
    if not total:
        return fmt_bytes(size)
    return f"{fmt_bytes(size)} · {fmt_pct(size / total)}"
