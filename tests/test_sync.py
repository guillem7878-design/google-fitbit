from datetime import date, timedelta

from fitbit_air import store
from fitbit_air.api import ApiError
from fitbit_air.sync import sync


class FakeClient:
    def __init__(self):
        self.filters = {}

    def list_points(self, data_type, filter_expr, page_size):
        self.filters[data_type] = filter_expr
        d = date.today() - timedelta(days=1)
        civil = {"year": d.year, "month": d.month, "day": d.day}
        if data_type == "daily-resting-heart-rate":
            return [{"name": "x/1", "dailyRestingHeartRate": {"date": civil, "beatsPerMinute": "53"}}]
        if data_type == "daily-heart-rate-variability":
            return [{"name": "x/2", "dailyHeartRateVariability": {"date": civil, "averageHeartRateVariabilityMilliseconds": 47.1}}]
        if data_type == "daily-vo2-max":
            raise ApiError(403, "no permitido")
        if data_type == "sleep":
            return [{"name": "x/3", "sleep": {
                "interval": {"startTime": f"{d - timedelta(days=1)}T22:30:00Z", "endTime": f"{d}T06:30:00Z"},
                "summary": {"minutesAsleep": "440", "minutesInSleepPeriod": "480"}}}]
        if data_type == "heart-rate":
            base = f"{d - timedelta(days=1)}T23:"
            return [{"heartRate": {"sampleTime": {"physicalTime": f"{base}{m:02d}:00Z"}, "beatsPerMinute": str(50 + m % 3)}}
                    for m in range(60)]
        return []

    def daily_rollup(self, data_type, start, end):
        if data_type == "steps" and start <= date.today() - timedelta(days=1) < end:
            d = date.today() - timedelta(days=1)
            return [{"civilStartTime": {"date": {"year": d.year, "month": d.month, "day": d.day}},
                     "steps": {"countSum": "10234"}}]
        return []


def test_sync_end_to_end(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    client = FakeClient()
    sync(conn, client, days=10, intraday_days=3)
    day = (date.today() - timedelta(days=1)).isoformat()
    t = store.daily_table(conn)[day]
    assert t["rhr"] == 53 and t["hrv"] == 47.1 and t["steps"] == 10234
    assert t["sleep_min"] == 440
    assert 50 <= t["sleep_hr_avg"] <= 52
    assert 'daily_resting_heart_rate.date >= "' in client.filters["daily-resting-heart-rate"]
    assert client.filters["sleep"].startswith("sleep.interval.civil_end_time >=")
    assert client.filters["heart-rate"].startswith("heart_rate.sample_time.physical_time >=")
