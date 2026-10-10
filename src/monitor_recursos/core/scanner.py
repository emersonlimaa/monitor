"""Varredura de disco: descobre o que de fato ocupa espaço.

Os tamanhos são o espaço realmente alocado no disco (blocos), igual ao que
o `du` informa: arquivos esparsos como imagens de emulador declaram um
tamanho muito maior do que de fato ocupam.

Classifica cada arquivo em categorias de uso real (documentos, filmes, música,
projetos de programação, jogos, caches…), mede o tamanho de cada pasta para
navegação tipo "mapa do disco", identifica raízes de projeto e aponta projetos
duplicados e caches que podem ser liberados.
"""

from __future__ import annotations

import heapq
import json
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# --------------------------------------------------------------- categorias --

DOCUMENTOS = "documentos"
IMAGENS = "imagens"
VIDEOS = "videos"
AUDIO = "audio"
PROJETOS = "projetos"
CODIGO = "codigo"
PACOTES = "pacotes"
VMS = "vms"
JOGOS = "jogos"
APPDATA = "appdata"
SISTEMA = "sistema"
CACHE = "cache"
OUTROS = "outros"

# (chave, rótulo, ícone simbólico, descrição, cor)
CATEGORIES: list[tuple[str, str, str, str, str]] = [
    (VIDEOS, "Vídeos e filmes", "video-x-generic-symbolic",
     "Filmes, séries, gravações e vídeos em geral", "#c4576b"),
    (PROJETOS, "Projetos de programação", "folder-code-symbolic",
     "Pastas com repositório Git ou arquivos de projeto", "#4a7fc1"),
    (JOGOS, "Jogos", "applications-games-symbolic",
     "Steam, Lutris, Heroic, Wine e afins", "#8a63b8"),
    (IMAGENS, "Imagens e fotos", "image-x-generic-symbolic",
     "Fotos, capturas de tela, artes e imagens brutas", "#3f9e8c"),
    (AUDIO, "Música e áudio", "audio-x-generic-symbolic",
     "Músicas, podcasts, gravações e projetos de áudio", "#d08a3e"),
    (DOCUMENTOS, "Documentos", "x-office-document-symbolic",
     "PDFs, textos, planilhas, apresentações e e-books", "#5a8f3d"),
    (PACOTES, "Pacotes e instaladores", "package-x-generic-symbolic",
     "Arquivos compactados, .deb, AppImage, ISOs", "#b5852f"),
    (VMS, "Máquinas virtuais", "drive-harddisk-symbolic",
     "Discos virtuais do VirtualBox, QEMU, VMware e WSL", "#7a7f99"),
    (CODIGO, "Código solto", "text-x-script-symbolic",
     "Arquivos de código fora de uma pasta de projeto", "#4f9dd4"),
    (APPDATA, "Dados de aplicativos", "application-x-executable-symbolic",
     "Configurações e dados em .config, .local e Flatpak", "#9b8e5f"),
    (SISTEMA, "Sistema e programas", "computer-symbolic",
     "Arquivos do Linux e dos programas instalados", "#6f7c8a"),
    (CACHE, "Caches e builds", "user-trash-symbolic",
     "node_modules, builds, miniaturas e lixeira: recuperável", "#9a5f8a"),
    (OUTROS, "Outros arquivos", "application-x-generic-symbolic",
     "Sem categoria reconhecida", "#8b8b8b"),
]

CATEGORY_INFO = {key: (label, icon, desc, color)
                 for key, label, icon, desc, color in CATEGORIES}
CATEGORY_ORDER = [key for key, *_ in CATEGORIES]


def category_label(key: str) -> str:
    return CATEGORY_INFO.get(key, ("Outros arquivos",))[0]


def category_icon(key: str) -> str:
    info = CATEGORY_INFO.get(key)
    return info[1] if info else "application-x-generic-symbolic"


def category_color(key: str) -> str:
    info = CATEGORY_INFO.get(key)
    return info[3] if info else "#8b8b8b"


