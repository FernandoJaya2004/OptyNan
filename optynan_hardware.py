"""Inventario de solo lectura. No consulta números de serie ni identificadores personales."""

import base64
import ctypes
import json
import os
import subprocess


UNAVAILABLE = "No disponible"
# SMBIOS Type 17 (no confundir con la enumeración CIM MemoryType).
SMBIOS_TYPES = {
    18: "DDR", 19: "DDR2", 20: "DDR2 FB-DIMM", 24: "DDR3",
    26: "DDR4", 27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3",
    30: "LPDDR4", 34: "DDR5", 35: "LPDDR5",
}
CIM_TYPES = {20: "DDR", 21: "DDR2", 22: "DDR2 FB-DIMM", 24: "DDR3", 26: "DDR4"}

INVENTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$result = [ordered]@{}
$issues = New-Object System.Collections.Generic.List[string]
function Read-Component($key, $class, $properties) {
    try {
        $result[$key] = @(Get-CimInstance -ClassName $class -OperationTimeoutSec 8 |
            Select-Object -Property $properties)
    } catch {
        $result[$key] = @()
        $issues.Add($key)
    }
}
Read-Component 'system' 'Win32_ComputerSystem' @('Manufacturer','Model','TotalPhysicalMemory')
Read-Component 'os' 'Win32_OperatingSystem' @('Caption','Version','BuildNumber','OSArchitecture')
Read-Component 'cpu' 'Win32_Processor' @('Name','Manufacturer','NumberOfCores','NumberOfLogicalProcessors','MaxClockSpeed')
Read-Component 'ram' 'Win32_PhysicalMemory' @('DeviceLocator','Capacity','SMBIOSMemoryType','MemoryType','ConfiguredClockSpeed','Speed','Manufacturer','PartNumber','FormFactor')
Read-Component 'gpu' 'Win32_VideoController' @('Name','VideoProcessor','DriverVersion')
Read-Component 'board' 'Win32_BaseBoard' @('Manufacturer','Product','Version')
Read-Component 'bios' 'Win32_BIOS' @('Manufacturer','SMBIOSBIOSVersion')
try {
    $result['disks'] = @(Get-PhysicalDisk | Select-Object FriendlyName,MediaType,BusType,Size)
} catch {
    Read-Component 'disks' 'Win32_DiskDrive' @('Model','InterfaceType','Size')
}
Read-Component 'audio' 'Win32_SoundDevice' @('Name','Manufacturer')
$result['issues'] = @($issues.ToArray())
ConvertTo-Json -InputObject $result -Depth 5 -Compress
"""


def memory_type(module):
    raw = module.get("SMBIOSMemoryType")
    if raw not in (None, 0, 1, 2):
        return SMBIOS_TYPES.get(raw, f"No identificado (SMBIOS {raw})")
    return CIM_TYPES.get(module.get("MemoryType"), UNAVAILABLE)


def value(raw, suffix=""):
    if raw is None or raw == "" or raw == 0:
        return UNAVAILABLE
    cleaned = str(raw).strip()
    if cleaned.lower() in {"unknown", "default string", "to be filled by o.e.m."}:
        return UNAVAILABLE
    return cleaned + suffix if cleaned else UNAVAILABLE


def gib(raw):
    try:
        return f"{int(raw) / 1024**3:.2f} GiB" if int(raw) > 0 else UNAVAILABLE
    except (ValueError, TypeError):
        return UNAVAILABLE


class DisplayDevice(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32), ("name", ctypes.c_wchar * 32),
        ("description", ctypes.c_wchar * 128), ("flags", ctypes.c_uint32),
        ("device_id", ctypes.c_wchar * 128), ("device_key", ctypes.c_wchar * 128),
    ]


class DisplayMode(ctypes.Structure):
    # DEVMODEW: la unión de impresora/pantalla ocupa 16 bytes.
    _fields_ = [
        ("name", ctypes.c_wchar * 32), ("spec", ctypes.c_uint16),
        ("driver", ctypes.c_uint16), ("size", ctypes.c_uint16),
        ("extra", ctypes.c_uint16), ("fields", ctypes.c_uint32),
        ("union", ctypes.c_byte * 16), ("color", ctypes.c_int16),
        ("duplex", ctypes.c_int16), ("y_resolution", ctypes.c_int16),
        ("tt_option", ctypes.c_int16), ("collate", ctypes.c_int16),
        ("form", ctypes.c_wchar * 32), ("log_pixels", ctypes.c_uint16),
        ("bits", ctypes.c_uint32), ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32), ("display_flags", ctypes.c_uint32),
        ("frequency", ctypes.c_uint32), ("icm_method", ctypes.c_uint32),
        ("icm_intent", ctypes.c_uint32), ("media_type", ctypes.c_uint32),
        ("dither", ctypes.c_uint32), ("reserved1", ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32), ("panning_width", ctypes.c_uint32),
        ("panning_height", ctypes.c_uint32),
    ]


def display_inventory():
    """Modos activos en píxeles físicos, independientes de la escala de Tkinter."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enumerate_device = user32.EnumDisplayDevicesW
    enumerate_device.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                 ctypes.POINTER(DisplayDevice), ctypes.c_uint32]
    enumerate_device.restype = ctypes.c_int
    enumerate_mode = user32.EnumDisplaySettingsW
    enumerate_mode.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(DisplayMode)]
    enumerate_mode.restype = ctypes.c_int
    displays = []
    index = 0
    while True:
        device = DisplayDevice()
        device.cb = ctypes.sizeof(device)
        if not enumerate_device(None, index, ctypes.byref(device), 0):
            break
        index += 1
        if not device.flags & 1 or device.flags & 8:
            continue
        mode = DisplayMode()
        mode.size = ctypes.sizeof(mode)
        success = enumerate_mode(device.name, 0xFFFFFFFF, ctypes.byref(mode))
        displays.append({
            "Name": device.name, "Primary": bool(device.flags & 4),
            "Width": mode.width if success else None,
            "Height": mode.height if success else None,
            "Hz": mode.frequency if success and mode.frequency > 1 else None,
        })
    return displays


