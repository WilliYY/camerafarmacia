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
            {"report_snapshots", "network_samples", "vision_events"}.issubset(tables)
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