def _ext_map() -> dict[str, str]:
    groups = {
        DOCUMENTOS: """pdf doc docx odt rtf txt md markdown tex epub mobi azw azw3 djvu
            xls xlsx ods csv tsv ppt pptx odp pages numbers key abw wpd fb2 chm
            odg vsdx mpp one onepkg""",
        IMAGENS: """jpg jpeg png gif webp bmp tiff tif heic heif avif ico svg psd xcf
            ai cdr eps raw cr2 cr3 nef arw dng orf rw2 raf pef srw kra sketch fig
            jfif""",
        VIDEOS: """mp4 mkv avi mov wmv flv webm m4v mpg mpeg ts m2ts mts 3gp vob ogv
            rmvb asf divx f4v mxf srt sub ass vtt prproj veg kdenlive""",
        AUDIO: """mp3 flac wav ogg oga m4a aac opus wma aiff aif alac ape mid midi amr
            dsf dff mpc ra als flp logicx rpp aup aup3 band sesx""",
        CODIGO: """py pyi ipynb js mjs cjs jsx ts tsx vue svelte c h cc cpp cxx hpp hh
            cs java kt kts scala go rs rb php pl pm lua r jl swift m mm dart ex exs
            erl hs clj cljs sh bash zsh fish ps1 bat sql html htm css scss sass less
            json json5 yaml yml toml ini cfg conf xml gradle cmake mk makefile
            dockerfile vim el lisp scm f90 pas asm s v sv vhdl tf hcl proto graphql
            prisma astro twig ejs hbs pug""",
        PACOTES: """zip tar gz tgz bz2 tbz xz txz zst 7z rar lz lz4 lzma arj cab
            deb rpm appimage snap flatpak flatpakref pkg dmg exe msi msix apk xapk
            jar war ear iso nrg mdf cue bin cpio ipk ova""",
        VMS: """qcow qcow2 vdi vmdk vhd vhdx vmem vmsd vmsn vmss nvram hdd img raw
            vdhx qed sparsebundle wsl ext4vhdx""",
    }
    out: dict[str, str] = {}
    for key, blob in groups.items():
        for ext in blob.split():
            out[ext] = key
    return out


EXT_MAP = _ext_map()

# ------------------------------------------------- detecção de contexto ------

# Pastas descartáveis: pesam muito e podem ser reconstruídas.
CACHE_DIRS = {
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".venv", "venv", ".virtualenvs", "site-packages",
    ".gradle", ".m2", ".ivy2", ".sbt", ".cargo-cache", ".nuget", ".dart_tool",
    ".next", ".nuxt", ".svelte-kit", ".angular", ".turbo", ".parcel-cache",
    ".cache", "Cache", "CachedData", "Code Cache", "GPUCache", "ShaderCache",
    "DawnCache", "GrShaderCache", "thumbnails", "Trash", ".Trash-1000",
    ".npm", ".yarn", ".pnpm-store", ".bun", ".deno", ".gem", ".rustup",
    ".ccache", ".sccache", ".cmake", "CMakeFiles", ".terraform", ".serverless",
    "Pods", "DerivedData", ".ipynb_checkpoints", ".ollama-cache",
}
# Nomes de build só contam como cache dentro de um projeto reconhecido.
BUILD_DIRS = {"build", "dist", "target", "out", "bin", "obj", ".output", "_build"}

GAME_DIRS = {
    "steamapps", "Steam", ".steam", "SteamLibrary", "lutris", ".wine", "heroic",
    ".minecraft", "Minecraft", "EpicGames", "GOG Games", "PrismLauncher",
    "multimc", "retroarch", "RetroArch", "ROMs", "roms", "Games", "Jogos",
    "jogos", "com.valvesoftware.Steam", "Proton", "compatdata", "ppsspp",
    "dolphin-emu", "pcsx2", "rpcs3", "yuzu", "ryujinx",
}
GAME_PREFIXES = ("wineprefix", "WINEPREFIX", "Prefix")

APPDATA_DIRS = {
    ".config", ".local", ".var", "snap", ".mozilla", ".thunderbird", ".java",
    ".librewolf", ".pki", ".gnupg", "flatpak",
}

SYSTEM_PREFIXES = (
    "/usr", "/var", "/opt", "/boot", "/lib", "/lib64", "/bin", "/sbin",
    "/etc", "/srv", "/snap", "/swapfile", "/swap.img",
)

NEVER_ENTER = {
    "/proc", "/sys", "/dev", "/run", "/snap", "/sys/fs/cgroup",
    "/var/lib/docker/overlay2", "/var/lib/containers/storage/overlay",
}

