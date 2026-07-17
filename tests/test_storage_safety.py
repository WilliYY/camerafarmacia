import importlib.machinery
import importlib.util
import ast
import contextlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest


def load_manager_copy():
    temp_dir = tempfile.TemporaryDirectory()
    source = Path(__file__).resolve().parents[1] / "gerenciador.pyw"
    copied_source = Path(temp_dir.name) / "gerenciador.pyw"
    shutil.copy2(source, copied_source)

    pil_module = types.ModuleType("PIL")
    pil_module.Image = types.ModuleType("PIL.Image")
    pil_module.ImageTk = types.ModuleType("PIL.ImageTk")
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_module.Image
    sys.modules["PIL.ImageTk"] = pil_module.ImageTk

    loader = importlib.machinery.SourceFileLoader("nvr_manager_test", str(copied_source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return temp_dir, module


class FakeRoot:
    def __init__(self):
        self.quit_called = False
        self.destroy_called = False

    def quit(self):
        self.quit_called = True

    def destroy(self):
        self.destroy_called = True

    def after(self, _delay, callback, *args):
        callback(*args)


class FakeAliveThread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class StorageSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir, cls.module = load_manager_copy()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def new_app(self):
        return self.module.CameraManagerApp.__new__(self.module.CameraManagerApp)

    def test_internal_method_calls_match_declared_arity(self):
        source_path = Path(__file__).resolve().parents[1] / "gerenciador.pyw"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        manager_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "CameraManagerApp"
        )
        methods = {}
        for node in manager_class.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional = node.args.posonlyargs + node.args.args
            if positional and positional[0].arg == "self":
                positional = positional[1:]
            required = len(positional) - len(node.args.defaults)
            methods[node.name] = (required, len(positional), node.args.vararg is not None)

        errors = []
        for call in ast.walk(manager_class):
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
                continue
            if call.func.value.id != "self" or call.func.attr not in methods:
                continue
            if any(isinstance(arg, ast.Starred) for arg in call.args):
                continue
            required, maximum, has_varargs = methods[call.func.attr]
            supplied = len(call.args) + len([kw for kw in call.keywords if kw.arg is not None])
            if supplied < required or (not has_varargs and supplied > maximum):
                errors.append(f"line {call.lineno}: {call.func.attr} received {supplied}, expected {required}..{maximum}")

        self.assertEqual(errors, [])

    def test_atomic_copy_validates_content_and_preserves_conflicts(self):
        app = self.new_app()
        work_dir = Path(self.temp_dir.name) / "atomic"
        work_dir.mkdir(exist_ok=True)
        source = work_dir / "source.ts"
        destination = work_dir / "destination.ts"
        source.write_bytes((b"abc123" * 100000) + b"end")

        self.assertTrue(app.safe_atomic_copy(str(source), str(destination)))
        self.assertEqual(source.read_bytes(), destination.read_bytes())
        self.assertTrue(app.safe_atomic_copy(str(source), str(destination)))

        source.write_bytes(b"X" * destination.stat().st_size)
        with self.assertRaises(Exception):
            app.safe_atomic_copy(str(source), str(destination))
        self.assertNotEqual(source.read_bytes(), destination.read_bytes())

    def test_local_reserve_never_deletes_pending_video(self):
        backup_dir = Path(self.temp_dir.name) / "backup"
        backup_dir.mkdir(exist_ok=True)
        pending = backup_dir / "pending.ts"
        pending.write_bytes(b"pending-video")

        status = self.module.garantir_limite_backup_local(
            str(backup_dir),
            min_free_bytes=10 ** 30,
        )
        self.assertFalse(status["ok"])
        self.assertEqual(pending.read_bytes(), b"pending-video")

    def test_unknown_battery_value_remains_unsigned(self):
        status = self.module.SYSTEM_POWER_STATUS()
        status.ACLineStatus = 255
        status.BatteryLifePercent = 255
        self.assertEqual(status.ACLineStatus, 255)
        self.assertEqual(status.BatteryLifePercent, 255)

    def test_silent_logs_are_persistent_and_memory_bounded(self):
        app = self.new_app()
        app.silent = True
        app._log_lock = threading.Lock()
        app._ui_log_queue = self.module.queue.Queue(maxsize=1000)
        self.module.GDRIVE_ROOT = ""

        with contextlib.redirect_stdout(io.StringIO()):
            for index in range(self.module.STARTUP_LOG_LIMIT + 25):
                app.add_log(f"unique health event {index}")

        self.assertEqual(len(app._startup_logs), self.module.STARTUP_LOG_LIMIT)
        local_logs = list(Path(self.module.LOGS_DIR).glob("log_*.txt"))
        self.assertTrue(local_logs)
        self.assertIn("unique health event", local_logs[0].read_text(encoding="utf-8"))

    def test_worker_log_is_queued_instead_of_touching_tk(self):
        app = self.new_app()
        app.silent = False
        app.txt_log = object()
        app._log_lock = threading.Lock()
        app._ui_log_queue = self.module.queue.Queue(maxsize=10)
        self.module.GDRIVE_ROOT = ""

        worker = threading.Thread(target=lambda: app.add_log("worker message"))
        worker.start()
        worker.join()

        message, tag = app._ui_log_queue.get_nowait()
        self.assertEqual(message, "worker message")
        self.assertTrue(tag.startswith("tag_"))

    def test_startup_cleanup_preserves_nonempty_video(self):
        app = self.new_app()
        app.silent = True
        temp_root = Path(self.module.PROJ_DIR) / "sistema" / "gravando_temp" / "cam"
        temp_root.mkdir(parents=True, exist_ok=True)
        video = temp_root / "temp_camera_test.ts"
        empty = temp_root / "empty.tmp"
        video.write_bytes(b"recoverable-video")
        empty.write_bytes(b"")

        app.limpar_arquivos_temporarios_orfaos()

        self.assertEqual(video.read_bytes(), b"recoverable-video")
        self.assertFalse(empty.exists())

    def test_destination_switches_between_backup_and_hd(self):
        app = self.new_app()
        app.recording_destinations = {}
        logs = []
        hd_dir = str(Path(self.temp_dir.name) / "external" / "camera 1")
        original_status = self.module.garantir_limite_backup_local
        self.module.garantir_limite_backup_local = lambda _path: {
            "ok": True,
            "free_bytes": 20 * 1024 ** 3,
            "reserve_bytes": 5 * 1024 ** 3,
        }
        app.get_gdrive_dir = lambda _stream, _index: hd_dir

        try:
            app.storage_path_is_writable = lambda path: path != hd_dir
            selected, heartbeat = app.select_recording_destination("cam", 0, logs.append)
            self.assertIn("backup_gravacoes", selected)
            self.assertEqual(heartbeat, "")

            app.storage_path_is_writable = lambda _path: True
            selected, heartbeat = app.select_recording_destination("cam", 0, logs.append)
            self.assertEqual(selected, hd_dir)
            self.assertEqual(heartbeat, hd_dir)
            self.assertTrue(any("disponivel novamente" in message for message in logs))
        finally:
            self.module.garantir_limite_backup_local = original_status

    def test_stop_sequence_waits_for_local_recording_thread(self):
        app = self.new_app()
        app.streams = ["cam"]
        app.recording_active = {"cam": True}
        app.active_connections = {}
        app._stop_lock = threading.Lock()
        app.silent = True
        finished = threading.Event()

        def recorder():
            while app.recording_active["cam"]:
                time.sleep(0.01)
            time.sleep(0.05)
            finished.set()

        thread = threading.Thread(target=recorder)
        app.recording_threads = {"cam": thread}
        app.is_pid_running_and_python = lambda _pid: False
        app.limpar_processos_ffmpeg_zumbis = lambda sync=False: None
        original_run = self.module.subprocess.run
        self.module.subprocess.run = lambda *args, **kwargs: None
        thread.start()
        try:
            app.run_stop_sequence()
        finally:
            self.module.subprocess.run = original_run

        self.assertTrue(finished.is_set())
        self.assertFalse(thread.is_alive())

    def test_quarantine_stays_on_source_disk(self):
        app = self.new_app()
        hd_root = Path(self.temp_dir.name) / "hd"
        source = hd_root / "camera 1" / "2026-07-17" / "bad.ts"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"suspect")
        self.module.GDRIVE_ROOT = str(hd_root)

        destination = Path(app.mover_video_corrompido_para_quarentena(str(source)))
        self.assertTrue(destination.exists())
        self.assertEqual(os.path.commonpath([str(destination), str(hd_root)]), str(hd_root))
        self.assertIn(".quarentena_corrompidos", destination.parts)

    def test_scanner_requires_two_failures_before_quarantine(self):
        app = self.new_app()
        app.streams = ["cam"]
        app.silent = True
        app._scan_lock = threading.Lock()
        app._scan_state_path = str(Path(self.temp_dir.name) / "scan_state.json")
        app.get_gdrive_dir = lambda _stream, _index: ""
        app.rotacionar_videos_hd = lambda _root: None
        self.module.GDRIVE_ROOT = ""

        ffmpeg = Path(self.module.PROJ_DIR) / "sistema" / "go2rtc" / "ffmpeg.exe"
        ffmpeg.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.write_bytes(b"test")
        source = (
            Path(self.module.PROJ_DIR)
            / "sistema"
            / "backup_gravacoes"
            / "cam"
            / "2026-07-17"
            / "suspect.ts"
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"not-a-video")
        old_time = time.time() - 600
        os.utime(source, (old_time, old_time))

        original_run = self.module.subprocess.run
        self.module.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(
            returncode=1,
            stderr="invalid media",
        )
        try:
            app.escanear_videos_corrompidos_thread(show_popup=False)
            self.assertTrue(source.exists())
            app.escanear_videos_corrompidos_thread(show_popup=False)
        finally:
            self.module.subprocess.run = original_run

        self.assertFalse(source.exists())
        quarantine = Path(self.module.PROJ_DIR) / "sistema" / "quarentena_corrompidos"
        self.assertTrue(any(path.name == "suspect.ts" for path in quarantine.rglob("*.ts")))

    def test_health_snapshot_correlates_recording_and_hardware_state(self):
        app = self.new_app()
        hd_root = Path(self.temp_dir.name) / "health-hd"
        recording_dir = hd_root / "camera 1" / "2026-07-17"
        recording_dir.mkdir(parents=True)
        recent_video = recording_dir / "recent.ts"
        recent_video.write_bytes(b"video")
        os.utime(recent_video, None)
        self.module.GDRIVE_ROOT = str(hd_root)

        app.streams = ["cam"]
        app.recording_active = {"cam": True}
        app.recording_threads = {"cam": FakeAliveThread(True)}
        app.recording_destinations = {"cam": "hd"}
        app.recording_started_at = {"cam": time.time() - 3600}
        app.reconnect_failures = {"cam": 0}
        app._last_go2rtc_ok = True
        app.go2rtc_restart_count = 0
        app._smart_snapshot = {"status": "ok", "drives": [], "error": None}
        app._power_snapshot = {"status": "ac", "battery_percent": 100}
        app.get_process_memory_mb = lambda: 100.0
        app.get_pending_backup_details = lambda: {
            "count": 0,
            "size_bytes": 0,
            "oldest_mtime": None,
            "truncated": False,
        }
        app.get_stale_storage_artifacts = lambda: []
        app.scan_recent_kernel_144_reports = lambda: {
            "status": "ok",
            "count_24h": 0,
            "latest": None,
        }

        healthy = app.collect_health_snapshot()
        self.assertEqual(healthy["overall_status"], "healthy")

        app.recording_threads["cam"] = FakeAliveThread(False)
        app.reconnect_failures["cam"] = 7
        critical = app.collect_health_snapshot()
        codes = {issue["code"] for issue in critical["issues"]}
        self.assertEqual(critical["overall_status"], "critical")
        self.assertIn("RECORDING_THREAD_DEAD", codes)
        self.assertIn("RECONNECT_STORM", codes)

    def test_go2rtc_probe_reports_names_without_payload_secrets(self):
        app = self.new_app()

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"farmacia": {"url": "tuya://host?password=secret"}}'

        original_urlopen = self.module.urllib.request.urlopen
        self.module.urllib.request.urlopen = lambda *_args, **_kwargs: FakeResponse()
        try:
            result = app.probe_go2rtc_api()
        finally:
            self.module.urllib.request.urlopen = original_urlopen

        self.assertEqual(result["streams"], ["farmacia"])
        self.assertNotIn("secret", str(result))

    def test_legacy_merge_keeps_different_same_named_files(self):
        app = self.new_app()
        app.silent = True
        hd_root = Path(self.temp_dir.name) / "legacy-hd"
        old_dir = hd_root / "CAMERA 1 FARMACIA" / "2026-07-17"
        new_dir = hd_root / "camera 1" / "2026-07-17"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        (old_dir / "same.ts").write_bytes(b"old-content")
        (new_dir / "same.ts").write_bytes(b"new-content")
        self.module.GDRIVE_ROOT = str(hd_root)

        app.limpar_e_fundir_pastas_legadas()

        contents = {path.read_bytes() for path in new_dir.glob("same*.ts")}
        self.assertEqual(contents, {b"old-content", b"new-content"})

    def test_silent_shutdown_closes_tk_root(self):
        app = self.new_app()
        app.root = FakeRoot()
        app.silent = True
        app.running_monitor = True
        app.running_sync = True
        app.apply_prevent_sleep = lambda _enabled: None
        app.run_stop_sequence = lambda: None
        app.limpar_processos_ffmpeg_zumbis = lambda sync=False: None

        app.graceful_shutdown()

        self.assertTrue(app.root.quit_called)
        self.assertTrue(app.root.destroy_called)


if __name__ == "__main__":
    unittest.main()
