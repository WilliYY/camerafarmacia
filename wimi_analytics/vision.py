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
        motion_duration = 0.0
        if state["motion"] and state["started_at"] is not None:
            motion_duration = max(0.0, (occurred_at - state["started_at"]).total_seconds())
        return {
            "motion": "active" if state["motion"] else "idle",
            "motion_duration_seconds": round(motion_duration, 1),
            "changed_ratio": round(changed_ratio, 4),
            "motion_threshold": round(effective_threshold, 4),
            "calibrated": calibrated,
            "adaptation_state": (
                "disabled" if not self.adaptive else "adaptive" if calibrated else "calibrating"
            ),
            "event": event,
        }


class BehaviorAnalyzer:
    def __init__(
        self,
        end_observations=2,
        start_observations=2,
        count_observations=2,
        observation_timeout_seconds=15.0,
    ):
        self.end_observations = max(1, min(int(end_observations), 12))
        self.start_observations = max(1, min(int(start_observations), 12))
        self.count_observations = max(1, min(int(count_observations), 12))
        self.observation_timeout_seconds = max(
            1.0, min(float(observation_timeout_seconds), 300.0)
        )
        self._states = {}

    @staticmethod
    def _activity_level(motion):
        if motion.get("motion") != "active":
            return "quiet"
        threshold = max(0.0001, float(motion.get("motion_threshold") or 0.0001))
        relative_change = max(0.0, float(motion.get("changed_ratio") or 0.0)) / threshold
        if relative_change >= 2.5:
            return "high"
        if relative_change >= 1.25:
            return "moderate"
        return "low"

    def analyze(self, stream, motion, person_count=None, occurred_at=None):
        occurred_at = occurred_at or datetime.now()
        state = self._states.setdefault(
            stream,
            {
                "last_count": None,
                "reported_count": None,
                "pending_count": None,
                "pending_count_observations": 0,
                "presence": False,
                "started_at": None,
                "candidate_started_at": None,
                "positive_observations": 0,
                "absent_observations": 0,
                "peak_count": 0,
                "candidate_peak_count": 0,
                "last_observation_at": None,
                "last_present_at": None,
            },
        )
        events = []
        previous_observation_at = state["last_observation_at"]
        if (
            previous_observation_at is not None
            and max(0.0, (occurred_at - previous_observation_at).total_seconds())
            > self.observation_timeout_seconds
        ):
            state["positive_observations"] = 0
            state["candidate_started_at"] = None
            state["candidate_peak_count"] = 0
            state["pending_count"] = None
            state["pending_count_observations"] = 0
        if person_count is not None:
            count = max(0, int(person_count))
            state["last_count"] = count
            state["last_observation_at"] = occurred_at
            if state["pending_count"] == count:
                state["pending_count_observations"] += 1
            else:
                state["pending_count"] = count
                state["pending_count_observations"] = 1
            if (
                state["pending_count_observations"] >= self.count_observations
                and state["reported_count"] != count
            ):
                events.append(
                    {
                        "event_type": "person_count",
                        "stream": stream,
                        "occurred_at": occurred_at,
                        "count": count,
                    }
                )
                state["reported_count"] = count
            if count > 0:
                state["absent_observations"] = 0
                state["last_present_at"] = occurred_at
                if state["presence"]:
                    state["peak_count"] = max(state["peak_count"], count)
                else:
                    if state["positive_observations"] == 0:
                        state["candidate_started_at"] = occurred_at
                        state["candidate_peak_count"] = count
                    state["positive_observations"] += 1
                    state["candidate_peak_count"] = max(
                        state["candidate_peak_count"], count
                    )
                if (
                    not state["presence"]
                    and state["positive_observations"] >= self.start_observations
                ):
                    state["presence"] = True
                    state["started_at"] = state["candidate_started_at"] or occurred_at
                    state["peak_count"] = state["candidate_peak_count"]
                    state["positive_observations"] = 0
                    state["candidate_started_at"] = None
                    state["candidate_peak_count"] = 0
                    events.append(
                        {
                            "event_type": "observed_presence_start",
                            "stream": stream,
                            "occurred_at": occurred_at,
                            "count": count,
                        }
                    )
            else:
                state["positive_observations"] = 0
                state["candidate_started_at"] = None
                state["candidate_peak_count"] = 0
                if state["presence"]:
                    state["absent_observations"] += 1
                if (
                    state["presence"]
                    and state["absent_observations"] >= self.end_observations
                ):
                    started_at = state["started_at"] or occurred_at
                    last_present_at = state["last_present_at"] or started_at
                    events.append(
                        {
                            "event_type": "observed_presence_end",
                            "stream": stream,
                            "occurred_at": occurred_at,
                            "count": state["peak_count"],
                            "duration_seconds": max(
                                0.0, (last_present_at - started_at).total_seconds()
                            ),
                        }
                    )
                    state["presence"] = False
                    state["started_at"] = None
                    state["absent_observations"] = 0
                    state["peak_count"] = 0
                    state["last_present_at"] = None

        presence_duration = 0.0
        if state["presence"] and state["started_at"] is not None:
            duration_end = state["last_present_at"] or state["started_at"]
            presence_duration = max(
                0.0, (duration_end - state["started_at"]).total_seconds()
            )
        last_observation_at = state["last_observation_at"]
        observation_fresh = False
        if last_observation_at is not None:
            observation_fresh = (
                max(0.0, (occurred_at - last_observation_at).total_seconds())
                <= self.observation_timeout_seconds
            )
        observed_presence = (
            "active"
            if state["presence"] and observation_fresh
            else "unknown"
            if (
                state["last_count"] is None
                or not observation_fresh
                or state["positive_observations"] > 0
            )
            else "idle"
        )
        return {
            "person_count": state["last_count"] if observation_fresh else None,
            "observed_presence": observed_presence,
            "presence_duration_seconds": round(presence_duration, 1),
            "peak_person_count": state["peak_count"] if state["presence"] else 0,
            "activity_level": self._activity_level(motion),
            "motion_duration_seconds": float(motion.get("motion_duration_seconds") or 0.0),
            "events": events,
        }

    def close_all(self, occurred_at=None):
        occurred_at = occurred_at or datetime.now()
        events = []
        for stream, state in self._states.items():
            if not state["presence"]:
                continue
            started_at = state["started_at"] or occurred_at
            last_present_at = state["last_present_at"] or started_at
            events.append(
                {
                    "event_type": "observed_presence_end",
                    "stream": stream,
                    "occurred_at": occurred_at,
                    "count": state["peak_count"],
                    "duration_seconds": max(
                        0.0, (last_present_at - started_at).total_seconds()
                    ),
                }
            )
            state["presence"] = False
            state["started_at"] = None
            state["absent_observations"] = 0
            state["peak_count"] = 0
            state["last_present_at"] = None
        return events


