import threading
import tkinter as tk
import unittest

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

    def list_reports(self, limit=200):
        return []


class FakeVision:
    def snapshot(self):
        return {}


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

    def test_reuses_one_native_window_and_preserves_six_tabs(self):
        controller = AnalyticsDesktopWindow(
            self.root,
            FakeCollector(),
            FakeStore(),
            FakeVision(),
            face_service=FakeFaceService(),
        )

        self.assertTrue(controller.show())
        first_window = controller.window
        self.root.update()
        self.assertEqual(len(controller.notebook.tabs()), 6)

        controller.hide()
        self.assertTrue(controller.show())
        self.assertIs(controller.window, first_window)
        self.assertEqual(len(controller.notebook.tabs()), 6)

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
            parent=panel,
        )

        self.assertTrue(controller.show())
        embedded_frame = controller.window
        self.root.update()

        self.assertIsInstance(embedded_frame, tk.Frame)
        self.assertIs(embedded_frame.winfo_toplevel(), self.root)
        self.assertEqual(len(controller.notebook.tabs()), 6)

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


if __name__ == "__main__":
    unittest.main()
