import json
import http.client
import os
import socket
import subprocess
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
from wimi_analytics.network_diagnostics import WindowsNetworkDiagnostics
from wimi_analytics.operations import build_operational_report, build_readiness


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
                "hardware": {
                    "smart": {
                        "status": "ok",
                        "telemetry_level": "basic_windows_status",
                        "checked_at": datetime.now().isoformat(timespec="seconds"),
                        "serial": "SECRET-SERIAL",
                        "drives": [
                            {"model": "Private model", "status": "OK"},
                            {"model": "Private backup", "status": "Pred Fail"},
                        ],
                    },
                    "kernel_144": {
                        "count_24h": 1,
                        "new_in_session": 0,
                        "report_ids": ["private-report"],
                    },
                    "power": {"status": "ac"},
                },
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
        self.assertNotIn("Private model", serialized)
        self.assertNotIn("private-report", serialized)
        self.assertEqual(result["snapshot"]["hardware_summary"]["drive_count"], 2)
        self.assertEqual(
            result["snapshot"]["hardware_summary"]["drive_warning_count"], 1
        )
        self.assertEqual(
            result["snapshot"]["hardware_summary"]["kernel_144_new_in_session"], 0
        )
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

    def test_invalid_timestamp_and_numeric_metrics_fail_closed(self):
        self.write_snapshot(
            {
                "schema_version": 1,
                "generated_at": "invalid",
                "overall_status": "healthy",
                "metrics": {"hd_available": True, "hd_free_gb": 800.0},
            }
        )
        invalid_time = NvrHealthBridge(self.health_path).read()
        self.assertEqual(invalid_time["state"], "unavailable")
        self.assertIsNone(invalid_time["snapshot"])

        self.write_snapshot(
            {
                "schema_version": 1,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "overall_status": "healthy",
                "issues": [],
                "metrics": {
                    "hd_available": True,
                    "hd_free_gb": float("nan"),
                    "process_memory_mb": float("inf"),
                    "pending_backup_count": -1,
                    "camera_connectivity": {},
                },
            }
        )
        invalid_metrics = NvrHealthBridge(self.health_path).read()["snapshot"]["metrics"]
        self.assertTrue(invalid_metrics["hd_available"])
        self.assertNotIn("hd_free_gb", invalid_metrics)
        self.assertNotIn("process_memory_mb", invalid_metrics)
        self.assertNotIn("pending_backup_count", invalid_metrics)


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

    def test_network_module_reports_limited_host_coverage_without_claiming_store_traffic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = NvrHealthBridge(Path(temp_dir) / "missing.json")
            payload = build_dashboard_payload(
                bridge,
                network={
                    "schema_version": 1,
                    "state": "active",
                    "coverage": "host_configuration_only",
                    "can_observe_store_traffic": False,
                    "reason": "host_network_detected",
                    "collected_at": datetime.now().isoformat(timespec="seconds"),
                    "interfaces": [],
                    "connectivity": {
                        "active_interface_count": 0,
                        "default_gateway_configured": False,
                        "dns_configured": False,
                    },
                },
            )

        modules = {module["id"]: module for module in payload["modules"]}
        self.assertEqual(modules["network"]["status"], "limited")
        self.assertFalse(payload["network"]["can_observe_store_traffic"])
        self.assertNotIn("packets", json.dumps(payload).lower())

    def test_current_operational_report_is_evidence_based_and_has_no_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            health_path = Path(temp_dir) / "health_status.json"
            health_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                        "overall_status": "healthy",
                        "issues": [],
                        "metrics": {
                            "hd_available": True,
                            "hd_free_gb": 824.5,
                            "pending_backup_count": 0,
                            "pending_backup_gb": 0.0,
                            "camera_connectivity": {
                                "farmacia": {
                                    "status": "ONLINE",
                                    "recording_active": True,
                                }
                            },
                        },
                        "intelligence": {
                            "status": "stable",
                            "hardware_protection": {
                                "heavy_maintenance_allowed": True,
                                "reason": "Sem bloqueio.",
                                "recording_recommendation": "continue_monitoring",
                            },
                        },
                        "hardware": {
                            "smart": {
                                "status": "ok",
                                "telemetry_level": "basic_windows_status",
                                "drives": [{"model": "hidden", "status": "OK"}],
                            },
                            "kernel_144": {"count_24h": 1, "new_in_session": 0},
                            "power": {"status": "ac"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            payload = build_dashboard_payload(
                NvrHealthBridge(health_path),
                network={
                    "schema_version": 1,
                    "state": "active",
                    "coverage": "host_configuration_only",
                    "can_observe_store_traffic": False,
                    "reason": "host_network_detected",
                    "collected_at": datetime.now().isoformat(timespec="seconds"),
                    "interfaces": [],
                    "connectivity": {
                        "active_interface_count": 1,
                        "default_gateway_configured": True,
                        "dns_configured": True,
                    },
                },
            )

        report = payload["operations"]["report"]
        readiness = payload["operations"]["readiness"]
        checks = {check["id"]: check for check in report["checks"]}
        modules = {module["id"]: module for module in payload["modules"]}

        self.assertEqual(report["state"], "current")
        self.assertEqual(report["scope"], "nvr_and_host_only")
        self.assertEqual(checks["cameras"]["status"], "active")
        self.assertEqual(checks["storage"]["status"], "active")
        self.assertEqual(checks["backups"]["status"], "active")
        self.assertEqual(modules["reports"]["status"], "active")
        self.assertIn("local_read_only", {item["id"] for item in readiness["strengths"]})
        self.assertIn("vision_not_configured", {item["id"] for item in readiness["limitations"]})
        self.assertIn("computers_not_configured", {item["id"] for item in readiness["limitations"]})
        self.assertIn("store_network_not_observed", {item["id"] for item in readiness["limitations"]})
        self.assertNotIn("score", json.dumps(payload).lower())


class OperationalInsightsTests(unittest.TestCase):
    def make_nvr(self, snapshot, state="active"):
        return {
            "state": state,
            "reason": "snapshot_current" if state == "active" else "snapshot_stale",
            "age_seconds": 0 if state == "active" else 600,
            "snapshot": snapshot,
        }

    def active_network(self):
        return {
            "schema_version": 1,
            "state": "active",
            "coverage": "host_configuration_only",
            "can_observe_store_traffic": False,
            "connectivity": {
                "active_interface_count": 1,
                "default_gateway_configured": True,
                "dns_configured": True,
            },
        }

    def test_connectivity_and_recording_are_reported_separately(self):
        snapshot = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "metrics": {
                "camera_connectivity": {
                    "farmacia": {"status": "ONLINE", "recording_active": False}
                }
            },
        }
        report = build_operational_report(self.make_nvr(snapshot), self.active_network())
        checks = {check["id"]: check for check in report["checks"]}

        self.assertEqual(checks["cameras"]["status"], "active")
        self.assertEqual(checks["recording"]["status"], "limited")
        self.assertEqual(checks["recording"]["value"], "Parada")

    def test_drive_warning_overrides_generic_smart_ok(self):
        snapshot = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "metrics": {},
            "hardware_summary": {
                "smart_status": "ok",
                "drive_count": 2,
                "drive_warning_count": 1,
            },
        }
        report = build_operational_report(self.make_nvr(snapshot), self.active_network())
        hardware = next(check for check in report["checks"] if check["id"] == "hardware")
        self.assertEqual(hardware["status"], "warning")

    def test_sanitized_storage_issue_overrides_available_flag(self):
        snapshot = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "metrics": {"hd_available": True, "hd_free_gb": 50.0},
            "issues": [
                {
                    "code": "HD_SPACE_LOW",
                    "severity": "warning",
                    "summary": "Pouco espaco no HD.",
                }
            ],
        }
        report = build_operational_report(self.make_nvr(snapshot), self.active_network())
        checks = {check["id"]: check for check in report["checks"]}

        self.assertEqual(checks["storage"]["status"], "warning")
        self.assertEqual(checks["alerts"]["status"], "warning")
        self.assertEqual(checks["alerts"]["value"], "1 ocorrencia")

        readiness = build_readiness(self.make_nvr(snapshot), self.active_network(), [], report)
        limitation_ids = {item["id"] for item in readiness["limitations"]}
        strength_ids = {item["id"] for item in readiness["strengths"]}
        self.assertEqual(readiness["status"], "warning")
        self.assertIn("active_nvr_issues", limitation_ids)
        self.assertNotIn("storage_available", strength_ids)

    def test_critical_current_issue_marks_readiness_critical(self):
        snapshot = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "metrics": {"hd_available": False},
            "issues": [
                {
                    "code": "HD_UNAVAILABLE",
                    "severity": "critical",
                    "summary": "HD principal indisponivel.",
                }
            ],
        }
        nvr = self.make_nvr(snapshot)
        report = build_operational_report(nvr, self.active_network())
        readiness = build_readiness(nvr, self.active_network(), [], report)

        self.assertEqual(readiness["status"], "critical")

    def test_missing_network_makes_host_scoped_report_partial(self):
        snapshot = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "metrics": {},
        }
        report = build_operational_report(
            self.make_nvr(snapshot),
            {"state": "unavailable", "can_observe_store_traffic": False},
        )
        self.assertEqual(report["state"], "partial")

    def test_stale_snapshot_does_not_create_current_strengths(self):
        snapshot = {
            "generated_at": (datetime.now() - timedelta(minutes=10)).isoformat(
                timespec="seconds"
            ),
            "metrics": {
                "hd_available": True,
                "pending_backup_count": 0,
            },
            "hardware_summary": {"kernel_144_new_in_session": 0},
        }
        nvr = self.make_nvr(snapshot, state="stale")
        report = build_operational_report(nvr, self.active_network())
        readiness = build_readiness(nvr, self.active_network(), [], report)
        strength_ids = {item["id"] for item in readiness["strengths"]}

        self.assertNotIn("storage_available", strength_ids)
        self.assertNotIn("backups_clear", strength_ids)
        self.assertNotIn("no_new_kernel_144", strength_ids)


