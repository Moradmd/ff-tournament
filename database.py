import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

USING_PG = bool(os.getenv("DATABASE_URL"))

if USING_PG:
    import psycopg2
    from psycopg2 import sql as psql
    from psycopg2.extras import RealDictCursor

    def _pg_ddl():
        return """
            CREATE TABLE IF NOT EXISTS tournaments (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                join_token TEXT,
                join_epoch INTEGER DEFAULT 1,
                room_id TEXT,
                room_pass TEXT,
                whatsapp_group_link TEXT,
                room_available_at TEXT,
                tournament_time TEXT,
                room_info TEXT,
                created_at TEXT DEFAULT (NOW())
            );
            CREATE TABLE IF NOT EXISTS slots (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                slot_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'empty',
                squad_name TEXT,
                leader_contact TEXT,
                order_id INTEGER,
                registered_at TEXT,
                UNIQUE (tournament_id, slot_number)
            );
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                status TEXT NOT NULL DEFAULT 'pending_approval',
                squad_name TEXT,
                leader_contact TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                payment_trx TEXT NOT NULL,
                gateway_tran_id TEXT,
                assigned_slot_id INTEGER REFERENCES slots(id),
                auto_approved INTEGER DEFAULT 0,
                view_token TEXT,
                player_changes_left INTEGER DEFAULT 2,
                created_at TEXT DEFAULT (NOW()),
                reviewed_at TEXT,
                reject_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS order_members (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id),
                position INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                uid TEXT,
                input_type TEXT NOT NULL DEFAULT 'name',
                UNIQUE (order_id, position)
            );
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                slot_id INTEGER NOT NULL REFERENCES slots(id),
                position INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                uid TEXT,
                UNIQUE (slot_id, position)
            );
        """

else:
    if os.getenv("DATABASE_PATH"):
        DB_PATH = Path(os.getenv("DATABASE_PATH"))
    elif os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"):
        DB_PATH = Path("/tmp/tournament.db")
    else:
        DB_PATH = Path(__file__).parent / "tournament.db"

    def _sqlite_ddl():
        return """
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                join_token TEXT,
                join_epoch INTEGER DEFAULT 1,
                room_id TEXT,
                room_pass TEXT,
                whatsapp_group_link TEXT,
                room_available_at TEXT,
                tournament_time TEXT,
                room_info TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                slot_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'empty',
                squad_name TEXT,
                leader_contact TEXT,
                order_id INTEGER,
                registered_at TEXT,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id),
                UNIQUE (tournament_id, slot_number)
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_approval',
                squad_name TEXT,
                leader_contact TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                payment_trx TEXT NOT NULL,
                gateway_tran_id TEXT,
                assigned_slot_id INTEGER,
                auto_approved INTEGER DEFAULT 0,
                view_token TEXT,
                player_changes_left INTEGER DEFAULT 2,
                created_at TEXT DEFAULT (datetime('now')),
                reviewed_at TEXT,
                reject_reason TEXT,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id),
                FOREIGN KEY (assigned_slot_id) REFERENCES slots(id)
            );
            CREATE TABLE IF NOT EXISTS order_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                uid TEXT,
                input_type TEXT NOT NULL DEFAULT 'name',
                FOREIGN KEY (order_id) REFERENCES orders(id),
                UNIQUE (order_id, position)
            );
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                uid TEXT,
                FOREIGN KEY (slot_id) REFERENCES slots(id),
                UNIQUE (slot_id, position)
            );
        """


# ─── Compatibility layer ──────────────────────────────────────────────

def _translate(sql):
    """Convert SQLite SQL to PostgreSQL syntax when needed."""
    if not USING_PG:
        return sql
    sql = sql.replace("datetime('now')", "NOW()")
    return sql


def _last_id(cursor):
    """Get last inserted row id — works for both SQLite and PostgreSQL."""
    if USING_PG:
        try:
            return cursor.fetchone()[0]
        except Exception:
            import traceback
            traceback.print_exc()
            return None
    return cursor.lastrowid


