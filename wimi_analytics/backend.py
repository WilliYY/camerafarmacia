import json
import math
import time
from datetime import datetime
from pathlib import Path

from .operations import build_operational_report, build_readiness


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
NONNEGATIVE_METRIC_FIELDS = {
    "thread_count",
    "process_memory_mb",
    "local_free_gb",
    "hd_free_gb",
    "pending_backup_count",
    "pending_backup_gb",
    "go2rtc_restart_count",
    "kernel_144_reports_24h",
}
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
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:500]
    return None


def _safe_nonnegative_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value >= 0 else None


def _allowlisted_dict(source, fields):
    if not isinstance(source, dict):
        return {}
    result = {}
    for field in fields:
        value = _safe_scalar(source.get(field))
        if value is not None:
            result[field] = value
    return result


def _sanitize_hardware_summary(source):
    if not isinstance(source, dict):
        return {}
    smart = source.get("smart") if isinstance(source.get("smart"), dict) else {}
    kernel = (
        source.get("kernel_144") if isinstance(source.get("kernel_144"), dict) else {}
    )
    power = source.get("power") if isinstance(source.get("power"), dict) else {}
    drives = smart.get("drives") if isinstance(smart.get("drives"), list) else []
    drive_statuses = [
        str(drive.get("status", "")).strip().lower()
        for drive in drives[:32]
        if isinstance(drive, dict)
    ]
    drive_warning_count = sum(
        status not in {"", "ok", "healthy"} for status in drive_statuses
    )
    summary = {
        "smart_status": _safe_scalar(smart.get("status")),
        "telemetry_level": _safe_scalar(smart.get("telemetry_level")),
        "checked_at": _safe_scalar(smart.get("checked_at")),
        "drive_count": len(drive_statuses),
        "drive_warning_count": drive_warning_count,
        "kernel_144_count_24h": _safe_nonnegative_number(kernel.get("count_24h")),
        "kernel_144_new_in_session": _safe_nonnegative_number(
            kernel.get("new_in_session")
        ),
        "power_status": _safe_scalar(power.get("status")),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _sanitize_snapshot(snapshot):
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    clean_metrics = {}
    for field in METRIC_FIELDS:
        value = metrics.get(field)
        if field in NONNEGATIVE_METRIC_FIELDS:
            value = _safe_nonnegative_number(value)
        elif field == "hd_available":
            value = value if isinstance(value, bool) else None
        else:
            value = _safe_scalar(value)
        if value is not None:
            clean_metrics[field] = value

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
    hardware_summary = _sanitize_hardware_summary(snapshot.get("hardware"))
    if hardware_summary:
        clean["hardware_summary"] = hardware_summary
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
                "snapshot": None,
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


def build_dashboard_payload(
    bridge,
    panel_url="http://127.0.0.1:8765",
    network=None,
    runtime=None,
):
    nvr = bridge.read()
    if not isinstance(network, dict) or network.get("schema_version") != SCHEMA_VERSION:
        network = {
            "schema_version": SCHEMA_VERSION,
            "state": "not_configured",
            "reason": "network_collector_not_configured",
            "coverage": "none",
            "can_observe_store_traffic": False,
            "collected_at": None,
            "interfaces": [],
            "connectivity": {
                "active_interface_count": 0,
                "default_gateway_configured": False,
                "dns_configured": False,
            },
        }
    network_state = network.get("state")
    if network_state == "active":
        network_module_status = "limited"
        network_detail = "Rede deste PC visivel; DNS e flows da loja nao configurados"
    elif network_state == "unsupported":
        network_module_status = "not_configured"
        network_detail = "Diagnostico local requer Windows"
    elif network_state == "not_configured":
        network_module_status = "not_configured"
        network_detail = "Conector somente leitura nao configurado"
    else:
        network_module_status = "unavailable"
        network_detail = "Diagnostico local da rede indisponivel"
    report = build_operational_report(nvr, network)
    report_state = report.get("state")
    if report_state == "current":
        report_module_status = "active"
        report_detail = "Relatorio operacional atual disponivel"
    elif report_state == "partial":
        report_module_status = "warning"
        report_detail = "Relatorio disponivel com dados possivelmente antigos"
    else:
        report_module_status = "waiting_for_data"
        report_detail = "Aguardando snapshot valido do NVR"
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
            "status": network_module_status,
            "detail": network_detail,
        },
        {
            "id": "reports",
            "label": "Relatorios",
            "status": report_module_status,
            "detail": report_detail,
        },
    ]
    if isinstance(runtime, dict):
        module_by_id = {item["id"]: item for item in modules}
        for module_id in ("analytics", "vision", "computers"):
            source = runtime.get(module_id)
            target = module_by_id.get(module_id)
            if not isinstance(source, dict) or target is None:
                continue
            status = _safe_scalar(source.get("status"))
            detail = _safe_scalar(source.get("detail"))
            if status:
                target["status"] = str(status)[:32]
            if detail:
                target["detail"] = str(detail)[:200]
    readiness = build_readiness(nvr, network, modules, report, runtime=runtime)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "service": {
            "id": "wimi-analytics",
            "name": "WIMI Analytics",
            "version": "0.1.0",
            "status": "active",
            "mode": (
                "native_local"
                if isinstance(runtime, dict)
                and isinstance(runtime.get("analytics"), dict)
                and runtime["analytics"].get("mode") == "native"
                else "local_read_only"
            ),
        },
        "nvr": nvr,
        "network": network,
        "operations": {
            "report": report,
            "readiness": readiness,
        },
        "modules": modules,
        "links": {
            "panel": panel_url,
            "cameras": "http://127.0.0.1:1984/visualizador.html",
        },
    }
