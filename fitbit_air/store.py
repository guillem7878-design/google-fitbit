"""Base de datos SQLite local con tus datos crudos y las métricas diarias."""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_points (
    data_type TEXT NOT NULL,
    point_id  TEXT NOT NULL,
    day       TEXT,
    payload   TEXT NOT NULL,
    PRIMARY KEY (data_type, point_id)
);
CREATE TABLE IF NOT EXISTS daily (
    day    TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL,
    PRIMARY KEY (day, metric)
);
CREATE TABLE IF NOT EXISTS hr_samples (
    ts  INTEGER PRIMARY KEY,   -- epoch UTC en segundos
    bpm INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
    day   TEXT NOT NULL,
    tag   TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 1,
    note  TEXT,
    PRIMARY KEY (day, tag)
);
CREATE TABLE IF NOT EXISTS metric_sources (
    metric TEXT PRIMARY KEY,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (
    data_type TEXT PRIMARY KEY,
    last_day  TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.db_path())
    conn.executescript(SCHEMA)
    return conn


def save_raw(conn, data_type: str, point_id: str, day: str | None, payload: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO raw_points VALUES (?, ?, ?, ?)",
        (data_type, point_id, day, json.dumps(payload)),
    )


def set_metric(conn, day: str, metric: str, value: float) -> None:
    conn.execute("INSERT OR REPLACE INTO daily VALUES (?, ?, ?)", (day, metric, float(value)))


def note_source(conn, metric: str, source: str) -> None:
    conn.execute("INSERT OR REPLACE INTO metric_sources VALUES (?, ?)", (metric, source))


def save_hr(conn, samples: list[tuple[int, int]]) -> None:
    conn.executemany("INSERT OR REPLACE INTO hr_samples VALUES (?, ?)", samples)


def daily_table(conn) -> dict[str, dict[str, float]]:
    """{día: {métrica: valor}} ordenado por día."""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for day, metric, value in conn.execute("SELECT day, metric, value FROM daily ORDER BY day"):
        out[day][metric] = value
    return dict(sorted(out.items()))


def journal_table(conn) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for day, tag, value in conn.execute("SELECT day, tag, value FROM journal"):
        out[day][tag] = value
    return dict(out)


def raw_points(conn, data_type: str, since_day: str | None = None) -> list[dict]:
    q = "SELECT payload FROM raw_points WHERE data_type = ?"
    args: list = [data_type]
    if since_day:
        q += " AND day >= ?"
        args.append(since_day)
    return [json.loads(p) for (p,) in conn.execute(q + " ORDER BY day", args)]


def get_sync_day(conn, data_type: str) -> str | None:
    row = conn.execute("SELECT last_day FROM sync_state WHERE data_type = ?", (data_type,)).fetchone()
    return row[0] if row else None


def set_sync_day(conn, data_type: str, day: str) -> None:
    conn.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?)", (data_type, day))
