"""Itens de limpeza: o que pode ser apagado sem perder nada importante."""

from __future__ import annotations

import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import packages as pk
from .scanner import BUILD_DIRS, CACHE_DIRS

HOME = os.path.expanduser("~")

USER = "usuario"
ROOT = "admin"


@dataclass
class CleanupItem:
    key: str
    title: str
    subtitle: str
    size: int
    icon: str = "user-trash-symbolic"
    scope: str = USER
    paths: list[str] = field(default_factory=list)
    helper_args: list[str] = field(default_factory=list)
    recommended: bool = True
    note: str = ""

    @property
    def needs_root(self) -> bool:
        return self.scope == ROOT


# ---------------------------------------------------------------- medição ----


def _subtree_size(path: str) -> int:
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            st = entry.stat(follow_symlinks=False)
                            total += st.st_blocks * 512 or st.st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def dir_size(path: str, workers: int = 8) -> int:
    """Tamanho de uma pasta, medindo as subpastas em paralelo.

    Ler metadados de disco é limitado por latência: várias threads mantêm a
    fila do SSD cheia e cortam o tempo de medição de pastas grandes como a
    ~/.cache.
    """
    if not os.path.isdir(path):
        return 0
    children: list[str] = []
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        children.append(entry.path)
                    else:
                        st = entry.stat(follow_symlinks=False)
                        total += st.st_blocks * 512 or st.st_size
                except OSError:
                    pass
    except OSError:
        return 0
    if not children:
        return total
    if len(children) == 1:
        return total + _subtree_size(children[0])
    with ThreadPoolExecutor(max_workers=min(workers, len(children))) as pool:
        total += sum(pool.map(_subtree_size, children))
    return total


def _app_caches() -> tuple[int, list[str]]:
    """Soma as subpastas de ~/.cache (menos miniaturas, medidas à parte)."""
    root = os.path.join(HOME, ".cache")
    paths: list[str] = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                if entry.name == "thumbnails":
                    continue
                if entry.is_dir(follow_symlinks=False):
                    paths.append(entry.path)
    except OSError:
        return 0, []
    if not paths:
        return 0, []
    with ThreadPoolExecutor(max_workers=min(8, len(paths))) as pool:
        sizes = list(pool.map(_subtree_size, paths))
    return sum(sizes), [p for p, s in zip(paths, sizes) if s > 0]


def _trash_paths() -> list[str]:
    base = os.path.join(HOME, ".local/share/Trash")
    return [os.path.join(base, sub) for sub in ("files", "info", "expunged")
            if os.path.isdir(os.path.join(base, sub))]


# ------------------------------------------------------------- descoberta ----


def _item_trash() -> CleanupItem | None:
    paths = _trash_paths()
    size = sum(dir_size(p) for p in paths)
    if size <= 0:
        return None
    return CleanupItem("lixeira", "Lixeira", "Arquivos que você já jogou fora",
                       size, "user-trash-full-symbolic", USER, paths)


def _items_caches() -> list[CleanupItem]:
    """Mede a ~/.cache inteira de uma vez e separa as miniaturas.

    A varredura paralela do analisador equilibra sozinha o caso comum de uma
    subpasta gigante (cache do navegador) ao lado de dezenas de pequenas.
    """
    from .scanner import scan

    root = os.path.join(HOME, ".cache")
    if not os.path.isdir(root):
        return []
    thumbs = os.path.join(root, "thumbnails")
    result = scan(root)
    thumb_size = result.dir_sizes.get(thumbs, 0)
    others: list[str] = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                if entry.name != "thumbnails" and entry.is_dir(follow_symlinks=False):
                    others.append(entry.path)
    except OSError:
        pass
    items = []
    if thumb_size > 1 << 20:
        items.append(CleanupItem(
            "miniaturas", "Miniaturas de imagens",
            "Pré-visualizações que o sistema recria quando precisar",
            thumb_size, "image-x-generic-symbolic", USER, [thumbs]))
    rest = max(result.size - thumb_size, 0)
    if rest > 8 << 20:
        items.append(CleanupItem(
            "cache-apps", "Caches de aplicativos",
            "Dados temporários de navegadores, editores e afins",
            rest, "folder-download-symbolic", USER, others, recommended=False,
            note="Os aplicativos recriam esses arquivos na próxima abertura."))
    return items


def _item_apt_cache() -> CleanupItem | None:
    size = pk.apt_cache_size()
    if size <= 1 << 20:
        return None
    return CleanupItem(
        "cache-apt", "Instaladores já usados",
        "Pacotes baixados que o sistema guardou após instalar",
        size, "package-x-generic-symbolic", ROOT,
        helper_args=["limpar-cache-apt"])


def _item_autoremove() -> CleanupItem | None:
    plan = pk.autoremovable()
    if not plan.removed:
        return None
    return CleanupItem(
        "autoremove", "Dependências sem uso",
        f"{len(plan.removed)} pacotes que nenhum programa usa mais",
        plan.freed, "application-x-addon-symbolic", ROOT,
        helper_args=["autoremover"])


def _item_snaps() -> CleanupItem | None:
    count, size = pk.snap_old_revisions()
    if not count:
        return None
    return CleanupItem(
        "snaps-antigos", "Versões antigas de aplicativos Snap",
        f"{count} versões guardadas para desfazer atualizações",
        size, "application-x-executable-symbolic", ROOT,
        helper_args=["limpar-snaps-antigos"])


