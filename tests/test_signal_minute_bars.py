import unittest

from nexustrade import signal
from nexustrade.client import _point_kind_points


class SignalTimestampTest(unittest.TestCase):
    def test_accepts_a_date_and_a_zoned_bar_open(self) -> None:
        signal.validate_row({"timestamp": "2026-09-21", "value": 1})
        signal.validate_row({"timestamp": "2026-09-21T13:30:00Z", "value": -1})
        signal.validate_row({"timestamp": "2026-09-21T09:30:00-04:00", "value": 0})

    def test_rejects_a_zone_less_instant(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid timestamp"):
            signal.validate_row({"timestamp": "2026-09-21T13:30:00", "value": 1})
        with self.assertRaisesRegex(ValueError, "invalid timestamp"):
            signal.validate_row({"timestamp": "20260921", "value": 1})


class OneMinuteAggregateTest(unittest.TestCase):
    def test_bar_is_available_at_its_close(self) -> None:
        points = _point_kind_points(
            [
                {"timestamp": "2026-09-21T13:30:00Z", "value": 1},
                {"timestamp": "2026-09-21T19:59:00Z", "value": 0},
            ],
            "period_aggregate",
            "1min",
        )
        self.assertEqual(
            [point["availableAt"] for point in points],
            ["2026-09-21T13:31:00Z", "2026-09-21T20:00:00Z"],
        )

    def test_rejects_a_date_or_seconds_as_a_bar_key(self) -> None:
        for timestamp in ("2026-09-21", "2026-09-21T13:30:15Z"):
            with self.assertRaisesRegex(ValueError, "1min timestamp"):
                _point_kind_points(
                    [{"timestamp": timestamp, "value": 1}], "period_aggregate", "1min"
                )

    def test_rejects_availability_inside_the_bar(self) -> None:
        with self.assertRaisesRegex(ValueError, "bar close"):
            _point_kind_points(
                [
                    {
                        "timestamp": "2026-09-21T13:30:00Z",
                        "availableAt": "2026-09-21T13:30:30Z",
                        "value": 1,
                    }
                ],
                "period_aggregate",
                "1min",
            )


    def test_rejects_a_date_only_availability_before_the_bar_closes(self) -> None:
        with self.assertRaisesRegex(ValueError, "bar close"):
            _point_kind_points(
                [
                    {
                        "timestamp": "2026-09-21T13:30:00Z",
                        "availableAt": "2026-09-20",
                        "value": 1,
                    }
                ],
                "period_aggregate",
                "1min",
            )

if __name__ == "__main__":
    unittest.main()
