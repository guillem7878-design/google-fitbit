"""Convierte las respuestas de la API en métricas diarias.

La API devuelve cada punto como {"dataSource": ..., "name": ..., "<tipo>": {...}}.
Para los tipos cuyo nombre exacto de campo no está documentado públicamente,
el valor se elige por palabras clave y la ruta usada se guarda en
`metric_sources` para poder revisarla con `fitbit-air inspect`.
"""

from datetime import datetime, timedelta, timezone

_SKIP_KEYS = {"dataSource", "name", "civilStartTime", "civilEndTime"}
_TIME_KEYS = {"date", "interval", "sampleTime", "createTime", "updateTime", "metadata",
              "year", "month", "day", "utcOffset", "startUtcOffset", "endUtcOffset"}


def type_payload(point: dict) -> dict:
    for k, v in point.items():
        if k not in _SKIP_KEYS and isinstance(v, dict):
            return v
    return {}


def point_id(point: dict) -> str:
    return point.get("name", "").rsplit("/", 1)[-1]


def civil_day(d: dict) -> str:
    return f"{int(d['year']):04d}-{int(d['month']):02d}-{int(d['day']):02d}"


def _numeric_leaves(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _TIME_KEYS:
                continue
            yield from _numeric_leaves(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        yield prefix, float(obj)
    elif isinstance(obj, str):
        try:
            yield prefix, float(obj)
        except ValueError:
            return


def pick_value(payload: dict, prefer: tuple[str, ...] = ()) -> tuple[str, float] | None:
    """Devuelve (ruta, valor) del primer campo numérico que encaje con `prefer`."""
    leaves = list(_numeric_leaves(payload))
    if not leaves:
        return None
    for word in prefer:
        for path, value in leaves:
            if word.lower() in path.lower():
                return path, value
    return leaves[0]


# métrica -> (tipo de la API, palabras clave preferidas, factor de escala)
DAILY_TYPES = {
    "rhr": ("daily-resting-heart-rate", ("beatsPerMinute",), 1.0),
    "hrv": ("daily-heart-rate-variability", ("rmssd", "average", "milli"), 1.0),
    "resp_rate": ("daily-respiratory-rate", ("breath", "average", "rate"), 1.0),
    "spo2": ("daily-oxygen-saturation", ("average", "percentage"), 1.0),
    "skin_temp_dev": ("daily-sleep-temperature-derivations", ("relative", "deviation", "nightly", "celsius"), 1.0),
    "vo2max": ("daily-vo2-max", ("vo2", "max", "ml"), 1.0),
}

ROLLUP_TYPES = {
    "steps": ("steps", ("countSum",), 1.0),
    "distance_km": ("distance", ("millimetersSum",), 1e-6),
    "calories": ("total-calories", ("kcalSum", "kcal"), 1.0),
    "azm": ("active-zone-minutes", ("sum", "minutes"), 1.0),
    "active_minutes": ("active-minutes", ("sum", "minutes"), 1.0),
}


def daily_value(point: dict, prefer) -> tuple[str, str, float] | None:
    """(día, ruta, valor) de un punto de un tipo diario (daily-*)."""
    payload = type_payload(point)
    if "date" not in payload:
        return None
    picked = pick_value(payload, prefer)
    if not picked:
        return None
    return civil_day(payload["date"]), picked[0], picked[1]


def rollup_value(point: dict, prefer) -> tuple[str, str, float] | None:
    """(día, ruta, valor) de un punto de dailyRollUp."""
    cst = point.get("civilStartTime", {}).get("date")
    if not cst:
        return None
    picked = pick_value(type_payload(point), prefer)
    if not picked:
        return None
    return civil_day(cst), picked[0], picked[1]


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _offset(s: str | None) -> timedelta:
    try:
        return timedelta(seconds=int(str(s or "0s").rstrip("s")))
    except ValueError:
        return timedelta(0)


def local_time(ts: str, offset: str | None) -> datetime:
    """Hora local (naive) a partir del instante UTC y el desfase que da la API."""
    return (parse_ts(ts) + _offset(offset)).replace(tzinfo=None)


def clock_minutes(dt: datetime) -> float:
    """Minutos respecto a medianoche: 23:30 -> -30, 00:45 -> 45, 07:10 -> 430."""
    m = dt.hour * 60 + dt.minute + dt.second / 60
    return m - 1440 if m >= 12 * 60 else m


def _stage_kind(name: str) -> str | None:
    n = (name or "").upper()
    for kind in ("DEEP", "REM", "LIGHT"):
        if kind in n:
            return kind.lower()
    if "AWAKE" in n or "WAKE" in n:
        return "awake"
    return None


def sleep_session(point: dict) -> dict | None:
    """Resumen de una sesión de sueño. El día asignado es el de despertarse."""
    s = point.get("sleep") or type_payload(point)
    iv = s.get("interval", {})
    if "startTime" not in iv or "endTime" not in iv:
        return None
    start = local_time(iv["startTime"], iv.get("startUtcOffset"))
    end = local_time(iv["endTime"], iv.get("endUtcOffset"))
    summary = s.get("summary", {})
    out = {
        "day": end.date().isoformat(),
        "is_nap": bool(s.get("metadata", {}).get("nap", False)),
        "start_utc": parse_ts(iv["startTime"]).timestamp(),
        "end_utc": parse_ts(iv["endTime"]).timestamp(),
        "bedtime": clock_minutes(start),
        "waketime": clock_minutes(end),
        "in_bed": float(summary.get("minutesInSleepPeriod") or (end - start).total_seconds() / 60),
    }
    if "minutesAsleep" in summary:
        out["asleep"] = float(summary["minutesAsleep"])
    if "minutesAwake" in summary:
        out["awake"] = float(summary["minutesAwake"])
    if "minutesToFallAsleep" in summary:
        out["latency"] = float(summary["minutesToFallAsleep"])
    stages = {}
    for st in summary.get("stagesSummary", []) or []:
        kind = _stage_kind(st.get("type", ""))
        if kind:
            stages[kind] = stages.get(kind, 0.0) + float(st.get("minutes", 0) or 0)
    if not stages:
        for st in s.get("stages", []) or []:
            kind = _stage_kind(st.get("type", ""))
            if kind and "startTime" in st and "endTime" in st:
                mins = (parse_ts(st["endTime"]) - parse_ts(st["startTime"])).total_seconds() / 60
                stages[kind] = stages.get(kind, 0.0) + mins
    out.update({f"{k}_min": v for k, v in stages.items()})
    if "asleep" not in out and stages:
        out["asleep"] = sum(v for k, v in stages.items() if k != "awake")
    return out


def sleep_daily_metrics(sessions: list[dict]) -> dict[str, dict[str, float]]:
    """Agrupa sesiones por día: la principal manda en horarios, las cantidades se suman."""
    by_day: dict[str, list[dict]] = {}
    for s in sessions:
        by_day.setdefault(s["day"], []).append(s)
    out = {}
    for day, items in by_day.items():
        main_items = [s for s in items if not s["is_nap"]] or items
        main = max(main_items, key=lambda s: s.get("asleep", s["in_bed"]))
        m = {
            "sleep_min": sum(s.get("asleep", 0) for s in main_items),
            "in_bed_min": sum(s["in_bed"] for s in main_items),
            "bedtime": main["bedtime"],
            "waketime": main["waketime"],
            "midsleep": (main["bedtime"] + main["waketime"]) / 2,
            "sleep_start_utc": main["start_utc"],
            "sleep_end_utc": main["end_utc"],
        }
        for key in ("deep_min", "rem_min", "light_min", "awake_min"):
            if any(key in s for s in main_items):
                m[key] = sum(s.get(key, 0) for s in main_items)
        if "latency" in main:
            m["latency_min"] = main["latency"]
        if m["in_bed_min"] > 0:
            m["efficiency"] = 100 * m["sleep_min"] / m["in_bed_min"]
        naps = [s for s in items if s["is_nap"] and s is not main]
        if naps:
            m["nap_min"] = sum(s.get("asleep", s["in_bed"]) for s in naps)
        out[day] = m
    return out


def hr_sample(point: dict) -> tuple[int, int] | None:
    p = type_payload(point)
    st = p.get("sampleTime", {})
    if "physicalTime" not in st or "beatsPerMinute" not in p:
        return None
    return int(parse_ts(st["physicalTime"]).timestamp()), int(float(p["beatsPerMinute"]))


def utc_day_bounds(day: str) -> tuple[datetime, datetime]:
    d = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return d, d + timedelta(days=1)