def _item_journal() -> CleanupItem | None:
    size = dir_size("/var/log/journal")
    if size <= 200 << 20:
        return None
    return CleanupItem(
        "registros", "Registros antigos do sistema",
        "Histórico de eventos; mantém os 200 MB mais recentes",
        size - (200 << 20), "text-x-generic-symbolic", ROOT,
        helper_args=["limpar-registros"], recommended=False)


def _item_crash() -> CleanupItem | None:
    size = dir_size("/var/crash")
    if size <= 1 << 20:
        return None
    return CleanupItem(
        "falhas", "Relatórios de falhas",
        "Despejos de programas que travaram", size, "dialog-warning-symbolic",
        ROOT, paths=["/var/crash"], helper_args=["limpar-pastas", "/var/crash"])


def _item_flatpak() -> CleanupItem | None:
    if not os.path.isdir("/var/lib/flatpak"):
        return None
    return CleanupItem(
        "flatpak-nao-usado", "Bibliotecas Flatpak sem uso",
        "Plataformas que nenhum aplicativo instalado usa",
        0, "application-x-firmware-symbolic", ROOT,
        helper_args=["limpar-flatpak"], recommended=False,
        note="O tamanho só é conhecido durante a limpeza.")


USER_JOBS = (_item_trash, _items_caches)
ROOT_JOBS = (_item_apt_cache, _item_autoremove, _item_snaps, _item_journal,
             _item_crash, _item_flatpak)


def discover_async(on_item, on_done, include_root: bool = True) -> None:
    """Mede tudo em paralelo e entrega cada item assim que ele fica pronto.

    Medir a ~/.cache pode levar dezenas de segundos na primeira vez; os
    outros itens aparecem na tela em um ou dois segundos.
    """
    jobs = list(USER_JOBS) + (list(ROOT_JOBS) if include_root else [])
    remaining = [len(jobs)]
    lock = threading.Lock()

    def run(job):
        try:
            result = job()
        except Exception:
            result = None
        if result:
            for item in (result if isinstance(result, list) else [result]):
                on_item(item)
        with lock:
            remaining[0] -= 1
            if remaining[0] == 0:
                on_done()

    for job in jobs:
        threading.Thread(target=run, args=(job,), daemon=True).start()


def discover(include_root: bool = True) -> list[CleanupItem]:
    """Versão que espera tudo ficar pronto (usada em testes)."""
    items: list[CleanupItem] = []
    done = threading.Event()
    lock = threading.Lock()

    def collect(item):
        with lock:
            items.append(item)

    discover_async(collect, done.set, include_root)
    done.wait(timeout=300)
    items.sort(key=lambda i: i.size, reverse=True)
    return items


# ------------------------------------------------------------- execução ------


def is_safe_cache_path(path: str) -> tuple[bool, str]:
    """Confere se uma pasta pode ser apagada sem risco."""
    real = os.path.realpath(path)
    home = os.path.realpath(HOME)
    if real in ("/", home) or not real.startswith(home + os.sep):
        return False, "Fora da sua pasta pessoal."
    if os.path.islink(path):
        return False, "É um atalho, não uma pasta."
    if not os.path.isdir(real):
        return False, "A pasta não existe mais."
    name = os.path.basename(real)
    allowed_prefixes = (
        os.path.join(home, ".cache"),
        os.path.join(home, ".local/share/Trash"),
        os.path.join(home, ".npm"),
        os.path.join(home, ".gradle"),
        os.path.join(home, ".m2"),
    )
    if name in CACHE_DIRS or name in BUILD_DIRS:
        return True, ""
    if real.startswith(allowed_prefixes):
        return True, ""
    return False, "Não parece uma pasta de cache."


def clear_paths(paths: list[str], on_progress=None, cancel: threading.Event | None = None
                ) -> tuple[int, list[str]]:
    """Apaga o conteúdo das pastas indicadas. Devolve (bytes liberados, erros)."""
    freed = 0
    errors: list[str] = []
    total = max(len(paths), 1)
    for index, path in enumerate(paths, 1):
        if cancel and cancel.is_set():
            break
        ok, reason = is_safe_cache_path(path)
        if not ok:
            errors.append(f"{os.path.basename(path)}: {reason}")
            continue
        if on_progress:
            on_progress(index / total, os.path.basename(path))
        before = dir_size(path)
        removed_all = True
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            shutil.rmtree(entry.path, ignore_errors=True)
                        else:
                            os.unlink(entry.path)
                    except OSError:
                        removed_all = False
        except OSError as exc:
            errors.append(f"{os.path.basename(path)}: {exc.strerror}")
            continue
        after = dir_size(path)
        freed += max(before - after, 0)
        if not removed_all:
            errors.append(f"{os.path.basename(path)}: alguns arquivos estavam em uso.")
    return freed, errors


def delete_tree(path: str) -> tuple[int, str]:
    """Apaga uma pasta inteira de cache (ex.: node_modules de um projeto)."""
    ok, reason = is_safe_cache_path(path)
    if not ok:
        return 0, reason
    size = dir_size(path)
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return 0, exc.strerror or "Não foi possível apagar."
    return size, ""
