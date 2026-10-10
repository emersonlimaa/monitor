"""Inventário das peças do computador.

Lê tudo de /sys, /proc e das bases de nomes que o próprio sistema traz
(pci.ids, usb.ids, pnp.ids), sem depender de lspci, lsusb ou de senha.
Os pentes de memória são a única peça que exige administrador: só o
dmidecode enxerga os slots, e isso é pedido sob demanda.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

from ..util import fmt_bytes, fmt_duration, run
from . import sysinfo


@dataclass
class Row:
    title: str
    subtitle: str = ""
    value: str = ""
    icon: str = ""
    details: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Section:
    key: str
    title: str
    icon: str
    rows: list[Row] = field(default_factory=list)
    note: str = ""


def _read(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


# ------------------------------------------------- bases de nomes do sistema -

_IDS_DIRS = ("/usr/share/misc", "/usr/share/hwdata")


def _ids_path(name: str) -> str | None:
    for d in _IDS_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def _lookup_ids(filename: str, wanted: set[tuple[str, str]]
                ) -> dict[tuple[str, str], tuple[str, str]]:
    """Resolve (vendor, device) -> (nome do fabricante, nome do produto).

    Uma passada só no arquivo, procurando apenas os pares pedidos.
    """
    path = _ids_path(filename)
    out: dict[tuple[str, str], tuple[str, str]] = {}
    if not path or not wanted:
        return out
    vendors = {v for v, _ in wanted}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            vendor_id = ""
            vendor_name = ""
            interessa = False
            for line in fh:
                if not line or line.startswith("#"):
                    continue
                if not line.startswith("\t"):
                    vendor_id = line[:4].lower()
                    interessa = vendor_id in vendors
                    if interessa:
                        vendor_name = line[4:].strip()
                    continue
                if not interessa or line.startswith("\t\t"):
                    continue
                dev_id = line[1:5].lower()
                key = (vendor_id, dev_id)
                if key in wanted:
                    out[key] = (vendor_name, line[5:].strip())
    except OSError:
        pass
    for key in wanted:
        if key not in out:
            out[key] = ("", "")
    return out


def _pnp_vendors() -> dict[str, str]:
    path = _ids_path("pnp.ids")
    out: dict[str, str] = {}
    if not path:
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#") or "\t" not in line:
                    continue
                code, _, name = line.partition("\t")
                code = code.strip()
                if len(code) == 3:
                    out[code.upper()] = name.strip()
    except OSError:
        pass
    return out


# ------------------------------------------------------------ computador ----

_CHASSIS = {
    "3": "Computador de mesa", "4": "Desktop baixo", "5": "Gabinete pizza",
    "6": "Mini torre", "7": "Torre", "8": "Portátil", "9": "Notebook",
    "10": "Subnotebook", "11": "Portátil de mão", "13": "All-in-one",
    "14": "Subnotebook", "15": "Desktop compacto", "16": "Notebook robusto",
    "17": "Servidor em rack", "23": "Servidor em rack", "30": "Tablet",
    "31": "Conversível", "32": "Destacável",
}


_MARCAS = {
    "micro-star international co., ltd.": "MSI",
    "asustek computer inc.": "ASUS",
    "gigabyte technology co., ltd.": "Gigabyte",
    "american megatrends international, llc.": "AMI",
    "american megatrends inc.": "AMI",
    "asrock": "ASRock",
    "hewlett-packard": "HP",
    "dell inc.": "Dell",
    "lenovo": "Lenovo",
    "intel corporation": "Intel",
    "advanced micro devices, inc.": "AMD",
    "nvidia corporation": "NVIDIA",
    "realtek semiconductor co., ltd.": "Realtek",
}


def marca(valor: str) -> str:
    """Encurta razões sociais: 'Micro-Star International Co., Ltd.' -> 'MSI'."""
    limpo = valor.strip()
    curto = _MARCAS.get(limpo.lower())
    if curto:
        return curto
    for sufixo in (", Inc.", " Inc.", " Co., Ltd.", " Corporation", " Corp.",
                   ", LLC.", " LLC", " Ltd.", " GmbH", " S.A."):
        if limpo.endswith(sufixo):
            return limpo[:-len(sufixo)].strip()
    return limpo


def _limpo(valor: str) -> str:
    """Descarta os preenchimentos bobos que os fabricantes deixam na BIOS."""
    lixo = {"default string", "to be filled by o.e.m.", "system manufacturer",
            "system product name", "none", "n/a", "unknown", "not specified",
            "to be filled by oem", "", "o.e.m."}
    return "" if valor.strip().lower() in lixo else valor.strip()


def machine_section() -> Section:
    dmi = "/sys/class/dmi/id"
    fabricante = marca(_limpo(_read(f"{dmi}/sys_vendor")))
    modelo = _limpo(_read(f"{dmi}/product_name"))
    placa_fab = marca(_limpo(_read(f"{dmi}/board_vendor")))
    placa = _limpo(_read(f"{dmi}/board_name"))
    bios_v = marca(_limpo(_read(f"{dmi}/bios_vendor")))
    bios_ver = _limpo(_read(f"{dmi}/bios_version"))
    bios_data = _limpo(_read(f"{dmi}/bios_date"))
    chassi = _CHASSIS.get(_read(f"{dmi}/chassis_type"), "")
    firmware = "UEFI" if os.path.isdir("/sys/firmware/efi") else "BIOS legado"
    secure = _read("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c")

    section = Section("maquina", "Computador", "computer-symbolic")
    if fabricante or modelo:
        section.rows.append(Row(
            title=" ".join(x for x in (fabricante, modelo) if x) or "Computador",
            subtitle=chassi or "Sistema",
            icon="computer-symbolic"))
    if placa:
        section.rows.append(Row(
            title=placa, subtitle=f"Placa-mãe{' · ' + placa_fab if placa_fab else ''}",
            icon="application-x-firmware-symbolic"))
    if bios_ver:
        detalhe = " · ".join(x for x in (bios_v, bios_data) if x)
        section.rows.append(Row(
            title=f"Firmware {bios_ver}",
            subtitle=f"{firmware}{' · ' + detalhe if detalhe else ''}",
            icon="emblem-system-symbolic"))
    else:
        section.rows.append(Row(title=firmware, subtitle="Modo de inicialização",
                                icon="emblem-system-symbolic"))
    return section


# ------------------------------------------------------------ processador ---


def cpu_section() -> Section:
    cpu = sysinfo.CpuMonitor()
    section = Section("cpu", "Processador", "speedometer-symbolic")
    caches = []
    base = "/sys/devices/system/cpu/cpu0/cache"
    try:
        for nome in sorted(os.listdir(base)):
            if not nome.startswith("index"):
                continue
            nivel = _read(f"{base}/{nome}/level")
            tipo = _read(f"{base}/{nome}/type")
            tamanho = _read(f"{base}/{nome}/size")
            if not nivel:
                continue
            rotulo = {"Data": "dados", "Instruction": "instruções",
                      "Unified": ""}.get(tipo, tipo.lower())
            if tamanho.endswith("K"):
                kb = int(tamanho[:-1]) if tamanho[:-1].isdigit() else 0
                if kb >= 1024:
                    tamanho = f"{kb // 1024} MB"
                elif kb:
                    tamanho = f"{kb} KB"
            caches.append(f"L{nivel}{' ' + rotulo if rotulo else ''} {tamanho}")
    except OSError:
        pass

    freq_max = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    freq_min = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq")
    faixa = ""
    if freq_max.isdigit():
        faixa = f"até {int(freq_max) / 1_000_000:.1f} GHz".replace(".", ",")
        if freq_min.isdigit():
            faixa = (f"{int(freq_min) / 1_000_000:.1f} a "
                     f"{int(freq_max) / 1_000_000:.1f} GHz").replace(".", ",")

    info = _read("/proc/cpuinfo")
    flags = set(re.search(r"^flags\s*:(.*)$", info, re.M).group(1).split()) \
        if re.search(r"^flags\s*:", info, re.M) else set()
    virtual = "Intel VT-x" if "vmx" in flags else "AMD-V" if "svm" in flags else ""
    soquetes = len({m.group(1) for m in
                    re.finditer(r"^physical id\s*:\s*(\d+)", info, re.M)}) or 1

    detalhes = [("Núcleos", f"{cpu.cores} físicos · {cpu.threads} lógicos")]
    if faixa:
        detalhes.append(("Frequência", faixa))
    if caches:
        detalhes.append(("Cache", " · ".join(caches)))
    if soquetes > 1:
        detalhes.append(("Soquetes", str(soquetes)))
    if virtual:
        detalhes.append(("Virtualização", f"{virtual} disponível"))
    detalhes.append(("Arquitetura", os.uname().machine))
    temp = sysinfo.cpu_temperature()
    if temp:
        detalhes.append(("Temperatura agora", f"{temp:.0f} °C"))

    section.rows.append(Row(
        title=cpu.model,
        subtitle=f"{cpu.cores} núcleos · {cpu.threads} threads"
                 + (f" · {faixa}" if faixa else ""),
        icon="speedometer-symbolic", details=detalhes))
    return section


# ---------------------------------------------------------------- memória ---


def memory_section(modulos: list[dict] | None = None) -> Section:
    mem = sysinfo.memory()
    section = Section("memoria", "Memória", "drive-harddisk-symbolic")
    detalhes = [
        ("Em uso agora", fmt_bytes(mem.used)),
        ("Disponível", fmt_bytes(mem.available)),
        ("Em cache", fmt_bytes(mem.cache_total)),
    ]
    if mem.swap_total:
        detalhes.append(("Memória virtual", f"{fmt_bytes(mem.swap_used)} de "
                                            f"{fmt_bytes(mem.swap_total)}"))
    section.rows.append(Row(
        title=f"{fmt_bytes(mem.total)} de memória RAM",
        subtitle="Total instalado", icon="drive-harddisk-symbolic",
        details=detalhes))

    if modulos is None:
        section.note = "pedir-modulos"
        return section
    if not modulos:
        section.note = "sem-modulos"
        return section
    for mod in modulos:
        partes = [p for p in (mod.get("tipo"), mod.get("velocidade")) if p]
        section.rows.append(Row(
            title=f"{mod.get('slot', 'Slot')} · {mod.get('tamanho', '?')}",
            subtitle=" · ".join(partes) or "Módulo instalado",
            icon="application-x-firmware-symbolic",
            details=[(k, v) for k, v in (
                ("Fabricante", mod.get("fabricante", "")),
                ("Modelo", mod.get("modelo", "")),
                ("Número de série", mod.get("serie", "")),
                ("Forma", mod.get("forma", "")),
            ) if v]))
    return section


def parse_dmidecode_memory(texto: str) -> list[dict]:
    """Lê a saída do dmidecode -t 17 e devolve só os slots ocupados."""
    modulos = []
    atual: dict[str, str] = {}
    for linha in texto.splitlines():
        limpa = linha.strip()
        if limpa.startswith("Memory Device"):
            if atual.get("tamanho"):
                modulos.append(atual)
            atual = {}
            continue
        if ":" not in limpa:
            continue
        chave, _, valor = limpa.partition(":")
        valor = valor.strip()
        chave = chave.strip()
        if valor.lower() in ("not specified", "unknown", "none", "no module installed"):
            continue
        mapa = {
            "Size": "tamanho", "Locator": "slot", "Bank Locator": "banco",
            "Type": "tipo", "Speed": "velocidade_max",
            "Configured Memory Speed": "velocidade", "Manufacturer": "fabricante",
            "Part Number": "modelo", "Serial Number": "serie",
            "Form Factor": "forma",
        }
        campo = mapa.get(chave)
        if campo:
            atual[campo] = valor
    if atual.get("tamanho"):
        modulos.append(atual)
    for mod in modulos:
        mod.setdefault("velocidade", mod.get("velocidade_max", ""))
        if mod.get("tamanho", "").endswith(" MB"):
            try:
                mb = int(mod["tamanho"].split()[0])
                if mb >= 1024:
                    mod["tamanho"] = f"{mb // 1024} GB"
            except ValueError:
                pass
    return modulos


# ------------------------------------------------- placas de vídeo (PCI) ----


def _pci_devices() -> list[dict]:
    base = "/sys/bus/pci/devices"
    saida = []
    try:
        nomes = sorted(os.listdir(base))
    except OSError:
        return saida
    for nome in nomes:
        d = f"{base}/{nome}"
        classe = _read(f"{d}/class")
        if not classe:
            continue
        driver = ""
        link = f"{d}/driver"
        if os.path.islink(link):
            driver = os.path.basename(os.path.realpath(link))
        saida.append({
            "slot": nome,
            "classe": classe,
            "vendor": _read(f"{d}/vendor").replace("0x", "").lower(),
            "device": _read(f"{d}/device").replace("0x", "").lower(),
            "driver": driver,
            "path": d,
        })
    return saida


def _nvidia_info() -> list[dict]:
    rc, out = run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,"
                   "temperature.gpu,utilization.gpu", "--format=csv,noheader"],
                  timeout=10)
    if rc != 0:
        return []
    placas = []
    for linha in out.strip().splitlines():
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) >= 3:
            placas.append({
                "nome": partes[0], "memoria": partes[1], "driver": partes[2],
                "temp": partes[3] if len(partes) > 3 else "",
                "uso": partes[4] if len(partes) > 4 else "",
            })
    return placas


def gpu_section(pci: list[dict], nomes: dict) -> Section:
    section = Section("video", "Placas de vídeo", "video-display-symbolic")
    nvidias = _nvidia_info()
    indice_nvidia = 0
    for dev in pci:
        if not dev["classe"].startswith("0x03"):
            continue
        fabricante, produto = nomes.get((dev["vendor"], dev["device"]), ("", ""))
        fabricante = marca(fabricante)
        titulo = produto or f"Dispositivo {dev['vendor']}:{dev['device']}"
        detalhes = [("Fabricante", fabricante)] if fabricante else []
        if dev["driver"]:
            detalhes.append(("Driver em uso", dev["driver"]))
        detalhes.append(("Endereço PCI", dev["slot"]))
        subtitulo = fabricante or "Vídeo"
        if dev["driver"] == "nvidia" and indice_nvidia < len(nvidias):
            n = nvidias[indice_nvidia]
            indice_nvidia += 1
            titulo = n["nome"] or titulo
            subtitulo = f"{n['memoria']} de memória · driver {n['driver']}"
            if n["temp"]:
                detalhes.append(("Temperatura agora", f"{n['temp']} °C"))
            if n["uso"]:
                detalhes.append(("Uso agora", n["uso"]))
        elif dev["classe"].startswith("0x0380"):
            subtitulo = (fabricante or "Vídeo") + " · integrada"
        section.rows.append(Row(title=titulo, subtitle=subtitulo,
                                icon="video-display-symbolic", details=detalhes))
    return section


# ------------------------------------------------------------ armazenamento -


def storage_section() -> Section:
    section = Section("discos", "Armazenamento", "drive-harddisk-symbolic")
    base = "/sys/block"
    try:
        nomes = sorted(os.listdir(base))
    except OSError:
        return section
    montagens: dict[str, list[str]] = {}
    for disco in sysinfo.disks():
        bloco = os.path.basename(os.path.realpath(disco.device))
        bloco = re.sub(r"(p?\d+)$", "", bloco)
        montagens.setdefault(bloco, []).append(disco.mountpoint)
    for nome in nomes:
        if nome.startswith(("loop", "ram", "zram", "dm-", "md")):
            continue
        d = f"{base}/{nome}"
        setores = _read(f"{d}/size")
        if not setores.isdigit() or int(setores) == 0:
            continue
        tamanho = int(setores) * 512
        modelo = _read(f"{d}/device/model") or _read(f"{d}/device/name") or nome
        fabricante = _read(f"{d}/device/vendor")
        rotacional = _read(f"{d}/queue/rotational") == "1"
        removivel = _read(f"{d}/removable") == "1"
        firmware = _read(f"{d}/device/firmware_rev") or _read(f"{d}/device/rev")
        serie = _read(f"{d}/device/serial")
        if nome.startswith("nvme"):
            tipo = "SSD NVMe"
        elif removivel:
            tipo = "Disco removível"
        elif rotacional:
            tipo = "Disco mecânico (HD)"
        else:
            tipo = "SSD"
        detalhes = [("Capacidade", fmt_bytes(tamanho)), ("Tipo", tipo),
                    ("Dispositivo", f"/dev/{nome}")]
        if fabricante:
            detalhes.insert(0, ("Fabricante", fabricante))
        if firmware:
            detalhes.append(("Firmware", firmware))
        if serie:
            detalhes.append(("Número de série", serie))
        pontos = montagens.get(nome, [])
        if pontos:
            detalhes.append(("Montado em", ", ".join(sorted(pontos))))
        section.rows.append(Row(
            title=modelo.strip() or nome,
            subtitle=f"{fmt_bytes(tamanho)} · {tipo}",
            icon="drive-removable-media-symbolic" if removivel
                 else "drive-harddisk-symbolic",
            details=detalhes))
    return section


# -------------------------------------------------------------------- rede --


def network_section(pci: list[dict], nomes: dict) -> Section:
    section = Section("rede", "Rede", "network-wired-symbolic")
    base = "/sys/class/net"
    try:
        interfaces = sorted(os.listdir(base))
    except OSError:
        return section
    por_slot = {d["slot"]: d for d in pci}
    for nome in interfaces:
        d = f"{base}/{nome}"
        # Só placas de verdade: as virtuais (docker, veth, bridges) não têm
        # dispositivo físico por trás.
        if nome == "lo" or not os.path.exists(f"{d}/device"):
            continue
        sem_fio = os.path.isdir(f"{d}/wireless") or os.path.exists(f"{d}/phy80211")
        driver = ""
        link = f"{d}/device/driver"
        if os.path.islink(link):
            driver = os.path.basename(os.path.realpath(link))
        slot = os.path.basename(os.path.realpath(f"{d}/device"))
        dev = por_slot.get(slot)
        titulo = nome
        fabricante = produto = ""
        if dev:
            fabricante, produto = nomes.get((dev["vendor"], dev["device"]), ("", ""))
            fabricante = marca(fabricante)
            titulo = produto or nome
        estado = _read(f"{d}/operstate")
        velocidade = _read(f"{d}/speed")
        estados = {"up": "conectada", "down": "desconectada",
                   "unknown": "estado desconhecido"}
        detalhes = [("Interface", nome),
                    ("Tipo", "Sem fio (Wi-Fi)" if sem_fio else "Cabo (Ethernet)")]
        if fabricante:
            detalhes.insert(0, ("Fabricante", fabricante))
        if driver:
            detalhes.append(("Driver", driver))
        mac = _read(f"{d}/address")
        if mac:
            detalhes.append(("Endereço físico", mac.upper()))
        if velocidade.lstrip("-").isdigit() and int(velocidade) > 0:
            detalhes.append(("Velocidade do enlace", f"{int(velocidade)} Mb/s"))
        subtitulo = estados.get(estado, estado)
        if velocidade.lstrip("-").isdigit() and int(velocidade) > 0:
            subtitulo += f" · {int(velocidade)} Mb/s"
        section.rows.append(Row(
            title=titulo, subtitle=subtitulo,
            icon="network-wireless-symbolic" if sem_fio else "network-wired-symbolic",
            details=detalhes))
    return section


# --------------------------------------------------------------- monitores --


def parse_edid(data: bytes) -> dict:
    """Extrai fabricante, modelo, tamanho e resolução nativa de um EDID."""
    if len(data) < 128 or data[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return {}
    bruto = (data[8] << 8) | data[9]
    codigo = "".join(chr(((bruto >> deslocamento) & 0x1F) + ord("A") - 1)
                     for deslocamento in (10, 5, 0))
    info = {
        "codigo": codigo if codigo.isalpha() else "",
        "produto": f"{data[11]:02X}{data[10]:02X}",
        "ano": data[17] + 1990 if data[17] else 0,
        "largura_cm": data[21],
        "altura_cm": data[22],
        "nome": "",
        "serie": "",
        "largura": 0,
        "altura": 0,
    }
    for inicio in (54, 72, 90, 108):
        bloco = data[inicio:inicio + 18]
        if len(bloco) < 18:
            break
        if bloco[0:3] == b"\x00\x00\x00":
            marca = bloco[3]
            texto = bloco[5:18].split(b"\x0a")[0].decode("ascii", "replace").strip()
            if marca == 0xFC:
                info["nome"] = texto
            elif marca == 0xFF:
                info["serie"] = texto
        elif not info["largura"]:
            info["largura"] = bloco[2] | ((bloco[4] & 0xF0) << 4)
            info["altura"] = bloco[5] | ((bloco[7] & 0xF0) << 4)
    if info["largura_cm"] and info["altura_cm"]:
        diagonal = math.hypot(info["largura_cm"], info["altura_cm"]) / 2.54
        info["polegadas"] = round(diagonal)
    return info


_CONECTORES = {
    "DP": "DisplayPort", "HDMI-A": "HDMI", "HDMI-B": "HDMI", "DVI-D": "DVI",
    "DVI-I": "DVI", "eDP": "Tela interna", "VGA": "VGA", "Virtual": "Virtual",
    "DSI": "Tela interna", "Writeback": "Virtual",
}


def display_section(pnp: dict[str, str]) -> Section:
    section = Section("monitores", "Monitores", "video-display-symbolic")
    base = "/sys/class/drm"
    try:
        conectores = sorted(os.listdir(base))
    except OSError:
        return section
    for conector in conectores:
        d = f"{base}/{conector}"
        if not os.path.isfile(f"{d}/status"):
            continue
        if _read(f"{d}/status") != "connected":
            continue
        nome_curto = conector.split("-", 1)[1] if "-" in conector else conector
        tipo = nome_curto.rsplit("-", 1)[0]
        tipo_legivel = _CONECTORES.get(tipo, tipo)
        edid = parse_edid(_read_bytes(f"{d}/edid"))
        fabricante = pnp.get(edid.get("codigo", ""), "")
        titulo = edid.get("nome") or fabricante or f"Monitor em {tipo_legivel}"
        if edid.get("nome") and fabricante:
            titulo = f"{fabricante} {edid['nome']}"
        modos = _read(f"{d}/modes").split()
        nativa = ""
        if edid.get("largura") and edid.get("altura"):
            nativa = f"{edid['largura']}×{edid['altura']}"
        elif modos:
            nativa = modos[0].replace("x", "×")
        partes = [p for p in (nativa, f"{edid.get('polegadas')}\"" if
                              edid.get("polegadas") else "", tipo_legivel) if p]
        detalhes = [("Conector", f"{tipo_legivel} ({nome_curto})")]
        if nativa:
            detalhes.append(("Resolução nativa", nativa))
        if edid.get("polegadas"):
            detalhes.append(("Tamanho", f"{edid['polegadas']} polegadas · "
                                        f"{edid['largura_cm']}×"
                                        f"{edid['altura_cm']} cm"))
        if fabricante:
            detalhes.append(("Fabricante", fabricante))
        if edid.get("ano"):
            detalhes.append(("Ano de fabricação", str(edid["ano"])))
        if len(modos) > 1:
            detalhes.append(("Modos aceitos", f"{len(modos)} resoluções"))
        section.rows.append(Row(title=titulo, subtitle=" · ".join(partes),
                                icon="video-display-symbolic", details=detalhes))
    if not section.rows:
        section.rows.append(Row(title="Nenhum monitor detectado",
                                subtitle="A sessão pode estar sem acesso ao vídeo",
                                icon="video-display-symbolic"))
    return section


# ------------------------------------------------------------------ áudio ---


def audio_section() -> Section:
    section = Section("audio", "Áudio", "audio-card-symbolic")
    texto = _read("/proc/asound/cards")
    for linha in texto.splitlines():
        m = re.match(r"\s*(\d+)\s+\[([^\]]+)\]:\s*(\S+)\s*-\s*(.+)", linha)
        if not m:
            continue
        numero, _curto, driver, nome = m.groups()
        section.rows.append(Row(
            title=nome.strip(), subtitle=f"Placa {numero} · {driver}",
            icon="audio-card-symbolic",
            details=[("Driver", driver), ("Índice", numero)]))
    if not section.rows:
        section.rows.append(Row(title="Nenhuma placa de som encontrada",
                                icon="audio-card-symbolic"))
    return section


# -------------------------------------------------------------------- USB ---


def usb_section() -> Section:
    section = Section("usb", "Dispositivos USB", "drive-removable-media-symbolic")
    base = "/sys/bus/usb/devices"
    try:
        nomes = sorted(os.listdir(base))
    except OSError:
        return section
    pendentes = []
    for nome in nomes:
        if ":" in nome:            # interfaces, não dispositivos
            continue
        d = f"{base}/{nome}"
        produto = _read(f"{d}/product")
        fabricante = _read(f"{d}/manufacturer")
        classe = _read(f"{d}/bDeviceClass")
        if _read(f"{d}/idVendor").lower() == "1d6b":
            continue               # hub-raiz: é o controlador, não um aparelho
        if not produto and not fabricante:
            continue
        if fabricante == produto:
            fabricante = ""
        if classe == "09":
            produto = produto or "Hub USB"
        velocidade = _read(f"{d}/speed")
        pendentes.append(Row(
            title=produto or fabricante,
            subtitle=" · ".join(x for x in (
                marca(fabricante) if produto else "",
                "Hub USB" if classe == "09" else "",
                f"{velocidade} Mb/s" if velocidade else "") if x) or "USB",
            icon="drive-removable-media-symbolic",
            details=[(k, v) for k, v in (
                ("Fabricante", fabricante),
                ("Identificação", f"{_read(f'{d}/idVendor')}:"
                                  f"{_read(f'{d}/idProduct')}"),
                ("Velocidade", f"{velocidade} Mb/s" if velocidade else ""),
                ("Porta", nome),
            ) if v]))
    section.rows = pendentes
    if not section.rows:
        section.rows.append(Row(title="Nenhum dispositivo USB conectado",
                                icon="drive-removable-media-symbolic"))
    return section


# ---------------------------------------------------------------- sistema ---


def system_section() -> Section:
    section = Section("sistema", "Sistema", "emblem-system-symbolic")
    section.rows.append(Row(
        title=sysinfo.distro(), subtitle="Sistema operacional",
        icon="emblem-system-symbolic",
        details=[("Núcleo", f"Linux {sysinfo.kernel()}"),
                 ("Ambiente", sysinfo.desktop()),
                 ("Nome da máquina", sysinfo.hostname()),
                 ("Ligado há", fmt_duration(sysinfo.uptime_seconds()))]))
    return section


# ------------------------------------------------------------------ tudo ----


def collect(modulos_memoria: list[dict] | None = None) -> list[Section]:
    pci = _pci_devices()
    desejados = {(d["vendor"], d["device"]) for d in pci}
    nomes = _lookup_ids("pci.ids", desejados)
    pnp = _pnp_vendors()
    return [
        machine_section(),
        cpu_section(),
        memory_section(modulos_memoria),
        gpu_section(pci, nomes),
        storage_section(),
        display_section(pnp),
        network_section(pci, nomes),
        audio_section(),
        usb_section(),
        system_section(),
    ]
