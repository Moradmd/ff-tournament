import os
import json
import time
import urllib.error
import urllib.request

from config import (
    FF_API_BASE,
    FF_API_FLAME_BASE,
    FF_API_LEGACY_BASE,
    FF_SERVER,
    FF_UID_API_KEY,
    FF_UID_API_MOCK,
    FF_UID_API_URL,
)

NAME_KEYS = (
    "name",
    "username",
    "ign",
    "nickname",
    "AccountName",
    "account_name",
    "player_name",
    "playerName",
    "displayName",
)
NEST_KEYS = (
    "data",
    "result",
    "player",
    "user",
    "profile",
    "basicinfo",
    "basicInfo",
    "basic_info",
    "AccountInfo",
    "profileInfo",
    "infos",
)
# apiinfo-flame: BD + PK (BD host often down — PK fallback for same UID)
FLAME_REGIONS = ("BD", "PK")
FLAME_RETRIES = 2
FALLBACK_SERVERS = ("bd", "pk")

# Speed tuning (fast fetch) — env diye control করা যাবে
UID_HTTP_TIMEOUT = float(os.getenv("FF_UID_TIMEOUT", "8"))  # seconds
UID_HTTP_RETRIES = int(os.getenv("FF_UID_RETRIES", "2"))
UID_CACHE_TTL = int(os.getenv("FF_UID_CACHE_TTL", "600"))  # seconds (10 min)

# Simple in-memory cache: (uid, server) -> (expires_at, data)
_uid_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _extract_name(data):
    if isinstance(data, str) and data.strip():
        return data.strip()
    if not isinstance(data, dict):
        return None
    # apiinfo-flame: { "basicInfo": { "nickname": "..." } }
    for key in ("basicInfo", "basic_info", "basicinfo"):
        block = data.get(key)
        if isinstance(block, dict):
            nick = block.get("nickname") or block.get("nick")
            if nick and str(nick).strip():
                return str(nick).strip()
    for k in NAME_KEYS:
        v = data.get(k)
        if v and str(v).strip():
            return str(v).strip()
    for nest in NEST_KEYS:
        if nest in data:
            if nest == "infos" and isinstance(data["infos"], list) and data["infos"]:
                found = _extract_name(data["infos"][0])
                if found:
                    return found
            else:
                found = _extract_name(data[nest])
                if found:
                    return found
    return None


def _http_get_json(url: str, retries: int = FLAME_RETRIES, timeout: float = UID_HTTP_TIMEOUT) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "FF-Tournament/1.0"}
    if FF_UID_API_KEY:
        headers["Authorization"] = f"Bearer {FF_UID_API_KEY}"
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise last_err
    raise last_err


def _server_to_flame_region(server: str) -> str:
    """FF_SERVER → flame region (shudhu BD | PK)."""
    s = (server or FF_SERVER or "bd").strip().lower()
    if s in ("pk", "pak"):
        return "PK"
    if s in ("bd", "bangladesh"):
        return "BD"
    # ind/sg/na — IND call na kore BD try (tournament BD/PK)
    return "BD"


def _uses_path_api() -> bool:
    """Path-based UID API — /bd/{uid} · /pk/{uid}"""
    base = (FF_API_BASE or "").lower()
    return "infoapiuid" in base or "ffuidchack" in base


def _path_endpoints_to_try(server: str) -> list[tuple[str, str]]:
    """(label, path_segment) — bd/pk first by FF_SERVER."""
    s = (server or FF_SERVER or "bd").strip().lower()
    if s in ("pk", "pak"):
        order = ("pk", "bd")
    else:
        order = ("bd", "pk")
    out = [(x, x) for x in order]
    out.append(("info", "info"))
    return out


