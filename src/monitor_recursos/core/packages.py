"""Inventário e remoção de aplicativos: apt (dpkg), Snap e Flatpak.

A interface nunca mostra comandos: esta camada traduz tudo para nomes,
tamanhos e listas de itens. A remoção em si é delegada ao auxiliar
privilegiado (ver `privileged.py`), que repete todas as validações de
segurança por conta própria.
"""

from __future__ import annotations

import fnmatch
import os
import re
import threading
from dataclasses import dataclass, field

from ..util import run

APT = "apt"
SNAP = "snap"
FLATPAK = "flatpak"
MANUAL = "manual"

KIND_LABEL = {
    APT: "Pacote do sistema",
    SNAP: "Snap",
    FLATPAK: "Flatpak",
    MANUAL: "Atalho local",
}

# ------------------------------------------------------- itens protegidos ----

# Remover qualquer um destes quebra o sistema, a área de trabalho ou este
# próprio aplicativo. A regra vale para o pacote pedido e para tudo que
# aparecer na simulação de remoção.
PROTECTED_PATTERNS = (
    "apt", "apt-utils", "dpkg", "debconf", "base-files", "base-passwd",
    "bash", "dash", "coreutils", "util-linux", "login", "passwd", "sudo",
    "libc6", "libc-bin", "libgcc-s1", "libstdc++6", "zlib1g", "tar", "gzip",
    "systemd*", "udev", "dbus*", "polkitd", "pkexec", "policykit-1",
    "python3", "python3-minimal", "python3.*", "libpython3*",
    "python3-gi", "python3-gi-cairo", "gir1.2-gtk-4.0", "gir1.2-adw-1",
    "gir1.2-glib-2.0", "libgtk-4-*", "libadwaita-1-*", "libglib2.0-*",
    "monitor-recursos",
    "linux-image*", "linux-headers*", "linux-modules*", "linux-generic*",
    "linux-firmware", "grub*", "shim*", "initramfs-tools*", "zstd",
    "ubuntu-desktop*", "ubuntu-session", "ubuntu-minimal", "ubuntu-standard",
    "ubuntu-advantage-tools", "ubuntu-pro-client*", "ubuntu-release-upgrader*",
    "gnome-shell*", "gnome-session*", "gdm3", "lightdm", "sddm", "mutter*",
    "xwayland", "xserver-xorg*", "xorg", "wayland*", "libwayland*",
    "mesa-*", "libgl1*", "libegl1*", "libdrm*", "nvidia-*", "libnvidia-*",
    "network-manager*", "netplan.io", "systemd-resolved", "resolvconf",
    "openssh-server", "ca-certificates", "gpg*", "gnupg*",
    "snapd", "flatpak", "fwupd*", "plymouth*", "cryptsetup*", "lvm2", "mdadm",
    "e2fsprogs", "dosfstools", "ntfs-3g", "fuse3", "libfuse3-*",
    "perl*", "gcc-*", "libatk*", "libcairo*", "libpango*", "libgdk-pixbuf*",
)

# Blocos de base do Snap: remover derruba todos os snaps de uma vez.
PROTECTED_SNAPS = ("snapd", "core", "core1?", "core2?", "bare")

# Plataformas compartilhadas: dá para remover, mas outros apps podem depender.
SHARED_SNAP_PATTERNS = (
    "gnome-*", "kf5-*", "kf6-*", "mesa-*", "ffmpeg-*", "gtk-common-themes",
    "*-2204", "*-2404", "wine-platform*", "qt*-core*", "cups",
    "snapd-desktop-integration", "firmware-updater", "desktop-security-center",
    "prompting-client", "bare",
)