class CompatCursor:
    """Wraps a DB-API cursor to handle paramstyle differences."""

    def __init__(self, cursor):
        self._c = cursor
        self._lastrowid = None
        self._rowcount = -1

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def rowcount(self):
        return self._rowcount

    def execute(self, sql, params=None):
        sql = _translate(sql)
        if USING_PG:
            # Convert ? placeholders to %s for psycopg2
            sql = sql.replace("?", "%s")
        if params is None:
            self._c.execute(sql)
        else:
            if isinstance(params, (list, tuple)):
                self._c.execute(sql, params)
            else:
                self._c.execute(sql, params)
        self._rowcount = self._c.rowcount if hasattr(self._c, 'rowcount') else -1
        return self

    def fetchone(self):
        row = self._c.fetchone()
        if row is None:
            return None
        if USING_PG:
            return RealDictRow(row)
        return row

    def fetchall(self):
        rows = self._c.fetchall()
        if USING_PG:
            return [RealDictRow(r) for r in rows]
        return rows

    def __iter__(self):
        for row in self._c:
            if USING_PG:
                yield RealDictRow(row)
            else:
                yield row

    def __getattr__(self, name):
        return getattr(self._c, name)


class RealDictRow:
    """Mimics sqlite3.Row for PostgreSQL RealDictRow."""

    def __init__(self, row):
        self._row = row
        self._keys = list(row.keys()) if row else []

    def keys(self):
        return self._keys

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._row[key]
        return list(self._row.values())[key]

    def __contains__(self, key):
        return key in self._row

    def get(self, key, default=None):
        try:
            return self._row[key]
        except (KeyError, IndexError):
            return default

    def __repr__(self):
        return dict(self._row).__repr__()


# ─── Connection provider ──────────────────────────────────────────────

@contextmanager
def get_db():
    if USING_PG:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
        try:
            conn.autocommit = False
            yield _CompatConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield _CompatConnection(conn)
            conn.commit()
        finally:
            conn.close()


