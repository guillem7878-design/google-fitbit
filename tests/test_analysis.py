from datetime import date, timedelta

from fitbit_air import analysis as an


def _table(n=40, **overrides_last):
    start = date(2026, 8, 1)
    t = {}
    for i in range(n):
        d = (start + timedelta(days=i)).isoformat()
        t[d] = {"rhr": 55 + (i % 3) - 1, "hrv": 45 + (i % 5) - 2, "resp_rate": 14.5 + (i % 2) * 0.2,
                "sleep_min": 450, "efficiency": 90, "deep_min": 80, "rem_min": 100,
                "bedtime": -30 + (i % 3) * 10, "azm": 40, "midsleep": 200}
    last = (start + timedelta(days=n - 1)).isoformat()
    t[last].update(overrides_last)
    return t, last


def test_recovery_normal_day_is_mid_range():
    t, day = _table()
    score, parts = an.recovery_score(t, day)
    assert 45 <= score <= 75
    assert set(parts) == {"hrv", "rhr", "sleep", "resp_rate"}


def test_recovery_drops_with_bad_signals():
    good, day = _table(hrv=52, rhr=52)
    bad, _ = _table(hrv=30, rhr=63, sleep_min=300)
    assert an.recovery_score(good, day)[0] > an.recovery_score(bad, day)[0] + 30


def test_illness_alert_needs_two_signals():
    t, day = _table(rhr=62, hrv=33, skin_temp_dev=0.8)
    alerts = an.alerts(t, day)
    assert alerts and alerts[0].level in ("media", "alta")
    t, day = _table()
    assert not [a for a in an.alerts(t, day) if a.level in ("media", "alta")]


def test_training_load_spike():
    t, day = _table()
    d = date.fromisoformat(day)
    for i in range(7):
        t[(d - timedelta(days=i)).isoformat()]["azm"] = 120
    tl = an.training_load(t, day)
    assert tl["ratio"] > 1.5
    assert any("carga" in a.title.lower() for a in an.alerts(t, day))


def test_tag_effects_detects_alcohol():
    t, _ = _table(60)
    journal = {}
    for i, d in enumerate(list(t)[:-1]):
        if i % 4 == 0:
            journal[d] = {"alcohol": 1}
            nxt = list(t)[i + 1]
            t[nxt]["hrv"] -= 10
    effects = an.tag_effects(t, journal)
    hrv = next(e for e in effects if e.metric == "hrv")
    assert hrv.diff_pct < -15


def test_pearson():
    assert round(an.pearson(list(range(12)), [2 * x + 1 for x in range(12)]), 6) == 1
    assert an.pearson([1, 2, 3], [1, 2, 3]) is None
