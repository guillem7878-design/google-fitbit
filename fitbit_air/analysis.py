"""Análisis: líneas base personales, puntuaciones, alertas y correlaciones.

Todo se compara contra TU propia historia (ventana móvil de 28 días previos),
no contra medias de población. Son heurísticas de seguimiento, no diagnósticos.
"""

import math
import statistics as st
from dataclasses import dataclass, field
from datetime import date, timedelta

BASELINE_DAYS = 28
MIN_BASELINE = 7


def _prev_values(table: dict, day: str, metric: str, n: int = BASELINE_DAYS) -> list[float]:
    d = date.fromisoformat(day)
    vals = []
    for i in range(1, n + 1):
        v = table.get((d - timedelta(days=i)).isoformat(), {}).get(metric)
        if v is not None:
            vals.append(v)
    return vals


def baseline(table: dict, day: str, metric: str) -> tuple[float, float] | None:
    vals = _prev_values(table, day, metric)
    if len(vals) < MIN_BASELINE:
        return None
    sd = st.stdev(vals) if len(vals) > 1 else 0.0
    return st.mean(vals), sd


def zscore(table: dict, day: str, metric: str, min_sd: float = 1e-6) -> float | None:
    v = table.get(day, {}).get(metric)
    b = baseline(table, day, metric)
    if v is None or b is None:
        return None
    mean, sd = b
    return (v - mean) / max(sd, min_sd)


def _clip(x: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, x))


# Desviación mínima para no disparar z enormes con métricas muy estables.
MIN_SD = {"rhr": 1.0, "hrv": 2.0, "resp_rate": 0.3, "sleep_hr_avg": 1.0}


def recovery_score(table: dict, day: str, sleep_target: float = 450) -> tuple[float, dict] | None:
    """0-100. HRV (40 %), FC en reposo (30 %), sueño (20 %), respiración (10 %)."""
    parts, weights = {}, {}
    z = zscore(table, day, "hrv", MIN_SD["hrv"])
    if z is not None:
        parts["hrv"], weights["hrv"] = z, 0.4
    z = zscore(table, day, "rhr", MIN_SD["rhr"])
    if z is not None:
        parts["rhr"], weights["rhr"] = -z, 0.3
    sleep = table.get(day, {}).get("sleep_min")
    if sleep is not None:
        parts["sleep"], weights["sleep"] = _clip((sleep - sleep_target) / 60, -3, 1.5), 0.2
    z = zscore(table, day, "resp_rate", MIN_SD["resp_rate"])
    if z is not None:
        parts["resp_rate"], weights["resp_rate"] = -z, 0.1
    if "hrv" not in parts and "rhr" not in parts:
        return None
    total_w = sum(weights.values())
    combined = sum(parts[k] * weights[k] for k in parts) / total_w
    return round(_clip(60 + 18 * combined)), parts


def sleep_score(table: dict, day: str, target: float = 450) -> float | None:
    """0-100: duración 40 %, eficiencia 20 %, fases profundo+REM 20 %, regularidad 20 %."""
    m = table.get(day, {})
    if "sleep_min" not in m:
        return None
    dur = _clip(100 * m["sleep_min"] / target)
    eff = _clip((m.get("efficiency", 85) - 70) * 100 / 25)
    restorative = None
    if "deep_min" in m and "rem_min" in m and m["sleep_min"] > 0:
        frac = (m["deep_min"] + m["rem_min"]) / m["sleep_min"]
        restorative = _clip(100 * frac / 0.40)  # ~40 % profundo+REM ya es excelente
    beds = _prev_values(table, day, "bedtime", 14)
    regular = None
    if len(beds) >= 5 and "bedtime" in m:
        regular = _clip(100 - abs(m["bedtime"] - st.median(beds)) * 100 / 90)  # 90 min -> 0
    score, w = 0.4 * dur + 0.2 * eff, 0.6
    if restorative is not None:
        score, w = score + 0.2 * restorative, w + 0.2
    if regular is not None:
        score, w = score + 0.2 * regular, w + 0.2
    return round(score / w)


@dataclass
class Alert:
    level: str  # "alta" | "media" | "info"
    title: str
    detail: str


