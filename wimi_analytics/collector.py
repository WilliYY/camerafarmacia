import copy
import threading
from datetime import datetime, timedelta


from .backend import build_dashboard_payload


class AnalyticsCollector:
    def __init__(
        self,
        bridge,
        network_diagnostics,
        store,
        interval_seconds=60,
        runtime_status_provider=None,
        daily_maintenance=None,
        maintenance_retry_seconds=900,
    ):
        self.bridge = bridge
        self.network_diagnostics = network_diagnostics
        self.store = store
        self.interval_seconds = max(15, int(interval_seconds))
        self.runtime_status_provider = runtime_status_provider
        self.daily_maintenance = daily_maintenance
        self.maintenance_retry_seconds = max(0, int(maintenance_retry_seconds))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._payload = None
        self._last_error = None
        self._last_cleanup_date = None
        self._maintenance_retry_at = None
        self._maintenance_error = None

    def collect_once(self):
        network = self.network_diagnostics.read()
        runtime = None
        if self.runtime_status_provider is not None:
            try:
                runtime = self.runtime_status_provider()
            except Exception:
                runtime = {
                    "vision": {
                        "status": "warning",
                        "detail": "Estado do worker indisponivel",
                    }
                }
        payload = build_dashboard_payload(
            self.bridge,
            panel_url="",
            network=network,
            runtime=runtime,
        )
        operations = payload.get("operations") or {}
        report = operations.get("report") or {}
        readiness = operations.get("readiness") or {}
        collected_at = datetime.now()
        self.store.record_report(report, readiness, collected_at=collected_at)
        self.store.record_network(network, collected_at=collected_at)
        maintenance_due = self._last_cleanup_date != collected_at.date() and (
            self._maintenance_retry_at is None
            or collected_at >= self._maintenance_retry_at
        )
        if maintenance_due:
            try:
                self.store.cleanup(retention_days=90, now=collected_at)
                if self.daily_maintenance is not None:
                    maintenance_result = self.daily_maintenance(now=collected_at)
                    if (
                        isinstance(maintenance_result, dict)
                        and int(maintenance_result.get("failed") or 0) > 0
                    ):
                        raise OSError("daily_maintenance_incomplete")
            except Exception as error:
                self._maintenance_error = f"maintenance: {str(error)[:240]}"
                self._maintenance_retry_at = collected_at + timedelta(
                    seconds=self.maintenance_retry_seconds
                )
            else:
                self._last_cleanup_date = collected_at.date()
                self._maintenance_retry_at = None
                self._maintenance_error = None
        with self._lock:
            self._payload = payload
            self._last_error = self._maintenance_error
        return copy.deepcopy(payload)

    def snapshot(self):
        with self._lock:
            return {
                "payload": copy.deepcopy(self._payload),
                "last_error": self._last_error,
                "running": bool(self._thread and self._thread.is_alive()),
            }

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="wimi-analytics-collector",
                daemon=True,
            )
            self._thread.start()
        return True

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.collect_once()
            except Exception as error:
                with self._lock:
                    self._last_error = str(error)[:300]
            if self._stop_event.wait(self.interval_seconds):
                break

    def stop(self, timeout=5.0):
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.1, float(timeout)))
        return not bool(thread and thread.is_alive())
