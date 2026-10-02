"""Descarga incremental de datos de la Google Health API a la base local."""

from datetime import date, datetime, timedelta, timezone

from . import normalize as nz
from . import store
from .api import ApiError, HealthClient


def _chunks(start: date, end: date, days: int):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def _start_for(conn, key: str, default_start: date) -> date:
    last = store.get_sync_day(conn, key)
    if not last:
        return default_start
    # Volver 3 días atrás: la pulsera puede sincronizar tarde y la API recalcula.
    return max(default_start, date.fromisoformat(last) - timedelta(days=3))


def _warn(dt: str, err: Exception) -> None:
    print(f"  ! {dt}: {err}")


def sync(conn, client: HealthClient, days: int = 90, intraday_days: int = 14, full: bool = False) -> None:
    today = date.today()
    end = today + timedelta(days=1)
    default_start = today - timedelta(days=days)

    def start_for(key: str) -> date:
        return default_start if full else _start_for(conn, key, default_start)

    # 1) Tipos diarios (FC en reposo, HRV, respiración, SpO2, temperatura, VO2 máx).
    for metric, (dt, prefer, scale) in nz.DAILY_TYPES.items():
        start = start_for(dt)
        flt = f'{dt.replace("-", "_")}.date >= "{start}" AND {dt.replace("-", "_")}.date < "{end}"'
        n = 0
        try:
            for p in client.list_points(dt, flt, 10000):
                res = nz.daily_value(p, prefer)
                if not res:
                    continue
                day, path, value = res
                store.save_raw(conn, dt, nz.point_id(p) or day, day, p)
                store.set_metric(conn, day, metric, value * scale)
                store.note_source(conn, metric, f"{dt}:{path}")
                n += 1
            store.set_sync_day(conn, dt, today.isoformat())
            print(f"  {dt}: {n} días")
        except ApiError as e:
            _warn(dt, e)
        conn.commit()

    # 2) Totales diarios por rollup (pasos, distancia, calorías, minutos en zona).
    for metric, (dt, prefer, scale) in nz.ROLLUP_TYPES.items():
        start = start_for(dt)
        n = 0
        try:
            for a, b in _chunks(start, end, 14):
                for p in client.daily_rollup(dt, a, b):
                    res = nz.rollup_value(p, prefer)
                    if not res:
                        continue
                    day, path, value = res
                    store.set_metric(conn, day, metric, value * scale)
                    store.note_source(conn, metric, f"{dt}:{path}")
                    n += 1
            store.set_sync_day(conn, dt, today.isoformat())
            print(f"  {dt}: {n} días")
        except ApiError as e:
            _warn(dt, e)
        conn.commit()

    # 3) Sueño (sesiones con fases). Solo se puede filtrar por hora de fin.
    start = start_for("sleep")
    flt = (f'sleep.interval.civil_end_time >= "{start}T00:00:00" '
           f'AND sleep.interval.civil_end_time < "{end}T00:00:00"')
    try:
        n = 0
        for p in client.list_points("sleep", flt, 25):
            sess = nz.sleep_session(p)
            if sess:
                store.save_raw(conn, "sleep", nz.point_id(p) or str(sess["start_utc"]), sess["day"], p)
                n += 1
        store.set_sync_day(conn, "sleep", today.isoformat())
        print(f"  sleep: {n} sesiones")
    except ApiError as e:
        _warn("sleep", e)
    conn.commit()

    # 4) Entrenamientos.
    start = start_for("exercise")
    flt = (f'exercise.interval.civil_start_time >= "{start}T00:00:00" '
           f'AND exercise.interval.civil_start_time < "{end}T00:00:00"')
    try:
        n = 0
        for p in client.list_points("exercise", flt, 25):
            iv = nz.type_payload(p).get("interval", {})
            day = nz.local_time(iv["startTime"], iv.get("startUtcOffset")).date().isoformat() if "startTime" in iv else None
            store.save_raw(conn, "exercise", nz.point_id(p), day, p)
            n += 1
        store.set_sync_day(conn, "exercise", today.isoformat())
        print(f"  exercise: {n} sesiones")
    except ApiError as e:
        _warn("exercise", e)
    conn.commit()

    # 5) Frecuencia cardiaca intradía (para la FC durante el sueño).
    if intraday_days > 0:
        hr_start = max(start_for("heart-rate"), today - timedelta(days=intraday_days))
        n = 0
        try:
            for a, b in _chunks(hr_start, end, 1):
                lo = datetime(a.year, a.month, a.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                hi = datetime(b.year, b.month, b.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                flt = f'heart_rate.sample_time.physical_time >= "{lo}" AND heart_rate.sample_time.physical_time < "{hi}"'
                batch = [s for s in (nz.hr_sample(p) for p in client.list_points("heart-rate", flt, 10000)) if s]
                store.save_hr(conn, batch)
                n += len(batch)
                conn.commit()
            store.set_sync_day(conn, "heart-rate", today.isoformat())
            print(f"  heart-rate: {n} muestras")
        except ApiError as e:
            _warn("heart-rate", e)

    rebuild_derived(conn)
    conn.commit()


def rebuild_derived(conn) -> None:
    """Recalcula las métricas que salen de los datos crudos (sueño y FC nocturna)."""
    sessions = [s for s in (nz.sleep_session(p) for p in store.raw_points(conn, "sleep")) if s]
    for day, metrics in nz.sleep_daily_metrics(sessions).items():
        for k, v in metrics.items():
            store.set_metric(conn, day, k, v)

    rows = conn.execute(
        "SELECT a.day, a.value, b.value FROM daily a JOIN daily b ON a.day = b.day "
        "WHERE a.metric = 'sleep_start_utc' AND b.metric = 'sleep_end_utc'"
    ).fetchall()
    for day, start, end in rows:
        bpm = [b for (b,) in conn.execute(
            "SELECT bpm FROM hr_samples WHERE ts >= ? AND ts < ? ORDER BY ts", (int(start), int(end)))]
        if len(bpm) < 30:
            continue
        store.set_metric(conn, day, "sleep_hr_avg", sum(bpm) / len(bpm))
        win = max(1, len(bpm) // 48)  # ~10 min con muestreo de unos 5 s en 8 h
        lows = [sum(bpm[i:i + win]) / win for i in range(0, len(bpm) - win + 1, win)]
        store.set_metric(conn, day, "sleep_hr_min", min(lows))
