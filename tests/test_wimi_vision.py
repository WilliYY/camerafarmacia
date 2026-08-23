import os
import sqlite3
import threading
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from wimi_analytics.biometric_storage import BiometricStore
from wimi_analytics.face_engine import IdentityMatcher, OpenCvFaceBackend
from wimi_analytics.face_engine import EnrollmentError, LocalFaceService
from wimi_analytics.person_engine import OpenCvPersonDetector
from wimi_analytics.privacy import DataProtectionError, protect_bytes, unprotect_bytes
from wimi_analytics.storage import AnalyticsStore
from wimi_analytics.vision import BehaviorAnalyzer, MotionAnalyzer, VisionCoordinator


class FakeVisionStore:
    def __init__(self):
        self.events = []

    def record_vision_event(self, event):
        self.events.append(dict(event))
        return event.get("event_id", "event")


class FakeFaceService:
    available = True
    status = "ready"

    def __init__(self):
        self.calls = 0

    def analyze_frame(self, stream, image):
        self.calls += 1
        return {
            "face_count": 1,
            "face_boxes": [(8, 6, 20, 20)],
            "identities": [
                {
                    "profile_id": "profile-1",
                    "display_name": "Pessoa autorizada",
                    "confidence": 0.91,
                }
            ],
        }


class FakeProtector:
    def protect(self, value):
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value):
        return bytes(byte ^ 0xA5 for byte in value)


class FakeEvidenceArchive:
    def __init__(self):
        self.captures = []

    def capture(self, stream, image, face_boxes, face_count, captured_at=None):
        self.captures.append(
            {
                "stream": stream,
                "size": image.size,
                "face_boxes": list(face_boxes),
                "face_count": face_count,
                "captured_at": captured_at,
            }
        )
        return "evidence-1"


class FakeFaceBackend:
    available = True
    status = "ready"
    model_id = "synthetic-test-model"

    def __init__(self, embeddings=None):
        self.embeddings = embeddings if embeddings is not None else [[1.0, 0.0, 0.0]]

    def extract_embeddings(self, image):
        return [list(value) for value in self.embeddings]


class FakePersonDetector:
    available = True
    status = "ready"

    def __init__(self, counts):
        self.counts = iter(counts)
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return [
            {"bbox": (index * 10, 0, 8, 20), "confidence": 0.90}
            for index in range(next(self.counts))
        ]


class MotionAnalyzerTests(unittest.TestCase):
    def test_motion_uses_hysteresis_and_emits_only_transitions(self):
        analyzer = MotionAnalyzer(
            width=32,
            height=18,
            pixel_threshold=15,
            changed_ratio_threshold=0.20,
            start_frames=2,
            end_frames=2,
        )
        black = Image.new("RGB", (64, 36), "black")
        white = Image.new("RGB", (64, 36), "white")
        started_at = datetime(2026, 8, 16, 10, 0, 0)

        self.assertIsNone(analyzer.analyze("farmacia", black, started_at)["event"])
        self.assertIsNone(
            analyzer.analyze("farmacia", white, started_at + timedelta(seconds=1))["event"]
        )
        started = analyzer.analyze(
            "farmacia", black, started_at + timedelta(seconds=2)
        )["event"]
        self.assertEqual(started["event_type"], "motion_start")

        self.assertIsNone(
            analyzer.analyze("farmacia", black, started_at + timedelta(seconds=3))["event"]
        )
        ended = analyzer.analyze(
            "farmacia", black, started_at + timedelta(seconds=4)
        )["event"]
        self.assertEqual(ended["event_type"], "motion_end")
        self.assertEqual(ended["duration_seconds"], 2.0)

    def test_each_camera_has_independent_motion_state(self):
        analyzer = MotionAnalyzer(start_frames=1, end_frames=1)
        black = Image.new("RGB", (64, 36), "black")
        white = Image.new("RGB", (64, 36), "white")

        analyzer.analyze("farmacia", black)
        result = analyzer.analyze("farmacia", white)
        other = analyzer.analyze("farmacia2", white)

        self.assertEqual(result["event"]["event_type"], "motion_start")
        self.assertIsNone(other["event"])

    def test_adaptive_mode_learns_bounded_camera_noise_before_emitting_motion(self):
        analyzer = MotionAnalyzer(
            width=64,
            height=36,
            pixel_threshold=10,
            changed_ratio_threshold=0.04,
            start_frames=1,
            end_frames=1,
            adaptive=True,
            calibration_samples=4,
            adaptation_window=12,
            adaptive_margin=0.02,
            adaptive_max_threshold=0.20,
        )
        quiet = Image.new("RGB", (64, 36), "black")
        camera_noise = quiet.copy()
        for x in range(5):
            for y in range(36):
                camera_noise.putpixel((x, y), (255, 255, 255))

        analyzer.analyze("farmacia", quiet)
        results = []
        for index in range(6):
            frame = camera_noise if index % 2 == 0 else quiet
            results.append(analyzer.analyze("farmacia", frame))

        self.assertTrue(results[-1]["calibrated"])
        self.assertGreater(results[-1]["motion_threshold"], 0.04)
        self.assertTrue(all(item["event"] is None for item in results))

        movement = analyzer.analyze("farmacia", Image.new("RGB", (64, 36), "white"))
        self.assertEqual(movement["event"]["event_type"], "motion_start")


