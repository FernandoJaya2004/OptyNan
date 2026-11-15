"""Pruebas aisladas: no ejecutan mantenimiento del equipo."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import threading
import sys
import unittest
from unittest.mock import patch

import OptyNan
from optynan_core import AnalysisCancelled, SessionStore, scan_files, delete_candidates, stream_command


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.state_dir.cleanup()

    def test_preview_preserves_files_and_matches_cleanup_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old.tmp"
            old.write_bytes(b"12345")
            (root / "recent.tmp").write_bytes(b"keep")
            timestamp = time.time() - 90000
            os.utime(old, (timestamp, timestamp))
            result = OptyNan.limpiar_archivos(root, 86400, preview=True)
            self.assertEqual(result, (1, 5, 1))
            self.assertEqual(old.read_bytes(), b"12345")
            self.assertEqual(OptyNan.limpiar_archivos(root, 86400), result)

    def test_new_features_in_isolated_ui(self):
        if os.name != "nt":
            self.skipTest("Interfaz exclusiva de Windows")
        app = OptyNan.OptyNan(data_dir=self.state_dir.name)
        app.withdraw()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                chosen = root / "chosen"
                untouched = root / "untouched"
                chosen.mkdir()
                untouched.mkdir()
                (chosen / "sample").write_bytes(b"delete me")
                (untouched / "sample").write_bytes(b"keep me")
                candidates, _ = scan_files(chosen)
                app.clean_categories([("Prueba", chosen, 0, ())], {chosen: candidates})
                deadline = time.monotonic() + 5
                while app.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.02)
                self.assertFalse(app.busy)
                self.assertFalse((chosen / "sample").exists())
                self.assertTrue((untouched / "sample").exists())
                export = root / "registro.txt"
                app.log("Diagnóstico: batería y conexión")
                with patch("OptyNan.filedialog.asksaveasfilename", return_value=str(export)):
                    app.export_log()
                self.assertIn("Diagnóstico: batería y conexión", export.read_text(encoding="utf-8"))
                with patch("OptyNan.filedialog.asksaveasfilename", return_value=""), patch("OptyNan.Path.write_text") as write:
                    app.export_log()
                    write.assert_not_called()
                app.history.extend([(time.monotonic() - 2, 20, 50), (time.monotonic(), 40, 55)])
                with patch.object(app.notebook, "select", return_value=str(app.charts_tab)), patch.object(app.chart, "winfo_ismapped", return_value=True):
                    app.draw_history()
                self.assertEqual(len(app.chart.find_withtag("series")), 2)
                candidates, skipped = scan_files(untouched)
                app.show_preview([(("Prueba", untouched, 0, ()), candidates, skipped, None)])
                dialogs = [w for w in app.winfo_children() if isinstance(w, OptyNan.tk.Toplevel)]
                self.assertEqual(len(dialogs), 1)
                dialogs[0].destroy()
        finally:
            app.destroy()

    def test_only_old_files_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old.tmp"
            recent = root / "recent.tmp"
            old.write_bytes(b"12345")
            recent.write_bytes(b"keep")
            timestamp = time.time() - 90000
            os.utime(old, (timestamp, timestamp))
            self.assertEqual(OptyNan.limpiar_archivos(root, 86400), (1, 5, 1))
            self.assertTrue(recent.exists())
            self.assertTrue(root.exists())

    def test_nested_files_and_missing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested/file.tmp").write_bytes(b"abc")
            self.assertEqual(OptyNan.limpiar_archivos(root), (1, 3, 0))
            self.assertTrue((root / "nested").is_dir())
            self.assertEqual(OptyNan.limpiar_archivos(root / "missing"), (0, 0, 0))

    def test_drive_root_rejected(self):
        with self.assertRaises(ValueError):
            OptyNan.limpiar_archivos(Path.cwd().anchor)

    def test_linked_root_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("optynan_core.is_link", return_value=True):
                with self.assertRaises(ValueError):
                    OptyNan.limpiar_archivos(directory)

    def test_command_failure_is_not_success(self):
        result = subprocess.CompletedProcess(["test"], 5, "", "Acceso denegado")
        with patch("OptyNan.subprocess.run", return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, "Código 5"):
                OptyNan.ejecutar(["test"])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_timeout_propagates(self):
        with patch("OptyNan.subprocess.run", side_effect=subprocess.TimeoutExpired("test", 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                OptyNan.ejecutar(["test"], 1)

    def test_ui_opens_and_finishes_background_task(self):
        if os.name != "nt":
            self.skipTest("Interfaz exclusiva de Windows")
        app = OptyNan.OptyNan(data_dir=self.state_dir.name)
        app.withdraw()
        try:
            app.update_metrics()
            app.start("Prueba aislada", lambda: "Resultado simulado")
            deadline = time.monotonic() + 5
            while app.busy and time.monotonic() < deadline:
                app.update()
                time.sleep(0.02)
            self.assertFalse(app.busy)
            self.assertIn("Resultado simulado", app.log_area.get("1.0", "end"))
        finally:
            app.destroy()

    def test_snapshot_excludes_new_changed_and_unselected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("selected", "changed", "excluded"):
                (root / name).write_bytes(b"original")
            candidates, _ = scan_files(root)
            (root / "new").write_bytes(b"new")
            (root / "changed").write_bytes(b"updated content")
            selected = [c for c in candidates if c.path.name != "excluded"]
            self.assertEqual(delete_candidates(root, selected), (1, 8, 1))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["changed", "excluded", "new"])

    def test_cancel_scan_never_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "keep"
            file.write_text("keep")
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(AnalysisCancelled):
                scan_files(directory, cancel=cancelled)
            self.assertTrue(file.exists())

    def test_snapshot_rejects_outside_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside").write_bytes(b"keep")
            candidates, _ = scan_files(root)
            inside = root / "inside"
            inside.mkdir()
            self.assertEqual(delete_candidates(inside, candidates), (0, 0, 1))
            self.assertTrue((root / "outside").exists())

    def test_persistent_plan_history_and_rotation(self):
        plan = "381b4222-f694-41f0-9685-ff5bb260df2e"
        store = SessionStore(self.state_dir.name)
        store.save_plan(plan)
        store.logger.info("Una tarea de la sesión anterior")
        store.close()
        reopened = SessionStore(self.state_dir.name)
        try:
            self.assertEqual(reopened.load_plan(), plan)
            self.assertIn("sesión anterior", reopened.read_history())
            reopened.handler.maxBytes = 150
            for _ in range(10):
                reopened.logger.info("x" * 80)
            self.assertTrue((Path(self.state_dir.name) / "activity.log.1").exists())
            self.assertLessEqual(len(reopened.read_history(limit=100).encode("utf-8")), 103)
            reopened.clear_plan()
            self.assertIsNone(reopened.load_plan())
            reopened.plan_path.write_text('{"previous_plan":"invalid"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                reopened.load_plan()
        finally:
            reopened.close()

    def test_streaming_arrives_before_process_exits(self):
        # El hijo espera a que el callback reciba su primera salida.
        signal = Path(self.state_dir.name) / "received"
        source = "import pathlib,time,sys; p=pathlib.Path(sys.argv[1]); print('inicio',flush=True); deadline=time.monotonic()+4\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.02)\nprint('fin',flush=True); sys.exit(0 if p.exists() else 7)"
        output = []

        def receive(text):
            output.append(text)
            signal.touch()

        stream_command([sys.executable, "-c", source, str(signal)], receive, 6)
        self.assertIn("inicio", "\n".join(output))
        self.assertIn("fin", "\n".join(output))

    def test_stream_timeout_and_failure(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            stream_command([sys.executable, "-c", "import time; time.sleep(5)"], lambda text: None, 0.15)
        with self.assertRaisesRegex(RuntimeError, "código 3"):
            stream_command([sys.executable, "-c", "raise SystemExit(3)"], lambda text: None, 3)

    def test_metrics_cache_pause_and_chart_reuse(self):
        app = OptyNan.OptyNan(data_dir=self.state_dir.name)
        app.withdraw()
        try:
            with patch("OptyNan.psutil.disk_usage", wraps=OptyNan.psutil.disk_usage) as disk, patch.object(app, "draw_history") as draw:
                app.update_metrics()
                app.update_metrics()
                self.assertEqual(disk.call_count, 1)
                draw.assert_not_called()
            size = len(app.history)
            app.monitor_paused.set(True)
            app.monitor_changed()
            app.update_metrics()
            self.assertEqual(len(app.history), size)
            self.assertIsNone(app.metric_timer)
            with patch.object(app.notebook, "select", return_value=str(app.charts_tab)), patch.object(app.chart, "winfo_ismapped", return_value=True):
                app.draw_history()
                ids = app.chart.find_all()
                app.draw_history()
                self.assertEqual(app.chart.find_all(), ids)
        finally:
            app.destroy()

    def test_browser_profiles_discovered(self):
        app = OptyNan.OptyNan(data_dir=self.state_dir.name)
        app.withdraw()
        try:
            app.local = Path(self.state_dir.name)
            for name in ("Default", "Profile 1", "Profile 7", "Unrelated"):
                (app.local / "Google/Chrome/User Data" / name).mkdir(parents=True)
            names = [category[0] for category in app.cleanup_categories() if category[3] == ("chrome.exe",)]
            self.assertEqual(len(names), 3)
            self.assertTrue(any("Profile 7" in name for name in names))
        finally:
            app.destroy()

    def test_energy_saved_before_change_and_retained_on_restore_failure(self):
        app = OptyNan.OptyNan(data_dir=self.state_dir.name)
        app.withdraw()
        plan = "381b4222-f694-41f0-9685-ff5bb260df2e"

        def execute(args, timeout=180):
            if args[1] == "/getactivescheme":
                return plan
            self.assertEqual(app.store.load_plan(), plan)
            return ""

        def wait():
            deadline = time.monotonic() + 4
            while app.busy and time.monotonic() < deadline:
                app.update()
                time.sleep(0.01)
            self.assertFalse(app.busy)

        try:
            # Procesar la comprobación inicial antes de simular la activación.
            app.update()
            with patch("OptyNan.ejecutar", side_effect=execute):
                app.toggle_performance()
                wait()
            self.assertEqual(app.previous_plan, plan)
            with patch("OptyNan.ejecutar", side_effect=RuntimeError("simulado")):
                app.toggle_performance()
                wait()
            self.assertEqual(app.store.load_plan(), plan)
            with patch("OptyNan.ejecutar", return_value=""):
                app.toggle_performance()
                wait()
            self.assertIsNone(app.store.load_plan())
            self.assertIsNone(app.previous_plan)
        finally:
            app.destroy()

    def test_startup_recovers_saved_plan(self):
        plan = "381b4222-f694-41f0-9685-ff5bb260df2e"
        store = SessionStore(self.state_dir.name)
        store.save_plan(plan)
        store.close()
        with patch("OptyNan.messagebox.askyesno", return_value=True), patch("OptyNan.ejecutar", return_value="") as execute:
            app = OptyNan.OptyNan(data_dir=self.state_dir.name)
            app.withdraw()
            try:
                app.update()
                deadline = time.monotonic() + 4
                while app.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                execute.assert_called_once_with(["powercfg", "/setactive", plan])
                self.assertIsNone(app.store.load_plan())
            finally:
                app.destroy()

    def test_preview_selection_excludes_file_in_ui(self):
        app = OptyNan.OptyNan(data_dir=self.state_dir.name)
        app.withdraw()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "include").write_bytes(b"delete")
                (root / "exclude").write_bytes(b"keep")
                candidates, skipped = scan_files(root)
                app.show_preview([(("Prueba", root, 0, ()), candidates, skipped, None)])
                dialog = next(w for w in app.winfo_children() if isinstance(w, OptyNan.tk.Toplevel))

                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)

                widgets = list(descendants(dialog))
                buttons = {w.cget("text"): w for w in widgets if isinstance(w, OptyNan.tk.Button)}
                trees = [w for w in widgets if isinstance(w, OptyNan.ttk.Treeview)]
                buttons["Seleccionar categoría"].invoke()
                index = next(i for i, c in enumerate(candidates) if c.path.name == "exclude")
                trees[1].selection_set(str(index))
                buttons["Excluir / incluir archivos"].invoke()
                with patch("OptyNan.messagebox.askyesno", return_value=True):
                    buttons["Limpiar selección"].invoke()
                deadline = time.monotonic() + 4
                while app.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertFalse((root / "include").exists())
                self.assertTrue((root / "exclude").exists())
        finally:
            app.destroy()

    def test_running_browser_cache_is_skipped(self):
        app = OptyNan.OptyNan(data_dir=self.state_dir.name)
        app.withdraw()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "cache").write_bytes(b"keep")
                candidates, _ = scan_files(root)
                class Browser:
                    info = {"name": "chrome.exe"}
                with patch("OptyNan.psutil.process_iter", return_value=[Browser()]):
                    app.clean_categories([("Chrome", root, 0, ("chrome.exe",))], {root: candidates})
                    deadline = time.monotonic() + 4
                    while app.busy and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                self.assertTrue((root / "cache").exists())
                self.assertIn("Omitido: Chrome", app.log_area.get("1.0", "end"))
        finally:
            app.destroy()

    def test_single_instance_prevents_shared_state_conflict(self):
        if os.name != "nt":
            self.skipTest("Bloqueo de sesión Windows")
        store = SessionStore(self.state_dir.name)
        try:
            with self.assertRaisesRegex(RuntimeError, "ya está abierto"):
                SessionStore(self.state_dir.name)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
