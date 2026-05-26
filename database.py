import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

if os.getenv("DATABASE_PATH"):
    DB_PATH = Path(os.getenv("DATABASE_PATH"))
elif os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"):
    DB_PATH = Path("/tmp/tournament.db")
else:
    DB_PATH = Path(__file__).parent / "tournament.db"
SLOT_COUNT = 12
SQUAD_SIZE = 4


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
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

    # orders / order_members — purono DB te table na thakle CREATE IF NOT EXISTS already ran
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
            # Existing DB may have NULL after migration; set default epoch
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
        except sqlite3.IntegrityError:
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
    # Reserve slot for immediate lobby display
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    slot = next_empty_slot(conn, order["tournament_id"])
    if slot:
        reserve_slot(conn, slot["id"], order_id, order["squad_name"], order["leader_contact"])
        conn.execute(
            "UPDATE orders SET assigned_slot_id = ? WHERE id = ?",
            (slot["id"], order_id),
        )
        # Copy player names to members table so lobby shows them
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
    """Auto-approve order (used for gateway payment success)."""
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return False

    # Only approve after payment is verified (pending_approval) or already approved
    if order["status"] == "approved":
        return True
    if order["status"] != "pending_approval":
        return False

    # Use existing reserved slot if available, otherwise pick next empty
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

    # If slot got taken between select and update, abort (caller can handle)
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
