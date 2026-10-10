"""Leitura de CPU, memória, discos, rede, temperaturas e processos.

Tudo vem de /proc e /sys: nenhuma dependência externa, nenhuma chamada a
binários do sistema no caminho rápido (atualização a cada 1,5 s).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

CLK_TCK = os.sysconf("SC_CLK_TCK") or 100
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") or 4096

# Sistemas de arquivos virtuais / de imagem que não representam espaço real.
_PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
    "securityfs", "pstore", "efivarfs", "bpf", "autofs", "hugetlbfs",
    "mqueue", "debugfs", "tracefs", "fusectl", "configfs", "ramfs",
    "binfmt_misc", "rpc_pipefs", "nsfs", "squashfs", "overlay", "fuse.portal",
    "fuse.gvfsd-fuse", "fuse.snapfuse", "nfsd", "selinuxfs", "resctrl",
}

_FS_LABELS = {
    "ext4": "ext4", "ext3": "ext3", "btrfs": "Btrfs", "xfs": "XFS",
    "vfat": "FAT32", "exfat": "exFAT", "ntfs3": "NTFS", "ntfs": "NTFS",
    "fuseblk": "NTFS/exFAT", "f2fs": "F2FS", "zfs": "ZFS", "iso9660": "CD/DVD",
}


def _read(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


# ---------------------------------------------------------------- CPU --------


@dataclass
class CpuSample:
    total: float = 0.0
    per_core: list[float] = field(default_factory=list)
    freq_mhz: float = 0.0


class CpuMonitor:
    def __init__(self) -> None:
        self._prev: dict[str, tuple[int, int]] = {}
        self.model = "Processador desconhecido"
        self.cores = os.cpu_count() or 1
        self.threads = self.cores
        self._read_model()

    def _read_model(self) -> None:
        text = _read("/proc/cpuinfo")
        ids = set()
        for line in text.splitlines():
            if line.startswith("model name") and self.model.startswith("Processador"):
                self.model = line.split(":", 1)[1].strip()
            elif line.startswith("core id"):
                ids.add(line.split(":", 1)[1].strip())
        self.threads = text.count("processor\t:") or (os.cpu_count() or 1)
        if ids:
            self.cores = len(ids)
        if self.model.startswith("Processador"):
            # ARM e alguns virtualizados não expõem "model name".
            for key in ("Model", "Hardware"):
                m = re.search(rf"^{key}\s*:\s*(.+)$", text, re.M)
                if m:
                    self.model = m.group(1).strip()
                    break

    def sample(self) -> CpuSample:
        out = CpuSample()
        cores: list[tuple[int, float]] = []
        for line in _read("/proc/stat").splitlines():
            if not line.startswith("cpu"):
                break
            parts = line.split()
            key = parts[0]
            try:
                vals = [int(v) for v in parts[1:11]]
            except ValueError:
                continue
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
            prev = self._prev.get(key)
            self._prev[key] = (total, idle)
            usage = 0.0
            if prev:
                dt, di = total - prev[0], idle - prev[1]
                if dt > 0:
                    usage = max(0.0, min(1.0, (dt - di) / dt))
            if key == "cpu":
                out.total = usage
            else:
                try:
                    cores.append((int(key[3:]), usage))
                except ValueError:
                    pass
        cores.sort()
        out.per_core = [u for _, u in cores]
        out.freq_mhz = self._freq()
        return out

    def _freq(self) -> float:
        vals = []
        base = "/sys/devices/system/cpu"
        try:
            for name in os.listdir(base):
                if not re.fullmatch(r"cpu\d+", name):
                    continue
                raw = _read(f"{base}/{name}/cpufreq/scaling_cur_freq").strip()
                if raw.isdigit():
                    vals.append(int(raw) / 1000.0)
        except OSError:
            pass
        if not vals:
            for line in _read("/proc/cpuinfo").splitlines():
                if line.startswith("cpu MHz"):
                    try:
                        vals.append(float(line.split(":", 1)[1]))
                    except ValueError:
                        pass
        return sum(vals) / len(vals) if vals else 0.0


def load_average() -> tuple[float, float, float]:
    try:
        return os.getloadavg()
    except OSError:
        return (0.0, 0.0, 0.0)


def uptime_seconds() -> float:
    raw = _read("/proc/uptime").split()
    try:
        return float(raw[0])
    except (IndexError, ValueError):
        return 0.0


def process_count() -> tuple[int, int]:
    """(processos, threads)"""
    procs = threads = 0
    try:
        for entry in os.scandir("/proc"):
            if entry.name.isdigit():
                procs += 1
                try:
                    threads += len(os.listdir(f"/proc/{entry.name}/task"))
                except OSError:
                    threads += 1
    except OSError:
        pass
    return procs, threads


# ------------------------------------------------------------- memória -------


@dataclass
class Memory:
    total: int = 0
    available: int = 0
    free: int = 0
    buffers: int = 0
    cached: int = 0
    shared: int = 0
    swap_total: int = 0
    swap_free: int = 0

    @property
    def used(self) -> int:
        return max(self.total - self.available, 0)

    @property
    def frac(self) -> float:
        return self.used / self.total if self.total else 0.0

    @property
    def cache_total(self) -> int:
        return self.buffers + self.cached

    @property
    def swap_used(self) -> int:
        return max(self.swap_total - self.swap_free, 0)

    @property
    def swap_frac(self) -> float:
        return self.swap_used / self.swap_total if self.swap_total else 0.0


def memory() -> Memory:
    m = Memory()
    keys = {
        "MemTotal": "total", "MemAvailable": "available", "MemFree": "free",
        "Buffers": "buffers", "Cached": "cached", "Shmem": "shared",
        "SwapTotal": "swap_total", "SwapFree": "swap_free",
    }
    for line in _read("/proc/meminfo").splitlines():
        name, _, rest = line.partition(":")
        attr = keys.get(name)
        if attr:
            try:
                setattr(m, attr, int(rest.split()[0]) * 1024)
            except (IndexError, ValueError):
                pass
    if not m.available:
        m.available = m.free + m.buffers + m.cached
    return m


# -------------------------------------------------------------- discos -------


@dataclass
class Disk:
    device: str
    mountpoint: str
    fstype: str
    total: int
    used: int
    free: int
    rotational: bool | None = None
    removable: bool = False
    label: str = ""
    model: str = ""

    @property
    def frac(self) -> float:
        return self.used / self.total if self.total else 0.0

    @property
    def fs_label(self) -> str:
        return _FS_LABELS.get(self.fstype, self.fstype)

    @property
    def kind(self) -> str:
        if self.removable:
            return "Removível"
        if self.rotational is False:
            return "SSD"
        if self.rotational is True:
            return "HD"
        return "Disco"

    @property
    def title(self) -> str:
        if self.mountpoint == "/":
            return "Sistema"
        if self.label:
            return self.label
        return os.path.basename(self.mountpoint.rstrip("/")) or self.mountpoint


def _block_name(device: str) -> str:
    base = os.path.basename(os.path.realpath(device))
    if re.fullmatch(r"nvme\d+n\d+p\d+", base):
        return re.sub(r"p\d+$", "", base)
    if re.fullmatch(r"mmcblk\d+p\d+", base):
        return re.sub(r"p\d+$", "", base)
    return re.sub(r"\d+$", "", base)


def _labels() -> dict[str, str]:
    out: dict[str, str] = {}
    d = "/dev/disk/by-label"
    try:
        for name in os.listdir(d):
            out[os.path.realpath(f"{d}/{name}")] = name.replace("\\x20", " ")
    except OSError:
        pass
    return out


def disks(include_all: bool = False) -> list[Disk]:
    labels = _labels()
    seen: set[tuple[int, int]] = set()
    result: list[Disk] = []
    for line in _read("/proc/self/mountinfo").splitlines():
        try:
            left, right = line.split(" - ", 1)
            lf = left.split()
            rf = right.split()
            mountpoint = lf[4].replace("\\040", " ").replace("\\011", "\t")
            fstype = rf[0]
            device = rf[1]
        except (ValueError, IndexError):
            continue
        if fstype in _PSEUDO_FS and not include_all:
            continue
        if not device.startswith("/dev/") and not include_all:
            continue
        if mountpoint.startswith(("/snap/", "/var/snap/", "/sys/", "/proc/", "/dev/")):
            continue
        try:
            st = os.stat(mountpoint)
            vfs = os.statvfs(mountpoint)
        except OSError:
            continue
        ident = (st.st_dev, 0)
        if ident in seen:
            continue
        if vfs.f_blocks == 0:
            continue
        seen.add(ident)
        total = vfs.f_blocks * vfs.f_frsize
        free = vfs.f_bavail * vfs.f_frsize
        used = (vfs.f_blocks - vfs.f_bfree) * vfs.f_frsize
        blk = _block_name(device)
        rot = _read(f"/sys/block/{blk}/queue/rotational").strip()
        rem = _read(f"/sys/block/{blk}/removable").strip() == "1"
        result.append(
            Disk(
                device=device,
                mountpoint=mountpoint,
                fstype=fstype,
                total=total,
                used=used,
                free=free,
                rotational={"0": False, "1": True}.get(rot),
                removable=rem,
                label=labels.get(os.path.realpath(device), ""),
                model=_read(f"/sys/block/{blk}/device/model").strip(),
            )
        )
    result.sort(key=lambda d: (d.mountpoint != "/", d.mountpoint))
    return result


def root_disk() -> Disk | None:
    for d in disks():
        if d.mountpoint == "/":
            return d
    return None


# --------------------------------------------------------------- rede --------


@dataclass
class NetSample:
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    rx_total: int = 0
    tx_total: int = 0
    interfaces: list[str] = field(default_factory=list)


class NetMonitor:
    def __init__(self) -> None:
        self._prev: tuple[float, int, int] | None = None

    def sample(self) -> NetSample:
        rx = tx = 0
        ifaces = []
        for line in _read("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            name = name.strip()
            if name in ("lo",) or name.startswith(("docker", "veth", "br-", "virbr")):
                continue
            parts = rest.split()
            if len(parts) < 9:
                continue
            try:
                r, t = int(parts[0]), int(parts[8])
            except ValueError:
                continue
            rx += r
            tx += t
            if r or t:
                ifaces.append(name)
        now = time.monotonic()
        out = NetSample(rx_total=rx, tx_total=tx, interfaces=ifaces)
        if self._prev:
            dt = now - self._prev[0]
            if dt > 0:
                out.rx_rate = max(0.0, (rx - self._prev[1]) / dt)
                out.tx_rate = max(0.0, (tx - self._prev[2]) / dt)
        self._prev = (now, rx, tx)
        return out


# ------------------------------------------------- temperatura e bateria -----

_TEMP_PRIORITY = ("k10temp", "coretemp", "zenpower", "cpu_thermal", "acpitz")


def temperatures() -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    base = "/sys/class/hwmon"
    try:
        hwmons = sorted(os.listdir(base))
    except OSError:
        hwmons = []
    for hw in hwmons:
        chip = _read(f"{base}/{hw}/name").strip() or hw
        try:
            files = sorted(os.listdir(f"{base}/{hw}"))
        except OSError:
            continue
        for f in files:
            m = re.fullmatch(r"temp(\d+)_input", f)
            if not m:
                continue
            raw = _read(f"{base}/{hw}/{f}").strip()
            if not raw.lstrip("-").isdigit():
                continue
            celsius = int(raw) / 1000.0
            if not (-40 < celsius < 150):
                continue
            label = _read(f"{base}/{hw}/temp{m.group(1)}_label").strip()
            name = f"{chip} · {label}" if label else chip
            out.append((name, celsius))
    out.sort(key=lambda t: (
        next((i for i, p in enumerate(_TEMP_PRIORITY) if t[0].startswith(p)), 99),
        t[0],
    ))
    return out


def cpu_temperature() -> float | None:
    for name, value in temperatures():
        if name.startswith(_TEMP_PRIORITY) or "Package" in name or "Tctl" in name:
            return value
    temps = temperatures()
    return temps[0][1] if temps else None


@dataclass
class Battery:
    percent: float
    status: str
    time_left: float | None = None


def battery() -> Battery | None:
    base = "/sys/class/power_supply"
    try:
        names = os.listdir(base)
    except OSError:
        return None
    for name in sorted(names):
        d = f"{base}/{name}"
        if _read(f"{d}/type").strip() != "Battery":
            continue
        cap = _read(f"{d}/capacity").strip()
        if not cap.isdigit():
            continue
        status = _read(f"{d}/status").strip() or "Desconhecido"
        pt = {
            "Charging": "Carregando", "Discharging": "Descarregando",
            "Full": "Carregada", "Not charging": "Não carregando",
        }.get(status, status)
        left = None
        try:
            now_u = int(_read(f"{d}/energy_now") or _read(f"{d}/charge_now"))
            rate = int(_read(f"{d}/power_now") or _read(f"{d}/current_now"))
            if rate > 0:
                left = now_u / rate * 3600
        except ValueError:
            pass
        return Battery(percent=int(cap) / 100.0, status=pt, time_left=left)
    return None


# ----------------------------------------------------------- processos -------


@dataclass
class Process:
    pid: int
    name: str
    cmdline: str
    cpu: float
    rss: int
    uid: int
    own: bool


class ProcessMonitor:
    """Uso de CPU por processo, calculado por delta entre amostras."""

    def __init__(self) -> None:
        self._prev: dict[int, tuple[int, int]] = {}
        self._prev_time = time.monotonic()
        self._uid = os.getuid()
        self._ncpu = os.cpu_count() or 1

    def sample(self, limit: int = 8) -> list[Process]:
        now = time.monotonic()
        elapsed = max(now - self._prev_time, 1e-6)
        self._prev_time = now
        current: dict[int, tuple[int, int]] = {}
        procs: list[Process] = []
        try:
            entries = os.listdir("/proc")
        except OSError:
            return []
        for name in entries:
            if not name.isdigit():
                continue
            pid = int(name)
            stat = _read(f"/proc/{pid}/stat")
            if not stat:
                continue
            try:
                open_p = stat.index("(")
                close_p = stat.rindex(")")
                comm = stat[open_p + 1:close_p]
                fields = stat[close_p + 2:].split()
                utime, stime = int(fields[11]), int(fields[12])
            except (ValueError, IndexError):
                continue
            ticks = utime + stime
            current[pid] = (ticks, 0)
            prev = self._prev.get(pid)
            cpu = 0.0
            if prev:
                delta = ticks - prev[0]
                cpu = max(0.0, delta / CLK_TCK / elapsed / self._ncpu)
            rss = 0
            statm = _read(f"/proc/{pid}/statm").split()
            if len(statm) > 1:
                try:
                    rss = int(statm[1]) * PAGE_SIZE
                except ValueError:
                    rss = 0
            try:
                uid = os.stat(f"/proc/{pid}").st_uid
            except OSError:
                uid = -1
            cmd = _read(f"/proc/{pid}/cmdline").replace("\x00", " ").strip()
            procs.append(
                Process(
                    pid=pid,
                    name=comm,
                    cmdline=cmd or comm,
                    cpu=cpu,
                    rss=rss,
                    uid=uid,
                    own=uid == self._uid,
                )
            )
        self._prev = current
        procs.sort(key=lambda p: (p.cpu, p.rss), reverse=True)
        return procs[:limit]

    def by_memory(self, procs: list[Process], limit: int = 8) -> list[Process]:
        return sorted(procs, key=lambda p: p.rss, reverse=True)[:limit]


# --------------------------------------------------------------- outros ------


def hostname() -> str:
    return _read("/proc/sys/kernel/hostname").strip() or os.uname().nodename


def kernel() -> str:
    return os.uname().release


def distro() -> str:
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        text = _read(path)
        m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.M)
        if m:
            return m.group(1)
    return "Linux"


def desktop() -> str:
    env = os.environ.get("XDG_CURRENT_DESKTOP", "")
    session = os.environ.get("XDG_SESSION_TYPE", "")
    parts = [p for p in (env.split(":")[0], session) if p]
    return " · ".join(parts) or "Desconhecido"
