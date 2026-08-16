import hashlib
import json
import math
import sys
import threading
from pathlib import Path

from .privacy import DpapiProtector


class FaceEngineUnavailable(RuntimeError):
    pass


class EnrollmentError(ValueError):
    pass


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
    def __init__(self, model_dir=None, runtime_dir=None, max_input_size=(960, 540), max_faces=8):
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
                str(models["yunet"]), "", (320, 320), 0.90, 0.30, max(32, self.max_faces * 4)
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
        if not self.available:
            raise FaceEngineUnavailable(self.status)
        width, height = image.size
        scale = min(self.max_input_size[0] / width, self.max_input_size[1] / height, 1.0)
        if scale < 1.0:
            image = image.resize(
                (max(1, round(width * scale)), max(1, round(height * scale)))
            )
        rgb = image.convert("RGB")
        frame = self._np.asarray(rgb)
        frame = self._cv2.cvtColor(frame, self._cv2.COLOR_RGB2BGR)
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(frame)
        if faces is None:
            return []
        embeddings = []
        for face in faces[: self.max_faces]:
            aligned = self._recognizer.alignCrop(frame, face)
            feature = self._recognizer.feature(aligned)
            embeddings.append([float(value) for value in feature.flatten()])
        return embeddings


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
        if self.available:
            self.refresh_profiles()

    def _encode(self, display_name, embedding):
        value = {
            "schema_version": 1,
            "model_id": self.backend.model_id,
            "display_name": display_name,
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
            "embedding": [float(item) for item in embedding],
        }

    def refresh_profiles(self):
        profiles = {}
        names = {}
        for item in self.store.list_profiles(include_payload=True):
            try:
                decoded = self._decode(item["protected_profile"])
                profiles[item["profile_id"]] = decoded["embedding"]
                names[item["profile_id"]] = decoded["display_name"]
            except Exception:
                continue
        with self._lock:
            self._profiles = profiles
            self._names = names

    def enroll(self, display_name, image, consent=False):
        if not consent:
            raise EnrollmentError("explicit_consent_required")
        if not self.available:
            raise FaceEngineUnavailable(self.status)
        with self._lock:
            embeddings = self.backend.extract_embeddings(image)
        if len(embeddings) != 1:
            raise EnrollmentError("exactly_one_face_required")
        display_name = " ".join(str(display_name).split())[:80]
        if not display_name:
            raise EnrollmentError("display_name_required")
        protected = self.protector.protect(self._encode(display_name, embeddings[0]))
        profile_id = self.store.create_profile(protected)
        self.refresh_profiles()
        return profile_id

    def list_profiles(self):
        with self._lock:
            return [
                {"profile_id": profile_id, "display_name": self._names[profile_id]}
                for profile_id in sorted(self._names, key=lambda value: self._names[value].casefold())
            ]

    def delete_profile(self, profile_id):
        deleted = self.store.delete_profile(profile_id)
        if deleted:
            self.refresh_profiles()
        return deleted

    def analyze_frame(self, stream, image):
        if not self.available:
            return {"face_count": 0, "identities": [], "state": "unavailable"}
        with self._lock:
            embeddings = self.backend.extract_embeddings(image)
            identities = []
            for index, embedding in enumerate(embeddings):
                result = self.matcher.match(f"{stream}:{index}", embedding, self._profiles)
                if result:
                    result["display_name"] = self._names.get(result["profile_id"], "Pessoa cadastrada")
                    identities.append(result)
        return {"face_count": len(embeddings), "identities": identities, "state": "active"}