def illness_signals(table: dict, day: str) -> list[str]:
    """Señales típicas de que el cuerpo está luchando contra algo (estrés, virus, sobrecarga)."""
    m = table.get(day, {})
    sig = []
    z = zscore(table, day, "rhr", MIN_SD["rhr"])
    b = baseline(table, day, "rhr")
    if z is not None and b and z >= 1.5 and m["rhr"] - b[0] >= 3:
        sig.append(f"FC en reposo {m['rhr']:.0f} lpm (+{m['rhr'] - b[0]:.0f} sobre tu media)")
    z = zscore(table, day, "hrv", MIN_SD["hrv"])
    if z is not None and z <= -1.5:
        sig.append(f"HRV {m['hrv']:.0f} ms ({z:+.1f} σ)")
    if m.get("skin_temp_dev") is not None and m["skin_temp_dev"] >= 0.5:
        sig.append(f"temperatura nocturna +{m['skin_temp_dev']:.1f} °C")
    z = zscore(table, day, "resp_rate", MIN_SD["resp_rate"])
    if z is not None and z >= 1.5:
        sig.append(f"respiración {m['resp_rate']:.1f} rpm ({z:+.1f} σ)")
    z = zscore(table, day, "sleep_hr_avg", MIN_SD["sleep_hr_avg"])
    if z is not None and z >= 1.5:
        sig.append(f"FC media durmiendo {m['sleep_hr_avg']:.0f} lpm ({z:+.1f} σ)")
    return sig


def training_load(table: dict, day: str, metric: str = "azm") -> dict | None:
    """Carga aguda (7 d) frente a crónica (28 d). Ratio >1,5 = riesgo de sobrecarga."""
    d = date.fromisoformat(day)
    vals = [table.get((d - timedelta(days=i)).isoformat(), {}).get(metric) for i in range(28)]
    acute = [v for v in vals[:7] if v is not None]
    chronic = [v for v in vals if v is not None]
    if len(acute) < 4 or len(chronic) < 14:
        return None
    a, c = st.mean(acute), st.mean(chronic)
    return {"acute": a, "chronic": c, "ratio": a / c if c else None}


def sleep_debt(table: dict, day: str, target: float = 450, days: int = 14) -> float | None:
    d = date.fromisoformat(day)
    vals = [table.get((d - timedelta(days=i)).isoformat(), {}).get("sleep_min") for i in range(days)]
    vals = [v for v in vals if v is not None]
    if len(vals) < days // 2:
        return None
    return sum(target - v for v in vals) / len(vals) * days  # extrapolado a la ventana


def social_jetlag(table: dict, day: str, days: int = 28) -> float | None:
    """Diferencia (min) del punto medio del sueño entre fin de semana y laborables."""
    d = date.fromisoformat(day)
    wk, we = [], []
    for i in range(days):
        dd = d - timedelta(days=i)
        mid = table.get(dd.isoformat(), {}).get("midsleep")
        if mid is None:
            continue
        (we if dd.weekday() >= 5 else wk).append(mid)  # noches que terminan en sáb/dom
    if len(wk) < 5 or len(we) < 2:
        return None
    return st.mean(we) - st.mean(wk)


def alerts(table: dict, day: str, sleep_target: float = 450) -> list[Alert]:
    out: list[Alert] = []
    sig = illness_signals(table, day)
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    sig_prev = illness_signals(table, yesterday) if yesterday in table else []
    if len(sig) >= 2:
        level = "alta" if len(sig) >= 3 or sig_prev else "media"
        out.append(Alert(level, "Tu cuerpo muestra señales de estrés fisiológico",
                         "; ".join(sig) + ". Prioriza descanso, hidratación y baja la intensidad. "
                         "Si se mantiene o aparecen síntomas, consulta a un profesional."))
    elif len(sig) == 1:
        out.append(Alert("info", "Una métrica fuera de tu rango habitual", sig[0]))

    tl = training_load(table, day)
    if tl and tl["ratio"]:
        if tl["ratio"] > 1.5:
            out.append(Alert("media", "Subida brusca de carga",
                             f"Últimos 7 días: {tl['acute']:.0f} min en zona/día frente a {tl['chronic']:.0f} "
                             f"de media (ratio {tl['ratio']:.2f}). Riesgo de sobrecarga o lesión."))
        elif tl["ratio"] < 0.6:
            out.append(Alert("info", "Carga de entreno a la baja",
                             f"Ratio agudo/crónico {tl['ratio']:.2f}: estás perdiendo estímulo."))

    debt = sleep_debt(table, day, sleep_target)
    if debt is not None and debt > 300:
        out.append(Alert("media", "Deuda de sueño acumulada",
                         f"Unas {debt / 60:.1f} h por debajo de tu objetivo en 14 días."))

    sj = social_jetlag(table, day)
    if sj is not None and abs(sj) >= 60:
        out.append(Alert("info", "Jet lag social",
                         f"El fin de semana tu sueño se desplaza {abs(sj):.0f} min. Afecta al lunes."))

    spo2 = table.get(day, {}).get("spo2")
    if spo2 is not None and spo2 < 92:
        out.append(Alert("media", "SpO2 nocturna baja",
                         f"Media de {spo2:.1f} %. Si se repite, coméntalo con tu médico "
                         "(puede indicar problemas respiratorios durante el sueño)."))
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 10:
        return None
    mx, my = st.mean(xs), st.mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# Qué hace hoy (X) que afecte a la noche/mañana siguiente (Y).