def _matches(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


def is_protected_apt(name: str) -> bool:
    base = name.split(":", 1)[0]
    return any(fnmatch.fnmatchcase(base, pat) for pat in PROTECTED_PATTERNS)


# ------------------------------------------------------------ estruturas -----


@dataclass
class App:
    """Um item removível mostrado na interface."""

    ident: str                  # nome do pacote / snap / ref do flatpak
    kind: str                   # APT, SNAP, FLATPAK, MANUAL
    name: str                   # nome de exibição
    summary: str = ""
    version: str = ""
    size: int = 0
    icon: str = ""
    desktop_file: str = ""
    section: str = ""
    priority: str = ""
    essential: bool = False
    is_app: bool = False        # tem atalho no menu de aplicativos
    is_system: bool = False     # componente de base (não oferecer remoção)
    shared: bool = False        # plataforma usada por outros apps (avisar)
    extra: str = ""             # observação curta (ex.: "3 revisões")

    @property
    def protected(self) -> bool:
        if self.is_system:
            return True
        if self.kind == APT:
            return self.essential or self.priority in ("required", "important") \
                or is_protected_apt(self.ident)
        if self.kind == SNAP:
            return _matches(self.ident, PROTECTED_SNAPS)
        return False

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def sort_name(self) -> str:
        return self.name.casefold()


@dataclass
class RemovalPlan:
    """O que vai sair do disco, em linguagem de gente."""

    targets: list[App] = field(default_factory=list)
    removed: list[tuple[str, int]] = field(default_factory=list)
    freed: int = 0
    blocked: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.blocked and not self.error and bool(self.removed or self.targets)

    @property
    def extra_count(self) -> int:
        names = {t.ident.split(":")[0] for t in self.targets}
        return len([n for n, _ in self.removed if n not in names])


# --------------------------------------------------- atalhos .desktop --------

_DESKTOP_DIRS = [
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
    os.path.expanduser("~/.local/share/applications"),
]

_LOCALES = ("pt_BR", "pt")


@dataclass
class DesktopEntry:
    path: str
    name: str
    comment: str = ""
    icon: str = ""
    no_display: bool = False
    categories: str = ""
    snap_name: str = ""
    flatpak_ref: str = ""


def _parse_desktop(path: str) -> DesktopEntry | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    in_main = False
    data: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            if in_main:
                break
            in_main = line == "[Desktop Entry]"
            continue
        if not in_main or "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip()

    def localized(key: str) -> str:
        for loc in _LOCALES:
            v = data.get(f"{key}[{loc}]")
            if v:
                return v
        return data.get(key, "")

    name = localized("Name")
    if not name:
        return None
    return DesktopEntry(
        path=path,
        name=name,
        comment=localized("Comment") or localized("GenericName"),
        icon=data.get("Icon", ""),
        no_display=data.get("NoDisplay", "").lower() == "true"
        or data.get("Hidden", "").lower() == "true",
        categories=data.get("Categories", ""),
        snap_name=data.get("X-SnapInstanceName", ""),
        flatpak_ref=data.get("X-Flatpak", ""),
    )


def desktop_entries() -> dict[str, DesktopEntry]:
    out: dict[str, DesktopEntry] = {}
    for d in _DESKTOP_DIRS:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fname in names:
            if not fname.endswith(".desktop"):
                continue
            entry = _parse_desktop(os.path.join(d, fname))
            if entry:
                out[os.path.join(d, fname)] = entry
    return out


def _dpkg_desktop_index() -> dict[str, str]:
    """Mapa atalho -> pacote dpkg, lido direto da base do dpkg (rápido)."""
    idx: dict[str, str] = {}
    base = "/var/lib/dpkg/info"
    needles = (b"/share/applications/",)
    try:
        entries = os.scandir(base)
    except OSError:
        return idx
    with entries as it:
        for e in it:
            if not e.name.endswith(".list"):
                continue
            try:
                with open(e.path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if not any(n in data for n in needles):
                continue
            pkg = e.name[:-5].split(":", 1)[0]
            for line in data.split(b"\n"):
                if line.endswith(b".desktop") and b"/share/applications/" in line:
                    idx.setdefault(line.decode("utf-8", "replace"), pkg)
    return idx


# ----------------------------------------------------------------- apt -------


def apt_packages() -> dict[str, App]:
    fmt = ("${binary:Package}|${db:Status-Status}|${Installed-Size}|"
           "${Essential}|${Priority}|${Section}|${binary:Summary}\n")
    rc, out = run(["dpkg-query", "-W", f"-f={fmt}"])
    apps: dict[str, App] = {}
    if rc not in (0, 1):
        return apps
    for line in out.splitlines():
        parts = line.split("|", 6)
        if len(parts) < 7:
            continue
        name, status, size, essential, priority, section, summary = parts
        if status != "installed":
            continue
        try:
            size_bytes = int(size) * 1024 if size.strip() else 0
        except ValueError:
            size_bytes = 0
        base = name.split(":", 1)[0]
        apps[base] = App(
            ident=name, kind=APT, name=base, summary=summary.strip(),
            size=size_bytes, essential=essential.strip() == "yes",
            priority=priority.strip(), section=section.strip(),
        )
    return apps


def apt_version(pkg: str) -> str:
    rc, out = run(["dpkg-query", "-W", "-f=${Version}", pkg])
    return out.strip() if rc == 0 else ""


def simulate_apt_removal(targets: list[App], purge: bool = True,
                         autoremove: bool = True) -> RemovalPlan:
    """Pergunta ao apt o que sairia, sem mudar nada no sistema."""
    plan = RemovalPlan(targets=list(targets))
    names = [t.ident for t in targets]
    if not names:
        plan.error = "Nada selecionado."
        return plan
    for t in targets:
        if t.protected:
            plan.blocked.append(t.name)
    if plan.blocked:
        return plan
    argv = ["apt-get", "-s", "remove"]
    if purge:
        argv.append("--purge")
    if autoremove:
        argv.append("--auto-remove")
    argv += names
    rc, out = run(argv, timeout=120)
    inventory = apt_packages()
    removed: list[tuple[str, int]] = []
    for line in out.splitlines():
        m = re.match(r"^(Remv|Purg)\s+(\S+)", line)
        if m:
            pkg = m.group(2).split(":", 1)[0]
            size = inventory.get(pkg).size if pkg in inventory else 0
            removed.append((pkg, size))
    if rc != 0 and not removed:
        msg = ""
        for line in out.splitlines():
            if line.startswith("E:"):
                msg = line[2:].strip()
                break
        plan.error = msg or "Não foi possível calcular a remoção."
        return plan
    for pkg, _ in removed:
        if is_protected_apt(pkg):
            plan.blocked.append(pkg)
        else:
            info = inventory.get(pkg)
            if info and (info.essential or info.priority in ("required", "important")):
                plan.blocked.append(pkg)
    plan.removed = removed
    plan.freed = sum(s for _, s in removed)
    return plan


def autoremovable() -> RemovalPlan:
    """Pacotes que o sistema instalou como dependência e ninguém usa mais."""
    plan = RemovalPlan()
    rc, out = run(["apt-get", "-s", "autoremove", "--purge"], timeout=120)
    inventory = apt_packages()
    for line in out.splitlines():
        m = re.match(r"^(Remv|Purg)\s+(\S+)", line)
        if m:
            pkg = m.group(2).split(":", 1)[0]
            info = inventory.get(pkg)
            plan.removed.append((pkg, info.size if info else 0))
    plan.freed = sum(s for _, s in plan.removed)
    return plan


def apt_cache_size() -> int:
    total = 0
    for d in ("/var/cache/apt/archives", "/var/cache/apt/archives/partial"):
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name.endswith((".deb", ".ddeb")):
                        try:
                            total += e.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass
    return total


# ---------------------------------------------------------------- snap -------


def snap_packages() -> dict[str, App]:
    apps: dict[str, App] = {}
    rc, out = run(["snap", "list", "--all"], timeout=30)
    if rc != 0:
        return apps
    sizes: dict[str, int] = {}
    revs: dict[str, int] = {}
    try:
        with os.scandir("/var/lib/snapd/snaps") as it:
            for e in it:
                if not e.name.endswith(".snap"):
                    continue
                stem = e.name[:-5]
                name = stem.rsplit("_", 1)[0]
                try:
                    sizes[name] = sizes.get(name, 0) + e.stat().st_size
                except OSError:
                    pass
                revs[name] = revs.get(name, 0) + 1
    except OSError:
        pass
    lines = out.splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        name, version = parts[0], parts[1]
        notes = parts[5] if len(parts) > 5 else ""
        if "disabled" in notes and name in apps:
            continue
        is_base = _matches(name, PROTECTED_SNAPS) or \
            any(tag in notes for tag in ("base", "snapd"))
        is_shared = not is_base and (
            _matches(name, SHARED_SNAP_PATTERNS) or "disabled" in notes
        )
        extra = ""
        n = revs.get(name, 1)
        if n > 1:
            extra = f"{n} versões guardadas"
        apps[name] = App(
            ident=name, kind=SNAP, name=name, version=version,
            size=sizes.get(name, 0), is_system=is_base, shared=is_shared,
            extra=extra,
            summary="Componente de base do Snap" if is_base
            else "Plataforma compartilhada entre aplicativos" if is_shared else "",
        )
    for name, app in apps.items():
        if not app.summary and not app.is_system and not app.shared:
            app.summary = "Aplicativo instalado via Snap"
    return apps


def snap_old_revisions() -> tuple[int, int]:
    """(quantidade, bytes) de revisões antigas desativadas."""
    rc, out = run(["snap", "list", "--all"], timeout=30)
    if rc != 0:
        return 0, 0
    count = 0
    total = 0
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6 or "disabled" not in parts[-1]:
            continue
        name, rev = parts[0], parts[2]
        count += 1
        try:
            total += os.stat(f"/var/lib/snapd/snaps/{name}_{rev}.snap").st_size
        except OSError:
            pass
    return count, total


# ------------------------------------------------------------- flatpak -------

_SIZE_RE = re.compile(r"([\d.,]+)\s*([kKMGTt]?i?[Bb])")
_MULT = {"b": 1, "kb": 1000, "kib": 1024, "mb": 1000**2, "mib": 1024**2,
         "gb": 1000**3, "gib": 1024**3, "tb": 1000**4, "tib": 1024**4}


def _parse_size(text: str) -> int:
    m = _SIZE_RE.search(text.strip())
    if not m:
        return 0
    value = m.group(1).replace(".", "").replace(",", ".") \
        if m.group(1).count(",") == 1 and m.group(1).count(".") >= 1 \
        else m.group(1).replace(",", ".")
    try:
        num = float(value)
    except ValueError:
        return 0
    return int(num * _MULT.get(m.group(2).lower(), 1))


def flatpak_packages() -> dict[str, App]:
    apps: dict[str, App] = {}
    rc, out = run(["flatpak", "list", "--app", "--columns=application,name,version,size,origin"],
                  timeout=30)
    if rc != 0:
        return apps
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        ident, name, version, size = parts[0], parts[1], parts[2], parts[3]
        origin = parts[4] if len(parts) > 4 else ""
        apps[ident] = App(
            ident=ident, kind=FLATPAK, name=name or ident, version=version,
            size=_parse_size(size), is_app=True,
            summary=f"Flatpak de {origin}" if origin else "Aplicativo Flatpak",
        )
    return apps


def flatpak_unused() -> tuple[int, int]:
    rc, out = run(["flatpak", "list", "--runtime", "--columns=application,size"],
                  timeout=30)
    if rc != 0:
        return 0, 0
    count = total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            count += 1
            total += _parse_size(parts[1])
    return count, total


# ------------------------------------------------------------ inventário -----


@dataclass
class Inventory:
    apps: list[App] = field(default_factory=list)       # com atalho no menu
    packages: list[App] = field(default_factory=list)   # todos os pacotes apt
    snaps: list[App] = field(default_factory=list)
    flatpaks: list[App] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(a.size for a in self.packages) + \
            sum(a.size for a in self.snaps) + sum(a.size for a in self.flatpaks)

    def all_items(self) -> list[App]:
        return self.apps + self.packages + self.snaps + self.flatpaks


def _icon_for(entry: DesktopEntry) -> str:
    icon = entry.icon
    if not icon:
        return "application-x-executable-symbolic"
    return icon


def build_inventory() -> Inventory:
    """Monta a lista de aplicativos visíveis e de pacotes do sistema."""
    inv = Inventory()
    results: dict[str, object] = {}

    def collect(key, fn):
        try:
            results[key] = fn()
        except Exception:
            results[key] = {}

    jobs = [("apt", apt_packages), ("snap", snap_packages),
            ("flatpak", flatpak_packages), ("entries", desktop_entries),
            ("idx", _dpkg_desktop_index)]
    threads = [threading.Thread(target=collect, args=j, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    apt = results.get("apt") or {}
    snaps = results.get("snap") or {}
    flatpaks = results.get("flatpak") or {}
    entries = results.get("entries") or {}
    dpkg_idx = results.get("idx") or {}

    claimed_apt: dict[str, App] = {}
    for path, entry in entries.items():
        if entry.no_display:
            continue
        if "/flatpak/exports/" in path:
            ref = entry.flatpak_ref.split("/")[1] if entry.flatpak_ref.count("/") >= 1 \
                else os.path.basename(path)[:-8]
            app = flatpaks.get(ref) or flatpaks.get(os.path.basename(path)[:-8])
            if app:
                app.name = entry.name
                app.summary = entry.comment or app.summary
                app.icon = _icon_for(entry)
                app.desktop_file = path
                app.is_app = True
            continue
        if path.startswith("/var/lib/snapd/desktop/applications/"):
            sname = entry.snap_name or os.path.basename(path)[:-8].split("_", 1)[0]
            app = snaps.get(sname)
            if app and not app.is_system and not app.shared:
                if not app.is_app:
                    app.name = entry.name
                    app.summary = entry.comment or app.summary
                    app.icon = _icon_for(entry)
                    app.desktop_file = path
                    app.is_app = True
            continue
        pkg = dpkg_idx.get(path)
        if pkg and pkg in apt:
            base = apt[pkg]
            if base.ident in claimed_apt:
                continue
            app = App(
                ident=base.ident, kind=APT, name=entry.name,
                summary=entry.comment or base.summary, size=base.size,
                icon=_icon_for(entry), desktop_file=path, section=base.section,
                priority=base.priority, essential=base.essential, is_app=True,
                version=base.version,
            )
            claimed_apt[base.ident] = app
            continue
        if path.startswith(os.path.expanduser("~/.local/share/applications")):
            inv.apps.append(App(
                ident=path, kind=MANUAL, name=entry.name,
                summary=entry.comment or "Atalho criado manualmente",
                icon=_icon_for(entry), desktop_file=path, is_app=True,
                size=os.path.getsize(path) if os.path.exists(path) else 0,
            ))

    inv.apps = sorted(
        list(claimed_apt.values())
        + [a for a in snaps.values() if a.is_app]
        + [a for a in flatpaks.values() if a.is_app]
        + inv.apps,
        key=lambda a: a.sort_name,
    )
    inv.packages = sorted(apt.values(), key=lambda a: a.sort_name)
    inv.snaps = sorted(snaps.values(), key=lambda a: a.sort_name)
    inv.flatpaks = sorted(flatpaks.values(), key=lambda a: a.sort_name)
    return inv