class BehaviorAnalyzerTests(unittest.TestCase):
    def test_tracks_observed_presence_peak_and_dwell_with_absence_hysteresis(self):
        analyzer = BehaviorAnalyzer(end_observations=2)
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        motion = {
            "motion": "active",
            "changed_ratio": 0.12,
            "motion_threshold": 0.04,
            "motion_duration_seconds": 3.0,
        }

        candidate = analyzer.analyze("farmacia", motion, 2, started_at)
        started = analyzer.analyze(
            "farmacia", motion, 2, started_at + timedelta(seconds=3)
        )
        pending = analyzer.analyze(
            "farmacia", motion, 0, started_at + timedelta(seconds=6)
        )
        ended = analyzer.analyze(
            "farmacia", motion, 0, started_at + timedelta(seconds=9)
        )

        self.assertEqual(candidate["events"], [])
        self.assertEqual(candidate["observed_presence"], "unknown")
        self.assertEqual(
            [event["event_type"] for event in started["events"]],
            ["person_count", "observed_presence_start"],
        )
        self.assertEqual(started["activity_level"], "high")
        self.assertEqual(pending["observed_presence"], "active")
        self.assertFalse(any(event["event_type"] == "observed_presence_end" for event in pending["events"]))
        presence_end = next(
            event for event in ended["events"] if event["event_type"] == "observed_presence_end"
        )
        self.assertEqual(presence_end["count"], 2)
        self.assertEqual(presence_end["duration_seconds"], 3.0)
        self.assertEqual(ended["observed_presence"], "idle")

    def test_missing_person_observation_does_not_end_an_active_session(self):
        analyzer = BehaviorAnalyzer(
            end_observations=1,
            start_observations=1,
            count_observations=1,
            observation_timeout_seconds=5,
        )
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        motion = {"motion": "idle", "changed_ratio": 0.0, "motion_threshold": 0.04}

        analyzer.analyze("farmacia", motion, 1, started_at)
        recent = analyzer.analyze(
            "farmacia", motion, None, started_at + timedelta(seconds=3)
        )
        stale = analyzer.analyze(
            "farmacia", motion, None, started_at + timedelta(seconds=6)
        )

        self.assertEqual(recent["observed_presence"], "active")
        self.assertEqual(recent["person_count"], 1)
        self.assertEqual(stale["observed_presence"], "unknown")
        self.assertIsNone(stale["person_count"])
        self.assertEqual(stale["presence_duration_seconds"], 0.0)
        self.assertEqual(stale["events"], [])

    def test_single_positive_detection_does_not_create_presence_session(self):
        analyzer = BehaviorAnalyzer()
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        motion = {"motion": "idle", "changed_ratio": 0.0, "motion_threshold": 0.04}

        results = [
            analyzer.analyze("farmacia", motion, 1, started_at),
            analyzer.analyze("farmacia", motion, 0, started_at + timedelta(seconds=5)),
            analyzer.analyze("farmacia", motion, 0, started_at + timedelta(seconds=10)),
        ]

        event_types = [
            event["event_type"] for result in results for event in result["events"]
        ]
        self.assertNotIn("observed_presence_start", event_types)
        self.assertNotIn("observed_presence_end", event_types)

    def test_person_count_event_requires_same_count_twice(self):
        analyzer = BehaviorAnalyzer()
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        motion = {"motion": "idle", "changed_ratio": 0.0, "motion_threshold": 0.04}

        unstable = [
            analyzer.analyze("farmacia", motion, 1, started_at),
            analyzer.analyze("farmacia", motion, 2, started_at + timedelta(seconds=5)),
            analyzer.analyze("farmacia", motion, 1, started_at + timedelta(seconds=10)),
        ]
        stable = analyzer.analyze(
            "farmacia", motion, 1, started_at + timedelta(seconds=15)
        )

        self.assertFalse(
            any(
                event["event_type"] == "person_count"
                for result in unstable
                for event in result["events"]
            )
        )
        self.assertEqual(
            [event["event_type"] for event in stable["events"]], ["person_count"]
        )

    def test_stale_positive_candidate_cannot_confirm_presence_later(self):
        analyzer = BehaviorAnalyzer(observation_timeout_seconds=10)
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        motion = {"motion": "idle", "changed_ratio": 0.0, "motion_threshold": 0.04}
        first = analyzer.analyze("farmacia", motion, 1, started_at)
        analyzer.analyze(
            "farmacia", motion, None, started_at + timedelta(seconds=20)
        )
        new_candidate = analyzer.analyze(
            "farmacia", motion, 1, started_at + timedelta(seconds=30)
        )
        confirmed = analyzer.analyze(
            "farmacia", motion, 1, started_at + timedelta(seconds=35)
        )

        self.assertEqual(first["events"], [])
        self.assertEqual(new_candidate["events"], [])
        start_event = next(
            event
            for event in confirmed["events"]
            if event["event_type"] == "observed_presence_start"
        )
        self.assertEqual(start_event["occurred_at"], started_at + timedelta(seconds=35))
        self.assertEqual(confirmed["presence_duration_seconds"], 5.0)

    def test_close_all_finishes_only_confirmed_active_sessions(self):
        analyzer = BehaviorAnalyzer()
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        motion = {"motion": "idle", "changed_ratio": 0.0, "motion_threshold": 0.04}
        analyzer.analyze("farmacia", motion, 1, started_at)
        analyzer.analyze("farmacia", motion, 1, started_at + timedelta(seconds=5))

        events = analyzer.close_all(started_at + timedelta(seconds=10))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "observed_presence_end")
        self.assertEqual(events[0]["duration_seconds"], 5.0)
        self.assertEqual(analyzer.close_all(started_at + timedelta(seconds=11)), [])


