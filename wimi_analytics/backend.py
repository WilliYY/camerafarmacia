import json
import time
from datetime import datetime
from pathlib import Path


SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
DEFAULT_STALE_AFTER_SECONDS = 180
MAX_CLOCK_SKEW_SECONDS = 60

METRIC_FIELDS = (
    "active_streams",
    "thread_count",
    "process_memory_mb",
    "local_free_gb",
    "hd_available",
    "hd_free_gb",
    "pending_backup_count",
    "pending_backup_gb",
    "go2rtc_restart_count",
    "kernel_144_reports_24h",
)
CAMERA_FIELDS = (
    "status",
    "reason",
    "status_since",
    "last_recovered_at",
    "last_data_age_seconds",
    "producer_active",
    "recording_active",
    "viewing_active",
)
ISSUE_FIELDS = ("code", "severity", "summary", "action", "stream")
INTELLIGENCE_FIELDS = (
    "status",
    "headline",
    "explanation",
    "confidence",
    "confidence_score",
    "recording_recommendation",
)
HARDWARE_PROTECTION_FIELDS = (
    "heavy_maintenance_allowed",
    "reason",
    "recording_recommendation",
)


def _safe_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    return None


def _allowlisted_dict(source, fields):
    if not isinstance(source, dict):
        return {}
    result = {}
    for field in fields:
        value = _safe_scalar(source.get(field))
        if value is not None:
            result[field] = value
    return result


def _sanitize_snapshot(snapshot):
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    clean_metrics = _allowlisted_dict(metrics, METRIC_FIELDS)

    active_streams = metrics.get("active_streams")
    if isinstance(active_streams, list):
        clean_metrics["active_streams"] = [
            str(stream)[:100] for stream in active_streams[:64] if isinstance(stream, str)
        ]

    connectivity = metrics.get("camera_connectivity")
    if isinstance(connectivity, dict):
        clean_metrics["camera_connectivity"] = {
            str(stream)[:100]: _allowlisted_dict(state, CAMERA_FIELDS)
            for stream, state in list(connectivity.items())[:64]
            if isinstance(stream, str) and isinstance(state, dict)
        }
    else:
        clean_metrics["camera_connectivity"] = {}

    issues = snapshot.get("issues")
    clean_issues = []
    if isinstance(issues, list):
        clean_issues = [
            _allowlisted_dict(issue, ISSUE_FIELDS)
            for issue in issues[:100]
            if isinstance(issue, dict)
        ]

    clean = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _safe_scalar(snapshot.get("generated_at")),
        "overall_status": _safe_scalar(snapshot.get("overall_status")) or "unknown",
        "issues": clean_issues,
        "metrics": clean_metrics,
    }
    intelligence = _allowlisted_dict(snapshot.get("intelligence"), INTELLIGENCE_FIELDS)
    if intelligence:
        source_intelligence = snapshot.get("intelligence")
        priority_actions = source_intelligence.get("priority_actions")
        if isinstance(priority_actions, list):
            intelligence["priority_actions"] = [
                str(action)[:500]
                for action in priority_actions[:10]
                if isinstance(action, str)
            ]
        protection = _allowlisted_dict(
            source_intelligence.get("hardware_protection"),
            HARDWARE_PROTECTION_FIELDS,
        )
        if protection:
            intelligence["hardware_protection"] = protection
        clean["intelligence"] = intelligence
    return clean


def _parse_local_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


