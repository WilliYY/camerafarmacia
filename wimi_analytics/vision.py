import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime

from PIL import Image, ImageChops


class MotionAnalyzer:
    def __init__(
        self,
        width=64,
        height=36,
        pixel_threshold=25,
        changed_ratio_threshold=0.04,
        start_frames=2,
        end_frames=3,
        adaptive=False,
        calibration_samples=8,
        adaptation_window=60,
        adaptive_margin=0.015,
        adaptive_max_threshold=0.18,
    ):
        self.size = (max(16, int(width)), max(9, int(height)))
        self.pixel_threshold = max(0, min(int(pixel_threshold), 254))
        self.changed_ratio_threshold = max(0.001, min(float(changed_ratio_threshold), 1.0))
        self.start_frames = max(1, int(start_frames))
        self.end_frames = max(1, int(end_frames))
        self.adaptive = bool(adaptive)
        self.calibration_samples = max(3, min(int(calibration_samples), 60))
        self.adaptation_window = max(
            self.calibration_samples,
            min(int(adaptation_window), 300),
        )
        self.adaptive_margin = max(0.005, min(float(adaptive_margin), 0.10))
        self.adaptive_max_threshold = max(
            self.changed_ratio_threshold,
            min(float(adaptive_max_threshold), 0.50),
        )
        self._states = {}

    def _motion_threshold(self, state):
        samples = list(state["noise_samples"])
        if not samples:
            return self.changed_ratio_threshold
        ordered = sorted(samples)
        baseline = ordered[len(ordered) // 2]
        deviations = sorted(abs(value - baseline) for value in samples)
        deviation = deviations[len(deviations) // 2]
        learned = baseline + max(self.adaptive_margin, deviation * 4.0)
        return min(
            self.adaptive_max_threshold,
            max(self.changed_ratio_threshold, learned),
        )

    def analyze(self, stream, image, occurred_at=None):
        occurred_at = occurred_at or datetime.now()
        current = image.convert("L").resize(self.size)
        state = self._states.setdefault(
            stream,
            {
                "previous": None,
                "motion": False,
                "positive": 0,
                "negative": 0,
                "started_at": None,
                "noise_samples": deque(maxlen=self.adaptation_window),
                "calibration_observations": 0,
            },
        )
        event = None
        changed_ratio = 0.0
        calibrated = not self.adaptive
        effective_threshold = self.changed_ratio_threshold
        if state["previous"] is not None:
            histogram = ImageChops.difference(state["previous"], current).histogram()
            total = sum(histogram) or 1
            changed_ratio = sum(histogram[self.pixel_threshold + 1 :]) / total
            if self.adaptive:
                state["calibration_observations"] += 1
                calibrated_before = (
                    len(state["noise_samples"]) >= self.calibration_samples
                    or state["calibration_observations"] >= self.calibration_samples * 3
                )
                if not calibrated_before:
                    if changed_ratio <= self.adaptive_max_threshold:
                        state["noise_samples"].append(changed_ratio)
                    changed = False
                else:
                    effective_threshold = self._motion_threshold(state)
                    changed = changed_ratio >= effective_threshold
                    if not state["motion"] and not changed:
                        state["noise_samples"].append(changed_ratio)
                calibrated = (
                    len(state["noise_samples"]) >= self.calibration_samples
                    or state["calibration_observations"] >= self.calibration_samples * 3
                )
                effective_threshold = self._motion_threshold(state)
            else:
                changed = changed_ratio >= effective_threshold
            if changed:
                state["positive"] += 1
                state["negative"] = 0
            else:
                state["negative"] += 1
                state["positive"] = 0

            if not state["motion"] and state["positive"] >= self.start_frames:
                state["motion"] = True
                state["started_at"] = occurred_at
                state["positive"] = 0
                event = {
                    "event_type": "motion_start",
                    "stream": stream,
                    "occurred_at": occurred_at,
                }
            elif state["motion"] and state["negative"] >= self.end_frames:
                started_at = state["started_at"] or occurred_at
                state["motion"] = False
                state["started_at"] = None
                state["negative"] = 0
                event = {
                    "event_type": "motion_end",
                    "stream": stream,
                    "occurred_at": occurred_at,
                    "duration_seconds": max(0.0, (occurred_at - started_at).total_seconds()),
                }
        state["previous"] = current
        return {
            "motion": "active" if state["motion"] else "idle",
            "changed_ratio": round(changed_ratio, 4),
            "motion_threshold": round(effective_threshold, 4),
            "calibrated": calibrated,
            "adaptation_state": (
                "disabled" if not self.adaptive else "adaptive" if calibrated else "calibrating"
            ),
            "event": event,
        }


class VisionCoordinator:
    def __init__(
        self,
        store,
        face_service=None,
        motion_analyzer=None,
        hardware_guard=None,
        sample_interval_seconds=1.0,
        face_interval_seconds=3.0,
        queue_size=2,
        max_frame_size=(1280, 720),
    ):
        self.store = store
        self.face_service = face_service
        self.motion_analyzer = motion_analyzer or MotionAnalyzer(adaptive=True)
        self.hardware_guard = hardware_guard or (lambda: None)
        self.sample_interval_seconds = max(0.0, float(sample_interval_seconds))
        self.face_interval_seconds = max(0.0, float(face_interval_seconds))
        self.max_frame_size = (
            max(320, int(max_frame_size[0])),
            max(180, int(max_frame_size[1])),
        )
        self._queue = queue.Queue(maxsize=max(1, min(int(queue_size), 16)))
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._last_submitted = {}
        self._last_face_analysis = {}
        self._last_face_count = {}
        self._last_presence = {}
        self._snapshots = {}
        self._latest_frames = {}
        self._last_worker_error_at = -1e9
        self._pending_events = deque(maxlen=64)
        self._dropped_event_count = 0

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    @property
    def pending_count(self):
        return self._queue.qsize()

    @property
    def pending_event_count(self):
        with self._lock:
            return len(self._pending_events)

    def start(self):
        if self.running:
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name="wimi-vision",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, timeout=5.0):
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(max(0.1, float(timeout)))
        if thread and thread.is_alive():
            return False
        self._thread = None
        with self._lock:
            self._latest_frames.clear()
        return True

    def submit(self, stream, image):
        if self._stop_event.is_set():
            return False
        now = time.monotonic()
        with self._lock:
            previous = self._last_submitted.get(stream, 0.0)
            if now - previous < self.sample_interval_seconds:
                return False
            self._last_submitted[stream] = now
        width, height = image.size
        scale = min(self.max_frame_size[0] / width, self.max_frame_size[1] / height, 1.0)
        if scale < 1.0:
            sample = image.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.BILINEAR,
            )
        else:
            sample = image.copy()
        item = (str(stream), sample, datetime.now(), now)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                return False
        return True

    def _worker(self):
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if item is None:
                    continue
                try:
                    self.process_frame_now(*item[:3], monotonic_now=item[3])
                except Exception as error:
                    self._record_worker_error(item[0], item[2], error)
                    time.sleep(0.1)
            finally:
                self._queue.task_done()

    def _record_worker_error(self, stream, occurred_at, error):
        result = {
            "stream": stream,
            "state": "degraded",
            "pause_reason": "transient_processing_error",
            "last_analyzed_at": occurred_at.isoformat(timespec="seconds"),
            "motion": "unknown",
            "face_count": None,
            "identities": [],
            "error": str(error)[:120],
        }
        self._save_snapshot(stream, result)
        now = time.monotonic()
        if now - self._last_worker_error_at < 60.0:
            return
        self._last_worker_error_at = now
        self._persist_event(
            {
                "event_type": "analysis_error",
                "stream": stream,
                "occurred_at": occurred_at,
            }
        )

    def _persist_event(self, event):
        event = dict(event)
        event.setdefault("event_id", str(uuid.uuid4()))
        try:
            self.store.record_vision_event(event)
            return True
        except Exception:
            with self._lock:
                event_id = event["event_id"]
                if any(item.get("event_id") == event_id for item in self._pending_events):
                    return False
                if len(self._pending_events) == self._pending_events.maxlen:
                    self._pending_events.popleft()
                    self._dropped_event_count += 1
                self._pending_events.append(event)
            return False

    def _flush_pending_events(self, limit=8):
        for _ in range(max(1, min(int(limit), 32))):
            with self._lock:
                if not self._pending_events:
                    return True
                event = self._pending_events.popleft()
            try:
                self.store.record_vision_event(event)
            except Exception:
                with self._lock:
                    self._pending_events.appendleft(event)
                return False
        return self.pending_event_count == 0

    def process_frame_now(self, stream, image, occurred_at=None, monotonic_now=None):
        occurred_at = occurred_at or datetime.now()
        monotonic_now = time.monotonic() if monotonic_now is None else monotonic_now
        try:
            pause_reason = self.hardware_guard()
        except Exception:
            pause_reason = "hardware_guard_error"
        if pause_reason:
            result = {
                "stream": stream,
                "state": "paused",
                "pause_reason": str(pause_reason)[:80],
                "last_analyzed_at": occurred_at.isoformat(timespec="seconds"),
                "motion": "unknown",
                "face_count": None,
                "identities": [],
            }
            self._save_snapshot(stream, result)
            return result

        self._flush_pending_events()
        motion = self.motion_analyzer.analyze(stream, image, occurred_at)
        if motion["event"]:
            self._persist_event(motion["event"])

        face_count = self._last_face_count.get(stream)
        identities = []
        face_state = "not_configured"
        face_service = self.face_service
        last_face_at = self._last_face_analysis.get(stream, -1e9)
        if face_service is not None:
            face_state = getattr(face_service, "status", "unavailable")
        if (
            face_service is not None
            and getattr(face_service, "available", False)
            and monotonic_now - last_face_at >= self.face_interval_seconds
        ):
            self._last_face_analysis[stream] = monotonic_now
            analyzed = face_service.analyze_frame(stream, image)
            face_count = max(0, int(analyzed.get("face_count") or 0))
            identities = list(analyzed.get("identities") or [])
            face_state = analyzed.get("state", "active")
            if self._last_face_count.get(stream) != face_count:
                self._persist_event(
                    {
                        "event_type": "face_count",
                        "stream": stream,
                        "occurred_at": occurred_at,
                        "count": face_count,
                    }
                )
                self._last_face_count[stream] = face_count
            for identity in identities:
                profile_id = str(identity.get("profile_id", ""))
                key = (stream, profile_id)
                if not profile_id or monotonic_now - self._last_presence.get(key, -1e9) < 60.0:
                    continue
                self._last_presence[key] = monotonic_now
                self._persist_event(
                    {
                        "event_type": "presence_confirmed",
                        "stream": stream,
                        "occurred_at": occurred_at,
                        "profile_id": profile_id,
                        "confidence": identity.get("confidence"),
                    }
                )

        result = {
            "stream": stream,
            "state": "active" if motion["calibrated"] else "calibrating",
            "pause_reason": None,
            "last_analyzed_at": occurred_at.isoformat(timespec="seconds"),
            "motion": motion["motion"],
            "changed_ratio": motion["changed_ratio"],
            "motion_threshold": motion["motion_threshold"],
            "adaptation_state": motion["adaptation_state"],
            "face_state": face_state,
            "face_count": face_count,
            "identities": identities,
        }
        with self._lock:
            self._latest_frames[stream] = (image.copy(), monotonic_now)
        self._save_snapshot(stream, result)
        return result

    def _save_snapshot(self, stream, result):
        with self._lock:
            self._snapshots[stream] = dict(result)

    def snapshot(self):
        with self._lock:
            return {key: dict(value) for key, value in self._snapshots.items()}

    def get_latest_frame(self, stream, max_age_seconds=5.0, monotonic_now=None):
        monotonic_now = time.monotonic() if monotonic_now is None else float(monotonic_now)
        with self._lock:
            latest = self._latest_frames.get(stream)
            if latest is None:
                return None
            image, captured_at = latest
            if monotonic_now - captured_at > max(0.0, float(max_age_seconds)):
                return None
            return image.copy()
