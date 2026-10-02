"""Línea de comandos: fitbit-air <comando>."""

import argparse
import csv
import json
import sys
import webbrowser
from datetime import date, timedelta
from pathlib import Path

from . import analysis, auth, config, store
from . import report as rp

SETTINGS_DEFAULTS = {"sleep_target_min": 450}


def load_settings() -> dict:
    p = config.settings_path()
    data = json.loads(p.read_text()) if p.exists() else {}
    return {**SETTINGS_DEFAULTS, **data}


def save_settings(data: dict) -> None:
    p = config.settings_path()
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    p.chmod(0o600)


def _parse_day(s: str) -> str:
    if s in ("hoy", "today"):
        return date.today().isoformat()
    if s in ("ayer", "yesterday"):
        return (date.today() - timedelta(days=1)).isoformat()
    return date.fromisoformat(s).isoformat()


def cmd_setup(a):
    dest = auth.install_client_secret(Path(a.client_secret))
    print(f"client_secret copiado a {dest}. Ahora: fitbit-air login")


def cmd_login(a):
    auth.login()


def cmd_sync(a):
    from .api import HealthClient
    from .sync import sync
    conn = store.connect()
    print(f"Sincronizando en {config.db_path()} ...")
    sync(conn, HealthClient(), days=a.days, intraday_days=a.intraday_days, full=a.full)
    print("Hecho.")


def cmd_demo(a):
    from .demo import generate
    conn = store.connect()
    generate(conn, days=a.days)
    print(f"Datos de demostración creados en {config.db_path()}")


def _sleep_target() -> float:
    return float(load_settings()["sleep_target_min"])


def cmd_report(a):
    conn = store.connect()
    out = Path(a.output) if a.output else config.data_dir() / "panel.html"
    rp.render_html(store.daily_table(conn), store.journal_table(conn), out, _sleep_target())
    print(f"Panel generado: {out}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())


def cmd_today(a):
    conn = store.connect()
    s = rp.build_summary(store.daily_table(conn), store.journal_table(conn), _sleep_target())
    print(rp.text_summary(s))


def cmd_notify(a):
    from . import notify
    settings = load_settings()
    conn = store.connect()
    s = rp.build_summary(store.daily_table(conn), store.journal_table(conn), _sleep_target())
    if a.only_alerts and not [x for x in s.get("alerts", []) if x["level"] in ("alta", "media")]:
        print("Sin alertas importantes: no se envía nada.")
        return
    urgent = any(x["level"] == "alta" for x in s.get("alerts", []))
    sent = notify.send(settings, "Fitbit Air · resumen", rp.text_summary(s), urgent)
    print("Enviado por: " + ", ".join(sent) if sent else
          "No hay canal configurado. Usa: fitbit-air config ntfy_topic <tema>")


def cmd_log(a):
    conn = store.connect()
    day = _parse_day(a.day)
    for tag in a.tags:
        name, _, val = tag.partition("=")
        conn.execute("INSERT OR REPLACE INTO journal VALUES (?, ?, ?, ?)",
                     (day, name.strip().lower(), float(val) if val else 1.0, a.note))
    conn.commit()
    print(f"Anotado en {day}: {', '.join(a.tags)}")


def cmd_unlog(a):
    conn = store.connect()
    conn.execute("DELETE FROM journal WHERE day = ? AND tag = ?", (_parse_day(a.day), a.tag.lower()))
    conn.commit()


def cmd_inspect(a):
    conn = store.connect()
    print("Campos de la API usados para cada métrica (revisa que tengan sentido):")
    for metric, source in conn.execute("SELECT metric, source FROM metric_sources ORDER BY metric"):
        print(f"  {metric:15s} <- {source}")
    for dt in a.raw or []:
        pts = store.raw_points(conn, dt)
        print(f"\nÚltimo punto crudo de {dt}:")
        print(json.dumps(pts[-1], indent=2, ensure_ascii=False) if pts else "  (vacío)")


def cmd_export(a):
    conn = store.connect()
    table = analysis.enrich(store.daily_table(conn), _sleep_target())
    journal = store.journal_table(conn)
    metrics = sorted({k for m in table.values() for k in m})
    tags = sorted({t for j in journal.values() for t in j})
    out = Path(a.output)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", *metrics, *[f"tag_{t}" for t in tags]])
        for day, m in table.items():
            w.writerow([day, *[m.get(k, "") for k in metrics], *[journal.get(day, {}).get(t, "") for t in tags]])
    print(f"Exportado {len(table)} días a {out}")


def cmd_config(a):
    s = load_settings()
    if a.key is None:
        for k, v in s.items():
            shown = "****" if "token" in k and v else v
            print(f"{k} = {shown}")
        return
    if a.value is None:
        print(s.get(a.key))
        return
    try:
        s[a.key] = json.loads(a.value)
    except json.JSONDecodeError:
        s[a.key] = a.value
    save_settings({k: v for k, v in s.items()})
    print(f"{a.key} guardado.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="fitbit-air", description="Tus datos de la Fitbit Air, a otro nivel.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="instala el client_secret.json de Google Cloud")
    s.add_argument("client_secret")
    s.set_defaults(fn=cmd_setup)

    sub.add_parser("login", help="autoriza el acceso a tu cuenta de Google").set_defaults(fn=cmd_login)

    s = sub.add_parser("sync", help="descarga tus datos a la base local")
    s.add_argument("--days", type=int, default=90, help="historial a descargar (por defecto 90)")
    s.add_argument("--intraday-days", type=int, default=14, help="días de FC minuto a minuto (0 = no)")
    s.add_argument("--full", action="store_true", help="vuelve a descargar todo el periodo")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("demo", help="rellena la base con datos de ejemplo")
    s.add_argument("--days", type=int, default=120)
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("report", help="genera el panel HTML")
    s.add_argument("-o", "--output")
    s.add_argument("--open", action="store_true", help="abre el panel en el navegador")
    s.set_defaults(fn=cmd_report)

    sub.add_parser("today", help="resumen de hoy en la terminal").set_defaults(fn=cmd_today)

    s = sub.add_parser("notify", help="envía el resumen al móvil (ntfy/Telegram)")
    s.add_argument("--only-alerts", action="store_true", help="solo si hay alertas importantes")
    s.set_defaults(fn=cmd_notify)

    s = sub.add_parser("log", help="anota hábitos del día: alcohol, cafe_tarde, cena_tarde, estres=3...")
    s.add_argument("tags", nargs="+")
    s.add_argument("--day", default="hoy", help="hoy, ayer o AAAA-MM-DD")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("unlog", help="borra una anotación")
    s.add_argument("tag")
    s.add_argument("--day", default="hoy")
    s.set_defaults(fn=cmd_unlog)

    s = sub.add_parser("inspect", help="muestra de qué campo de la API sale cada métrica")
    s.add_argument("--raw", nargs="*", help="tipos de dato cuyo último punto crudo mostrar (ej. sleep)")
    s.set_defaults(fn=cmd_inspect)

    s = sub.add_parser("export", help="exporta todas las métricas diarias a CSV")
    s.add_argument("output", nargs="?", default="fitbit_air_diario.csv")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("config", help="ver o cambiar ajustes (sleep_target_min, ntfy_topic, telegram_token...)")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(fn=cmd_config)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