# marcador -> tipo de projeto
PROJECT_MARKERS: dict[str, str] = {
    ".git": "Git", ".hg": "Mercurial", ".svn": "Subversion",
    "package.json": "Node.js", "pnpm-lock.yaml": "Node.js",
    "pyproject.toml": "Python", "setup.py": "Python", "Pipfile": "Python",
    "requirements.txt": "Python", "environment.yml": "Conda",
    "Cargo.toml": "Rust", "go.mod": "Go", "pom.xml": "Java (Maven)",
    "build.gradle": "Gradle", "build.gradle.kts": "Gradle",
    "composer.json": "PHP", "Gemfile": "Ruby", "mix.exs": "Elixir",
    "pubspec.yaml": "Flutter/Dart", "CMakeLists.txt": "C/C++ (CMake)",
    "Makefile": "Make", "meson.build": "Meson", "deno.json": "Deno",
    "flake.nix": "Nix", "Dockerfile": "Docker", "docker-compose.yml": "Docker",
    "compose.yaml": "Docker", "Package.swift": "Swift", "project.clj": "Clojure",
    "stack.yaml": "Haskell", "dune-project": "OCaml", "Chart.yaml": "Helm",
    "angular.json": "Angular", "nuxt.config.ts": "Nuxt", "next.config.js": "Next.js",
    "vite.config.ts": "Vite", "vite.config.js": "Vite", "tauri.conf.json": "Tauri",
    "gradlew": "Android/Gradle", "local.properties": "Android",
    ".terraform.lock.hcl": "Terraform", "manage.py": "Django",
    "artisan": "Laravel", "unity": "Unity", "Assembly-CSharp.csproj": "Unity",
}
# Marcadores fracos: só valem se a pasta tiver mais de um deles.
WEAK_MARKERS = frozenset({
    "requirements.txt", "Makefile", "Dockerfile", "docker-compose.yml",
    "compose.yaml", "local.properties", "gradlew",
})
ALL_MARKERS = frozenset(PROJECT_MARKERS)

MAX_EXPAND_DEPTH = 6
TOP_FILES_PER_CATEGORY = 15
TOP_FILES_GLOBAL = 80
MIN_DIR_SIZE = 1 << 20          # 1 MB: menor que isso não entra no mapa
MAX_DIR_DEPTH = 10


# ------------------------------------------------------------- resultados ----


@dataclass
class CatStat:
    key: str
    size: int = 0
    files: int = 0
    top: list[tuple[int, str]] = field(default_factory=list)   # heap (size, path)

    @property
    def label(self) -> str:
        return category_label(self.key)

    def biggest(self, limit: int = TOP_FILES_PER_CATEGORY) -> list[tuple[str, int]]:
        return [(p, s) for s, p in sorted(self.top, reverse=True)[:limit]]


@dataclass
class ProjectInfo:
    path: str
    kind: str
    size: int = 0
    cache_size: int = 0
    files: int = 0
    mtime: float = 0.0

    @property
    def name(self) -> str:
        return os.path.basename(self.path) or self.path

    @property
    def clean_size(self) -> int:
        return max(self.size - self.cache_size, 0)


@dataclass
class CacheInfo:
    path: str
    size: int
    kind: str
    files: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path) or self.path


