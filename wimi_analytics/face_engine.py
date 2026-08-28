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
        self._names = {}
        self._roles = {}
        self._provisional_profiles = {}
        self._provisional_names = {}
        self._provisional_crops = {}
        self._provisional_metadata = {}
        self._pending_unknowns = []
        self._last_provisional_write = {}
        self._frame_sequence = 0
        if self.available:
            self.refresh_profiles()
            self.refresh_provisional_clusters()

    def _encode(self, display_name, embedding, role):
        value = {
            "schema_version": 1,
            "model_id": self.backend.model_id,
            "display_name": display_name,
            "role": normalize_profile_role(role),
            "embedding": [float(item) for item in embedding],
        }
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def _decode(self, protected):
        payload = json.loads(self.protector.unprotect(bytes(protected)).decode("utf-8"))
        if payload.get("schema_version") != 1 or payload.get("model_id") != self.backend.model_id:
            raise ValueError("face_profile_model_mismatch")
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise ValueError("face_profile_invalid")
        display_name = str(payload.get("display_name", "")).strip()
        if not display_name:
            raise ValueError("face_profile_name_missing")
        return {
            "display_name": display_name,
            "role": normalize_profile_role(payload.get("role", "authorized")),
            "embedding": [float(item) for item in embedding],
        }

    def _encode_provisional(self, display_name, embedding, crop_jpeg):
        value = {
            "schema_version": 1,
            "model_id": self.backend.model_id,
            "display_name": display_name,
            "embedding": [float(item) for item in embedding],
            "crop_jpeg": base64.b64encode(bytes(crop_jpeg)).decode("ascii"),
        }
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def _decode_provisional(self, protected):
        payload = json.loads(self.protector.unprotect(bytes(protected)).decode("utf-8"))
        if payload.get("schema_version") != 1 or payload.get("model_id") != self.backend.model_id:
            raise ValueError("provisional_face_model_mismatch")
        embedding = payload.get("embedding")
        display_name = str(payload.get("display_name") or "").strip()
        if not isinstance(embedding, list) or not embedding or not display_name:
            raise ValueError("provisional_face_invalid")
        crop_jpeg = base64.b64decode(payload.get("crop_jpeg") or "", validate=True)
        if not crop_jpeg.startswith(b"\xff\xd8") or len(crop_jpeg) > 64 * 1024:
            raise ValueError("provisional_face_crop_invalid")
        return {
            "display_name": display_name,
            "embedding": [float(item) for item in embedding],
            "crop_jpeg": crop_jpeg,
        }

    def refresh_profiles(self):
        profiles = {}
        names = {}
        roles = {}
        for item in self.store.list_profiles(include_payload=True):
            try:
                decoded = self._decode(item["protected_profile"])
                profiles[item["profile_id"]] = decoded["embedding"]
                names[item["profile_id"]] = decoded["display_name"]
                roles[item["profile_id"]] = decoded["role"]
            except Exception:
                continue
        with self._lock:
            self._profiles = profiles
            self._names = names
            self._roles = roles

    def refresh_provisional_clusters(self):
        profiles = {}
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

    def _match_provisional(self, embedding):
        scored = sorted(
            (
                (_cosine_similarity(embedding, candidate), cluster_id)
                for cluster_id, candidate in self._provisional_profiles.items()
            ),
            reverse=True,
        )
        if not scored or scored[0][0] < 0.55:
            return None
        best_score, cluster_id = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1.0
        if best_score - second_score < 0.06:
            return None
        return cluster_id, max(0.0, min(float(best_score), 1.0))

    def _resembles_confirmed_profile(self, embedding):
        scored = sorted(
            _cosine_similarity(embedding, candidate)
            for candidate in self._profiles.values()
        )
        if not scored or scored[-1] < self.matcher.threshold:
            return False
        second = scored[-2] if len(scored) > 1 else -1.0
        return scored[-1] - second >= self.matcher.minimum_margin

    def _touch_provisional(self, cluster_id, monotonic_now):
        previous = self._last_provisional_write.get(cluster_id, -1e9)
        if monotonic_now - previous < PROVISIONAL_WRITE_INTERVAL_SECONDS:
            return
        metadata = self._provisional_metadata.get(cluster_id) or {}
        crop_jpeg = self._provisional_crops.get(cluster_id)
        embedding = self._provisional_profiles.get(cluster_id)
        display_name = self._provisional_names.get(cluster_id)
        if not crop_jpeg or embedding is None or not display_name:
            return
        try:
            protected = self.protector.protect(
                self._encode_provisional(display_name, embedding, crop_jpeg)
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

    def _observe_unknown(self, image, bbox, embedding, frame_token, monotonic_now):
        match = self._match_provisional(embedding)
        if match is not None:
            cluster_id, confidence = match
            self._touch_provisional(cluster_id, monotonic_now)
            return cluster_id, confidence

        self._pending_unknowns = [
            item
            for item in self._pending_unknowns
            if monotonic_now - item["last_seen"] <= 30.0
        ]
        best = None
        best_score = -1.0
        for item in self._pending_unknowns:
            if item.get("frame_token") == frame_token:
                continue
            score = _cosine_similarity(embedding, item["embedding"])
            if score > best_score:
                best = item
                best_score = score
        if best is None or best_score < 0.62:
            self._pending_unknowns.append(
                {
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
        self._provisional_names[cluster_id] = display_name
        self._provisional_crops[cluster_id] = crop_jpeg
        self._provisional_metadata[cluster_id] = {
            "first_seen_at": datetime.now().isoformat(timespec="seconds"),
            "last_seen_at": datetime.now().isoformat(timespec="seconds"),
            "expires_at": None,
            "observation_count": 1,
        }
        self._last_provisional_write[cluster_id] = monotonic_now
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
                protected = self.protector.protect(
                    self._encode(display_name, embedding, normalize_profile_role(role))
                )
                promote = getattr(self.store, "promote_provisional_cluster", None)
                if not callable(promote):
                    return False
                new_profile_id = promote(cluster_id, protected)
                if not new_profile_id:
                    return False
                self._provisional_profiles.pop(cluster_id, None)
                self._provisional_names.pop(cluster_id, None)
                self._provisional_crops.pop(cluster_id, None)
                self._provisional_metadata.pop(cluster_id, None)
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
            self._frame_sequence += 1
            frame_token = self._frame_sequence
            monotonic_now = time.monotonic()
            for index, face in enumerate(faces):
                embedding = face["embedding"]
                bbox = tuple(face["bbox"]) if face.get("bbox") is not None else None
                if bbox is not None:
                    face_boxes.append(bbox)
                result = self.matcher.match(f"{stream}:{index}", embedding, self._profiles)
                if result:
                    result["display_name"] = self._names.get(result["profile_id"], "Pessoa cadastrada")
                    result["role"] = self._roles.get(result["profile_id"], "authorized")
                    result["face_index"] = index
                    result["bbox"] = bbox
                    identities.append(result)
                elif bbox is not None and not self._resembles_confirmed_profile(embedding):
                    provisional = self._observe_unknown(
                        image,
                        bbox,
                        embedding,
                        frame_token,
                        monotonic_now,
                    )
                    if provisional is not None:
                        cluster_id, confidence = provisional
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