class NetworkDiagnosticsTests(unittest.TestCase):
    def make_completed_process(self, payload, returncode=0, stderr=b""):
        return subprocess.CompletedProcess(
            args=["powershell.exe"],
            returncode=returncode,
            stdout=json.dumps(payload).encode("utf-8"),
            stderr=stderr,
        )

    def test_windows_collector_sanitizes_configuration_and_declares_host_only_coverage(self):
        runner = mock.Mock(
            return_value=self.make_completed_process(
                [
                    {
                        "alias": "Ethernet",
                        "profile": "Rede interna",
                        "status": "Up",
                        "link_speed": "1 Gbps",
                        "ipv4": ["192.168.7.5", "invalid"],
                        "gateway": ["192.168.7.1"],
                        "dns": ["192.168.7.1", "fe80::1"],
                        "mac_address": "AA-BB-CC-DD-EE-FF",
                        "capture": "secret payload",
                    }
                ]
            )
        )
        diagnostics = WindowsNetworkDiagnostics(
            runner=runner,
            platform_name="win32",
            ttl_seconds=60,
        )

        result = diagnostics.read()
        serialized = json.dumps(result)

        self.assertEqual(result["state"], "active")
        self.assertEqual(result["source"], "windows_cim_network_configuration")
        self.assertEqual(result["coverage"], "host_configuration_only")
        self.assertFalse(result["can_observe_store_traffic"])
        self.assertEqual(result["interfaces"][0]["ipv4"], ["192.168.7.5"])
        self.assertEqual(result["interfaces"][0]["gateways"], ["192.168.7.1"])
        self.assertTrue(result["connectivity"]["default_gateway_configured"])
        self.assertTrue(result["connectivity"]["dns_configured"])
        self.assertNotIn("AA-BB-CC", serialized)
        self.assertNotIn("secret payload", serialized)
        powershell_command = runner.call_args.args[0][-1]
        self.assertIn("Win32_NetworkAdapterConfiguration", powershell_command)
        self.assertNotIn("Get-NetIPConfiguration", powershell_command)

    def test_collector_uses_bounded_cache_instead_of_spawning_powershell_per_poll(self):
        runner = mock.Mock(
            return_value=self.make_completed_process(
                [
                    {
                        "alias": "Ethernet",
                        "status": "Up",
                        "ipv4": ["192.168.7.5"],
                        "gateway": ["192.168.7.1"],
                        "dns": ["192.168.7.1"],
                    }
                ]
            )
        )
        clock = mock.Mock(side_effect=[100.0, 110.0, 401.0])
        diagnostics = WindowsNetworkDiagnostics(
            runner=runner,
            platform_name="win32",
            clock=clock,
        )

        diagnostics.read()
        diagnostics.read()
        diagnostics.read()

        self.assertEqual(runner.call_count, 2)

    def test_failed_collection_uses_short_cache_for_automatic_recovery(self):
        runner = mock.Mock(
            side_effect=[
                self.make_completed_process([]),
                self.make_completed_process(
                    [
                        {
                            "alias": "Ethernet",
                            "status": "Up",
                            "ipv4": ["192.168.7.5"],
                        }
                    ]
                ),
            ]
        )
        clock = mock.Mock(side_effect=[100.0, 120.0, 131.0])
        diagnostics = WindowsNetworkDiagnostics(
            runner=runner,
            platform_name="win32",
            clock=clock,
        )

        self.assertEqual(diagnostics.read()["state"], "unavailable")
        self.assertEqual(diagnostics.read()["state"], "unavailable")
        self.assertEqual(diagnostics.read()["state"], "active")
        self.assertEqual(runner.call_count, 2)

    def test_collector_fails_closed_on_timeout_invalid_json_and_unsupported_platform(self):
        timeout_runner = mock.Mock(side_effect=subprocess.TimeoutExpired("powershell", 4))
        timed_out = WindowsNetworkDiagnostics(
            runner=timeout_runner,
            platform_name="win32",
        ).read()
        self.assertEqual(timed_out["state"], "unavailable")
        self.assertEqual(timed_out["reason"], "collector_timeout")

        invalid_runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                args=["powershell.exe"], returncode=0, stdout=b"{invalid", stderr=b""
            )
        )
        invalid = WindowsNetworkDiagnostics(
            runner=invalid_runner,
            platform_name="win32",
        ).read()
        self.assertEqual(invalid["state"], "unavailable")
        self.assertEqual(invalid["reason"], "collector_invalid_output")

        unsupported = WindowsNetworkDiagnostics(platform_name="linux").read()
        self.assertEqual(unsupported["state"], "unsupported")
        self.assertEqual(unsupported["interfaces"], [])


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
        self.network_diagnostics = mock.Mock()
        self.network_diagnostics.read.return_value = {
            "schema_version": 1,
            "state": "active",
            "reason": "host_network_detected",
            "coverage": "host_configuration_only",
            "can_observe_store_traffic": False,
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "interfaces": [],
            "connectivity": {
                "active_interface_count": 0,
                "default_gateway_configured": False,
                "dns_configured": False,
            },
        }
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            bridge=NvrHealthBridge(health_path),
            network_diagnostics=self.network_diagnostics,
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

        network_request = urllib.request.Request(
            f"{self.base_url}/api/v1/network/status",
            headers={"Cookie": cookie},
        )
        with urllib.request.urlopen(network_request, timeout=2) as response:
            network = json.loads(response.read().decode("utf-8"))
        self.assertEqual(network["coverage"], "host_configuration_only")
        self.assertFalse(network["can_observe_store_traffic"])

        report_request = urllib.request.Request(
            f"{self.base_url}/api/v1/reports/current",
            headers={"Cookie": cookie},
        )
        with urllib.request.urlopen(report_request, timeout=2) as response:
            report = json.loads(response.read().decode("utf-8"))
        self.assertEqual(report["scope"], "nvr_and_host_only")

        readiness_request = urllib.request.Request(
            f"{self.base_url}/api/v1/system/readiness",
            headers={"Cookie": cookie},
        )
        with urllib.request.urlopen(readiness_request, timeout=2) as response:
            readiness = json.loads(response.read().decode("utf-8"))
        self.assertIn("strengths", readiness)
        self.assertIn("limitations", readiness)

    def test_healthz_remains_responsive_while_network_collection_is_busy(self):
        with urllib.request.urlopen(f"{self.base_url}/", timeout=2) as response:
            cookie = response.headers.get("Set-Cookie").split(";", 1)[0]

        collection_started = threading.Event()
        release_collection = threading.Event()
        network_payload = self.network_diagnostics.read.return_value

        def slow_network_read():
            collection_started.set()
            release_collection.wait(timeout=2)
            return network_payload

        self.network_diagnostics.read.side_effect = slow_network_read
        overview_request = urllib.request.Request(
            f"{self.base_url}/api/v1/overview",
            headers={"Cookie": cookie},
        )
        overview_result = []

        def load_overview():
            with urllib.request.urlopen(overview_request, timeout=3) as response:
                overview_result.append(response.status)

        overview_thread = threading.Thread(target=load_overview)
        overview_thread.start()
        self.assertTrue(collection_started.wait(timeout=1))
        try:
            with urllib.request.urlopen(f"{self.base_url}/healthz", timeout=0.5) as response:
                self.assertEqual(response.status, 200)
        finally:
            release_collection.set()
            overview_thread.join(timeout=3)

        self.assertEqual(overview_result, [200])

    def test_nvr_health_route_does_not_depend_on_network_collector(self):
        with urllib.request.urlopen(f"{self.base_url}/", timeout=2) as response:
            cookie = response.headers.get("Set-Cookie").split(";", 1)[0]
        self.network_diagnostics.read.side_effect = RuntimeError("collector failed")
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/nvr/health",
            headers={"Cookie": cookie},
        )

        with urllib.request.urlopen(request, timeout=1) as response:
            nvr = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(nvr["state"], "active")
        self.network_diagnostics.read.assert_not_called()

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
        self.assertIn("function renderNetwork()", script)
        self.assertIn("function renderReports()", script)
        self.assertIn("function renderSystem()", script)
        self.assertIn('state.route === "network"', script)
        self.assertIn('state.route === "reports"', script)
        self.assertIn('state.route === "system"', script)
        self.assertIn("can_observe_store_traffic", script)
        self.assertIn("operations.report", script)
        self.assertIn("operations.readiness", script)
        self.assertIn("window.scrollTo(0, 0)", script)
        self.assertIn("function keepActiveRouteVisible()", script)
        self.assertIn("priority_actions.forEach", script)
        self.assertIn("stale-data-banner", script)
        self.assertIn("Ocorrências da última coleta", script)
        self.assertIn('id="topbar-update"', html)
        self.assertIn('class="nav-state"', html)
        self.assertIn('aria-label="Navegação principal"', html)


if __name__ == "__main__":
    unittest.main()