@dataclass
class ScanResult:
    root: str
    size: int = 0
    files: int = 0
    dirs: int = 0
    errors: int = 0
    elapsed: float = 0.0
    partial: bool = False
    as_root: bool = False
    categories: dict[str, CatStat] = field(default_factory=dict)
    dir_sizes: dict[str, int] = field(default_factory=dict)
    projects: list[ProjectInfo] = field(default_factory=list)
    caches: list[CacheInfo] = field(default_factory=list)
    big_files: list[tuple[str, int, str]] = field(default_factory=list)

    # ---------------------------------------------------------- consultas ---

    def ordered_categories(self) -> list[CatStat]:
        stats = [c for c in self.categories.values() if c.size > 0]
        stats.sort(key=lambda c: c.size, reverse=True)
        return stats

    def children(self, path: str, limit: int = 400) -> list[tuple[str, int]]:
        """Subpastas diretas de `path` com tamanho conhecido, maiores primeiro."""
        prefix = path.rstrip("/") + "/"
        out = []
        for p, size in self.dir_sizes.items():
            if p.startswith(prefix) and "/" not in p[len(prefix):]:
                out.append((p, size))
        out.sort(key=lambda t: t[1], reverse=True)
        return out[:limit]

    def size_of(self, path: str) -> int:
        return self.dir_sizes.get(path.rstrip("/") or "/", 0)

    def duplicate_projects(self) -> list[tuple[str, list[ProjectInfo]]]:
        """Projetos com o mesmo nome em lugares diferentes (cópias esquecidas)."""
        groups: dict[str, list[ProjectInfo]] = {}
        for p in self.projects:
            groups.setdefault(p.name.lower(), []).append(p)
        dups = [(name, sorted(items, key=lambda i: i.size, reverse=True))
                for name, items in groups.items() if len(items) > 1]
        dups.sort(key=lambda t: sum(i.size for i in t[1]), reverse=True)
        return dups

    def reclaimable(self) -> int:
        return sum(c.size for c in self.caches)

    # -------------------------------------------------- (de)serialização ----

    def to_json(self) -> str:
        return json.dumps({
            "root": self.root, "size": self.size, "files": self.files,
            "dirs": self.dirs, "errors": self.errors, "elapsed": self.elapsed,
            "partial": self.partial, "as_root": self.as_root,
            "categories": {
                k: {"size": c.size, "files": c.files,
                    "top": [[s, p] for s, p in sorted(c.top, reverse=True)[:TOP_FILES_PER_CATEGORY]]}
                for k, c in self.categories.items()
            },
            "dir_sizes": {p: s for p, s in self.dir_sizes.items() if s >= 4 << 20},
            "projects": [[p.path, p.kind, p.size, p.cache_size, p.files, p.mtime]
                         for p in self.projects],
            "caches": [[c.path, c.size, c.kind, c.files] for c in self.caches],
            "big_files": [[p, s, c] for p, s, c in self.big_files],
        })

    @classmethod
    def from_json(cls, blob: str) -> "ScanResult":
        d = json.loads(blob)
        res = cls(root=d["root"], size=d["size"], files=d["files"], dirs=d["dirs"],
                  errors=d["errors"], elapsed=d["elapsed"], partial=d["partial"],
                  as_root=d.get("as_root", False))
        for k, c in d["categories"].items():
            res.categories[k] = CatStat(key=k, size=c["size"], files=c["files"],
                                        top=[(s, p) for s, p in c["top"]])
        res.dir_sizes = dict(d["dir_sizes"])
        res.projects = [ProjectInfo(path=p, kind=k, size=s, cache_size=cs,
                                    files=f, mtime=m)
                        for p, k, s, cs, f, m in d["projects"]]
        res.caches = [CacheInfo(path=p, size=s, kind=k, files=f)
                      for p, s, k, f in d["caches"]]
        res.big_files = [(p, s, c) for p, s, c in d["big_files"]]
        return res


# ---------------------------------------------------------------- varredura --


class _Node:
    """Pasta aberta na fase inicial: guarda o que precisa para somar depois."""

    __slots__ = ("path", "ctx", "depth", "parent", "children", "own", "total",
                 "project", "cache_root", "name")

    def __init__(self, path: str, ctx: int, depth: int, parent) -> None:
        self.path = path
        self.ctx = ctx
        self.depth = depth
        self.parent = parent
        self.children: list["_Node"] = []
        self.own = 0
        self.total = 0
        self.project: ProjectInfo | None = None
        self.cache_root = False
        self.name = os.path.basename(path.rstrip("/")) or path


class _Acc:
    """Acumulador local de uma thread (sem travas no laço quente)."""

    __slots__ = ("files", "dirs", "errors", "categories", "dir_sizes",
                 "projects", "caches", "heap")

    def __init__(self) -> None:
        self.files = 0
        self.dirs = 0
        self.errors = 0
        self.categories = {k: CatStat(key=k) for k in CATEGORY_ORDER}
        self.dir_sizes: dict[str, int] = {}
        self.projects: list[ProjectInfo] = []
        self.caches: list[CacheInfo] = []
        self.heap: list[tuple[int, str, str]] = []


