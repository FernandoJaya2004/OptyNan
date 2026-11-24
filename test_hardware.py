"""Pruebas de inventario sin consultar el hardware real ni modificar el equipo."""

import ctypes
import gc
import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import OptyNan
import optynan_hardware as hardware


class HardwareTests(unittest.TestCase):
    def tearDown(self):
        # Recoger los ciclos de Tk en el hilo principal antes de otra prueba.
        gc.collect()

    def test_smbios_and_cim_use_different_enumerations(self):
        for code, name in [(18, "DDR"), (19, "DDR2"), (20, "DDR2 FB-DIMM"),
                           (24, "DDR3"), (26, "DDR4"), (30, "LPDDR4"),
                           (34, "DDR5"), (35, "LPDDR5")]:
            self.assertEqual(hardware.memory_type({"SMBIOSMemoryType": code}), name)
        self.assertEqual(hardware.memory_type({"SMBIOSMemoryType": 0, "MemoryType": 20}), "DDR")
        self.assertEqual(hardware.memory_type({"SMBIOSMemoryType": 2, "MemoryType": 21}), "DDR2")
        self.assertIn("No identificado", hardware.memory_type({"SMBIOSMemoryType": 99, "MemoryType": 26}))
        self.assertEqual(hardware.memory_type({}), hardware.UNAVAILABLE)

    def test_multiple_components_and_missing_data(self):
        data = {
            "ram": [{"Capacity": 8 * 1024**3, "SMBIOSMemoryType": 26},
                    {"Capacity": 16 * 1024**3, "SMBIOSMemoryType": 34}],
            "gpu": [{"Name": "Integrada"}, {"Name": "Dedicada"}],
            "displays": [{"Width": 1920, "Height": 1080, "Hz": 144, "Primary": True},
                         {"Width": 2560, "Height": 1440, "Hz": 60, "Primary": False}],
            "disks": [{"FriendlyName": "SSD de prueba", "MediaType": 4, "BusType": 17}],
            "issues": ["bios"],
        }
        sections = {title: dict(pairs) for title, pairs in hardware.hardware_sections(data)}
        self.assertEqual(sections["Memoria RAM"]["Capacidad instalada"], "24.00 GiB")
        self.assertEqual(sections["RAM · módulo 2"]["Tipo"], "DDR5")
        self.assertEqual(sections["Gráfica 2"]["Modelo"], "Dedicada")
        self.assertEqual(sections["Pantalla activa 1"]["Resolución actual"], "1920 × 1080 píxeles")
        self.assertEqual(sections["Almacenamiento · unidad 1"]["Interfaz"], "NVMe")
        self.assertEqual(sections["BIOS"]["Versión"], hardware.UNAVAILABLE)
        self.assertEqual(sections["Consultas no disponibles"]["Secciones"], "bios")

    def test_zero_and_partial_values_are_not_invented(self):
        self.assertEqual(hardware.gib(None), hardware.UNAVAILABLE)
        self.assertEqual(hardware.value("To Be Filled By O.E.M."), hardware.UNAVAILABLE)
        sections = dict(hardware.hardware_sections({"ram": [{"Capacity": 1024**3}, {}]}))
        self.assertEqual(dict(sections["Memoria RAM"])["Capacidad instalada"], hardware.UNAVAILABLE)
        self.assertTrue(hardware.hardware_sections({}))

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_display_struct_layout(self):
        self.assertEqual(ctypes.sizeof(hardware.DisplayMode), 220)
        self.assertEqual(hardware.DisplayMode.width.offset, 172)
        self.assertEqual(ctypes.sizeof(hardware.DisplayDevice), 840)

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_collector_utf8_partial_results_and_timeout(self):
        data = {"cpu": [{"Name": "Procesador de prueba"}], "issues": ["ram"]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(data).encode("utf-8"), b"")
        with patch("optynan_hardware.subprocess.run", return_value=completed) as run, patch("optynan_hardware.display_inventory", return_value=[]):
            actual = hardware.collect_hardware()
            self.assertEqual(actual["cpu"], data["cpu"])
            self.assertEqual(actual["issues"], ["ram"])
            self.assertEqual(run.call_args.kwargs["timeout"], 100)
            self.assertNotIn("shell", run.call_args.kwargs)
        with patch("optynan_hardware.subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 100)):
            with self.assertRaises(subprocess.TimeoutExpired):
                hardware.collect_hardware()

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_invalid_inventory_is_reported(self):
        for output in (b"invalid-json", b"null", b"[]"):
            result = subprocess.CompletedProcess([], 0, output, b"")
            with patch("optynan_hardware.subprocess.run", return_value=result):
                with self.assertRaises(RuntimeError):
                    hardware.collect_hardware()

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_hardware_tab_loads_once_and_refreshes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OptyNan.OptyNan(data_dir=directory)
            app.withdraw()
            try:
                with patch("OptyNan.collect_hardware", return_value={"cpu": [{"Name": "CPU de prueba"}]}) as collect:
                    app.notebook.select(app.hardware_tab)
                    app.tab_changed()
                    deadline = time.monotonic() + 5
                    while app.busy and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.02)
                    self.assertTrue(app.hardware_loaded)
                    app.tab_changed()
                    self.assertEqual(collect.call_count, 1)
                    entries = [app.hardware_tree.item(child, "values")
                               for parent in app.hardware_tree.get_children()
                               for child in app.hardware_tree.get_children(parent)]
                    self.assertIn(("CPU de prueba",), entries)
                    app.refresh_hardware()
                    deadline = time.monotonic() + 5
                    while app.busy and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.02)
                    self.assertEqual(collect.call_count, 2)
            finally:
                app.destroy()
