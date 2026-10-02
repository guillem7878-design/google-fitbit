"""Datos sintéticos realistas para probar el panel sin conectar la pulsera."""

import random
from datetime import date, timedelta

from . import store


def generate(conn, days: int = 120, seed: int = 7) -> None:
    rnd = random.Random(seed)
    today = date.today()
    sick_start = days - 25  # una semana "enfermo" para ver las alertas
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        day = d.isoformat()
        weekend = d.weekday() >= 5
        sick = sick_start <= i < sick_start + 4
        fitness = i / days  # mejora progresiva
        drink = rnd.random() < (0.35 if d.weekday() in (4, 5) else 0.08)
        late_coffee = rnd.random() < 0.2
        prev_drink = conn.execute("SELECT 1 FROM journal WHERE day = ? AND tag = 'alcohol'",
                                  ((d - timedelta(days=1)).isoformat(),)).fetchone()
        prev_coffee = conn.execute("SELECT 1 FROM journal WHERE day = ? AND tag = 'cafe_tarde'",
                                   ((d - timedelta(days=1)).isoformat(),)).fetchone()

        rhr = 58 - 3 * fitness + rnd.gauss(0, 1.2) + (6 if sick else 0) + (2.5 if prev_drink else 0)
        hrv = 42 + 8 * fitness + rnd.gauss(0, 4) - (14 if sick else 0) - (8 if prev_drink else 0)
        bed = (-40 if not weekend else 30) + rnd.gauss(0, 25) + (25 if prev_coffee else 0)
        asleep = 430 + rnd.gauss(0, 35) - (40 if prev_coffee else 0) + (35 if weekend else 0)
        deep = asleep * (0.17 + rnd.gauss(0, 0.02) - (0.04 if prev_drink else 0))
        rem = asleep * (0.22 + rnd.gauss(0, 0.025) - (0.05 if prev_drink else 0))
        awake = 35 + rnd.gauss(0, 10) + (15 if prev_drink else 0)
        in_bed = asleep + awake
        wake = bed + in_bed
        azm = max(0, 25 + 20 * fitness + rnd.gauss(0, 15) + (30 if d.weekday() in (1, 3, 6) else 0) - (25 if sick else 0))
        steps = max(1500, 8000 + 2500 * fitness + rnd.gauss(0, 2500) - (4000 if sick else 0))

        metrics = {
            "rhr": rhr, "hrv": hrv,
            "resp_rate": 14.5 + rnd.gauss(0, 0.3) + (1.6 if sick else 0),
            "spo2": 96 + rnd.gauss(0, 0.8),
            "skin_temp_dev": rnd.gauss(0, 0.15) + (0.8 if sick else 0),
            "steps": round(steps), "distance_km": steps * 0.00075,
            "calories": 2100 + steps * 0.04 + azm * 8,
            "azm": round(azm), "active_minutes": round(azm * 1.4),
            "sleep_min": asleep, "in_bed_min": in_bed, "awake_min": awake,
            "deep_min": deep, "rem_min": rem, "light_min": asleep - deep - rem,
            "efficiency": 100 * asleep / in_bed, "latency_min": max(2, 12 + rnd.gauss(0, 6)),
            "bedtime": bed, "waketime": wake, "midsleep": (bed + wake) / 2,
            "sleep_hr_avg": rhr + 3 + rnd.gauss(0, 1), "sleep_hr_min": rhr - 4 + rnd.gauss(0, 1),
            "vo2max": 41 + 4 * fitness + rnd.gauss(0, 0.3),
        }
        for k, v in metrics.items():
            store.set_metric(conn, day, k, v)
        if drink:
            conn.execute("INSERT OR REPLACE INTO journal VALUES (?, 'alcohol', 1, NULL)", (day,))
        if late_coffee:
            conn.execute("INSERT OR REPLACE INTO journal VALUES (?, 'cafe_tarde', 1, NULL)", (day,))
        if rnd.random() < 0.25:
            conn.execute("INSERT OR REPLACE INTO journal VALUES (?, 'cena_tarde', 1, NULL)", (day,))
    conn.commit()