class Scanner:
    """Varredura recursiva com progresso e cancelamento.

    A varredura de metadados é limitada pela latência do disco, não pela CPU:
    com o cache do sistema frio, uma única thread fica em fila 1 e rende ~6 mil
    arquivos por segundo. Por isso a raiz é aberta até formar uma frente de
    subárvores independentes, varridas em paralelo — cada thread percorre a sua
    de forma recursiva e serial, sem disputa, e o disco trabalha com fila cheia.
    """

    def __init__(
        self,
        root: str,
        *,
        same_filesystem: bool = True,
        follow_hidden: bool = True,
        cancel: threading.Event | None = None,
        progress: callable | None = None,
        progress_every: int = 4096,
    ) -> None:
        self.root = os.path.realpath(root)
        self.same_filesystem = same_filesystem
        self.follow_hidden = follow_hidden
        self.cancel = cancel or threading.Event()
        self.progress = progress
        self.progress_every = progress_every
        self.result = ScanResult(root=self.root)
        for key in CATEGORY_ORDER:
            self.result.categories[key] = CatStat(key=key)
        self._root_dev: int | None = None
        self._blocked: set[str] = set(NEVER_ENTER)
        self._inode_lock = threading.Lock()
        self._accs: list[_Acc] = []
        self.workers = _worker_count(self.root)
        self._seen_inodes: set[tuple[int, int]] = set()
        self._current = self.root
        self._home = os.path.expanduser("~")

    # ------------------------------------------------------------ helpers --

    def _classify(self, name: str, ctx: int) -> str:
        """ctx: bitmask com o contexto herdado da pasta."""
        if ctx & _CACHE:
            return CACHE
        if ctx & _GAME:
            return JOGOS
        if ctx & _PROJECT:
            return PROJETOS
        if ctx & _SYSTEM:
            return SISTEMA
        dot = name.rfind(".")
        if dot > 0:
            cat = EXT_MAP.get(name[dot + 1:].lower())
            if cat:
                return cat
        elif name.lower() in ("makefile", "dockerfile", "readme", "license"):
            return CODIGO if name.lower() in ("makefile", "dockerfile") else DOCUMENTOS
        if ctx & _APPDATA:
            return APPDATA
        return OUTROS

    def _detect_project(self, hits: set[str]) -> str | None:
        strong = [PROJECT_MARKERS[m] for m in hits if m not in WEAK_MARKERS]
        if strong:
            return strong[0]
        if len(hits) >= 2:
            return PROJECT_MARKERS[next(iter(hits))]
        return None

    @staticmethod
    def _push_big(acc: _Acc, size: int, path: str, cat: str) -> None:
        heap = acc.heap
        if len(heap) < TOP_FILES_GLOBAL:
            heapq.heappush(heap, (size, path, cat))
        elif size > heap[0][0]:
            heapq.heapreplace(heap, (size, path, cat))
        stat = acc.categories[cat]
        if len(stat.top) < TOP_FILES_PER_CATEGORY:
            heapq.heappush(stat.top, (size, path))
        elif size > stat.top[0][0]:
            heapq.heapreplace(stat.top, (size, path))

    # --------------------------------------------------------------- walk --

    def run(self) -> ScanResult:
        started = time.monotonic()
        res = self.result
        try:
            st = os.stat(self.root)
            self._root_dev = st.st_dev
            if self.same_filesystem:
                self._blocked |= _foreign_mounts(st.st_dev)
        except OSError:
            res.partial = True
            res.errors = 1
            return res

        main = _Acc()
        self._accs.append(main)
        root_node = _Node(self.root, self._initial_ctx(self.root), 0, None)

        # 1) Abre os primeiros níveis para formar uma frente de subárvores.
        opened: list[_Node] = []
        pending: deque[_Node] = deque([root_node])
        target = self.workers * 12
        while (pending and len(pending) < target
               and pending[0].depth < MAX_EXPAND_DEPTH
               and not self.cancel.is_set()):
            node = pending.popleft()
            self._open(node, main)
            opened.append(node)
            pending.extend(node.children)

        frontier = list(pending)

        # 2) Varre cada subárvore em paralelo; cada thread tem seu acumulador.
        reporter = None
        stop = threading.Event()
        if self.progress:
            reporter = threading.Thread(target=self._report, args=(stop,), daemon=True)
            reporter.start()
        try:
            if frontier:
                tls = threading.local()

                def job(node: "_Node") -> None:
                    acc = getattr(tls, "acc", None)
                    if acc is None:
                        acc = _Acc()
                        tls.acc = acc
                        with self._inode_lock:
                            self._accs.append(acc)
                    node.total = self._walk(node.path, node.ctx, node.depth, acc)

                if len(frontier) == 1 or self.workers == 1:
                    for node in frontier:
                        job(node)
                else:
                    with ThreadPoolExecutor(
                        max_workers=min(self.workers, len(frontier)),
                        thread_name_prefix="varredura",
                    ) as pool:
                        list(pool.map(job, frontier))
        finally:
            stop.set()
            if reporter:
                reporter.join(timeout=1.0)

        # 3) Soma de baixo para cima: cada pasta aberta recebe o total dos filhos.
        for node in reversed(opened):
            node.total = node.own + sum(c.total for c in node.children)

        # 4) Junta os acumuladores das threads no resultado final.
        heap: list[tuple[int, str, str]] = []
        for acc in self._accs:
            res.files += acc.files
            res.dirs += acc.dirs
            res.errors += acc.errors
            for key, cat in acc.categories.items():
                target_cat = res.categories[key]
                target_cat.size += cat.size
                target_cat.files += cat.files
                for item in cat.top:
                    if len(target_cat.top) < TOP_FILES_PER_CATEGORY:
                        heapq.heappush(target_cat.top, item)
                    elif item[0] > target_cat.top[0][0]:
                        heapq.heapreplace(target_cat.top, item)
            res.dir_sizes.update(acc.dir_sizes)
            res.projects.extend(acc.projects)
            res.caches.extend(acc.caches)
            for item in acc.heap:
                if len(heap) < TOP_FILES_GLOBAL:
                    heapq.heappush(heap, item)
                elif item[0] > heap[0][0]:
                    heapq.heapreplace(heap, item)

        for node in opened + frontier:
            if node.total >= MIN_DIR_SIZE and node.depth <= MAX_DIR_DEPTH:
                res.dir_sizes[node.path] = node.total
            if node.cache_root and node.total >= 48 << 20:
                res.caches.append(CacheInfo(path=node.path, size=node.total,
                                            kind=_cache_kind(node.name)))
        for node in opened:
            if node.project is not None:
                node.project.size = node.total
                # Pastas de cache relevantes dentro do projeto, pelo caminho.
                node.project.cache_size = sum(
                    c.size for c in res.caches
                    if c.path.startswith(node.path + os.sep))
                if node.total >= 1 << 20:
                    res.projects.append(node.project)

        res.size = root_node.total
        res.dir_sizes[self.root] = root_node.total
        res.big_files = [(p, sz, c) for sz, p, c in sorted(heap, reverse=True)]
        res.projects.sort(key=lambda p: p.size, reverse=True)
        res.caches.sort(key=lambda c: c.size, reverse=True)
        del res.caches[200:]
        del res.projects[500:]
        res.elapsed = time.monotonic() - started
        if self.cancel.is_set():
            res.partial = True
        if self.progress:
            self.progress(res.files, res.size, self.root)
        return res

    def _report(self, stop: threading.Event) -> None:
        """Informa o progresso somando os acumuladores (sem travas)."""
        while not stop.wait(0.15):
            files = 0
            size = 0
            for acc in list(self._accs):
                files += acc.files
                for cat in acc.categories.values():
                    size += cat.size
            try:
                self.progress(files, size, self._current)
            except Exception:
                return

    def _open(self, node: "_Node", acc: _Acc) -> None:
        """Processa os arquivos de uma pasta e cria os nós das subpastas."""
        try:
            with os.scandir(node.path) as it:
                entries = list(it)
        except OSError:
            acc.errors += 1
            return
        ctx = node.ctx
        names = {e.name for e in entries}
        if not (ctx & (_PROJECT | _CACHE)):
            hits = names & ALL_MARKERS
            if hits:
                kind = self._detect_project(hits)
                if kind:
                    ctx |= _PROJECT
                    node.ctx = ctx
                    node.project = ProjectInfo(path=node.path, kind=kind)
        if ctx & _CACHE:
            forced = CACHE
        elif ctx & _GAME:
            forced = JOGOS
        elif ctx & _PROJECT:
            forced = PROJETOS
        elif ctx & _SYSTEM:
            forced = SISTEMA
        else:
            forced = None
        fallback = APPDATA if ctx & _APPDATA else OUTROS
        cats = acc.categories
        ext_get = EXT_MAP.get
        own = 0
        nfiles = 0
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    name = entry.name
                    if not self.follow_hidden and name[0] == ".":
                        continue
                    if entry.path in self._blocked:
                        continue
                    child_ctx = ctx
                    cache_root = False
                    if name in CACHE_DIRS or (ctx & _PROJECT and name in BUILD_DIRS):
                        child_ctx |= _CACHE
                        cache_root = not (ctx & _CACHE)
                    elif name in GAME_DIRS or name.startswith(GAME_PREFIXES):
                        child_ctx |= _GAME
                    elif name in APPDATA_DIRS:
                        child_ctx |= _APPDATA
                    child = _Node(entry.path, child_ctx, node.depth + 1, node)
                    child.cache_root = cache_root
                    node.children.append(child)
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                acc.errors += 1
                continue
            size = st.st_blocks * 512 or st.st_size
            if st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                with self._inode_lock:
                    if key in self._seen_inodes:
                        continue
                    self._seen_inodes.add(key)
            if forced is not None:
                cat = forced
            else:
                name = entry.name
                dot = name.rfind(".")
                cat = ext_get(name[dot + 1:].lower(), fallback) if dot > 0 else fallback
            own += size
            nfiles += 1
            stat_entry = cats[cat]
            stat_entry.size += size
            stat_entry.files += 1
            if size >= 256 << 10:
                self._push_big(acc, size, entry.path, cat)
        node.own = own
        acc.files += nfiles
        acc.dirs += 1
        if node.project is not None:
            node.project.files = nfiles
            try:
                node.project.mtime = os.stat(node.path).st_mtime
            except OSError:
                pass

    def _initial_ctx(self, path: str) -> int:
        ctx = 0
        clean = path.rstrip("/") or "/"
        if any(clean == pre or clean.startswith(pre + "/") for pre in SYSTEM_PREFIXES):
            ctx |= _SYSTEM
        parts = clean.split("/")
        if any(part in GAME_DIRS for part in parts):
            ctx |= _GAME
        if any(part in APPDATA_DIRS for part in parts):
            ctx |= _APPDATA
        if parts[-1] in CACHE_DIRS:
            ctx |= _CACHE
        return ctx

    def _walk(self, path: str, ctx: int, depth: int, acc: _Acc) -> int:
        """Devolve o tamanho total da subárvore e alimenta o resultado.

        Escrito com variáveis locais no laço quente: a varredura chega a
        centenas de milhares de arquivos por segundo com o cache do SO quente.
        """
        cancelled = self.cancel.is_set
        if cancelled():
            return 0
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            acc.errors += 1
            return 0

        names = {e.name for e in entries}
        project_kind = None
        if not (ctx & (_PROJECT | _CACHE)):
            hits = names & ALL_MARKERS
            if hits:
                project_kind = self._detect_project(hits)
                if project_kind:
                    ctx |= _PROJECT

        project = ProjectInfo(path=path, kind=project_kind) if project_kind else None

        # Dentro de um contexto conhecido todos os arquivos têm a mesma
        # categoria: resolvemos uma vez por pasta em vez de por arquivo.
        if ctx & _CACHE:
            forced = CACHE
        elif ctx & _GAME:
            forced = JOGOS
        elif ctx & _PROJECT:
            forced = PROJETOS
        elif ctx & _SYSTEM:
            forced = SISTEMA
        else:
            forced = None
        fallback = APPDATA if ctx & _APPDATA else OUTROS

        cats = acc.categories
        ext_get = EXT_MAP.get
        blocked = self._blocked
        push_big = self._push_big
        inodes = self._seen_inodes
        inode_lock = self._inode_lock
        in_project = ctx & _PROJECT
        hidden_ok = self.follow_hidden
        total = 0
        nfiles = 0
        nerrors = 0
        subdirs = []
        big_limit = 256 << 10

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    name = entry.name
                    if not hidden_ok and name[0] == ".":
                        continue
                    child = entry.path
                    if child in blocked:
                        continue
                    child_ctx = ctx
                    if name in CACHE_DIRS or (in_project and name in BUILD_DIRS):
                        child_ctx |= _CACHE
                    elif name in GAME_DIRS or name.startswith(GAME_PREFIXES):
                        child_ctx |= _GAME
                    elif name in APPDATA_DIRS:
                        child_ctx |= _APPDATA
                    subdirs.append((child, name, child_ctx))
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                nerrors += 1
                continue

            size = st.st_blocks * 512 or st.st_size
            if st.st_nlink > 1:
                # Arquivos com vínculo duplo são raros: só aqui há trava.
                key = (st.st_dev, st.st_ino)
                with inode_lock:
                    if key in inodes:
                        continue
                    inodes.add(key)
            if forced is not None:
                cat = forced
            else:
                name = entry.name
                dot = name.rfind(".")
                cat = ext_get(name[dot + 1:].lower(), fallback) if dot > 0 else fallback
            total += size
            nfiles += 1
            stat_entry = cats[cat]
            stat_entry.size += size
            stat_entry.files += 1
            if size >= big_limit:
                push_big(acc, size, entry.path, cat)

        acc.files += nfiles
        acc.dirs += 1
        acc.errors += nerrors
        self._current = path
        if project is not None:
            project.files += nfiles

        cache_stat = cats[CACHE]
        for child, name, child_ctx in subdirs:
            if cancelled():
                break
            before_cache = cache_stat.size
            sub = self._walk(child, child_ctx, depth + 1, acc)
            total += sub
            if project is not None:
                project.cache_size += cache_stat.size - before_cache
            # Pastas descartáveis grandes entram na lista de limpeza.
            if (child_ctx & _CACHE) and not (ctx & _CACHE) and sub >= 48 << 20:
                acc.caches.append(CacheInfo(path=child, size=sub, kind=_cache_kind(name)))

        if total >= MIN_DIR_SIZE and depth <= MAX_DIR_DEPTH:
            acc.dir_sizes[path] = total

        if project is not None:
            project.size = total
            try:
                project.mtime = os.stat(path).st_mtime
            except OSError:
                pass
            if total >= 1 << 20:
                acc.projects.append(project)

        return total


