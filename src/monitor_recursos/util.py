"""Formatação de números, datas e execução de comandos auxiliares."""

from __future__ import annotations

import os
import subprocess
import time

APP_ID = "io.github.emersonlimaa.MonitorRecursos"
APP_NAME = "Monitor de Recursos"
VERSION = "1.0.0"

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def fmt_bytes(n: float, decimals: int | None = None) -> str:
    """1536 -> '1,5 KB'. Usa base 1024 com rótulos curtos."""
    v = float(max(n or 0, 0))
    i = 0
    while v >= 1024 and i < len(_UNITS) - 1:
        v /= 1024.0
        i += 1
    if decimals is None:
        decimals = 0 if i == 0 or v >= 100 else 1
    return f"{v:.{decimals}f}".replace(".", ",") + f" {_UNITS[i]}"


def fmt_pct(frac: float, decimals: int = 0) -> str:
    return f"{frac * 100:.{decimals}f}".replace(".", ",") + "%"


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def fmt_duration(seconds: float) -> str:
    s = int(max(seconds, 0))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}min"
    if h:
        return f"{h}h {m}min"
    if m:
        return f"{m}min {s}s"
    return f"{s}s"


def fmt_elapsed(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.1f}".replace(".", ",") + " s"


def shorten_path(path: str, keep: int = 3) -> str:
    home = os.path.expanduser("~")
    p = path
    if p == home:
        return "Pasta pessoal"
    if p.startswith(home + os.sep):
        p = "~" + p[len(home):]
    parts = p.split(os.sep)
    if len(parts) > keep + 1:
        return os.sep.join(["…"] + parts[-keep:])
    return p


def run(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    """Executa um comando silenciosamente e devolve (código, saída)."""
    env = dict(os.environ)
    env.update(LC_ALL="C", LANG="C", DEBIAN_FRONTEND="noninteractive")
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def have(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


class Throttle:
    """Limita a frequência de chamadas (usado nos callbacks de progresso)."""

    def __init__(self, interval: float = 0.1) -> None:
        self.interval = interval
        self._last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self.interval:
            self._last = now
            return True
        return False
