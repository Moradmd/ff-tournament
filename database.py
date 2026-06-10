import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if DATABASE_URL:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    IS_PG = True
else:
    IS_PG = False

if IS_PG:
    DB_PATH = None
elif os.getenv("DATABASE_PATH"):
    DB_PATH = Path(os.getenv("DATABASE_PATH"))
elif os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"):
    DB_PATH = Path("/tmp/tournament.db")
else:
    DB_PATH = Path(__file__).parent / "tournament.db"

SLOT_COUNT = 12
SQUAD_SIZE = 4


_PG_NOW = "TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"


def _pg_translate(sql):
    sql = sql.replace("?", "%s")
    sql = re.sub(
        r"datetime\('now',\s*'([^']+)'\)",
        lambda m: f"CURRENT_TIMESTAMP - INTERVAL '{m.group(1).lstrip('-+')}'"
        if m.group(1).startswith("-")
        else f"CURRENT_TIMESTAMP + INTERVAL '{m.group(1).lstrip('-+')}'",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(r"datetime\('now'\)", _PG_NOW, sql, flags=re.IGNORECASE)
    sql = re.sub(r"datetime\((\w+(?:\.\w+)?)\)", r"\1", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"SELECT\s+last_insert_rowid\s*\(\)",
        "SELECT lastval()",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(r"PRAGMA\s+\w+(?:\s*=\s*\w+)?", "", sql, flags=re.IGNORECASE)
    return sql


class _Cursor:
    def __init__(self, cur, is_pg):
        self._cur = cur
        self._is_pg = is_pg

    @property
    def lastrowid(self):
        if self._is_pg:
            if self._cur.description:
                row = self._cur.fetchone()
                if row:
                    vals = list(row.values())
                    return vals[0] if vals else None
            return None
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)


class _Connection:
    def __init__(self, conn, is_pg):
        self._conn = conn
        self._is_pg = is_pg

    def execute(self, sql, params=None):
        if params is None:
            params = ()
        if self._is_pg:
            sql = _pg_translate(sql)
            stripped = sql.strip().upper()
            if (
                stripped.startswith("INSERT")
                and "RETURNING" not in stripped
                and "lastval" not in stripped
            ):
                sql += " RETURNING id"
            cur = self._conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
        else:
            cur = self._conn.execute(sql, params)
        return _Cursor(cur, self._is_pg)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def rollback(self):
        self._conn.rollback()


@contextmanager
def get_db():
    if IS_PG:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        conn.autocommit = False
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
    wrapped = _Connection(conn, IS_PG)
    try:
        yield wrapped
        wrapped.commit()
    except Exception:
        wrapped.rollback()
        raise
    finally:
        wrapped.close()


def init_db():
    if not IS_PG:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        if IS_PG:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tournaments (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    join_token TEXT UNIQUE,
                    created_at TEXT DEFAULT TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                )
                """
            )
            conn.execute(
                """
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
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS members (
                    id SERIAL PRIMARY KEY,
                    slot_id INTEGER NOT NULL REFERENCES slots(id),
                    position INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    uid TEXT,
                    UNIQUE (slot_id, position)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                    status TEXT NOT NULL DEFAULT 'pending_approval',
                    squad_name TEXT,
                    leader_contact TEXT NOT NULL,
                    payment_method TEXT NOT NULL,
                    payment_trx TEXT NOT NULL,
                    assigned_slot_id INTEGER,
                    created_at TEXT DEFAULT TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
                    reviewed_at TEXT,
                    reject_reason TEXT,
                    FOREIGN KEY (assigned_slot_id) REFERENCES slots(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_members (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER NOT NULL REFERENCES orders(id),
                    position INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    uid TEXT,
                    input_type TEXT NOT NULL DEFAULT 'name',
                    UNIQUE (order_id, position)
                )
                """
            )
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    join_token TEXT UNIQUE,
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

                CREATE TABLE IF NOT EXISTS members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slot_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    uid TEXT,
                    FOREIGN KEY (slot_id) REFERENCES slots(id),
                    UNIQUE (slot_id, position)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_approval',
                    squad_name TEXT,
                    leader_contact TEXT NOT NULL,
                    payment_method TEXT NOT NULL,
                    payment_trx TEXT NOT NULL,
                    assigned_slot_id INTEGER,
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
                """
            )
        _migrate(conn)


def _add_column(conn, table, column, col_type):
    if IS_PG:
        cols = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,),
            ).fetchall()
        }
    else:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _migrate(conn):
    _add_column(conn, "tournaments", "join_token", "TEXT")
    _add_column(conn, "tournaments", "join_epoch", "INTEGER DEFAULT 1")
    _add_column(conn, "tournaments", "room_id", "TEXT")
    _add_column(conn, "tournaments", "room_pass", "TEXT")
    _add_column(conn, "tournaments", "whatsapp_group_link", "TEXT")
    _add_column(conn, "tournaments", "room_available_at", "TEXT")
    _add_column(conn, "tournaments", "tournament_time", "TEXT")
    _add_column(conn, "tournaments", "room_info", "TEXT")
    _add_column(conn, "orders", "gateway_tran_id", "TEXT")
    _add_column(conn, "orders", "auto_approved", "INTEGER DEFAULT 0")
    _add_column(conn, "orders", "view_token", "TEXT")
    _add_column(conn, "orders", "player_changes_left", "INTEGER DEFAULT 2")

    for col, typ in (
        ("order_id", "INTEGER"),
        ("registered_at", "TEXT"),
        ("leader_contact", "TEXT"),
        ("squad_name", "TEXT"),
        ("status", "TEXT"),
    ):
        _add_column(conn, "slots", col, typ)

    if not IS_PG:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_approval',
                squad_name TEXT,
                leader_contact TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                payment_trx TEXT NOT NULL,
                assigned_slot_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                reviewed_at TEXT,
                reject_reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                uid TEXT,
                input_type TEXT NOT NULL DEFAULT 'name',
                UNIQUE (order_id, position)
            )
            """
        )


def ensure_default_tournament():
    import secrets

    with get_db() as conn:
        row = conn.execute("SELECT * FROM tournaments LIMIT 1").fetchone()
        if row:
            if not row["join_token"]:
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


def get_tournament(conn):
    return conn.execute("SELECT * FROM tournaments LIMIT 1").fetchone()


def next_empty_slot(conn, tournament_id):
    return conn.execute(
        """
        SELECT * FROM slots
        WHERE tournament_id = ? AND status = 'empty'
        ORDER BY slot_number
        LIMIT 1
        """,
        (tournament_id,),
    ).fetchone()


def reserve_slot(conn, slot_id, order_id, squad_name, leader_contact):
    cur = conn.execute(
        """
        UPDATE slots
        SET status = 'reserved',
            order_id = ?,
            squad_name = ?,
            leader_contact = ?
        WHERE id = ? AND status = 'empty'
        """,
        (order_id, squad_name, leader_contact, slot_id),
    )
    return cur.rowcount > 0


def release_slot_reservation(conn, slot_id):
    conn.execute("DELETE FROM members WHERE slot_id = ?", (slot_id,))
    conn.execute(
        """
        UPDATE slots
        SET status = 'empty',
            squad_name = NULL,
            leader_contact = NULL,
            order_id = NULL,
            registered_at = NULL
        WHERE id = ? AND status IN ('reserved', 'registered')
        """,
        (slot_id,),
    )


def fail_pending_payment(conn, order_id):
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order or order["status"] != "pending_payment":
        return False
    if order["assigned_slot_id"]:
        release_slot_reservation(conn, order["assigned_slot_id"])
    conn.execute(
        "UPDATE orders SET status = 'payment_failed', reviewed_at = datetime('now') WHERE id = ?",
        (order_id,),
    )
    return True


def complete_gateway_payment(conn, order_id, payment_trx, payment_method):
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order or order["status"] != "pending_payment":
        return False
    conn.execute(
        """
        UPDATE orders
        SET status = 'pending_approval',
            payment_trx = ?,
            payment_method = ?
        WHERE id = ?
        """,
        (payment_trx, payment_method, order_id),
    )
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    slot = next_empty_slot(conn, order["tournament_id"])
    if slot:
        reserve_slot(conn, slot["id"], order_id, order["squad_name"], order["leader_contact"])
        conn.execute(
            "UPDATE orders SET assigned_slot_id = ? WHERE id = ?",
            (slot["id"], order_id),
        )
        members = conn.execute(
            "SELECT * FROM order_members WHERE order_id = ? ORDER BY position",
            (order_id,),
        ).fetchall()
        conn.execute("DELETE FROM members WHERE slot_id = ?", (slot["id"],))
        for m in members:
            conn.execute(
                "INSERT INTO members (slot_id, position, display_name, uid) VALUES (?, ?, ?, ?)",
                (slot["id"], m["position"], m["display_name"], m["uid"]),
            )
    return True


def approve_order_auto(conn, order_id):
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return False
    if order["status"] == "approved":
        return True
    if order["status"] != "pending_approval":
        return False
    if order["assigned_slot_id"]:
        slot = conn.execute("SELECT * FROM slots WHERE id = ?", (order["assigned_slot_id"],)).fetchone()
    else:
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
        """
        UPDATE slots
        SET status = 'registered',
            squad_name = ?,
            leader_contact = ?,
            order_id = ?,
            registered_at = datetime('now')
        WHERE id = ? AND status IN ('empty', 'reserved')
        """,
        (order["squad_name"], order["leader_contact"], order_id, slot_id),
    )
    if cur.rowcount <= 0:
        return False
    conn.execute(
        """
        UPDATE orders
        SET status = 'approved',
            assigned_slot_id = ?,
            reviewed_at = datetime('now'),
            auto_approved = 1
        WHERE id = ?
        """,
        (slot_id, order_id),
    )
    return True


def count_filled_players(conn, tournament_id):
    return conn.execute(
        """
        SELECT COUNT(*) AS c FROM members m
        JOIN slots s ON s.id = m.slot_id
        WHERE s.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchone()["c"]


def _insert_order(
    conn,
    tournament_id,
    assigned_slot_id,
    squad_name,
    leader_contact,
    status,
    payment_method,
    payment_trx,
    members,
):
    cur = conn.execute(
        """
        INSERT INTO orders (tournament_id, status, squad_name, leader_contact,
                            payment_method, payment_trx, assigned_slot_id, player_changes_left)
        VALUES (?, ?, ?, ?, ?, ?, ?, 2)
        """,
        (
            tournament_id,
            status,
            squad_name,
            leader_contact,
            payment_method,
            payment_trx,
            assigned_slot_id,
        ),
    )
    order_id = cur.lastrowid
    for m in members:
        conn.execute(
            """
            INSERT INTO order_members (order_id, position, display_name, uid, input_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, m["position"], m["display_name"], m["uid"], m["input_type"]),
        )
    return order_id