class IdentityMatcherTests(unittest.TestCase):
    def test_requires_margin_and_two_consecutive_matches(self):
        matcher = IdentityMatcher(
            threshold=0.80,
            minimum_margin=0.10,
            confirmations=2,
        )
        profiles = {
            "profile-a": [1.0, 0.0, 0.0],
            "profile-b": [0.0, 1.0, 0.0],
        }

        self.assertIsNone(matcher.match("farmacia", [1.0, 0.0, 0.0], profiles))
        matched = matcher.match("farmacia", [1.0, 0.0, 0.0], profiles)
        self.assertEqual(matched["profile_id"], "profile-a")

        ambiguous = matcher.match("farmacia2", [0.71, 0.70, 0.0], profiles)
        self.assertIsNone(ambiguous)


class BiometricStoreTests(unittest.TestCase):
    def test_profile_payload_is_protected_separate_and_deletable(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "biometric.sqlite3"
            store = BiometricStore(db_path)
            service = LocalFaceService(
                store,
                backend=FakeFaceBackend(),
                protector=FakeProtector(),
                matcher=IdentityMatcher(confirmations=1),
            )
            profile_id = service.enroll(
                "Maria Teste",
                Image.new("RGB", (64, 64), "white"),
                consent=True,
                role="employee",
            )

            self.assertEqual(service.list_profiles()[0]["display_name"], "Maria Teste")
            self.assertEqual(service.list_profiles()[0]["role"], "employee")
            self.assertNotIn(b"Maria Teste", db_path.read_bytes())
            self.assertNotIn(b"employee", db_path.read_bytes())
            self.assertTrue(service.delete_profile(profile_id))
            self.assertEqual(service.list_profiles(), [])
            store.close()

    def test_recognition_returns_manual_role_without_auto_enrolling_unknown_face(self):
        with tempfile.TemporaryDirectory() as temp:
            store = BiometricStore(Path(temp) / "biometric.sqlite3")
            backend = FakeFaceBackend()
            service = LocalFaceService(
                store,
                backend=backend,
                protector=FakeProtector(),
                matcher=IdentityMatcher(confirmations=1),
            )
            image = Image.new("RGB", (64, 64), "white")
            service.enroll("João", image, consent=True, role="employee")

            recognized = service.analyze_frame("farmacia", image)
            self.assertEqual(recognized["identities"][0]["role"], "employee")

            backend.embeddings = [[0.0, 1.0, 0.0]]
            unknown = service.analyze_frame("farmacia", image)
            self.assertEqual(unknown["identities"], [])
            self.assertEqual(len(service.list_profiles()), 1)
            store.close()

    def test_profile_created_before_roles_remains_available_as_authorized(self):
        with tempfile.TemporaryDirectory() as temp:
            store = BiometricStore(Path(temp) / "biometric.sqlite3")
            protector = FakeProtector()
            legacy_payload = (
                b'{"schema_version":1,"model_id":"synthetic-test-model",'
                b'"display_name":"Perfil antigo","embedding":[1.0,0.0,0.0]}'
            )
            store.create_profile(protector.protect(legacy_payload))
            service = LocalFaceService(
                store,
                backend=FakeFaceBackend(),
                protector=protector,
                matcher=IdentityMatcher(confirmations=1),
            )

            self.assertEqual(service.list_profiles()[0]["role"], "authorized")
            store.close()

    def test_profile_rename_preserves_id_role_embedding_and_records_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "biometric.sqlite3"
            store = BiometricStore(db_path)
            service = LocalFaceService(
                store,
                backend=FakeFaceBackend(),
                protector=FakeProtector(),
                matcher=IdentityMatcher(confirmations=1),
            )
            image = Image.new("RGB", (64, 64), "white")
            profile_id = service.enroll(
                "Pessoa 1", image, consent=True, role="employee"
            )

            self.assertTrue(service.rename_profile(profile_id, "Thiago"))
            self.assertEqual(
                service.list_profiles(),
                [
                    {
                        "profile_id": profile_id,
                        "display_name": "Thiago",
                        "role": "employee",
                    }
                ],
            )
            recognized = service.analyze_frame("farmacia", image)
            self.assertEqual(recognized["identities"][0]["display_name"], "Thiago")
            connection = sqlite3.connect(db_path)
            try:
                audit = connection.execute(
                    "SELECT action, profile_id FROM biometric_audit ORDER BY audit_id"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(audit[-1], ("updated", profile_id))
            self.assertNotIn(b"Thiago", db_path.read_bytes())
            store.close()

    def test_deleted_payload_is_removed_from_database_and_wal(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "biometric.sqlite3"
            store = BiometricStore(db_path)
            protected = b"WIMI-UNIQUE-DELETED-PROFILE-" + (b"X" * 4096)
            profile_id = store.create_profile(protected)

            self.assertTrue(store.delete_profile(profile_id))
            store.close()

            for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
                if candidate.exists():
                    self.assertNotIn(protected, candidate.read_bytes())

    def test_enrollment_requires_consent_and_exactly_one_face(self):
        with tempfile.TemporaryDirectory() as temp:
            store = BiometricStore(Path(temp) / "biometric.sqlite3")
            service = LocalFaceService(
                store,
                backend=FakeFaceBackend(embeddings=[]),
                protector=FakeProtector(),
            )
            image = Image.new("RGB", (64, 64), "white")

            with self.assertRaises(EnrollmentError):
                service.enroll("Pessoa", image, consent=False)
            with self.assertRaises(EnrollmentError):
                service.enroll("Pessoa", image, consent=True, role="inferred_employee")
            with self.assertRaises(EnrollmentError):
                service.enroll("Pessoa", image, consent=True)
            store.close()

    def test_cleanup_failure_does_not_keep_deleted_profile_in_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            store = BiometricStore(Path(temp) / "biometric.sqlite3")
            service = LocalFaceService(
                store,
                backend=FakeFaceBackend(),
                protector=FakeProtector(),
            )
            profile_id = service.enroll(
                "Pessoa removida",
                Image.new("RGB", (64, 64), "white"),
                consent=True,
            )

            def fail_cleanup():
                raise OSError("disk_busy")

            store._compact_sensitive_store = fail_cleanup
            self.assertTrue(service.delete_profile(profile_id))
            self.assertEqual(service.list_profiles(), [])
            self.assertEqual(store.list_profiles(), [])
            self.assertIn("disk_busy", store.last_cleanup_error)
            connection = sqlite3.connect(store.path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            finally:
                connection.close()

    def test_face_analysis_returns_boxes_without_persisting_face_images(self):
        class BoxBackend(FakeFaceBackend):
            def analyze_faces(self, image):
                return [
                    {
                        "embedding": [1.0, 0.0, 0.0],
                        "bbox": (10, 12, 24, 28),
                    }
                ]

        with tempfile.TemporaryDirectory() as temp:
            store = BiometricStore(Path(temp) / "biometric.sqlite3")
            service = LocalFaceService(
                store,
                backend=BoxBackend(),
                protector=FakeProtector(),
            )

            result = service.analyze_frame(
                "farmacia", Image.new("RGB", (64, 64), "white")
            )

            self.assertEqual(result["face_count"], 1)
            self.assertEqual(result["face_boxes"], [(10, 12, 24, 28)])
            self.assertEqual(store.list_profiles(include_payload=True), [])
            store.close()


class VisionCoordinatorTests(unittest.TestCase):
    def test_person_detection_emits_bounded_observable_behavior_events(self):
        store = FakeVisionStore()
        detector = FakePersonDetector([2, 2, 0, 0])
        coordinator = VisionCoordinator(
            store=store,
            person_detector=detector,
            behavior_analyzer=BehaviorAnalyzer(end_observations=2),
            sample_interval_seconds=0,
            person_interval_seconds=0,
        )
        image = Image.new("RGB", (64, 36), "black")
        started_at = datetime(2026, 8, 23, 10, 0, 0)

        candidate = coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at, monotonic_now=1
        )
        active = coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=3), monotonic_now=2
        )
        coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=6), monotonic_now=3
        )
        last = coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=9), monotonic_now=4
        )

        self.assertEqual(candidate["person_count"], 2)
        self.assertEqual(candidate["observed_presence"], "unknown")
        self.assertEqual(active["observed_presence"], "active")
        self.assertEqual(last["observed_presence"], "idle")
        self.assertEqual(detector.calls, 4)
        event_types = [event["event_type"] for event in store.events]
        self.assertIn("person_count", event_types)
        self.assertIn("observed_presence_start", event_types)
        self.assertIn("observed_presence_end", event_types)

    def test_person_error_persists_and_stale_observation_becomes_unknown(self):
        class FailingAfterSuccessDetector:
            available = True
            status = "ready"

            def __init__(self):
                self.calls = 0

            def detect(self, image):
                self.calls += 1
                if self.calls == 1:
                    return [{"bbox": (1, 1, 10, 20), "confidence": 0.9}]
                raise RuntimeError("inference_failed")

        detector = FailingAfterSuccessDetector()
        coordinator = VisionCoordinator(
            store=FakeVisionStore(),
            person_detector=detector,
            behavior_analyzer=BehaviorAnalyzer(
                start_observations=1,
                count_observations=1,
                observation_timeout_seconds=5,
            ),
            sample_interval_seconds=0,
            person_interval_seconds=5,
        )
        image = Image.new("RGB", (64, 36), "black")
        started_at = datetime(2026, 8, 23, 10, 0, 0)

        first = coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at, monotonic_now=1
        )
        between = coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=1), monotonic_now=2
        )
        failed = coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=6), monotonic_now=7
        )
        stale = coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=7), monotonic_now=8
        )

        self.assertEqual(first["person_state"], "active")
        self.assertEqual(between["person_state"], "active")
        self.assertTrue(failed["person_state"].startswith("processing_error:"))
        self.assertEqual(stale["person_state"], failed["person_state"])
        self.assertEqual(stale["observed_presence"], "unknown")
        self.assertIsNone(stale["person_count"])

    def test_safe_stop_persists_end_for_active_observed_presence(self):
        store = FakeVisionStore()
        detector = FakePersonDetector([1, 1])
        coordinator = VisionCoordinator(
            store=store,
            person_detector=detector,
            sample_interval_seconds=0,
            person_interval_seconds=0,
        )
        image = Image.new("RGB", (64, 36), "black")
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at, monotonic_now=1
        )
        coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=5), monotonic_now=2
        )

        self.assertTrue(coordinator.stop())

        end_events = [
            event
            for event in store.events
            if event["event_type"] == "observed_presence_end"
        ]
        self.assertEqual(len(end_events), 1)
        self.assertEqual(end_events[0]["duration_seconds"], 5.0)

    def test_safe_stop_retries_pending_presence_end_before_reporting_success(self):
        class ToggleStore(FakeVisionStore):
            def __init__(self):
                super().__init__()
                self.fail = False

            def record_vision_event(self, event):
                if self.fail:
                    raise sqlite3.OperationalError("database_busy")
                return super().record_vision_event(event)

        store = ToggleStore()
        detector = FakePersonDetector([1, 1])
        coordinator = VisionCoordinator(
            store=store,
            person_detector=detector,
            sample_interval_seconds=0,
            person_interval_seconds=0,
        )
        image = Image.new("RGB", (64, 36), "black")
        started_at = datetime(2026, 8, 23, 10, 0, 0)
        coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at, monotonic_now=1
        )
        coordinator.process_frame_now(
            "farmacia", image, occurred_at=started_at + timedelta(seconds=5), monotonic_now=2
        )
        store.fail = True

        self.assertFalse(coordinator.stop())
        self.assertEqual(coordinator.pending_event_count, 1)
        store.fail = False
        self.assertTrue(coordinator.stop())
        self.assertEqual(coordinator.pending_event_count, 0)
        self.assertEqual(
            sum(
                event["event_type"] == "observed_presence_end"
                for event in store.events
            ),
            1,
        )

    def test_missing_person_model_keeps_existing_vision_active(self):
        class MissingPersonDetector:
            available = False
            status = "model_nanodet_person_missing"

        coordinator = VisionCoordinator(
            store=FakeVisionStore(),
            person_detector=MissingPersonDetector(),
            sample_interval_seconds=0,
        )

        snapshot = coordinator.process_frame_now(
            "farmacia", Image.new("RGB", (64, 36), "black")
        )

        self.assertIn(snapshot["state"], {"active", "calibrating"})
        self.assertEqual(snapshot["person_state"], "model_nanodet_person_missing")
        self.assertIsNone(snapshot["person_count"])

    def test_processes_existing_frame_without_writing_images(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(Path(temp).iterdir())
            store = FakeVisionStore()
            face_service = FakeFaceService()
            coordinator = VisionCoordinator(
                store=store,
                face_service=face_service,
                motion_analyzer=MotionAnalyzer(start_frames=1, end_frames=1),
                sample_interval_seconds=0,
            )
            black = Image.new("RGB", (64, 36), "black")
            white = Image.new("RGB", (64, 36), "white")

            coordinator.process_frame_now("farmacia", black)
            snapshot = coordinator.process_frame_now("farmacia", white)

            self.assertEqual(snapshot["state"], "active")
            self.assertEqual(snapshot["face_count"], 1)
            self.assertEqual(snapshot["motion"], "active")
            self.assertTrue(any(event["event_type"] == "motion_start" for event in store.events))
            self.assertEqual(set(Path(temp).iterdir()), before)

    def test_forwards_only_detected_face_regions_to_anonymized_archive(self):
        evidence = FakeEvidenceArchive()
        coordinator = VisionCoordinator(
            store=FakeVisionStore(),
            face_service=FakeFaceService(),
            evidence_archive=evidence,
            motion_analyzer=MotionAnalyzer(start_frames=1, end_frames=1),
            sample_interval_seconds=0,
            face_interval_seconds=0,
        )
        captured_at = datetime(2026, 8, 16, 10, 0, 0)
        image = Image.new("RGB", (64, 36), "black")

        result = coordinator.process_frame_now(
            "farmacia", image, occurred_at=captured_at, monotonic_now=1
        )

        self.assertEqual(len(evidence.captures), 1)
        self.assertEqual(evidence.captures[0]["face_boxes"], [(8, 6, 20, 20)])
        self.assertEqual(evidence.captures[0]["captured_at"], captured_at)
        self.assertNotIn("face_boxes", result)

    def test_hardware_guard_pauses_analysis_before_face_work(self):
        store = FakeVisionStore()
        face_service = FakeFaceService()
        coordinator = VisionCoordinator(
            store=store,
            face_service=face_service,
            hardware_guard=lambda: "memory_limit",
            sample_interval_seconds=0,
        )

        snapshot = coordinator.process_frame_now(
            "farmacia", Image.new("RGB", (64, 36), "black")
        )

        self.assertEqual(snapshot["state"], "paused")
        self.assertEqual(snapshot["pause_reason"], "memory_limit")
        self.assertEqual(face_service.calls, 0)
        self.assertEqual(store.events, [])

    def test_async_queue_is_bounded_and_stop_is_clean(self):
        coordinator = VisionCoordinator(
            store=FakeVisionStore(),
            face_service=None,
            sample_interval_seconds=0,
            queue_size=2,
        )
        coordinator.start()
        image = Image.new("RGB", (64, 36), "black")
        for _ in range(20):
            coordinator.submit("farmacia", image)
        coordinator.stop(timeout=2)

        self.assertLessEqual(coordinator.pending_count, 2)
        self.assertFalse(coordinator.running)

    def test_stop_reports_timeout_and_keeps_live_thread_reference(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingCoordinator(VisionCoordinator):
            def process_frame_now(self, *args, **kwargs):
                started.set()
                release.wait(2)

        coordinator = BlockingCoordinator(
            store=FakeVisionStore(),
            sample_interval_seconds=0,
        )
        coordinator.start()
        coordinator.submit("farmacia", Image.new("RGB", (64, 36), "black"))
        self.assertTrue(started.wait(1))

        self.assertFalse(coordinator.stop(timeout=0.01))
        self.assertTrue(coordinator.running)
        release.set()
        self.assertTrue(coordinator.stop(timeout=2))
        self.assertFalse(coordinator.running)

    def test_worker_recovers_after_one_transient_processing_error(self):
        recovered = threading.Event()

        class FlakyCoordinator(VisionCoordinator):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.calls = 0

            def process_frame_now(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient")
                recovered.set()
                return {"state": "active"}

        coordinator = FlakyCoordinator(
            store=FakeVisionStore(),
            sample_interval_seconds=0,
        )
        coordinator.start()
        image = Image.new("RGB", (64, 36), "black")
        coordinator.submit("farmacia", image)
        deadline = time.monotonic() + 1
        while coordinator.pending_count and time.monotonic() < deadline:
            time.sleep(0.01)
        coordinator.submit("farmacia2", image)

        self.assertTrue(recovered.wait(1))
        self.assertTrue(coordinator.stop(timeout=2))

    def test_motion_event_retries_with_stable_id_after_ambiguous_sqlite_error(self):
        with tempfile.TemporaryDirectory() as temp:
            real_store = AnalyticsStore(Path(temp) / "analytics.sqlite3")

            class AmbiguousStore:
                def __init__(self):
                    self.failed = False

                def record_vision_event(self, event):
                    event_id = real_store.record_vision_event(event)
                    if not self.failed and event.get("event_type") == "motion_start":
                        self.failed = True
                        raise OSError("sqlite_commit_result_unknown")
                    return event_id

            coordinator = VisionCoordinator(
                store=AmbiguousStore(),
                face_service=None,
                motion_analyzer=MotionAnalyzer(start_frames=1, end_frames=2),
                sample_interval_seconds=0,
            )
            black = Image.new("RGB", (64, 36), "black")
            white = Image.new("RGB", (64, 36), "white")

            coordinator.process_frame_now("farmacia", black, monotonic_now=1)
            coordinator.process_frame_now("farmacia", white, monotonic_now=2)
            self.assertEqual(coordinator.pending_event_count, 1)
            coordinator.process_frame_now("farmacia", white, monotonic_now=3)

            events = real_store.list_vision_events(limit=10)
            self.assertEqual(
                [event["event_type"] for event in events].count("motion_start"),
                1,
            )
            self.assertEqual(coordinator.pending_event_count, 0)
            real_store.close()

    def test_analysis_error_is_supported_by_real_store(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AnalyticsStore(Path(temp) / "analytics.sqlite3")
            coordinator = VisionCoordinator(store=store, sample_interval_seconds=0)

            coordinator._record_worker_error(
                "farmacia", datetime(2026, 8, 16, 10, 0, 0), RuntimeError("transient")
            )

            events = store.list_vision_events(limit=10)
            self.assertEqual(events[0]["event_type"], "analysis_error")
            store.close()

    def test_observable_behavior_events_are_supported_by_real_store(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AnalyticsStore(Path(temp) / "analytics.sqlite3")
            occurred_at = datetime(2026, 8, 23, 10, 0, 0)

            for event in (
                {"event_type": "person_count", "count": 2},
                {"event_type": "observed_presence_start", "count": 2},
                {
                    "event_type": "observed_presence_end",
                    "count": 2,
                    "duration_seconds": 45.0,
                },
            ):
                store.record_vision_event(
                    {**event, "stream": "farmacia", "occurred_at": occurred_at}
                )

            events = store.list_vision_events(limit=10)
            self.assertEqual(len(events), 3)
            self.assertEqual(
                {event["event_type"] for event in events},
                {"person_count", "observed_presence_start", "observed_presence_end"},
            )
            store.close()

    def test_pending_event_retry_queue_is_bounded(self):
        class FailingStore:
            def record_vision_event(self, event):
                raise OSError("sqlite_unavailable")

        coordinator = VisionCoordinator(store=FailingStore(), sample_interval_seconds=0)
        for index in range(100):
            coordinator._persist_event(
                {
                    "event_type": "face_count",
                    "stream": "farmacia",
                    "occurred_at": datetime(2026, 8, 16, 10, 0, 0),
                    "count": index,
                }
            )

        self.assertEqual(coordinator.pending_event_count, 64)
        self.assertEqual(coordinator._dropped_event_count, 36)

    def test_latest_frame_expires_and_large_queue_samples_are_bounded(self):
        coordinator = VisionCoordinator(
            store=FakeVisionStore(),
            sample_interval_seconds=0,
        )
        large = Image.new("RGB", (3840, 2160), "black")

        coordinator.process_frame_now("farmacia", large, monotonic_now=10.0)
        self.assertIsNotNone(
            coordinator.get_latest_frame("farmacia", max_age_seconds=5, monotonic_now=15.0)
        )
        self.assertIsNone(
            coordinator.get_latest_frame("farmacia", max_age_seconds=5, monotonic_now=15.01)
        )

        for stream in ("farmacia", "farmacia2", "farmacia3"):
            coordinator.submit(stream, large)
        self.assertLessEqual(coordinator.pending_count, 2)
        while coordinator.pending_count:
            item = coordinator._queue.get_nowait()
            self.assertLessEqual(item[1].width, 1280)
            self.assertLessEqual(item[1].height, 720)
            coordinator._queue.task_done()


class FaceBackendLimitsTests(unittest.TestCase):
    def test_backend_limits_resolution_and_faces_per_frame(self):
        class FakeFrame:
            shape = (540, 960, 3)

        class FakeNp:
            def __init__(self):
                self.seen_size = None

            def asarray(self, image):
                self.seen_size = image.size
                return FakeFrame()

        class FakeDetector:
            def __init__(self):
                self.input_size = None

            def setInputSize(self, size):
                self.input_size = size

            def detect(self, frame):
                return None, list(range(40))

        class FakeFeature:
            def flatten(self):
                return [1.0, 0.0]

        class FakeRecognizer:
            def __init__(self):
                self.calls = 0

            def alignCrop(self, frame, face):
                return face

            def feature(self, aligned):
                self.calls += 1
                return FakeFeature()

        class FakeCv2:
            COLOR_RGB2BGR = 1

            @staticmethod
            def cvtColor(frame, code):
                return frame

        backend = object.__new__(OpenCvFaceBackend)
        backend.available = True
        backend.status = "ready"
        backend.max_input_size = (960, 540)
        backend.max_faces = 8
        backend._np = FakeNp()
        backend._cv2 = FakeCv2()
        backend._detector = FakeDetector()
        backend._recognizer = FakeRecognizer()

        embeddings = backend.extract_embeddings(Image.new("RGB", (3840, 2160), "white"))

        self.assertEqual(len(embeddings), 8)
        self.assertEqual(backend._recognizer.calls, 8)
        self.assertEqual(backend._np.seen_size, (960, 540))
        self.assertEqual(backend._detector.input_size, (960, 540))


class PersonDetectorLimitsTests(unittest.TestCase):
    def test_preserves_official_model_rgb_channel_order(self):
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            self.skipTest(f"Runtime OpenCV indisponivel: {error}")
        detector = OpenCvPersonDetector.__new__(OpenCvPersonDetector)
        detector._cv2 = cv2
        detector._np = np
        detector._input_size = (416, 416)

        frame, geometry = detector._letterbox(
            Image.new("RGB", (416, 416), (10, 20, 30))
        )

        self.assertEqual(frame[0, 0].tolist(), [10, 20, 30])
        self.assertEqual(geometry, (1.0, 0, 0, 416, 416))

    def test_missing_verified_model_disables_detector_without_error(self):
        with tempfile.TemporaryDirectory() as temp:
            detector = OpenCvPersonDetector(model_dir=temp)

        self.assertFalse(detector.available)
        self.assertIn("model_nanodet_person_missing", detector.status)


class VisionCoordinatorCompatibilityTests(unittest.TestCase):
    def test_preserves_legacy_positional_constructor_order(self):
        store = FakeVisionStore()
        face_service = object()
        evidence_archive = object()
        motion_analyzer = MotionAnalyzer(adaptive=False)
        hardware_guard = lambda: None

        coordinator = VisionCoordinator(
            store,
            face_service,
            evidence_archive,
            motion_analyzer,
            hardware_guard,
            0.5,
            2.0,
            3,
            (960, 540),
        )

        self.assertIs(coordinator.face_service, face_service)
        self.assertIs(coordinator.evidence_archive, evidence_archive)
        self.assertIs(coordinator.motion_analyzer, motion_analyzer)
        self.assertIs(coordinator.hardware_guard, hardware_guard)
        self.assertEqual(coordinator.sample_interval_seconds, 0.5)
        self.assertEqual(coordinator.face_interval_seconds, 2.0)
        self.assertEqual(coordinator._queue.maxsize, 3)
        self.assertEqual(coordinator.max_frame_size, (960, 540))


@unittest.skipUnless(os.name == "nt", "DPAPI disponivel apenas no Windows")
class DpapiWindowsTests(unittest.TestCase):
    def test_round_trip_and_real_error_code(self):
        payload = b"wimi-dpapi-test"
        protected = protect_bytes(payload)
        self.assertEqual(unprotect_bytes(protected), payload)

        with self.assertRaises(DataProtectionError) as context:
            unprotect_bytes(b"not-a-dpapi-payload")
        self.assertNotRegex(str(context.exception), r"_failed_0$")


if __name__ == "__main__":
    unittest.main()