class NvrHealthBridge:
    """Read-only, allowlisted bridge for the NVR health snapshot."""

    def __init__(
        self,
        health_path,
        stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS,
        max_bytes=MAX_SNAPSHOT_BYTES,
        read_retries=3,
    ):
        self.health_path = Path(health_path)
        self.stale_after_seconds = max(1, int(stale_after_seconds))
        self.max_bytes = max(1, int(max_bytes))
        self.read_retries = max(1, min(int(read_retries), 5))

    def _read_bytes(self):
        for attempt in range(self.read_retries):
            try:
                size = self.health_path.stat().st_size
                if size > self.max_bytes:
                    raise OverflowError("snapshot_too_large")
                with self.health_path.open("rb") as snapshot_file:
                    return snapshot_file.read(self.max_bytes + 1)
            except PermissionError:
                if attempt + 1 >= self.read_retries:
                    raise
                time.sleep(0.02 * (attempt + 1))
        return b""

    def read(self):
        if not self.health_path.is_file():
            return {
                "state": "unavailable",
                "reason": "snapshot_missing",
                "age_seconds": None,
                "snapshot": None,
            }

        try:
            raw = self._read_bytes()
            if len(raw) > self.max_bytes:
                raise OverflowError("snapshot_too_large")
            snapshot = json.loads(raw.decode("utf-8"))
        except OverflowError:
            return {
                "state": "unavailable",
                "reason": "snapshot_too_large",
                "age_seconds": None,
                "snapshot": None,
            }
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {
                "state": "unavailable",
                "reason": "snapshot_invalid",
                "age_seconds": None,
                "snapshot": None,
            }

        if not isinstance(snapshot, dict):
            return {
                "state": "unavailable",
                "reason": "snapshot_invalid",
                "age_seconds": None,
                "snapshot": None,
            }
        if snapshot.get("schema_version") != SCHEMA_VERSION:
            return {
                "state": "unavailable",
                "reason": "schema_unsupported",
                "age_seconds": None,
                "snapshot": None,
            }

        generated_at = _parse_local_timestamp(snapshot.get("generated_at"))
        if generated_at is None:
            return {
                "state": "unavailable",
                "reason": "timestamp_invalid",
                "age_seconds": None,
                "snapshot": _sanitize_snapshot(snapshot),
            }

        age_seconds = round((datetime.now() - generated_at).total_seconds(), 1)
        clean_snapshot = _sanitize_snapshot(snapshot)
        if age_seconds < -MAX_CLOCK_SKEW_SECONDS:
            return {
                "state": "unknown",
                "reason": "clock_skew",
                "age_seconds": age_seconds,
                "snapshot": clean_snapshot,
            }
        if age_seconds > self.stale_after_seconds:
            return {
                "state": "stale",
                "reason": "snapshot_stale",
                "age_seconds": age_seconds,
                "snapshot": clean_snapshot,
            }
        return {
            "state": "active",
            "reason": "snapshot_current",
            "age_seconds": max(0.0, age_seconds),
            "snapshot": clean_snapshot,
        }


def build_dashboard_payload(bridge, panel_url="http://127.0.0.1:8765"):
    nvr = bridge.read()
    modules = [
        {
            "id": "nvr",
            "label": "Cameras e gravacao",
            "status": nvr["state"],
            "detail": nvr["reason"],
        },
        {
            "id": "analytics",
            "label": "Fundacao Analytics",
            "status": "active",
            "detail": "API local e painel unificado ativos",
        },
        {
            "id": "vision",
            "label": "Visao computacional",
            "status": "not_configured",
            "detail": "Worker nao iniciado",
        },
        {
            "id": "computers",
            "label": "Computadores",
            "status": "not_configured",
            "detail": "Agente Windows nao instalado",
        },
        {
            "id": "network",
            "label": "Rede",
            "status": "not_configured",
            "detail": "Conector somente leitura nao configurado",
        },
        {
            "id": "reports",
            "label": "Relatorios",
            "status": "waiting_for_data",
            "detail": "Aguardando fontes operacionais",
        },
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "service": {
            "id": "wimi-analytics",
            "name": "WIMI Analytics",
            "version": "0.1.0",
            "status": "active",
            "mode": "local_read_only",
        },
        "nvr": nvr,
        "modules": modules,
        "links": {
            "panel": panel_url,
            "cameras": "http://127.0.0.1:1984/visualizador.html",
        },
    }
