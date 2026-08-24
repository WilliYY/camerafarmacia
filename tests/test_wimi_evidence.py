import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from wimi_analytics.evidence import AnonymizedEvidenceArchive
from wimi_analytics.storage import AnalyticsStore


class FakeProtector:
    def protect(self, value):
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value):
        return bytes(byte ^ 0xA5 for byte in value)


class AnonymizedEvidenceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = AnalyticsStore(self.root / "runtime" / "analytics.sqlite3")
        self.archive = AnonymizedEvidenceArchive(
            self.store,
            self.root / "runtime" / "evidence",
            protector=FakeProtector(),
            min_interval_seconds=0,
            max_total_bytes=4 * 1024 * 1024,
        )

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    @staticmethod
    def patterned_frame():
        image = Image.new("RGB", (320, 180), "#17324D")
        for x in range(0, 72):
            for y in range(0, 72):
                value = 225 if ((x // 8) + (y // 8)) % 2 else 25
                image.putpixel((x, y), (value, value, value))
        for x in range(80, 160):
            for y in range(40, 120):
                value = 255 if (x + y) % 2 else 0
                image.putpixel((x, y), (value, 30, 255 - value))
        return image

    def test_capture_is_anonymized_encrypted_bounded_and_contains_no_identity(self):
        source = self.patterned_frame()
        captured_at = datetime(2026, 8, 16, 10, 0, 0)

        evidence_id = self.archive.capture(
            "farmacia",
            source,
            face_boxes=[(80, 40, 80, 80)],
            face_count=1,
            captured_at=captured_at,
        )

        self.assertIsNotNone(evidence_id)
        rows = self.archive.list_snapshots(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["expires_at"], "2026-08-26T10:00:00")
        self.assertEqual(
            rows[0]["anonymization"],
            "clear_context_faces_flattened_v4",
        )
        self.assertNotIn("profile", str(rows).casefold())
        self.assertNotIn("name", str(rows).casefold())

        protected_path = self.archive.root / rows[0]["relative_path"]
        self.assertTrue(protected_path.is_file())
        self.assertFalse(protected_path.read_bytes().startswith(b"\xff\xd8"))

        restored = self.archive.read_image(evidence_id)
        original_variance = sum(ImageStat.Stat(source.crop((80, 40, 160, 120))).stddev)
        restored_variance = sum(ImageStat.Stat(restored.crop((80, 40, 160, 120))).stddev)
        self.assertLess(restored_variance, original_variance * 0.35)
        context_variance = sum(ImageStat.Stat(restored.crop((0, 0, 72, 72))).stddev)
        self.assertGreater(context_variance, 45)

    def test_context_outside_detected_faces_remains_legible(self):
        source = Image.new("RGB", (120, 120), "black")
        for x in range(source.width):
            for y in range(source.height):
                value = 255 if ((x // 6) + (y // 6)) % 2 else 0
                source.putpixel((x, y), (value, value, value))

        jpeg = self.archive._anonymized_jpeg(source, face_boxes=[], face_count=0)
        with Image.open(io.BytesIO(jpeg)) as image:
            image.load()
            restored = image.convert("RGB")

        difference = ImageChops.difference(source, restored)
        mean_error = sum(ImageStat.Stat(difference).mean) / 3
        self.assertLess(mean_error, 25)

    def test_large_capture_keeps_720p_context_without_upscaling(self):
        source = Image.new("RGB", (1920, 1080), "#17324D")

        evidence_id = self.archive.capture(
            "farmacia",
            source,
            face_boxes=[(720, 240, 240, 240)],
            face_count=1,
            captured_at=datetime(2026, 8, 16, 10, 0, 0),
        )

        restored = self.archive.read_image(evidence_id)
        self.assertEqual(restored.size, (1280, 720))

    def test_refuses_capture_without_complete_face_boxes(self):
        image = self.patterned_frame()

        self.assertIsNone(
            self.archive.capture(
                "farmacia",
                image,
                face_boxes=[],
                face_count=1,
                captured_at=datetime(2026, 8, 16, 10, 0, 0),
            )
        )
        self.assertEqual(self.archive.list_snapshots(limit=10), [])
        self.assertEqual(list(self.archive.root.glob("*")), [])

    def test_retention_is_ten_days_and_manual_delete_removes_file_and_metadata(self):
        image = self.patterned_frame()
        old_id = self.archive.capture(
            "farmacia",
            image,
            face_boxes=[(80, 40, 80, 80)],
            face_count=1,
            captured_at=datetime(2026, 8, 1, 10, 0, 0),
        )
        current_id = self.archive.capture(
            "farmacia2",
            image,
            face_boxes=[(80, 40, 80, 80)],
            face_count=1,
            captured_at=datetime(2026, 8, 16, 10, 0, 0),
        )

        result = self.archive.cleanup(now=datetime(2026, 8, 16, 10, 0, 1))

        self.assertEqual(result["deleted"], 1)
        self.assertIsNone(self.store.get_evidence_snapshot(old_id))
        self.assertIsNotNone(self.store.get_evidence_snapshot(current_id))
        current_path = self.archive.root / self.store.get_evidence_snapshot(current_id)["relative_path"]
        self.assertTrue(current_path.exists())

        self.assertTrue(self.archive.delete(current_id))
        self.assertFalse(current_path.exists())
        self.assertEqual(self.archive.list_snapshots(limit=10), [])

    def test_rate_limit_and_capacity_stop_new_writes_without_deleting_retained_items(self):
        limited = AnonymizedEvidenceArchive(
            self.store,
            self.root / "runtime" / "limited",
            protector=FakeProtector(),
            min_interval_seconds=900,
            max_total_bytes=1,
        )
        image = self.patterned_frame()
        now = datetime(2026, 8, 16, 10, 0, 0)

        self.assertIsNone(
            limited.capture(
                "farmacia", image, [(80, 40, 80, 80)], 1, captured_at=now
            )
        )
        self.assertEqual(limited.status()["state"], "capacity_reached")
        self.assertEqual(limited.list_snapshots(limit=10), [])

        normal = AnonymizedEvidenceArchive(
            self.store,
            self.root / "runtime" / "normal",
            protector=FakeProtector(),
            min_interval_seconds=900,
            max_total_bytes=4 * 1024 * 1024,
        )
        first = normal.capture(
            "farmacia", image, [(80, 40, 80, 80)], 1, captured_at=now
        )
        second = normal.capture(
            "farmacia",
            image,
            [(80, 40, 80, 80)],
            1,
            captured_at=now + timedelta(minutes=5),
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(normal.list_snapshots(limit=10)), 1)
