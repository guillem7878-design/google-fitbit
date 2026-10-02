from fitbit_air import normalize as nz


def test_daily_value_resting_hr():
    p = {"name": "users/me/dataTypes/daily-resting-heart-rate/dataPoints/abc",
         "dailyRestingHeartRate": {"date": {"year": 2026, "month": 9, "day": 3}, "beatsPerMinute": "54"}}
    day, path, value = nz.daily_value(p, ("beatsPerMinute",))
    assert (day, path, value) == ("2026-09-03", "beatsPerMinute", 54.0)
    assert nz.point_id(p) == "abc"


def test_pick_value_prefers_keyword_and_skips_dates():
    payload = {"date": {"year": 2026, "month": 1, "day": 1}, "deepSleepRmssd": 30, "averageRmssdMilliseconds": 42.5}
    assert nz.pick_value(payload, ("average",)) == ("averageRmssdMilliseconds", 42.5)
    assert nz.pick_value(payload, ("nothing",))[0] == "deepSleepRmssd"


def test_rollup_value_steps():
    p = {"civilStartTime": {"date": {"year": 2026, "month": 3, "day": 28}}, "steps": {"countSum": "9037"}}
    assert nz.rollup_value(p, ("countSum",)) == ("2026-03-28", "countSum", 9037.0)


def test_clock_minutes():
    from datetime import datetime
    assert nz.clock_minutes(datetime(2026, 1, 1, 23, 30)) == -30
    assert nz.clock_minutes(datetime(2026, 1, 1, 7, 10)) == 430


def _sleep_point(start, end, offset="7200s", nap=False):
    return {"name": "users/me/dataTypes/sleep/dataPoints/s1", "sleep": {
        "interval": {"startTime": start, "endTime": end, "startUtcOffset": offset, "endUtcOffset": offset},
        "metadata": {"nap": nap},
        "summary": {"minutesAsleep": "420", "minutesAwake": "40", "minutesInSleepPeriod": "460",
                    "stagesSummary": [{"type": "DEEP", "minutes": "80"}, {"type": "REM", "minutes": "95"},
                                      {"type": "LIGHT", "minutes": "245"}, {"type": "AWAKE", "minutes": "40"}]},
    }}


def test_sleep_session_local_times_and_stages():
    s = nz.sleep_session(_sleep_point("2026-09-02T21:20:00Z", "2026-09-03T05:00:00Z"))
    assert s["day"] == "2026-09-03"          # 07:00 local
    assert s["bedtime"] == -40               # 23:20 local
    assert s["waketime"] == 420
    assert s["deep_min"] == 80 and s["rem_min"] == 95 and s["awake_min"] == 40
    m = nz.sleep_daily_metrics([s])["2026-09-03"]
    assert m["sleep_min"] == 420
    assert round(m["efficiency"], 1) == 91.3


def test_naps_do_not_replace_main_sleep():
    main = nz.sleep_session(_sleep_point("2026-09-02T21:20:00Z", "2026-09-03T05:00:00Z"))
    nap = nz.sleep_session(_sleep_point("2026-09-03T13:00:00Z", "2026-09-03T13:30:00Z", nap=True))
    m = nz.sleep_daily_metrics([main, nap])["2026-09-03"]
    assert m["sleep_min"] == 420 and m["bedtime"] == -40 and m["nap_min"] == 420


def test_hr_sample():
    p = {"heartRate": {"sampleTime": {"physicalTime": "2026-04-20T08:00:00Z"}, "beatsPerMinute": "72"}}
    ts, bpm = nz.hr_sample(p)
    assert bpm == 72 and ts == 1776672000
