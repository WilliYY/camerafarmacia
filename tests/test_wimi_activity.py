import unittest
from datetime import datetime

from wimi_analytics.activity import build_profile_activity


class ProfileActivityTests(unittest.TestCase):
    def test_groups_repeated_confirmations_and_reports_camera_sequence(self):
        observations = [
            {
                "event_id": "3",
                "event_type": "presence_confirmed",
                "profile_id": "profile-1",
                "stream": "farmacia2",
                "occurred_at": "2026-08-23T10:02:00",
                "confidence": 0.91,
            },
            {
                "event_id": "1",
                "event_type": "presence_confirmed",
                "profile_id": "profile-1",
                "stream": "farmacia",
                "occurred_at": "2026-08-23T10:00:00",
                "confidence": 0.88,
            },
            {
                "event_id": "2",
                "event_type": "presence_confirmed",
                "profile_id": "profile-1",
                "stream": "farmacia",
                "occurred_at": "2026-08-23T10:01:00",
                "confidence": 0.90,
            },
        ]
        profiles = [
            {
                "profile_id": "profile-1",
                "display_name": "Thiago",
                "role": "employee",
            }
        ]

        result = build_profile_activity(
            observations,
            profiles,
            now=datetime(2026, 8, 23, 10, 3, 0),
        )

        self.assertEqual(result["summary"]["profile_count"], 1)
        self.assertEqual(result["summary"]["transition_count"], 1)
        transition = next(
            item for item in result["activities"] if item["kind"] == "transition"
        )
        repeated = next(
            item
            for item in result["activities"]
            if item["kind"] == "observed_window"
        )
        self.assertEqual(transition["display_name"], "Thiago")
        self.assertEqual(transition["from_stream"], "farmacia")
        self.assertEqual(transition["to_stream"], "farmacia2")
        self.assertEqual(transition["duration_seconds"], 60.0)
        self.assertIn("Sequência observada", transition["description"])
        self.assertEqual(repeated["confirmation_count"], 2)
        self.assertEqual(repeated["duration_seconds"], 60.0)

    def test_same_second_confirmations_do_not_claim_camera_change(self):
        result = build_profile_activity(
            [
                {
                    "event_type": "presence_confirmed",
                    "profile_id": "profile-1",
                    "stream": "farmacia2",
                    "occurred_at": "2026-08-23T10:00:00",
                    "confidence": 0.92,
                },
                {
                    "event_type": "presence_confirmed",
                    "profile_id": "profile-1",
                    "stream": "farmacia",
                    "occurred_at": "2026-08-23T10:00:00",
                    "confidence": 0.90,
                },
            ],
            [{"profile_id": "profile-1", "display_name": "Thiago"}],
            now=datetime(2026, 8, 23, 10, 1, 0),
        )

        sequence = next(
            item for item in result["activities"] if item["kind"] == "transition"
        )
        self.assertTrue(sequence["simultaneous"])
        self.assertIn("Confirmações simultâneas", sequence["description"])
        self.assertNotIn("mudança", sequence["description"].casefold())

    def test_long_gap_is_inconclusive_and_never_claims_unknown_location(self):
        observations = [
            {
                "event_id": "1",
                "event_type": "presence_confirmed",
                "profile_id": "profile-1",
                "stream": "farmacia",
                "occurred_at": "2026-08-23T10:00:00",
                "confidence": 0.90,
            },
            {
                "event_id": "2",
                "event_type": "presence_confirmed",
                "profile_id": "profile-1",
                "stream": "farmacia2",
                "occurred_at": "2026-08-23T10:10:00",
                "confidence": 0.92,
            },
        ]

        result = build_profile_activity(
            observations,
            [{"profile_id": "profile-1", "display_name": "Thiago"}],
            now=datetime(2026, 8, 23, 10, 11, 0),
            confirmation_gap_seconds=180,
        )

        gap = next(
            item for item in result["activities"] if item["kind"] == "coverage_gap"
        )
        self.assertEqual(gap["duration_seconds"], 600.0)
        self.assertIn("sem confirmação visual", gap["description"].casefold())
        self.assertNotIn("esteve em", gap["description"].casefold())
        self.assertNotIn("saiu", gap["description"].casefold())

    def test_current_gap_is_derived_without_persisting_a_new_event(self):
        observations = [
            {
                "event_id": "1",
                "event_type": "presence_confirmed",
                "profile_id": "profile-1",
                "stream": "farmacia",
                "occurred_at": "2026-08-23T10:00:00",
                "confidence": 0.90,
            }
        ]

        result = build_profile_activity(
            observations,
            [{"profile_id": "profile-1", "display_name": "Thiago"}],
            now=datetime(2026, 8, 23, 10, 5, 0),
            confirmation_gap_seconds=180,
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(result["summary"]["coverage_gap_count"], 1)
        current_gap = result["activities"][0]
        self.assertEqual(current_gap["kind"], "coverage_gap")
        self.assertTrue(current_gap["current"])
        self.assertEqual(current_gap["duration_seconds"], 300.0)
        self.assertEqual(current_gap["occurred_at"], "2026-08-23T10:00:00")
        self.assertEqual(current_gap["evaluated_at"], "2026-08-23T10:05:00")

    def test_ignores_observations_without_current_consent_profile(self):
        result = build_profile_activity(
            [
                {
                    "event_type": "presence_confirmed",
                    "profile_id": "revoked-profile",
                    "stream": "farmacia",
                    "occurred_at": "2026-08-23T10:00:00",
                    "confidence": 0.90,
                }
            ],
            [],
            now=datetime(2026, 8, 23, 10, 1, 0),
        )

        self.assertEqual(result["activities"], [])
        self.assertEqual(result["summary"]["profile_count"], 0)
        self.assertEqual(result["summary"]["observation_count"], 0)

    def test_timezone_aware_values_use_the_local_timeline_consistently(self):
        result = build_profile_activity(
            [
                {
                    "event_type": "presence_confirmed",
                    "profile_id": "profile-1",
                    "stream": "farmacia",
                    "occurred_at": "2026-08-23T10:00:00-03:00",
                    "confidence": 0.90,
                }
            ],
            [{"profile_id": "profile-1", "display_name": "Thiago"}],
            now=datetime.fromisoformat("2026-08-23T10:02:00-03:00"),
        )

        self.assertEqual(result["summary"]["coverage_gap_count"], 0)
        self.assertEqual(result["activities"][0]["duration_seconds"], 0.0)

    def test_summary_counts_are_not_truncated_with_visible_rows(self):
        result = build_profile_activity(
            [
                {
                    "event_type": "presence_confirmed",
                    "profile_id": "profile-1",
                    "stream": stream,
                    "occurred_at": occurred_at,
                    "confidence": 0.90,
                }
                for stream, occurred_at in (
                    ("farmacia", "2026-08-23T10:00:00"),
                    ("farmacia2", "2026-08-23T10:01:00"),
                    ("farmacia", "2026-08-23T10:02:00"),
                )
            ],
            [{"profile_id": "profile-1", "display_name": "Thiago"}],
            now=datetime(2026, 8, 23, 10, 2, 0),
            limit=1,
        )

        self.assertEqual(len(result["activities"]), 1)
        self.assertEqual(result["summary"]["transition_count"], 2)


if __name__ == "__main__":
    unittest.main()
