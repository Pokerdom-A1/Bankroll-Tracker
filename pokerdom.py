#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pokerdom.py — консольный банкролл-трекер для Pokerdom
Один файл, SQLite-хранилище, импорт CSV/XLSX, расчёт метрик (cash/mtt),
кэшбек по конфигу, графики (опционально), экспорт отчётов.

Зависимости (обязательно): Python 3.10+, stdlib
Опционально: matplotlib (графики), openpyxl (чтение .xlsx)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

# Попытка подгрузить опциональные зависимости
try:
    import openpyxl  # type: ignore
    HAS_OPENPYXL = True
except Exception:
    HAS_OPENPYXL = False

try:
    import matplotlib.pyplot as plt  # type: ignore
    HAS_MPL = True
except Exception:
    HAS_MPL = False


# ------------------------------
# Константы и настройки по умолчанию
# ------------------------------

APP_NAME = "pokerdom.py"
DEFAULT_DB = ".pokerdom_tracker.sqlite"
DEFAULT_LOG_LEVEL = logging.INFO
DATE_FMT = "%Y-%m-%d"

# Ключи конфигурации:
CFG_RAKEBACK = "rakeback"
CFG_LOG_FILE = "log_file"
CFG_RATES = "rates"
CFG_LANG = "lang"  # 'ru' | 'en'

DEFAULT_RAKEBACK = {
    "currency": "RUB",
    "tiers": [
        {"min_rake": 0, "rate": 0.02},
        {"min_rake": 5000, "rate": 0.04},
        {"min_rake": 20000, "rate": 0.06},
        {"min_rake": 50000, "rate": 0.08},
    ],
    "reset_period": "weekly",  # weekly | monthly | none
}

DEFAULT_MESSAGES = {
    "ru": {
        "db_created": "База данных и конфиг инициализированы.",
        "already_exists": "База данных уже существует, пропускаю создание таблиц.",
        "import_done": "Импорт завершён: {ok} добавлено, {failed} с ошибками.",
        "dry_run": "Пробный запуск (dry-run): {ok} потенциальных вставок, {failed} ошибок.",
        "xlsx_missing": "Для чтения .xlsx установите пакет openpyxl или сохраните файл в CSV.",
        "no_mpl": "Matplotlib не найден. Установите пакет для построения графиков.",
        "graph_saved": "График сохранён: {path}",
        "export_done": "Экспорт завершён в папку: {path}",
        "config_set": "Конфиг обновлён.",
        "config_show": "Текущие значения конфига:",
        "forecast_unreachable": "Цель недостижима с текущим темпом (<= 0).",
        "rakeback_report": "Кэшбек за период: рейк={rake:.2f} {cur}, ставка={rate:.2%}, кэшбек={rb:.2f} {cur} (уровень от {minr:.2f})",
        "stats_header": "Сводная статистика",
        "stats_cash": "КЭШ:",
        "stats_mtt": "ТУРНИРЫ:",
    },
    "en": {
        "db_created": "Database and config initialized.",
        "already_exists": "Database already exists; skipping schema creation.",
        "import_done": "Import finished: {ok} inserted, {failed} failed.",
        "dry_run": "Dry-run: {ok} potential inserts, {failed} failed.",
        "xlsx_missing": "Install openpyxl to read .xlsx or save the file as CSV.",
        "no_mpl": "Matplotlib not found. Install it to generate charts.",
        "graph_saved": "Chart saved: {path}",
        "export_done": "Export completed to: {path}",
        "config_set": "Config updated.",
        "config_show": "Current config values:",
        "forecast_unreachable": "Target is unreachable with current pace (<= 0).",
        "rakeback_report": "Rakeback for period: rake={rake:.2f} {cur}, rate={rate:.2%}, rakeback={rb:.2f} {cur} (tier from {minr:.2f})",
        "stats_header": "Stats summary",
        "stats_cash": "CASH:",
        "stats_mtt": "MTT:",
    },
}

# Нормализация русских колонок к целевым именам
COLUMN_SYNONYMS = {
    # cash
    "date": ["date", "дата"],
    "stakes_bb": ["stakes_bb", "блайнд", "бб", "bb", "ставка_блайнда"],
    "hands": ["hands", "руки", "раздачи"],
    "buyin": ["buyin", "вход", "бай-ин", "байин", "депозит_за_сессию"],
    "cashout": ["cashout", "выход", "кэшаут", "выплата", "итог"],
    "currency": ["currency", "валюта"],
    "rake": ["rake", "рейк"],
    "notes": ["notes", "заметки", "комментарий"],
    "room": ["room", "рум"],
    # mtt
    "fee": ["fee", "комиссия", "рейк_турнирный"],
    "prize": ["prize", "приз", "выплата"],
    "position": ["position", "место"],
    "field_size": ["field_size", "участников", "поле", "игроков"],
}

# ------------------------------
# Утилиты
# ------------------------------


def setup_logging(verbose: bool, db_path: Path, lang: str, log_file: Optional[str]) -> None:
    """Настроить логирование: уровень, вывод в консоль и файл (опционально)."""
    level = logging.DEBUG if verbose else DEFAULT_LOG_LEVEL
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        try:
            handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
        except Exception:
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def parse_date(s: str) -> dt.date:
    """Парсинг даты в формате YYYY-MM-DD с внятной ошибкой."""
    return dt.datetime.strptime(s.strip(), DATE_FMT).date()


def today_str() -> str:
    return dt.date.today().strftime(DATE_FMT)