class VisionCoordinator:
    def __init__(
        self,
        store,
        face_service=None,
        evidence_archive=None,
        motion_analyzer=None,
        hardware_guard=None,
        sample_interval_seconds=1.0,
        face_interval_seconds=1.0,
        queue_size=2,
        max_frame_size=(1280, 720),
        person_detector=None,
        behavior_analyzer=None,
        person_interval_seconds=5.0,
    ):
        self.store = store
        self.face_service = face_service
        self.evidence_archive = evidence_archive
        self.person_detector = person_detector
        self.motion_analyzer = motion_analyzer or MotionAnalyzer(adaptive=True)
        self.hardware_guard = hardware_guard or (lambda: None)
        self.sample_interval_seconds = max(0.0, float(sample_interval_seconds))
        self.face_interval_seconds = max(0.0, float(face_interval_seconds))
        self.person_interval_seconds = max(0.0, float(person_interval_seconds))
        self.behavior_analyzer = behavior_analyzer or BehaviorAnalyzer(
            observation_timeout_seconds=max(10.0, self.person_interval_seconds * 3.0)
        )
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
        self._last_face_state = {}
        self._last_identities = {}
        self._identity_overlays = {}
        self._last_person_analysis = {}
        self._last_person_state = {}
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
        events_flushed = True
        close_all = getattr(self.behavior_analyzer, "close_all", None)
        if callable(close_all):
            for event in close_all(datetime.now()):
                self._persist_event(event)
            events_flushed = self._flush_pending_events(limit=32)
        self._thread = None
        with self._lock:
            self._latest_frames.clear()
            self._identity_overlays.clear()
            self._last_identities.clear()
            self._last_face_state.clear()
        return events_flushed

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
        self._clear_identity_state(stream)
        result = {
            "stream": stream,
            "state": "degraded",
            "pause_reason": "transient_processing_error",
            "last_analyzed_at": occurred_at.isoformat(timespec="seconds"),
            "motion": "unknown",
            "face_count": None,
            "person_count": None,
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

    def _clear_identity_state(self, stream):
        with self._lock:
            self._identity_overlays.pop(stream, None)
            self._last_identities[stream] = []

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
            self._clear_identity_state(stream)
            result = {
                "stream": stream,
                "state": "paused",
                "pause_reason": str(pause_reason)[:80],
                "last_analyzed_at": occurred_at.isoformat(timespec="seconds"),
                "motion": "unknown",
                "face_count": None,
                "person_count": None,
                "person_state": "paused",
                "observed_presence": "unknown",
                "presence_duration_seconds": 0.0,
                "activity_level": "unknown",
                "identities": [],
            }
            self._save_snapshot(stream, result)
            return result

        self._flush_pending_events()
        motion = self.motion_analyzer.analyze(stream, image, occurred_at)
        if motion["event"]:
            self._persist_event(motion["event"])

        person_state = self._last_person_state.get(stream, "not_configured")
        person_observation = None
        person_detector = self.person_detector
        last_person_at = self._last_person_analysis.get(stream, -1e9)
        if person_detector is not None:
            person_state = self._last_person_state.get(
                stream, getattr(person_detector, "status", "unavailable")
            )
        if (
            person_detector is not None
            and getattr(person_detector, "available", False)
            and monotonic_now - last_person_at >= self.person_interval_seconds
        ):
            self._last_person_analysis[stream] = monotonic_now
            try:
                person_observation = len(person_detector.detect(image))
                person_state = "active"
            except Exception as error:
                person_state = f"processing_error:{type(error).__name__}"[:80]
            self._last_person_state[stream] = person_state
        elif person_detector is not None and not getattr(person_detector, "available", False):
            self._last_person_state[stream] = person_state
        behavior = self.behavior_analyzer.analyze(
            stream,
            motion,
            person_count=person_observation,
            occurred_at=occurred_at,
        )
        for event in behavior.pop("events"):
            self._persist_event(event)

        face_count = self._last_face_count.get(stream)
        identities = list(self._last_identities.get(stream, []))
        face_boxes = []
        face_state = self._last_face_state.get(stream, "not_configured")
        face_service = self.face_service
        last_face_at = self._last_face_analysis.get(stream, -1e9)
        if face_service is not None:
            face_state = getattr(face_service, "status", "unavailable")
        if face_service is None or not getattr(face_service, "available", False):
            identities = []
            face_count = 0
            self._clear_identity_state(stream)
        if (
            face_service is not None
            and getattr(face_service, "available", False)
            and monotonic_now - last_face_at >= self.face_interval_seconds
        ):
            self._last_face_analysis[stream] = monotonic_now
            analyzed = face_service.analyze_frame(stream, image)
            face_count = max(0, int(analyzed.get("face_count") or 0))
            raw_identities = list(analyzed.get("identities") or [])
            face_boxes = list(analyzed.get("face_boxes") or [])
            face_state = analyzed.get("state", "active")
            identity_by_index = {}
            identity_by_bbox = {}
            for identity_index, identity in enumerate(raw_identities):
                face_index = identity.get("face_index", identity_index)
                try:
                    face_index = int(face_index)
                except (TypeError, ValueError):
                    face_index = identity_index
                identity_by_index[face_index] = identity
                identity_bbox = identity.get("bbox")
                if isinstance(identity_bbox, (list, tuple)) and len(identity_bbox) == 4:
                    identity_by_bbox[tuple(identity_bbox)] = identity
            overlay_faces = []
            for face_index, bbox in enumerate(face_boxes):
                identity = identity_by_bbox.get(tuple(bbox)) or identity_by_index.get(
                    face_index
                )
                overlay_faces.append(
                    {
                        "bbox": tuple(bbox),
                        "recognized": identity is not None,
                        "display_name": (
                            identity.get("display_name", "Pessoa cadastrada")
                            if identity is not None
                            else "Desconhecido"
                        ),
                        "role": identity.get("role") if identity is not None else None,
                        "confidence": (
                            identity.get("confidence") if identity is not None else None
                        ),
                    }
                )
            identities = [
                {
                    key: value
                    for key, value in identity.items()
                    if key not in {"bbox", "face_index"}
                }
                for identity in raw_identities
            ]
            with self._lock:
                self._identity_overlays[stream] = {
                    "source_size": tuple(image.size),
                    "faces": overlay_faces,
                    "updated_at": monotonic_now,
                }
                self._last_identities[stream] = list(identities)
                self._last_face_state[stream] = face_state
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
            archive = self.evidence_archive
            if archive is not None and face_count > 0 and len(face_boxes) >= face_count:
                try:
                    archive.capture(
                        stream,
                        image,
                        face_boxes=face_boxes,
                        face_count=face_count,
                        captured_at=occurred_at,
                    )
                except Exception:
                    pass
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
            "motion_duration_seconds": motion["motion_duration_seconds"],
            "changed_ratio": motion["changed_ratio"],
            "motion_threshold": motion["motion_threshold"],
            "adaptation_state": motion["adaptation_state"],
            "face_state": face_state,
            "face_count": face_count,
            "person_state": person_state,
            "identities": identities,
            **behavior,
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

    def get_identity_overlay(
        self, stream, max_age_seconds=2.5, monotonic_now=None
    ):
        monotonic_now = time.monotonic() if monotonic_now is None else float(monotonic_now)
        with self._lock:
            overlay = self._identity_overlays.get(stream)
            if overlay is None:
                return None
            if monotonic_now - overlay["updated_at"] > max(0.0, float(max_age_seconds)):
                return None
            return {
                "source_size": tuple(overlay["source_size"]),
                "faces": [dict(face) for face in overlay["faces"]],
            }
