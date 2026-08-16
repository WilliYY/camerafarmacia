import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median


SCHEMA_VERSION = 3
MAX_PAYLOAD_BYTES = 256 * 1024
JOURNAL_SIZE_LIMIT = 8 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
PRESENCE_SESSION_GAP_SECONDS = 90
PRESENCE_SAMPLE_SECONDS = 3.0
TRAFFIC_SPIKE_MIN_BYTES_PER_SECOND = 5 * 1024 * 1024
TRAFFIC_SPIKE_MULTIPLIER = 4.0


class UnsafeAnalyticsPathError(ValueError):
    pass


def _is_within(path, root):
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except (ValueError, OSError):
        return False


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed.isoformat(timespec="seconds")
    raise ValueError("invalid_datetime")


def _stable_value(value):
    if isinstance(value, dict):
        return {
            key: _stable_value(item)
            for key, item in value.items()
            if key not in {"generated_at", "collected_at"}
        }
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    return value


def _json_payload(value):
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("analytics_payload_too_large")
    return serialized


def _fingerprint(value):
    payload = _json_payload(_stable_value(value)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _profile_hash(profile_id):
    return hashlib.sha256(str(profile_id).encode("utf-8")).hexdigest()


class AnalyticsStore:
    def __init__(self, path, forbidden_roots=None):
        self.path = Path(path).resolve()
        for root in forbidden_roots or []:
            resolved_root = Path(root).resolve()
            if _is_within(self.path, resolved_root):
                raise UnsafeAnalyticsPathError("analytics_database_inside_recording_root")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.last_cleanup_error = None
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT}")
        connection.execute("PRAGMA wal_autocheckpoint=250")
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._lock, self._connection() as connection:
            schema_meta_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_meta'
                """
            ).fetchone() is not None
            previous_version = 0
            if schema_meta_exists:
                version_row = connection.execute(
                    "SELECT MAX(version) AS version FROM schema_meta"
                ).fetchone()
                previous_version = int(version_row["version"] or 0)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );
                INSERT INTO schema_meta(version)
                SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);

                CREATE TABLE IF NOT EXISTS report_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at TEXT NOT NULL,
                    source_generated_at TEXT,
                    state TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reports_collected
                    ON report_snapshots(collected_at DESC);

                CREATE TABLE IF NOT EXISTS network_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    active_interface_count INTEGER NOT NULL,
                    primary_connection_type TEXT NOT NULL DEFAULT 'unknown',
                    wired_interface_count INTEGER NOT NULL DEFAULT 0,
                    wireless_interface_count INTEGER NOT NULL DEFAULT 0,
                    virtual_interface_count INTEGER NOT NULL DEFAULT 0,
                    default_gateway_configured INTEGER NOT NULL,
                    dns_configured INTEGER NOT NULL,
                    coverage TEXT NOT NULL,
                    received_bytes INTEGER NOT NULL DEFAULT 0,
                    sent_bytes INTEGER NOT NULL DEFAULT 0,
                    received_packets INTEGER NOT NULL DEFAULT 0,
                    sent_packets INTEGER NOT NULL DEFAULT 0,
                    received_errors INTEGER NOT NULL DEFAULT 0,
                    sent_errors INTEGER NOT NULL DEFAULT 0,
                    received_discarded INTEGER NOT NULL DEFAULT 0,
                    sent_discarded INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_network_collected
                    ON network_samples(collected_at DESC);

                CREATE TABLE IF NOT EXISTS vision_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    count INTEGER,
                    profile_id TEXT,
                    confidence REAL,
                    duration_seconds REAL
                );
                CREATE INDEX IF NOT EXISTS idx_vision_occurred
                    ON vision_events(occurred_at DESC);

                CREATE TABLE IF NOT EXISTS profile_presence_stats (
                    profile_id TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    visit_count INTEGER NOT NULL,
                    observation_count INTEGER NOT NULL,
                    observed_seconds REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_profile_presence_rank
                    ON profile_presence_stats(observed_seconds DESC, last_seen_at DESC);

                CREATE TABLE IF NOT EXISTS profile_presence_streams (
                    profile_id TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    observation_count INTEGER NOT NULL,
                    PRIMARY KEY(profile_id, stream),
                    FOREIGN KEY(profile_id) REFERENCES profile_presence_stats(profile_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS profile_presence_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    observation_count INTEGER NOT NULL,
                    sample_seconds REAL NOT NULL,
                    FOREIGN KEY(profile_id) REFERENCES profile_presence_stats(profile_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_profile_sessions_window
                    ON profile_presence_sessions(profile_id, started_at, ended_at);

                CREATE TABLE IF NOT EXISTS deleted_profile_hashes (
                    profile_hash TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS maintenance_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                """
            )
            existing_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(network_samples)")
            }
            migrations = {
                "primary_connection_type": "TEXT NOT NULL DEFAULT 'unknown'",
                "wired_interface_count": "INTEGER NOT NULL DEFAULT 0",
                "wireless_interface_count": "INTEGER NOT NULL DEFAULT 0",
                "virtual_interface_count": "INTEGER NOT NULL DEFAULT 0",
                "received_bytes": "INTEGER NOT NULL DEFAULT 0",
                "sent_bytes": "INTEGER NOT NULL DEFAULT 0",
                "received_packets": "INTEGER NOT NULL DEFAULT 0",
                "sent_packets": "INTEGER NOT NULL DEFAULT 0",
                "received_errors": "INTEGER NOT NULL DEFAULT 0",
                "sent_errors": "INTEGER NOT NULL DEFAULT 0",
                "received_discarded": "INTEGER NOT NULL DEFAULT 0",
                "sent_discarded": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, declaration in migrations.items():
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE network_samples ADD COLUMN {column} {declaration}"
                    )
            if previous_version < SCHEMA_VERSION:
                event_cursor = connection.execute(
                    "DELETE FROM vision_events WHERE event_type = 'presence_confirmed'"
                )
                stats_cursor = connection.execute("DELETE FROM profile_presence_stats")
                if event_cursor.rowcount > 0 or stats_cursor.rowcount > 0:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO maintenance_state(key, value)
                        VALUES ('sensitive_compaction_pending', '1')
                        """
                    )
            connection.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))
        self._run_pending_sensitive_compaction()

    def record_report(self, report, readiness, collected_at=None, min_interval_seconds=900):
        collected_at = collected_at or datetime.now()
        collected_iso = _iso(collected_at)
        payload = {"report": report, "readiness": readiness}
        serialized = _json_payload(payload)
        fingerprint = _fingerprint(payload)
        state = str(report.get("state", "unavailable"))[:32]
        source_generated_at = report.get("source_generated_at")
        if source_generated_at is not None:
            source_generated_at = _iso(source_generated_at)

        with self._lock, self._connection() as connection:
            previous = connection.execute(
                "SELECT collected_at, fingerprint FROM report_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if previous and previous["fingerprint"] == fingerprint:
                previous_at = datetime.fromisoformat(previous["collected_at"])
                if (collected_at - previous_at).total_seconds() < min_interval_seconds:
                    return False
            connection.execute(
                """
                INSERT INTO report_snapshots(
                    collected_at, source_generated_at, state, fingerprint, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (collected_iso, source_generated_at, state, fingerprint, serialized),
            )
        return True

    def list_reports(self, limit=100):
        limit = max(1, min(int(limit), 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, collected_at, source_generated_at, state, payload_json
                FROM report_snapshots ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "collected_at": row["collected_at"],
                "source_generated_at": row["source_generated_at"],
                "state": row["state"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def record_network(self, network, collected_at=None, min_interval_seconds=3600):
        collected_at = collected_at or datetime.now()
        connectivity = network.get("connectivity") if isinstance(network, dict) else {}
        counters = network.get("traffic_counters") if isinstance(network, dict) else {}
        if not isinstance(connectivity, dict):
            connectivity = {}
        if not isinstance(counters, dict):
            counters = {}
        counter_names = (
            "received_bytes",
            "sent_bytes",
            "received_packets",
            "sent_packets",
            "received_errors",
            "sent_errors",
            "received_discarded",
            "sent_discarded",
        )
        aggregate = {
            "state": str(network.get("state", "unavailable"))[:32],
            "coverage": str(network.get("coverage", "none"))[:64],
            "active_interface_count": max(0, int(connectivity.get("active_interface_count") or 0)),
            "primary_connection_type": (
                str(connectivity.get("primary_connection_type") or "unknown")
                if str(connectivity.get("primary_connection_type") or "unknown")
                in {"wired", "wireless", "virtual", "unknown"}
                else "unknown"
            ),
            "wired_interface_count": max(0, min(int(connectivity.get("wired_interface_count") or 0), 16)),
            "wireless_interface_count": max(0, min(int(connectivity.get("wireless_interface_count") or 0), 16)),
            "virtual_interface_count": max(0, min(int(connectivity.get("virtual_interface_count") or 0), 16)),
            "default_gateway_configured": connectivity.get("default_gateway_configured") is True,
            "dns_configured": connectivity.get("dns_configured") is True,
        }
        for name in counter_names:
            try:
                value = int(counters.get(name) or 0)
            except (TypeError, ValueError, OverflowError):
                value = 0
            aggregate[name] = max(0, min(value, (1 << 63) - 1))
        fingerprint = _fingerprint(aggregate)
        with self._lock, self._connection() as connection:
            previous = connection.execute(
                "SELECT collected_at, fingerprint FROM network_samples ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if previous and previous["fingerprint"] == fingerprint:
                previous_at = datetime.fromisoformat(previous["collected_at"])
                if (collected_at - previous_at).total_seconds() < min_interval_seconds:
                    return False
            connection.execute(
                """
                INSERT INTO network_samples(
                    collected_at, state, fingerprint, active_interface_count,
                    primary_connection_type, wired_interface_count,
                    wireless_interface_count, virtual_interface_count,
                    default_gateway_configured, dns_configured, coverage,
                    received_bytes, sent_bytes, received_packets, sent_packets,
                    received_errors, sent_errors, received_discarded, sent_discarded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _iso(collected_at),
                    aggregate["state"],
                    fingerprint,
                    aggregate["active_interface_count"],
                    aggregate["primary_connection_type"],
                    aggregate["wired_interface_count"],
                    aggregate["wireless_interface_count"],
                    aggregate["virtual_interface_count"],
                    int(aggregate["default_gateway_configured"]),
                    int(aggregate["dns_configured"]),
                    aggregate["coverage"],
                    *(aggregate[name] for name in counter_names),
                ),
            )
        return True

    def list_network_samples(self, limit=100):
        limit = max(1, min(int(limit), 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT collected_at, state, active_interface_count,
                       primary_connection_type, wired_interface_count,
                       wireless_interface_count, virtual_interface_count,
                       default_gateway_configured, dns_configured, coverage,
                       received_bytes, sent_bytes, received_packets, sent_packets,
                       received_errors, sent_errors, received_discarded, sent_discarded
                FROM network_samples ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        results = []
        for index, row in enumerate(rows):
            item = {
                "collected_at": row["collected_at"],
                "state": row["state"],
                "active_interface_count": row["active_interface_count"],
                "primary_connection_type": row["primary_connection_type"],
                "wired_interface_count": row["wired_interface_count"],
                "wireless_interface_count": row["wireless_interface_count"],
                "virtual_interface_count": row["virtual_interface_count"],
                "default_gateway_configured": bool(row["default_gateway_configured"]),
                "dns_configured": bool(row["dns_configured"]),
                "coverage": row["coverage"],
            }
            for name in (
                "received_bytes",
                "sent_bytes",
                "received_packets",
                "sent_packets",
                "received_errors",
                "sent_errors",
                "received_discarded",
                "sent_discarded",
            ):
                item[name] = row[name]
            item["received_bytes_per_second"] = None
            item["sent_bytes_per_second"] = None
            item["discarded_delta"] = None
            item["error_delta"] = None
            item["counter_reset_detected"] = False
            item["sample_continuity_broken"] = False
            if index + 1 < len(rows):
                older = rows[index + 1]
                item["sample_continuity_broken"] = bool(
                    row["state"] != "active"
                    or older["state"] != "active"
                    or row["primary_connection_type"]
                    != older["primary_connection_type"]
                )
                tracked_counters = (
                    "received_bytes",
                    "sent_bytes",
                    "received_packets",
                    "sent_packets",
                    "received_errors",
                    "sent_errors",
                    "received_discarded",
                    "sent_discarded",
                )
                if not item["sample_continuity_broken"]:
                    item["counter_reset_detected"] = any(
                        row[name] < older[name] for name in tracked_counters
                    )
                elapsed = (
                    datetime.fromisoformat(row["collected_at"])
                    - datetime.fromisoformat(older["collected_at"])
                ).total_seconds()
                received_delta = row["received_bytes"] - older["received_bytes"]
                sent_delta = row["sent_bytes"] - older["sent_bytes"]
                if (
                    elapsed > 0
                    and not item["sample_continuity_broken"]
                    and not item["counter_reset_detected"]
                    and received_delta >= 0
                    and sent_delta >= 0
                ):
                    item["received_bytes_per_second"] = round(received_delta / elapsed, 2)
                    item["sent_bytes_per_second"] = round(sent_delta / elapsed, 2)
                fault_names = (
                    "received_errors",
                    "sent_errors",
                    "received_discarded",
                    "sent_discarded",
                )
                fault_deltas = [row[name] - older[name] for name in fault_names]
                if (
                    elapsed > 0
                    and not item["sample_continuity_broken"]
                    and not item["counter_reset_detected"]
                    and all(value >= 0 for value in fault_deltas)
                ):
                    item["discarded_delta"] = fault_deltas[2] + fault_deltas[3]
                    item["error_delta"] = sum(fault_deltas)
            results.append(item)
        return results

    def summarize_network_traffic(self, limit=120, samples=None):
        limit = max(1, min(int(limit), 1000))
        samples = (
            self.list_network_samples(limit=limit)
            if samples is None
            else list(samples)[:limit]
        )
        if not samples:
            return {
                "state": "no_data",
                "anomaly": "insufficient_history",
                "traffic_detected": False,
                "current_bytes_per_second": None,
                "baseline_bytes_per_second": None,
                "sample_count": 0,
                "scope": "this_host_aggregate_only",
                "captures_content": False,
            }

        latest = samples[0]
        current_rate = None
        if (
            latest.get("received_bytes_per_second") is not None
            and latest.get("sent_bytes_per_second") is not None
        ):
            current_rate = round(
                latest["received_bytes_per_second"]
                + latest["sent_bytes_per_second"],
                2,
            )
        prior_rates = []
        for sample in samples[1:]:
            received = sample.get("received_bytes_per_second")
            sent = sample.get("sent_bytes_per_second")
            if received is None or sent is None or sample.get("counter_reset_detected"):
                continue
            prior_rates.append(received + sent)
        baseline = round(float(median(prior_rates)), 2) if prior_rates else None
        spike = bool(
            current_rate is not None
            and len(prior_rates) >= 3
            and current_rate >= TRAFFIC_SPIKE_MIN_BYTES_PER_SECOND
            and current_rate >= max(1.0, baseline or 0.0) * TRAFFIC_SPIKE_MULTIPLIER
        )
        recent_faults = int(latest.get("error_delta") or 0)
        if latest.get("state") != "active":
            state = "limited"
            anomaly = "collection_unavailable"
        elif latest.get("sample_continuity_broken"):
            state = "limited"
            anomaly = "continuity_changed"
        elif latest.get("counter_reset_detected"):
            state = "warning"
            anomaly = "counter_reset"
        elif recent_faults:
            state = "warning"
            anomaly = "link_errors"
        elif spike:
            state = "warning"
            anomaly = "traffic_spike"
        elif current_rate is None:
            state = "limited"
            anomaly = "insufficient_history"
        else:
            state = "active" if current_rate >= 1024 else "idle"
            anomaly = "none"
        return {
            "state": state,
            "anomaly": anomaly,
            "traffic_detected": bool(current_rate is not None and current_rate >= 1024),
            "current_bytes_per_second": current_rate,
            "baseline_bytes_per_second": baseline,
            "sample_count": len(samples),
            "scope": "this_host_aggregate_only",
            "captures_content": False,
        }

    def _update_presence_stats(
        self,
        connection,
        profile_id,
        stream,
        occurred_at,
        sample_seconds=None,
    ):
        profile_id = str(profile_id).strip()[:80]
        stream = str(stream).strip()[:80]
        if not profile_id or not stream:
            return
        occurred_at = _iso(occurred_at)
        sample_seconds = max(
            0.0,
            min(float(sample_seconds or PRESENCE_SAMPLE_SECONDS), PRESENCE_SESSION_GAP_SECONDS),
        )
        occurred_datetime = datetime.fromisoformat(occurred_at)
        window_start = _iso(
            occurred_datetime - timedelta(seconds=PRESENCE_SESSION_GAP_SECONDS)
        )
        window_end = _iso(
            occurred_datetime + timedelta(seconds=PRESENCE_SESSION_GAP_SECONDS)
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO profile_presence_stats(
                profile_id, first_seen_at, last_seen_at, visit_count,
                observation_count, observed_seconds
            ) VALUES (?, ?, ?, 0, 0, 0)
            """,
            (profile_id, occurred_at, occurred_at),
        )
        current = connection.execute(
            "SELECT * FROM profile_presence_stats WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        sessions = connection.execute(
            """
            SELECT id, started_at, ended_at, observation_count, sample_seconds
            FROM profile_presence_sessions
            WHERE profile_id = ? AND ended_at >= ? AND started_at <= ?
            ORDER BY started_at ASC, id ASC
            """,
            (profile_id, window_start, window_end),
        ).fetchall()
        started_at = min([occurred_at] + [row["started_at"] for row in sessions])
        ended_at = max([occurred_at] + [row["ended_at"] for row in sessions])
        merged_sample_seconds = max(
            [sample_seconds] + [float(row["sample_seconds"]) for row in sessions]
        )
        merged_observations = 1 + sum(
            int(row["observation_count"]) for row in sessions
        )
        old_session_seconds = sum(
            self._presence_session_seconds(
                row["started_at"], row["ended_at"], row["sample_seconds"]
            )
            for row in sessions
        )
        if sessions:
            placeholders = ",".join("?" for _ in sessions)
            connection.execute(
                f"DELETE FROM profile_presence_sessions WHERE id IN ({placeholders})",
                [row["id"] for row in sessions],
            )
        connection.execute(
            """
            INSERT INTO profile_presence_sessions(
                profile_id, started_at, ended_at, observation_count, sample_seconds
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                started_at,
                ended_at,
                merged_observations,
                merged_sample_seconds,
            ),
        )
        new_session_seconds = self._presence_session_seconds(
            started_at, ended_at, merged_sample_seconds
        )
        connection.execute(
            """
            UPDATE profile_presence_stats
            SET first_seen_at = MIN(first_seen_at, ?),
                last_seen_at = MAX(last_seen_at, ?),
                visit_count = visit_count - ? + 1,
                observation_count = observation_count + 1,
                observed_seconds = MAX(0, observed_seconds - ? + ?)
            WHERE profile_id = ?
            """,
            (
                occurred_at,
                occurred_at,
                len(sessions),
                old_session_seconds,
                new_session_seconds,
                profile_id,
            ),
        )
        stream_row = connection.execute(
            """
            SELECT first_seen_at, last_seen_at, observation_count
            FROM profile_presence_streams
            WHERE profile_id = ? AND stream = ?
            """,
            (profile_id, stream),
        ).fetchone()
        if stream_row is None:
            connection.execute(
                """
                INSERT INTO profile_presence_streams(
                    profile_id, stream, first_seen_at, last_seen_at, observation_count
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (profile_id, stream, occurred_at, occurred_at),
            )
        else:
            connection.execute(
                """
                UPDATE profile_presence_streams
                SET first_seen_at = MIN(first_seen_at, ?),
                    last_seen_at = CASE WHEN ? > last_seen_at THEN ? ELSE last_seen_at END,
                    observation_count = observation_count + 1
                WHERE profile_id = ? AND stream = ?
                """,
                (occurred_at, occurred_at, occurred_at, profile_id, stream),
            )

    @staticmethod
    def _presence_session_seconds(started_at, ended_at, sample_seconds):
        elapsed = max(
            0.0,
            (
                datetime.fromisoformat(ended_at)
                - datetime.fromisoformat(started_at)
            ).total_seconds(),
        )
        return elapsed + max(0.0, float(sample_seconds))

    def record_vision_event(self, event):
        allowed_types = {
            "motion_start",
            "motion_end",
            "face_count",
            "presence_confirmed",
            "analysis_error",
        }
        event_type = str(event.get("event_type", ""))
        if event_type not in allowed_types:
            raise ValueError("invalid_vision_event_type")
        stream = str(event.get("stream", "")).strip()[:80]
        if not stream:
            raise ValueError("invalid_vision_stream")
        occurred_at = _iso(event.get("occurred_at") or datetime.now())
        event_id = str(event.get("event_id") or uuid.uuid4())
        count = event.get("count")
        count = max(0, int(count)) if count is not None else None
        confidence = event.get("confidence")
        confidence = max(0.0, min(float(confidence), 1.0)) if confidence is not None else None
        duration = event.get("duration_seconds")
        duration = max(0.0, float(duration)) if duration is not None else None
        profile_id = str(event.get("profile_id"))[:80] if event.get("profile_id") else None
        with self._lock, self._connection() as connection:
            if event_type == "presence_confirmed" and profile_id:
                deleted_profile = connection.execute(
                    "SELECT 1 FROM deleted_profile_hashes WHERE profile_hash = ?",
                    (_profile_hash(profile_id),),
                ).fetchone()
                if deleted_profile is not None:
                    return event_id
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO vision_events(
                    event_id, occurred_at, event_type, stream, count,
                    profile_id, confidence, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, occurred_at, event_type, stream, count, profile_id, confidence, duration),
            )
            if cursor.rowcount and event_type == "presence_confirmed" and profile_id:
                self._update_presence_stats(
                    connection,
                    profile_id,
                    stream,
                    occurred_at,
                    duration,
                )
        return event_id

    def list_vision_events(self, limit=200):
        limit = max(1, min(int(limit), 2000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM vision_events ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_profile_presence_summary(self, limit=100):
        limit = max(1, min(int(limit), 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT profile_id, first_seen_at, last_seen_at, visit_count,
                       observation_count, observed_seconds
                FROM profile_presence_stats
                ORDER BY observed_seconds DESC, visit_count DESC, last_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            profile_ids = [row["profile_id"] for row in rows]
            streams_by_profile = {profile_id: [] for profile_id in profile_ids}
            if profile_ids:
                placeholders = ",".join("?" for _ in profile_ids)
                stream_rows = connection.execute(
                    f"""
                    SELECT profile_id, stream
                    FROM profile_presence_streams
                    WHERE profile_id IN ({placeholders})
                    ORDER BY profile_id ASC, stream ASC
                    """,
                    profile_ids,
                ).fetchall()
                for stream_row in stream_rows:
                    streams_by_profile[stream_row["profile_id"]].append(stream_row["stream"])
        return [
            {
                "profile_id": row["profile_id"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "visit_count": row["visit_count"],
                "observation_count": row["observation_count"],
                "observed_seconds": round(float(row["observed_seconds"]), 1),
                "streams": streams_by_profile.get(row["profile_id"], []),
            }
            for row in rows
        ]

    def delete_profile_presence(self, profile_id):
        profile_id = str(profile_id).strip()[:80]
        if not profile_id:
            return False
        profile_hash = _profile_hash(profile_id)
        with self._lock, self._connection() as connection:
            tombstone_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO deleted_profile_hashes(profile_hash, deleted_at)
                VALUES (?, ?)
                """,
                (profile_hash, _iso(datetime.now())),
            )
            event_cursor = connection.execute(
                "DELETE FROM vision_events WHERE profile_id = ?",
                (profile_id,),
            )
            stats_cursor = connection.execute(
                "DELETE FROM profile_presence_stats WHERE profile_id = ?",
                (profile_id,),
            )
            deleted = (
                tombstone_cursor.rowcount > 0
                or stats_cursor.rowcount > 0
                or event_cursor.rowcount > 0
            )
            if deleted:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO maintenance_state(key, value)
                    VALUES ('sensitive_compaction_pending', '1')
                    """
                )
        if deleted:
            self._run_pending_sensitive_compaction()
        return deleted

    def _run_pending_sensitive_compaction(self):
        with self._lock, self._connection() as connection:
            pending = connection.execute(
                """
                SELECT 1 FROM maintenance_state
                WHERE key = 'sensitive_compaction_pending'
                """
            ).fetchone()
        if pending is None:
            return True
        if not self._compact_after_sensitive_delete():
            return False
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM maintenance_state WHERE key = 'sensitive_compaction_pending'"
            )
        return True

    def _compact_after_sensitive_delete(self):
        try:
            with self._lock:
                connection = self._connect()
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    connection.execute("VACUUM")
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    connection.close()
            self.last_cleanup_error = None
            return True
        except (OSError, sqlite3.Error) as error:
            self.last_cleanup_error = str(error)[:200]
            return False

    def cleanup(self, retention_days=90, now=None):
        retention_days = max(7, min(int(retention_days), 3650))
        cutoff = _iso((now or datetime.now()) - timedelta(days=retention_days))
        deleted = 0
        with self._lock, self._connection() as connection:
            for table, column in (
                ("report_snapshots", "collected_at"),
                ("network_samples", "collected_at"),
                ("vision_events", "occurred_at"),
            ):
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE {column} < ?",
                    (cutoff,),
                )
                deleted += max(0, cursor.rowcount)
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return deleted

    def close(self):
        return None
