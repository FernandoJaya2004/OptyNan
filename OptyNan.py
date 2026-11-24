"""Panel de mantenimiento para Windows. Importar este módulo no ejecuta tareas."""

from __future__ import annotations

import ctypes
from collections import deque
from datetime import datetime
import heapq
import json
import locale
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

import psutil
from optynan_hardware import collect_hardware, hardware_sections
from optynan_core import (
    AnalysisCancelled, SessionStore, delete_candidates, scan_files, stream_command,
)


BG = "#0b1220"
CARD = "#172338"
BUTTON = "#263852"
TEXT = "#edf4ff"
MUTED = "#a1b2ca"
ACCENT = "#5eead4"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
GUID = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")


def es_admin():
    """Consultar permisos sin solicitar elevación al importar o iniciar."""
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def ejecutar(args, timeout=180):
    """Ejecutar argumentos sin shell y comprobar el resultado real."""
    encoding = (
        f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        if os.name == "nt"
        else locale.getpreferredencoding(False)
    )
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    output = "\n".join(s.strip() for s in (result.stdout, result.stderr) if s.strip())
    if result.returncode != 0:
        raise RuntimeError(f"Código {result.returncode}: {output or 'Sin detalles del sistema'}")
    return output


def limpiar_archivos(root, min_age=0, *, preview=False):
    """Compatibilidad: emplear el mismo análisis y eliminación que la interfaz."""
    candidates, skipped = scan_files(root, min_age)
    if preview:
        return len(candidates), sum(candidate.size for candidate in candidates), skipped
    count, size, omitted = delete_candidates(root, candidates)
    return count, size, skipped + omitted


