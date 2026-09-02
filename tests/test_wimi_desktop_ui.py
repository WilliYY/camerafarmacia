import threading
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from wimi_analytics.desktop import AnalyticsDesktopWindow


class FakeCollector:
    def snapshot(self):
        return {
            "running": True,
            "last_error": None,
            "payload": {
                "nvr": {"state": "active", "snapshot": {"overall_status": "healthy", "metrics": {}}},
                "network": {
                    "state": "active",
                    "coverage": "host_configuration_only",
                    "connectivity": {"active_interface_count": 1},
                },
                "operations": {"report": {"state": "current"}},
                "modules": [],
            },
        }


class FakeStore:
    def list_vision_events(self, limit=300):
        return []

    def list_profile_observations(self, limit=500):
        return [
            item
            for item in self.list_vision_events(limit=2000)
            if item.get("event_type") == "presence_confirmed"
            and item.get("profile_id")
        ][:limit]

    def list_network_samples(self, limit=200):
        return []

    def list_network_sessions(self, limit=50):
        return []

    def list_network_device_sessions(self, limit=100):
        return []

    def list_local_application_sessions(self, limit=100):
        return []

    def list_reports(self, limit=200):
        return []

    def list_profile_presence_summary(self, limit=100):
        return []

    def summarize_network_traffic(self, limit=120, samples=None):
        return {
            "state": "no_data",
            "anomaly": "insufficient_history",
            "traffic_detected": False,
            "current_bytes_per_second": None,
            "baseline_bytes_per_second": None,
            "scope": "this_host_aggregate_only",
            "captures_content": False,
        }

    def delete_profile_presence(self, profile_id):
        return False


class FakeVision:
    def snapshot(self):
        return {}


class FakeEvidenceArchive:
    retention_days = 10

    def __init__(self, snapshots=None):
        self.snapshots = list(snapshots or [])
        self.deleted = []
        self.read_ids = []

    def list_snapshots(self, limit=200):
        return list(self.snapshots[:limit])

    def status(self):
        return {
            "state": "active",
            "count": len(self.snapshots),
            "total_bytes": sum(item.get("byte_count", 0) for item in self.snapshots),
            "retention_days": 10,
            "identifiable_faces_stored": False,
        }

    def read_image(self, evidence_id):
        self.read_ids.append(evidence_id)
        if any(item["evidence_id"] == evidence_id for item in self.snapshots):
            return Image.new("RGB", (320, 180), "#345678")
        return None

    def delete(self, evidence_id):
        before = len(self.snapshots)
        self.snapshots = [
            item for item in self.snapshots if item["evidence_id"] != evidence_id
        ]
        if len(self.snapshots) != before:
            self.deleted.append(evidence_id)
            return True
        return False


class FakeFaceService:
    available = False
    status = "models_missing"

    def list_profiles(self):
        return []


class AnalyticsDesktopWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk indisponível: {error}")
        self.root.withdraw()

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def test_evidence_activity_shows_people_activity_and_observed_dwell(self):
        class BehaviorVision(FakeVision):
            def snapshot(self):
                return {
                    "farmacia": {
                        "state": "active",
                        "motion": "active",
                        "activity_level": "high",
                        "person_count": 2,
                        "presence_duration_seconds": 12.0,
                        "face_count": 1,
                        "identities": [],
                        "last_analyzed_at": "2026-08-23T10:00:12",
                    }
                }

        class BehaviorStore(FakeStore):
            def list_vision_events(self, limit=300):
                return [
                    {
                        "event_type": "observed_presence_end",
                        "stream": "farmacia",
                        "occurred_at": "2026-08-23T10:00:30",
                        "count": 2,
                        "duration_seconds": 30.0,
                    },
                    {
                        "event_type": "observed_presence_start",
                        "stream": "farmacia",
                        "occurred_at": "2026-08-23T10:00:00",
                        "count": 2,
                    },
                ]

        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            BehaviorStore(),
            BehaviorVision(),
            face_service=FakeFaceService(),
        )

        self.assertTrue(controller.show())
        controller.notebook.select(controller._evidence_tab)
        controller._evidence_notebook.select(controller._evidence_activity_tab)
        self.root.update()
        camera_values = controller._trees["cameras"].item("camera-0", "values")
        summary = controller._labels["behavior_summary"].cget("text")

        self.assertIn("Alta", camera_values)
        self.assertIn("2", camera_values)
        self.assertIn("12 s", camera_values)
        self.assertIn("Sessões concluídas: 1", summary)
        self.assertIn("Pico amostrado: 2", summary)
        self.assertIn("30 s", summary)
        self.assertEqual(
            [
                controller._behavior_notebook.tab(tab, "text")
                for tab in controller._behavior_notebook.tabs()
            ],
            ["Trajetos identificados", "Eventos técnicos"],
        )
        self.assertEqual(len(controller._trees["events"].get_children()), 2)
        controller.destroy()

    def test_evidence_activity_reports_consent_profile_camera_change(self):
        class ActivityStore(FakeStore):
            def list_vision_events(self, limit=300):
                return [
                    {
                        "event_id": "new",
                        "event_type": "presence_confirmed",
                        "profile_id": "profile-1",
                        "stream": "farmacia2",
                        "occurred_at": "2026-08-23T10:02:00",
                        "confidence": 0.92,
                    },
                    {
                        "event_id": "old",
                        "event_type": "presence_confirmed",
                        "profile_id": "profile-1",
                        "stream": "farmacia",
                        "occurred_at": "2026-08-23T10:01:00",
                        "confidence": 0.89,
                    },
                ]

        class ActivityFaces(FakeFaceService):
            available = True
            status = "ready"

            def list_profiles(self):
                return [
                    {
                        "profile_id": "profile-1",
                        "display_name": "Thiago",
                        "role": "employee",
                    }
                ]

        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            ActivityStore(),
            FakeVision(),
            face_service=ActivityFaces(),
        )

        self.assertTrue(controller.show())
        controller.notebook.select(controller._evidence_tab)
        controller._evidence_notebook.select(controller._evidence_activity_tab)
        self.root.update()
        rows = [
            controller._trees["profile_activity"].item(item, "values")
            for item in controller._trees["profile_activity"].get_children()
        ]

        self.assertTrue(
            any(any("Thiago" in str(value) for value in row) for row in rows)
        )
        self.assertTrue(
            any(
                any(
                    "Sequência observada: FARMACIA → FARMACIA2" in str(value)
                    for value in row
                )
                for row in rows
            )
        )
        self.assertIn(
            "Sequências entre câmeras: 1",
            controller._labels["profile_activity_summary"].cget("text"),
        )
        controller.destroy()

    def test_profile_timeline_is_loaded_only_while_activity_is_visible(self):
        class CountingStore(FakeStore):
            def __init__(self):
                self.observation_reads = 0

            def list_profile_observations(self, limit=500):
                self.observation_reads += 1
                return []

        store = CountingStore()
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            store,
            FakeVision(),
            face_service=FakeFaceService(),
        )

        self.assertTrue(controller.show())
        self.root.update()
        self.assertEqual(store.observation_reads, 0)

        controller.notebook.select(controller._evidence_tab)
        self.root.update()
        self.assertEqual(store.observation_reads, 0)

        controller._evidence_notebook.select(controller._evidence_activity_tab)
        self.root.update()
        self.assertGreaterEqual(store.observation_reads, 1)
        controller.destroy()

    def test_activity_view_preserves_selection_at_minimum_geometry(self):
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
        )

        self.assertTrue(controller.show())
        controller.window.geometry("980x760")
        controller.notebook.select(controller._evidence_tab)
        controller._evidence_notebook.select(controller._evidence_capture_tab)
        self.root.update()
        self.assertGreater(controller._evidence_gallery_canvas.winfo_height(), 250)

        controller._evidence_notebook.select(controller._evidence_activity_tab)
        controller._behavior_notebook.select(1)
        self.root.update()

        self.assertGreaterEqual(controller.window.winfo_width(), 980)
        self.assertGreaterEqual(controller.window.winfo_height(), 760)
        self.assertGreater(controller._trees["events"].winfo_height(), 200)
        controller._evidence_notebook.select(controller._evidence_people_tab)
        self.root.update()
        self.assertGreater(controller._trees["people"].winfo_height(), 300)

        controller.notebook.select(0)
        self.root.update()
        controller.notebook.select(controller._evidence_tab)
        self.root.update()
        self.assertEqual(controller._behavior_notebook.index("current"), 1)
        self.assertEqual(controller._evidence_notebook.index("current"), 2)
        controller.destroy()

    def test_camera_tab_shows_manually_assigned_role_after_recognition(self):
        class RecognizedVision(FakeVision):
            def snapshot(self):
                return {
                    "farmacia": {
                        "state": "active",
                        "queue_delay_ms": 12.4,
                        "processing_duration_ms": 136.2,
                        "identities": [
                            {
                                "display_name": "Maria",
                                "role": "employee",
                            }
                        ],
                    }
                }

        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            RecognizedVision(),
            face_service=FakeFaceService(),
        )

        self.assertTrue(controller.show())
        self.root.update()
        camera_values = controller._trees["cameras"].item("camera-0", "values")

        self.assertIn("Maria (Funcionário)", camera_values)
        self.assertIn("12 + 136 ms", camera_values)
        controller.destroy()

    def test_camera_tab_unifies_repeated_identifications_and_active_state(self):
        class IdentificationStore(FakeStore):
            def list_vision_events(self, limit=300):
                return [
                    {
                        "event_type": "presence_confirmed",
                        "profile_id": "profile-1",
                        "stream": "farmacia2",
                        "occurred_at": "2026-08-23T15:42:00",
                        "confidence": 0.91,
                    },
                    {
                        "event_type": "presence_confirmed",
                        "profile_id": "profile-1",
                        "stream": "farmacia",
                        "occurred_at": "2026-08-23T14:10:00",
                        "confidence": 0.88,
                    },
                ]

        class ActiveVision(FakeVision):
            def snapshot(self):
                return {
                    "farmacia": {"state": "active"},
                    "farmacia2": {"state": "active"},
                }

        class FaceWithProfile(FakeFaceService):
            available = True
            status = "ready"

            def list_profiles(self):
                return [
                    {
                        "profile_id": "profile-1",
                        "display_name": "Pessoa 1",
                        "role": "employee",
                    }
                ]

        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            IdentificationStore(),
            ActiveVision(),
            face_service=FaceWithProfile(),
            activate_cameras=lambda: None,
        )

        self.assertTrue(controller.show())
        self.root.update()
        identification_rows = [
            controller._trees["identifications"].item(item, "values")
            for item in controller._trees["identifications"].get_children()
        ]

        self.assertEqual(len(identification_rows), 2)
        self.assertTrue(all("Pessoa 1" in row for row in identification_rows))
        self.assertIn("FARMACIA2", identification_rows[0])
        self.assertIn("2 confirmações", controller._labels["identification_summary"].cget("text"))
        self.assertEqual(controller._analysis_button.cget("text"), "Análise já ativa")
        self.assertEqual(str(controller._analysis_button.cget("state")), "disabled")
        controller.destroy()

    def test_renaming_selected_identification_updates_all_history_rows(self):
        class IdentificationStore(FakeStore):
            def list_vision_events(self, limit=300):
                return [
                    {
                        "event_type": "presence_confirmed",
                        "profile_id": "profile-1",
                        "stream": "farmacia2",
                        "occurred_at": "2026-08-23T15:42:00",
                        "confidence": 0.91,
                    },
                    {
                        "event_type": "presence_confirmed",
                        "profile_id": "profile-1",
                        "stream": "farmacia",
                        "occurred_at": "2026-08-23T14:10:00",
                        "confidence": 0.88,
                    },
                ]

        class RenamableFaceService(FakeFaceService):
            available = True
            status = "ready"

            def __init__(self):
                self.name = "Pessoa 1"

            def list_profiles(self):
                return [
                    {
                        "profile_id": "profile-1",
                        "display_name": self.name,
                        "role": "employee",
                    }
                ]

            def rename_profile(self, profile_id, display_name):
                if profile_id != "profile-1":
                    return False
                self.name = display_name
                return True

        face_service = RenamableFaceService()
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            IdentificationStore(),
            FakeVision(),
            face_service=face_service,
        )
        self.assertTrue(controller.show())
        self.root.update()
        first_row = controller._trees["identifications"].get_children()[0]
        controller._trees["identifications"].selection_set(first_row)

        with (
            patch(
                "wimi_analytics.desktop.simpledialog.askstring",
                return_value="Thiago",
            ),
            patch("wimi_analytics.desktop.messagebox.showinfo"),
        ):
            controller._rename_selected_identification()
            self.assertTrue(controller.wait_for_workers(timeout=2))
            controller._drain_ui_actions()

        identification_rows = [
            controller._trees["identifications"].item(item, "values")
            for item in controller._trees["identifications"].get_children()
        ]
        people_values = controller._trees["people"].item("profile-1", "values")
        self.assertTrue(all("Thiago" in row for row in identification_rows))
        self.assertIn("Thiago", people_values)
        controller.destroy()

    def test_reuses_one_native_window_and_groups_operational_views_in_central(self):
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
            evidence_archive=FakeEvidenceArchive(),
        )

        self.assertTrue(controller.show())
        first_window = controller.window
        self.root.update()
        top_level_tabs = [
            controller.notebook.tab(tab, "text") for tab in controller.notebook.tabs()
        ]
        self.assertEqual(
            top_level_tabs,
            ["Central", "Evidências"],
        )
        self.assertEqual(
            [
                controller._central_notebook.tab(tab, "text")
                for tab in controller._central_notebook.tabs()
            ],
            ["Visão geral", "Câmeras", "Rede", "Relatórios"],
        )
        self.assertIs(controller._operation_notebook, controller._central_notebook)
        self.assertIs(controller._network_reports_notebook, controller._central_notebook)
        self.assertEqual(
            [
                controller._evidence_notebook.tab(tab, "text")
                for tab in controller._evidence_notebook.tabs()
            ],
            ["Capturas", "Atividade e trajetos", "Pessoas observadas"],
        )

        controller.hide()
        self.assertTrue(controller.show())
        self.assertIs(controller.window, first_window)
        self.assertEqual(len(controller.notebook.tabs()), 2)

        controller.destroy()
        self.assertIsNone(controller.window)

    def test_external_window_destroy_cancels_refresh_callback(self):
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
            evidence_archive=FakeEvidenceArchive(),
        )

        self.assertTrue(controller.show())
        self.assertIsNotNone(controller._after_id)
        controller.window.destroy()
        self.root.update()

        self.assertIsNone(controller._after_id)

    def test_embeds_in_existing_panel_without_creating_a_second_window(self):
        panel = tk.Frame(self.root)
        panel.pack(fill="both", expand=True)
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
            evidence_archive=FakeEvidenceArchive(),
            parent=panel,
        )

        self.assertTrue(controller.show())
        embedded_frame = controller.window
        self.root.update()

        self.assertIsInstance(embedded_frame, tk.Frame)
        self.assertIs(embedded_frame.winfo_toplevel(), self.root)
        self.assertEqual(len(controller.notebook.tabs()), 2)

        controller.hide()
        self.assertTrue(controller.show())
        self.assertIs(controller.window, embedded_frame)
        controller.destroy()
        self.assertIsNone(controller.window)

    def test_enrollment_runs_off_tk_thread_and_destroy_is_queued(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingFaceService(FakeFaceService):
            available = True
            status = "ready"

            def enroll(self, name, frame, consent=False, role="authorized"):
                started.set()
                release.wait(2)
                return "profile-1"

        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=BlockingFaceService(),
        )
        controller.show()

        controller._start_enrollment("Pessoa", object(), "employee")
        self.assertTrue(started.wait(1))
        self.assertTrue(controller.enrollment_running)

        worker = threading.Thread(target=controller.request_destroy)
        worker.start()
        worker.join(1)
        self.assertIsNotNone(controller.window)

        release.set()
        self.assertTrue(controller.wait_for_workers(timeout=2))
        controller._drain_ui_actions()
        self.assertIsNone(controller.window)

    def test_refresh_shows_ranked_consent_profile_and_aggregate_network_boundary(self):
        class StoreWithSummary(FakeStore):
            def list_profile_presence_summary(self, limit=100):
                return [
                    {
                        "profile_id": "profile-1",
                        "visit_count": 4,
                        "observation_count": 12,
                        "observed_seconds": 3720.0,
                        "first_seen_at": "2026-08-15T09:00:00",
                        "last_seen_at": "2026-08-16T10:00:00",
                        "streams": ["farmacia", "farmacia2"],
                    }
                ]

            def summarize_network_traffic(self, limit=120, samples=None):
                return {
                    "state": "active",
                    "anomaly": "none",
                    "traffic_detected": True,
                    "current_bytes_per_second": 2048.0,
                    "baseline_bytes_per_second": 1024.0,
                    "scope": "this_host_aggregate_only",
                    "captures_content": False,
                }

        class FaceWithProfile(FakeFaceService):
            available = True
            status = "ready"

            def list_profiles(self):
                return [
                    {
                        "profile_id": "profile-1",
                        "display_name": "Pessoa Consentida",
                        "role": "employee",
                    }
                ]

        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            StoreWithSummary(),
            FakeVision(),
            face_service=FaceWithProfile(),
        )

        self.assertTrue(controller.show())
        self.root.update()
        people_values = controller._trees["people"].item("profile-1", "values")
        network_text = controller._labels["network_summary"].cget("text")

        self.assertIn("1º", people_values)
        self.assertIn("Pessoa Consentida", people_values)
        self.assertIn("Funcionário", people_values)
        self.assertIn("1 h 2 min", people_values)
        self.assertIn("Tráfego deste PC: 2,0 KB/s", network_text)
        self.assertIn("conteúdo não coletado", network_text)

    def test_network_view_unifies_gateway_devices_and_local_application_sessions(self):
        class NetworkCollector(FakeCollector):
            def snapshot(self):
                snapshot = super().snapshot()
                snapshot["payload"]["network"].update(
                    {
                        "coverage": "host_configuration_counters_and_presence",
                        "gateway_probe": {"state": "reachable", "latency_ms": 3.0},
                        "lan_visibility": {"state": "partial", "device_count": 1},
                        "application_visibility": {
                            "state": "available",
                            "application_count": 1,
                        },
                    }
                )
                return snapshot

        class NetworkStore(FakeStore):
            def list_network_device_sessions(self, limit=100):
                return [
                    {
                        "id": 1,
                        "device_id": "0123456789abcdef",
                        "ipv4": "192.168.7.20",
                        "interface_alias": "Ethernet",
                        "started_at": "2026-08-16T09:00:00",
                        "last_seen_at": "2026-08-16T09:05:00",
                        "duration_seconds": 300.0,
                        "last_state": "reachable",
                        "active": True,
                    }
                ]

            def list_local_application_sessions(self, limit=100):
                return [
                    {
                        "id": 2,
                        "application_name": "chrome",
                        "started_at": "2026-08-16T09:00:00",
                        "last_seen_at": "2026-08-16T09:05:00",
                        "duration_seconds": 300.0,
                        "current_connection_count": 4,
                        "peak_connection_count": 7,
                        "active": True,
                    }
                ]

        controller = AnalyticsDesktopWindow(
            self.root,
            NetworkCollector(),
            NetworkStore(),
            FakeVision(),
            face_service=FakeFaceService(),
        )

        self.assertTrue(controller.show())
        self.root.update()
        summary = controller._labels["network_summary"].cget("text")
        device_values = controller._trees["network_devices"].item("device-1", "values")
        app_values = controller._trees["network_applications"].item("application-2", "values")

        self.assertIn("Gateway: acessível em 3 ms", summary)
        self.assertIn("visão parcial", summary)
        self.assertIn("192.168.7.20", device_values)
        self.assertIn("chrome", app_values)
        self.assertIn("7", app_values)

    def test_network_view_marks_open_sessions_unconfirmed_when_sources_fail(self):
        class UnavailableCollector(FakeCollector):
            def snapshot(self):
                snapshot = super().snapshot()
                snapshot["payload"]["network"].update(
                    {
                        "lan_visibility": {"state": "unavailable", "device_count": 0},
                        "application_visibility": {
                            "state": "unavailable",
                            "application_count": 0,
                        },
                    }
                )
                return snapshot

        class OpenSessionsStore(FakeStore):
            def list_network_device_sessions(self, limit=100):
                return [
                    {
                        "id": 1,
                        "device_id": "0123456789abcdef",
                        "ipv4": "192.168.7.20",
                        "interface_alias": "Ethernet",
                        "started_at": "2026-08-16T09:00:00",
                        "last_seen_at": "2026-08-16T09:05:00",
                        "duration_seconds": 300.0,
                        "last_state": "reachable",
                        "active": True,
                    }
                ]

            def list_local_application_sessions(self, limit=100):
                return [
                    {
                        "id": 2,
                        "application_name": "chrome",
                        "started_at": "2026-08-16T09:00:00",
                        "last_seen_at": "2026-08-16T09:05:00",
                        "duration_seconds": 300.0,
                        "current_connection_count": 4,
                        "peak_connection_count": 7,
                        "active": True,
                    }
                ]

        controller = AnalyticsDesktopWindow(
            self.root,
            UnavailableCollector(),
            OpenSessionsStore(),
            FakeVision(),
            face_service=FakeFaceService(),
        )

        self.assertTrue(controller.show())
        self.root.update()

        self.assertIn(
            "Sem confirmação",
            controller._trees["network_devices"].item("device-1", "values"),
        )
        self.assertIn(
            "Sem confirmação",
            controller._trees["network_applications"].item("application-2", "values"),
        )

    def test_evidence_tab_renders_gallery_and_selection_controls(self):
        archive = FakeEvidenceArchive(
            [
                {
                    "evidence_id": "evidence-1",
                    "captured_at": "2026-08-16T10:00:00",
                    "expires_at": "2026-08-26T10:00:00",
                    "stream": "farmacia",
                    "category": "service_observation",
                    "byte_count": 32768,
                    "face_count": 2,
                    "anonymization": "face_regions_pixelated",
                },
                {
                    "evidence_id": "evidence-2",
                    "captured_at": "2026-08-16T10:05:00",
                    "expires_at": "2026-08-26T10:05:00",
                    "stream": "farmacia2",
                    "category": "service_observation",
                    "byte_count": 24576,
                    "face_count": 1,
                    "anonymization": "full_frame_pixelated_faces_flattened",
                },
            ]
        )
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
            evidence_archive=archive,
        )

        self.assertTrue(controller.show())
        self.assertEqual(archive.read_ids, [])
        controller.notebook.select(controller._evidence_tab)
        self.root.update()
        evidence_status = controller._labels["evidence_status"].cget("text")
        self.assertIn("10 dias", evidence_status)
        self.assertIn("revisão facial local criptografada", evidence_status)
        self.assertNotIn("identificáveis: não", evidence_status)
        self.assertIn("✕", controller._evidence_delete_button.cget("text"))
        self.assertEqual(set(controller._evidence_cards), {"evidence-1", "evidence-2"})
        self.assertEqual(set(controller._evidence_photo_cache), {"evidence-1", "evidence-2"})
        self.assertLessEqual(controller._evidence_photo_cache["evidence-1"].width(), 232)
        self.assertLessEqual(controller._evidence_photo_cache["evidence-1"].height(), 131)
        reads_after_render = list(archive.read_ids)
        controller._refresh_evidence()
        self.assertEqual(archive.read_ids, reads_after_render)

        controller._open_evidence_preview("evidence-1")
        self.root.update()
        self.assertIsNotNone(controller._evidence_preview_window)
        self.assertEqual(controller._evidence_preview_photo[0].width(), 320)
        self.assertEqual(controller._evidence_preview_photo[0].height(), 180)
        controller.hide()
        self.assertIsNone(controller._evidence_preview_window)
        controller.show()

        controller._select_all_evidence()
        self.assertEqual(controller._evidence_selected_ids, {"evidence-1", "evidence-2"})
        self.assertIn("2", controller._evidence_delete_button.cget("text"))
        controller._clear_evidence_selection()
        self.assertEqual(controller._evidence_selected_ids, set())
        self.assertEqual(str(controller._evidence_delete_button.cget("state")), "disabled")
        controller.destroy()

    def test_evidence_mousewheel_scrolls_gallery(self):
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
        )
        canvas = MagicMock()
        controller._evidence_gallery_canvas = canvas

        result = controller._on_evidence_mousewheel(SimpleNamespace(delta=-120))

        self.assertEqual(result, "break")
        canvas.yview_scroll.assert_called_once_with(1, "units")

    def test_unified_evidence_does_not_decrypt_until_visible(self):
        archive = FakeEvidenceArchive(
            [
                {
                    "evidence_id": "evidence-1",
                    "captured_at": "2026-08-16T10:00:00",
                    "expires_at": "2026-08-26T10:00:00",
                    "stream": "farmacia",
                    "category": "service_observation",
                    "byte_count": 32768,
                    "face_count": 1,
                    "anonymization": "face_regions_pixelated",
                }
            ]
        )
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
            evidence_archive=archive,
        )

        self.assertTrue(controller.show())
        self.assertEqual(archive.read_ids, [])

        controller.notebook.select(controller._evidence_tab)
        self.root.update()
        self.assertEqual(archive.read_ids, ["evidence-1"])
        controller.destroy()

    def test_evidence_tab_deletes_multiple_selected_captures(self):
        archive = FakeEvidenceArchive(
            [
                {
                    "evidence_id": f"evidence-{index}",
                    "captured_at": f"2026-08-16T10:0{index}:00",
                    "expires_at": f"2026-08-26T10:0{index}:00",
                    "stream": "farmacia",
                    "category": "service_observation",
                    "byte_count": 20000 + index,
                    "face_count": index,
                    "anonymization": "full_frame_pixelated_faces_flattened",
                }
                for index in range(1, 4)
            ]
        )
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
            evidence_archive=archive,
        )

        self.assertTrue(controller.show())
        controller.notebook.select(controller._evidence_tab)
        self.root.update()
        controller._set_evidence_selected("evidence-1", True)
        controller._set_evidence_selected("evidence-3", True)

        with patch("wimi_analytics.desktop.messagebox.askyesno", return_value=True):
            controller._delete_evidence()

        self.assertEqual(archive.deleted, ["evidence-1", "evidence-3"])
        self.assertEqual([item["evidence_id"] for item in archive.snapshots], ["evidence-2"])
        self.assertEqual(set(controller._evidence_cards), {"evidence-2"})
        self.assertEqual(controller._evidence_selected_ids, set())
        controller.destroy()

    def test_evidence_gallery_pages_thumbnails_but_selects_all_captures(self):
        archive = FakeEvidenceArchive(
            [
                {
                    "evidence_id": f"evidence-{index:02d}",
                    "captured_at": "2026-08-16T10:00:00",
                    "expires_at": "2026-08-26T10:00:00",
                    "stream": "farmacia",
                    "category": "service_observation",
                    "byte_count": 20000,
                    "face_count": 1,
                    "anonymization": "full_frame_pixelated_faces_flattened",
                }
                for index in range(30)
            ]
        )
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
            evidence_archive=archive,
        )

        self.assertTrue(controller.show())
        controller.notebook.select(controller._evidence_tab)
        self.root.update()
        self.assertEqual(len(controller._evidence_cards), 24)
        self.assertEqual(len(controller._evidence_photo_cache), 24)

        controller._select_all_evidence()
        self.assertEqual(len(controller._evidence_selected_ids), 30)
        controller._change_evidence_page(1)
        self.root.update()

        self.assertEqual(len(controller._evidence_cards), 6)
        self.assertEqual(len(controller._evidence_photo_cache), 6)
        self.assertIn("Página 2 de 2", controller._evidence_page_label.cget("text"))
        self.assertTrue(all(variable.get() for variable in controller._evidence_selection_vars.values()))
        controller.destroy()

    def test_profile_deletion_also_removes_operational_presence_history(self):
        class DeletionStore(FakeStore):
            def __init__(self):
                self.deleted_profiles = []

            def delete_profile_presence(self, profile_id):
                self.deleted_profiles.append(profile_id)
                return True

        class DeletionFaceService(FakeFaceService):
            available = True
            status = "ready"

            def __init__(self):
                self.profiles = [{"profile_id": "profile-1", "display_name": "Pessoa"}]

            def list_profiles(self):
                return list(self.profiles)

            def delete_profile(self, profile_id):
                self.profiles = [
                    item for item in self.profiles if item["profile_id"] != profile_id
                ]
                return True

        store = DeletionStore()
        face_service = DeletionFaceService()
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            store,
            FakeVision(),
            face_service=face_service,
        )
        self.assertTrue(controller.show())
        self.root.update()
        controller._trees["people"].selection_set("profile-1")

        with patch("wimi_analytics.desktop.messagebox.askyesno", return_value=True):
            controller._delete_person()
            self.assertTrue(controller.wait_for_workers(timeout=2))
            controller._drain_ui_actions()

        self.assertEqual(store.deleted_profiles, ["profile-1"])
        self.assertEqual(face_service.list_profiles(), [])

    def test_failed_biometric_delete_keeps_profile_available_for_retry(self):
        class DeletionStore(FakeStore):
            def __init__(self):
                self.deleted_profiles = []

            def delete_profile_presence(self, profile_id):
                self.deleted_profiles.append(profile_id)
                return True

        class FailingFaceService(FakeFaceService):
            available = True
            status = "ready"

            def list_profiles(self):
                return [{"profile_id": "profile-1", "display_name": "Pessoa"}]

            def delete_profile(self, profile_id):
                raise OSError("biometric_store_busy")

        store = DeletionStore()
        face_service = FailingFaceService()
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            store,
            FakeVision(),
            face_service=face_service,
        )
        self.assertTrue(controller.show())
        self.root.update()
        controller._trees["people"].selection_set("profile-1")

        with (
            patch("wimi_analytics.desktop.messagebox.askyesno", return_value=True),
            patch("wimi_analytics.desktop.messagebox.showerror") as showerror,
        ):
            controller._delete_person()
            self.assertTrue(controller.wait_for_workers(timeout=2))
            controller._drain_ui_actions()

        self.assertEqual(store.deleted_profiles, ["profile-1"])
        self.assertEqual(face_service.list_profiles()[0]["profile_id"], "profile-1")
        showerror.assert_called_once()

    def test_failed_operational_delete_does_not_touch_biometric_profile(self):
        class FailingStore(FakeStore):
            def delete_profile_presence(self, profile_id):
                raise OSError("analytics_store_busy")

        class TrackingFaceService(FakeFaceService):
            available = True
            status = "ready"

            def __init__(self):
                self.delete_calls = []

            def list_profiles(self):
                return [{"profile_id": "profile-1", "display_name": "Pessoa"}]

            def delete_profile(self, profile_id):
                self.delete_calls.append(profile_id)
                return True

        face_service = TrackingFaceService()
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FailingStore(),
            FakeVision(),
            face_service=face_service,
        )
        self.assertTrue(controller.show())
        self.root.update()
        controller._trees["people"].selection_set("profile-1")

        with (
            patch("wimi_analytics.desktop.messagebox.askyesno", return_value=True),
            patch("wimi_analytics.desktop.messagebox.showerror") as showerror,
        ):
            controller._delete_person()
            self.assertTrue(controller.wait_for_workers(timeout=2))
            controller._drain_ui_actions()

        self.assertEqual(face_service.delete_calls, [])
        showerror.assert_called_once()

    def test_profile_deletion_runs_off_tk_thread(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingStore(FakeStore):
            def delete_profile_presence(self, profile_id):
                started.set()
                release.wait(2)
                return True

        class FaceService(FakeFaceService):
            available = True
            status = "ready"

            def list_profiles(self):
                return [{"profile_id": "profile-1", "display_name": "Pessoa"}]

            def delete_profile(self, profile_id):
                return True

        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            BlockingStore(),
            FakeVision(),
            face_service=FaceService(),
        )
        self.assertTrue(controller.show())
        self.root.update()
        controller._trees["people"].selection_set("profile-1")

        with patch("wimi_analytics.desktop.messagebox.askyesno", return_value=True):
            controller._delete_person()
        self.assertTrue(started.wait(1))
        self.assertTrue(controller.deletion_running)
        self.root.update()

        release.set()
        self.assertTrue(controller.wait_for_workers(timeout=2))
        controller._drain_ui_actions()
        self.assertFalse(controller.deletion_running)


if __name__ == "__main__":
    unittest.main()
