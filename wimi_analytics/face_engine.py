import base64
import hashlib
import io
import json
import math
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from .privacy import DpapiProtector


class FaceEngineUnavailable(RuntimeError):
    pass


class EnrollmentError(ValueError):
    pass


PROFILE_ROLES = frozenset({"authorized", "contractor", "employee", "manager"})
PROVISIONAL_PREFIX = "pending:"
PROVISIONAL_RETENTION_DAYS = 10
PROVISIONAL_WRITE_INTERVAL_SECONDS = 300.0
PROVISIONAL_MATCH_THRESHOLD = 0.42
PROVISIONAL_MINIMUM_MARGIN = 0.04
PROVISIONAL_EQUIVALENT_CLUSTER_THRESHOLD = 0.65
PROVISIONAL_PENDING_THRESHOLD = 0.45
PROVISIONAL_TRACK_THRESHOLD = 0.20
PROVISIONAL_TRACK_MAX_AGE_SECONDS = 2.0
FACE_GALLERY_MAX_TEMPLATES = 5
FACE_GALLERY_DIVERSITY_THRESHOLD = 0.92
FACE_GALLERY_ADMISSION_THRESHOLD = 0.50
FACE_GALLERY_CONFIRMATION_THRESHOLD = 0.80
FACE_GALLERY_CONFIRMATION_SECONDS = 15.0


def normalize_profile_role(value):
    role = str(value or "authorized").strip().lower()
    if role not in PROFILE_ROLES:
        raise EnrollmentError("invalid_profile_role")
    return role


def _cosine_similarity(left, right):
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)


class IdentityMatcher:
    def __init__(self, threshold=0.45, minimum_margin=0.08, confirmations=2):
        self.threshold = max(-1.0, min(float(threshold), 1.0))
        self.minimum_margin = max(0.0, float(minimum_margin))
        self.confirmations = max(1, int(confirmations))
        self._candidates = {}

    def match(self, stream, embedding, profiles):
        scored = sorted(
            (
                (_cosine_similarity(embedding, profile_embedding), profile_id)
                for profile_id, profile_embedding in profiles.items()
            ),
            reverse=True,
        )
        if not scored or scored[0][0] < self.threshold:
            self._candidates.pop(stream, None)
            return None
        best_score, best_id = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1.0
        if best_score - second_score < self.minimum_margin:
            self._candidates.pop(stream, None)
            return None

        previous_id, previous_count = self._candidates.get(stream, (None, 0))
        count = previous_count + 1 if previous_id == best_id else 1
        self._candidates[stream] = (best_id, count)
        if count < self.confirmations:
            return None
        return {"profile_id": best_id, "confidence": max(0.0, min(best_score, 1.0))}


