"""Medición reproducible de arranque y reposo; no ejecuta mantenimiento."""
import importlib.machinery
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import psutil


def measure(source, seconds=6):
    start = time.perf_counter()
    loader = importlib.machinery.SourceFileLoader("optynan_benchmark", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        options = {"data_dir": Path(directory)} if "data_dir" in inspect.signature(module.OptyNan).parameters else {}
        app = module.OptyNan(**options)
        app.withdraw()
        app.update_idletasks()
        startup = time.perf_counter() - start
        process = psutil.Process()
        initial_cpu = sum(process.cpu_times()[:2])
        started = time.perf_counter()
        with patch.object(module.psutil, "disk_usage", wraps=module.psutil.disk_usage) as disk, patch.object(app, "draw_history", wraps=app.draw_history) as draw:
            while time.perf_counter() - started < seconds:
                app.update()
                time.sleep(0.01)
            duration = time.perf_counter() - started
            result = {"startup_ms": round(startup * 1000, 1), "idle_seconds": round(duration, 2),
                      "cpu_seconds": round(sum(process.cpu_times()[:2]) - initial_cpu, 4),
                      "rss_mb": round(process.memory_info().rss / 1024**2, 2),
                      "disk_queries": disk.call_count, "hidden_chart_draw_calls": draw.call_count}
        app.destroy()
    return result


if __name__ == "__main__":
    print(json.dumps(measure(Path(sys.argv[1])), indent=2))
