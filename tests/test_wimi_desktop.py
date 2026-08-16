import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


from wimi_analytics.collector import AnalyticsCollector
from wimi_analytics.storage import AnalyticsStore, UnsafeAnalyticsPathError


class AnalyticsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "runtime" / "analytics.sqlite3"
        self.store = AnalyticsStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def sample_report(self, state="current"):
        return {
            "schema_version": 1,
            "state": state,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_generated_at": datetime.now().isoformat(timespec="seconds"),
            "checks": [
                {
                    "id": "storage",
                    "label": "HD",
                    "status": "active",
                    "value": "800 GB",
                    "detail": "Disponivel",
                    "evidence": "nvr.metrics.hd_available",
                }
            ],
        }

    def sample_readiness(self):
        return {
            "schema_version": 1,
            "status": "limited",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "strengths": [],
            "limitations": [{"id": "vision", "label": "Visao pendente", "detail": "", "evidence": "vision"}],
        }

    def test_initializes_bounded_schema_outside_recording_root(self):
        self.assertTrue(self.db_path.exists())
        with closing(sqlite3.connect(self.db_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            journal_limit = connection.execute("PRAGMA journal_size_limit").fetchone()[0]

        self.assertTrue(
            {
                "report_snapshots",
                "network_samples",
                "vision_events",
                "profile_presence_stats",
                "profile_presence_streams",
                "profile_presence_sessions",
                "deleted_profile_hashes",
                "maintenance_state",
            }.issubset(tables)
        )
        self.assertNotIn("face_profiles", tables)
        self.assertLessEqual(journal_limit, 8 * 1024 * 1024)

        recording_root = self.root / "videos"
        recording_root.mkdir()
        with self.assertRaises(UnsafeAnalyticsPathError):
            AnalyticsStore(recording_root / "analytics.sqlite3", forbidden_roots=[recording_root])

    def test_adds_connection_columns_to_existing_network_history(self):
        legacy_path = self.root / "legacy" / "analytics.sqlite3"
        legacy_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(legacy_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE network_samples (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        collected_at TEXT NOT NULL,
                        state TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        active_interface_count INTEGER NOT NULL,
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
                    )
                    """
                )

        migrated = AnalyticsStore(legacy_path)
        migrated.close()
        with closing(sqlite3.connect(legacy_path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(network_samples)")
            }

        self.assertTrue(
            {
                "primary_connection_type",
                "wired_interface_count",
                "wireless_interface_count",
                "virtual_interface_count",
            }.issubset(columns)
        )

    def test_migration_does_not_resurrect_historical_profile_summaries(self):
        legacy_path = self.root / "legacy-presence" / "analytics.sqlite3"
        legacy_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(legacy_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE vision_events (
                        event_id TEXT PRIMARY KEY,
                        occurred_at TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        stream TEXT NOT NULL,
                        count INTEGER,
                        profile_id TEXT,
                        confidence REAL,
                        duration_seconds REAL
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO vision_events(
                        event_id, occurred_at, event_type, stream, profile_id
                    ) VALUES (?, ?, 'presence_confirmed', 'farmacia', ?)
                    """,
                    (
                        ("old-1", "2026-08-16T09:00:00", "profile-consentido"),
                        ("old-2", "2026-08-16T09:01:00", "profile-consentido"),
                    ),
                )

        migrated = AnalyticsStore(legacy_path)
        first_summary = migrated.list_profile_presence_summary()
        migrated_events = migrated.list_vision_events(limit=10)
        migrated.close()
        reopened = AnalyticsStore(legacy_path)
        second_summary = reopened.list_profile_presence_summary()
        with closing(sqlite3.connect(legacy_path)) as connection:
            schema_version = connection.execute("SELECT version FROM schema_meta").fetchone()[0]
        reopened.close()

        self.assertEqual(first_summary, [])
        self.assertEqual(second_summary, [])
        self.assertEqual(migrated_events, [])
        self.assertEqual(schema_version, 3)
        self.assertNotIn(b"profile-consentido", legacy_path.read_bytes())

    def test_report_writes_only_on_change_or_safety_interval(self):
        report = self.sample_report()
        readiness = self.sample_readiness()
        now = datetime(2026, 8, 16, 9, 0, 0)

        self.assertTrue(self.store.record_report(report, readiness, collected_at=now))
        self.assertFalse(
            self.store.record_report(
                report,
                readiness,
                collected_at=now + timedelta(minutes=5),
            )
        )
        self.assertTrue(
            self.store.record_report(
                report,
                readiness,
                collected_at=now + timedelta(minutes=16),
            )
        )
        changed = self.sample_report(state="partial")
        self.assertTrue(
            self.store.record_report(
                changed,
                readiness,
                collected_at=now + timedelta(minutes=17),
            )
        )
        self.assertEqual(len(self.store.list_reports(limit=20)), 3)

    def test_network_history_is_aggregate_and_deduplicated(self):
        network = {
            "state": "active",
            "coverage": "host_configuration_and_counters",
            "can_observe_store_traffic": False,
            "interfaces": [
                {
                    "name": "Ethernet",
                    "ipv4": ["192.168.1.50"],
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "dns_servers": ["1.1.1.1"],
                }
            ],
            "connectivity": {
                "active_interface_count": 1,
                "primary_connection_type": "wired",
                "wired_interface_count": 1,
                "wireless_interface_count": 0,
                "virtual_interface_count": 0,
                "default_gateway_configured": True,
                "dns_configured": True,
            },
            "traffic_counters": {
                "received_bytes": 100000,
                "sent_bytes": 50000,
                "received_packets": 1000,
                "sent_packets": 500,
                "received_errors": 0,
                "sent_errors": 0,
                "received_discarded": 0,
                "sent_discarded": 0,
            },
        }
        now = datetime(2026, 8, 16, 10, 0, 0)

        self.assertTrue(self.store.record_network(network, collected_at=now))
        self.assertFalse(
            self.store.record_network(network, collected_at=now + timedelta(minutes=10))
        )
        changed = json.loads(json.dumps(network))
        changed["traffic_counters"]["received_bytes"] += 60000
        changed["traffic_counters"]["sent_bytes"] += 30000
        changed["traffic_counters"]["received_discarded"] += 5
        self.assertTrue(
            self.store.record_network(changed, collected_at=now + timedelta(minutes=11))
        )
        samples = self.store.list_network_samples(limit=10)
        serialized = json.dumps(samples)

        self.assertEqual(len(samples), 2)
        self.assertNotIn("192.168", serialized)
        self.assertNotIn("AA:BB", serialized)
        self.assertNotIn("1.1.1.1", serialized)
        self.assertEqual(samples[0]["active_interface_count"], 1)
        self.assertEqual(samples[0]["primary_connection_type"], "wired")
        self.assertEqual(samples[0]["wired_interface_count"], 1)
        self.assertEqual(samples[0]["received_bytes_per_second"], 90.91)
        self.assertEqual(samples[0]["sent_bytes_per_second"], 45.45)
        self.assertEqual(samples[0]["discarded_delta"], 5)
        self.assertEqual(samples[0]["error_delta"], 5)

    def test_cleanup_is_limited_to_analytics_rows(self):
        old = datetime(2026, 4, 1, 8, 0, 0)
        current = datetime(2026, 8, 16, 8, 0, 0)
        self.store.record_report(self.sample_report(), self.sample_readiness(), collected_at=old)
        self.store.record_vision_event(
            {"event_type": "motion_start", "stream": "farmacia", "occurred_at": old.isoformat()}
        )
        sentinel = self.root / "video.ts"
        sentinel.write_bytes(b"video")

        deleted = self.store.cleanup(retention_days=90, now=current)

        self.assertGreaterEqual(deleted, 2)
        self.assertEqual(self.store.list_reports(limit=10), [])
        self.assertEqual(sentinel.read_bytes(), b"video")

    def test_network_counter_reset_is_reported_as_inconclusive_delta(self):
        network = {
            "state": "active",
            "coverage": "host_configuration_and_counters",
            "connectivity": {"active_interface_count": 1},
            "traffic_counters": {
                "received_bytes": 1000,
                "sent_bytes": 500,
                "received_packets": 10,
                "sent_packets": 5,
                "received_errors": 0,
                "sent_errors": 0,
                "received_discarded": 8,
                "sent_discarded": 0,
            },
        }
        now = datetime(2026, 8, 16, 11, 0, 0)
        self.assertTrue(self.store.record_network(network, collected_at=now))
        reset = json.loads(json.dumps(network))
        reset["traffic_counters"]["received_bytes"] = 20
        reset["traffic_counters"]["received_discarded"] = 0
        self.assertTrue(
            self.store.record_network(reset, collected_at=now + timedelta(minutes=1))
        )

        latest = self.store.list_network_samples(limit=2)[0]

        self.assertTrue(latest["counter_reset_detected"])
        self.assertIsNone(latest["received_bytes_per_second"])
        self.assertIsNone(latest["error_delta"])

    def test_presence_summary_tracks_visits_without_storing_names_or_images(self):
        start = datetime(2026, 8, 16, 9, 0, 0)
        observations = (
            ("event-1", "farmacia", start),
            ("event-2", "farmacia2", start),
            ("event-3", "farmacia", start + timedelta(seconds=60)),
            ("event-4", "farmacia", start + timedelta(seconds=120)),
            ("event-5", "farmacia", start + timedelta(seconds=300)),
        )
        for event_id, stream, occurred_at in observations:
            self.store.record_vision_event(
                {
                    "event_id": event_id,
                    "event_type": "presence_confirmed",
                    "stream": stream,
                    "profile_id": "profile-consentido",
                    "occurred_at": occurred_at,
                }
            )
        self.store.record_vision_event(
            {
                "event_id": "event-outro",
                "event_type": "presence_confirmed",
                "stream": "farmacia2",
                "profile_id": "profile-outro",
                "occurred_at": start + timedelta(seconds=30),
            }
        )

        summaries = self.store.list_profile_presence_summary(limit=10)
        serialized = json.dumps(summaries)

        self.assertEqual(summaries[0]["profile_id"], "profile-consentido")
        self.assertEqual(summaries[0]["visit_count"], 2)
        self.assertEqual(summaries[0]["observation_count"], 5)
        self.assertEqual(summaries[0]["observed_seconds"], 126.0)
        self.assertEqual(summaries[0]["streams"], ["farmacia", "farmacia2"])
        self.assertNotIn("nome", serialized.casefold())
        self.assertNotIn("image", serialized.casefold())

        self.assertTrue(self.store.delete_profile_presence("profile-consentido"))
        self.assertEqual(
            [item["profile_id"] for item in self.store.list_profile_presence_summary()],
            ["profile-outro"],
        )
        self.assertNotIn(
            "profile-consentido",
            [item.get("profile_id") for item in self.store.list_vision_events(limit=20)],
        )
        self.store.record_vision_event(
            {
                "event_id": "event-after-delete",
                "event_type": "presence_confirmed",
                "stream": "farmacia",
                "profile_id": "profile-consentido",
                "occurred_at": start + timedelta(seconds=360),
            }
        )
        self.assertNotIn(
            "profile-consentido",
            [item.get("profile_id") for item in self.store.list_vision_events(limit=20)],
        )
        self.assertEqual(
            [item["profile_id"] for item in self.store.list_profile_presence_summary()],
            ["profile-outro"],
        )
        self.assertNotIn(b"profile-consentido", self.db_path.read_bytes())
        wal_path = Path(f"{self.db_path}-wal")
        if wal_path.exists():
            self.assertNotIn(b"profile-consentido", wal_path.read_bytes())

    def test_duplicate_presence_event_does_not_double_count(self):
        event = {
            "event_id": "same-event",
            "event_type": "presence_confirmed",
            "stream": "farmacia",
            "profile_id": "profile-consentido",
            "occurred_at": datetime(2026, 8, 16, 10, 0, 0),
        }

        self.store.record_vision_event(event)
        self.store.record_vision_event(event)

        summary = self.store.list_profile_presence_summary()[0]
        self.assertEqual(summary["observation_count"], 1)
        self.assertEqual(summary["visit_count"], 1)

    def test_failed_sensitive_compaction_is_retried_on_next_open(self):
        self.store.record_vision_event(
            {
                "event_id": "event-before-delete",
                "event_type": "presence_confirmed",
                "stream": "farmacia",
                "profile_id": "profile-consentido",
                "occurred_at": datetime(2026, 8, 16, 10, 0, 0),
            }
        )
        original_compact = self.store._compact_after_sensitive_delete
        self.store._compact_after_sensitive_delete = lambda: False
        try:
            self.assertTrue(self.store.delete_profile_presence("profile-consentido"))
        finally:
            self.store._compact_after_sensitive_delete = original_compact
        with closing(sqlite3.connect(self.db_path)) as connection:
            pending_before = connection.execute(
                """
                SELECT value FROM maintenance_state
                WHERE key = 'sensitive_compaction_pending'
                """
            ).fetchone()
        self.assertIsNotNone(pending_before)

        reopened = AnalyticsStore(self.db_path)
        reopened.close()
        with closing(sqlite3.connect(self.db_path)) as connection:
            pending_after = connection.execute(
                """
                SELECT value FROM maintenance_state
                WHERE key = 'sensitive_compaction_pending'
                """
            ).fetchone()

        self.assertIsNone(pending_after)

    def test_delayed_presence_event_produces_same_summary_as_chronological_order(self):
        start = datetime(2026, 8, 16, 10, 0, 0)

        def record(store, offsets):
            for offset in offsets:
                store.record_vision_event(
                    {
                        "event_id": f"event-{offset}",
                        "event_type": "presence_confirmed",
                        "stream": "farmacia",
                        "profile_id": "profile-consentido",
                        "occurred_at": start + timedelta(seconds=offset),
                    }
                )

        record(self.store, (0, 100, 200))
        expected = self.store.list_profile_presence_summary()[0]
        delayed_store = AnalyticsStore(self.root / "delayed" / "analytics.sqlite3")
        try:
            record(delayed_store, (0, 200, 100))
            actual = delayed_store.list_profile_presence_summary()[0]
        finally:
            delayed_store.close()

        self.assertEqual(actual, expected)
        self.assertEqual(actual["visit_count"], 3)

    def test_delayed_event_after_cleanup_preserves_lifetime_presence_summary(self):
        start = datetime(2026, 1, 1, 10, 0, 0)
        for offset in (0, 100, 200):
            self.store.record_vision_event(
                {
                    "event_id": f"event-{offset}",
                    "event_type": "presence_confirmed",
                    "stream": "farmacia",
                    "profile_id": "profile-consentido",
                    "occurred_at": start + timedelta(seconds=offset),
                }
            )
        self.store.cleanup(retention_days=90, now=datetime(2026, 8, 16, 10, 0, 0))
        self.assertEqual(self.store.list_vision_events(limit=20), [])

        self.store.record_vision_event(
            {
                "event_id": "event-delayed",
                "event_type": "presence_confirmed",
                "stream": "farmacia2",
                "profile_id": "profile-consentido",
                "occurred_at": start + timedelta(seconds=50),
            }
        )
        summary = self.store.list_profile_presence_summary()[0]

        self.assertEqual(summary["observation_count"], 4)
        self.assertEqual(summary["visit_count"], 2)
        self.assertEqual(summary["first_seen_at"], "2026-01-01T10:00:00")
        self.assertEqual(summary["last_seen_at"], "2026-01-01T10:03:20")

    def test_network_summary_detects_only_aggregate_activity_and_anomalies(self):
        base = {
            "state": "active",
            "coverage": "host_configuration_and_counters",
            "connectivity": {
                "active_interface_count": 1,
                "primary_connection_type": "wired",
            },
            "traffic_counters": {
                "received_bytes": 0,
                "sent_bytes": 0,
                "received_packets": 0,
                "sent_packets": 0,
                "received_errors": 0,
                "sent_errors": 0,
                "received_discarded": 0,
                "sent_discarded": 0,
            },
        }
        start = datetime(2026, 8, 16, 10, 0, 0)
        totals = (0, 60_000, 120_000, 180_000, 400_000_000)
        for index, total in enumerate(totals):
            sample = json.loads(json.dumps(base))
            sample["traffic_counters"]["received_bytes"] = total
            self.store.record_network(
                sample,
                collected_at=start + timedelta(minutes=index),
                min_interval_seconds=0,
            )

        summary = self.store.summarize_network_traffic(limit=20)

        self.assertEqual(summary["state"], "warning")
        self.assertEqual(summary["anomaly"], "traffic_spike")
        self.assertTrue(summary["traffic_detected"])
        self.assertFalse(summary["captures_content"])
        self.assertEqual(summary["scope"], "this_host_aggregate_only")
        self.assertNotIn("destination", summary)

    def test_network_summary_does_not_call_unavailable_collection_idle(self):
        unavailable = {
            "state": "unavailable",
            "coverage": "host_configuration_and_counters",
            "connectivity": {"active_interface_count": 0},
            "traffic_counters": {},
        }
        start = datetime(2026, 8, 16, 12, 0, 0)
        self.store.record_network(unavailable, collected_at=start, min_interval_seconds=0)
        self.store.record_network(
            unavailable,
            collected_at=start + timedelta(minutes=1),
            min_interval_seconds=0,
        )

        summary = self.store.summarize_network_traffic()

        self.assertEqual(summary["state"], "limited")
        self.assertEqual(summary["anomaly"], "collection_unavailable")
        self.assertFalse(summary["traffic_detected"])

    def test_network_delta_is_inconclusive_after_unavailable_sample(self):
        active = {
            "state": "active",
            "coverage": "host_configuration_and_counters",
            "connectivity": {
                "active_interface_count": 1,
                "primary_connection_type": "wired",
            },
            "traffic_counters": {
                "received_bytes": 1_000,
                "sent_bytes": 500,
                "received_packets": 10,
                "sent_packets": 5,
            },
        }
        unavailable = json.loads(json.dumps(active))
        unavailable["state"] = "unavailable"
        unavailable["traffic_counters"] = {}
        recovered = json.loads(json.dumps(active))
        recovered["traffic_counters"]["received_bytes"] = 20_000_000
        recovered["traffic_counters"]["sent_bytes"] = 10_000_000
        start = datetime(2026, 8, 16, 13, 0, 0)
        for index, sample in enumerate((active, unavailable, recovered)):
            self.store.record_network(
                sample,
                collected_at=start + timedelta(minutes=index),
                min_interval_seconds=0,
            )

        latest = self.store.list_network_samples(limit=3)[0]
        summary = self.store.summarize_network_traffic(limit=3)

        self.assertTrue(latest["sample_continuity_broken"])
        self.assertIsNone(latest["received_bytes_per_second"])
        self.assertIsNone(latest["error_delta"])
        self.assertEqual(summary["state"], "limited")
        self.assertEqual(summary["anomaly"], "continuity_changed")


class AnalyticsCollectorTests(unittest.TestCase):
    def test_collect_once_persists_sanitized_operational_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AnalyticsStore(Path(temp_dir) / "analytics.sqlite3")

            class FakeBridge:
                def read(self):
                    return {
                        "state": "active",
                        "reason": "snapshot_current",
                        "snapshot": {
                            "schema_version": 1,
                            "generated_at": datetime.now().isoformat(timespec="seconds"),
                            "overall_status": "healthy",
                            "issues": [],
                            "metrics": {"hd_available": True, "pending_backup_count": 0},
                        },
                    }

            class FakeNetwork:
                def read(self):
                    return {
                        "schema_version": 1,
                        "state": "active",
                        "coverage": "host_configuration_only",
                        "can_observe_store_traffic": False,
                        "interfaces": [],
                        "connectivity": {
                            "active_interface_count": 1,
                            "default_gateway_configured": True,
                            "dns_configured": True,
                        },
                    }

            collector = AnalyticsCollector(FakeBridge(), FakeNetwork(), store)
            payload = collector.collect_once()

            self.assertEqual(payload["operations"]["report"]["state"], "current")
            self.assertEqual(len(store.list_reports(limit=10)), 1)
            self.assertEqual(len(store.list_network_samples(limit=10)), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