def ensure_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", ".")
    if s == "" or s.lower() in {"none", "nan"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def ensure_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, int):
        return x
    s = str(x).strip()
    if s == "" or s.lower() in {"none", "nan"}:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def normalize_header(h: str) -> str:
    """Нормализуем имя колонки: нижний регистр, удаление пробелов и пр."""
    return re.sub(r"[^a-z0-9_]", "", h.lower().replace(" ", "_"))


def auto_map_headers(headers: List[str], target_fields: List[str]) -> Dict[str, str]:
    """
    Автоматически сопоставляем заголовки CSV к целевым полям БД.
    target_fields — список целевых полей (например, для cash или mtt).
    Возвращает: {target_field: input_header_name}
    """
    normalized = {normalize_header(h): h for h in headers}
    mapping: Dict[str, str] = {}
    for target in target_fields:
        candidates = COLUMN_SYNONYMS.get(target, [target])
        for cand in candidates:
            key = normalize_header(cand)
            # прямое совпадение
            if key in normalized:
                mapping[target] = normalized[key]
                break
            # поиск по всем хедерам (которые могут быть русские)
            for nh, orig in normalized.items():
                if nh == key:
                    mapping[target] = orig
                    break
            if target in mapping:
                break
        # если до сих пор не нашли — пробуем по «похожим» словам
        if target not in mapping:
            for nh, orig in normalized.items():
                if target in nh:
                    mapping[target] = orig
                    break
    return mapping


