import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_START_LOCK = threading.Lock()


@dataclass
class AnalyticsServerHandle:
    process: object
    owned: bool
    port: int

    @property
    def url(self):
        return f"http://{DEFAULT_HOST}:{self.port}/"


def probe_server(port=DEFAULT_PORT, timeout_seconds=0.5):
    try:
        with urllib.request.urlopen(
            f"http://{DEFAULT_HOST}:{int(port)}/healthz",
            timeout=max(0.1, float(timeout_seconds)),
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(4096).decode("utf-8"))
        return payload == {"service": "wimi-analytics", "status": "ready"}
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False


def port_is_listening(port=DEFAULT_PORT, timeout_seconds=0.2):
    try:
        with socket.create_connection(
            (DEFAULT_HOST, int(port)), timeout=max(0.05, float(timeout_seconds))
        ):
            return True
    except OSError:
        return False


def _spawn_server(project_root, port):
    project_root = Path(project_root).resolve()
    command = [
        sys.executable,
        "-m",
        "wimi_analytics.server",
        "--host",
        DEFAULT_HOST,
        "--port",
        str(int(port)),
    ]
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        command,
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )


def ensure_server(project_root, port=DEFAULT_PORT, timeout_seconds=5.0):
    port = int(port)
    if port in (1984, 29999) or not 1024 <= port <= 65535:
        raise ValueError("porta reservada ou invalida para o WIMI Analytics")

    with _START_LOCK:
        if probe_server(port):
            return AnalyticsServerHandle(process=None, owned=False, port=port)
        if port_is_listening(port):
            raise RuntimeError(
                f"A porta local {port} esta ocupada por outro servico; nenhum processo foi encerrado."
            )

        process = _spawn_server(project_root, port)
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        while time.monotonic() < deadline:
            if probe_server(port):
                if process.poll() is None:
                    return AnalyticsServerHandle(process=process, owned=True, port=port)
                return AnalyticsServerHandle(process=None, owned=False, port=port)
            if process.poll() is not None:
                break
            time.sleep(0.05)

        handle = AnalyticsServerHandle(process=process, owned=True, port=port)
        stop_owned_server(handle)
        raise RuntimeError("O WIMI Analytics nao ficou pronto dentro do limite seguro.")


def stop_owned_server(handle, timeout_seconds=3.0):
    if not handle or not handle.owned or handle.process is None:
        return False
    process = handle.process
    if process.poll() is not None:
        return True
    process.terminate()
    try:
        process.wait(timeout=max(0.1, float(timeout_seconds)))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)
    return True