class OptyNan(tk.Tk):
    def __init__(self, data_dir=None):
        super().__init__()
        self.title("OptyNan · Centro de mantenimiento")
        self.geometry("980x760")
        self.minsize(860, 660)
        self.configure(bg=BG)
        self.events = queue.Queue(maxsize=512)
        self.busy = False
        self.previous_plan = None
        self.cancel_analysis = threading.Event()
        self.disk_checked_at = 0
        self.disk_cache = None
        self.chart_size = None
        self.metric_timer = None
        self.hardware_loaded = False
        self.action_buttons = []
        self.history = deque(maxlen=601)
        self.system_drive = Path(os.environ.get("SystemRoot", "C:/Windows")).anchor
        self.local = Path(os.environ["LOCALAPPDATA"])
        self.temp = Path(tempfile.gettempdir())
        self.windows = Path(os.environ["SystemRoot"])
        self.store = SessionStore(data_dir or self.local / "OptyNan")
        self.sample_seconds = tk.IntVar(value=2)
        self.monitor_paused = tk.BooleanVar(value=False)
        try:
            settings = json.loads(self.store.settings_path.read_text(encoding="utf-8"))
            if settings.get("sample_seconds") in (1, 2, 5, 10):
                self.sample_seconds.set(settings["sample_seconds"])
        except (OSError, ValueError, AttributeError):
            pass
        self.configure_styles()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        psutil.cpu_percent(None)
        self.after(150, self.poll_events)
        self.metric_timer = self.after(600, self.update_metrics)
        self.log("Listo. Selecciona una herramienta; sus resultados aparecerán aquí.")
        self.after_idle(self.check_recovery)

    def configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD, foreground=MUTED,
                        padding=(20, 12), font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", BUTTON)],
                  foreground=[("selected", ACCENT)])
        style.configure("TProgressbar", troughcolor=BG, background=ACCENT,
                        borderwidth=0, thickness=5)

    def label(self, parent, text, size=10, color=TEXT, bold=False, bg=None):
        return tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=color,
                        font=("Segoe UI", size, "bold" if bold else "normal"))

    def button(self, parent, text, command, primary=False):
        button = tk.Button(
            parent, text=text, command=command, font=("Segoe UI", 10, "bold"),
            bg=ACCENT if primary else BUTTON, fg=BG if primary else TEXT,
            activebackground="#82f2df" if primary else "#354d6e",
            activeforeground=BG if primary else TEXT, disabledforeground=MUTED,
            relief="flat", borderwidth=0, padx=16, pady=11, cursor="hand2",
            wraplength=365,
        )
        self.action_buttons.append(button)
        return button

    def build_ui(self):
        header = tk.Frame(self, bg=BG, padx=26, pady=20)
        header.pack(fill="x")
        self.label(header, "OptyNan", 25, bold=True).pack(anchor="w")
        self.label(header, "Mantenimiento y diagnóstico de tu equipo", color=MUTED).pack(anchor="w")
        badge = "Administrador" if es_admin() else "Sesión estándar · algunas tareas requieren administrador"
        self.label(header, badge, 9, ACCENT).pack(anchor="w", pady=(6, 0))

        metrics = tk.Frame(self, bg=BG, padx=26)
        metrics.pack(fill="x")
        self.metric_widgets = []
        for index, title in enumerate(("PROCESADOR", "MEMORIA RAM", "DISCO DEL SISTEMA")):
            metrics.columnconfigure(index, weight=1, uniform="metric")
            card = tk.Frame(metrics, bg=CARD, padx=16, pady=14)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 10, 0))
            self.label(card, title, 9, MUTED, True).pack(anchor="w")
            value = self.label(card, "—", 20, bold=True)
            value.pack(anchor="w")
            detail = self.label(card, "Leyendo…", 9, MUTED)
            detail.pack(anchor="w", pady=(0, 8))
            bar = ttk.Progressbar(card, maximum=100)
            bar.pack(fill="x")
            self.metric_widgets.append((value, detail, bar))

        actions = tk.Frame(self, bg=BG, padx=26, pady=16)
        actions.pack(fill="x")
        self.button(actions, "Analizar espacio recuperable", self.preview_cleanup, True).pack(side="left")
        self.label(actions, "Revisa y selecciona antes de limpiar.", 10, MUTED).pack(side="left", padx=16)

        notebook = ttk.Notebook(self)
        self.notebook = notebook
        notebook.pack(fill="x", padx=26)
        cleaning = self.tab(notebook, "Limpieza")
        performance = self.tab(notebook, "Rendimiento")
        diagnostics = self.tab(notebook, "Diagnóstico")
        charts = self.tab(notebook, "Gráficas")
        self.charts_tab = charts
        controls = tk.Frame(charts, bg=CARD)
        controls.pack(fill="x")
        self.label(controls, "CPU · verde  RAM · violeta | 10 min | Intervalo (s):", 9, MUTED).pack(side="left")
        interval = ttk.Combobox(controls, textvariable=self.sample_seconds, values=(1, 2, 5, 10), state="readonly", width=3)
        interval.pack(side="left", padx=6)
        interval.bind("<<ComboboxSelected>>", self.monitor_changed)
        tk.Checkbutton(controls, text="Pausar monitoreo", variable=self.monitor_paused,
                       command=self.monitor_changed, bg=CARD, fg=TEXT, selectcolor=BG,
                       activebackground=CARD, activeforeground=TEXT).pack(side="right")
        self.chart = tk.Canvas(charts, bg=CARD, height=140, highlightthickness=0)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda _event: self.draw_history())
        self.hardware_tab = self.tab(notebook, "Mi equipo")
        hardware_header = tk.Frame(self.hardware_tab, bg=CARD)
        hardware_header.pack(fill="x", pady=(0, 6))
        self.hardware_status = self.label(hardware_header, "Consulta los componentes de tu computadora.", 9, MUTED)
        self.hardware_status.pack(side="left")
        self.button(hardware_header, "Actualizar componentes", self.refresh_hardware).pack(side="right")
        hardware_body = tk.Frame(self.hardware_tab, bg=CARD)
        hardware_body.pack(fill="both", expand=True)
        self.hardware_tree = ttk.Treeview(hardware_body, columns=("value",), height=7)
        self.hardware_tree.heading("#0", text="Componente / característica")
        self.hardware_tree.heading("value", text="Información del equipo")
        self.hardware_tree.column("#0", width=280, minwidth=170)
        self.hardware_tree.column("value", width=540, minwidth=330)
        hardware_scroll = ttk.Scrollbar(hardware_body, command=self.hardware_tree.yview)
        hardware_scroll.pack(side="right", fill="y")
        hardware_horizontal = ttk.Scrollbar(hardware_body, orient="horizontal", command=self.hardware_tree.xview)
        hardware_horizontal.pack(side="bottom", fill="x")
        self.hardware_tree.configure(yscrollcommand=hardware_scroll.set, xscrollcommand=hardware_horizontal.set)
        self.hardware_tree.pack(fill="both", expand=True)
        notebook.bind("<<NotebookTabChanged>>", self.tab_changed)
        items = [
            (cleaning, "Caché de Chrome y Edge", self.clean_browser),
            (cleaning, "Vaciar papelera", self.empty_recycle_bin),
            (cleaning, "Caché de DirectX", lambda: self.clean("Caché de DirectX", [self.local / "D3DSCache"])),
            (cleaning, "Caché web de Steam", lambda: self.clean("Caché de Steam (cierra Steam primero)", [self.local / "Steam/htmlcache"])),
            (cleaning, "Limpieza de Windows Update", lambda: self.start("Limpieza de disco de Windows", lambda: ejecutar(["cleanmgr.exe"], 7200))),
            (cleaning, "Limpiar caché DNS", lambda: self.command("Limpiar DNS", ["ipconfig", "/flushdns"], admin=True)),
            (cleaning, "Temporales (más de 24 horas)", self.clean_temps),
            (performance, "Activar / restaurar alto rendimiento", self.toggle_performance),
            (performance, "Ver los 5 procesos que más RAM usan", lambda: self.start("Procesos por memoria", self.top_processes)),
            (performance, "Abrir Administrador de tareas", lambda: self.start("Administrador de tareas", lambda: subprocess.Popen(["taskmgr.exe"]))),
            (performance, "Optimizar unidad del sistema", lambda: self.command("Optimizar disco", ["defrag", self.system_drive.rstrip("\\/"), "/O"], True, 7200, "La optimización puede tardar y aumentar el uso del disco. ¿Continuar?")),
            (performance, "Comprobar y reparar archivos (SFC)", lambda: self.command("SFC", ["sfc", "/scannow"], True, 7200)),
            (performance, "Reparar imagen de Windows (DISM)", lambda: self.command("DISM", ["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], True, 7200)),
            (diagnostics, "Probar conexión y latencia", lambda: self.command("Ping a Google DNS", ["ping", "-n", "4", "8.8.8.8"])),
            (diagnostics, "Reporte de batería", lambda: self.start("Reporte de batería", self.battery_report)),
            (diagnostics, "Salud de unidades físicas", lambda: self.command("Salud de discos (no es un informe SMART completo)", ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus | Format-Table -AutoSize"])),
            (diagnostics, "Restablecer protocolos de red", self.reset_network),
            (diagnostics, "Diagnosticar conexión local, Internet y DNS", self.network_diagnostic),
        ]
        positions = {}
        for tab, title, callback in items:
            index = positions.get(tab, 0)
            self.button(tab, title, callback).grid(row=index // 2, column=index % 2, sticky="ew", padx=5, pady=5)
            positions[tab] = index + 1

        log_frame = tk.Frame(self, bg=BG, padx=26, pady=16)
        log_frame.pack(fill="both", expand=True)
        log_header = tk.Frame(log_frame, bg=BG)
        log_header.pack(fill="x")
        tk.Button(log_header, text="Exportar registro…", command=self.export_log,
                  bg=BUTTON, fg=TEXT, relief="flat", padx=10, pady=4).pack(side="right")
        tk.Button(log_header, text="Historial", command=self.show_history,
                  bg=BUTTON, fg=TEXT, relief="flat", padx=10, pady=4).pack(side="right", padx=6)
        self.cancel_button = tk.Button(log_header, text="Cancelar análisis", command=self.cancel_analysis.set,
                                       state="disabled", bg=BUTTON, fg=TEXT, relief="flat", padx=10, pady=4)
        self.cancel_button.pack(side="right")
        self.status = self.label(log_header, "LISTO", 9, ACCENT, True)
        self.status.pack(anchor="w", pady=(0, 8))
        self.progress = ttk.Progressbar(log_frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 8))
        container = tk.Frame(log_frame, bg=BG)
        container.pack(fill="both", expand=True)
        self.log_area = tk.Text(container, bg="#080e19", fg=TEXT, font=("Consolas", 10),
                                relief="flat", padx=14, pady=10, wrap="word", height=8,
                                state="disabled")
        scrollbar = ttk.Scrollbar(container, command=self.log_area.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_area.configure(yscrollcommand=scrollbar.set)
        self.log_area.pack(fill="both", expand=True)

    def tab(self, notebook, title):
        tab = tk.Frame(notebook, bg=CARD, padx=10, pady=10)
        for column in (0, 1):
            tab.columnconfigure(column, weight=1, uniform="actions")
        notebook.add(tab, text=title)
        return tab

    def log(self, text):
        self.store.logger.info(text)
        self.log_area.configure(state="normal")
        self.log_area.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
        if int(self.log_area.index("end-1c").split(".")[0]) > 2500:
            self.log_area.delete("1.0", "500.0")
        self.log_area.see("end")
        self.log_area.configure(state="disabled")

    def start(self, title, operation, admin=False, confirm=None, cancellable=False):
        if self.busy:
            return
        if admin and not es_admin():
            messagebox.showinfo("Permisos necesarios", "Esta tarea requiere abrir OptyNan desde una terminal ejecutada como administrador.")
            return
        if confirm and not messagebox.askyesno(title, confirm, parent=self):
            return
        self.busy = True
        self.cancel_analysis.clear()
        self.cancel_button.configure(state="normal" if cancellable else "disabled")
        for button in self.action_buttons:
            button.configure(state="disabled")
        self.status.configure(text=f"EN CURSO · {title}")
        self.progress.start(15)
        self.log(f"Iniciando: {title}")

        def worker():
            try:
                output = operation()
                if isinstance(output, str) and output:
                    self.events.put(("log", output))
                self.events.put(("log", f"Completado: {title}"))
            except AnalysisCancelled:
                self.events.put(("log", "Análisis cancelado; no se borró ningún archivo."))
            except subprocess.TimeoutExpired:
                self.events.put(("log", "Se agotó el tiempo de espera. Revisa el estado del sistema antes de repetir la tarea."))
            except Exception as error:
                self.events.put(("log", f"Error en {title}: {error}"))
            finally:
                self.events.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def poll_events(self):
        for _ in range(100):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "log":
                self.log(value)
            elif event == "plan":
                self.previous_plan = value
            elif event == "preview":
                self.after_idle(lambda rows=value: self.show_preview(rows))
            elif event == "hardware":
                self.show_hardware(value)
            elif event == "hardware_error":
                self.hardware_status.configure(text="No se pudo consultar. Pulsa Actualizar componentes para reintentar.")
            elif event == "done":
                self.busy = False
                self.disk_checked_at = 0
                self.cancel_button.configure(state="disabled")
                self.progress.stop()
                self.status.configure(text="LISTO · Alto rendimiento activo" if self.previous_plan else "LISTO")
                for button in self.action_buttons:
                    button.configure(state="normal")
        self.after(100, self.poll_events)

    def command(self, title, args, admin=False, timeout=180, confirm=None):
        self.start(title, lambda: stream_command(args, lambda text: self.events.put(("log", text)), timeout), admin, confirm)

    def tab_changed(self, _event=None):
        selected = self.notebook.select()
        if selected == str(self.charts_tab) and self.chart.winfo_ismapped():
            self.draw_history()
        elif selected == str(self.hardware_tab) and not self.hardware_loaded:
            self.refresh_hardware()

    def refresh_hardware(self):
        if self.busy:
            self.hardware_status.configure(text="Espera a que termine la tarea y pulsa Actualizar componentes.")
            return
        self.hardware_status.configure(text="Consultando componentes… puede tardar unos segundos.")

        def operation():
            try:
                sections = hardware_sections(collect_hardware())
                self.events.put(("hardware", sections))
                return "Inventario actualizado en la pestaña Mi equipo."
            except Exception:
                self.events.put(("hardware_error", None))
                raise

        self.start("Consultar componentes del equipo", operation)

    def show_hardware(self, sections):
        self.hardware_tree.delete(*self.hardware_tree.get_children())
        for title, pairs in sections:
            parent = self.hardware_tree.insert("", "end", text=title, open=True)
            for label, content in pairs:
                self.hardware_tree.insert(parent, "end", text=label, values=(content,))
        self.hardware_loaded = True
        self.hardware_status.configure(text=f"Actualizado a las {datetime.now():%H:%M:%S} · Datos informados por Windows/firmware.")

    def show_history(self):
        dialog = tk.Toplevel(self)
        dialog.title("Historial de actividad · últimas entradas")
        dialog.geometry("850x480")
        dialog.configure(bg=CARD)
        self.label(dialog, "Últimos 200 KB del historial conservado · registros rotados hasta 4 MB", 10, MUTED).pack(pady=10)
        text = tk.Text(dialog, bg=BG, fg=TEXT, wrap="word", font=("Consolas", 10))
        scroll = ttk.Scrollbar(dialog, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill="both", expand=True)
        try:
            text.insert("end", self.store.read_history())
        except OSError as error:
            text.insert("end", f"No se pudo leer el historial: {error}")
        text.configure(state="disabled")
        text.see("end")

    def monitor_changed(self, _event=None):
        try:
            self.store.atomic_write(self.store.settings_path, {"sample_seconds": self.sample_seconds.get()})
        except OSError as error:
            self.log(f"No se pudo guardar la preferencia: {error}")
        if self.metric_timer:
            self.after_cancel(self.metric_timer)
            self.metric_timer = None
        if self.monitor_paused.get():
            self.log("Monitoreo pausado; las tareas pueden seguir ejecutándose.")
        else:
            psutil.cpu_percent(None)
            self.metric_timer = self.after(self.sample_seconds.get() * 1000, self.update_metrics)

    def export_log(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Exportar registro de actividad", defaultextension=".txt",
            initialfile=f"OptyNan-{datetime.now():%Y%m%d-%H%M%S}.txt",
            filetypes=[("Archivo de texto", "*.txt")],
        )
        if not path:
            return
        try:
            content = self.log_area.get("1.0", "end-1c")
            Path(path).write_text(
                f"OptyNan · Registro visible de actividad\nExportado: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n{content}",
                encoding="utf-8",
            )
        except OSError as error:
            messagebox.showerror("No se pudo exportar", str(error), parent=self)
        else:
            self.log(f"Registro exportado: {path}")

    def cleanup_categories(self):
        categories = [("Temporales del usuario (>24 h)", self.temp, 86400, ())]
        if es_admin() and self.windows / "Temp" != self.temp:
            categories.append(("Temporales de Windows (>24 h)", self.windows / "Temp", 86400, ()))
        for name, base, process in (("Chrome", "Google/Chrome", "chrome.exe"), ("Edge", "Microsoft/Edge", "msedge.exe")):
            data = self.local / base / "User Data"
            try:
                profiles = sorted(p for p in data.iterdir() if p.name == "Default" or re.fullmatch(r"Profile \d+", p.name))
            except OSError:
                profiles = []
            for profile in profiles:
                categories.append((f"Caché {name} · {profile.name}", profile / "Cache/Cache_Data", 0, (process,)))
        categories.extend([
            ("Caché DirectX", self.local / "D3DSCache", 0, ()),
            ("Caché web Steam", self.local / "Steam/htmlcache", 0, ("steam.exe", "steamwebhelper.exe")),
        ])
        return categories

    def preview_cleanup(self, categories=None):
        def operation():
            rows = []
            for category in categories if categories is not None else self.cleanup_categories():
                try:
                    candidates, skipped = scan_files(category[1], category[2], self.cancel_analysis)
                    rows.append((category, candidates, skipped, None))
                except (OSError, ValueError) as error:
                    rows.append((category, [], 0, str(error)))
            if self.cancel_analysis.is_set():
                raise AnalysisCancelled()
            self.events.put(("preview", rows))
            return "Análisis terminado sin borrar archivos."
        self.start("Vista previa de limpieza", operation, cancellable=True)

    def show_preview(self, rows):
        dialog = tk.Toplevel(self)
        dialog.title("Revisa los archivos antes de limpiar")
        dialog.geometry("940x590")
        dialog.minsize(760, 480)
        dialog.configure(bg=CARD, padx=18, pady=16)
        dialog.transient(self)
        dialog.grab_set()
        self.label(dialog, "Selecciona categorías y excluye archivos", 18, bold=True).pack(anchor="w")
        self.label(dialog, "Solo se eliminarán archivos revisados que no hayan cambiado. El espacio es una estimación.", 9, MUTED).pack(anchor="w", pady=(4, 10))
        selected_categories = set()
        excluded = set()
        current_page = [0]
        page_size = 150
        category_rows = {str(i): row for i, row in enumerate(rows)}
        counts = {str(i): [len(row[1]), sum(c.size for c in row[1])] for i, row in enumerate(rows)}

        category_frame = tk.Frame(dialog, bg=CARD)
        category_frame.pack(fill="x")
        categories = ttk.Treeview(category_frame, columns=("selected", "count", "size", "status"), height=5)
        categories.heading("#0", text="Categoría")
        categories.column("#0", width=290)
        for key, label, width in (("selected", "Limpiar", 60), ("count", "Archivos", 70), ("size", "MB", 80), ("status", "Estado", 220)):
            categories.heading(key, text=label)
            categories.column(key, width=width, stretch=key == "status")
        scroll = ttk.Scrollbar(category_frame, command=categories.yview)
        scroll.pack(side="right", fill="y")
        categories.configure(yscrollcommand=scroll.set)
        categories.pack(fill="x")
        for key, (category, candidates, skipped, error) in category_rows.items():
            categories.insert("", "end", iid=key, text=category[0],
                              values=("No", len(candidates), f"{counts[key][1] / 1024**2:.2f}", error or f"{skipped} omitidos"))
        toolbar = tk.Frame(dialog, bg=CARD)
        toolbar.pack(fill="x", pady=6)
        detail_frame = tk.Frame(dialog, bg=CARD)
        detail_frame.pack(fill="both", expand=True)
        files = ttk.Treeview(detail_frame, columns=("included", "size"), selectmode="extended", height=8)
        files.heading("#0", text="Archivo (ruta completa)")
        files.heading("included", text="Incluir")
        files.heading("size", text="KB")
        files.column("#0", width=620)
        files.column("included", width=65, stretch=False)
        files.column("size", width=80, stretch=False)
        scroll_files = ttk.Scrollbar(detail_frame, command=files.yview)
        scroll_files.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(detail_frame, orient="horizontal", command=files.xview)
        horizontal.pack(side="bottom", fill="x")
        files.configure(yscrollcommand=scroll_files.set, xscrollcommand=horizontal.set)
        files.pack(fill="both", expand=True)
        footer = tk.Frame(dialog, bg=CARD)
        footer.pack(fill="x", pady=(10, 0))
        total = tk.StringVar()
        page_label = tk.StringVar()
        self.label(toolbar, "Doble clic en categoría: seleccionar. En archivo: excluir/incluir.", 9, MUTED).pack(side="left")

        def active_key():
            return categories.focus() or None

        def update_total():
            count = sum(counts[key][0] for key in selected_categories)
            size = sum(counts[key][1] for key in selected_categories)
            total.set(f"Selección: {count} archivos · {size / 1024**2:.2f} MB")
            clean_button.configure(state="normal" if count else "disabled")

        def render_files(reset=False):
            if reset:
                current_page[0] = 0
            files.delete(*files.get_children())
            key = active_key()
            if key is None:
                return
            candidates = category_rows[key][1]
            pages = max(1, (len(candidates) + page_size - 1) // page_size)
            current_page[0] = max(0, min(current_page[0], pages - 1))
            start = current_page[0] * page_size
            for index in range(start, min(start + page_size, len(candidates))):
                candidate = candidates[index]
                files.insert("", "end", iid=str(index), text=str(candidate.path),
                             values=("No" if (key, index) in excluded else "Sí", f"{candidate.size / 1024:.1f}"))
            page_label.set(f"Página {current_page[0] + 1}/{pages}")

        def toggle_category(_event=None):
            key = active_key()
            if key is None or category_rows[key][3] or not category_rows[key][1]:
                return
            if key in selected_categories:
                selected_categories.remove(key)
            else:
                selected_categories.add(key)
            categories.set(key, "selected", "Sí" if key in selected_categories else "No")
            update_total()

        def toggle_files(_event=None):
            key = active_key()
            if key is None:
                return
            for index_text in files.selection():
                index = int(index_text)
                entry = (key, index)
                size = category_rows[key][1][index].size
                if entry in excluded:
                    excluded.remove(entry)
                    counts[key][0] += 1
                    counts[key][1] += size
                else:
                    excluded.add(entry)
                    counts[key][0] -= 1
                    counts[key][1] -= size
                files.set(index_text, "included", "No" if entry in excluded else "Sí")
            update_total()

        def change_page(delta):
            current_page[0] += delta
            render_files()

        def clean_selected():
            if self.busy:
                return
            selection = [(category_rows[key][0], [c for i, c in enumerate(category_rows[key][1]) if (key, i) not in excluded])
                         for key in sorted(selected_categories)]
            if not any(candidates for _category, candidates in selection):
                return
            if not messagebox.askyesno("Confirmar limpieza", "Se eliminarán definitivamente los archivos incluidos de las categorías seleccionadas. ¿Continuar?", parent=dialog):
                return
            dialog.destroy()
            self.clean_categories([category for category, _ in selection],
                                  snapshots={category[1]: candidates for category, candidates in selection})

        categories.bind("<<TreeviewSelect>>", lambda _event: render_files(True))
        categories.bind("<Double-1>", toggle_category)
        files.bind("<Double-1>", toggle_files)
        controls = tk.Frame(dialog, bg=CARD)
        controls.pack(fill="x")
        for title, command in (("Seleccionar categoría", toggle_category), ("Excluir / incluir archivos", toggle_files),
                               ("◀", lambda: change_page(-1)), ("▶", lambda: change_page(1))):
            tk.Button(controls, text=title, command=command, bg=BUTTON, fg=TEXT, relief="flat", padx=10, pady=6).pack(side="left", padx=(0, 6))
        tk.Label(controls, textvariable=page_label, bg=CARD, fg=MUTED).pack(side="right")
        tk.Label(footer, textvariable=total, bg=CARD, fg=ACCENT).pack(side="left")
        clean_button = tk.Button(footer, text="Limpiar selección", command=clean_selected,
                                 bg=ACCENT, fg=BG, relief="flat", padx=18, pady=8)
        clean_button.pack(side="right")
        tk.Button(footer, text="Cancelar", command=dialog.destroy, bg=BUTTON, fg=TEXT, relief="flat", padx=12, pady=8).pack(side="right", padx=8)
        update_total()
        if rows:
            categories.focus("0")
            categories.selection_set("0")
            render_files()

    def clean_categories(self, categories, snapshots=None):
        # Todos los botones de limpieza llegan aquí después de la misma vista previa.
        if snapshots is None:
            self.preview_cleanup(categories)
            return

        def operation():
            total = 0
            failures = 0
            for name, path, age, processes in categories:
                running = {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
                if running.intersection(processes):
                    self.events.put(("log", f"Omitido: {name}. Cierra la aplicación antes de limpiar."))
                    failures += 1
                    continue
                try:
                    count, size, skipped = delete_candidates(path, snapshots.get(path, []))
                    total += size
                    self.events.put(("log", f"{name}: {count} archivos eliminados · {size / 1024**2:.2f} MB · {skipped} omitidos (modificados, bloqueados o no disponibles)."))
                except (OSError, ValueError) as error:
                    failures += 1
                    self.events.put(("log", f"No se pudo limpiar {name}: {error}"))
            return f"Resultado: {total / 1024**2:.2f} MB eliminados; {failures} categorías no procesadas."
        self.start("Limpieza de archivos seleccionados", operation)

    def draw_history(self):
        if self.notebook.select() != str(self.charts_tab) or not self.chart.winfo_ismapped():
            return
        canvas = self.chart
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 100)
        left, top, right, bottom = 40, 12, width - 12, height - 24
        if self.chart_size != (width, height):
            self.chart_size = (width, height)
            canvas.delete("all")
            for percent in (0, 50, 100):
                y = bottom - (bottom - top) * percent / 100
                canvas.create_line(left, y, right, y, fill=BUTTON)
                canvas.create_text(left - 5, y, text=f"{percent}%", anchor="e", fill=MUTED, font=("Segoe UI", 8))
            canvas.create_text(left, height - 8, text="−10 min", anchor="w", fill=MUTED)
            canvas.create_text(right, height - 8, text="Última muestra", anchor="e", fill=MUTED)
            self.chart_lines = {column: canvas.create_line(0, 0, 0, 0, fill=color, width=2, tags="series", state="hidden")
                                for column, color in ((1, ACCENT), (2, "#c4a1ff"))}
        now = self.history[-1][0] if self.history else time.monotonic()
        for column, line in self.chart_lines.items():
            points = []
            for sample in self.history:
                if now - sample[0] <= 600:
                    points.extend((right - (now - sample[0]) / 600 * (right - left),
                                   bottom - max(0, min(100, sample[column])) / 100 * (bottom - top)))
            if len(points) == 2:
                points += [points[0] + 1, points[1]]
            if points:
                canvas.coords(line, *points)
            canvas.itemconfigure(line, state="normal" if points else "hidden")

    def clean(self, title, paths, min_age=0):
        # Resolver categorías existentes para conservar las comprobaciones por aplicación.
        def operation():
            catalog = {category[1]: category for category in self.cleanup_categories()}
            rows = []
            for path in paths:
                category = catalog.get(path, (title, path, min_age, ()))
                candidates, skipped = scan_files(path, category[2], self.cancel_analysis)
                rows.append((category, candidates, skipped, None))
            if self.cancel_analysis.is_set():
                raise AnalysisCancelled()
            self.events.put(("preview", rows))
        self.start(title, operation, cancellable=True)

    def clean_temps(self):
        paths = [self.temp]
        if es_admin() and self.windows / "Temp" != self.temp:
            paths.append(self.windows / "Temp")
        self.clean("Temporales (>24 h)", paths, 86400)

    def clean_browser(self):
        def operation():
            rows = []
            for category in self.cleanup_categories():
                if category[3] not in (("chrome.exe",), ("msedge.exe",)):
                    continue
                try:
                    candidates, skipped = scan_files(category[1], 0, self.cancel_analysis)
                    rows.append((category, candidates, skipped, None))
                except (OSError, ValueError) as error:
                    rows.append((category, [], 0, str(error)))
            if self.cancel_analysis.is_set():
                raise AnalysisCancelled()
            self.events.put(("preview", rows))
            return "Perfiles detectados. Si no aparecen perfiles, no hay carpetas compatibles disponibles."
        self.start("Analizar perfiles de navegadores", operation, cancellable=True)

    def empty_recycle_bin(self):
        def operation():
            result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0001 | 0x0002 | 0x0004)
            if result != 0:
                raise RuntimeError(f"Windows no pudo vaciar la papelera (HRESULT {result:#x}).")
        self.start("Vaciar papelera", operation, confirm="Se eliminará definitivamente todo el contenido de la papelera. ¿Continuar?")

    def toggle_performance(self):
        previous = self.previous_plan

        def operation():
            if previous:
                ejecutar(["powercfg", "/setactive", previous])
                self.store.clear_plan()
                self.events.put(("plan", None))
                return "Se restauró el plan de energía anterior."
            active = ejecutar(["powercfg", "/getactivescheme"])
            match = GUID.search(active)
            if not match:
                raise RuntimeError("No se pudo identificar el plan de energía actual.")
            if match.group().lower() == HIGH_PERFORMANCE:
                return "El plan de alto rendimiento ya estaba activo; no se modificó."
            self.store.save_plan(match.group())
            self.events.put(("plan", match.group()))
            ejecutar(["powercfg", "/setactive", HIGH_PERFORMANCE])
            return "Alto rendimiento activo. Pulsa otra vez para restaurar el plan anterior."

        self.start("Plan de energía", operation)

    def check_recovery(self):
        try:
            previous = self.store.load_plan()
        except (OSError, ValueError) as error:
            self.log(f"No se pudo leer el plan pendiente de recuperación: {error}")
            return
        if not previous:
            return
        self.previous_plan = previous
        self.log(f"Se encontró un plan pendiente de restauración: {previous}.")
        if messagebox.askyesno("Recuperar plan de energía", "La sesión anterior dejó un plan de energía pendiente. ¿Restaurar ahora el plan anterior?", parent=self):
            self.toggle_performance()
        else:
            self.status.configure(text="PLAN PENDIENTE · Usa Activar / restaurar alto rendimiento")

    def top_processes(self):
        processes = []
        for process in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = process.info
                if info["memory_info"]:
                    processes.append((info["memory_info"].rss, info["pid"], info["name"] or "Desconocido"))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return "\n".join(f"{name[:35]:35} PID {pid:7}  {memory / 1024**2:8.1f} MB" for memory, pid, name in heapq.nlargest(5, processes))

    def battery_report(self):
        with tempfile.NamedTemporaryFile(suffix=".html", prefix="optynan-battery-", delete=False) as report:
            path = Path(report.name)
        try:
            output = ejecutar(["powercfg", "/batteryreport", "/output", str(path)])
            if path.stat().st_size == 0:
                raise RuntimeError("Windows no generó el reporte de batería.")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        webbrowser.open(path.as_uri())
        return f"{output}\nReporte: {path}"

    def reset_network(self):
        def operation():
            for args in (["netsh", "winsock", "reset"], ["netsh", "int", "ip", "reset"]):
                self.events.put(("log", ejecutar(args)))
            return "Es necesario reiniciar Windows para completar el restablecimiento."
        self.start("Restablecer red", operation, admin=True,
                   confirm="Puede interrumpir tu conexión y restablecer configuraciones de red personalizadas. Necesitarás reiniciar Windows. ¿Continuar?")

    def network_diagnostic(self):
        script = """
$ErrorActionPreference = 'Stop'
Write-Output '1. CONEXIÓN LOCAL'
$configs = @(Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' })
if (-not $configs) { Write-Output 'No se detectaron adaptadores conectados.' }
foreach ($config in $configs) {
    Write-Output ($config.InterfaceAlias + ': ' + ($config.IPv4Address.IPAddress -join ', '))
    foreach ($gateway in $config.IPv4DefaultGateway.NextHop) {
        Write-Output ('Puerta de enlace: ' + $gateway)
        ping.exe -n 1 -w 1500 $gateway
    }
}
Write-Output '2. ACCESO A INTERNET (TCP 1.1.1.1:443)'
$client = New-Object System.Net.Sockets.TcpClient
try {
    $connect = $client.ConnectAsync('1.1.1.1', 443)
    if ($connect.Wait(4000) -and $client.Connected) { Write-Output 'Conexión TCP establecida.' }
    else { Write-Output 'No se pudo conectar a este destino.' }
} catch { Write-Output ('Fallo de conexión: ' + $_.Exception.Message) }
finally { $client.Dispose() }
Write-Output '3. RESOLUCIÓN DNS (example.com)'
try { Resolve-DnsName example.com -DnsOnly -QuickTimeout | Select-Object Name,IPAddress | Format-Table -AutoSize }
catch { Write-Output ('Fallo DNS: ' + $_.Exception.Message) }
Write-Output 'Un fallo de ping o de un destino puede deberse a filtrado; no demuestra por sí solo una caída de Internet.'
"""
        self.command("Diagnóstico de conexión", ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=90)

    def update_metrics(self):
        if self.metric_timer:
            self.after_cancel(self.metric_timer)
            self.metric_timer = None
        if self.monitor_paused.get():
            return
        try:
            cpu = psutil.cpu_percent(None)
            ram = psutil.virtual_memory()
            now = time.monotonic()
            if self.disk_cache is None or now - self.disk_checked_at >= 30:
                self.disk_cache = psutil.disk_usage(self.system_drive)
                self.disk_checked_at = now
            disk = self.disk_cache
            self.history.append((time.monotonic(), cpu, ram.percent))
            if self.notebook.select() == str(self.charts_tab) and self.chart.winfo_ismapped():
                self.draw_history()
            readings = ((cpu, "Uso actual"), (ram.percent, f"{ram.used / 1024**3:.1f} / {ram.total / 1024**3:.1f} GB"),
                        (disk.percent, f"{disk.free / 1024**3:.1f} GB libres · {self.system_drive}"))
            for (label, detail, bar), (percent, description) in zip(self.metric_widgets, readings):
                label.configure(text=f"{percent:.0f}%", fg="#fbbf24" if percent >= 85 else TEXT)
                detail.configure(text=description)
                bar.configure(value=percent)
        except (OSError, psutil.Error) as error:
            self.status.configure(text=f"Métricas no disponibles: {error}")
        finally:
            self.metric_timer = self.after(self.sample_seconds.get() * 1000, self.update_metrics)

    def destroy(self):
        # Cancelar callbacks antes de destruir su intérprete Tcl/Tk.
        for callback in self.tk.splitlist(self.tk.call("after", "info")):
            self.after_cancel(callback)
        self.store.close()
        super().destroy()

    def close(self):
        if self.busy:
            messagebox.showinfo("Tarea en curso", "Espera a que termine la tarea antes de cerrar OptyNan.")
            return
        if self.previous_plan:
            self.log("Restaurando el plan de energía antes de cerrar. Cuando termine, vuelve a cerrar la ventana.")
            self.toggle_performance()
            return
        self.destroy()


def main():
    if os.name != "nt":
        print("OptyNan requiere Windows.", file=sys.stderr)
        return 1
    try:
        app = OptyNan()
    except (OSError, RuntimeError) as error:
        messagebox.showerror("No se pudo iniciar OptyNan", str(error))
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
