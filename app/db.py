"""SQLite 数据层：联系人、运行日志、全局配置。"""
import json
import os
import sqlite3
import sys
import threading
import time
from typing import Any, Optional

# 打包成 exe 后 __file__ 指向临时解包目录，数据必须落在 exe 同级的可写目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "douyin.db")

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname    TEXT NOT NULL,             -- 抖音会话列表里显示的名字
                remark      TEXT DEFAULT '',           -- 备注名（仅控制台展示用）
                message     TEXT NOT NULL,             -- 续火发送的内容
                send_time   TEXT NOT NULL DEFAULT '09:00',  -- 每日发送时间 HH:MM
                enabled     INTEGER NOT NULL DEFAULT 1,
                last_sent   TEXT DEFAULT '',           -- 最近一次成功发送日期 YYYY-MM-DD
                streak      INTEGER NOT NULL DEFAULT 0,-- 连续成功天数
                created_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS logs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      REAL NOT NULL,
                level   TEXT NOT NULL,
                contact TEXT DEFAULT '',
                text    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # 轻量迁移：flame_* 为后加字段（抖音侧真实火花状态，与本地 streak 区分）
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(contacts)")}
        if "flame_days" not in cols:
            conn.execute(
                "ALTER TABLE contacts ADD COLUMN flame_days INTEGER NOT NULL DEFAULT 0"
            )
        if "flame_state" not in cols:
            conn.execute(
                "ALTER TABLE contacts ADD COLUMN flame_state TEXT NOT NULL DEFAULT ''"
            )
        if "flame_reburn" not in cols:
            conn.execute(
                "ALTER TABLE contacts ADD COLUMN flame_reburn TEXT NOT NULL DEFAULT ''"
            )


def set_flame(nickname: str, days: int, state: str = "", reburn: str = "") -> bool:
    """按昵称更新抖音火花状态。联系人存在返回 True。"""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE contacts SET flame_days = ?, flame_state = ?, flame_reburn = ? "
            "WHERE nickname = ?",
            (days, state, reburn, nickname),
        )
        return cur.rowcount > 0


# ---------- 联系人 ----------

def add_contact(nickname: str, message: str, send_time: str, remark: str = "") -> int:
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO contacts (nickname, remark, message, send_time, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nickname, remark, message, send_time, time.time()),
        )
        return cur.lastrowid


def update_contact(cid: int, **fields: Any) -> None:
    allowed = {"nickname", "remark", "message", "send_time", "enabled"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    sets = ", ".join(f"{k} = ?" for k in data)
    with _lock, _conn() as conn:
        conn.execute(f"UPDATE contacts SET {sets} WHERE id = ?", (*data.values(), cid))


def delete_contact(cid: int) -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))


def list_contacts() -> list[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT * FROM contacts ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_contact(cid: int) -> Optional[dict]:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def enabled_contacts() -> list[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM contacts WHERE enabled = 1 ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(cid: int, ok: bool) -> None:
    """发送成功则更新 last_sent / streak；失败则断掉连击。"""
    today = time.strftime("%Y-%m-%d")
    with _lock, _conn() as conn:
        row = conn.execute("SELECT last_sent, streak FROM contacts WHERE id = ?", (cid,)).fetchone()
        if not row:
            return
        if ok:
            streak = row["streak"] + 1 if row["last_sent"] != today else row["streak"]
            conn.execute(
                "UPDATE contacts SET last_sent = ?, streak = ? WHERE id = ?",
                (today, streak, cid),
            )
        else:
            conn.execute("UPDATE contacts SET streak = 0 WHERE id = ?", (cid,))


# ---------- 日志 ----------

def add_log(level: str, text: str, contact: str = "") -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO logs (ts, level, contact, text) VALUES (?, ?, ?, ?)",
            (time.time(), level, contact, text),
        )


def list_logs(limit: int = 200, after_id: int = 0) -> list[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM logs WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_logs() -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM logs")


# ---------- 配置 ----------

def get_setting(key: str, default: str = "") -> str:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def export_state() -> dict:
    """控制台一次性拉全量数据。"""
    return {
        "contacts": list_contacts(),
        "logs": list_logs(limit=100),
        "logged_in": os.path.exists(os.path.join(BASE_DIR, "user_data", "storage_state.json")),
    }


if __name__ == "__main__":
    init_db()
    print("db ready:", DB_PATH)
