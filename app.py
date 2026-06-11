import logging
import secrets
import threading
import traceback
from datetime import datetime
from urllib.parse import parse_qs, urlparse
import re
import requests as http_req

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from config import (
    ADMIN_PIN,
    AUTO_APPROVE_GATEWAY,
    BKASH_NUMBER,
    ENABLE_MANUAL_PAYMENT,
    ENTRY_FEE,
    FF_SERVER,
    HOST,
    NAGAD_NUMBER,
    PORT,
    PUBLIC_BASE_URL,
    SECRET_KEY,
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    SUPABASE_TABLE,
    WHATSAPP_GROUP_LINK,
)
from database import (
    SQUAD_SIZE,
    SLOT_COUNT,
    approve_order_auto,
    complete_gateway_payment,
    count_filled_players,
    ensure_default_tournament,
    fail_pending_payment,
    get_db,
    get_tournament,
    init_db,
    next_empty_slot,
    release_slot_reservation,
    reserve_slot,
)
import payment_gateway
from sslcommerz import parse_order_id, validate_payment as ssl_validate
import rupantorpay
import bkash
from uid_api import FF_UID_MAX_LEN, FF_UID_MIN_LEN, lookup_uid, resolve_player, resolve_player_input

app = Flask(__name__)
app.secret_key = SECRET_KEY or secrets.token_hex(16)
app.permanent_session_lifetime = 60 * 60 * 24 * 7
logging.basicConfig(level=logging.INFO)


_db_lock = threading.Lock()
_db_ready = False

PAID_ORDER_STATUSES = frozenset({"pending_approval", "approved"})


def _get_whatsapp_link(tournament):
    """Safely get whatsapp_group_link from a sqlite3.Row, with env var fallback."""
    try:
        val = tournament["whatsapp_group_link"]
        return (val or "").strip() or WHATSAPP_GROUP_LINK
    except (KeyError, IndexError, TypeError):
        return WHATSAPP_GROUP_LINK