def _lookup_path_api(uid: str, server: str) -> dict:
    """Path-based UID API — GET /bd/{uid} or /pk/{uid}"""
    base = FF_API_BASE.rstrip("/")
    last_err = None
    for label, segment in _path_endpoints_to_try(server):
        url = f"{base}/{segment}/{uid}"
        try:
            data = _http_get_json(url, retries=max(1, UID_HTTP_RETRIES), timeout=UID_HTTP_TIMEOUT)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode())
                last_err = body.get("error") or body.get("message") or f"HTTP {e.code}"
            except Exception:
                last_err = f"{label.upper()}: HTTP {e.code}"
            continue
        except Exception as e:
            last_err = str(e)
            continue

        if isinstance(data, dict) and data.get("error"):
            last_err = str(data.get("error"))
            continue

        name = _extract_name(data)
        if name:
            reg = label.upper() if label in ("bd", "pk") else _server_to_flame_region(server)
            return {
                "ok": True,
                "name": name,
                "uid": uid,
                "server": reg,
                "source": "infoapiuid",
            }
        last_err = f"{label}: nickname পাওয়া যায়নি"

    err = "UID পাওয়া যায়নি (BD/PK)। সঠিক UID দাও বা Name দিয়ে register করো।"
    if last_err and "decode" in str(last_err).lower():
        err = "API UID decode করতে পারেনি — UID ঠিক আছে কিনা দেখো, না হলে Name দিয়ে register করো।"
    return {"ok": False, "error": err, "detail": last_err}


def _flame_regions_to_try(server: str) -> list[str]:
    preferred = _server_to_flame_region(server)
    order = [preferred]
    for r in FLAME_REGIONS:
        if r not in order:
            order.append(r)
    return order


def _lookup_flame(uid: str, server: str) -> dict:
    """apiinfo-flame — /info?uid=&region=BD|PK (basicInfo.nickname)"""
    base = FF_API_FLAME_BASE.rstrip("/")
    regions = _flame_regions_to_try(server)
    last_err = None
    tried_pk_fallback = False

    for reg in regions:
        url = f"{base}/info?uid={uid}&region={reg}"
        try:
            data = _http_get_json(url, retries=max(1, UID_HTTP_RETRIES), timeout=UID_HTTP_TIMEOUT)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode())
                last_err = body.get("error") or body.get("message") or f"HTTP {e.code}"
            except Exception:
                last_err = f"{reg}: HTTP {e.code}"
            if reg == "PK" and preferred_bd(server):
                tried_pk_fallback = True
            continue
        except Exception as e:
            last_err = str(e)
            continue

        if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
            last_err = str(data.get("error") or data.get("message") or f"{reg}: API error")
            if reg == "PK" and preferred_bd(server):
                tried_pk_fallback = True
            continue

        name = _extract_name(data)
        if name:
            return {
                "ok": True,
                "name": name,
                "uid": uid,
                "server": reg,
                "source": "apiinfo-flame",
            }
        last_err = f"{reg}: nickname পাওয়া যায়নি"

    if preferred_bd(server) and tried_pk_fallback:
        err = "UID BD/PK তে পাওয়া যায়নি। সঠিক UID দাও বা Name দিয়ে register করো।"
    elif preferred_bd(server):
        err = "BD API এখন কাজ করছে না। আবার try করো বা Name দিয়ে register করো।"
    else:
        err = "UID পাওয়া যায়নি। Name দিয়ে register করো।"
    return {"ok": False, "error": err, "detail": last_err}


def preferred_bd(server: str) -> bool:
    return _server_to_flame_region(server) == "BD"


def _lookup_freefire_legacy(uid: str, server: str) -> dict:
    """freefire-api-six — fallback, shudhu bd/pk server"""
    base = FF_API_LEGACY_BASE.rstrip("/")
    server = (server or FF_SERVER or "bd").strip().lower()
    if server not in FALLBACK_SERVERS:
        server = "bd"
    tried = []

    servers_to_try = [server]
    for s in FALLBACK_SERVERS:
        if s not in servers_to_try:
            servers_to_try.append(s)

    last_err = None
    for srv in servers_to_try:
        url = f"{base}/get_player_personal_show?server={srv}&uid={uid}"
        tried.append(srv.upper())
        try:
            data = _http_get_json(url, retries=1, timeout=UID_HTTP_TIMEOUT)
        except urllib.error.HTTPError as e:
            last_err = f"{srv.upper()}: HTTP {e.code}"
            continue
        except Exception as e:
            last_err = str(e)
            continue

        name = _extract_name(data)
        if name:
            return {
                "ok": True,
                "name": name,
                "uid": uid,
                "server": srv.upper(),
                "source": "freefire-api-legacy",
            }
        last_err = f"{srv.upper()}: nickname পাওয়া যায়নি"

    return {
        "ok": False,
        "error": f"UID খুঁজে পাওয়া যায়নি ({', '.join(tried)}). Name দিয়ে register করো।",
        "detail": last_err,
    }