_CACHE = 1
_PROJECT = 2
_GAME = 4
_APPDATA = 8
_SYSTEM = 16


def _worker_count(root: str) -> int:
    """Quantas threads de varredura usar.

    Em SSD/NVMe o ganho vem de manter a fila do disco cheia; em disco mecânico
    muitas threads só fazem a cabeça de leitura pular de um lado para o outro.
    """
    cpus = os.cpu_count() or 4
    try:
        from . import sysinfo

        dev = os.stat(root).st_dev
        for disk in sysinfo.disks():
            if os.stat(disk.mountpoint).st_dev == dev:
                if disk.rotational:
                    return 2
                if disk.fstype.startswith("fuse"):
                    return 4
                break
    except OSError:
        pass
    return max(2, min(12, cpus))


def _foreign_mounts(root_dev: int) -> set[str]:
    """Pontos de montagem que pertencem a outro disco (não devem ser varridos)."""
    out: set[str] = set()
    try:
        with open("/proc/self/mountinfo", "rb") as fh:
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return out
    for line in lines:
        fields = line.split(" ")
        if len(fields) < 5:
            continue
        mp = fields[4].replace("\\040", " ").replace("\\011", "\t")
        if mp == "/":
            continue
        try:
            if os.stat(mp).st_dev != root_dev:
                out.add(mp)
        except OSError:
            out.add(mp)
    return out


