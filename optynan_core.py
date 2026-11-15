"""Operaciones comprobables sin interfaz ni efectos al importar."""

from dataclasses import dataclass
import codecs
import ctypes
import json
import locale
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import threading
import time


GUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class AnalysisCancelled(Exception):
    """El usuario canceló una exploración sin modificar archivos."""


@dataclass(frozen=True)
class Candidate:
    path: Path
    size: int
    modified_ns: int
    device: int
    inode: int


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def validate_root(root):
    root = Path(os.path.abspath(root))
    if root == Path(root.anchor):
        raise ValueError("No se permite limpiar la raíz de una unidad.")
    if any(is_link(p) for p in (root, *root.parents) if p.exists() or p.is_symlink()):
        raise ValueError(f"Se rechazó una ruta con enlaces: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"No es una carpeta: {root}")
    return root


def scan_files(root, min_age=0, cancel=None):
    """Enumerar candidatos; nunca borrar y comprobar cancelación entre entradas."""
    root = validate_root(root)
    candidates, skipped = [], 0
    cutoff = time.time() - min_age

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise AnalysisCancelled()

    def scan_error(_error):
        nonlocal skipped
        skipped += 1

    check_cancel()
    if not root.exists():
        return candidates, skipped
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
        check_cancel()
        safe = []
        for name in dirs:
            check_cancel()
            try:
                if is_link(Path(folder) / name):
                    skipped += 1
                else:
                    safe.append(name)
            except OSError:
                skipped += 1
        dirs[:] = safe
        for name in files:
            check_cancel()
            path = Path(folder) / name
            try:
                info = path.lstat()
                if is_link(path) or not stat.S_ISREG(info.st_mode) or info.st_mtime > cutoff:
                    skipped += 1
                    continue
                candidates.append(Candidate(path, info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino))
            except OSError:
                skipped += 1
    return candidates, skipped


def delete_candidates(root, candidates):
    """Eliminar solo los archivos revisados que aún coinciden con la instantánea."""
    root = validate_root(root)
    count = size = skipped = 0
    for candidate in candidates:
        path = candidate.path
        try:
            path.relative_to(root)
            if ".." in path.parts or any(is_link(p) for p in (path, *path.parents)):
                raise ValueError("Archivo fuera de la carpeta o con enlaces.")
            info = path.lstat()
            signature = (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino)
            if not stat.S_ISREG(info.st_mode) or signature != (
                candidate.size, candidate.modified_ns, candidate.device, candidate.inode
            ):
                skipped += 1
                continue
            path.unlink()
            count += 1
            size += info.st_size
        except (OSError, ValueError):
            skipped += 1
    return count, size, skipped


class SessionStore:
    """Estado atómico y registro acotado: 1 MB más tres copias rotadas."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = None
        if os.name == "nt":
            import msvcrt
            self.lock = (self.directory / "session.lock").open("a+b")
            self.lock.seek(0, os.SEEK_END)
            if not self.lock.tell():
                self.lock.write(b"0")
                self.lock.flush()
            self.lock.seek(0)
            try:
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.lock.close()
                raise RuntimeError("OptyNan ya está abierto con esta carpeta de datos.") from None
        self.plan_path = self.directory / "energy-plan.json"
        self.settings_path = self.directory / "settings.json"
        self.logger = logging.Logger(f"optynan-{id(self)}", level=logging.INFO)
        self.handler = RotatingFileHandler(self.directory / "activity.log", maxBytes=1_000_000,
                                          backupCount=3, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(self.handler)

    @staticmethod
    def atomic_write(path, value):
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)

    def save_plan(self, plan):
        if not GUID_PATTERN.fullmatch(plan):
            raise ValueError("Identificador de plan inválido.")
        self.atomic_write(self.plan_path, {"previous_plan": plan})

    def load_plan(self):
        if not self.plan_path.exists():
            return None
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan = value.get("previous_plan") if isinstance(value, dict) else None
        if not isinstance(plan, str) or not GUID_PATTERN.fullmatch(plan):
            raise ValueError("El archivo de recuperación del plan no es válido.")
        return plan

    def clear_plan(self):
        self.plan_path.unlink(missing_ok=True)

    def read_history(self, limit=200_000):
        # Leer la cola en bytes: memoria acotada incluso con registros rotados.
        chunks, remaining = [], limit
        for suffix in ("", ".1", ".2", ".3"):
            path = self.directory / f"activity.log{suffix}"
            if not path.exists() or remaining <= 0:
                continue
            with path.open("rb") as file:
                file.seek(0, os.SEEK_END)
                size = min(file.tell(), remaining)
                file.seek(-size, os.SEEK_END)
                chunks.insert(0, file.read(size))
                remaining -= size
        return b"".join(chunks).decode("utf-8", errors="replace")

    def close(self):
        self.handler.close()
        self.logger.removeHandler(self.handler)
        if self.lock is not None:
            self.lock.close()
            self.lock = None


def stream_command(args, notify, timeout=180):
    """Drenar la salida mientras corre el proceso, con memoria acotada y timeout."""
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    chunks = queue.Queue(maxsize=64)
    stop = threading.Event()

    def offer(chunk):
        while not stop.is_set():
            try:
                chunks.put(chunk, timeout=0.1)
                return
            except queue.Full:
                continue

    def reader():
        try:
            while data := os.read(process.stdout.fileno(), 4096):
                offer(data)
        finally:
            offer(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    decoder = None
    try:
        while True:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                data = chunks.get(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if data is None:
                if decoder:
                    tail = decoder.decode(b"", final=True).strip()
                    if tail:
                        notify(tail)
                break
            if decoder is None:
                encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}" if os.name == "nt" else locale.getpreferredencoding(False)
                if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                    encoding = "utf-16"
                elif len(data) > 3 and data[1] == 0 and data[3] == 0:
                    encoding = "utf-16-le"
                decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
            text = decoder.decode(data).replace("\r", "\n").strip()
            if text:
                notify(text)
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if code:
            raise RuntimeError(f"El comando terminó con código {code}. Revisa su salida en el registro.")
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
            process.wait()
        thread.join(timeout=1)
        process.stdout.close()
