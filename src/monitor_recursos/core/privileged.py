"""Ponte com o auxiliar privilegiado.

A interface nunca monta linhas de comando: ela pede uma operação pelo nome
e recebe de volta frases em português, progresso e erros. A autenticação
é feita pelo pkexec, com a mensagem definida na política do polkit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass

HELPER_NAME = "monitor-recursos-helper"
_INSTALLED = [
    f"/usr/libexec/monitor-recursos/{HELPER_NAME}",
    f"/usr/lib/monitor-recursos/{HELPER_NAME}",
    f"/usr/local/libexec/monitor-recursos/{HELPER_NAME}",
]


def helper_path() -> str | None:
    for path in _INSTALLED:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    # Execução a partir do código-fonte.
    here = os.path.dirname(os.path.abspath(__file__))
    dev = os.path.normpath(os.path.join(here, "..", "..", "..", "helper", HELPER_NAME))
    if os.path.isfile(dev) and os.access(dev, os.X_OK):
        return dev
    return None


def available() -> tuple[bool, str]:
    if helper_path() is None:
        return False, "O componente de administração não está instalado."
    if os.geteuid() != 0 and shutil.which("pkexec") is None:
        return False, "O sistema não tem o pkexec para pedir sua senha."
    return True, ""


@dataclass
class OperationResult:
    code: int = 0
    payload: str = ""
    error: str = ""
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.error and not self.cancelled


class Operation:
    """Executa uma operação privilegiada em segundo plano.

    Os retornos chegam pelas funções passadas no construtor, chamadas
    a partir da thread de trabalho (a interface reenvia para o laço GTK).
    """

    def __init__(
        self,
        args: list[str],
        *,
        on_status=None,
        on_progress=None,
        on_result=None,
        on_done=None,
    ) -> None:
        self.args = args
        self.on_status = on_status
        self.on_progress = on_progress
        self.on_result = on_result
        self.on_done = on_done
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except OSError:
                    pass

    # ------------------------------------------------------------ interno --

    def _argv(self) -> list[str] | None:
        helper = helper_path()
        if helper is None:
            return None
        if os.geteuid() == 0:
            return [helper, *self.args]
        return ["pkexec", helper, *self.args]

    def _run(self) -> None:
        result = OperationResult()
        argv = self._argv()
        if argv is None:
            result.error = "O componente de administração não está instalado."
            result.code = 1
            self._finish(result)
            return
        env = dict(os.environ)
        env.update(LC_ALL="C", LANG="C")
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                errors="replace", env=env,
            )
        except OSError:
            result.error = "Não foi possível iniciar a operação."
            result.code = 1
            self._finish(result)
            return
        with self._lock:
            self._proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            kind, _, text = line.rstrip("\n").partition("|")
            if kind == "S" and self.on_status:
                self.on_status(text)
            elif kind == "P" and self.on_progress:
                try:
                    self.on_progress(float(text))
                except ValueError:
                    pass
            elif kind == "E":
                result.error = text
            elif kind == "R":
                result.payload = text
                if self.on_result:
                    self.on_result(text)
            elif kind == "F":
                try:
                    result.code = int(text)
                except ValueError:
                    result.code = 1
        code = proc.wait()
        if code in (126, 127) and not result.error:
            result.cancelled = True
            result.code = code
        elif code != 0 and result.code == 0:
            result.code = code
            if not result.error:
                result.error = "A operação não foi concluída."
        self._finish(result)

    def _finish(self, result: OperationResult) -> None:
        if self.on_done:
            self.on_done(result)
