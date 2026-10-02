"""Genera el panel HTML (un único archivo) y el resumen de texto."""

import html
import json
from datetime import date, timedelta
from pathlib import Path

from . import analysis as an

LABELS = {
    "hrv": "HRV (ms)", "rhr": "FC en reposo (lpm)", "sleep_min": "Sueño (min)",
    "deep_min": "Sueño profundo (min)", "rem_min": "REM (min)", "light_min": "Sueño ligero (min)",
    "efficiency": "Eficiencia del sueño (%)", "steps": "Pasos", "azm": "Minutos en zona activa",
    "active_minutes": "Minutos activos", "calories": "Calorías", "resp_rate": "Respiración (rpm)",
    "spo2": "SpO2 (%)", "skin_temp_dev": "Variación de temperatura (°C)", "vo2max": "VO2 máx",
    "sleep_hr_avg": "FC media durmiendo (lpm)", "bedtime": "Hora de acostarse", "nap_min": "Siestas (min)",
    "recovery": "Recuperación", "sleep_score": "Puntuación de sueño", "distance_km": "Distancia (km)",
}

# Para la tabla de tendencias: True si subir es bueno.
TREND_METRICS = [("hrv", True), ("rhr", False), ("sleep_min", True), ("deep_min", True),
                 ("rem_min", True), ("efficiency", True), ("sleep_hr_avg", False), ("resp_rate", False),
                 ("steps", True), ("azm", True), ("vo2max", True)]


def fmt_clock(minutes: float | None) -> str:
    if minutes is None:
        return "–"
    m = round(minutes) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def fmt_hm(minutes: float | None) -> str:
    if minutes is None:
        return "–"
    m = round(minutes)
    return f"{m // 60} h {m % 60:02d} min"


def build_summary(table: dict, journal: dict, sleep_target: float = 450) -> dict:
    an.enrich(table, sleep_target)
    day = an.latest_day(table)
    if not day:
        return {"day": None}
    m = table[day]
    rec = an.recovery_score(table, day, sleep_target)
    base = {k: an.baseline(table, day, k) for k in ("hrv", "rhr", "sleep_hr_avg", "resp_rate")}
    trends = []
    for metric, up_good in TREND_METRICS:
        t = an.trend(table, day, metric)
        if t:
            good = (t["delta"] > 0) == up_good
            trends.append({"metric": metric, "label": LABELS.get(metric, metric), **t,
                           "good": good if abs(t["pct"]) >= 3 else None})
    return {
        "day": day,
        "today": m,
        "recovery": rec[0] if rec else None,
        "recovery_parts": rec[1] if rec else {},
        "sleep_score": m.get("sleep_score"),
        "baselines": {k: v[0] for k, v in base.items() if v},
        "alerts": [a.__dict__ for a in an.alerts(table, day, sleep_target)],
        "trends": trends,
        "load": an.training_load(table, day),
        "debt": an.sleep_debt(table, day, sleep_target),
        "jetlag": an.social_jetlag(table, day),
        "correlations": an.lagged_correlations(table)[:8],
        "tags": [e.__dict__ for e in an.tag_effects(table, journal)[:12]],
    }


def text_summary(summary: dict) -> str:
    if not summary.get("day"):
        return "Todavía no hay datos. Ejecuta `fitbit-air sync` (o `fitbit-air demo`)."
    m = summary["today"]
    b = summary["baselines"]
    lines = [f"Fitbit Air · {summary['day']}"]
    if summary["recovery"] is not None:
        lines.append(f"Recuperación: {summary['recovery']}/100 ({recovery_label(summary['recovery'])})")
    if summary["sleep_score"] is not None:
        lines.append(f"Sueño: {fmt_hm(m.get('sleep_min'))} · puntuación {summary['sleep_score']}/100")
    if "hrv" in m:
        lines.append(f"HRV: {m['hrv']:.0f} ms" + (f" (tu media {b['hrv']:.0f})" if "hrv" in b else ""))
    if "rhr" in m:
        lines.append(f"FC reposo: {m['rhr']:.0f} lpm" + (f" (tu media {b['rhr']:.0f})" if "rhr" in b else ""))
    for a in summary["alerts"]:
        lines.append(f"[{a['level'].upper()}] {a['title']}: {a['detail']}")
    return "\n".join(lines)


