import importlib.machinery
import importlib.util
import ast
import base64
import contextlib
from datetime import datetime
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from unittest.mock import patch


def load_manager_copy():
    temp_dir = tempfile.TemporaryDirectory()
    source = Path(__file__).resolve().parents[1] / "gerenciador.pyw"
    copied_source = Path(temp_dir.name) / "gerenciador.pyw"
    shutil.copy2(source, copied_source)
    source_player = source.parent / "sistema" / "viewer_assets" / "video-rtc.js"
    copied_player = Path(temp_dir.name) / "sistema" / "viewer_assets" / "video-rtc.js"
    copied_player.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_player, copied_player)

    pil_module = types.ModuleType("PIL")
    pil_module.Image = types.ModuleType("PIL.Image")
    pil_module.ImageTk = types.ModuleType("PIL.ImageTk")
    pil_module.ImageDraw = types.ModuleType("PIL.ImageDraw")
    pil_module.ImageFont = types.ModuleType("PIL.ImageFont")
    fake_modules = {
        "PIL": pil_module,
        "PIL.Image": pil_module.Image,
        "PIL.ImageTk": pil_module.ImageTk,
        "PIL.ImageDraw": pil_module.ImageDraw,
        "PIL.ImageFont": pil_module.ImageFont,
    }
    previous_modules = {name: sys.modules.get(name) for name in fake_modules}
    try:
        sys.modules.update(fake_modules)
        loader = importlib.machinery.SourceFileLoader("nvr_manager_test", str(copied_source))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
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

    def test_producer_requires_real_media_evidence(self):
        self.assertFalse(self.module.producer_has_media_evidence({"url": "local-source"}))
        self.assertFalse(self.module.producer_has_media_evidence({"error": "offline"}))
        self.assertTrue(self.module.producer_has_media_evidence({"medias": [{"kind": "video"}]}))
        self.assertTrue(self.module.producer_has_media_evidence({"receivers": [{"id": 1}]}))

    def test_camera_connectivity_uses_recording_data_and_timeouts(self):
        now = 10_000.0
        fresh = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=True,
            recording_active=True,
            recording_started_at=now - 600,
            last_data_at=now - 2,
            now=now,
        )
        stale = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=True,
            recording_active=True,
            recording_started_at=now - 600,
            last_data_at=now - 40,
            now=now,
        )
        offline = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=True,
            recording_active=True,
            recording_started_at=now - 600,
            last_data_at=now - 120,
            now=now,
        )

        self.assertEqual(fresh["status"], "online")
        self.assertEqual(stale["status"], "reconnecting")
        self.assertEqual(offline["status"], "offline")
        self.assertEqual(offline["reason"], "recording_data_timeout")

    def test_camera_connectivity_has_startup_grace_and_recovery_hysteresis(self):
        now = 20_000.0
        starting = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=False,
            recording_active=True,
            recording_started_at=now - 10,
            now=now,
        )
        first_recovery = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=True,
            success_samples=1,
            previous_status="offline",
            now=now,
        )
        confirmed_recovery = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=True,
            success_samples=2,
            previous_status="offline",
            now=now,
        )

        self.assertEqual(starting["status"], "connecting")
        self.assertEqual(first_recovery["status"], "reconnecting")
        self.assertEqual(confirmed_recovery["status"], "online")

    def test_camera_connectivity_marks_persistent_missing_producer_offline(self):
        result = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=False,
            preview_active=True,
            missing_samples=self.module.CAMERA_SIGNAL_OFFLINE_SAMPLES,
            now=30_000.0,
        )
        bridge_down = self.module.classify_camera_connectivity(
            go2rtc_ok=False,
            producer_active=True,
            now=30_000.0,
        )

        self.assertEqual(result["status"], "offline")
        self.assertEqual(result["reason"], "producer_without_media")
        self.assertEqual(bridge_down["status"], "offline")
        self.assertEqual(bridge_down["reason"], "go2rtc_offline")

    def test_camera_connectivity_does_not_claim_offline_without_active_probe(self):
        result = self.module.classify_camera_connectivity(
            go2rtc_ok=True,
            producer_active=False,
            recording_active=False,
            preview_active=False,
            missing_samples=self.module.CAMERA_SIGNAL_OFFLINE_SAMPLES * 3,
            now=40_000.0,
        )

        self.assertEqual(result["status"], "standby")
        self.assertEqual(result["reason"], "no_active_media_probe")

    def test_camera_state_tracks_duration_and_confirmed_recovery(self):
        offline = self.module.enrich_camera_connectivity_state(
            {"status": "offline", "status_since": 100.0},
            {"status": "offline", "reason": "recording_without_data"},
            now=200.0,
        )
        recovering = self.module.enrich_camera_connectivity_state(
            offline,
            {"status": "reconnecting", "reason": "recovery_confirmation"},
            now=205.0,
        )
        online = self.module.enrich_camera_connectivity_state(
            recovering,
            {"status": "online", "reason": "recording_data_recent"},
            now=211.0,
        )

        self.assertEqual(offline["status_since"], 100.0)
        self.assertEqual(recovering["status_since"], 205.0)
        self.assertEqual(online["status_since"], 211.0)
        self.assertEqual(online["last_recovered_at"], 211.0)

    def test_camera_activity_text_explains_media_and_automatic_recovery(self):
        offline_text = self.module.format_camera_activity(
            {
                "connectivity": {
                    "status": "offline",
                    "status_since": 100.0,
                    "last_data_age_seconds": None,
                },
            },
            now=3_700.0,
        )
        online_text = self.module.format_camera_activity(
            {
                "connectivity": {
                    "status": "online",
                    "status_since": 3_690.0,
                    "last_data_age_seconds": 1.2,
                },
            },
            now=3_700.0,
        )

        self.assertIn("Sem mídia há 1h", offline_text)
        self.assertIn("reconexão automática ativa", offline_text)
        self.assertEqual(online_text, "Mídia recebida agora")

    def test_camera_signal_samples_are_bounded_for_long_running_use(self):
        cap = self.module.CAMERA_SIGNAL_COUNTER_CAP
        samples = {"missing": cap, "success": 0}
        self.module.update_camera_signal_samples(
            samples,
            positive_sample=False,
            observation_active=True,
        )
        self.assertEqual(samples, {"missing": cap, "success": 0})

        samples = {"missing": 0, "success": cap}
        self.module.update_camera_signal_samples(
            samples,
            positive_sample=True,
            observation_active=True,
        )
        self.assertEqual(samples, {"missing": 0, "success": cap})

    def test_recording_retry_backoff_is_bounded_and_resets_after_data(self):
        self.assertEqual(self.module.next_recording_retry_delay(2.0), 4.0)
        self.assertEqual(self.module.next_recording_retry_delay(4.0), 8.0)
        self.assertEqual(self.module.next_recording_retry_delay(8.0), 15.0)
        self.assertEqual(self.module.next_recording_retry_delay(15.0), 15.0)
        self.assertEqual(
            self.module.next_recording_retry_delay(15.0, received_data=True),
            2.0,
        )

    def test_health_collection_time_is_human_readable_and_safe(self):
        self.assertEqual(
            self.module.format_health_collection_time("2026-08-04T09:37:37"),
            "09:37:37",
        )
        self.assertEqual(
            self.module.format_health_collection_time("invalid"),
            "horário indisponível",
        )

    def test_recording_overview_requires_recent_camera_data(self):
        cam_states = {
            "cam_ok": {
                "grav_ok": True,
                "connectivity": {"status": "online"},
            },
            "cam_offline": {
                "grav_ok": True,
                "connectivity": {"status": "offline"},
            },
        }

        overview = self.module.summarize_recording_coverage(cam_states)
        offline_badge = self.module.camera_recording_display(cam_states["cam_offline"])

        self.assertEqual(overview["active_count"], 2)
        self.assertEqual(overview["verified_count"], 1)
        self.assertEqual(overview["level"], "warning")
        self.assertEqual(overview["label"], "NVR: GRAVANDO 1/2")
        self.assertEqual(offline_badge, ("SEM DADOS", "error"))

    def test_recording_overview_distinguishes_stopped_and_fully_healthy(self):
        stopped = self.module.summarize_recording_coverage({
            "cam": {"grav_ok": False, "connectivity": {"status": "standby"}},
        })
        healthy = self.module.summarize_recording_coverage({
            "cam": {"grav_ok": True, "connectivity": {"status": "online"}},
        })

        self.assertEqual((stopped["label"], stopped["level"]), ("NVR STATUS: PARADO", "error"))
        self.assertEqual((healthy["label"], healthy["level"]), ("NVR: GRAVANDO 1/1", "ok"))

    def test_active_offline_camera_becomes_health_issue(self):
        issues = self.module.active_camera_connectivity_issues(
            ["cam_offline", "cam_online"],
            {
                "cam_offline": {"status": "offline", "reason": "recording_without_data"},
                "cam_online": {"status": "online", "reason": "recording_data_recent"},
            },
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], "CAMERA_OFFLINE")
        self.assertEqual(issues[0]["severity"], "critical")
        self.assertEqual(issues[0]["stream"], "cam_offline")
        self.assertIn("recording_without_data", issues[0]["evidence"])

    def test_camera_transition_invalidates_cached_health_assessment(self):
        app = self.new_app()
        app.camera_connectivity_states = {"cam": {"status": "reconnecting"}}
        app.camera_signal_samples = {"cam": {"missing": 9, "success": 0}}
        app.recording_active = {"cam": True}
        app.stream_last_data_at = {}
        app.recording_started_at = {"cam": time.time() - 120}
        app.reconnect_failures = {"cam": 3}
        app.camera_widgets = {}
        app._last_health_check = time.time()
        app.add_log = lambda *_args, **_kwargs: None

        result = app.evaluate_camera_connectivity(True, "cam", "Conectando...")

        self.assertEqual(result["status"], "offline")
        self.assertEqual(app._last_health_check, 0.0)

    def test_last_recording_scan_is_cached_per_camera_path(self):
        app = self.new_app()
        app._last_recording_cache = {}
        scan_calls = []

        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            def fake_scan(read_path):
                scan_calls.append(read_path)
                return os.path.join(read_path, "camera_2026-08-04.ts"), time.time()

            app.scan_latest_recording = fake_scan
            first = app.check_last_recording(True, first_dir, "cam")
            second = app.check_last_recording(True, first_dir, "cam")
            app.check_last_recording(True, second_dir, "cam")

        self.assertEqual(first, second)
        self.assertIn("camera_2026-08-04.ts", first)
        self.assertEqual(len(scan_calls), 2)

    def test_log_tail_reader_is_bounded_to_recent_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "camera.log"
            log_path.write_text(
                "[ERRO_DUPLICADO] antigo\n" + ("linha comum\n" * 3000),
                encoding="utf-8",
            )

            recent_lines = self.module.read_log_tail_lines(log_path, max_bytes=4096)
            self.assertFalse(any("[ERRO_DUPLICADO]" in line for line in recent_lines))

            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write("[ERRO_DUPLICADO] recente\n")

            recent_lines = self.module.read_log_tail_lines(log_path, max_bytes=4096)
            self.assertTrue(any("[ERRO_DUPLICADO] recente" in line for line in recent_lines))

    def test_log_dedup_state_is_bounded(self):
        last_logged = {f"msg-{index}": float(index) for index in range(700)}
        suppressed = {key: 1 for key in last_logged}

        self.module.prune_log_dedup_state(
            last_logged,
            suppressed,
            now=10_000.0,
            max_entries=500,
            max_age_seconds=20_000.0,
        )

        self.assertEqual(len(last_logged), 500)
        self.assertEqual(set(suppressed), set(last_logged))
        self.assertNotIn("msg-0", last_logged)
        self.assertIn("msg-699", last_logged)

    def test_rtsp_signal_rejects_configured_but_inactive_producer(self):
        app = self.new_app()
        app.get_cached_streams_data = lambda: {
            "cam": {"producers": [{"url": "local-source"}]},
        }
        self.assertEqual(app.check_rtsp_stream(True, "cam"), "Conectando...")

        app.get_cached_streams_data = lambda: {
            "cam": {"producers": [{"medias": [{"kind": "video"}]}]},
        }
        self.assertEqual(app.check_rtsp_stream(True, "cam"), "Sinal OK")

    def test_recording_retry_is_bounded_and_heartbeat_is_throttled(self):
        app = self.new_app()
        app.recording_active = {"cam": True}
        started_at = time.monotonic()
        self.assertTrue(app.wait_for_recording_retry("cam", 0.02))
        self.assertGreaterEqual(time.monotonic() - started_at, 0.015)

        app.recording_active = {"cam": False}
        self.assertFalse(app.wait_for_recording_retry("cam", 0.01))

        source = inspect.getsource(self.module.CameraManagerApp.gravar_bloco_cam)
        self.assertIn("if now_ts - last_heartbeat_time >= 30:", source)
        self.assertIn("if not received_data_this_connection:", source)
        self.assertIn("self.wait_for_recording_retry(stream_name, retry_delay)", source)
        self.assertIn("next_recording_retry_delay(retry_delay)", source)

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

    def test_local_reserve_defaults_to_larger_of_twenty_gb_or_ten_percent(self):
        gib = 1024 ** 3

        self.assertEqual(
            self.module.calculate_local_storage_reserve_bytes(100 * gib),
            20 * gib,
        )
        self.assertEqual(
            self.module.calculate_local_storage_reserve_bytes(1000 * gib),
            100 * gib,
        )
        self.assertEqual(
            self.module.calculate_local_storage_reserve_bytes(1000 * gib, configured_gb=40),
            40 * gib,
        )

    def test_storage_folder_map_survives_stream_reordering(self):
        original = self.module.normalize_storage_folder_map(
            {},
            ["farmacia", "farmacia2"],
        )
        reordered = self.module.normalize_storage_folder_map(
            original,
            ["farmacia2", "farmacia"],
        )

        self.assertEqual(original["farmacia"], "camera 1")
        self.assertEqual(original["farmacia2"], "camera 2")
        self.assertEqual(reordered, original)

    def test_kernel_report_filter_uses_dump_timestamp_and_deduplicates_reprocessing(self):
        now = datetime(2026, 7, 25, 18, 0, 0)
        stamps = [
            "20260718-2129",
            "20260718-2129",
            "20260725-1730",
            "invalid",
        ]

        reports = self.module.filter_kernel_144_dump_stamps(stamps, now, hours=24)

        self.assertEqual(reports, ["20260725-1730"])

    def test_unknown_battery_value_remains_unsigned(self):
        status = self.module.SYSTEM_POWER_STATUS()
        status.ACLineStatus = 255
        status.BatteryLifePercent = 255
        self.assertEqual(status.ACLineStatus, 255)
        self.assertEqual(status.BatteryLifePercent, 255)

    def test_intelligence_correlates_new_usb_failure_with_missing_hd(self):
        snapshot = {
            "issues": [
                {"code": "KERNEL_144_NEW_SESSION", "severity": "critical"},
                {"code": "HD_UNAVAILABLE", "severity": "warning"},
            ],
            "metrics": {"active_streams": ["cam"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["root_cause"], "usb_storage_instability")
        self.assertFalse(result["hardware_protection"]["heavy_maintenance_allowed"])
        self.assertEqual(result["hardware_protection"]["recording_recommendation"], "safe_stop")

    def test_intelligence_distinguishes_usb_history_from_new_failure(self):
        snapshot = {
            "issues": [{"code": "KERNEL_144_REPORTS", "severity": "critical"}],
            "metrics": {"active_streams": ["cam"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["root_cause"], "usb_history")
        self.assertIn("sem falha nova", result["headline"].lower())

    def test_kernel_baseline_waits_for_first_reliable_scan(self):
        app = self.new_app()
        app._kernel_144_session_baseline = None

        unknown = app.add_kernel_session_context({
            "status": "unknown",
            "count_24h": 0,
            "latest": None,
        })
        first_reliable = app.add_kernel_session_context({
            "status": "ok",
            "count_24h": 5,
            "latest": "2026-07-17T09:34:21",
        })
        later_failure = app.add_kernel_session_context({
            "status": "ok",
            "count_24h": 6,
            "latest": "2026-07-17T13:00:00",
        })

        self.assertEqual(unknown["new_in_session"], 0)
        self.assertEqual(first_reliable["new_in_session"], 0)
        self.assertEqual(later_failure["new_in_session"], 1)

    def test_intelligence_localizes_single_camera_without_data(self):
        snapshot = {
            "issues": [
                {"code": "STREAM_NO_DATA", "severity": "warning", "stream": "cam1"},
            ],
            "metrics": {"active_streams": ["cam1", "cam2"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "single_camera_path")
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["affected_streams"], ["cam1"])
        self.assertTrue(result["hardware_protection"]["heavy_maintenance_allowed"])

    def test_intelligence_marks_confirmed_single_camera_outage_critical(self):
        snapshot = {
            "issues": [
                {"code": "STREAM_NO_DATA", "severity": "critical", "stream": "cam1"},
            ],
            "metrics": {"active_streams": ["cam1", "cam2"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "single_camera_path")
        self.assertEqual(result["status"], "critical")
        self.assertIn("offline", result["headline"].lower())

    def test_intelligence_correlates_go2rtc_failure_with_all_missing_data(self):
        snapshot = {
            "issues": [
                {"code": "GO2RTC_UNAVAILABLE", "severity": "critical"},
                {"code": "STREAM_NO_DATA", "severity": "critical", "stream": "cam1"},
                {"code": "STREAM_NO_DATA", "severity": "critical", "stream": "cam2"},
            ],
            "metrics": {"active_streams": ["cam1", "cam2"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "video_bridge_failure")
        self.assertEqual(result["status"], "critical")

    def test_intelligence_recognizes_upstream_outage_when_bridge_is_available(self):
        snapshot = {
            "issues": [
                {"code": "STREAM_NO_DATA", "severity": "critical", "stream": "cam1"},
                {"code": "STREAM_NO_DATA", "severity": "critical", "stream": "cam2"},
            ],
            "metrics": {"active_streams": ["cam1", "cam2"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "upstream_video_outage")
        self.assertEqual(result["affected_streams"], ["cam1", "cam2"])

    def test_intelligence_prioritizes_dead_recorder_over_missing_data(self):
        snapshot = {
            "issues": [
                {"code": "RECORDING_THREAD_DEAD", "severity": "critical", "stream": "cam1"},
                {"code": "STREAM_NO_DATA", "severity": "critical", "stream": "cam1"},
            ],
            "metrics": {"active_streams": ["cam1", "cam2"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "recording_worker_failure")
        self.assertEqual(result["status"], "critical")

    def test_intelligence_prioritizes_local_fallback_pressure(self):
        snapshot = {
            "issues": [
                {"code": "HD_UNAVAILABLE", "severity": "warning"},
                {"code": "LOCAL_SPACE_CRITICAL", "severity": "critical"},
                {"code": "BACKUP_PENDING", "severity": "warning"},
            ],
            "metrics": {"active_streams": ["cam"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "local_fallback_pressure")
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["hardware_protection"]["recording_recommendation"], "safe_stop")

    def test_intelligence_does_not_let_video_symptom_override_disk_protection(self):
        snapshot = {
            "issues": [
                {"code": "STREAM_NO_DATA", "severity": "warning", "stream": "cam1"},
                {"code": "LOCAL_SPACE_CRITICAL", "severity": "critical"},
                {"code": "BACKUP_PENDING", "severity": "warning"},
            ],
            "metrics": {"active_streams": ["cam1", "cam2"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "local_fallback_pressure")
        self.assertFalse(result["hardware_protection"]["heavy_maintenance_allowed"])

    def test_intelligence_applies_direct_smart_and_power_protection(self):
        cases = [
            ("SMART_DEGRADED", "critical", "physical_disk_degradation", "safe_stop"),
            ("POWER_ON_BATTERY", "critical", "power_instability", "safe_stop"),
        ]
        for code, severity, expected_cause, expected_recommendation in cases:
            with self.subTest(code=code):
                result = self.module.build_operational_intelligence({
                    "issues": [{"code": code, "severity": severity}],
                    "metrics": {"active_streams": ["cam"]},
                })

                self.assertEqual(result["root_cause"], expected_cause)
                self.assertFalse(result["hardware_protection"]["heavy_maintenance_allowed"])
                self.assertEqual(
                    result["hardware_protection"]["recording_recommendation"],
                    expected_recommendation,
                )

    def test_intelligence_does_not_hide_active_issue_behind_usb_history(self):
        snapshot = {
            "issues": [
                {"code": "KERNEL_144_REPORTS", "severity": "critical"},
                {
                    "code": "BACKUP_PENDING",
                    "severity": "warning",
                    "summary": "Backups aguardando sincronizacao.",
                    "evidence": "2 arquivos.",
                    "action": "Restabelecer o HD.",
                },
            ],
            "metrics": {"active_streams": ["cam"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "backup_pending")
        self.assertEqual(result["status"], "attention")
        self.assertFalse(result["hardware_protection"]["heavy_maintenance_allowed"])

    def test_intelligence_blocks_heavy_scan_for_critical_memory(self):
        snapshot = {
            "issues": [{"code": "PROCESS_MEMORY_HIGH", "severity": "critical"}],
            "metrics": {"active_streams": ["cam"]},
        }

        result = self.module.build_operational_intelligence(snapshot)

        self.assertEqual(result["root_cause"], "process_resource_growth")
        self.assertEqual(result["status"], "critical")
        self.assertFalse(result["hardware_protection"]["heavy_maintenance_allowed"])

    def test_resource_trend_detects_growth_without_unbounded_history(self):
        app = self.new_app()
        now = time.time()
        app._resource_samples = [
            {
                "timestamp": now - 3600 + (index * 20),
                "memory_mb": 100.0 + index,
                "thread_count": 10,
            }
            for index in range(150)
        ]

        trend = app.update_resource_trend(now, 350.0, 20)

        self.assertGreater(trend["memory_growth_mb"], 100.0)
        self.assertEqual(trend["thread_growth"], 10)
        self.assertEqual(len(app._resource_samples), 120)

    def test_smoke_test_duration_is_bounded(self):
        self.assertEqual(self.module.normalize_smoke_test_seconds(0), 0)
        self.assertEqual(self.module.normalize_smoke_test_seconds(30), 30)
        self.assertEqual(self.module.normalize_smoke_test_seconds(1800), 1800)
        with self.assertRaises(ValueError):
            self.module.normalize_smoke_test_seconds(29)
        with self.assertRaises(ValueError):
            self.module.normalize_smoke_test_seconds(1801)

    def test_stream_config_rejects_yaml_injection_and_unknown_protocols(self):
        streams = self.module.normalize_streams_config({
            "farmacia": "tuya://camera.example/?token=ok",
            "camera\ninjetada": "rtsp://camera.example/live",
            "camera2": "rtsp://camera.example/live\napi: aberto",
            "camera3": "file:///C:/video.ts",
        })

        self.assertEqual(streams, {"farmacia": "tuya://camera.example/?token=ok"})

    def test_storage_identity_requires_the_expected_volume_serial(self):
        path = Path(self.temp_dir.name) / "external" / "camera"
        path.mkdir(parents=True)
        original_identity = self.module.get_volume_identity
        self.module.get_volume_identity = lambda _path: {"serial": "A1B2C3D4", "label": "FARMACIA"}
        try:
            self.assertTrue(self.module.storage_path_matches_identity(
                str(path), {"serial": "A1B2C3D4", "label": "FARMACIA"}
            ))
            self.assertFalse(self.module.storage_path_matches_identity(
                str(path), {"serial": "00000000", "label": "FARMACIA"}
            ))
        finally:
            self.module.get_volume_identity = original_identity

    def test_generated_go2rtc_config_is_authenticated_and_serves_only_public_viewer(self):
        source_viewer = Path(self.module.PROJ_DIR) / "sistema" / "visualizador.html"
        source_viewer.parent.mkdir(parents=True, exist_ok=True)
        source_viewer.write_text("<html>viewer</html>", encoding="utf-8")
        original_config = self.module.CONFIG
        self.module.CONFIG = {
            "streams": {"farmacia": "tuya://camera.example/?token=ok"},
            "web_auth": {"username": "viewer", "password": "senha-segura-123456"},
        }
        try:
            self.assertTrue(self.module.atualizar_go2rtc_yaml(self.module.PROJ_DIR))
        finally:
            self.module.CONFIG = original_config

        yaml_path = Path(self.module.PROJ_DIR) / "sistema" / "go2rtc" / "go2rtc.yaml"
        public_viewer = Path(self.module.PROJ_DIR) / "sistema" / "web" / "visualizador.html"
        public_index = Path(self.module.PROJ_DIR) / "sistema" / "web" / "index.html"
        public_player = Path(self.module.PROJ_DIR) / "sistema" / "web" / "video-rtc.js"
        yaml_text = yaml_path.read_text(encoding="utf-8")
        self.assertIn('static_dir: "../web"', yaml_text)
        self.assertIn('username: "viewer"', yaml_text)
        self.assertIn('password: "senha-segura-123456"', yaml_text)
        self.assertIn('ffmpeg:farmacia#video=mjpeg', yaml_text)
        self.assertNotIn('ffmpeg:farmacia#video=mjpeg#hardware', yaml_text)
        self.assertIn('modules: [api, rtsp, webrtc, exec, ffmpeg, mjpeg, mpegts, mp4, hls, tuya]', yaml_text)
        self.assertNotIn(', mpeg,', yaml_text)
        self.assertIn('/api/stream.ts', yaml_text)
        self.assertIn('/api/stream.mjpeg', yaml_text)
        self.assertIn('/api/ws', yaml_text)
        self.assertIn('allow_paths: [/, /api/streams', yaml_text)
        self.assertNotIn('/video.html', yaml_text)
        self.assertNotIn('allow_paths: [/api,', yaml_text)
        self.assertIn('allow_paths: ["', yaml_text)
        self.assertNotIn('allow_paths: [ffmpeg]', yaml_text)
        self.assertEqual(public_viewer.read_text(encoding="utf-8"), "<html>viewer</html>")
        self.assertEqual(public_index.read_text(encoding="utf-8"), "<html>viewer</html>")
        self.assertEqual(
            public_player.read_bytes(),
            (Path(self.module.PROJ_DIR) / "sistema" / "viewer_assets" / "video-rtc.js").read_bytes(),
        )

    def test_dependency_download_rejects_declared_and_streamed_oversize(self):
        destination = Path(self.temp_dir.name) / "dependency.zip.tmp"

        class FakeResponse:
            def __init__(self, chunks, content_length=None):
                self.chunks = list(chunks)
                self.content_length = content_length

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def info(self):
                if self.content_length is None:
                    return {}
                return {"Content-Length": str(self.content_length)}

            def read(self, _size=-1):
                return self.chunks.pop(0) if self.chunks else b""

        original_urlopen = self.module.urllib.request.urlopen
        original_disk_usage = self.module.shutil.disk_usage
        self.module.shutil.disk_usage = lambda _path: (
            100 * 1024 ** 3,
            10 * 1024 ** 3,
            90 * 1024 ** 3,
        )
        try:
            self.module.urllib.request.urlopen = lambda *_args, **_kwargs: FakeResponse(
                [],
                content_length=5,
            )
            with self.assertRaises(ValueError):
                self.module.download_url_to_file_bounded(
                    "https://example.invalid/dependency.zip",
                    str(destination),
                    max_bytes=4,
                    timeout=1,
                )

            self.module.urllib.request.urlopen = lambda *_args, **_kwargs: FakeResponse(
                [b"abc", b"def"],
            )
            with self.assertRaises(ValueError):
                self.module.download_url_to_file_bounded(
                    "https://example.invalid/dependency.zip",
                    str(destination),
                    max_bytes=4,
                    timeout=1,
                )
        finally:
            self.module.urllib.request.urlopen = original_urlopen
            self.module.shutil.disk_usage = original_disk_usage

        self.assertFalse(destination.exists())

    def test_windows_disk_status_identifies_basic_telemetry_source(self):
        app = self.new_app()
        original_check_output = self.module.subprocess.check_output
        self.module.subprocess.check_output = lambda *_args, **_kwargs: (
            '{"Model":"Disk","Status":"OK"}'
        )
        try:
            result = app.query_smart_status()
        finally:
            self.module.subprocess.check_output = original_check_output

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["telemetry_level"], "basic_windows_status")
        self.assertEqual(result["source"], "Win32_DiskDrive.Status")

    def test_viewer_uses_safe_dom_and_validates_server_host(self):
        viewer_path = Path(__file__).resolve().parents[1] / "sistema" / "visualizador.html"
        viewer = viewer_path.read_text(encoding="utf-8")

        self.assertNotIn("innerHTML", viewer)
        self.assertNotIn("onclick=", viewer)
        self.assertNotIn("onchange=", viewer)
        self.assertIn("normalizeServerHost", viewer)
        self.assertIn("replaceChildren", viewer)
        self.assertIn('type="module"', viewer)
        self.assertIn('from "./video-rtc.js"', viewer)
        self.assertIn('document.createElement("video-rtc")', viewer)
        self.assertNotIn("/video.html", viewer)
        self.assertNotIn("<iframe", viewer)
        self.assertIn("Content-Security-Policy", viewer)
        self.assertIn("script-src 'self' 'sha256-", viewer)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", viewer)
        script = re.search(r'<script type="module">(.*?)</script>', viewer, re.DOTALL)
        declared_hash = re.search(r"script-src 'self' 'sha256-([^']+)'", viewer)
        self.assertIsNotNone(script)
        self.assertIsNotNone(declared_hash)
        actual_hash = base64.b64encode(
            hashlib.sha256(script.group(1).encode("utf-8")).digest()
        ).decode("ascii")
        self.assertEqual(declared_hash.group(1), actual_hash)

    def test_copied_web_link_targets_the_allowed_viewer_route(self):
        source = inspect.getsource(self.module.CameraManagerApp.copy_link_to_clipboard)
        self.assertIn("/visualizador.html", source)

    def test_stream_parser_uses_validated_config_without_yaml_quotes(self):
        app = self.new_app()
        original_config = self.module.CONFIG
        self.module.CONFIG = {
            "streams": {
                "farmacia": "tuya://camera.example/?token=ok",
                "farmacia2": "tuya://camera.example/?token=ok2",
            }
        }
        try:
            self.assertEqual(app.parse_streams(), ["farmacia", "farmacia2"])
        finally:
            self.module.CONFIG = original_config

    def test_binary_hash_validation_rejects_unknown_file(self):
        candidate = Path(self.temp_dir.name) / "go2rtc.exe"
        candidate.write_bytes(b"not-the-approved-binary")
        self.assertFalse(self.module.binary_is_trusted(
            str(candidate), self.module.TRUSTED_BINARY_HASHES["go2rtc.exe"]
        ))

    def test_update_payload_requires_preapproved_hashes(self):
        app = self.new_app()
        manager = b'''VERSION = "4.13"\nclass CameraManagerApp:\n    pass\ndef gravar_bloco_cam():\n    pass\ndef safe_atomic_copy():\n    pass\ndef validate_update_payloads():\n    pass\n# --wait-for-pid\nif __name__ == "__main__":\n    pass\n'''
        viewer = b'<html><div class="camera-grid"></div><script>loadActiveStreams()</script></html>'
        hashes = {
            "manager_sha256": hashlib.sha256(manager).hexdigest(),
            "viewer_sha256": hashlib.sha256(viewer).hexdigest(),
        }

        self.assertEqual(
            app.validate_update_payloads("4.13", manager, viewer, hashes),
            hashes["manager_sha256"],
        )
        hashes["viewer_sha256"] = "0" * 64
        with self.assertRaises(Exception):
            app.validate_update_payloads("4.13", manager, viewer, hashes)

    def test_stop_managed_go2rtc_does_not_use_global_process_kill(self):
        app = self.new_app()
        app.go2rtc_api_fails = 2

        class FakeProcess:
            def __init__(self):
                self.running = True
                self.terminated = False

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.terminated = True
                self.running = False

            def wait(self, timeout):
                return 0

        process = FakeProcess()
        app._go2rtc_process = process
        app.stop_managed_go2rtc()

        self.assertTrue(process.terminated)
        self.assertIsNone(app._go2rtc_process)
        self.assertEqual(app.go2rtc_api_fails, 0)

    def test_concurrent_go2rtc_start_creates_only_one_managed_process(self):
        app = self.new_app()
        app.silent = True
        app._lifecycle_lock = threading.RLock()
        app._go2rtc_process = None
        app.go2rtc_restart_count = 0
        app.go2rtc_api_fails = 0
        app.add_log = lambda *_args, **_kwargs: None
        app.probe_go2rtc_api = lambda *_args, **_kwargs: {
            "ok": False,
            "streams": [],
            "error": "offline",
        }

        class FakeProcess:
            def poll(self):
                return None

        starts = []
        original_popen = self.module.subprocess.Popen

        def fake_popen(*_args, **_kwargs):
            starts.append(True)
            time.sleep(0.05)
            return FakeProcess()

        self.module.subprocess.Popen = fake_popen
        try:
            workers = [threading.Thread(target=app.iniciar_go2rtc) for _ in range(5)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        finally:
            self.module.subprocess.Popen = original_popen

        self.assertEqual(len(starts), 1)

    def test_preview_loop_does_not_mutate_go2rtc_watchdog_state(self):
        source = inspect.getsource(self.module.LiveCameraWidget.stream_loop)
        self.assertNotIn("check_process_go2rtc", source)

    def test_preview_applies_transient_identity_overlay_without_new_stream(self):
        stream_source = inspect.getsource(self.module.LiveCameraWidget.stream_loop)
        overlay_source = inspect.getsource(
            self.module.LiveCameraWidget.apply_identity_overlay
        )

        self.assertEqual(stream_source.count("_read_mjpeg_frames"), 1)
        self.assertLess(
            stream_source.index("inspect_preview_frame"),
            stream_source.index("submit_vision_frame"),
        )
        self.assertIn("apply_identity_overlay", stream_source)
        self.assertIn("get_vision_identity_overlay", overlay_source)
        self.assertIn("render_identity_overlay", overlay_source)
        self.assertIn("0.066 if self.expanded else 0.5", stream_source)
        render_branch = stream_source.index(
            "if self.expanded", stream_source.index("submit_vision_frame")
        )
        self.assertLess(
            stream_source.index("submit_vision_frame"),
            render_branch,
        )

    def test_continuous_analysis_keeps_collapsed_recording_preview_running(self):
        widget = self.module.LiveCameraWidget.__new__(
            self.module.LiveCameraWidget
        )
        widget.expanded = True
        widget.continuous_analysis = True
        widget.update_header_text = lambda: None
        widget.pack_configure = lambda **_kwargs: None
        widget.body_frame = types.SimpleNamespace(pack_forget=lambda: None)
        widget._recalc_camera_sizes = lambda: None
        stop_calls = []
        widget.stop_stream = lambda: stop_calls.append(True)

        widget.collapse()

        self.assertFalse(widget.expanded)
        self.assertEqual(stop_calls, [])

    def test_continuous_analysis_starts_and_releases_hidden_preview(self):
        widget = self.module.LiveCameraWidget.__new__(
            self.module.LiveCameraWidget
        )
        widget.expanded = False
        widget.continuous_analysis = False
        starts = []
        stops = []
        widget.start_stream = lambda: starts.append(True)
        widget.stop_stream = lambda: stops.append(True)

        widget.set_continuous_analysis(True)
        widget.set_continuous_analysis(False)

        self.assertEqual(starts, [True])
        self.assertEqual(stops, [True])
        self.assertFalse(widget.continuous_analysis)

    def test_continuous_analysis_does_not_duplicate_live_preview_thread(self):
        widget = self.module.LiveCameraWidget.__new__(
            self.module.LiveCameraWidget
        )
        widget.running = True
        widget.thread = FakeAliveThread()

        self.assertFalse(widget.start_stream())
        self.assertIsInstance(widget.thread, FakeAliveThread)

    def test_recording_state_controls_continuous_camera_analysis(self):
        app = self.new_app()
        app.recording_active = {"cam1": True, "cam2": False}
        calls = {"cam1": [], "cam2": []}
        app.camera_widgets = {
            name: types.SimpleNamespace(
                set_continuous_analysis=lambda enabled, stream=name: calls[
                    stream
                ].append(enabled)
            )
            for name in calls
        }

        app.sync_continuous_vision_streams()

        self.assertEqual(calls["cam1"], [True])
        self.assertEqual(calls["cam2"], [False])

    def test_recording_lifecycle_requests_continuous_analysis_sync(self):
        start_source = inspect.getsource(
            self.module.CameraManagerApp._run_start_sequence_locked
        )
        stop_source = inspect.getsource(
            self.module.CameraManagerApp._run_stop_sequence_locked
        )

        self.assertIn("request_continuous_vision_sync", start_source)
        self.assertIn("request_continuous_vision_sync", stop_source)

    def test_preview_keeps_only_latest_pending_ui_frame(self):
        callbacks = []

        class FakeRoot:
            def after(self, _delay, callback):
                callbacks.append(callback)

            def call(self, *_args):
                return None

        class FakeLabel:
            def __init__(self):
                self.image = None

            def configure(self, **values):
                self.image = values.get("image")

        widget = self.module.LiveCameraWidget.__new__(self.module.LiveCameraWidget)
        widget.app = types.SimpleNamespace(root=FakeRoot())
        widget.video_lbl = FakeLabel()
        widget.running = True
        widget.photo = None
        widget.current_error_msg = ""
        widget.is_online = False
        widget.connectivity_status = "connecting"
        widget.last_frame_at = None
        widget._display_lock = threading.Lock()
        widget._pending_display_image = None
        widget._display_update_scheduled = False
        widget.update_header_text = lambda: None
        first = object()
        latest = object()

        with patch.object(
            self.module.ImageTk,
            "PhotoImage",
            side_effect=lambda image: image,
            create=True,
        ):
            widget.update_image(first)
            widget.update_image(latest)
            self.assertEqual(len(callbacks), 1)
            callbacks.pop()()

        self.assertIs(widget.photo, latest)
        self.assertIs(widget.video_lbl.image, latest)

    def test_analytics_maintenance_forwards_collector_time(self):
        source = inspect.getsource(
            self.module.CameraManagerApp._start_wimi_analytics_worker
        )

        self.assertIn("def run_analytics_maintenance(now=None):", source)
        self.assertIn("evidence_archive.cleanup(now=now)", source)
        self.assertIn("face_service.cleanup_provisional(now=now)", source)
        self.assertIn("sample_interval_seconds=0.5", source)
        self.assertIn("face_interval_seconds=0.5", source)

    def test_stop_sequence_never_kills_external_python_from_stale_lock(self):
        app = self.new_app()
        app.streams = ["cam"]
        app.recording_active = {"cam": False}
        app.recording_threads = {}
        app.active_connections = {}
        app._stop_lock = threading.Lock()
        app._lifecycle_lock = threading.RLock()
        app.silent = True
        app.stop_managed_go2rtc = lambda: None
        app.limpar_processos_ffmpeg_zumbis = lambda sync=False: None
        app.is_pid_running_and_python = lambda _pid: True
        app.is_pid_owned_recorder = lambda _pid, _lock_data=None: True
        lock_path = Path(self.module.LOGS_DIR) / "gravando_cam.lock"
        lock_path.write_text("4242", encoding="utf-8")

        kill_calls = []
        original_kill = self.module.os.kill
        original_sleep = self.module.time.sleep
        self.module.os.kill = lambda pid, signal: kill_calls.append((pid, signal))
        self.module.time.sleep = lambda _seconds: None
        try:
            app.run_stop_sequence()
        finally:
            self.module.os.kill = original_kill
            self.module.time.sleep = original_sleep

        self.assertEqual(kill_calls, [])
        self.assertTrue(lock_path.exists())

    def test_network_heartbeat_is_atomic_owned_and_malformed_recent_lock_fails_closed(self):
        app = self.new_app()
        app._recorder_owner_token = "owner-token"
        app.local_ip = "127.0.0.1"
        heartbeat_dir = Path(self.temp_dir.name) / "heartbeat"
        heartbeat_dir.mkdir(exist_ok=True)

        self.assertTrue(app.atualizar_heartbeat_cam(str(heartbeat_dir), "cam"))
        lock_path = heartbeat_dir / ".active_recorder.json"
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["owner_token"], "owner-token")
        self.assertEqual(payload["stream"], "cam")
        self.assertFalse(list(heartbeat_dir.glob("*.tmp")))

        lock_path.write_text("{", encoding="utf-8")
        conflict = app.verificar_duplicidade_rede_cam(str(heartbeat_dir), "cam")
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["reason"], "unreadable_recent_lock")

    def test_atomic_copy_preserves_completed_temporary_when_publication_fails(self):
        app = self.new_app()
        work_dir = Path(self.temp_dir.name) / "copy-preserve"
        work_dir.mkdir(exist_ok=True)
        source = work_dir / "source.ts"
        destination = work_dir / "destination.ts"
        source.write_bytes(b"recoverable-video" * 1024)

        original_replace = self.module.os.replace
        self.module.os.replace = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("publication failed")
        )
        try:
            with self.assertRaises(OSError):
                app.safe_atomic_copy(str(source), str(destination))
        finally:
            self.module.os.replace = original_replace

        temporary = Path(str(destination) + ".syncing")
        self.assertTrue(temporary.exists())
        self.assertEqual(temporary.read_bytes(), source.read_bytes())

    def test_singleton_uses_exclusive_address_on_windows(self):
        options = []

        class FakeSocket:
            def setsockopt(self, *args):
                options.append(args)

            def bind(self, _address):
                return None

            def listen(self, _backlog):
                return None

        original_socket = self.module.socket.socket
        self.module.socket.socket = lambda *_args, **_kwargs: FakeSocket()
        try:
            self.assertTrue(self.module.garantir_instancia_unica())
        finally:
            self.module.socket.socket = original_socket

        self.assertIn(
            (
                self.module.socket.SOL_SOCKET,
                self.module.socket.SO_EXCLUSIVEADDRUSE,
                1,
            ),
            options,
        )

    def test_silent_power_guard_keeps_system_awake_without_forcing_display(self):
        app = self.new_app()
        app.silent = True
        requested_states = []
        original_windll = self.module.ctypes.windll
        self.module.ctypes.windll = types.SimpleNamespace(
            kernel32=types.SimpleNamespace(
                SetThreadExecutionState=requested_states.append,
            )
        )
        try:
            app.apply_prevent_sleep(True)
        finally:
            self.module.ctypes.windll = original_windll

        self.assertEqual(requested_states, [0x80000000 | 0x00000001])

    def test_low_space_does_not_delete_recordings_without_explicit_policy(self):
        app = self.new_app()
        app.silent = True
        app.add_log = lambda *_args, **_kwargs: None
        hd_root = Path(self.temp_dir.name) / "retention-hd"
        old_day = (self.module.datetime.now() - self.module.timedelta(days=120)).strftime("%Y-%m-%d")
        recording_day = hd_root / "camera 1" / old_day
        recording_day.mkdir(parents=True)
        (recording_day / "video.ts").write_bytes(b"recording")

        original_config = self.module.CONFIG
        original_root = self.module.GDRIVE_ROOT
        original_disk_usage = self.module.shutil.disk_usage
        self.module.CONFIG = {
            **original_config,
            "storage_identity": None,
            "retention_days": 90,
            "emergency_cleanup_enabled": False,
        }
        self.module.GDRIVE_ROOT = str(hd_root)
        self.module.shutil.disk_usage = lambda _path: (
            100 * 1024 ** 3,
            90 * 1024 ** 3,
            10 * 1024 ** 3,
        )
        app.get_camera_storage_dirs = lambda: [str(hd_root / "camera 1")]

        try:
            app.executar_limpeza_emergencial()
        finally:
            self.module.CONFIG = original_config
            self.module.GDRIVE_ROOT = original_root
            self.module.shutil.disk_usage = original_disk_usage

        self.assertTrue(recording_day.exists())

    def test_safe_shutdown_request_runs_only_once(self):
        app = self.new_app()
        app._shutdown_request_lock = threading.Lock()
        app._shutdown_requested = False
        messages = []
        completed = threading.Event()
        app.add_log = messages.append
        app.graceful_shutdown = completed.set

        app.request_safe_shutdown("teste")
        app.request_safe_shutdown("teste repetido")

        self.assertTrue(completed.wait(1.0))
        self.assertEqual(messages, ["Encerramento seguro solicitado: teste."])

    def test_start_sequence_does_not_restart_after_shutdown(self):
        app = self.new_app()
        app._shutdown_executed = True
        app.running_monitor = False
        app.run_stop_sequence = lambda: None
        start_attempted = []
        app.iniciar_go2rtc = lambda: start_attempted.append(True)
        original_sleep = self.module.time.sleep
        self.module.time.sleep = lambda _seconds: None
        try:
            app.run_start_sequence()
        finally:
            self.module.time.sleep = original_sleep

        self.assertEqual(start_attempted, [])

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
        recoverable_tmp = temp_root / "recoverable.tmp"
        video.write_bytes(b"recoverable-video")
        empty.write_bytes(b"")
        recoverable_tmp.write_bytes(b"recoverable-temporary")

        app.limpar_arquivos_temporarios_orfaos()

        self.assertEqual(video.read_bytes(), b"recoverable-video")
        self.assertFalse(empty.exists())
        self.assertEqual(recoverable_tmp.read_bytes(), b"recoverable-temporary")

    def test_destination_switches_between_backup_and_hd(self):
        app = self.new_app()
        app.recording_destinations = {}
        logs = []
        hd_dir = str(Path(self.temp_dir.name) / "external" / "camera 1")
        Path(hd_dir).mkdir(parents=True)
        original_status = self.module.garantir_limite_backup_local
        original_config = self.module.CONFIG
        original_identity = self.module.get_volume_identity
        self.module.garantir_limite_backup_local = lambda _path: {
            "ok": True,
            "free_bytes": 20 * 1024 ** 3,
            "reserve_bytes": 5 * 1024 ** 3,
        }
        self.module.CONFIG = {**original_config, "storage_identity": {"serial": "A1B2C3D4", "label": "FARMACIA"}}
        self.module.get_volume_identity = lambda _path: {"serial": "A1B2C3D4", "label": "FARMACIA"}
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
            self.module.CONFIG = original_config
            self.module.get_volume_identity = original_identity

    def test_external_hd_recording_does_not_require_local_fallback_space(self):
        app = self.new_app()
        app.recording_destinations = {}
        logs = []
        hd_dir = str(Path(self.temp_dir.name) / "external-only" / "camera 1")
        Path(hd_dir).mkdir(parents=True)
        original_status = self.module.garantir_limite_backup_local
        original_config = self.module.CONFIG
        original_identity = self.module.get_volume_identity
        original_disk_usage = self.module.shutil.disk_usage
        self.module.garantir_limite_backup_local = lambda _path: {
            "ok": False,
            "free_bytes": 1 * 1024 ** 3,
            "reserve_bytes": 20 * 1024 ** 3,
        }
        self.module.CONFIG = {
            **original_config,
            "storage_identity": {"serial": "A1B2C3D4", "label": "FARMACIA"},
        }
        self.module.get_volume_identity = lambda _path: {
            "serial": "A1B2C3D4",
            "label": "FARMACIA",
        }
        self.module.shutil.disk_usage = lambda _path: (
            1000 * 1024 ** 3,
            100 * 1024 ** 3,
            900 * 1024 ** 3,
        )
        app.get_gdrive_dir = lambda _stream, _index: hd_dir
        app.storage_path_is_writable = lambda _path: True

        try:
            selected, heartbeat = app.select_recording_destination("cam", 0, logs.append)
        finally:
            self.module.garantir_limite_backup_local = original_status
            self.module.CONFIG = original_config
            self.module.get_volume_identity = original_identity
            self.module.shutil.disk_usage = original_disk_usage

        self.assertEqual(selected, hd_dir)
        self.assertEqual(heartbeat, hd_dir)
        self.assertEqual(app.recording_destinations["cam"], "hd")

    def test_recording_file_is_published_without_copy_on_destination_volume(self):
        app = self.new_app()
        work_dir = Path(self.temp_dir.name) / "direct-publish"
        work_dir.mkdir()
        source = work_dir / "block.ts.recording"
        destination = work_dir / "block.ts"
        source.write_bytes(b"camera-data")
        app.safe_atomic_copy = lambda *_args, **_kwargs: self.fail(
            "same-volume publication must not copy video data"
        )

        app.publish_recording_file(str(source), str(destination))

        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"camera-data")

    def test_recording_buffer_is_flushed_at_bounded_intervals(self):
        app = self.new_app()

        class FakeFile:
            def __init__(self):
                self.flush_count = 0

            def flush(self):
                self.flush_count += 1

        out_file = FakeFile()
        last_flush = app.flush_recording_buffer_if_due(out_file, 100.0, 104.9)
        self.assertEqual(last_flush, 100.0)
        self.assertEqual(out_file.flush_count, 0)

        last_flush = app.flush_recording_buffer_if_due(out_file, last_flush, 105.0)
        self.assertEqual(last_flush, 105.0)
        self.assertEqual(out_file.flush_count, 1)

    def test_stale_recording_temp_is_recovered_without_deleting_unknown_files(self):
        app = self.new_app()
        camera_dir = Path(self.temp_dir.name) / "camera 1"
        temp_dir = camera_dir / ".gravando_temp"
        temp_dir.mkdir(parents=True)
        recoverable = temp_dir / "camera_2026-07-25_20-00_ate_20-30.ts.recording"
        unknown = temp_dir / "unknown.recording"
        recoverable.write_bytes(b"recoverable-video")
        unknown.write_bytes(b"preserve-me")
        logs = []

        app.recover_recording_temporaries(str(camera_dir), None, logs.append)

        published = camera_dir / "2026-07-25" / "camera_2026-07-25_20-00_ate_20-30.ts"
        self.assertEqual(published.read_bytes(), b"recoverable-video")
        self.assertFalse(recoverable.exists())
        self.assertEqual(unknown.read_bytes(), b"preserve-me")

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
        retention_calls = []
        app.rotacionar_videos_hd = retention_calls.append
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
        self.assertEqual(retention_calls, [])

    def test_scanner_does_not_quarantine_when_ffmpeg_returns_zero_with_output(self):
        app = self.new_app()
        app.streams = ["cam"]
        app.silent = True
        app._scan_lock = threading.Lock()
        app._scan_state_path = str(Path(self.temp_dir.name) / "scan_warning_state.json")
        app.get_gdrive_dir = lambda _stream, _index: ""
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
            / "warning.ts"
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        old_time = time.time() - 600
        os.utime(source, (old_time, old_time))

        original_run = self.module.subprocess.run
        self.module.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0,
            stderr="non-fatal diagnostic",
        )
        try:
            app.escanear_videos_corrompidos_thread(show_popup=False)
            app.escanear_videos_corrompidos_thread(show_popup=False)
        finally:
            self.module.subprocess.run = original_run

        self.assertTrue(source.exists())
        state = app.load_integrity_scan_state()["files"]
        self.assertEqual(next(iter(state.values()))["result"], "inconclusive")

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
        app.stream_bytes_written = {"cam": 1024}
        app.stream_last_data_at = {"cam": time.time()}
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
        original_local_status = self.module.garantir_limite_backup_local
        self.addCleanup(
            setattr,
            self.module,
            "garantir_limite_backup_local",
            original_local_status,
        )
        self.module.garantir_limite_backup_local = lambda _path: {
            "ok": True,
            "free_bytes": 50 * 1024 ** 3,
            "reserve_bytes": 20 * 1024 ** 3,
        }
        original_disk_usage = self.module.shutil.disk_usage
        self.addCleanup(
            setattr,
            self.module.shutil,
            "disk_usage",
            original_disk_usage,
        )
        self.module.shutil.disk_usage = lambda _path: (
            100 * 1024 ** 3,
            50 * 1024 ** 3,
            50 * 1024 ** 3,
        )

        healthy = app.collect_health_snapshot()
        self.assertEqual(healthy["overall_status"], "healthy")
        self.assertEqual(healthy["intelligence"]["status"], "stable")
        self.assertEqual(healthy["intelligence"]["root_cause"], "no_active_risk")
        self.assertEqual(healthy["metrics"]["stream_data"]["cam"]["bytes_written_session"], 1024)

        app.stream_last_data_at["cam"] = time.time() - 120
        no_data = app.collect_health_snapshot()
        no_data_codes = {issue["code"] for issue in no_data["issues"]}
        self.assertIn("STREAM_NO_DATA", no_data_codes)
        app.stream_last_data_at["cam"] = time.time()

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

    def test_recording_route_probe_requires_registered_mpegts_handler(self):
        app = self.new_app()
        original_urlopen = self.module.urllib.request.urlopen

        def http_error(body):
            return urllib.error.HTTPError(
                "http://127.0.0.1:1984/api/stream.ts",
                404,
                "Not Found",
                {},
                io.BytesIO(body),
            )

        try:
            self.module.urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                http_error(b"stream not found\n")
            )
            self.assertTrue(app.probe_go2rtc_recording_route())

            self.module.urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                http_error(b"404 page not found\n")
            )
            self.assertFalse(app.probe_go2rtc_recording_route())
        finally:
            self.module.urllib.request.urlopen = original_urlopen

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
        original_config = self.module.CONFIG
        self.module.CONFIG = {**original_config, "storage_identity": None}

        try:
            app.limpar_e_fundir_pastas_legadas()
        finally:
            self.module.CONFIG = original_config

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

    def test_shutdown_keeps_root_open_while_wimi_worker_is_alive(self):
        app = self.new_app()
        app.root = FakeRoot()
        app.silent = True
        app.running_monitor = True
        app.running_sync = True
        app.apply_prevent_sleep = lambda _enabled: None
        app.run_stop_sequence = lambda: None
        app.limpar_processos_ffmpeg_zumbis = lambda sync=False: None
        app.wait_for_wimi_shutdown = lambda: False

        app.graceful_shutdown()

        self.assertFalse(app.root.quit_called)
        self.assertFalse(app.root.destroy_called)

    def test_power_loss_keeps_tk_open_until_wimi_releases_databases(self):
        app = self.new_app()
        app.root = FakeRoot()
        app.speak = lambda _message: None
        app.running_monitor = True
        app.running_sync = True
        app.run_stop_sequence = lambda: None
        app.wait_for_wimi_shutdown = lambda attempts=1, retry_delay=0: False
        commands = []
        original_popen = self.module.subprocess.Popen
        self.module.subprocess.Popen = lambda command, shell=False: commands.append(command)
        try:
            app.graceful_shutdown_due_to_power_loss()
        finally:
            self.module.subprocess.Popen = original_popen

        self.assertEqual(len(commands), 1)
        self.assertFalse(app.root.quit_called)
        self.assertFalse(app.root.destroy_called)

    def test_background_shutdown_uses_main_thread_control_queue(self):
        class DeferredRoot(FakeRoot):
            def __init__(self):
                super().__init__()
                self.after_calls = []

            def after(self, delay, callback, *args):
                self.after_calls.append((delay, callback, args))

        app = self.new_app()
        app.root = DeferredRoot()
        app._ui_control_queue = self.module.queue.Queue(maxsize=8)
        worker = threading.Thread(target=app.request_tk_shutdown)

        worker.start()
        worker.join(1)

        self.assertFalse(app.root.quit_called)
        self.assertEqual(app.root.after_calls, [])
        app.drain_ui_control_queue()
        self.assertTrue(app.root.quit_called)
        self.assertTrue(app.root.destroy_called)

    def test_wimi_integration_keeps_vision_queue_at_two_frames(self):
        source = inspect.getsource(self.module.CameraManagerApp._start_wimi_analytics_worker)
        self.assertIn("queue_size=2", source)
        self.assertNotIn("queue_size=4", source)
        self.assertNotIn("root.after", source)
        self.assertIn("wimi_ready", source)

    def test_wimi_ready_control_runs_on_queue_drain(self):
        class DeferredRoot(FakeRoot):
            def __init__(self):
                super().__init__()
                self.after_calls = []

            def after(self, delay, callback, *args):
                self.after_calls.append((delay, callback, args))

        app = self.new_app()
        app.root = DeferredRoot()
        app._ui_control_queue = self.module.queue.Queue(maxsize=8)
        received = []
        app._on_wimi_analytics_ready = received.append
        app._ui_control_queue.put_nowait(("wimi_ready", True))

        app.drain_ui_control_queue()

        self.assertEqual(received, [True])
        self.assertEqual(len(app.root.after_calls), 1)

    def test_wimi_panel_is_embedded_and_selects_the_main_analytics_tab(self):
        import wimi_analytics.desktop as desktop_module

        captured = {}

        class FakeNotebook:
            def select(self, page):
                captured["selected"] = page

        class FakeAnalyticsPanel:
            def __init__(self, *args, **kwargs):
                captured["parent"] = kwargs.get("parent")

            def show(self):
                captured["shown"] = True
                return True

        app = self.new_app()
        app.root = FakeRoot()
        app.main_notebook = FakeNotebook()
        app.analytics_page = object()
        app.analytics_placeholder = None
        app._analytics_collector = object()
        app._analytics_store = object()
        app._vision_coordinator = object()
        app._face_service = object()
        app._analytics_window = None
        app.camera_widgets = {}
        app.activate_wimi_camera_analysis = lambda: None
        original = desktop_module.AnalyticsDesktopWindow
        desktop_module.AnalyticsDesktopWindow = FakeAnalyticsPanel
        try:
            app.open_wimi_analytics()
        finally:
            desktop_module.AnalyticsDesktopWindow = original

        self.assertIs(captured["selected"], app.analytics_page)
        self.assertIs(captured["parent"], app.analytics_page)
        self.assertTrue(captured["shown"])


if __name__ == "__main__":
    unittest.main()
