import threading
import tkinter as tk
import unittest
from unittest.mock import patch

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

    def list_network_samples(self, limit=200):
        return []

    def list_network_sessions(self, limit=50):
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

    def test_reuses_one_native_window_and_preserves_seven_tabs(self):
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
        self.assertEqual(len(controller.notebook.tabs()), 7)
        self.assertIn(
            "Evidências",
            [controller.notebook.tab(tab, "text") for tab in controller.notebook.tabs()],
        )

        controller.hide()
        self.assertTrue(controller.show())
        self.assertIs(controller.window, first_window)
        self.assertEqual(len(controller.notebook.tabs()), 7)

        controller.destroy()
        self.assertIsNone(controller.window)

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
        self.assertEqual(len(controller.notebook.tabs()), 7)

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

            def enroll(self, name, frame, consent=False):
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

        controller._start_enrollment("Pessoa", object())
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
                return [{"profile_id": "profile-1", "display_name": "Pessoa Consentida"}]

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
        self.assertIn("1 h 2 min", people_values)
        self.assertIn("Tráfego deste PC: 2,0 KB/s", network_text)
        self.assertIn("conteúdo não coletado", network_text)

    def test_evidence_tab_previews_anonymized_capture_and_deletes_on_request(self):
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
        self.root.update()
        self.assertIn("10 dias", controller._labels["evidence_status"].cget("text"))
        self.assertIn("✕", controller._evidence_delete_button.cget("text"))
        controller._trees["evidence"].selection_set("evidence-1")
        controller._show_selected_evidence()
        self.assertIsNotNone(controller._evidence_photo)

        archive.snapshots = []
        controller._refresh_evidence()
        self.assertIsNone(controller._evidence_photo)
        archive.snapshots = [
            {
                "evidence_id": "evidence-1",
                "captured_at": "2026-08-16T10:00:00",
                "expires_at": "2026-08-26T10:00:00",
                "stream": "farmacia",
                "category": "service_observation",
                "byte_count": 32768,
                "face_count": 2,
                "anonymization": "full_frame_pixelated_faces_flattened",
            }
        ]
        controller._refresh_evidence()
        controller._trees["evidence"].selection_set("evidence-1")

        with patch("wimi_analytics.desktop.messagebox.askyesno", return_value=True):
            controller._delete_evidence()

        self.assertEqual(archive.deleted, ["evidence-1"])
        self.assertEqual(controller._trees["evidence"].get_children(), ())

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