def collect_hardware():
    if os.name != "nt":
        raise RuntimeError("El inventario requiere Windows.")
    encoded = base64.b64encode(INVENTORY_SCRIPT.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, timeout=100, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("Windows no pudo consultar el inventario. Puedes volver a intentarlo.")
    try:
        data = json.loads(result.stdout.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Respuesta inválida")
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Windows devolvió un inventario no válido.") from error
    try:
        data["displays"] = display_inventory()
    except OSError:
        data["displays"] = []
        data.setdefault("issues", []).append("displays")
    return data


def hardware_sections(data):
    """Construir secciones legibles sin inferir datos ausentes del firmware."""
    sections = []

    def rows(key):
        content = data.get(key) or []
        return [content] if isinstance(content, dict) else content

    def add(title, pairs):
        sections.append((title, pairs or [("Información", UNAVAILABLE)]))

    system = rows("system")
    operating = rows("os")
    summary = []
    for item in system:
        summary.extend([("Fabricante", value(item.get("Manufacturer"))),
                        ("Modelo del equipo", value(item.get("Model"))),
                        ("RAM disponible para Windows", gib(item.get("TotalPhysicalMemory")))])
    for item in operating:
        summary.extend((label, value(item.get(key))) for label, key in
                       [("Windows", "Caption"), ("Versión", "Version"),
                        ("Compilación", "BuildNumber"), ("Arquitectura", "OSArchitecture")])
    add("Equipo y sistema operativo", summary)
    cpus = rows("cpu")
    for index, item in enumerate(cpus or [{}], 1):
        add(f"Procesador {index}", [(label, value(item.get(key), suffix)) for label, key, suffix in
            [("Modelo", "Name", ""), ("Fabricante", "Manufacturer", ""),
             ("Núcleos físicos", "NumberOfCores", ""), ("Procesadores lógicos", "NumberOfLogicalProcessors", ""),
             ("Frecuencia máxima informada", "MaxClockSpeed", " MHz")]])
    modules = rows("ram")
    if modules:
        capacities = [item.get("Capacity") for item in modules]
        total = sum(int(c or 0) for c in capacities)
        add("Memoria RAM", [("Módulos informados", str(len(modules))),
                            ("Capacidad instalada", gib(total) if all(capacities) else UNAVAILABLE)])
    for index, item in enumerate(modules or [{}], 1):
        add(f"RAM · módulo {index}", [
            ("Ubicación", value(item.get("DeviceLocator"))),
            ("Capacidad", gib(item.get("Capacity"))), ("Tipo", memory_type(item)),
            ("Fabricante", value(item.get("Manufacturer"))), ("Referencia", value(item.get("PartNumber"))),
            ("Formato", {8: "DIMM", 12: "SO-DIMM"}.get(item.get("FormFactor"), UNAVAILABLE)),
            ("Velocidad configurada informada", value(item.get("ConfiguredClockSpeed"), " MT/s")),
            ("Velocidad nominal informada", value(item.get("Speed"), " MT/s")),
        ])
    for index, item in enumerate(rows("gpu") or [{}], 1):
        add(f"Gráfica {index}", [("Modelo", value(item.get("Name"))),
                                ("Procesador gráfico", value(item.get("VideoProcessor"))),
                                ("Controlador", value(item.get("DriverVersion"))),
                                ("VRAM dedicada", "No disponible de forma fiable mediante esta consulta")])
    for index, item in enumerate(rows("displays") or [{}], 1):
        resolution = f"{item['Width']} × {item['Height']} píxeles" if item.get("Width") and item.get("Height") else UNAVAILABLE
        add(f"Pantalla activa {index}", [("Identificador de pantalla", value(item.get("Name"))),
                                         ("Principal", "Sí" if item.get("Primary") else "No" if "Primary" in item else UNAVAILABLE),
                                         ("Resolución actual", resolution),
                                         ("Frecuencia actual", value(item.get("Hz"), " Hz"))])
    for index, item in enumerate(rows("disks") or [{}], 1):
        media = item.get("MediaType")
        disk_type = {3: "HDD", 4: "SSD", 5: "SCM", "HDD": "HDD", "SSD": "SSD", "SCM": "SCM"}.get(media, UNAVAILABLE)
        bus = item.get("BusType")
        connection = {7: "USB", 11: "SATA", 17: "NVMe", 8: "RAID", 10: "SAS"}.get(bus)
        if connection is None:
            connection = value(bus) if isinstance(bus, str) else value(item.get("InterfaceType"))
        add(f"Almacenamiento · unidad {index}", [("Modelo", value(item.get("FriendlyName") or item.get("Model"))),
                                               ("Capacidad", gib(item.get("Size"))), ("Tipo", disk_type),
                                               ("Interfaz", connection)])
    for index, item in enumerate(rows("board") or [{}], 1):
        add(f"Placa base {index}", [("Fabricante", value(item.get("Manufacturer"))),
                                   ("Modelo", value(item.get("Product"))), ("Versión", value(item.get("Version")))])
    for item in rows("bios") or [{}]:
        add("BIOS", [("Fabricante", value(item.get("Manufacturer"))),
                     ("Versión", value(item.get("SMBIOSBIOSVersion")))])
    add("Dispositivos de audio", [(f"Dispositivo {i}", value(item.get("Name")))
                                 for i, item in enumerate(rows("audio"), 1)])
    if data.get("issues"):
        add("Consultas no disponibles", [("Secciones", ", ".join(data["issues"]))])
    return sections