def recovery_label(score: float) -> str:
    if score >= 70:
        return "listo para apretar"
    if score >= 45:
        return "normal, entreno moderado"
    return "prioriza recuperar"


def _series(table: dict, keys: list[str]) -> dict:
    days = list(table)
    return {"days": days, **{k: [table[d].get(k) for d in days] for k in keys}}


def _bands(table: dict, metric: str) -> dict:
    lo, hi = [], []
    for d in table:
        b = an.baseline(table, d, metric)
        lo.append(round(b[0] - b[1], 2) if b else None)
        hi.append(round(b[0] + b[1], 2) if b else None)
    return {"lo": lo, "hi": hi}


def render_html(table: dict, journal: dict, out: Path, sleep_target: float = 450) -> Path:
    summary = build_summary(table, journal, sleep_target)
    keys = ["recovery", "sleep_score", "hrv", "rhr", "sleep_min", "deep_min", "rem_min", "light_min",
            "awake_min", "bedtime", "waketime", "steps", "azm", "resp_rate", "skin_temp_dev", "spo2",
            "sleep_hr_avg", "vo2max", "efficiency"]
    data = _series(table, keys)
    data["hrv_band"] = _bands(table, "hrv")
    data["rhr_band"] = _bands(table, "rhr")
    payload = {"summary": summary, "series": data, "labels": LABELS, "sleep_target": sleep_target}
    page = TEMPLATE.replace("__DATA__", json.dumps(payload, default=float).replace("</", "<\\/"))
    page = page.replace("__DAY__", html.escape(summary.get("day") or ""))
    out.write_text(page, encoding="utf-8")
    return out


def last_days(table: dict, n: int) -> dict:
    cutoff = (date.today() - timedelta(days=n)).isoformat()
    return {d: v for d, v in table.items() if d >= cutoff}


TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fitbit Air Lab</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s7: #4a3aa7;
  --band: rgba(42,120,214,0.12);
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b; --good-text: #006300;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s7: #9085e9;
    --band: rgba(57,135,229,0.18); --good-text: #0ca30c;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s7: #9085e9;
  --band: rgba(57,135,229,0.18); --good-text: #0ca30c;
}
* { box-sizing: border-box; }
body { margin: 0; overflow-x: hidden; background: var(--page); color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 64px; }
header { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; justify-content: space-between; }
h1 { font-size: 22px; margin: 0; }
h2 { font-size: 16px; margin: 0 0 4px; }
.sub { color: var(--ink-2); font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(150px, 100%), 1fr)); gap: 12px; margin: 20px 0; }
.tile, .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.tile .k { color: var(--ink-2); font-size: 13px; }
.tile .v { font-size: 30px; font-weight: 650; margin: 2px 0; }
.tile .d { font-size: 13px; color: var(--ink-2); }
.up { color: var(--good-text); } .down { color: var(--critical); }
.alerts { display: grid; gap: 8px; margin-bottom: 20px; }
.alert { display: flex; gap: 10px; align-items: flex-start; background: var(--surface);
  border: 1px solid var(--border); border-left: 4px solid var(--muted); border-radius: 10px; padding: 10px 14px; }
