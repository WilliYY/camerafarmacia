import io
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageFilter

from .privacy import DpapiProtector


class AnonymizedEvidenceArchive:
    def __init__(
        self,
        store,
        root,
        protector=None,
        retention_days=10,
        min_interval_seconds=900,
        max_total_bytes=256 * 1024 * 1024,
        max_image_size=(960, 540),
        jpeg_quality=72,
    ):
        self.store = store
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.protector = protector or DpapiProtector()
        self.retention_days = max(1, min(int(retention_days), 30))
        self.min_interval_seconds = max(0, int(min_interval_seconds))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.max_image_size = (
            max(320, min(int(max_image_size[0]), 1920)),
            max(180, min(int(max_image_size[1]), 1080)),
        )
        self.jpeg_quality = max(45, min(int(jpeg_quality), 85))
        self._lock = threading.RLock()
        self._last_capture = {}
        self._last_error = None
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def _path_for(self, relative_path):
        relative = Path(str(relative_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe_evidence_path")
        candidate = (self.root / relative).resolve()
        try:
            inside = os.path.commonpath([str(candidate), str(self.root)]) == str(self.root)
        except (OSError, ValueError):
            inside = False
        if not inside:
            raise ValueError("unsafe_evidence_path")
        return candidate

    @staticmethod
    def _normalize_boxes(face_boxes, face_count, width, height):
        if face_count <= 0 or len(face_boxes or []) < face_count:
            return []
        normalized = []
        for raw in list(face_boxes)[:face_count]:
            if not isinstance(raw, (tuple, list)) or len(raw) < 4:
                return []
            try:
                x, y, box_width, box_height = (int(round(float(value))) for value in raw[:4])
            except (TypeError, ValueError, OverflowError):
                return []
            if box_width <= 0 or box_height <= 0:
                return []
            margin_x = max(8, box_width // 4)
            margin_y = max(8, box_height // 3)
            left = max(0, x - margin_x)
            top = max(0, y - margin_y)
            right = min(width, x + box_width + margin_x)
            bottom = min(height, y + box_height + margin_y)
            if right - left < 4 or bottom - top < 4:
                return []
            normalized.append((left, top, right, bottom))
        return normalized

    def _anonymized_jpeg(self, image, face_boxes, face_count):
        source = image.convert("RGB")
        source_width, source_height = source.size
        scale = min(
            self.max_image_size[0] / source_width,
            self.max_image_size[1] / source_height,
            1.0,
        )
        if scale < 1.0:
            output = source.resize(
                (
                    max(1, round(source_width * scale)),
                    max(1, round(source_height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        else:
            output = source.copy()
        context_size = (
            max(1, output.width // 24),
            max(1, output.height // 24),
        )
        output = output.resize(context_size, Image.Resampling.BOX).resize(
            output.size, Image.Resampling.NEAREST
        )
        scaled_boxes = [
            tuple(float(value) * scale for value in box[:4])
            for box in face_boxes or []
        ]
        regions = self._normalize_boxes(
            scaled_boxes,
            int(face_count),
            output.width,
            output.height,
        )
        if len(regions) != int(face_count):
            return None
        for region_box in regions:
            region = output.crop(region_box)
            anonymized = region.resize((1, 1), Image.Resampling.BOX).resize(
                region.size, Image.Resampling.NEAREST
            )
            anonymized = anonymized.filter(ImageFilter.GaussianBlur(radius=4))
            output.paste(anonymized, region_box)
        buffer = io.BytesIO()
        output.save(
            buffer,
            format="JPEG",
            quality=self.jpeg_quality,
            optimize=True,
            progressive=False,
        )
        return buffer.getvalue()

    def capture(
        self,
        stream,
        image,
        face_boxes,
        face_count,
        captured_at=None,
        category="service_observation",
    ):
        captured_at = captured_at or datetime.now()
        stream = str(stream)[:80]
        with self._lock:
            previous = self._last_capture.get(stream)
            if previous is not None:
                elapsed = (captured_at - previous).total_seconds()
                if 0 <= elapsed < self.min_interval_seconds:
                    return None
            jpeg = self._anonymized_jpeg(image, face_boxes, face_count)
            if not jpeg:
                self._last_error = "complete_face_boxes_required"
                return None
            try:
                protected = self.protector.protect(jpeg)
            except Exception as error:
                self._last_error = str(error)[:160]
                return None
            current = self.status()
            if current["total_bytes"] + len(protected) > self.max_total_bytes:
                self._last_error = "capacity_reached"
                return None

            evidence_id = str(uuid.uuid4())
            relative_path = f"{evidence_id}.wimi"
            final_path = self._path_for(relative_path)
            temporary_path = final_path.with_suffix(".tmp")
            try:
                with open(temporary_path, "xb") as handle:
                    handle.write(protected)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, final_path)
                try:
                    os.chmod(final_path, 0o600)
                except OSError:
                    pass
                self.store.record_evidence_snapshot(
                    {
                        "evidence_id": evidence_id,
                        "captured_at": captured_at,
                        "expires_at": captured_at + timedelta(days=self.retention_days),
                        "stream": stream,
                        "category": category,
                        "relative_path": relative_path,
                        "byte_count": len(protected),
                        "face_count": int(face_count),
                        "anonymization": "full_frame_pixelated_faces_flattened",
                    }
                )
            except Exception as error:
                self._last_error = str(error)[:160]
                for path in (temporary_path, final_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                return None
            self._last_capture[stream] = captured_at
            self._last_error = None
            return evidence_id

    def list_snapshots(self, limit=200):
        return self.store.list_evidence_snapshots(limit=limit)

    def read_image(self, evidence_id):
        metadata = self.store.get_evidence_snapshot(evidence_id)
        if metadata is None:
            return None
        try:
            path = self._path_for(metadata["relative_path"])
            if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                return None
            jpeg = self.protector.unprotect(path.read_bytes())
            if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > 2 * 1024 * 1024:
                return None
            with Image.open(io.BytesIO(jpeg)) as image:
                image.load()
                return image.convert("RGB")
        except Exception as error:
            self._last_error = str(error)[:160]
            return None

    def delete(self, evidence_id):
        try:
            metadata = self.store.get_evidence_snapshot(evidence_id)
            if metadata is None:
                return False
            path = self._path_for(metadata["relative_path"])
            path.unlink(missing_ok=True)
            deleted = self.store.delete_evidence_snapshot(evidence_id)
        except Exception as error:
            self._last_error = str(error)[:160]
            return False
        if deleted:
            self._last_error = None
        return deleted

    def cleanup(self, now=None):
        now = now or datetime.now()
        deleted = 0
        failed = 0
        for _ in range(4):
            expired = self.store.list_evidence_snapshots(
                limit=2500,
                expires_before=now,
            )
            if not expired:
                break
            for metadata in expired:
                if self.delete(metadata["evidence_id"]):
                    deleted += 1
                else:
                    failed += 1
            if failed or len(expired) < 2500:
                break
        return {"deleted": deleted, "failed": failed}

    def status(self):
        summary = self.store.summarize_evidence_storage()
        total_bytes = summary["total_bytes"]
        if self._last_error == "capacity_reached":
            state = "capacity_reached"
        elif self._last_error:
            state = "warning"
        else:
            state = "active"
        return {
            "state": state,
            "count": summary["count"],
            "total_bytes": total_bytes,
            "max_total_bytes": self.max_total_bytes,
            "retention_days": self.retention_days,
            "last_error": self._last_error,
            "identifiable_faces_stored": False,
        }
