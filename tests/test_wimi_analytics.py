import json
import http.client
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wimi_analytics.backend import NvrHealthBridge, build_dashboard_payload
from wimi_analytics.server import create_server
from wimi_analytics.launcher import (
    AnalyticsServerHandle,
    ensure_server,
    probe_server,
    stop_owned_server,
)


class NvrHealthBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.health_path = Path(self.temp_dir.name) / "health_status.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_snapshot(self, payload):
        self.health_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_snapshot_is_reported_without_inventing_metrics(self):
        result = NvrHealthBridge(self.health_path).read()

        self.assertEqual(result["state"], "unavailable")
        self.assertIsNone(result["snapshot"])
        self.assertEqual(result["reason"], "snapshot_missing")

    def test_snapshot_is_allowlisted_and_sensitive_fields_are_removed(self):
        self.write_snapshot(
            {
                "schema_version": 1,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "hostname": "NVR-LOCAL",
                "overall_status": "warning",
                "secret": "do-not-expose",
                "issues": [
                    {
                        "key": "disk:path",
                        "code": "HD_LOW_SPACE",
                        "severity": "warning",
                        "summary": "Pouco espaco livre.",
                        "evidence": "D:\\private\\recordings",
                        "action": "Verificar o armazenamento.",
                        "stream": "farmacia",
                    }
                ],
                "metrics": {
                    "active_streams": ["farmacia"],
                    "thread_count": 12,
                    "process_memory_mb": 120.5,
                    "local_free_gb": 80.0,
                    "hd_available": True,
                    "hd_free_gb": 700.0,
                    "pending_backup_count": 0,
                    "pending_backup_gb": 0.0,
                    "camera_connectivity": {
                        "farmacia": {
                            "status": "ONLINE",
                            "reason": "recent_media",
                            "recording_active": True,
                            "rtsp_url": "rtsp://user:password@camera/private",
                        }
                    },
                    "private_metric": "hidden",
                },
                "intelligence": {
                    "status": "stable",
                    "headline": "Sistema estavel nesta coleta.",
                    "explanation": "Sem alertas ativos.",
                    "confidence_score": 95,
                    "priority_actions": ["Manter o monitoramento automatico."],
                    "private_reasoning": "hidden",
                    "hardware_protection": {
                        "heavy_maintenance_allowed": True,
                        "reason": "Sem bloqueio.",
                        "recording_recommendation": "continue_monitoring",
                        "secret": "hidden",
                    },
                },
                "hardware": {"smart": {"status": "ok", "serial": "SECRET-SERIAL"}},
            }
        )

        result = NvrHealthBridge(self.health_path).read()
        serialized = json.dumps(result)

        self.assertEqual(result["state"], "active")
        self.assertEqual(result["snapshot"]["overall_status"], "warning")
        self.assertNotIn("do-not-expose", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("password", serialized)
        self.assertNotIn("SECRET-SERIAL", serialized)
        self.assertNotIn("evidence", result["snapshot"]["issues"][0])
        self.assertEqual(
            result["snapshot"]["intelligence"]["headline"],
            "Sistema estavel nesta coleta.",
        )
        self.assertEqual(result["snapshot"]["intelligence"]["confidence_score"], 95)
        self.assertNotIn("private_reasoning", result["snapshot"]["intelligence"])
        self.assertNotIn("secret", serialized)
        self.assertEqual(
            result["snapshot"]["metrics"]["camera_connectivity"]["farmacia"]["status"],
            "ONLINE",
        )

    def test_old_snapshot_is_stale_and_keeps_last_safe_evidence(self):
        generated_at = datetime.now() - timedelta(minutes=10)
        self.write_snapshot(
            {
                "schema_version": 1,
                "generated_at": generated_at.isoformat(timespec="seconds"),
                "overall_status": "healthy",
                "issues": [],
                "metrics": {"camera_connectivity": {}},
            }
        )

        result = NvrHealthBridge(self.health_path, stale_after_seconds=180).read()

        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["reason"], "snapshot_stale")
        self.assertGreaterEqual(result["age_seconds"], 590)
        self.assertEqual(result["snapshot"]["overall_status"], "healthy")

    def test_invalid_or_oversized_snapshot_fails_closed(self):
        self.health_path.write_text("{invalid", encoding="utf-8")
        invalid = NvrHealthBridge(self.health_path).read()
        self.assertEqual(invalid["state"], "unavailable")
        self.assertEqual(invalid["reason"], "snapshot_invalid")

        self.health_path.write_bytes(b"x" * 1025)
        oversized = NvrHealthBridge(self.health_path, max_bytes=1024).read()
        self.assertEqual(oversized["state"], "unavailable")
        self.assertEqual(oversized["reason"], "snapshot_too_large")

    def test_unknown_schema_and_future_clock_are_not_treated_as_current(self):
        self.write_snapshot(
            {
                "schema_version": 99,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        unsupported = NvrHealthBridge(self.health_path).read()
        self.assertEqual(unsupported["state"], "unavailable")
        self.assertEqual(unsupported["reason"], "schema_unsupported")

        self.write_snapshot(
            {
                "schema_version": 1,
                "generated_at": (datetime.now() + timedelta(minutes=5)).isoformat(
                    timespec="seconds"
                ),
                "overall_status": "healthy",
                "issues": [],
                "metrics": {"camera_connectivity": {}},
            }
        )
        future = NvrHealthBridge(self.health_path).read()
        self.assertEqual(future["state"], "unknown")
        self.assertEqual(future["reason"], "clock_skew")


class DashboardPayloadTests(unittest.TestCase):
    def test_unconfigured_collectors_are_explicit_and_not_reported_as_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = NvrHealthBridge(Path(temp_dir) / "missing.json")
            payload = build_dashboard_payload(bridge)

        modules = {module["id"]: module for module in payload["modules"]}
        self.assertEqual(payload["service"]["id"], "wimi-analytics")
        self.assertEqual(payload["service"]["status"], "active")
        self.assertEqual(modules["vision"]["status"], "not_configured")
        self.assertEqual(modules["computers"]["status"], "not_configured")
        self.assertEqual(modules["network"]["status"], "not_configured")
        self.assertEqual(modules["reports"]["status"], "waiting_for_data")
        self.assertNotIn("productivity_score", json.dumps(payload))


class AnalyticsHttpServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        health_path = Path(self.temp_dir.name) / "health_status.json"
        health_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "overall_status": "healthy",
                    "issues": [],
                    "metrics": {"camera_connectivity": {}},
                }
            ),
            encoding="utf-8",
        )
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            bridge=NvrHealthBridge(health_path),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def test_healthz_is_minimal_and_api_requires_browser_session(self):
        with urllib.request.urlopen(f"{self.base_url}/healthz", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        self.assertEqual(health, {"service": "wimi-analytics", "status": "ready"})

        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(f"{self.base_url}/api/v1/overview", timeout=2)
        self.assertEqual(unauthorized.exception.code, 401)

        with urllib.request.urlopen(f"{self.base_url}/", timeout=2) as response:
            cookie = response.headers.get("Set-Cookie").split(";", 1)[0]
            html = response.read().decode("utf-8")
        self.assertIn("WIMI Analytics", html)

        request = urllib.request.Request(
            f"{self.base_url}/api/v1/overview",
            headers={"Cookie": cookie},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            overview = json.loads(response.read().decode("utf-8"))
        self.assertEqual(overview["service"]["id"], "wimi-analytics")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_external_host_and_origin_are_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.putrequest("GET", "/healthz", skip_host=True)
        connection.putheader("Host", "malicious.example")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 421)
        response.read()
        connection.close()

        with urllib.request.urlopen(f"{self.base_url}/", timeout=2) as root_response:
            cookie = root_response.headers.get("Set-Cookie").split(";", 1)[0]
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/overview",
            headers={"Cookie": cookie, "Origin": "https://malicious.example"},
        )
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(rejected.exception.code, 403)

    def test_unknown_paths_and_write_methods_fail_closed(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{self.base_url}/%2e%2e/gerenciador.pyw", timeout=2)
        self.assertEqual(missing.exception.code, 404)

        request = urllib.request.Request(f"{self.base_url}/api/v1/overview", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(rejected.exception.code, 405)


class AnalyticsLauncherTests(unittest.TestCase):
    def test_real_process_starts_once_and_leaves_no_listener_after_owned_stop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]

        owned = ensure_server(PROJECT_ROOT, port=port, timeout_seconds=5)
        try:
            reused = ensure_server(PROJECT_ROOT, port=port, timeout_seconds=1)
            self.assertTrue(owned.owned)
            self.assertFalse(reused.owned)
            self.assertTrue(probe_server(port))
        finally:
            stop_owned_server(owned)

        restarted = ensure_server(PROJECT_ROOT, port=port, timeout_seconds=5)
        self.assertTrue(restarted.owned)
        stop_owned_server(restarted)

        for _ in range(20):
            if not probe_server(port, timeout_seconds=0.1):
                break
            threading.Event().wait(0.05)
        self.assertFalse(probe_server(port, timeout_seconds=0.1))

    @mock.patch("wimi_analytics.launcher.subprocess.Popen")
    @mock.patch("wimi_analytics.launcher.port_is_listening", return_value=True)
    @mock.patch("wimi_analytics.launcher.probe_server", return_value=False)
    def test_port_collision_never_starts_or_stops_the_occupant(
        self, probe_mock, listening_mock, popen_mock
    ):
        with self.assertRaisesRegex(RuntimeError, "ocupada"):
            ensure_server(PROJECT_ROOT, timeout_seconds=0.1)
        popen_mock.assert_not_called()

    @mock.patch("wimi_analytics.launcher.subprocess.Popen")
    @mock.patch("wimi_analytics.launcher.probe_server", return_value=True)
    def test_repeated_open_reuses_the_existing_service(self, probe_mock, popen_mock):
        handle = ensure_server(PROJECT_ROOT, timeout_seconds=0.1)

        self.assertFalse(handle.owned)
        self.assertIsNone(handle.process)
        popen_mock.assert_not_called()

    def test_shutdown_only_terminates_owned_live_process(self):
        process = mock.Mock()
        process.poll.return_value = None
        owned = AnalyticsServerHandle(process=process, owned=True, port=8765)
        stop_owned_server(owned)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once()

        foreign_process = mock.Mock()
        stop_owned_server(
            AnalyticsServerHandle(process=foreign_process, owned=False, port=8765)
        )
        foreign_process.terminate.assert_not_called()


class AnalyticsFrontendSafetyTests(unittest.TestCase):
    def test_frontend_uses_safe_dom_and_never_calls_recording_route(self):
        script = (PROJECT_ROOT / "wimi_analytics" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (PROJECT_ROOT / "wimi_analytics" / "static" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("innerHTML", script)
        self.assertNotIn("/api/stream.ts", script)
        self.assertNotIn("eval(", script)
        self.assertIn('fetch("/api/v1/overview"', script)
        self.assertIn('rel = "noopener noreferrer"', script)
        self.assertIn('aria-label="Navegação principal"', html)


if __name__ == "__main__":
    unittest.main()