.alert.alta { border-left-color: var(--critical); } .alert.media { border-left-color: var(--serious); }
.alert.info { border-left-color: var(--s1); } .alert.ok { border-left-color: var(--good); }
.alert > div { min-width: 0; }
.alert .tag { font-size: 12px; font-weight: 650; text-transform: uppercase; color: var(--ink-2); white-space: nowrap; }
.filters { display: flex; gap: 6px; margin: 4px 0 12px; flex-wrap: wrap; }
.filters button { background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  border-radius: 999px; padding: 6px 14px; font: inherit; font-size: 13px; cursor: pointer; }
.filters button[aria-pressed="true"] { background: var(--ink); color: var(--page); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 12px; }
.grid > *, .tiles > * { min-width: 0; }
.chart-box { position: relative; height: 220px; }
.card p { margin: 4px 0 10px; color: var(--ink-2); font-size: 13px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
section { margin-top: 24px; }
.scroll { overflow-x: auto; }
@media (max-width: 420px) { .tile .v { font-size: 24px; } .tile { padding: 12px; } }
</style>
</head>
<body>
<main>
<header>
  <h1>Fitbit Air Lab</h1>
  <span class="sub">Datos hasta el __DAY__ · comparados con tus últimos 28 días</span>
</header>
<div class="tiles" id="tiles"></div>
<div class="alerts" id="alerts"></div>

<div class="filters" role="group" aria-label="Periodo">
  <button data-days="30" aria-pressed="false">30 días</button>
  <button data-days="90" aria-pressed="true">90 días</button>
  <button data-days="365" aria-pressed="false">1 año</button>
  <button data-days="0" aria-pressed="false">Todo</button>
</div>

<div class="grid">
  <div class="card"><h2>Recuperación diaria</h2><p>HRV, FC en reposo, sueño y respiración frente a tu media.</p><div class="chart-box"><canvas id="c-recovery"></canvas></div></div>
  <div class="card"><h2>HRV</h2><p>La banda es tu rango normal (media ± 1σ de 28 días).</p><div class="chart-box"><canvas id="c-hrv"></canvas></div></div>
  <div class="card"><h2>FC en reposo</h2><p>Subidas sostenidas: estrés, mal descanso o algo incubándose.</p><div class="chart-box"><canvas id="c-rhr"></canvas></div></div>
  <div class="card"><h2>Fases del sueño</h2><p>Minutos por noche.</p><div class="chart-box"><canvas id="c-stages"></canvas></div></div>
  <div class="card"><h2>Horario de sueño</h2><p>De acostarte a levantarte. La regularidad importa tanto como la duración.</p><div class="chart-box"><canvas id="c-schedule"></canvas></div></div>
  <div class="card"><h2>Temperatura nocturna</h2><p>Variación respecto a tu base (°C).</p><div class="chart-box"><canvas id="c-temp"></canvas></div></div>
  <div class="card"><h2>Respiración durmiendo</h2><p>Respiraciones por minuto. Muy estable; los cambios significan algo.</p><div class="chart-box"><canvas id="c-resp"></canvas></div></div>
  <div class="card"><h2>Minutos en zona activa</h2><p>Barras: cada día. Línea: media de 7 días.</p><div class="chart-box"><canvas id="c-azm"></canvas></div></div>
  <div class="card"><h2>Pasos</h2><p>Línea: media de 7 días.</p><div class="chart-box"><canvas id="c-steps"></canvas></div></div>
</div>

<section class="card">
  <h2>Tendencias: últimos 7 días frente a los 28 anteriores</h2>
  <div class="scroll"><table id="t-trends"></table></div>
</section>

<section class="card">
  <h2>Qué afecta a tus noches</h2>
  <p>Lo que apuntas con <code>fitbit-air log</code> comparado con la noche siguiente. Cuantas más anotaciones, más fiable.</p>
  <div class="scroll"><table id="t-tags"></table></div>
</section>

<section class="card">
  <h2>Correlaciones (hoy → noche/mañana siguiente)</h2>
  <p>Relaciones con |r| ≥ 0,25. Correlación no es causalidad: úsalas como hipótesis para probar.</p>
  <div class="scroll"><table id="t-corr"></table></div>
</section>

<section class="card">
  <h2>Últimos 14 días</h2>
  <div class="scroll"><table id="t-days"></table></div>
</section>
<p class="sub">Heurísticas personales para hacer seguimiento, no diagnósticos médicos.</p>
</main>

<script>
const DATA = __DATA__;
const S = DATA.summary, SER = DATA.series, L = DATA.labels;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("es-ES", {maximumFractionDigits: d, minimumFractionDigits: d});
const hm = m => m == null ? "–" : `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, "0")}`;
const clock = m => { if (m == null) return "–"; let x = ((Math.round(m) % 1440) + 1440) % 1440; return `${String(Math.floor(x / 60)).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

// ---------- Tiles ----------
function tile(k, v, d) { return `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d || ""}</div></div>`; }
function delta(v, base, upGood, unit, dec = 0) {
  if (v == null || base == null) return "";
  const diff = v - base, good = (diff > 0) === upGood;
  const cls = Math.abs(diff) < 0.5 ? "" : (good ? "up" : "down");
  return `<span class="${cls}">${diff > 0 ? "▲" : diff < 0 ? "▼" : "•"} ${fmt(Math.abs(diff), dec)} ${unit}</span> vs tu media`;
}
function recLabel(s) { return s >= 70 ? "Listo para apretar" : s >= 45 ? "Normal: entreno moderado" : "Prioriza recuperar"; }
(function tiles() {
  const t = S.today || {}, b = S.baselines || {};
  const out = [];
  if (S.recovery != null) out.push(tile("Recuperación", `${S.recovery}<span class="sub">/100</span>`, recLabel(S.recovery)));
  if (S.sleep_score != null) out.push(tile("Sueño", hm(t.sleep_min), `Puntuación ${S.sleep_score}/100 · ${clock(t.bedtime)}–${clock(t.waketime)}`));
  if (t.hrv != null) out.push(tile("HRV", `${fmt(t.hrv)} <span class="sub">ms</span>`, delta(t.hrv, b.hrv, true, "ms")));
  if (t.rhr != null) out.push(tile("FC en reposo", `${fmt(t.rhr)} <span class="sub">lpm</span>`, delta(t.rhr, b.rhr, false, "lpm")));
  if (t.sleep_hr_avg != null) out.push(tile("FC durmiendo", `${fmt(t.sleep_hr_avg)} <span class="sub">lpm</span>`, delta(t.sleep_hr_avg, b.sleep_hr_avg, false, "lpm")));
  if (S.load && S.load.ratio) out.push(tile("Carga 7 d / 28 d", fmt(S.load.ratio, 2), S.load.ratio > 1.5 ? "Subida brusca" : S.load.ratio < 0.8 ? "Por debajo de lo habitual" : "Zona óptima (0,8–1,5)"));
  if (S.debt != null) out.push(tile("Deuda de sueño 14 d", hm(Math.max(0, S.debt)), S.debt <= 0 ? "Sin deuda" : "Por debajo de tu objetivo"));
  document.getElementById("tiles").innerHTML = out.join("") || tile("Sin datos", "–", "Ejecuta fitbit-air sync");
})();

// ---------- Alerts ----------
(function alerts() {
  const icon = {alta: "⛔", media: "⚠️", info: "ℹ️", ok: "✅"};
  const name = {alta: "Importante", media: "Atención", info: "Aviso", ok: "Todo en orden"};
  const list = S.alerts && S.alerts.length ? S.alerts : [{level: "ok", title: "Sin señales fuera de tu rango", detail: "Tus métricas clave están dentro de lo normal para ti."}];
  document.getElementById("alerts").innerHTML = list.map(a =>
    `<div class="alert ${a.level}" role="status"><span aria-hidden="true">${icon[a.level]}</span><div><span class="tag">${name[a.level]}</span> <strong>${esc(a.title)}</strong><div class="sub">${esc(a.detail)}</div></div></div>`).join("");
})();

// ---------- Charts ----------
function rolling(arr, n = 7) {
  return arr.map((_, i) => { const w = arr.slice(Math.max(0, i - n + 1), i + 1).filter(v => v != null); return w.length >= 3 ? w.reduce((a, b) => a + b, 0) / w.length : null; });
}
const charts = [];
function base(opts = {}) {
  const grid = css("--grid"), muted = css("--muted"), ink = css("--ink");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: "index", intersect: false},
    plugins: {
      legend: {display: !!opts.legend, labels: {color: css("--ink-2"), boxWidth: 12, boxHeight: 12, usePointStyle: true}, onClick: () => {}},
      tooltip: {backgroundColor: css("--surface"), titleColor: ink, bodyColor: ink, borderColor: css("--axis"), borderWidth: 1, padding: 10,
        callbacks: opts.tooltip || {}, filter: i => !String(i.dataset.label || "").startsWith("_")},
    },
    scales: {
      x: {stacked: !!opts.stacked, ticks: {color: muted, maxTicksLimit: 8, maxRotation: 0}, grid: {display: false}, border: {color: css("--axis")}},
      y: {stacked: !!opts.stacked, ticks: {color: muted, maxTicksLimit: opts.step ? 10 : 5, stepSize: opts.step, callback: opts.yfmt}, grid: {color: grid}, border: {display: false},
          min: opts.min, max: opts.max, reverse: !!opts.reverse, suggestedMin: opts.suggestedMin},
    },
  };
}
const bar = (label, data, color, extra = {}) => ({type: "bar", label, data, backgroundColor: color, borderRadius: 4, borderSkipped: "start", barPercentage: 0.9, categoryPercentage: 0.9, ...extra});
const line = (label, data, color, extra = {}) => ({type: "line", label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, spanGaps: true, tension: 0.25, ...extra});

function draw(range) {
  charts.forEach(c => c.destroy()); charts.length = 0;
  const n = SER.days.length, start = range ? Math.max(0, n - range) : 0;
  const cut = a => a.slice(start);
  const days = cut(SER.days).map(d => d.slice(5).split("-").reverse().join("/"));
  const mk = (id, datasets, opts) => charts.push(new Chart(document.getElementById(id), {data: {labels: days, datasets}, options: base(opts)}));
  const s1 = css("--s1"), s2 = css("--s2"), s3 = css("--s3"), s4 = css("--s4"), s7 = css("--s7"), band = css("--band");

  mk("c-recovery", [bar("Recuperación", cut(SER.recovery), s1)], {min: 0, max: 100});
  for (const [id, key, color] of [["c-hrv", "hrv", s1], ["c-rhr", "rhr", s2]]) {
    mk(id, [
      line("_lo", cut(SER[key + "_band"].lo), "transparent", {pointHoverRadius: 0}),
      line("_hi", cut(SER[key + "_band"].hi), "transparent", {fill: "-1", backgroundColor: band, pointHoverRadius: 0}),
      line(L[key], cut(SER[key]), color, {pointRadius: 2}),
    ], {tooltip: {label: c => `${c.dataset.label}: ${fmt(c.parsed.y)}`}});
  }
  mk("c-stages", [
    bar("Profundo", cut(SER.deep_min), s7), bar("REM", cut(SER.rem_min), s1),
    bar("Ligero", cut(SER.light_min), s3), bar("Despierto", cut(SER.awake_min), s4),
  ], {stacked: true, legend: true, tooltip: {label: c => `${c.dataset.label}: ${hm(c.parsed.y)}`}});
  const sched = cut(SER.bedtime).map((b, i) => b == null || cut(SER.waketime)[i] == null ? null : [b, cut(SER.waketime)[i]]);
  const flat = sched.filter(Boolean).flat();
  mk("c-schedule", [bar("Sueño", sched, s7, {borderSkipped: false})], {
    reverse: true, yfmt: v => clock(v), step: 120,
    min: flat.length ? Math.floor(Math.min(...flat) / 60) * 60 : undefined,
    max: flat.length ? Math.ceil(Math.max(...flat) / 60) * 60 : undefined,
    tooltip: {label: c => { const r = c.raw; return r ? `${clock(r[0])} → ${clock(r[1])} (${hm(r[1] - r[0])})` : ""; }}});
  const temp = cut(SER.skin_temp_dev);
  mk("c-temp", [bar("Variación (°C)", temp, temp.map(v => v != null && v > 0 ? s2 : s1))], {suggestedMin: -1,
    tooltip: {label: c => `${c.parsed.y > 0 ? "+" : ""}${fmt(c.parsed.y, 2)} °C`}});
  mk("c-resp", [line("Respiración (rpm)", cut(SER.resp_rate), s3, {pointRadius: 2})], {tooltip: {label: c => `${fmt(c.parsed.y, 1)} rpm`}});
  mk("c-azm", [bar("Minutos en zona", cut(SER.azm), s2), line("Media 7 días", cut(rolling(SER.azm)), css("--ink-2"))], {legend: true});
  mk("c-steps", [bar("Pasos", cut(SER.steps), s1), line("Media 7 días", cut(rolling(SER.steps)), css("--ink-2"))], {legend: true});
}
document.querySelectorAll(".filters button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".filters button").forEach(x => x.setAttribute("aria-pressed", x === b));
  try { localStorage.setItem("range", b.dataset.days); } catch (e) {}
  draw(+b.dataset.days);
}));
let saved = null; try { saved = localStorage.getItem("range"); } catch (e) {}
if (saved) document.querySelectorAll(".filters button").forEach(x => x.setAttribute("aria-pressed", x.dataset.days === saved));
if (window.Chart) draw(+(saved || 90));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => window.Chart && draw(+document.querySelector('.filters [aria-pressed="true"]').dataset.days));

// ---------- Tables ----------
(function tables() {
  const tr = S.trends || [];
  document.getElementById("t-trends").innerHTML = "<tr><th>Métrica</th><th class=n>7 días</th><th class=n>28 previos</th><th class=n>Cambio</th></tr>" +
    tr.map(t => `<tr><td>${esc(t.label)}</td><td class=n>${fmt(t.recent, t.prior > 200 ? 0 : 1)}</td><td class=n>${fmt(t.prior, t.prior > 200 ? 0 : 1)}</td><td class="n ${t.good == null ? "" : t.good ? "up" : "down"}">${t.pct > 0 ? "▲" : "▼"} ${fmt(Math.abs(t.pct), 1)} %</td></tr>`).join("");
  const tags = S.tags || [];
  document.getElementById("t-tags").innerHTML = tags.length ? "<tr><th>Anotación</th><th>Métrica (noche siguiente)</th><th class=n>Con</th><th class=n>Sin</th><th class=n>Diferencia</th><th class=n>n</th></tr>" +
    tags.map(t => `<tr><td>${esc(t.tag)}</td><td>${esc(L[t.metric] || t.metric)}</td><td class=n>${fmt(t.with_tag, 1)}</td><td class=n>${fmt(t.without, 1)}</td><td class=n>${t.diff_pct > 0 ? "+" : ""}${fmt(t.diff_pct, 1)} %</td><td class=n>${t.n_with}/${t.n_without}</td></tr>`).join("")
    : "<tr><td>Aún no hay suficientes anotaciones (mínimo 3 días con y 3 sin cada etiqueta).</td></tr>";
  const co = S.correlations || [];
  document.getElementById("t-corr").innerHTML = co.length ? "<tr><th>Hoy</th><th>Mañana</th><th class=n>r</th><th class=n>días</th></tr>" +
    co.map(c => `<tr><td>${esc(L[c.x] || c.x)}</td><td>${esc(L[c.y] || c.y)}</td><td class=n>${c.r > 0 ? "+" : ""}${fmt(c.r, 2)}</td><td class=n>${c.n}</td></tr>`).join("")
    : "<tr><td>Sin correlaciones relevantes todavía.</td></tr>";
  const cols = [["recovery", "Recup.", 0], ["sleep_score", "Sueño", 0], ["sleep_min", "Dormido", "hm"], ["deep_min", "Prof.", "hm"], ["rem_min", "REM", "hm"],
    ["bedtime", "Acostarse", "clock"], ["hrv", "HRV", 0], ["rhr", "FC rep.", 0], ["resp_rate", "Resp.", 1], ["skin_temp_dev", "Temp.", 2], ["steps", "Pasos", 0], ["azm", "AZM", 0]];
  const idx = SER.days.map((d, i) => i).slice(-14).reverse();
  document.getElementById("t-days").innerHTML = "<tr><th>Día</th>" + cols.map(c => `<th class=n>${c[1]}</th>`).join("") + "</tr>" +
    idx.map(i => `<tr><td>${SER.days[i]}</td>` + cols.map(([k, , f]) => { const v = SER[k][i]; return `<td class=n>${f === "hm" ? hm(v) : f === "clock" ? clock(v) : fmt(v, f)}</td>`; }).join("") + "</tr>").join("");
})();
</script>
</body>
</html>
"""
