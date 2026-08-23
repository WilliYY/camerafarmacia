import hashlib
import json
import sys
from pathlib import Path


class PersonDetectorUnavailable(RuntimeError):
    pass


class OpenCvPersonDetector:
    """CPU-only person detector based on OpenCV Zoo NanoDet."""

    def __init__(
        self,
        model_dir=None,
        runtime_dir=None,
        confidence_threshold=0.45,
        iou_threshold=0.60,
        max_people=16,
    ):
        package_dir = Path(__file__).resolve().parent
        self.model_dir = Path(model_dir or package_dir / "models")
        runtime = Path(runtime_dir or package_dir / "runtime" / "python")
        if runtime.exists() and str(runtime) not in sys.path:
            sys.path.insert(0, str(runtime))
        self.confidence_threshold = max(0.10, min(float(confidence_threshold), 0.95))
        self.iou_threshold = max(0.10, min(float(iou_threshold), 0.95))
        self.max_people = max(1, min(int(max_people), 64))
        self.available = False
        self.status = "opencv_or_person_model_missing"
        self.model_id = "nanodet-opencv-zoo-int8bq"
        self._cv2 = None
        self._np = None
        self._net = None
        self._input_size = (416, 416)
        self._strides = (8, 16, 32, 64)
        self._reg_max = 7
        self._anchors = []
        self._load()

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _model_path(self):
        manifest_path = Path(__file__).resolve().parent / "model_manifest.json"
        if not manifest_path.is_file():
            raise PersonDetectorUnavailable("model_manifest_missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = (manifest.get("models") or {}).get("nanodet_person")
        if not isinstance(item, dict):
            raise PersonDetectorUnavailable("model_nanodet_person_missing")
        path = self.model_dir / str(item.get("filename", ""))
        expected = str(item.get("sha256", "")).lower()
        if not path.is_file():
            raise PersonDetectorUnavailable("model_nanodet_person_missing")
        if len(expected) != 64 or self._sha256(path) != expected:
            raise PersonDetectorUnavailable("model_nanodet_person_hash_mismatch")
        return path

    def _load(self):
        try:
            model_path = self._model_path()
            import cv2
            import numpy as np

            net = cv2.dnn.readNet(str(model_path))
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            anchors = []
            for stride in self._strides:
                width = self._input_size[0] // stride
                height = self._input_size[1] // stride
                shift_x = np.arange(width) * stride
                shift_y = np.arange(height) * stride
                xv, yv = np.meshgrid(shift_x, shift_y)
                anchors.append(
                    np.column_stack(
                        (
                            xv.flatten() + 0.5 * (stride - 1),
                            yv.flatten() + 0.5 * (stride - 1),
                        )
                    )
                )
        except Exception as error:
            self.status = str(error)[:160]
            return
        self._cv2 = cv2
        self._np = np
        self._net = net
        self._anchors = anchors
        self.available = True
        self.status = "ready"

    def _letterbox(self, image):
        np = self._np
        cv2 = self._cv2
        source = np.asarray(image.convert("RGB"))
        source_height, source_width = source.shape[:2]
        target_width, target_height = self._input_size
        scale = min(target_width / source_width, target_height / source_height)
        resized_width = max(1, round(source_width * scale))
        resized_height = max(1, round(source_height * scale))
        resized = cv2.resize(source, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
        left = (target_width - resized_width) // 2
        top = (target_height - resized_height) // 2
        canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
        canvas[top : top + resized_height, left : left + resized_width] = resized
        return canvas, (scale, left, top, source_width, source_height)

    def _post_process(self, outputs):
        np = self._np
        cv2 = self._cv2
        class_scores = outputs[::2]
        box_predictions = outputs[1::2]
        boxes_by_level = []
        scores_by_level = []
        classes_by_level = []
        project = np.arange(self._reg_max + 1)
        for stride, scores, boxes, anchors in zip(
            self._strides, class_scores, box_predictions, self._anchors
        ):
            scores = scores.squeeze(axis=0) if scores.ndim == 3 else scores
            boxes = boxes.squeeze(axis=0) if boxes.ndim == 3 else boxes
            maximum = scores.max(axis=1)
            keep = maximum.argsort()[::-1][:1000]
            scores = scores[keep]
            boxes = boxes[keep]
            anchors = anchors[keep]
            logits = boxes.reshape(-1, self._reg_max + 1)
            logits -= logits.max(axis=1, keepdims=True)
            probabilities = np.exp(logits)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            distances = np.dot(probabilities, project).reshape(-1, 4) * stride
            decoded = np.column_stack(
                (
                    anchors[:, 0] - distances[:, 0],
                    anchors[:, 1] - distances[:, 1],
                    anchors[:, 0] + distances[:, 2],
                    anchors[:, 1] + distances[:, 3],
                )
            )
            boxes_by_level.append(decoded)
            scores_by_level.append(maximum[keep])
            classes_by_level.append(scores.argmax(axis=1))

        boxes = np.concatenate(boxes_by_level, axis=0)
        confidences = np.concatenate(scores_by_level, axis=0)
        class_ids = np.concatenate(classes_by_level, axis=0)
        keep = (
            (class_ids == 0)
            & (confidences >= self.confidence_threshold)
            & np.isfinite(boxes).all(axis=1)
            & np.isfinite(confidences)
        )
        boxes = boxes[keep]
        confidences = confidences[keep]
        if not len(boxes):
            return []
        xywh = boxes.copy()
        xywh[:, 2:4] -= xywh[:, 0:2]
        indices = cv2.dnn.NMSBoxes(
            xywh.tolist(),
            confidences.tolist(),
            self.confidence_threshold,
            self.iou_threshold,
        )
        if indices is None or len(indices) == 0:
            return []
        return [(boxes[int(index)], float(confidences[int(index)])) for index in np.asarray(indices).flatten()]

    def detect(self, image):
        if not self.available:
            raise PersonDetectorUnavailable(self.status)
        frame, geometry = self._letterbox(image)
        scale, left, top, source_width, source_height = geometry
        frame = frame.astype(self._np.float32)
        mean = self._np.array([103.53, 116.28, 123.675], dtype=self._np.float32)
        std = self._np.array([57.375, 57.12, 58.395], dtype=self._np.float32)
        blob = self._cv2.dnn.blobFromImage((frame - mean) / std)
        self._net.setInput(blob)
        outputs = self._net.forward(self._net.getUnconnectedOutLayersNames())
        detections = []
        for box, confidence in self._post_process(outputs):
            x1 = max(0, min(round((float(box[0]) - left) / scale), source_width - 1))
            y1 = max(0, min(round((float(box[1]) - top) / scale), source_height - 1))
            x2 = max(0, min(round((float(box[2]) - left) / scale), source_width))
            y2 = max(0, min(round((float(box[3]) - top) / scale), source_height))
            width = x2 - x1
            height = y2 - y1
            if width < 6 or height < 12:
                continue
            detections.append({"bbox": (x1, y1, width, height), "confidence": confidence})
        detections.sort(key=lambda item: item["confidence"], reverse=True)
        return detections[: self.max_people]