def _parse_room_available_at(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _format_room_time(dt):
    if not dt:
        return ""
    return dt.strftime("%d %b %Y, %I:%M %p").lstrip("0").replace(" 0", " ")


def _tournament_room_configured(tournament):
    return bool(
        (tournament["room_id"] or "").strip() or (tournament["room_pass"] or "").strip()
    )


def _tournament_room_schedule(tournament):
    try:
        raw = tournament["room_available_at"]
    except (KeyError, IndexError):
        raw = None
    return _parse_room_available_at(raw)


def _room_schedule_label(tournament):
    dt = _tournament_room_schedule(tournament)
    return _format_room_time(dt) if dt else ""


def _order_has_paid(order):
    return (order["status"] or "") in PAID_ORDER_STATUSES


def _room_player_state(order, tournament):
    """need_payment | not_eligible | not_created | scheduled | ready"""
    st = (order["status"] or "").strip()
    if st == "pending_payment":
        return "need_payment"
    if not _order_has_paid(order):
        return "not_eligible"
    if not _tournament_room_configured(tournament):
        return "not_created"
    sched = _tournament_room_schedule(tournament)
    if sched and datetime.now() < sched:
        return "scheduled"
    return "ready"


def _lobby_slot_label(squad_name, _players=None):
    """Lobby: শুধু squad name দিলে দেখাবে — নম্বর/লিডার নাম নয়।"""
    sn = (squad_name or "").strip()
    if sn and sn.lower() != "squad":
        return sn
    return ""


def _fetch_order_for_room(conn, order_id, view_token):
    order = conn.execute(
        """
        SELECT o.*, s.slot_number
        FROM orders o
        LEFT JOIN slots s ON s.id = o.assigned_slot_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if not order:
        return None
    vt = order["view_token"] if "view_token" in order.keys() else ""
    if view_token and vt and view_token != vt:
        return None
    return order


def bootstrap_database():
    global _db_ready
    with _db_lock:
        if _db_ready:
            return
        init_db()
        ensure_default_tournament()
        _db_ready = True


bootstrap_database()


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.errorhandler(500)
def server_error(exc):
    traceback.print_exc()
    return "সার্ভার ত্রুটি — লগ দেখুন", 500


def external_url(endpoint, **values):
    """Payment gateway callbacks — needs public HTTPS URL on hosting."""
    if PUBLIC_BASE_URL:
        path = url_for(endpoint, _external=False, **values)
        return f"{PUBLIC_BASE_URL}{path}"
    return url_for(endpoint, _external=True, **values)


if PUBLIC_BASE_URL:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


def require_admin():
    # Only cookie-based session; avoid PIN in URL query (leaks via logs/history).
    return bool(ADMIN_PIN) and request.cookies.get("admin_ok") == "1"


def lobby_data(conn, tournament):
    rows = conn.execute(
        """
        SELECT s.id AS slot_id, s.slot_number, s.squad_name, s.leader_contact, s.status,
               m.position, m.display_name, m.uid
        FROM slots s
        LEFT JOIN members m ON m.slot_id = s.id
        WHERE s.tournament_id = ?
        ORDER BY s.slot_number, m.position
        """,
        (tournament["id"],),
    ).fetchall()

    slots = []
    for n in range(1, SLOT_COUNT + 1):
        slots.append({
            "slot_id": None,
            "slot_number": n,
            "status": "empty",
            "squad_name": None,
            "leader_contact": None,
            "display_label": None,
            "players": [None, None, None, None],
        })

    for r in rows:
        idx = r["slot_number"] - 1
        slots[idx]["slot_id"] = r["slot_id"]
        slots[idx]["status"] = r["status"]
        if r["squad_name"]:
            slots[idx]["squad_name"] = r["squad_name"]
        if r["leader_contact"]:
            slots[idx]["leader_contact"] = r["leader_contact"]
        if r["display_name"] and r["position"]:
            slots[idx]["players"][r["position"] - 1] = {
                "name": r["display_name"],
                "uid": r["uid"],
            }

    filled = count_filled_players(conn, tournament["id"])
    return slots, filled


@app.route("/")
def home():
    with get_db() as conn:
        tournament = get_tournament(conn)
        slots, filled = lobby_data(conn, tournament)
        slots_full = all(s.get("status") == "registered" for s in slots)
    whatsapp_link = _get_whatsapp_link(tournament)
    room_configured = bool((tournament["room_id"] or "").strip())
    return render_template(
        "lobby.html",
        tournament=tournament,
        slots=slots,
        filled=filled,
        max_players=SLOT_COUNT * SQUAD_SIZE,
        entry_fee=ENTRY_FEE,
        slots_full=slots_full,
        whatsapp_group_link=whatsapp_link,
        room_configured=room_configured,
        room_id=tournament["room_id"] or "",
        room_pass=tournament["room_pass"] or "",
    )


@app.route("/api/my-room")
def api_my_room():
    """Return room info + whatsapp for a given contact, if they have an approved order."""
    contact = (request.args.get("contact") or "").strip()
    if not contact:
        return jsonify({"ok": False, "error": "contact required"})
    norm = _norm_contact(contact)
    with get_db() as conn:
        t = get_tournament(conn)
        order = _find_order_by_contact(conn, t["id"], contact, norm)
        if not order:
            return jsonify({"ok": False, "error": "no_order", "whatsapp_link": _get_whatsapp_link(t)})
        has_room = bool((t["room_id"] or "").strip())
        st = order["status"] or ""
        common = {
            "order_id": order["id"],
            "view_token": order["view_token"] if "view_token" in order.keys() else "",
            "slot_number": order["slot_number"],
            "status": st,
            "whatsapp_link": _get_whatsapp_link(t),
        }
        if st == "approved" and has_room:
            return jsonify({
                **common,
                "ok": True,
                "room_id": t["room_id"],
                "room_pass": t["room_pass"],
                "room_info": t["room_info"] if "room_info" in t.keys() else None,
            })
        if st == "approved" and not has_room:
            return jsonify({
                **common,
                "ok": False,
                "error": "no_room",
                "room_schedule_label": _room_schedule_label(t),
                "tournament_time": t["tournament_time"] if "tournament_time" in t.keys() else None,
                "room_info": t["room_info"] if "room_info" in t.keys() else None,
            })
        if st == "pending_payment":
            return jsonify({"ok": False, "error": "pending_payment", **common})
        if st == "pending_approval":
            return jsonify({"ok": False, "error": "pending_approval", **common})
        return jsonify({"ok": False, "error": "not_active", **common})


@app.route("/api/my-order")
def api_my_order():
    """Return order members + view_token for a given contact (for lobby player change)."""
    contact = (request.args.get("contact") or "").strip()
    if not contact:
        return jsonify({"ok": False, "error": "contact required"})
    norm = _norm_contact(contact)
    with get_db() as conn:
        t = get_tournament(conn)
        order = _find_order_by_contact(conn, t["id"], contact, norm)
        if not order:
            return jsonify({"ok": False, "error": "no_order"})
        st = order["status"] or ""
        if st not in ("pending_approval", "approved"):
            return jsonify({"ok": False, "error": "not_active", "status": st})
        changes_left = order["player_changes_left"] if "player_changes_left" in order.keys() else None
        if changes_left is None:
            changes_left = 2
        members = conn.execute(
            "SELECT position, display_name, uid FROM order_members WHERE order_id = ? ORDER BY position",
            (order["id"],),
        ).fetchall()
        vt = order["view_token"] if "view_token" in order.keys() else ""
        return jsonify({
            "ok": True,
            "order_id": order["id"],
            "view_token": vt,
            "members": [{"position": m["position"], "display_name": m["display_name"], "uid": m["uid"]} for m in members],
            "player_changes_left": changes_left,
            "status": st,
            "assigned_slot_id": order["assigned_slot_id"],
        })


@app.route("/api/check-joined")
def api_check_joined():
    """Check if a contact already has an active order (any status except rejected/closed/failed)."""
    contact = (request.args.get("contact") or "").strip()
    if not contact:
        return jsonify({"joined": False})
    norm = _norm_contact(contact)
    with get_db() as conn:
        t = get_tournament(conn)
        order = _find_order_by_contact(conn, t["id"], contact, norm)
        if not order or (order["status"] or "") in ("rejected", "closed", "payment_failed"):
            return jsonify({"joined": False})
        vt = order["view_token"] if "view_token" in order.keys() else ""
        return jsonify({
            "joined": True,
            "order_id": order["id"],
            "status": order["status"],
            "status_url": url_for("join_status", order_id=order["id"], t=vt) if vt else url_for("join_status", order_id=order["id"]),
        })


@app.route("/api/recent-orders")
def api_recent_orders():
    limit = max(1, min(50, request.args.get("limit", 10, type=int)))
    with get_db() as conn:
        t = get_tournament(conn)
        rows = conn.execute(
            """
            SELECT o.id, o.status, o.squad_name, o.leader_contact, o.created_at,
                   s.slot_number
            FROM orders o
            LEFT JOIN slots s ON s.id = o.assigned_slot_id
            WHERE o.tournament_id = ? AND o.status IN ('approved','pending_approval')
            ORDER BY datetime(o.created_at) DESC
            LIMIT ?
            """,
            (t["id"], limit),
        ).fetchall()
        result = []
        for r in rows:
            players = conn.execute(
                "SELECT display_name FROM order_members WHERE order_id = ? ORDER BY position LIMIT 1",
                (r["id"],),
            ).fetchone()
            name = (r["squad_name"] or "").strip() or (players["display_name"] if players else "")
            result.append({
                "order_id": r["id"],
                "slot": r["slot_number"],
                "name": name,
                "status": r["status"],
                "time": r["created_at"],
            })
    return jsonify(result)


@app.route("/api/lobby")
def api_lobby():
    with get_db() as conn:
        t = get_tournament(conn)
        slots, filled = lobby_data(conn, t)
        slots_full = all(s.get("status") == "registered" for s in slots)
        return jsonify({
            "filled": filled,
            "max_players": SLOT_COUNT * SQUAD_SIZE,
            "slots_full": slots_full,
            "slots": [
                {
                    "slot_number": s["slot_number"],
                    "squad_name": s["squad_name"],
                    "status": s["status"],
                    "players": [
                        {"name": p["name"], "uid": p["uid"]} if p else None
                        for p in s["players"]
                    ],
                }
                for s in slots
            ],
        })


@app.route("/join")
@app.route("/join/<token>")
def join(token=None):
    with get_db() as conn:
        tournament = get_tournament(conn)
        if token and token != tournament["join_token"]:
            abort(404)
    return render_template(
        "join.html",
        tournament=tournament,
        squad_size=SQUAD_SIZE,
        entry_fee=ENTRY_FEE,
        bkash=BKASH_NUMBER,
        nagad=NAGAD_NUMBER,
        gateway_enabled=payment_gateway.is_enabled(),
        gateway_name=payment_gateway.provider_name(),
        manual_payment=ENABLE_MANUAL_PAYMENT,
        auto_approve_gateway=AUTO_APPROVE_GATEWAY,
        uid_min=FF_UID_MIN_LEN,
        uid_max=FF_UID_MAX_LEN,
        ff_server=FF_SERVER,
        join_url=url_for("join", token=tournament["join_token"], _external=True),
    )


@app.route("/api/lookup-uid")
def api_lookup_uid():
    uid = request.args.get("uid", "")
    server = request.args.get("server", FF_SERVER)
    return jsonify(lookup_uid(uid, server))


def _parse_join_members():
    members = []
    for i in range(1, SQUAD_SIZE + 1):
        raw = (request.form.get(f"player_{i}") or "").strip()
        if raw:
            res = resolve_player_input(raw, FF_SERVER)
        else:
            mode = (request.form.get(f"mode_{i}") or "name").strip()
            name = (request.form.get(f"name_{i}") or "").strip()
            uid = (request.form.get(f"uid_{i}") or "").strip() or None
            res = resolve_player(mode, name, uid, FF_SERVER)
        if not res["ok"]:
            return None, f"Player {i}: {res['error']}"
        members.append({
            "position": i,
            "display_name": res["display_name"],
            "uid": res.get("uid"),
            "input_type": res["input_type"],
        })
    return members, None


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


@app.route("/join/submit", methods=["POST"])
def join_submit():
    leader_contact = (request.form.get("leader_contact") or "").strip()
    squad_name = (request.form.get("squad_name") or "").strip() or None

    if not leader_contact:
        return jsonify({"ok": False, "error": "হোয়াটসঅ্যাপ / যোগাযোগ নম্বর দিন"}), 400

    members, err = _parse_join_members()
    if err:
        return jsonify({"ok": False, "error": err}), 400

    # Payment — manual (bKash/Nagad) or online gateway
    if not payment_gateway.is_enabled():
        return jsonify({"ok": False, "error": "পেমেন্ট কনফিগার করা নেই"}), 400
    payment_method = payment_gateway.provider_slug()
    payment_trx = "PENDING"
    order_status = "pending_payment"
    use_gateway = not ENABLE_MANUAL_PAYMENT

    with get_db() as conn:
        tournament = get_tournament(conn)
        # 1) Cleanup old abandoned pending payments (free capacity)
        conn.execute(
            """
            UPDATE orders
            SET status = 'payment_failed', reviewed_at = datetime('now'),
                reject_reason = COALESCE(reject_reason, 'Payment timeout')
            WHERE tournament_id = ?
              AND status = 'pending_payment'
              AND datetime(created_at) < datetime('now', '-30 minutes')
            """,
            (tournament["id"],),
        )

        # 2) Capacity check: do not allow payment if all slots are already taken
        registered_slots = conn.execute(
            """
            SELECT COUNT(*) AS c FROM slots
            WHERE tournament_id = ? AND status = 'registered'
            """,
            (tournament["id"],),
        ).fetchone()["c"]
        open_orders = conn.execute(
            """
            SELECT COUNT(*) AS c FROM orders
            WHERE tournament_id = ?
              AND status IN ('pending_payment', 'pending_approval')
            """,
            (tournament["id"],),
        ).fetchone()["c"]
        if registered_slots + open_orders >= SLOT_COUNT:
            return jsonify({"ok": False, "error": "সব ১২টি স্লট পূর্ণ — আর রেজিস্ট্রেশন নেই"}), 400

        view_token = secrets.token_urlsafe(16)
        order_id = _insert_order(
            conn,
            tournament["id"],
            None,  # no slot reservation before payment
            squad_name,
            leader_contact,
            order_status,
            payment_method,
            payment_trx,
            members,
        )
        if order_id:
            conn.execute(
                "UPDATE orders SET view_token = ? WHERE id = ?",
                (view_token, order_id),
            )
        if not order_id:
            return jsonify({
                "ok": False,
                "error": "স্লট একই সময়ে পূর্ণ হয়েছে — আবার চেষ্টা করুন",
            }), 409

        if not use_gateway:
            # Manual: go to status page directly (admin will approve)
            return jsonify({
                "ok": True,
                "order_id": order_id,
                "redirect": url_for("join_status", order_id=order_id, t=view_token),
            })

        if use_gateway:
            client_host = request.host.split(":")[0] if request.host else "127.0.0.1"
            slug = payment_gateway.provider_slug()
            if slug == "supabase":
                success_url = external_url("supabase_pay", order_id=order_id)
                fail_url = success_url
                cancel_url = success_url
                webhook_url = success_url
            elif slug == "rupantorpay":
                success_url = external_url("rupantor_success", order_id=order_id)
                cancel_url = external_url("rupantor_cancel", order_id=order_id)
                webhook_url = external_url("rupantor_webhook")
                fail_url = cancel_url
            elif slug == "bkash":
                # bKash will call this URL with paymentID + status
                success_url = external_url("bkash_callback", order_id=order_id)
                fail_url = success_url
                cancel_url = success_url
                webhook_url = success_url
            else:
                success_url = external_url("sslcommerz_success")
                fail_url = external_url("sslcommerz_fail")
                cancel_url = external_url("sslcommerz_cancel")
                webhook_url = external_url("sslcommerz_ipn")

            session = payment_gateway.start_checkout(
                order_id=order_id,
                amount=ENTRY_FEE,
                customer_name=squad_name or (members[0]["display_name"] if members else "খেলোয়াড়"),
                customer_phone=leader_contact,
                success_url=success_url,
                fail_url=fail_url,
                cancel_url=cancel_url,
                webhook_url=webhook_url,
                product_name=f"{tournament['name']} Entry",
                client_host=client_host,
            )
            if not session["ok"]:
                fail_pending_payment(conn, order_id)
                return jsonify({"ok": False, "error": session["error"]}), 502

            if session.get("tran_id"):
                conn.execute(
                    "UPDATE orders SET gateway_tran_id = ? WHERE id = ?",
                    (session["tran_id"], order_id),
                )
            return jsonify({
                "ok": True,
                "order_id": order_id,
                "redirect": session["gateway_url"],
            })

    # Fallback (should not happen): all successful flows return earlier.
    return jsonify({"ok": False, "error": "Unexpected join state"}), 500


def _complete_gateway_order(order_id, trx_id, payment_method, gateway_tran_id=None):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            return None, "Order not found"
        if order["status"] in ("pending_approval", "approved"):
            return order_id, None
        if order["status"] != "pending_payment":
            return None, "Order already processed"
        if gateway_tran_id and order["gateway_tran_id"]:
            if order["gateway_tran_id"] != gateway_tran_id:
                return None, "Transaction ID mismatch"
        complete_gateway_payment(conn, order_id, trx_id, payment_method)
        if AUTO_APPROVE_GATEWAY:
            ok = approve_order_auto(conn, order_id)
            if not ok:
                # Extreme case: slots became full after payment completed.
                conn.execute(
                    """
                    UPDATE orders
                    SET status = 'rejected',
                        reject_reason = 'Slots full hoye geche. Admin er sathe contact koro.',
                        reviewed_at = datetime('now')
                    WHERE id = ?
                    """,
                    (order_id,),
                )
                return None, "Slots full — admin er sathe contact koro"
    return order_id, None


def _finalize_rupantor(transaction_id):
    result = rupantorpay.verify_payment(transaction_id, ENTRY_FEE)
    if not result["ok"]:
        return None, result["error"]
    order_id = result.get("order_id")
    if not order_id:
        return None, "Order ID পাওয়া যায়নি (metadata)"
    return _complete_gateway_order(
        order_id,
        result["trx_id"],
        result["payment_method"],
        gateway_tran_id=transaction_id,
    )


def _finalize_sslcommerz(val_id, tran_id_hint=None):
    result = ssl_validate(val_id, ENTRY_FEE)
    if not result["ok"]:
        return None, result["error"]

    order_id = result.get("order_id")
    if not order_id and tran_id_hint:
        order_id = parse_order_id(tran_id_hint)

    if not order_id:
        return None, "Order ID পাওয়া যায়নি"

    return _complete_gateway_order(
        order_id,
        result["trx_id"],
        result["payment_method"],
        gateway_tran_id=result.get("tran_id"),
    )


@app.route("/payment/rupantor/success")
def rupantor_success():
    transaction_id = (
        request.args.get("transactionId")
        or request.args.get("transaction_id")
        or ""
    ).strip()
    status = (request.args.get("status") or "").upper()
    if status in ("ERROR", "FAILED", "CANCELLED"):
        return _join_redirect("Payment failed")
    if not transaction_id:
        return _join_redirect("Transaction ID নেই")
    # order_id directly from URL (passed in success_url), fallback to verify API metadata
    order_id = request.args.get("order_id", type=int)

    result = rupantorpay.verify_payment(transaction_id, ENTRY_FEE)
    if not result["ok"]:
        return _join_redirect(result["error"])

    if not order_id:
        order_id = result.get("order_id")
    if not order_id:
        return _join_redirect("Order ID পাওয়া যায়নি")

    _, err = _complete_gateway_order(
        order_id,
        result["trx_id"],
        result["payment_method"],
        gateway_tran_id=transaction_id,
    )
    if err:
        return _join_redirect(err)

    with get_db() as conn:
        conn.execute(
            "UPDATE orders SET gateway_tran_id = ? WHERE id = ?",
            (transaction_id, order_id),
        )
        tok = conn.execute("SELECT view_token FROM orders WHERE id = ?", (order_id,)).fetchone()
        t = tok["view_token"] if tok else ""
    return redirect(url_for("join_status", order_id=order_id, t=t, auto=1))


@app.route("/payment/rupantor/cancel")
def rupantor_cancel():
    transaction_id = (
        request.args.get("transactionId")
        or request.args.get("transaction_id")
        or ""
    ).strip()
    order_id = request.args.get("order_id", type=int)
    if not order_id:
        meta_order = None
        if transaction_id:
            vr = rupantorpay.verify_payment(transaction_id)
            if vr.get("ok"):
                meta_order = vr.get("order_id")
        if meta_order:
            with get_db() as conn:
                fail_pending_payment(conn, meta_order)
    else:
        with get_db() as conn:
            fail_pending_payment(conn, order_id)
    return _join_redirect("Payment cancelled")


@app.route("/payment/rupantor/webhook", methods=["POST", "GET"])
def rupantor_webhook():
    data = request.get_json(silent=True) or {}
    if not data and request.form:
        data = request.form.to_dict()
    transaction_id = (
        data.get("transaction_id")
        or data.get("transactionId")
        or request.args.get("transactionId")
        or ""
    )
    transaction_id = str(transaction_id).strip()
    if not transaction_id:
        return "FAILED", 400

    result = rupantorpay.verify_payment(transaction_id, ENTRY_FEE)
    if not result["ok"]:
        logging.warning("RupantorPay webhook verify fail: %s", result.get("error"))
        return "FAILED", 400

    order_id = data.get("order_id") or request.args.get("order_id", type=int)
    if not order_id:
        order_id = result.get("order_id")
    if not order_id:
        logging.warning("RupantorPay webhook: no order_id for %s", transaction_id)
        return "FAILED", 400

    _, err = _complete_gateway_order(
        order_id,
        result["trx_id"],
        result["payment_method"],
        gateway_tran_id=transaction_id,
    )
    if err:
        logging.warning("RupantorPay webhook complete fail: %s", err)
        return "FAILED", 400

    with get_db() as conn:
        conn.execute(
            "UPDATE orders SET gateway_tran_id = ? WHERE id = ?",
            (transaction_id, order_id),
        )
    return "OK", 200


@app.route("/api/manual-payment/submit", methods=["POST"])
def manual_payment_submit():
    order_id = request.form.get("order_id", type=int)
    view_token = (request.form.get("t") or "").strip()
    payment_method = (request.form.get("payment_method") or "").strip().lower()
    trx_id = (request.form.get("trx_id") or "").strip()

    if not order_id or not view_token or not payment_method or not trx_id:
        return jsonify({"ok": False, "error": "সব তথ্য দিন"}), 400
    if payment_method not in ("bkash", "nagad"):
        return jsonify({"ok": False, "error": "পেমেন্ট মেথড সঠিক নয়"}), 400
    if len(trx_id) < 4:
        return jsonify({"ok": False, "error": "ট্রানজেকশন আইডি সঠিক নয়"}), 400

    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            return jsonify({"ok": False, "error": "অর্ডার পাওয়া যায়নি"}), 404
        vt = order["view_token"] if "view_token" in order.keys() else ""
        if not vt or vt != view_token:
            return jsonify({"ok": False, "error": "Invalid token"}), 404
        if (order["status"] or "") != "pending_payment":
            return jsonify({"ok": False, "error": "এই অর্ডার পেমেন্ট স্টেজে নেই"}), 400

        conn.execute(
            """
            UPDATE orders
            SET status = 'pending_approval',
                payment_trx = ?,
                payment_method = ?
            WHERE id = ?
            """,
            (trx_id, payment_method, order_id),
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

    return jsonify({
        "ok": True,
        "redirect": url_for("join_status", order_id=order_id, t=view_token),
    })


def _join_redirect(pay_error=None):
    with get_db() as conn:
        t = get_tournament(conn)
    url = url_for("join", token=t["join_token"])
    if pay_error:
        from urllib.parse import quote

        url += f"?pay_error={quote(str(pay_error))}"
    return redirect(url)


@app.route("/payment/sslcommerz/success")
def sslcommerz_success():
    val_id = request.args.get("val_id", "").strip()
    tran_id = request.args.get("tran_id", "").strip()
    order_id, err = _finalize_sslcommerz(val_id, tran_id)
    if err or not order_id:
        return _join_redirect(err or "Payment verify হয়নি")
    with get_db() as conn:
        tok = conn.execute("SELECT view_token FROM orders WHERE id = ?", (order_id,)).fetchone()
        t = tok["view_token"] if tok else ""
    return redirect(url_for("join_status", order_id=order_id, t=t))


@app.route("/payment/sslcommerz/fail")
def sslcommerz_fail():
    tran_id = request.args.get("tran_id", "").strip()
    order_id = parse_order_id(tran_id)
    if order_id:
        with get_db() as conn:
            fail_pending_payment(conn, order_id)
    return _join_redirect("Payment failed")


@app.route("/payment/sslcommerz/cancel")
def sslcommerz_cancel():
    tran_id = request.args.get("tran_id", "").strip()
    order_id = parse_order_id(tran_id)
    if order_id:
        with get_db() as conn:
            fail_pending_payment(conn, order_id)
    return _join_redirect("Payment cancelled")


@app.route("/payment/sslcommerz/ipn", methods=["POST"])
def sslcommerz_ipn():
    val_id = (request.form.get("val_id") or request.values.get("val_id") or "").strip()
    tran_id = (request.form.get("tran_id") or request.values.get("tran_id") or "").strip()
    order_id, err = _finalize_sslcommerz(val_id, tran_id)
    if err or not order_id:
        return "FAILED", 400
    return "OK", 200


@app.route("/payment/bkash/callback")
def bkash_callback():
    """bKash URL-based checkout callback.

    bKash calls back to the callbackURL we provided in Create Payment with query params:
    - paymentID
    - status: success | failure | cancel
    - signature (optional)
    """
    order_id = request.args.get("order_id", type=int)
    payment_id = (request.args.get("paymentID") or request.args.get("paymentId") or "").strip()
    status = (request.args.get("status") or "").strip().lower()

    if not order_id:
        return _join_redirect("Order ID missing")
    if not payment_id:
        return _join_redirect("paymentID missing")

    if status in ("cancel", "cancelled", "canceled"):
        with get_db() as conn:
            fail_pending_payment(conn, order_id)
        return _join_redirect("Payment cancelled")
    if status and status not in ("success",):
        with get_db() as conn:
            fail_pending_payment(conn, order_id)
        return _join_redirect("Payment failed")

    exec_res = bkash.execute_payment(payment_id=payment_id)
    if not exec_res.get("ok"):
        return _join_redirect(exec_res.get("error") or "Payment verify হয়নি")

    order_id2, err = _complete_gateway_order(
        order_id,
        exec_res["trx_id"],
        "bkash",
        gateway_tran_id=payment_id,
    )
    if err or not order_id2:
        return _join_redirect(err or "Order complete হয়নি")
    with get_db() as conn:
        tok = conn.execute("SELECT view_token FROM orders WHERE id = ?", (order_id,)).fetchone()
        t = tok["view_token"] if tok else ""
    return redirect(url_for("join_status", order_id=order_id, t=t))


@app.route("/payment/supabase")
def supabase_pay():
    """Supabase auto-detect payment page — shows instructions + polls Supabase."""
    order_id = request.args.get("order_id", type=int)
    if not order_id:
        return _join_redirect("Order ID missing")
    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            abort(404)
        vt = order["view_token"] if "view_token" in order.keys() else ""
        view_token = request.args.get("t") or vt
        if vt and view_token and view_token != vt:
            abort(404)
        tournament = get_tournament(conn)
    return render_template(
        "supabase_pay.html",
        order=dict(order),
        tournament=tournament,
        view_token=view_token,
        entry_fee=ENTRY_FEE,
        bkash=BKASH_NUMBER,
        nagad=NAGAD_NUMBER,
        supabase_url=SUPABASE_URL,
        supabase_anon_key=SUPABASE_ANON_KEY,
        supabase_table=SUPABASE_TABLE,
    )


@app.route("/api/verify-supabase-payment", methods=["POST"])
def verify_supabase_payment():
    """Server-side verify: check Supabase for trx_id, mark used, complete order."""
    order_id = request.form.get("order_id", type=int)
    trx_id = (request.form.get("trx_id") or "").strip()
    view_token = (request.form.get("t") or "").strip()

    if not order_id or not trx_id:
        return jsonify({"ok": False, "error": "order_id and trx_id required"}), 400

    # Verify Supabase transaction is valid + unused
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }
    table_url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    query = f"trx_id=eq.{trx_id}&status=eq.unused&select=trx_id,amount,status"
    try:
        resp = http_req.get(f"{table_url}?{query}", headers=headers, timeout=10)
        if not resp.ok:
            return jsonify({"ok": False, "error": "Supabase query failed"}), 502
        rows = resp.json()
        if not rows:
            return jsonify({"ok": False, "error": "Transaction not found or already used"}), 404
        row = rows[0]
        paid_amount = float(row.get("amount") or 0)
        required = float(ENTRY_FEE)
        if paid_amount < required:
            return jsonify({
                "ok": False,
                "error": f"Amount ৳{paid_amount:.0f} is less than entry fee ৳{required:.0f}. Send at least ৳{required:.0f}"
            }), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Supabase error: {e}"}), 502

    # Mark as used in Supabase
    patch_query = f"trx_id=eq.{trx_id}"
    try:
        patch_resp = http_req.patch(
            f"{table_url}?{patch_query}",
            headers={**headers, "Prefer": "return=minimal"},
            json={"status": "used"},
            timeout=10,
        )
        if not patch_resp.ok:
            return jsonify({"ok": False, "error": "Failed to mark transaction used"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"Supabase mark error: {e}"}), 502

    # Complete the order in ff-tournament — always auto-approve for Supabase
    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            return jsonify({"ok": False, "error": "Order not found"}), 404
        vt = order["view_token"] if "view_token" in order.keys() else ""
        if vt and view_token and view_token != vt:
            return jsonify({"ok": False, "error": "Invalid token"}), 404
        if order["status"] not in ("pending_payment",):
            return jsonify({"ok": False, "error": "Order not in payment state"}), 400

        complete_gateway_payment(conn, order_id, trx_id, "supabase")
        ok = approve_order_auto(conn, order_id)
        if not ok:
            return jsonify({"ok": False, "error": "All slots full — admin e contact korun"}), 409

    with get_db() as conn:
        tok = conn.execute("SELECT view_token FROM orders WHERE id = ?", (order_id,)).fetchone()
        t = tok["view_token"] if tok else ""
    return jsonify({
        "ok": True,
        "redirect": url_for("join_status", order_id=order_id, t=t),
    })


@app.route("/claim/<token>")
def claim_squad(token):
    token = (token or "").strip()
    error = request.args.get("error") or ""
    claimed = request.args.get("claimed") == "1"

    with get_db() as conn:
        order = conn.execute(
            """
            SELECT o.*, s.slot_number
            FROM orders o
            LEFT JOIN slots s ON s.id = o.assigned_slot_id
            WHERE o.claim_token = ?
            """,
            (token,),
        ).fetchone()
        if not order:
            abort(404)
        if order["status"] != "approved":
            return render_template("claim.html", error="এই স্লট আর সক্রিয় নেই", token=token, claimed=False, order=None)
        try:
            already_claimed = order["claimed_contact"]
        except (KeyError, IndexError, TypeError):
            already_claimed = None
        if already_claimed:
            try:
                vt = order["view_token"]
            except (KeyError, IndexError, TypeError):
                vt = ""
            return redirect(url_for("join_status", order_id=order["id"], t=vt))
        tournament = get_tournament(conn)
        members = conn.execute(
            "SELECT * FROM order_members WHERE order_id = ? ORDER BY position",
            (order["id"],),
        ).fetchall()

    return render_template(
        "claim.html",
        token=token,
        error=error,
        claimed=claimed,
        order=dict(order),
        members=members,
        tournament=tournament,
        entry_fee=ENTRY_FEE,
    )


@app.route("/claim/<token>/submit", methods=["POST"])
def claim_squad_submit(token):
    contact = (request.form.get("contact") or "").strip()
    if not contact:
        return redirect(url_for("claim_squad", token=token, error="মোবাইল নম্বর দিন"))

    with get_db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE claim_token = ?", (token,)
        ).fetchone()
        if not order:
            abort(404)
        try:
            already = order["claimed_contact"]
        except (KeyError, IndexError, TypeError):
            already = None
        if already:
            return redirect(url_for("claim_squad", token=token, error="ইতিমধ্যে ক্লেইম করা হয়েছে"))
        conn.execute(
            "UPDATE orders SET claimed_contact = ?, leader_contact = ? WHERE id = ?",
            (contact, contact, order["id"]),
        )
        try:
            vt = order["view_token"]
        except (KeyError, IndexError, TypeError):
            vt = ""

    return redirect(url_for("join_status", order_id=order["id"], t=vt))


@app.route("/join/status/<int:order_id>")
def join_status(order_id):
    view_token = (request.args.get("t") or "").strip()
    with get_db() as conn:
        order = conn.execute(
            """
            SELECT o.*, s.slot_number
            FROM orders o
            LEFT JOIN slots s ON s.id = o.assigned_slot_id
            WHERE o.id = ?
            """,
            (order_id,),
        ).fetchone()
        if not order:
            abort(404)
        # Protect status page (room/pass etc) using a per-order token
        vt = order["view_token"] if "view_token" in order.keys() else ""
        if view_token and vt and view_token != vt:
            abort(404)
        order = dict(order)
        members = conn.execute(
            "SELECT * FROM order_members WHERE order_id = ? ORDER BY position",
            (order_id,),
        ).fetchall()
        tournament = get_tournament(conn)

    squad_players = [None, None, None, None]
    for m in members:
        pos = m["position"] - 1
        if 0 <= pos < SQUAD_SIZE:
            squad_players[pos] = {
                "name": m["display_name"],
                "uid": m["uid"],
            }

    auto_redirect = request.args.get("auto", type=int) == 1
    admin_reg = request.args.get("admin_reg", type=int) == 1
    return render_template(
        "join_status.html",
        order=order,
        members=members,
        squad_players=squad_players,
        squad_size=SQUAD_SIZE,
        tournament=tournament,
        entry_fee=ENTRY_FEE,
        bkash=BKASH_NUMBER,
        nagad=NAGAD_NUMBER,
        whatsapp_group_link=_get_whatsapp_link(tournament),
        view_token=view_token,
        room_schedule_label=_room_schedule_label(tournament),
        auto_redirect=auto_redirect,
        admin_reg=admin_reg,
    )


def _norm_contact(s: str) -> str:
    s = (s or "").strip()
    # Keep only digits (common in BD numbers)
    return re.sub(r"[^0-9]", "", s)


def _find_order_by_contact(conn, tournament_id, contact_raw, contact_norm):
    """Lookup the most recent order by contact, trying multiple format normalizations."""
    if not contact_raw and not contact_norm:
        return None
    if not contact_norm:
        contact_norm = _norm_contact(contact_raw)
    # Build the tail (last 11 digits = BD local number)
    tail = contact_norm[-11:] if len(contact_norm) >= 11 else contact_norm
    # Fetch ALL orders for this tournament (small dataset)
    cur = conn.execute(
        """
        SELECT o.id, o.status, o.leader_contact, s.slot_number, o.assigned_slot_id,
               o.player_changes_left, o.view_token
        FROM orders o
        LEFT JOIN slots s ON s.id = o.assigned_slot_id
        WHERE o.tournament_id = ?
        ORDER BY datetime(o.created_at) DESC, o.id DESC
        """,
        (tournament_id,),
    )
    for row in cur:
        db_val = (row["leader_contact"] or "").strip()
        if not db_val:
            continue
        # Exact match
        if db_val == contact_raw:
            return row
        # Norm match
        db_norm = _norm_contact(db_val)
        if db_norm == contact_norm:
            return row
        # Tail match — compare last 11 digits
        db_tail = db_norm[-11:] if len(db_norm) >= 11 else db_norm
        if db_tail == tail:
            return row
    return None


@app.route("/room")
def room_auto():
    """Redirect to lobby."""
    return redirect(url_for("home"))


@app.route("/room/history", methods=["GET", "POST"])
def room_history():
    """Hidden account (by contact): show latest order + history, and room info only for paid/approved."""
    error = None
    contact = (request.values.get("contact") or "").strip()
    norm = _norm_contact(contact)
    orders = []
    latest = None
    join_link = None

    with get_db() as conn:
        t = get_tournament(conn)
        join_link = url_for("join", token=t["join_token"])

        if norm:
            # Match both exact and normalized forms (space/hyphen removed)
            orders = conn.execute(
                """
                SELECT o.*, s.slot_number
                FROM orders o
                LEFT JOIN slots s ON s.id = o.assigned_slot_id
                WHERE REPLACE(REPLACE(REPLACE(o.leader_contact, ' ', ''), '-', ''), '+', '') = ?
                   OR o.leader_contact = ?
                ORDER BY datetime(o.created_at) DESC, o.id DESC
                LIMIT 20
                """,
                (norm, contact),
            ).fetchall()
            latest = orders[0] if orders else None

    # POST: auto route based on latest order status
    if request.method == "POST":
        if not norm:
            error = "যে নম্বর দিয়ে অর্ডার করেছেন সেটা দিন"
        elif not latest:
            error = "এই নম্বরে কোনো অর্ডার পাওয়া যায়নি — আগে টুর্নামেন্টে যোগ দিয়ে পেমেন্ট করুন"
        else:
            st = (latest["status"] or "").strip()
            tok = (latest["view_token"] or "").strip()
            oid = latest["id"]

            if st == "pending_payment":
                return redirect(join_link + "?pay_error=" + "পেমেন্ট বাকি আছে".replace(" ", "%20"))
            if st in ("payment_failed", "rejected", "closed"):
                return redirect(join_link + "?pay_error=" + "অর্ডার সক্রিয় নয় — আবার রেজিস্টার করুন".replace(" ", "%20"))
            if _order_has_paid(dict(latest)) and tok:
                return redirect(url_for("room_view", order_id=oid, t=tok))
            if tok:
                return redirect(url_for("join_status", order_id=oid, t=tok))
            return redirect(url_for("join_status", order_id=oid))

    return render_template(
        "room.html",
        error=error,
        contact=contact,
        orders=[dict(o) for o in orders] if orders else [],
        join_link=join_link,
    )


@app.route("/room/info/<int:order_id>")
def room_info(order_id):
    """Purono link — room_view e redirect."""
    view_token = (request.args.get("t") or "").strip()
    return redirect(url_for("room_view", order_id=order_id, t=view_token))


@app.route("/room/view")
def room_view():
    """Payment করা player — room ID/Pass (admin dile) / na thakle not-created message."""
    order_id = request.args.get("order_id", type=int)
    view_token = (request.args.get("t") or "").strip()
    join_link = None

    with get_db() as conn:
        tournament = get_tournament(conn)
        join_link = url_for("join", token=tournament["join_token"])
        room_schedule_label = _room_schedule_label(tournament)

        if not order_id or not view_token:
            return render_template(
                "room_view.html",
                state="need_link",
                join_link=join_link,
                history_link=url_for("room_history"),
                entry_fee=ENTRY_FEE,
            )

        order = _fetch_order_for_room(conn, order_id, view_token)
        if not order:
            abort(404)

        player_state = _room_player_state(order, tournament)
        status_url = url_for("join_status", order_id=order_id, t=view_token)

        if player_state == "need_payment":
            return render_template(
                "room_view.html",
                state="need_payment",
                order=order,
                join_link=join_link,
                status_url=status_url,
                entry_fee=ENTRY_FEE,
            )
        if player_state == "not_eligible":
            return render_template(
                "room_view.html",
                state="not_eligible",
                order=order,
                join_link=join_link,
                status_url=status_url,
            )
        if player_state == "not_created":
            return render_template(
                "room_view.html",
                state="not_created",
                order=order,
                tournament=tournament,
                join_link=join_link,
                status_url=status_url,
                whatsapp_group_link=_get_whatsapp_link(tournament),
                room_schedule_label=room_schedule_label,
            )
        if player_state == "scheduled":
            return render_template(
                "room_view.html",
                state="scheduled",
                order=order,
                tournament=tournament,
                join_link=join_link,
                status_url=status_url,
                whatsapp_group_link=_get_whatsapp_link(tournament),
                room_schedule_label=room_schedule_label,
            )

        return render_template(
            "room_view.html",
            state="ready",
            order=order,
            tournament=tournament,
            join_link=join_link,
            status_url=status_url,
            whatsapp_group_link=_get_whatsapp_link(tournament),
            room_schedule_label=room_schedule_label,
        )


@app.route("/order/<int:order_id>/change-player/<view_token>", methods=["POST"])
def change_player(order_id, view_token):
    position = request.form.get("position", type=int)
    display_name = (request.form.get("display_name") or "").strip()
    uid = (request.form.get("uid") or "").strip()
    if not position or position < 1 or position > SQUAD_SIZE:
        return jsonify(ok=False, error="Invalid position"), 400
    if not display_name:
        return jsonify(ok=False, error="Player name required"), 400
    with get_db() as conn:
        order = conn.execute(
            "SELECT o.*, s.slot_number FROM orders o LEFT JOIN slots s ON s.id = o.assigned_slot_id WHERE o.id = ?",
            (order_id,),
        ).fetchone()
        if not order:
            return jsonify(ok=False, error="Order not found"), 404
        vt = order["view_token"] if "view_token" in order.keys() else ""
        if not vt or vt != view_token:
            return jsonify(ok=False, error="Invalid token"), 404
        if order["status"] not in ("pending_approval", "approved"):
            return jsonify(ok=False, error="Cannot change player for this order"), 400
        changes_left = order["player_changes_left"] if "player_changes_left" in order.keys() else None
        if changes_left is None:
            changes_left = 2
        if changes_left < 1:
            return jsonify(ok=False, error="Change limit reached (2 per order)"), 400
        conn.execute(
            "UPDATE order_members SET display_name = ?, uid = ? WHERE order_id = ? AND position = ?",
            (display_name, uid or None, order_id, position),
        )
        if order["assigned_slot_id"]:
            conn.execute(
                "UPDATE members SET display_name = ?, uid = ? WHERE slot_id = ? AND position = ?",
                (display_name, uid or None, order["assigned_slot_id"], position),
            )
        conn.execute(
            "UPDATE orders SET player_changes_left = player_changes_left - 1 WHERE id = ?",
            (order_id,),
        )
    return jsonify(ok=True, changes_left=changes_left - 1)


@app.route("/room/access", methods=["GET", "POST"])
def room_access():
    """Legacy: open room via status link/token paste."""
    error = None
    if request.method == "POST":
        link = (request.form.get("status_link") or "").strip()
        order_id = request.form.get("order_id", type=int)
        token = (request.form.get("t") or "").strip()

        # If user pasted status link, extract order id + token from it
        if link:
            try:
                u = urlparse(link)
                m = re.search(r"/join/status/(\d+)", u.path or "")
                if m:
                    order_id = int(m.group(1))
                q = parse_qs(u.query or "")
                if not token:
                    token = (q.get("t") or [""])[0]
            except Exception:
                pass

        if not order_id or not token:
            error = "স্ট্যাটাস লিংক অথবা অর্ডার আইডি + টোকেন দিন"
        else:
            return redirect(url_for("join_status", order_id=order_id, t=token))

    return render_template("room_access.html", error=error)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if not ADMIN_PIN:
        error = "অ্যাডমিন পিন সেট করা নেই। ADMIN_PIN এনভায়রনমেন্টে সেট করুন।"
    if request.method == "POST":
        if ADMIN_PIN and request.form.get("pin") == ADMIN_PIN:
            resp = redirect(url_for("admin_dashboard"))
            resp.set_cookie(
                "admin_ok",
                "1",
                max_age=60 * 60 * 24 * 7,
                httponly=True,
                samesite="Lax",
                path="/",
            )
            return resp
        error = "ভুল PIN"
    return render_template("login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    resp = redirect(url_for("admin_login"))
    resp.delete_cookie("admin_ok")
    return resp


@app.route("/admin")
def admin_dashboard():
    if not require_admin():
        return redirect(url_for("admin_login"))

    with get_db() as conn:
        tournament = get_tournament(conn)
        pending_payment_count = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE tournament_id = ? AND status = 'pending_payment'",
            (tournament["id"],),
        ).fetchone()["c"]
        pending = conn.execute(
            """
            SELECT o.*, s.slot_number,
                   (SELECT COUNT(*) FROM order_members om WHERE om.order_id = o.id) AS member_count
            FROM orders o
            LEFT JOIN slots s ON s.id = o.assigned_slot_id
            WHERE o.tournament_id = ? AND o.status = 'pending_approval'
            ORDER BY o.created_at DESC
            """,
            (tournament["id"],),
        ).fetchall()

        pending_full = []
        for p in pending:
            d = dict(p)
            d["members"] = [
                dict(m)
                for m in conn.execute(
                    "SELECT * FROM order_members WHERE order_id = ? ORDER BY position",
                    (p["id"],),
                ).fetchall()
            ]
            pending_full.append(d)

        slots, filled = lobby_data(conn, tournament)

        payment_history = [
            dict(r) for r in conn.execute(
                """
                SELECT o.id, o.status, o.squad_name, o.leader_contact,
                       o.payment_method, o.payment_trx, o.gateway_tran_id,
                       o.auto_approved, o.created_at, o.reviewed_at,
                       o.reject_reason, s.slot_number
                FROM orders o
                LEFT JOIN slots s ON s.id = o.assigned_slot_id
                WHERE o.tournament_id = ?
                ORDER BY o.created_at DESC
                LIMIT 200
                """,
                (tournament["id"],),
            ).fetchall()
        ]

    join_link = url_for("join", token=tournament["join_token"], _external=True)
    raw_at = ""
    try:
        raw_at = (tournament["room_available_at"] or "").strip()
    except (KeyError, IndexError):
        pass
    room_at_input = ""
    if raw_at:
        room_at_input = raw_at[:16].replace(" ", "T")
    return render_template(
        "admin.html",
        tournament=tournament,
        pending=pending_full,
        slots=slots,
        filled=filled,
        join_link=join_link,
        bkash=BKASH_NUMBER,
        nagad=NAGAD_NUMBER,
        entry_fee=ENTRY_FEE,
        gateway_enabled=payment_gateway.is_enabled(),
        gateway_name=payment_gateway.provider_name(),
        pending_payment_count=pending_payment_count,
        payment_history=payment_history,
        whatsapp_group_link=_get_whatsapp_link(tournament),
        room_schedule_label=_room_schedule_label(tournament),
        room_at_input=room_at_input,
    )


@app.route("/admin/room", methods=["POST"])
def admin_update_room():
    if not require_admin():
        return redirect(url_for("admin_login"))

    room_id = (request.form.get("room_id") or "").strip()
    room_pass = (request.form.get("room_pass") or "").strip()
    room_available_at = (request.form.get("room_available_at") or "").strip()
    whatsapp_link_submitted = (request.form.get("whatsapp_group_link") or "").strip()
    tournament_time = (request.form.get("tournament_time") or "").strip()
    room_info = (request.form.get("room_info") or "").strip()

    with get_db() as conn:
        t = get_tournament(conn)
        whatsapp_link = whatsapp_link_submitted or (t["whatsapp_group_link"] if "whatsapp_group_link" in t.keys() else None)
        conn.execute(
            """
            UPDATE tournaments
            SET room_id = ?, room_pass = ?, whatsapp_group_link = ?,
                room_available_at = ?, tournament_time = ?, room_info = ?
            WHERE id = ?
            """,
            (
                room_id or None,
                room_pass or None,
                whatsapp_link or None,
                room_available_at or None,
                tournament_time or None,
                room_info or None,
                t["id"],
            ),
        )
    return redirect(url_for("admin_dashboard") + "?room=1")


@app.route("/admin/room/clear", methods=["POST"])
def admin_clear_room():
    if not require_admin():
        return redirect(url_for("admin_login"))

    with get_db() as conn:
        t = get_tournament(conn)
        conn.execute(
            """
            UPDATE tournaments
            SET room_id = NULL, room_pass = NULL, room_available_at = NULL
            WHERE id = ?
            """,
            (t["id"],),
        )
    return redirect(url_for("admin_dashboard") + "?room=cleared")


@app.route("/admin/slot/<int:slot_id>/edit-player", methods=["POST"])
def admin_edit_slot_player(slot_id):
    if not require_admin():
        return redirect(url_for("admin_login"))
    position = request.form.get("position", type=int)
    display_name = (request.form.get("display_name") or "").strip()
    uid = (request.form.get("uid") or "").strip()
    if not position or position < 1 or position > SQUAD_SIZE:
        abort(400)
    if not display_name:
        abort(400)
    with get_db() as conn:
        slot = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
        if not slot:
            abort(404)
        conn.execute(
            "UPDATE members SET display_name = ?, uid = ? WHERE slot_id = ? AND position = ?",
            (display_name, uid or None, slot_id, position),
        )
        if slot["order_id"]:
            conn.execute(
                "UPDATE order_members SET display_name = ?, uid = ? WHERE order_id = ? AND position = ?",
                (display_name, uid or None, slot["order_id"], position),
            )
    return redirect(url_for("admin_dashboard") + "?updated=1")


@app.route("/admin/history")
def admin_history():
    if not require_admin():
        return redirect(url_for("admin_login"))

    with get_db() as conn:
        tournament = get_tournament(conn)
        # Latest 200 orders (enough for admin history)
        orders = conn.execute(
            """
            SELECT o.*, s.slot_number
            FROM orders o
            LEFT JOIN slots s ON s.id = o.assigned_slot_id
            WHERE o.tournament_id = ?
            ORDER BY o.created_at DESC
            LIMIT 200
            """,
            (tournament["id"],),
        ).fetchall()

        status_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS c
            FROM orders
            WHERE tournament_id = ?
            GROUP BY status
            """,
            (tournament["id"],),
        ).fetchall()
        status_counts = {r["status"]: r["c"] for r in status_rows}

    return render_template(
        "admin_history.html",
        tournament=tournament,
        orders=[dict(o) for o in orders],
        status_counts=status_counts,
    )


@app.route("/admin/tournament/clear", methods=["POST"])
def admin_clear_tournament():
    if not require_admin():
        return redirect(url_for("admin_login"))

    confirm = (request.form.get("confirm") or "").strip().upper()
    if confirm != "CLEAR":
        return redirect(url_for("admin_dashboard") + "?error=confirm")

    with get_db() as conn:
        tournament = get_tournament(conn)
        tid = tournament["id"]

        # Clear room info for new tournament
        conn.execute(
            """
            UPDATE tournaments
            SET room_id = NULL, room_pass = NULL, room_available_at = NULL
            WHERE id = ?
            """,
            (tid,),
        )

        # Increment tournament epoch so "already joined" state resets after all clear
        conn.execute(
            """
            UPDATE tournaments
            SET join_epoch = COALESCE(join_epoch, 1) + 1
            WHERE id = ?
            """,
            (tid,),
        )

        # Close orders so old status/room links won't remain "active" after tournament ends.
        conn.execute(
            """
            UPDATE orders
            SET status = 'closed',
                reject_reason = COALESCE(reject_reason, 'Tournament ended'),
                reviewed_at = datetime('now')
            WHERE tournament_id = ?
              AND status IN ('pending_payment', 'pending_approval', 'approved')
            """,
            (tid,),
        )

        # Clear members + reset slots
        conn.execute(
            """
            DELETE FROM members
            WHERE slot_id IN (SELECT id FROM slots WHERE tournament_id = ?)
            """,
            (tid,),
        )
        conn.execute(
            """
            UPDATE slots
            SET status = 'empty',
                squad_name = NULL,
                leader_contact = NULL,
                order_id = NULL,
                registered_at = NULL
            WHERE tournament_id = ?
            """,
            (tid,),
        )

    return redirect(url_for("admin_dashboard") + "?cleared=1")


@app.route("/admin/slot/<int:slot_id>/cancel", methods=["POST"])
def cancel_reservation(slot_id):
    """Cancel a reserved slot + close its related order (if any)."""
    if not require_admin():
        return redirect(url_for("admin_login"))

    with get_db() as conn:
        slot = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
        if not slot:
            abort(404)
        if slot["status"] != "reserved":
            abort(400)

        # Close related order if exists (safe for history)
        if slot["order_id"]:
            conn.execute(
                """
                UPDATE orders
                SET status = 'closed',
                    reject_reason = 'Admin cancelled reservation',
                    reviewed_at = datetime('now')
                WHERE id = ? AND status IN ('pending_payment', 'pending_approval')
                """,
                (slot["order_id"],),
            )
        release_slot_reservation(conn, slot_id)

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/order/<int:order_id>/approve", methods=["POST"])
def approve_order(order_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order or order["status"] != "pending_approval":
            abort(400)

        if order["assigned_slot_id"]:
            slot = conn.execute(
                "SELECT * FROM slots WHERE id = ?",
                (order["assigned_slot_id"],),
            ).fetchone()
        else:
            slot = next_empty_slot(conn, order["tournament_id"])
        if not slot:
            return redirect(url_for("admin_dashboard") + "?error=all_full")

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

        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE slots
            SET status = 'registered',
                squad_name = ?,
                leader_contact = ?,
                order_id = ?,
                registered_at = ?
            WHERE id = ?
            """,
            (order["squad_name"], order["leader_contact"], order_id, now, slot["id"]),
        )
        conn.execute(
            """
            UPDATE orders
            SET status = 'approved', assigned_slot_id = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (slot["id"], now, order_id),
        )

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/order/<int:order_id>/reject", methods=["POST"])
def reject_order(order_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    reason = (request.form.get("reason") or "Payment verify হয়নি").strip()
    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.execute(
            """
            UPDATE orders SET status = 'rejected', reject_reason = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (reason, datetime.utcnow().isoformat(), order_id),
        )
        if order and order["assigned_slot_id"]:
            release_slot_reservation(conn, order["assigned_slot_id"])
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/slot/<int:slot_id>/release", methods=["POST"])
def release_slot(slot_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    with get_db() as conn:
        conn.execute("DELETE FROM members WHERE slot_id = ?", (slot_id,))
        conn.execute(
            """
            UPDATE slots
            SET status = 'empty', squad_name = NULL, leader_contact = NULL,
                order_id = NULL, registered_at = NULL
            WHERE id = ?
            """,
            (slot_id,),
        )
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/register-team", methods=["POST"])
def admin_register_team():
    if not require_admin():
        return redirect(url_for("admin_login"))

    squad_name = (request.form.get("squad_name") or "").strip()
    leader_contact = (request.form.get("leader_contact") or "").strip()
    if not squad_name:
        return redirect(url_for("admin_dashboard") + "?error=missing_fields")

    with get_db() as conn:
        tournament = get_tournament(conn)
        slot = next_empty_slot(conn, tournament["id"])
        if not slot:
            return redirect(url_for("admin_dashboard") + "?error=all_full")

        now = datetime.utcnow().isoformat()
        view_token = secrets.token_urlsafe(16)
        claim_token = secrets.token_urlsafe(16)
        cur = conn.execute(
            """
            INSERT INTO orders (tournament_id, squad_name, leader_contact, status,
                payment_method, payment_trx, auto_approved, created_at, reviewed_at,
                view_token, claim_token)
            VALUES (?, ?, ?, 'approved', 'manual', 'ADMIN', 1, ?, ?, ?, ?)
            """,
            (tournament["id"], squad_name, leader_contact, now, now, view_token, claim_token),
        )
        order_id = cur.lastrowid

        for pos in range(1, 5):
            name = (request.form.get(f"player_{pos}") or "").strip()
            uid = (request.form.get(f"uid_{pos}") or "").strip()
            if name:
                conn.execute(
                    "INSERT INTO order_members (order_id, position, display_name, uid) VALUES (?, ?, ?, ?)",
                    (order_id, pos, name, uid),
                )

        conn.execute("DELETE FROM members WHERE slot_id = ?", (slot["id"],))
        for pos in range(1, 5):
            name = (request.form.get(f"player_{pos}") or "").strip()
            uid = (request.form.get(f"uid_{pos}") or "").strip()
            if name:
                conn.execute(
                    "INSERT INTO members (slot_id, position, display_name, uid) VALUES (?, ?, ?, ?)",
                    (slot["id"], pos, name, uid),
                )

        conn.execute(
            """
            UPDATE slots SET status = 'registered', squad_name = ?, leader_contact = ?,
                order_id = ?, registered_at = ?
            WHERE id = ?
            """,
            (squad_name, leader_contact, order_id, now, slot["id"]),
        )
        conn.execute(
            "UPDATE orders SET assigned_slot_id = ? WHERE id = ?",
            (slot["id"], order_id),
        )

    claim_url = url_for("claim_squad", token=claim_token, _external=True)
    return render_template("claim_link.html", claim_url=claim_url, order_id=order_id, squad_name=squad_name)


@app.route("/admin/slot/<int:slot_id>/edit-squad", methods=["POST"])
def admin_edit_slot_squad(slot_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    new_name = (request.form.get("squad_name") or "").strip()
    new_contact = (request.form.get("leader_contact") or "").strip()
    if not new_name:
        return redirect(url_for("admin_dashboard") + "?error=missing_name")

    with get_db() as conn:
        conn.execute(
            "UPDATE slots SET squad_name = ?, leader_contact = ? WHERE id = ?",
            (new_name, new_contact, slot_id),
        )
    return redirect(url_for("admin_dashboard") + "?updated=1")


@app.route("/admin/clear-history", methods=["POST"])
def admin_clear_history():
    if not require_admin():
        return redirect(url_for("admin_login"))

    confirm = (request.form.get("confirm") or "").strip().upper()
    if confirm != "CLEAR":
        return redirect(url_for("admin_history") + "?error=confirm")

    with get_db() as conn:
        tournament = get_tournament(conn)
        conn.execute("DELETE FROM order_members WHERE order_id IN (SELECT id FROM orders WHERE tournament_id = ?)", (tournament["id"],))
        conn.execute("DELETE FROM orders WHERE tournament_id = ?", (tournament["id"],))
    return redirect(url_for("admin_history") + "?cleared=1")


@app.route("/admin/api/restore", methods=["POST"])
def admin_restore():
    if not require_admin():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    with get_db() as conn:
        # Restore orders
        for o in data.get("orders", []):
            exists = conn.execute("SELECT id FROM orders WHERE id = ?", (o["id"],)).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO orders (id, tournament_id, squad_name, leader_contact, status,
                        payment_method, payment_trx, gateway_tran_id, auto_approved,
                        assigned_slot_id, created_at, reviewed_at, view_token)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        o["id"], o.get("tournament_id", 1), o.get("squad_name", ""),
                        o["leader_contact"], o.get("status", "approved"),
                        o.get("payment_method", "bkash"), o.get("payment_trx", ""),
                        o.get("gateway_trx_id", o.get("gateway_tran_id", "")),
                        o.get("auto_approved", 1),
                        o.get("assigned_slot_id"), o.get("created_at"),
                        o.get("reviewed_at"), o.get("view_token", "restore"),
                    ),
                )

        # Restore order_members
        for om in data.get("order_members", []):
            exists = conn.execute(
                "SELECT id FROM order_members WHERE order_id = ? AND position = ?",
                (om["order_id"], om["position"]),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO order_members (order_id, position, display_name, uid) VALUES (?, ?, ?, ?)",
                    (om["order_id"], om["position"], om["display_name"], om.get("uid", "")),
                )

        # Restore slots
        for s in data.get("slots", []):
            conn.execute(
                """
                UPDATE slots SET status = ?, squad_name = ?, leader_contact = ?,
                    order_id = ?, registered_at = datetime('now')
                WHERE id = ?
                """,
                (s.get("status", "registered"), s["squad_name"],
                 s.get("leader_contact", ""), s["order_id"], s["id"]),
            )

        # Restore members
        conn.execute("DELETE FROM members")
        for m in data.get("members", []):
            conn.execute(
                "INSERT INTO members (slot_id, position, display_name, uid) VALUES (?, ?, ?, ?)",
                (m["slot_id"], m["position"], m["display_name"], m.get("uid", "")),
            )

    return jsonify({"ok": True})


@app.route("/api/export")
def export_text():
    with get_db() as conn:
        tournament = get_tournament(conn)
        slots, _ = lobby_data(conn, tournament)

    lines = [f"🏆 {tournament['name']}", "━━━━━━━━━━━━━━━━━━━━"]
    for s in slots:
        names = [p["name"] if p else "—" for p in s["players"]]
        if any(n != "—" for n in names):
            lines.append(f"\nSlot {s['slot_number']} — {s['squad_name'] or ''}")
            for i, n in enumerate(names, 1):
                if n != "—":
                    lines.append(f"  P{i}: {n}")

    text = "\n".join(lines)
    if request.args.get("format") == "json":
        return jsonify({"text": text})
    return render_template("export.html", text=text)


def _lan_ips():
    import socket

    seen = set()
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            seen.add(ip)
            ips.append(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip in seen:
                continue
            seen.add(ip)
            ips.append(ip)
    except OSError:
        pass
    return ips


def _print_urls(port, join_token):
    base_local = f"http://127.0.0.1:{port}"
    print("")
    print("=== PC (ei computer) ===")
    print("Lobby: ", base_local + "/")
    print("Join:  ", base_local + f"/join/{join_token}")
    print("Admin: ", base_local + "/admin")
    lan = _lan_ips()
    if lan:
        print("")
        print("=== Phone (same WiFi) ===")
        for ip in lan:
            base = f"http://{ip}:{port}"
            print("Lobby: ", base + "/")
            print("Join:  ", base + f"/join/{join_token}")
            print("Admin: ", base + "/admin")
            print("---")
    else:
        print("")
        print("Phone: WiFi same rakho, cmd e 'ipconfig' → IPv4 address")
        print(f"       http://<PC-IP>:{port}/")
    print("")
    print("Firewall block korle: Windows > Allow app > Python > Private network ON")
    print("Bandh: Ctrl+C")
    print("")


if __name__ == "__main__":
    bootstrap_database()
    with get_db() as conn:
        t = get_tournament(conn)
        join_token = t["join_token"]
    _print_urls(PORT, join_token)
    app.run(debug=True, host=HOST, port=PORT)