DRIVERS = ["steps", "azm", "active_minutes", "calories", "bedtime", "nap_min", "sleep_min"]
OUTCOMES = ["hrv", "rhr", "deep_min", "rem_min", "efficiency", "sleep_min", "sleep_hr_avg"]


def lagged_correlations(table: dict, lag: int = 1) -> list[dict]:
    res = []
    for x in DRIVERS:
        for y in OUTCOMES:
            if x == y:
                continue
            xs, ys = [], []
            for day, m in table.items():
                nxt = (date.fromisoformat(day) + timedelta(days=lag)).isoformat()
                if x in m and y in table.get(nxt, {}):
                    xs.append(m[x])
                    ys.append(table[nxt][y])
            r = pearson(xs, ys)
            if r is not None and abs(r) >= 0.25:
                res.append({"x": x, "y": y, "r": r, "n": len(xs)})
    return sorted(res, key=lambda d: -abs(d["r"]))


@dataclass
class TagEffect:
    tag: str
    metric: str
    with_tag: float
    without: float
    n_with: int
    n_without: int
    diff_pct: float = field(init=False)

    def __post_init__(self):
        self.diff_pct = 100 * (self.with_tag - self.without) / self.without if self.without else 0.0


def tag_effects(table: dict, journal: dict, min_n: int = 3) -> list[TagEffect]:
    """Efecto de lo que anotas un día (alcohol, cafeína tarde, cena tarde...) en la noche siguiente."""
    tags = sorted({t for tags in journal.values() for t in tags})
    if not tags:
        return []
    out = []
    days = list(table)
    for tag in tags:
        for metric in ("hrv", "rhr", "deep_min", "rem_min", "sleep_min", "efficiency", "sleep_hr_avg"):
            yes, no = [], []
            for day in days:
                nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                v = table.get(nxt, {}).get(metric)
                if v is None:
                    continue
                (yes if journal.get(day, {}).get(tag) else no).append(v)
            if len(yes) >= min_n and len(no) >= min_n:
                out.append(TagEffect(tag, metric, st.mean(yes), st.mean(no), len(yes), len(no)))
    return sorted(out, key=lambda e: -abs(e.diff_pct))


def trend(table: dict, day: str, metric: str) -> dict | None:
    """Media de los últimos 7 días frente a los 28 anteriores."""
    d = date.fromisoformat(day)
    recent = [table.get((d - timedelta(days=i)).isoformat(), {}).get(metric) for i in range(7)]
    prior = [table.get((d - timedelta(days=i)).isoformat(), {}).get(metric) for i in range(7, 35)]
    recent = [v for v in recent if v is not None]
    prior = [v for v in prior if v is not None]
    if len(recent) < 3 or len(prior) < 7:
        return None
    a, b = st.mean(recent), st.mean(prior)
    return {"recent": a, "prior": b, "delta": a - b, "pct": 100 * (a - b) / b if b else 0.0}


def enrich(table: dict, sleep_target: float = 450) -> dict:
    """Añade al día sus puntuaciones calculadas (recovery, sleep_score)."""
    for day in table:
        rec = recovery_score(table, day, sleep_target)
        if rec:
            table[day]["recovery"] = rec[0]
        ss = sleep_score(table, day, sleep_target)
        if ss is not None:
            table[day]["sleep_score"] = ss
    return table


def latest_day(table: dict) -> str | None:
    for day in reversed(list(table)):
        if any(k in table[day] for k in ("rhr", "hrv", "sleep_min")):
            return day
    return None