class OpenCvFaceBackend:
    def __init__(
        self,
        model_dir=None,
        runtime_dir=None,
        max_input_size=(960, 540),
        max_faces=8,
        score_threshold=0.80,
    ):
        package_dir = Path(__file__).resolve().parent
        self.model_dir = Path(model_dir or package_dir / "models")
        runtime = Path(runtime_dir or package_dir / "runtime" / "python")
        if runtime.exists() and str(runtime) not in sys.path:
            sys.path.insert(0, str(runtime))
        self.available = False
        self.status = "opencv_or_models_missing"
        self.model_id = "sface-opencv-zoo"
        self.max_input_size = (
            max(320, int(max_input_size[0])),
            max(180, int(max_input_size[1])),
        )
        self.max_faces = max(1, min(int(max_faces), 32))
        self.score_threshold = max(0.70, min(float(score_threshold), 0.95))
        self._cv2 = None
        self._np = None
        self._detector = None
        self._recognizer = None
        self._load()

    def _load_manifest(self):
        manifest_path = Path(__file__).resolve().parent / "model_manifest.json"
        if not manifest_path.is_file():
            raise FaceEngineUnavailable("model_manifest_missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        models = manifest.get("models") if isinstance(manifest, dict) else None
        if not isinstance(models, dict):
            raise FaceEngineUnavailable("model_manifest_invalid")
        resolved = {}
        for key in ("yunet", "sface"):
            item = models.get(key)
            if not isinstance(item, dict):
                raise FaceEngineUnavailable(f"model_{key}_missing")
            path = self.model_dir / str(item.get("filename", ""))
            expected = str(item.get("sha256", "")).lower()
            if not path.is_file() or len(expected) != 64:
                raise FaceEngineUnavailable(f"model_{key}_missing")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected:
                raise FaceEngineUnavailable(f"model_{key}_hash_mismatch")
            resolved[key] = path
        return resolved

    def _load(self):
        try:
            import cv2
            import numpy as np

            models = self._load_manifest()
            self._detector = cv2.FaceDetectorYN.create(
                str(models["yunet"]),
                "",
                (320, 320),
                self.score_threshold,
                0.30,
                max(32, self.max_faces * 4),
            )
            self._recognizer = cv2.FaceRecognizerSF.create(str(models["sface"]), "")
            self._cv2 = cv2
            self._np = np
        except Exception as error:
            self.status = str(error)[:160]
            return
        self.available = True
        self.status = "ready"

    def extract_embeddings(self, image):
        return [item["embedding"] for item in self.analyze_faces(image)]

    def analyze_faces(self, image):
        if not self.available:
            raise FaceEngineUnavailable(self.status)
        original_width, original_height = image.size
        scale = min(
            self.max_input_size[0] / original_width,
            self.max_input_size[1] / original_height,
            1.0,
        )
        if scale < 1.0:
            image = image.resize(
                (
                    max(1, round(original_width * scale)),
                    max(1, round(original_height * scale)),
                )
            )
        rgb = image.convert("RGB")
        frame = self._np.asarray(rgb)
        frame = self._cv2.cvtColor(frame, self._cv2.COLOR_RGB2BGR)
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(frame)
        if faces is None:
            return []
        results = []
        for face in faces[: self.max_faces]:
            aligned = self._recognizer.alignCrop(frame, face)
            feature = self._recognizer.feature(aligned)
            bbox = None
            try:
                x, y, box_width, box_height = (float(value) for value in face[:4])
                inverse_scale = 1.0 / scale
                x = max(0, min(round(x * inverse_scale), original_width - 1))
                y = max(0, min(round(y * inverse_scale), original_height - 1))
                box_width = max(1, min(round(box_width * inverse_scale), original_width - x))
                box_height = max(1, min(round(box_height * inverse_scale), original_height - y))
                bbox = (x, y, box_width, box_height)
            except (TypeError, ValueError, OverflowError):
                pass
            results.append(
                {
                    "embedding": [float(value) for value in feature.flatten()],
                    "bbox": bbox,
                }
            )
        return results


class LocalFaceService:
    def __init__(self, store, backend=None, protector=None, matcher=None):
        self.store = store
        self.backend = backend or OpenCvFaceBackend()
        self.protector = protector or DpapiProtector()
        self.matcher = matcher or IdentityMatcher()
        self._lock = threading.RLock()
        self.available = bool(self.backend.available)
        self.status = self.backend.status
        self._profiles = {}
        self._profile_galleries = {}
        self._names = {}
        self._roles = {}
        self._provisional_profiles = {}
        self._provisional_galleries = {}
        self._provisional_names = {}
        self._provisional_crops = {}
        self._provisional_metadata = {}
        self._pending_unknowns = []
        self._provisional_tracks = {}
        self._pending_provisional_templates = {}
        self._last_provisional_write = {}
        self._frame_sequence = 0
        if self.available:
            self.refresh_profiles()
            self.refresh_provisional_clusters()

    @staticmethod
    def _embedding_gallery(embedding, embeddings=None):
        primary = [float(item) for item in embedding]
        if not primary or not all(math.isfinite(item) for item in primary):
            raise ValueError("face_embedding_invalid")
        gallery = [primary]
        candidates = embeddings if isinstance(embeddings, list) else []
        for candidate in candidates:
            if not isinstance(candidate, list) or len(candidate) != len(primary):
                continue
            normalized = [float(item) for item in candidate]
            if not all(math.isfinite(item) for item in normalized):
                continue
            if any(_cosine_similarity(normalized, saved) >= 0.9999 for saved in gallery):
                continue
            gallery.append(normalized)
            if len(gallery) >= FACE_GALLERY_MAX_TEMPLATES:
                break
        return gallery

    def _encode(self, display_name, embedding, role, embeddings=None):
        gallery = self._embedding_gallery(embedding, embeddings)
        value = {
            "schema_version": 2 if len(gallery) > 1 else 1,
            "model_id": self.backend.model_id,
            "display_name": display_name,
            "role": normalize_profile_role(role),
            "embedding": gallery[0],
        }
        if len(gallery) > 1:
            value["embeddings"] = gallery
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def _decode(self, protected):
        payload = json.loads(self.protector.unprotect(bytes(protected)).decode("utf-8"))
        schema_version = payload.get("schema_version")
        if schema_version not in {1, 2} or payload.get("model_id") != self.backend.model_id:
            raise ValueError("face_profile_model_mismatch")
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise ValueError("face_profile_invalid")
        display_name = str(payload.get("display_name", "")).strip()
        if not display_name:
            raise ValueError("face_profile_name_missing")
        gallery = self._embedding_gallery(
            embedding,
            payload.get("embeddings") if schema_version == 2 else None,
        )
        return {
            "display_name": display_name,
            "role": normalize_profile_role(payload.get("role", "authorized")),
            "embedding": gallery[0],
            "embeddings": gallery,
        }

    def _encode_provisional(self, display_name, embedding, crop_jpeg, embeddings=None):
        gallery = self._embedding_gallery(embedding, embeddings)
        value = {
            "schema_version": 2 if len(gallery) > 1 else 1,
            "model_id": self.backend.model_id,
            "display_name": display_name,
            "embedding": gallery[0],
            "crop_jpeg": base64.b64encode(bytes(crop_jpeg)).decode("ascii"),
        }
        if len(gallery) > 1:
            value["embeddings"] = gallery
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def _decode_provisional(self, protected):
        payload = json.loads(self.protector.unprotect(bytes(protected)).decode("utf-8"))
        schema_version = payload.get("schema_version")
        if schema_version not in {1, 2} or payload.get("model_id") != self.backend.model_id:
            raise ValueError("provisional_face_model_mismatch")
        embedding = payload.get("embedding")
        display_name = str(payload.get("display_name") or "").strip()
        if not isinstance(embedding, list) or not embedding or not display_name:
            raise ValueError("provisional_face_invalid")
        crop_jpeg = base64.b64decode(payload.get("crop_jpeg") or "", validate=True)
        if not crop_jpeg.startswith(b"\xff\xd8") or len(crop_jpeg) > 64 * 1024:
            raise ValueError("provisional_face_crop_invalid")
        gallery = self._embedding_gallery(
            embedding,
            payload.get("embeddings") if schema_version == 2 else None,
        )
        return {
            "display_name": display_name,
            "embedding": gallery[0],
            "embeddings": gallery,
            "crop_jpeg": crop_jpeg,
        }

    def refresh_profiles(self):
        profiles = {}
        galleries = {}
        names = {}
        roles = {}
        for item in self.store.list_profiles(include_payload=True):
            try:
                decoded = self._decode(item["protected_profile"])
                profiles[item["profile_id"]] = decoded["embedding"]
                galleries[item["profile_id"]] = decoded["embeddings"]
                names[item["profile_id"]] = decoded["display_name"]
                roles[item["profile_id"]] = decoded["role"]
            except Exception:
                continue
        with self._lock:
            self._profiles = profiles
            self._profile_galleries = galleries
            self._names = names
            self._roles = roles

    def refresh_provisional_clusters(self):
        profiles = {}
        galleries = {}
        names = {}
        crops = {}
        metadata = {}
        list_clusters = getattr(self.store, "list_provisional_clusters", None)
        if not callable(list_clusters):
            return
        for item in list_clusters(include_payload=True):
            try:
                decoded = self._decode_provisional(item["protected_cluster"])
                cluster_id = str(item["cluster_id"])
                profiles[cluster_id] = decoded["embedding"]
                galleries[cluster_id] = decoded["embeddings"]
                names[cluster_id] = decoded["display_name"]
                crops[cluster_id] = decoded["crop_jpeg"]
                metadata[cluster_id] = {
                    "first_seen_at": item.get("first_seen_at"),
                    "last_seen_at": item.get("last_seen_at"),
                    "expires_at": item.get("expires_at"),
                    "observation_count": max(1, int(item.get("observation_count") or 1)),
                }
            except Exception:
                continue
        with self._lock:
            self._provisional_profiles = profiles
            self._provisional_galleries = galleries
            self._provisional_names = names
            self._provisional_crops = crops
            self._provisional_metadata = metadata

    @staticmethod
    def _face_crop_jpeg(image, bbox):
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        try:
            x, y, width, height = (int(round(float(value))) for value in bbox)
        except (TypeError, ValueError, OverflowError):
            return None
        if width < 8 or height < 8:
            return None
        padding = max(8, round(max(width, height) * 0.35))
        left = max(0, x - padding)
        top = max(0, y - padding)
        right = min(image.width, x + width + padding)
        bottom = min(image.height, y + height + padding)
        if right <= left or bottom <= top:
            return None
        crop = image.convert("RGB").crop((left, top, right, bottom))
        scale = min(384 / max(crop.size), max(1.0, 224 / max(crop.size)))
        if scale != 1.0:
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )
        for quality in (90, 82, 74):
            buffer = io.BytesIO()
            crop.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
            )
            value = buffer.getvalue()
            if len(value) <= 64 * 1024:
                return value
        return None

    def _next_provisional_name(self):
        used = set()
        for name in self._provisional_names.values():
            prefix, separator, number = str(name).rpartition(" ")
            if separator and prefix == "Pessoa" and number.isdigit():
                used.add(int(number))
        number = 1
        while number in used:
            number += 1
        return f"Pessoa {number}"

    def _provisional_similarity(self, embedding, cluster_id):
        gallery = self._provisional_galleries.get(cluster_id)
        if not gallery:
            candidate = self._provisional_profiles.get(cluster_id)
            gallery = [candidate] if candidate is not None else []
        scores = [_cosine_similarity(embedding, candidate) for candidate in gallery]
        return max(scores) if scores else -1.0

    def _provisional_cluster_similarity(self, left_id, right_id):
        return _cosine_similarity(
            self._provisional_profiles[left_id],
            self._provisional_profiles[right_id],
        )

    def _match_provisional(self, embedding, excluded_cluster_ids=None):
        excluded_cluster_ids = set(excluded_cluster_ids or ())
        scored = sorted(
            (
                (self._provisional_similarity(embedding, cluster_id), cluster_id)
                for cluster_id in self._provisional_profiles
                if cluster_id not in excluded_cluster_ids
            ),
            reverse=True,
        )
        if not scored or scored[0][0] < PROVISIONAL_MATCH_THRESHOLD:
            return None
        best_score, cluster_id = scored[0]
        selected_score = best_score
        second_score = scored[1][0] if len(scored) > 1 else -1.0
        if best_score - second_score < PROVISIONAL_MINIMUM_MARGIN:
            equivalent = [
                (score, candidate_id)
                for score, candidate_id in scored
                if best_score - score < PROVISIONAL_MINIMUM_MARGIN
            ]
            for index, (_score, candidate_id) in enumerate(equivalent):
                for _other_score, other_id in equivalent[index + 1 :]:
                    if self._provisional_cluster_similarity(
                        candidate_id, other_id
                    ) < PROVISIONAL_EQUIVALENT_CLUSTER_THRESHOLD:
                        return None
            selected_score, cluster_id = max(
                equivalent,
                key=lambda item: (
                    int(
                        (
                            self._provisional_metadata.get(item[1]) or {}
                        ).get("observation_count")
                        or 0
                    ),
                    item[0],
                    item[1],
                ),
            )
        return cluster_id, max(0.0, min(float(selected_score), 1.0))

    @staticmethod
    def _track_geometry_score(current_bbox, previous_bbox):
        try:
            current_x, current_y, current_width, current_height = (
                float(value) for value in current_bbox
            )
            previous_x, previous_y, previous_width, previous_height = (
                float(value) for value in previous_bbox
            )
        except (TypeError, ValueError, OverflowError):
            return None
        if min(current_width, current_height, previous_width, previous_height) <= 0:
            return None
        current_right = current_x + current_width
        current_bottom = current_y + current_height
        previous_right = previous_x + previous_width
        previous_bottom = previous_y + previous_height
        intersection_width = max(
            0.0, min(current_right, previous_right) - max(current_x, previous_x)
        )
        intersection_height = max(
            0.0, min(current_bottom, previous_bottom) - max(current_y, previous_y)
        )
        intersection = intersection_width * intersection_height
        union = (
            current_width * current_height
            + previous_width * previous_height
            - intersection
        )
        overlap = intersection / union if union > 0 else 0.0
        current_center = (
            current_x + current_width / 2.0,
            current_y + current_height / 2.0,
        )
        previous_center = (
            previous_x + previous_width / 2.0,
            previous_y + previous_height / 2.0,
        )
        distance = math.hypot(
            current_center[0] - previous_center[0],
            current_center[1] - previous_center[1],
        )
        scale = max(current_width, current_height, previous_width, previous_height)
        normalized_distance = distance / scale
        if overlap < 0.10 and normalized_distance > 0.75:
            return None
        return max(overlap, max(0.0, 1.0 - normalized_distance))

    def _match_provisional_track(
        self,
        stream,
        bbox,
        embedding,
        monotonic_now,
        excluded_cluster_ids=None,
    ):
        excluded_cluster_ids = set(excluded_cluster_ids or ())
        tracks = self._provisional_tracks.get(str(stream)) or {}
        best = None
        best_score = -1.0
        for cluster_id, track in tracks.items():
            if cluster_id in excluded_cluster_ids:
                continue
            if cluster_id not in self._provisional_profiles:
                continue
            if monotonic_now - float(track.get("last_seen") or 0.0) > PROVISIONAL_TRACK_MAX_AGE_SECONDS:
                continue
            similarity = self._provisional_similarity(embedding, cluster_id)
            if similarity < PROVISIONAL_TRACK_THRESHOLD:
                continue
            geometry = self._track_geometry_score(bbox, track.get("bbox"))
            if geometry is None:
                continue
            score = geometry * 0.65 + max(0.0, similarity) * 0.35
            if score > best_score:
                best = (cluster_id, max(0.0, min(float(similarity), 1.0)))
                best_score = score
        return best

    def _remember_provisional_track(self, stream, cluster_id, bbox, monotonic_now):
        stream = str(stream)
        tracks = self._provisional_tracks.setdefault(stream, {})
        tracks[cluster_id] = {
            "bbox": tuple(bbox),
            "last_seen": float(monotonic_now),
        }
        stale = sorted(
            tracks,
            key=lambda value: float(tracks[value].get("last_seen") or 0.0),
            reverse=True,
        )[16:]
        for stale_cluster_id in stale:
            tracks.pop(stale_cluster_id, None)

    def _confirmed_candidates(self, embedding):
        candidates = {}
        for profile_id, primary in self._profiles.items():
            gallery = self._profile_galleries.get(profile_id) or [primary]
            candidates[profile_id] = max(
                gallery,
                key=lambda candidate: _cosine_similarity(embedding, candidate),
            )
        return candidates

    def _resembles_confirmed_profile(self, embedding):
        scored = sorted(
            _cosine_similarity(embedding, candidate)
            for candidate in self._confirmed_candidates(embedding).values()
        )
        if not scored or scored[-1] < self.matcher.threshold:
            return False
        second = scored[-2] if len(scored) > 1 else -1.0
        return scored[-1] - second >= self.matcher.minimum_margin

    def _remember_provisional_template(self, cluster_id, embedding, monotonic_now):
        gallery = self._provisional_galleries.setdefault(
            cluster_id,
            [list(self._provisional_profiles[cluster_id])],
        )
        candidate = [float(item) for item in embedding]
        if (
            len(candidate) != len(gallery[0])
            or not all(math.isfinite(item) for item in candidate)
        ):
            return False
        best_score = max(
            _cosine_similarity(candidate, saved) for saved in gallery
        )
        if (
            best_score < FACE_GALLERY_ADMISSION_THRESHOLD
            or len(gallery) >= FACE_GALLERY_MAX_TEMPLATES
        ):
            self._pending_provisional_templates.pop(cluster_id, None)
            return False
        if best_score >= FACE_GALLERY_DIVERSITY_THRESHOLD:
            self._pending_provisional_templates.pop(cluster_id, None)
            return False

        pending = self._pending_provisional_templates.get(cluster_id)
        pending_is_compatible = (
            pending is not None
            and monotonic_now - float(pending["last_seen"])
            <= FACE_GALLERY_CONFIRMATION_SECONDS
            and _cosine_similarity(candidate, pending["embedding"])
            >= FACE_GALLERY_CONFIRMATION_THRESHOLD
        )
        if not pending_is_compatible:
            self._pending_provisional_templates[cluster_id] = {
                "embedding": candidate,
                "count": 1,
                "last_seen": float(monotonic_now),
            }
            return False

        count = int(pending["count"]) + 1
        confirmed = [
            ((float(previous) * (count - 1)) + float(current)) / count
            for previous, current in zip(pending["embedding"], candidate)
        ]
        self._pending_provisional_templates.pop(cluster_id, None)
        gallery.append(confirmed)
        return True

    def _touch_provisional(self, cluster_id, monotonic_now, embedding=None):
        gallery_changed = False
        if embedding is not None:
            gallery_changed = self._remember_provisional_template(
                cluster_id, embedding, monotonic_now
            )
        previous = self._last_provisional_write.get(cluster_id, -1e9)
        if (
            not gallery_changed
            and monotonic_now - previous < PROVISIONAL_WRITE_INTERVAL_SECONDS
        ):
            return
        metadata = self._provisional_metadata.get(cluster_id) or {}
        crop_jpeg = self._provisional_crops.get(cluster_id)
        embedding = self._provisional_profiles.get(cluster_id)
        display_name = self._provisional_names.get(cluster_id)
        if not crop_jpeg or embedding is None or not display_name:
            return
        try:
            protected = self.protector.protect(
                self._encode_provisional(
                    display_name,
                    embedding,
                    crop_jpeg,
                    self._provisional_galleries.get(cluster_id),
                )
            )
            update = getattr(self.store, "update_provisional_cluster", None)
            updated = callable(update) and update(
                cluster_id,
                protected,
                observed_at=datetime.now(),
                retention_days=PROVISIONAL_RETENTION_DAYS,
            )
        except Exception:
            updated = False
        if not updated:
            return
        self._last_provisional_write[cluster_id] = monotonic_now
        metadata["observation_count"] = max(
            1, int(metadata.get("observation_count") or 1) + 1
        )
        metadata["last_seen_at"] = datetime.now().isoformat(timespec="seconds")
        self._provisional_metadata[cluster_id] = metadata

    def _observe_unknown(
        self,
        stream,
        image,
        bbox,
        embedding,
        frame_token,
        monotonic_now,
        excluded_cluster_ids=None,
    ):
        match = self._match_provisional(embedding, excluded_cluster_ids)
        if match is None:
            match = self._match_provisional_track(
                stream,
                bbox,
                embedding,
                monotonic_now,
                excluded_cluster_ids,
            )
        if match is not None:
            cluster_id, confidence = match
            self._remember_provisional_track(stream, cluster_id, bbox, monotonic_now)
            self._touch_provisional(cluster_id, monotonic_now, embedding)
            return cluster_id, confidence

        stream = str(stream)
        self._pending_unknowns = [
            item
            for item in self._pending_unknowns
            if monotonic_now - item["last_seen"] <= 30.0
        ]
        best = None
        best_score = -1.0
        for item in self._pending_unknowns:
            if item.get("stream") != stream:
                continue
            if item.get("frame_token") == frame_token:
                continue
            score = _cosine_similarity(embedding, item["embedding"])
            if score > best_score:
                best = item
                best_score = score
        if best is None or best_score < PROVISIONAL_PENDING_THRESHOLD:
            self._pending_unknowns.append(
                {
                    "stream": stream,
                    "embedding": list(embedding),
                    "count": 1,
                    "last_seen": monotonic_now,
                    "frame_token": frame_token,
                }
            )
            self._pending_unknowns = self._pending_unknowns[-16:]
            return None

        count = max(1, int(best["count"])) + 1
        best["embedding"] = [
            ((float(previous) * (count - 1)) + float(current)) / count
            for previous, current in zip(best["embedding"], embedding)
        ]
        best["count"] = count
        best["last_seen"] = monotonic_now
        best["frame_token"] = frame_token
        if count < 3:
            return None

        crop_jpeg = self._face_crop_jpeg(image, bbox)
        if crop_jpeg is None:
            self._pending_unknowns.remove(best)
            return None
        display_name = self._next_provisional_name()
        try:
            protected = self.protector.protect(
                self._encode_provisional(display_name, best["embedding"], crop_jpeg)
            )
            create = getattr(self.store, "create_provisional_cluster", None)
            if not callable(create):
                return None
            cluster_id = create(
                protected,
                observed_at=datetime.now(),
                retention_days=PROVISIONAL_RETENTION_DAYS,
            )
        except Exception:
            self._pending_unknowns.remove(best)
            return None
        self._pending_unknowns.remove(best)
        self._provisional_profiles[cluster_id] = list(best["embedding"])
        self._provisional_galleries[cluster_id] = [list(best["embedding"])]
        self._provisional_names[cluster_id] = display_name
        self._provisional_crops[cluster_id] = crop_jpeg
        self._provisional_metadata[cluster_id] = {
            "first_seen_at": datetime.now().isoformat(timespec="seconds"),
            "last_seen_at": datetime.now().isoformat(timespec="seconds"),
            "expires_at": None,
            "observation_count": 1,
        }
        self._last_provisional_write[cluster_id] = monotonic_now
        self._remember_provisional_track(stream, cluster_id, bbox, monotonic_now)
        return cluster_id, max(0.0, min(float(best_score), 1.0))

    def enroll(self, display_name, image, consent=False, role="authorized"):
        if not consent:
            raise EnrollmentError("explicit_consent_required")
        if not self.available:
            raise FaceEngineUnavailable(self.status)
        role = normalize_profile_role(role)
        with self._lock:
            embeddings = self.backend.extract_embeddings(image)
        if len(embeddings) != 1:
            raise EnrollmentError("exactly_one_face_required")
        display_name = " ".join(str(display_name).split())[:80]
        if not display_name:
            raise EnrollmentError("display_name_required")
        protected = self.protector.protect(
            self._encode(display_name, embeddings[0], role)
        )
        profile_id = self.store.create_profile(protected)
        self.refresh_profiles()
        return profile_id

    def list_profiles(self):
        with self._lock:
            confirmed = [
                {
                    "profile_id": profile_id,
                    "display_name": self._names[profile_id],
                    "role": self._roles.get(profile_id, "authorized"),
                }
                for profile_id in sorted(self._names, key=lambda value: self._names[value].casefold())
            ]
            provisional = [
                {
                    "profile_id": f"{PROVISIONAL_PREFIX}{cluster_id}",
                    "display_name": self._provisional_names[cluster_id],
                    "role": "pending",
                    "provisional": True,
                    **dict(self._provisional_metadata.get(cluster_id) or {}),
                }
                for cluster_id in sorted(
                    self._provisional_names,
                    key=lambda value: self._provisional_names[value].casefold(),
                )
            ]
            return confirmed + provisional

    def rename_profile(self, profile_id, display_name, role="authorized", consent=False):
        if not self.available:
            raise FaceEngineUnavailable(self.status)
        profile_id = str(profile_id)
        display_name = " ".join(str(display_name).split())[:80]
        if not display_name:
            raise EnrollmentError("display_name_required")
        if profile_id.startswith(PROVISIONAL_PREFIX):
            if not consent:
                raise EnrollmentError("explicit_consent_required")
            cluster_id = profile_id[len(PROVISIONAL_PREFIX) :]
            with self._lock:
                embedding = self._provisional_profiles.get(cluster_id)
                if embedding is None:
                    return False
                gallery = self._provisional_galleries.get(cluster_id) or [embedding]
                protected = self.protector.protect(
                    self._encode(
                        display_name,
                        embedding,
                        normalize_profile_role(role),
                        gallery,
                    )
                )
                promote = getattr(self.store, "promote_provisional_cluster", None)
                if not callable(promote):
                    return False
                new_profile_id = promote(cluster_id, protected)
                if not new_profile_id:
                    return False
                self._provisional_profiles.pop(cluster_id, None)
                self._provisional_galleries.pop(cluster_id, None)
                self._provisional_names.pop(cluster_id, None)
                self._provisional_crops.pop(cluster_id, None)
                self._provisional_metadata.pop(cluster_id, None)
                self._pending_provisional_templates.pop(cluster_id, None)
                self._last_provisional_write.pop(cluster_id, None)
            self.refresh_profiles()
            return new_profile_id
        with self._lock:
            embedding = self._profiles.get(profile_id)
            if embedding is None:
                return False
            role = self._roles.get(profile_id, "authorized")
            protected = self.protector.protect(
                self._encode(display_name, embedding, role)
            )
            if not self.store.update_profile(profile_id, protected):
                return False
            self._names[profile_id] = display_name
        return True

    def delete_profile(self, profile_id):
        profile_id = str(profile_id)
        if profile_id.startswith(PROVISIONAL_PREFIX):
            cluster_id = profile_id[len(PROVISIONAL_PREFIX) :]
            delete = getattr(self.store, "delete_provisional_cluster", None)
            deleted = bool(callable(delete) and delete(cluster_id))
            if deleted:
                self.refresh_provisional_clusters()
            return deleted
        deleted = self.store.delete_profile(profile_id)
        if deleted:
            self.refresh_profiles()
        return deleted

    def read_profile_face(self, profile_id):
        profile_id = str(profile_id)
        if not profile_id.startswith(PROVISIONAL_PREFIX):
            return None
        with self._lock:
            jpeg = self._provisional_crops.get(profile_id[len(PROVISIONAL_PREFIX) :])
        if not jpeg:
            return None
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                image.load()
                return image.convert("RGB")
        except Exception:
            return None

    def cleanup_provisional(self, now=None):
        cleanup = getattr(self.store, "cleanup_provisional_clusters", None)
        if not callable(cleanup):
            return 0
        deleted = cleanup(now=now)
        if deleted:
            self.refresh_provisional_clusters()
        return deleted

    def analyze_frame(self, stream, image):
        if not self.available:
            return {
                "face_count": 0,
                "face_boxes": [],
                "identities": [],
                "state": "unavailable",
            }
        with self._lock:
            analyze_faces = getattr(self.backend, "analyze_faces", None)
            if callable(analyze_faces):
                faces = analyze_faces(image)
            else:
                faces = [
                    {"embedding": embedding, "bbox": None}
                    for embedding in self.backend.extract_embeddings(image)
                ]
            identities = []
            face_boxes = []
            used_provisional_clusters = set()
            self._frame_sequence += 1
            frame_token = self._frame_sequence
            monotonic_now = time.monotonic()
            for index, face in enumerate(faces):
                embedding = face["embedding"]
                bbox = tuple(face["bbox"]) if face.get("bbox") is not None else None
                if bbox is not None:
                    face_boxes.append(bbox)
                result = self.matcher.match(
                    f"{stream}:{index}",
                    embedding,
                    self._confirmed_candidates(embedding),
                )
                if result:
                    result["display_name"] = self._names.get(result["profile_id"], "Pessoa cadastrada")
                    result["role"] = self._roles.get(result["profile_id"], "authorized")
                    result["face_index"] = index
                    result["bbox"] = bbox
                    identities.append(result)
                elif bbox is not None and not self._resembles_confirmed_profile(embedding):
                    provisional = self._observe_unknown(
                        stream,
                        image,
                        bbox,
                        embedding,
                        frame_token,
                        monotonic_now,
                        used_provisional_clusters,
                    )
                    if provisional is not None:
                        cluster_id, confidence = provisional
                        used_provisional_clusters.add(cluster_id)
                        identities.append(
                            {
                                "profile_id": f"{PROVISIONAL_PREFIX}{cluster_id}",
                                "display_name": self._provisional_names.get(
                                    cluster_id, "Pessoa em análise"
                                ),
                                "role": "pending",
                                "provisional": True,
                                "confidence": confidence,
                                "face_index": index,
                                "bbox": bbox,
                            }
                        )
        return {
            "face_count": len(faces),
            "face_boxes": face_boxes,
            "identities": identities,
            "state": "active",
        }