def _cache_kind(name: str) -> str:
    table = {
        "node_modules": "Dependências do Node.js",
        ".venv": "Ambiente virtual Python", "venv": "Ambiente virtual Python",
        "__pycache__": "Bytecode Python", "target": "Build do Rust/Java",
        "build": "Arquivos de build", "dist": "Arquivos de build",
        ".gradle": "Cache do Gradle", ".m2": "Cache do Maven",
        ".cache": "Cache de aplicativos", "Cache": "Cache de aplicativos",
        ".next": "Build do Next.js", "Trash": "Lixeira",
        ".Trash-1000": "Lixeira", "thumbnails": "Miniaturas de imagens",
        ".npm": "Cache do npm", ".yarn": "Cache do Yarn",
        ".pnpm-store": "Cache do pnpm", ".rustup": "Toolchains do Rust",
        ".dart_tool": "Cache do Dart/Flutter", "DerivedData": "Build do Xcode",
        ".ccache": "Cache de compilação C/C++",
    }
    return table.get(name, "Cache ou build")


def scan(root: str, **kwargs) -> ScanResult:
    return Scanner(root, **kwargs).run()


# ---------------------------------------------------------- alvos sugeridos --


@dataclass
class ScanTarget:
    path: str
    title: str
    subtitle: str
    icon: str
    needs_root: bool = False


def suggested_targets() -> list[ScanTarget]:
    from . import sysinfo

    home = os.path.expanduser("~")
    targets = [
        ScanTarget(home, "Pasta pessoal", "Seus arquivos, projetos e downloads",
                   "user-home-symbolic"),
    ]
    for d in sysinfo.disks():
        if d.total < 512 << 20:
            continue
        if d.mountpoint == "/":
            targets.append(ScanTarget(
                "/", "Todo o sistema",
                f"{d.device} · inclui programas e arquivos do Linux",
                "drive-harddisk-system-symbolic", needs_root=True))
        else:
            targets.append(ScanTarget(
                d.mountpoint, d.title,
                f"{d.device} · {d.fs_label} · {d.kind}",
                "drive-removable-media-symbolic" if d.removable
                else "drive-harddisk-symbolic"))
    for name in ("Downloads", "Documentos", "Documents", "Vídeos", "Videos",
                 "Imagens", "Pictures", "Música", "Music"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            targets.append(ScanTarget(p, name, "Pasta do usuário", "folder-symbolic"))
    return targets