def open_csv_or_xlsx(path: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Открыть CSV или XLSX и вернуть (headers, rows).
    - Для XLSX требуется openpyxl. Читается активный лист. Первая строка — заголовки.
    - Для CSV — используется csv.DictReader (utf-8 / utf-8-sig).
    """
    if path.suffix.lower() in {".xlsx", ".xls"}:
        if not HAS_OPENPYXL:
            raise RuntimeError(DEFAULT_MESSAGES["ru"]["xlsx_missing"])
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return [], []
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        data_rows: List[Dict[str, Any]] = []
        for r in rows[1:]:
            d = {headers[i]: r[i] for i in range(len(headers))}
            data_rows.append(d)
        return headers, data_rows
    else:
        # CSV
        data_rows = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            for row in reader:
                data_rows.append(row)
        return headers, data_rows


def hash_row(row: Dict[str, Any], fields: List[str]) -> str:
    """Хэш строки по нормализованным полям для дедупликации импорта."""
    key = "|".join(
        str(row.get(f, "")).strip().lower().replace(" ", "")
        for f in fields
    )
    # простой псевдо-хэш (без внешних зависимостей)
    return str(abs(hash(key)))


def fetch_config(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Получить конфиг как словарь."""
    cur = conn.execute("SELECT key, value FROM config")
    res = {}
    for k, v in cur.fetchall():
        try:
            res[k] = json.loads(v)
        except Exception:
            res[k] = v
    return res


def set_config(conn: sqlite3.Connection, kv: Dict[str, Any]) -> None:
    """Сохранить пары key=value в config (value хранится как JSON/строка)."""
    for k, v in kv.items():
        if isinstance(v, str):
            val = v
        else:
            val = json.dumps(v, ensure_ascii=False)
        conn.execute(
            "INSERT INTO config(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, val),
        )
    conn.commit()


def get_lang(conn: sqlite3.Connection) -> str:
    cfg = fetch_config(conn)
    lang = cfg.get(CFG_LANG, "ru")
    if isinstance(lang, str):
        return lang if lang in DEFAULT_MESSAGES else "ru"
    return "ru"


def msg(conn: sqlite3.Connection, key: str, **kwargs) -> str:
    lang = get_lang(conn)
    template = DEFAULT_MESSAGES.get(lang, DEFAULT_MESSAGES["ru"]).get(key, key)
    try:
        return template.format(**kwargs)
    except Exception:
        return template


# ------------------------------
# Работа с БД (схема / соединение)
# ------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cash_sessions (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    room TEXT,
    currency TEXT,
    stakes_bb REAL,
    hands INTEGER,
    buyin REAL,
    cashout REAL,
    rake REAL,
    notes TEXT,
    import_id INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cash_date ON cash_sessions(date);
CREATE INDEX IF NOT EXISTS idx_cash_room ON cash_sessions(room);

CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    room TEXT,
    currency TEXT,
    buyin REAL,
    fee REAL,
    prize REAL,
    position INTEGER,
    field_size INTEGER,
    rake REAL,
    notes TEXT,
    import_id INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mtt_date ON tournaments(date);
CREATE INDEX IF NOT EXISTS idx_mtt_room ON tournaments(room);

CREATE TABLE IF NOT EXISTS bankroll_tx (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    currency TEXT,
    amount REAL NOT NULL,
    type TEXT CHECK(type IN ('deposit','withdraw','bonus','adjustment')) NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON bankroll_tx(date);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    mapping_json TEXT,
    rows_ok INTEGER,
    rows_failed INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Заполняем дефолтный конфиг, если пусто
    cur = conn.execute("SELECT COUNT(*) FROM config")
    if cur.fetchone()[0] == 0:
        set_config(conn, {
            CFG_RAKEBACK: DEFAULT_RAKEBACK,
            CFG_LANG: "ru",
        })
    conn.commit()


# ------------------------------
# Импорт и добавление данных
# ------------------------------

def insert_import_record(conn: sqlite3.Connection, source_file: str, mapping_json: Dict[str, Any],
                         rows_ok: int, rows_failed: int) -> int:
    now = dt.datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO imports(source_file, mapping_json, rows_ok, rows_failed, created_at) VALUES(?,?,?,?,?)",
        (source_file, json.dumps(mapping_json, ensure_ascii=False), rows_ok, rows_failed, now),
    )
    conn.commit()
    return cur.lastrowid


def import_cash(conn: sqlite3.Connection, file_path: Path, mapping: Optional[Dict[str, str]],
                room: str, currency_default: Optional[str], dry_run: bool) -> Tuple[int, int]:
    headers, rows = open_csv_or_xlsx(file_path)
    if not headers:
        return 0, 0
    target_fields = ["date", "stakes_bb", "hands", "buyin", "cashout", "currency", "rake", "notes"]
    auto_mapping = auto_map_headers(headers, target_fields)
    if mapping:
        auto_mapping.update(mapping)

    ok, failed = 0, 0
    now = dt.datetime.utcnow().isoformat()
    dedup_seen: set[str] = set()

    for row in rows:
        rec = {}
        for tf in target_fields:
            src = auto_mapping.get(tf)
            rec[tf] = (row.get(src) if src else None)

        # Валидация и нормализация
        try:
            date = parse_date(str(rec["date"]).strip())
            stakes = ensure_float(rec["stakes_bb"])
            hands = ensure_int(rec["hands"]) or 0
            buyin = ensure_float(rec["buyin"]) or 0.0
            cashout = ensure_float(rec["cashout"]) or 0.0
            rake = ensure_float(rec.get("rake"))
            currency = str(rec.get("currency") or currency_default or "RUB").strip().upper()
            notes = str(rec.get("notes") or "").strip()
            room_val = room

            # дедупликация: ключевые поля
            h = hash_row({
                "date": date.strftime(DATE_FMT),
                "room": room_val,
                "stakes_bb": stakes,
                "hands": hands,
                "buyin": buyin,
                "cashout": cashout,
                "currency": currency,
            }, ["date", "room", "stakes_bb", "hands", "buyin", "cashout", "currency"])
            if h in dedup_seen:
                failed += 1
                continue
            dedup_seen.add(h)

            if not dry_run:
                conn.execute(
                    "INSERT INTO cash_sessions(date, room, currency, stakes_bb, hands, buyin, cashout, rake, notes, import_id, created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (date.strftime(DATE_FMT), room_val, currency, stakes, hands, buyin, cashout, rake, notes, None, now),
                )
            ok += 1
        except Exception as e:
            logging.debug("Ошибка импорта строки: %r", e)
            failed += 1

    if dry_run:
        print(msg(conn, "dry_run", ok=ok, failed=failed))
    else:
        print(msg(conn, "import_done", ok=ok, failed=failed))
        insert_import_record(conn, str(file_path), auto_mapping, ok, failed)
        conn.commit()

    return ok, failed


def import_mtt(conn: sqlite3.Connection, file_path: Path, mapping: Optional[Dict[str, str]],
               room: str, currency_default: Optional[str], dry_run: bool) -> Tuple[int, int]:
    headers, rows = open_csv_or_xlsx(file_path)
    if not headers:
        return 0, 0
    target_fields = ["date", "buyin", "fee", "prize", "position", "field_size", "currency", "rake", "notes"]
    auto_mapping = auto_map_headers(headers, target_fields)
    if mapping:
        auto_mapping.update(mapping)

    ok, failed = 0, 0
    now = dt.datetime.utcnow().isoformat()
    dedup_seen: set[str] = set()

    for row in rows:
        rec = {}
        for tf in target_fields:
            src = auto_mapping.get(tf)
            rec[tf] = (row.get(src) if src else None)

        try:
            date = parse_date(str(rec["date"]).strip())
            buyin = ensure_float(rec["buyin"]) or 0.0
            fee = ensure_float(rec.get("fee"))
            prize = ensure_float(rec.get("prize")) or 0.0
            pos = ensure_int(rec.get("position"))
            field_size = ensure_int(rec.get("field_size"))
            rake = ensure_float(rec.get("rake"))
            currency = str(rec.get("currency") or currency_default or "RUB").strip().upper()
            notes = str(rec.get("notes") or "").strip()
            room_val = room

            # дедуп
            h = hash_row({
                "date": date.strftime(DATE_FMT),
                "room": room_val,
                "buyin": buyin,
                "fee": fee or 0.0,
                "prize": prize,
                "currency": currency,
                "pos": pos or 0,
                "field": field_size or 0,
            }, ["date", "room", "buyin", "fee", "prize", "currency", "pos", "field"])
            if h in dedup_seen:
                failed += 1
                continue
            dedup_seen.add(h)

            if not dry_run:
                conn.execute(
                    "INSERT INTO tournaments(date, room, currency, buyin, fee, prize, position, field_size, rake, notes, import_id, created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (date.strftime(DATE_FMT), room_val, currency, buyin, fee, prize, pos, field_size, rake, notes, None, now),
                )
            ok += 1
        except Exception as e:
            logging.debug("Ошибка импорта MTT: %r", e)
            failed += 1

    if dry_run:
        print(msg(conn, "dry_run", ok=ok, failed=failed))
    else:
        print(msg(conn, "import_done", ok=ok, failed=failed))
        insert_import_record(conn, str(file_path), auto_mapping, ok, failed)
        conn.commit()

    return ok, failed


def add_cash(conn: sqlite3.Connection, **kwargs) -> None:
    date = parse_date(kwargs["date"])
    stakes_bb = float(kwargs["stakes_bb"])
    hands = int(kwargs["hands"])
    buyin = float(kwargs["buyin"])
    cashout = float(kwargs["cashout"])
    currency = (kwargs.get("currency") or "RUB").upper()
    rake = ensure_float(kwargs.get("rake"))
    notes = kwargs.get("notes") or ""
    room = kwargs.get("room") or "Pokerdom"
    now = dt.datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO cash_sessions(date, room, currency, stakes_bb, hands, buyin, cashout, rake, notes, import_id, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (date.strftime(DATE_FMT), room, currency, stakes_bb, hands, buyin, cashout, rake, notes, None, now),
    )
    conn.commit()
    print("OK")


def add_mtt(conn: sqlite3.Connection, **kwargs) -> None:
    date = parse_date(kwargs["date"])
    buyin = float(kwargs["buyin"])
    fee = ensure_float(kwargs.get("fee"))
    prize = float(kwargs.get("prize"))
    pos = ensure_int(kwargs.get("position"))
    field_size = ensure_int(kwargs.get("field_size"))
    currency = (kwargs.get("currency") or "RUB").upper()
    rake = ensure_float(kwargs.get("rake"))
    notes = kwargs.get("notes") or ""
    room = kwargs.get("room") or "Pokerdom"
    now = dt.datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO tournaments(date, room, currency, buyin, fee, prize, position, field_size, rake, notes, import_id, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (date.strftime(DATE_FMT), room, currency, buyin, fee, prize, pos, field_size, rake, notes, None, now),
    )
    conn.commit()
    print("OK")


def add_tx(conn: sqlite3.Connection, **kwargs) -> None:
    date = parse_date(kwargs["date"])
    amount = float(kwargs["amount"])
    ttype = kwargs["type"]
    currency = (kwargs.get("currency") or "RUB").upper()
    notes = kwargs.get("notes") or ""
    conn.execute(
        "INSERT INTO bankroll_tx(date, currency, amount, type, notes) VALUES(?,?,?,?,?)",
        (date.strftime(DATE_FMT), currency, amount, ttype, notes),
    )
    conn.commit()
    print("OK")


# ------------------------------
# Агрегации и метрики
# ------------------------------

def filters_where(params: Dict[str, Any], table_alias: str = "") -> Tuple[str, List[Any]]:
    """
    Построить WHERE по фильтрам --from/--to/--room/--currency.
    table_alias — префикс таблицы (например, 'c.' или 't.')
    """
    where = []
    args: List[Any] = []
    from_d = params.get("from")
    to_d = params.get("to")
    room = params.get("room")
    currency = params.get("currency")

    alias = (table_alias if table_alias.endswith(".") or table_alias == "" else table_alias + ".")
    if from_d:
        where.append(f"{alias}date >= ?")
        args.append(from_d)
    if to_d:
        where.append(f"{alias}date <= ?")
        args.append(to_d)
    if room:
        where.append(f"{alias}room = ?")
        args.append(room)
    if currency:
        where.append(f"{alias}currency = ?")
        args.append(currency)

    if where:
        return "WHERE " + " AND ".join(where), args
    return "", []


def stats_cash(conn: sqlite3.Connection, filters: Dict[str, Any]) -> Dict[str, Any]:
    where, args = filters_where(filters, "c")
    cur = conn.execute(
        f"SELECT COALESCE(SUM(cashout - buyin),0), "
        f"       COALESCE(SUM(hands),0), "
        f"       COALESCE(SUM(rake),0), "
        f"       COALESCE(SUM(stakes_bb * 0 + 1),0) "
        f"FROM cash_sessions c {where}", args)
    profit, hands, rake_sum, rows = cur.fetchone()
    # Для winrate нужен средний bb/100: profit в валютах -> в больших блайндах
    # Приближение: используем средневзвешенный stakes_bb по рукам.
    cur2 = conn.execute(
        f"SELECT COALESCE(SUM((cashout - buyin)/NULLIF(stakes_bb,0) * hands),0.0), "
        f"       COALESCE(SUM(hands),0) "
        f"FROM cash_sessions c {where}", args)
    bb_profit_weighted, hands_total = cur2.fetchone()
    winrate = None
    if hands_total and hands_total > 0:
        # bb_profit_weighted — сумма bb по всем рукам; bb/100 = (sum_bb / hands) * 100
        winrate = (bb_profit_weighted / hands_total) * 100.0

    res = {
        "profit": float(profit or 0.0),
        "hands": int(hands or 0),
        "rake": float(rake_sum or 0.0),
        "winrate_bb100": (round(winrate, 2) if winrate is not None else None),
        "rows": int(rows or 0),
    }
    return res


def stats_mtt(conn: sqlite3.Connection, filters: Dict[str, Any]) -> Dict[str, Any]:
    where, args = filters_where(filters, "t")
    cur = conn.execute(
        f"SELECT COALESCE(SUM(prize),0), COALESCE(SUM(buyin + COALESCE(fee,0)),0), "
        f"       COALESCE(SUM(CASE WHEN prize>0 THEN 1 ELSE 0 END),0), "
        f"       COALESCE(COUNT(*),0) "
        f"FROM tournaments t {where}", args)
    prize_sum, invested, itm_count, total = cur.fetchone()
    roi = None
    if invested and invested > 0:
        roi = (prize_sum - invested) / invested
    res = {
        "profit": float((prize_sum or 0.0) - (invested or 0.0)),
        "invested": float(invested or 0.0),
        "prize": float(prize_sum or 0.0),
        "roi": (round(roi * 100.0, 2) if roi is not None else None),
        "itm_pct": (round((itm_count / total) * 100.0, 2) if total else None),
        "tournaments": int(total or 0),
    }
    return res


def bankroll_current(conn: sqlite3.Connection, currency: Optional[str], filters: Dict[str, Any]) -> float:
    """
    Текущий банкролл: сумма tx (депозиты/выводы/бонусы/коррекции) +
    профит из cash + профит из mtt, с учётом фильтров по валюте/датам/руму.
    """
    # Сумма движений
    where_tx, args_tx = "", []
    if currency:
        where_tx = "WHERE currency = ?"
        args_tx = [currency]

    cur = conn.execute(f"SELECT COALESCE(SUM(amount),0) FROM bankroll_tx {where_tx}", args_tx)
    base = float(cur.fetchone()[0] or 0.0)

    c = stats_cash(conn, filters)
    m = stats_mtt(conn, filters)
    return base + c["profit"] + m["profit"]


def print_stats(conn: sqlite3.Connection, filters: Dict[str, Any]) -> None:
    print("=" * 60)
    print(msg(conn, "stats_header"))
    print("-" * 60)
    # CASH
    print(msg(conn, "stats_cash"))
    c = stats_cash(conn, filters)
    print(f"  Сессий:         {c['rows']}")
    print(f"  Руки:           {c['hands']}")
    print(f"  Профит:         {c['profit']:.2f} {filters.get('currency','')}")
    print(f"  Рейк (сумма):   {c['rake']:.2f} {filters.get('currency','')}")
    if c["winrate_bb100"] is not None:
        print(f"  Winrate bb/100: {c['winrate_bb100']:.2f}")
    else:
        print(f"  Winrate bb/100: —")
    print("-" * 60)
    # MTT
    print(msg(conn, "stats_mtt"))
    m = stats_mtt(conn, filters)
    print(f"  Турниров:       {m['tournaments']}")
    print(f"  Инвестиции:     {m['invested']:.2f} {filters.get('currency','')}")
    print(f"  Призы:          {m['prize']:.2f} {filters.get('currency','')}")
    print(f"  Профит:         {m['profit']:.2f} {filters.get('currency','')}")
    print(f"  ROI:            {m['roi']:.2f} %" if m["roi"] is not None else "  ROI:            —")
    print(f"  ITM:            {m['itm_pct']:.2f} %" if m["itm_pct"] is not None else "  ITM:            —")
    print("-" * 60)
    # Банкролл
    br = bankroll_current(conn, filters.get("currency"), filters)
    print(f"Текущий банкролл: {br:.2f} {filters.get('currency','')}")
    print("=" * 60)


# ------------------------------
# Графики
# ------------------------------

def graph_profit_cum(conn: sqlite3.Connection, filters: Dict[str, Any], out_dir: Path) -> Path:
    if not HAS_MPL:
        raise RuntimeError(DEFAULT_MESSAGES["ru"]["no_mpl"])
    where_c, args_c = filters_where(filters, "c")
    where_t, args_t = filters_where(filters, "t")

    # Собираем все даты и профиты (cash + mtt) по дням
    q_cash = f"SELECT date, COALESCE(SUM(cashout - buyin),0) as p FROM cash_sessions c {where_c} GROUP BY date ORDER BY date"
    q_mtt = f"SELECT date, COALESCE(SUM(prize - (buyin + COALESCE(fee,0))),0) as p FROM tournaments t {where_t} GROUP BY date ORDER BY date"

    d2p: Dict[str, float] = {}
    for d, p in conn.execute(q_cash, args_c):
        d2p[d] = d2p.get(d, 0.0) + float(p or 0.0)
    for d, p in conn.execute(q_mtt, args_t):
        d2p[d] = d2p.get(d, 0.0) + float(p or 0.0)

    dates = sorted(d2p.keys())
    cum = []
    s = 0.0
    for d in dates:
        s += d2p[d]
        cum.append(s)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "graph_profit_cum.png"
    plt.figure()
    plt.plot(dates, cum, marker="o")
    plt.title("Кумулятивный профит")
    plt.xlabel("Дата")
    plt.ylabel(f"Профит, {filters.get('currency','')}")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    return out_path


def graph_bankroll(conn: sqlite3.Connection, filters: Dict[str, Any], out_dir: Path) -> Path:
    if not HAS_MPL:
        raise RuntimeError(DEFAULT_MESSAGES["ru"]["no_mpl"])
    currency = filters.get("currency")

    # Движения банкролла по датам
    tx: Dict[str, float] = {}
    if currency:
        cur = conn.execute("SELECT date, SUM(amount) FROM bankroll_tx WHERE currency=? GROUP BY date ORDER BY date", (currency,))
    else:
        cur = conn.execute("SELECT date, SUM(amount) FROM bankroll_tx GROUP BY date ORDER BY date")
    for d, s in cur.fetchall():
        tx[d] = float(s or 0.0)

    # Профиты по датам
    where_c, args_c = filters_where(filters, "c")
    where_t, args_t = filters_where(filters, "t")
    cash = {d: float(p or 0.0) for d, p in conn.execute(
        f"SELECT date, SUM(cashout - buyin) FROM cash_sessions c {where_c} GROUP BY date ORDER BY date", args_c)}
    mtt = {d: float(p or 0.0) for d, p in conn.execute(
        f"SELECT date, SUM(prize - (buyin + COALESCE(fee,0))) FROM tournaments t {where_t} GROUP BY date ORDER BY date", args_t)}

    all_dates = sorted(set().union(tx.keys(), cash.keys(), mtt.keys()))
    br_vals = []
    s = 0.0
    for d in all_dates:
        s += tx.get(d, 0.0) + cash.get(d, 0.0) + mtt.get(d, 0.0)
        br_vals.append(s)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "graph_bankroll.png"
    plt.figure()
    plt.plot(all_dates, br_vals, marker="o")
    plt.title("Банкролл по датам")
    plt.xlabel("Дата")
    plt.ylabel(f"Сумма, {currency or ''}")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    return out_path


def graph_cash_winrate(conn: sqlite3.Connection, filters: Dict[str, Any], out_dir: Path, group: str = "month") -> Path:
    if not HAS_MPL:
        raise RuntimeError(DEFAULT_MESSAGES["ru"]["no_mpl"])
    where, args = filters_where(filters, "c")
    cur = conn.execute(
        f"SELECT date, stakes_bb, hands, (cashout - buyin) as profit FROM cash_sessions c {where}", args
    )
    buckets: Dict[str, Tuple[float, int]] = {}  # bucket -> (sum_bb, sum_hands)
    for d, stakes, hands, profit in cur.fetchall():
        if not hands or not stakes:
            continue
        # определяем бакет
        y, m, _ = d.split("-")
        bucket = f"{y}-{m}" if group == "month" else f"{d}"
        bb = float(profit or 0.0) / float(stakes)
        sum_bb, sum_h = buckets.get(bucket, (0.0, 0))
        buckets[bucket] = (sum_bb + bb * hands, sum_h + int(hands))

    xs = sorted(buckets.keys())
    vals = []
    for k in xs:
        sum_bb, sum_h = buckets[k]
        if sum_h > 0:
            vals.append((sum_bb / sum_h) * 100.0)
        else:
            vals.append(0.0)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "graph_cash_winrate.png"
    plt.figure()
    plt.bar(xs, vals)
    plt.title(f"Winrate bb/100 по {('месяцам' if group=='month' else 'дням')}")
    plt.xlabel("Период")
    plt.ylabel("bb/100")
    plt.grid(True, axis="y")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    return out_path


def graph_mtt_roi(conn: sqlite3.Connection, filters: Dict[str, Any], out_dir: Path) -> Path:
    if not HAS_MPL:
        raise RuntimeError(DEFAULT_MESSAGES["ru"]["no_mpl"])
    where, args = filters_where(filters, "t")
    cur = conn.execute(
        f"SELECT date, prize, (buyin + COALESCE(fee,0)) FROM tournaments t {where}", args
    )
    buckets: Dict[str, Tuple[float, float]] = {}  # ym -> (prize_sum, invested_sum)
    for d, prize, invested in cur.fetchall():
        y, m, _ = d.split("-")
        key = f"{y}-{m}"
        ps, inv = buckets.get(key, (0.0, 0.0))
        buckets[key] = (ps + float(prize or 0.0), inv + float(invested or 0.0))

    xs = sorted(buckets.keys())
    vals = []
    for k in xs:
        ps, inv = buckets[k]
        if inv > 0:
            vals.append((ps - inv) / inv * 100.0)
        else:
            vals.append(0.0)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "graph_mtt_roi.png"
    plt.figure()
    plt.bar(xs, vals)
    plt.title("ROI по месяцам, %")
    plt.xlabel("Месяц")
    plt.ylabel("ROI, %")
    plt.grid(True, axis="y")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    return out_path


# ------------------------------
# Экспорт
# ------------------------------

def export_data(conn: sqlite3.Connection, what: str, fmt: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump_table(name: str) -> List[sqlite3.Row]:
        cur = conn.execute(f"SELECT * FROM {name}")
        return cur.fetchall()

    def write_csv(path: Path, rows: List[sqlite3.Row]) -> None:
        if not rows:
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write("")
            return
        headers = rows[0].keys()
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for r in rows:
                w.writerow(dict(r))

    def write_json(path: Path, obj: Any) -> None:
        with path.open("w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    tables = []
    if what in ("cash", "all"):
        rows = dump_table("cash_sessions")
        (out_dir / "cash.csv").unlink(missing_ok=True)
        (out_dir / "cash.json").unlink(missing_ok=True)
        if fmt == "csv":
            write_csv(out_dir / "cash.csv", rows)
        else:
            write_json(out_dir / "cash.json", [dict(r) for r in rows])
        tables.append("cash_sessions")

    if what in ("mtt", "all"):
        rows = dump_table("tournaments")
        if fmt == "csv":
            write_csv(out_dir / "tournaments.csv", rows)
        else:
            write_json(out_dir / "tournaments.json", [dict(r) for r in rows])
        tables.append("tournaments")

    if what in ("tx", "all"):
        rows = dump_table("bankroll_tx")
        if fmt == "csv":
            write_csv(out_dir / "bankroll_tx.csv", rows)
        else:
            write_json(out_dir / "bankroll_tx.json", [dict(r) for r in rows])
        tables.append("bankroll_tx")

    # summary.json
    if what in ("all",):
        filters = {}
        cur = bankroll_current(conn, None, filters)
        summary = {
            "generated_at": dt.datetime.utcnow().isoformat(),
            "bankroll_total": cur,
        }
        write_json(out_dir / "summary.json", summary)

    print(msg(conn, "export_done", path=str(out_dir)))


# ------------------------------
# Прогноз до цели
# ------------------------------

def forecast(conn: sqlite3.Connection, target: float, start: Optional[str], end: Optional[str], lookback_days: int = 30) -> None:
    # Текущий банкролл без фильтров по датам/руму (для простоты)
    br = bankroll_current(conn, None, {})
    # Оценим дневной темп по последним N дням
    to_date = parse_date(end) if end else dt.date.today()
    from_date = parse_date(start) if start else (to_date - dt.timedelta(days=lookback_days - 1))

    filters = {"from": from_date.strftime(DATE_FMT), "to": to_date.strftime(DATE_FMT)}
    c = stats_cash(conn, filters)
    m = stats_mtt(conn, filters)
    daily_pace = (c["profit"] + m["profit"]) / max(1, (to_date - from_date).days + 1)

    print("ПРОГНОЗ ДО ЦЕЛИ")
    print(f"  Текущий банкролл: {br:.2f}")
    print(f"  Цель:             {target:.2f}")
    print(f"  Окно оценки:      {from_date} .. {to_date}")
    print(f"  Средний темп/день:{daily_pace:.2f}")

    if daily_pace <= 0:
        print(DEFAULT_MESSAGES["ru"]["forecast_unreachable"])
        return
    need = target - br
    if need <= 0:
        print("Цель уже достигнута или превышена.")
        return
    days = math.ceil(need / daily_pace)
    eta = to_date + dt.timedelta(days=days)
    print(f"  Оценка дней до цели: {days} (дата достижения: {eta})")


# ------------------------------
# Рейкбек
# ------------------------------

def rakeback_calc(conn: sqlite3.Connection, start: Optional[str], end: Optional[str]) -> None:
    cfg = fetch_config(conn).get(CFG_RAKEBACK, DEFAULT_RAKEBACK)
    currency = cfg.get("currency", "RUB")
    tiers = sorted(cfg.get("tiers", []), key=lambda x: float(x["min_rake"]))
    filters = {"from": start, "to": end, "currency": currency}
    # суммируем рейк в кэше
    where_c, args_c = filters_where(filters, "c")
    cur = conn.execute(f"SELECT COALESCE(SUM(rake),0) FROM cash_sessions c {where_c}", args_c)
    rake_cash = float(cur.fetchone()[0] or 0.0)
    # суммируем рейк в турнирах (если есть отдельное поле)
    where_t, args_t = filters_where(filters, "t")
    cur = conn.execute(f"SELECT COALESCE(SUM(rake),0) FROM tournaments t {where_t}", args_t)
    rake_mtt = float(cur.fetchone()[0] or 0.0)
    rake_total = rake_cash + rake_mtt

    rate = 0.0
    minr_applied = 0.0
    for tier in tiers:
        if rake_total >= float(tier.get("min_rake", 0.0)):
            rate = float(tier.get("rate", 0.0))
            minr_applied = float(tier.get("min_rake", 0.0))
        else:
            break
    rb = rake_total * rate
    print(msg(conn, "rakeback_report", rake=rake_total, cur=currency, rate=rate, rb=rb, minr=minr_applied))


# ------------------------------
# CLI
# ------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=APP_NAME, description="Банкролл-трекер для Pokerdom (один файл).")
    p.add_argument("--db", default=DEFAULT_DB, help=f"Путь к SQLite (по умолчанию {DEFAULT_DB})")
    p.add_argument("-v", "--verbose", action="store_true", help="Подробные логи")

    sub = p.add_subparsers(dest="command", required=True)

    # init
    sp = sub.add_parser("init", help="Инициализировать БД и дефолтный конфиг")

    # import-cash
    sp = sub.add_parser("import-cash", help="Импорт кэш-сессий из CSV/XLSX")
    sp.add_argument("--file", required=True, help="Путь к файлу CSV/XLSX")
    sp.add_argument("--map", help="JSON-сопоставление колонок (target->source)")
    sp.add_argument("--room", default="Pokerdom")
    sp.add_argument("--currency", help="Валюта по умолчанию")
    sp.add_argument("--dry-run", action="store_true")

    # import-mtt
    sp = sub.add_parser("import-mtt", help="Импорт турниров из CSV/XLSX")
    sp.add_argument("--file", required=True)
    sp.add_argument("--map")
    sp.add_argument("--room", default="Pokerdom")
    sp.add_argument("--currency")
    sp.add_argument("--dry-run", action="store_true")

    # add cash
    sp = sub.add_parser("add-cash", help="Добавить одну кэш-сессию")
    sp.add_argument("--date", required=True)
    sp.add_argument("--stakes-bb", required=True)
    sp.add_argument("--hands", required=True)
    sp.add_argument("--buyin", required=True)
    sp.add_argument("--cashout", required=True)
    sp.add_argument("--currency", default="RUB")
    sp.add_argument("--rake")
    sp.add_argument("--notes")
    sp.add_argument("--room", default="Pokerdom")

    # add mtt
    sp = sub.add_parser("add-mtt", help="Добавить один турнир")
    sp.add_argument("--date", required=True)
    sp.add_argument("--buyin", required=True)
    sp.add_argument("--fee")
    sp.add_argument("--prize", required=True)
    sp.add_argument("--position")
    sp.add_argument("--field-size")
    sp.add_argument("--currency", default="RUB")
    sp.add_argument("--rake")
    sp.add_argument("--notes")
    sp.add_argument("--room", default="Pokerdom")

    # add tx
    sp = sub.add_parser("add-tx", help="Добавить движение банкролла (deposit/withdraw/bonus/adjustment)")
    sp.add_argument("--date", required=True)
    sp.add_argument("--amount", required=True)
    sp.add_argument("--type", required=True, choices=["deposit", "withdraw", "bonus", "adjustment"])
    sp.add_argument("--currency", default="RUB")
    sp.add_argument("--notes")

    # stats
    sp = sub.add_parser("stats", help="Показать сводную статистику")
    sp.add_argument("--from", dest="from_", help="Дата с (YYYY-MM-DD)")
    sp.add_argument("--to", help="Дата по (YYYY-MM-DD)")
    sp.add_argument("--room", default="Pokerdom")
    sp.add_argument("--currency", default="RUB")

    # graph
    sp = sub.add_parser("graph", help="Сформировать графики (PNG)")
    sp.add_argument("--type", required=True, choices=["profit_cum", "bankroll", "cash_winrate", "mtt_roi"])
    sp.add_argument("--out", required=True, help="Папка для сохранения")
    sp.add_argument("--group", choices=["day", "month"], default="month", help="Группировка для cash_winrate")

    # export
    sp = sub.add_parser("export", help="Экспорт данных")
    sp.add_argument("--what", required=True, choices=["cash", "mtt", "tx", "all"])
    sp.add_argument("--format", required=True, choices=["csv", "json"])
    sp.add_argument("--out", required=True)

    # forecast
    sp = sub.add_parser("forecast", help="Прогноз достижения цели банкролла")
    sp.add_argument("--target", required=True, type=float)
    sp.add_argument("--from", dest="from_", help="Дата начала окна (по умолчанию последние 30 дней)")
    sp.add_argument("--to", help="Дата окончания окна")

    # rakeback
    sp = sub.add_parser("rakeback", help="Расчёт кэшбека по текущей модели")
    sp.add_argument("--from", dest="from_", help="Дата с")
    sp.add_argument("--to", help="Дата по")

    # config
    sp = sub.add_parser("config", help="Показать/изменить конфиг")
    sp.add_argument("--set", dest="set_kv", action="append", help="key=value (значение можно в JSON)")
    sp.add_argument("--get", dest="get_key", help="Ключ для чтения")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser().resolve()
    # Временное соединение для чтения/записи конфига (чтобы знать log_file/lang)
    conn_tmp = connect_db(db_path)
    try:
        init_db(conn_tmp)  # безопасно, если уже есть
    except Exception:
        # игнорируем
        pass
    cfg = fetch_config(conn_tmp)
    lang = cfg.get(CFG_LANG, "ru") if isinstance(cfg.get(CFG_LANG), str) else "ru"
    log_file = cfg.get(CFG_LOG_FILE) if isinstance(cfg.get(CFG_LOG_FILE), str) else None
    setup_logging(args.verbose, db_path, lang, log_file)

    # Основное соединение
    conn = connect_db(db_path)

    if args.command == "init":
        init_db(conn)
        print(msg(conn, "db_created"))
        return 0

    if args.command == "import-cash":
        mapping = json.loads(args.map) if args.map else None
        import_cash(conn, Path(args.file), mapping, args.room, args.currency, args.dry_run)
        return 0

    if args.command == "import-mtt":
        mapping = json.loads(args.map) if args.map else None
        import_mtt(conn, Path(args.file), mapping, args.room, args.currency, args.dry_run)
        return 0

    if args.command == "add-cash":
        add_cash(conn, **vars(args))
        return 0

    if args.command == "add-mtt":
        add_mtt(conn, **vars(args))
        return 0

    if args.command == "add-tx":
        add_tx(conn, **vars(args))
        return 0

    if args.command == "stats":
        filters = {
            "from": getattr(args, "from_"),
            "to": getattr(args, "to"),
            "room": getattr(args, "room"),
            "currency": getattr(args, "currency"),
        }
        print_stats(conn, filters)
        return 0

    if args.command == "graph":
        filters = {"room": "Pokerdom"}  # минимальные фильтры; можно расширить через доп. флаги при желании
        out_dir = Path(args.out)
        if args.type == "profit_cum":
            path = graph_profit_cum(conn, filters, out_dir)
        elif args.type == "bankroll":
            path = graph_bankroll(conn, filters, out_dir)
        elif args.type == "cash_winrate":
            path = graph_cash_winrate(conn, filters, out_dir, args.group)
        elif args.type == "mtt_roi":
            path = graph_mtt_roi(conn, filters, out_dir)
        else:
            parser.error("Неизвестный тип графика.")
            return 2
        print(msg(conn, "graph_saved", path=str(path)))
        return 0

    if args.command == "export":
        export_data(conn, args.what, args.format, Path(args.out))
        return 0

    if args.command == "forecast":
        forecast(conn, args.target, getattr(args, "from_"), getattr(args, "to"))
        return 0

    if args.command == "rakeback":
        rakeback_calc(conn, getattr(args, "from_"), getattr(args, "to"))
        return 0

    if args.command == "config":
        if args.set_kv:
            updates = {}
            for kv in args.set_kv:
                if "=" not in kv:
                    parser.error("--set ожидает key=value")
                k, v = kv.split("=", 1)
                v = v.strip()
                # пытаемся разобрать JSON, иначе как строку
                try:
                    updates[k] = json.loads(v)
                except Exception:
                    updates[k] = v
            set_config(conn, updates)
            print(msg(conn, "config_set"))
            return 0
        if args.get_key:
            cfg = fetch_config(conn)
            print(json.dumps({args.get_key: cfg.get(args.get_key)}, ensure_ascii=False, indent=2))
            return 0
        # показать весь конфиг
        cfg = fetch_config(conn)
        print(msg(conn, "config_show"))
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0

    parser.error("Неизвестная команда")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)