def _lookup_builtin(uid: str, server: str | None) -> dict:
    """Primary FF_API_BASE, then apiinfo-flame, then legacy six API."""
    srv = server or FF_SERVER
    if _uses_path_api():
        result = _lookup_path_api(uid, srv)
        if result["ok"]:
            return result
    else:
        result = _lookup_flame(uid, srv)
        if result["ok"]:
            return result

    flame = _lookup_flame(uid, srv)
    if flame["ok"]:
        return flame
    legacy = _lookup_freefire_legacy(uid, srv)
    if legacy["ok"]:
        return legacy
    return result if _uses_path_api() else flame


def _lookup_custom_url(uid: str) -> dict:
    url = FF_UID_API_URL.replace("{uid}", uid)
    try:
        data = _http_get_json(url)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"API error {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    name = _extract_name(data)
    if not name:
        return {"ok": False, "error": "API থেকে name পাওয়া যায়নি"}
    return {"ok": True, "name": name, "uid": uid, "source": "custom"}


FF_UID_MIN_LEN = 8
FF_UID_MAX_LEN = 12


def is_ff_uid(value: str) -> bool:
    value = (value or "").strip()
    return value.isdigit() and FF_UID_MIN_LEN <= len(value) <= FF_UID_MAX_LEN


def lookup_uid(uid: str, server: str | None = None) -> dict:
    uid = (uid or "").strip()
    if not uid or not uid.isdigit():
        return {"ok": False, "error": "সঠিক UID দাও (শুধু number)"}
    if not is_ff_uid(uid):
        return {
            "ok": False,
            "error": f"UID {FF_UID_MIN_LEN}–{FF_UID_MAX_LEN} digit হতে হবে",
        }

    srv = (server or FF_SERVER or "bd").strip().lower()

    # Cache hit → instant response
    ck = (uid, srv)
    cached = _uid_cache.get(ck)
    if cached:
        exp, data = cached
        if exp > time.time():
            return data
        _uid_cache.pop(ck, None)

    if FF_UID_API_URL:
        return _lookup_custom_url(uid)

    result = _lookup_builtin(uid, srv)
    if result["ok"]:
        # cache success
        _uid_cache[ck] = (time.time() + UID_CACHE_TTL, result)
        return result

    if FF_UID_API_MOCK:
        return {"ok": True, "name": f"Player_{uid[-4:]}", "uid": uid, "mock": True}

    return result


def resolve_player_input(value: str, server: str | None = None) -> dict:
    """একটা field: name লিখলে name, UID digit হলে API lookup."""
    value = (value or "").strip()
    if not value:
        return {"ok": False, "error": "Name বা UID দাও"}
    if is_ff_uid(value):
        return resolve_player("uid", "", value, server)
    return resolve_player("name", value, None, server)


def resolve_player(mode: str, name: str, uid: str | None, server: str | None = None) -> dict:
    mode = (mode or "name").strip().lower()
    name = (name or "").strip()
    uid = (uid or "").strip() or None

    if mode == "uid":
        if not uid:
            return {"ok": False, "error": "UID দাও"}
        res = lookup_uid(uid, server)
        if not res["ok"]:
            return res
        return {
            "ok": True,
            "display_name": res["name"],
            "uid": uid,
            "input_type": "uid",
        }

    if not name:
        return {"ok": False, "error": "In-game name দাও"}
    return {
        "ok": True,
        "display_name": name,
        "uid": None,
        "input_type": "name",
    }