class _CompatConnection:
    """Wraps a DB-API connection to return CompatCursor."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cursor = self._conn.cursor()
        cc = CompatCursor(cursor)
        cc.execute(sql, params)
        if USING_PG:
            try:
                cc._lastrowid = _last_id(cursor)
            except Exception:
                cc._lastrowid = None
        else:
            cc._lastrowid = cursor.lastrowid
        return cc

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ─── Init & migration ─────────────────────────────────────────────────

def init_db():
    if not USING_PG:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_db() as conn:
        if not USING_PG:
            conn.execute("PRAGMA journal_mode=WAL")
        if USING_PG:
            conn.execute(_pg_ddl())
        else:
            conn.execute(_sqlite_ddl())
        _migrate(conn)


def _add_column_sqlite(conn, table, column, col_type):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _add_column_pg(conn, table, column, col_type):
    """ALTER TABLE ADD COLUMN IF NOT EXISTS for PostgreSQL."""
    cur = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
        (table, column),
    )
    if not cur.fetchone():
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _migrate(conn):
    if USING_PG:
        _ac = _add_column_pg
    else:
        _ac = _add_column_sqlite

    _ac(conn, "tournaments", "join_token", "TEXT")
    _ac(conn, "tournaments", "join_epoch", "INTEGER DEFAULT 1")
    _ac(conn, "tournaments", "room_id", "TEXT")
    _ac(conn, "tournaments", "room_pass", "TEXT")
    _ac(conn, "tournaments", "whatsapp_group_link", "TEXT")
    _ac(conn, "tournaments", "room_available_at", "TEXT")
    _ac(conn, "tournaments", "tournament_time", "TEXT")
    _ac(conn, "tournaments", "room_info", "TEXT")
    _ac(conn, "orders", "gateway_tran_id", "TEXT")
    _ac(conn, "orders", "auto_approved", "INTEGER DEFAULT 0")
    _ac(conn, "orders", "view_token", "TEXT")
    _ac(conn, "orders", "player_changes_left", "INTEGER DEFAULT 2")

    for col, typ in (
        ("order_id", "INTEGER"),
        ("registered_at", "TEXT"),
        ("leader_contact", "TEXT"),
        ("squad_name", "TEXT"),
        ("status", "TEXT"),
    ):
        _ac(conn, "slots", col, typ)

    # Re-run CREATE TABLE IF NOT EXISTS for safety
    if USING_PG:
        conn.execute(_pg_ddl())
    else:
        conn.execute(_sqlite_ddl())


def ensure_default_tournament():
    import secrets

    with get_db() as conn:
        row = conn.execute("SELECT * FROM tournaments LIMIT 1").fetchone()
        if row:
            if not row.get("join_token"):
                token = secrets.token_urlsafe(10)
                conn.execute(
                    "UPDATE tournaments SET join_token = ? WHERE id = ?",
                    (token, row["id"]),
                )
            try:
                if "join_epoch" in row.keys() and row["join_epoch"] is None:
                    conn.execute(
                        "UPDATE tournaments SET join_epoch = 1 WHERE id = ?",
                        (row["id"],),
                    )
            except Exception:
                pass
            return row["id"]

        try:
            token = secrets.token_urlsafe(10)
            if USING_PG:
                cur = conn.execute(
                    "INSERT INTO tournaments (name, join_token, join_epoch) VALUES (%s, %s, 1) RETURNING id",
                    ("Free Fire Custom Tournament", token),
                )
                tournament_id = cur.fetchone()[0]
            else:
                cur = conn.execute(
                    "INSERT INTO tournaments (name, join_token, join_epoch) VALUES (?, ?, 1)",
                    ("Free Fire Custom Tournament", token),
                )
                tournament_id = cur.lastrowid

            for n in range(1, SLOT_COUNT + 1):
                conn.execute(
                    "INSERT INTO slots (tournament_id, slot_number, status) VALUES (?, ?, 'empty')",
                    (tournament_id, n),
                )
            return tournament_id
        except Exception:
            row = conn.execute("SELECT * FROM tournaments LIMIT 1").fetchone()
            return row["id"] if row else None


# ─── Query helpers ────────────────────────────────────────────────────

SLOT_COUNT = 12
SQUAD_SIZE = 4


def get_tournament(conn):
    return conn.execute("SELECT * FROM tournaments LIMIT 1").fetchone()


def next_empty_slot(conn, tournament_id):
    return conn.execute(
        "SELECT * FROM slots WHERE tournament_id = ? AND status = 'empty' ORDER BY slot_number LIMIT 1",
        (tournament_id,),
    ).fetchone()


def reserve_slot(conn, slot_id, order_id, squad_name, leader_contact):
    cur = conn.execute(
        "UPDATE slots SET status = 'reserved', order_id = ?, squad_name = ?, leader_contact = ? WHERE id = ? AND status = 'empty'",
        (order_id, squad_name, leader_contact, slot_id),
    )
    return cur.rowcount > 0


def release_slot_reservation(conn, slot_id):
    conn.execute(
        "UPDATE slots SET status = 'empty', order_id = NULL, squad_name = NULL, leader_contact = NULL WHERE id = ?",
        (slot_id,),
    )


def complete_gateway_payment(conn, order_id, payment_trx, payment_method):
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order or order["status"] != "pending_payment":
        return False
    conn.execute(
        "UPDATE orders SET status = 'pending_approval', payment_trx = ?, payment_method = ? WHERE id = ?",
        (payment_trx, payment_method, order_id),
    )
    return True


def fail_pending_payment(conn, order_id):
    conn.execute(
        "UPDATE orders SET status = 'payment_failed', reviewed_at = datetime('now') WHERE id = ? AND status = 'pending_payment'",
        (order_id,),
    )


def approve_order_auto(conn, order_id):
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return False

    if order["status"] == "approved":
        return True
    if order["status"] != "pending_approval":
        return False

    slot = next_empty_slot(conn, order["tournament_id"])
    if not slot:
        return False
    slot_id = slot["id"]

    members = conn.execute(
        "SELECT * FROM order_members WHERE order_id = ? ORDER BY position",
        (order_id,),
    ).fetchall()

    conn.execute("DELETE FROM members WHERE slot_id = ?", (slot_id,))
    for m in members:
        conn.execute(
            "INSERT INTO members (slot_id, position, display_name, uid) VALUES (?, ?, ?, ?)",
            (slot_id, m["position"], m["display_name"], m["uid"]),
        )

    cur = conn.execute(
        "UPDATE slots SET status = 'registered', squad_name = ?, leader_contact = ?, order_id = ?, registered_at = datetime('now') WHERE id = ? AND status = 'empty'",
        (order["squad_name"], order["leader_contact"], order_id, slot_id),
    )
    if cur.rowcount <= 0:
        return False
    conn.execute(
        "UPDATE orders SET status = 'approved', assigned_slot_id = ?, reviewed_at = datetime('now'), auto_approved = 1 WHERE id = ?",
        (slot_id, order_id),
    )
    return True


def count_filled_players(conn, tournament_id):
    cur = conn.execute(
        "SELECT COUNT(*) AS c FROM members m JOIN slots s ON s.id = m.slot_id WHERE s.tournament_id = ?",
        (tournament_id,),
    )
    row = cur.fetchone()
    return row["c"] if row else 0
