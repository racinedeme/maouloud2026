"""
Maouloud 2026 — backend de l'application de gestion de l'association.

Stocke chaque module (cotisations, dépenses, bonnes volontés, bus...) comme un
document JSON dans une base SQLite locale. Chaque collecteur dispose d'un
compte nominatif (nom d'utilisateur + mot de passe) ; toute modification est
tracée dans un journal d'activité consultable par les administrateurs.

Rôles :
  - "admin"      : accès complet + gestion des comptes + journal d'activité
  - "collecteur" : accès complet à la saisie (cotisations, dépenses...) mais
                    ne peut pas gérer les comptes
  - non connecté : consultation seule (rôle "membre" côté frontend)
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "data.db"))
SEED_PATH = BASE_DIR / "seed_data.json"

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changez-ce-mot-de-passe")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
SESSION_HOURS = float(os.environ.get("SESSION_HOURS", "12"))

STORAGE_KEYS = {
    "members": "maouloud2026-members",
    "mobilemoney": "maouloud2026-mobilemoney",
    "bonnesvolontes": "maouloud2026-bonnesvolontes",
    "donsnature": "maouloud2026-donsnature",
    "depenses": "maouloud2026-depenses",
    "buses": "maouloud2026-buses",
    "voyageurs": "maouloud2026-voyageurs",
    "synthese": "maouloud2026-synthese",
    "bustransport": "maouloud2026-bustransport",
    "ziar": "maouloud2026-ziar",
}

# Modules a collecteur account's permissions can be restricted to. A storage
# key can satisfy more than one module (e.g. "members" is written by both the
# Encaissement and Cotisations screens). Keys not listed here (e.g. the
# dashboard's "synthese" report_caisse) are writable by any admin/collecteur
# regardless of per-module permissions.
MODULES = ["encaissement", "cotisations", "mobilemoney", "bonnesvolontes", "ziar", "depenses", "bus"]
STORAGE_KEY_TO_MODULES = {
    "maouloud2026-members": ["encaissement", "cotisations"],
    "maouloud2026-mobilemoney": ["mobilemoney"],
    "maouloud2026-bonnesvolontes": ["bonnesvolontes"],
    "maouloud2026-donsnature": ["bonnesvolontes"],
    "maouloud2026-ziar": ["ziar"],
    "maouloud2026-depenses": ["depenses"],
    "maouloud2026-buses": ["bus"],
    "maouloud2026-voyageurs": ["bus"],
    "maouloud2026-bustransport": ["bus"],
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    with db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            " username TEXT PRIMARY KEY,"
            " password_hash TEXT NOT NULL,"
            " salt TEXT NOT NULL,"
            " display_name TEXT NOT NULL,"
            " role TEXT NOT NULL,"
            " permissions TEXT NOT NULL DEFAULT '[]',"
            " created_at TEXT NOT NULL"
            ")"
        )
        # Migration for databases created before per-module permissions existed.
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "permissions" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN permissions TEXT NOT NULL DEFAULT '[]'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_log ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " username TEXT NOT NULL,"
            " action TEXT NOT NULL,"
            " target TEXT,"
            " detail TEXT,"
            " at TEXT NOT NULL"
            ")"
        )
        audit_cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
        if "detail" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN detail TEXT")

        existing = {row[0] for row in conn.execute("SELECT key FROM kv")}
        if not existing and SEED_PATH.exists():
            seeds = json.loads(SEED_PATH.read_text(encoding="utf-8"))
            for name, storage_key in STORAGE_KEYS.items():
                value = seeds.get(name)
                if value is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)",
                        (storage_key, json.dumps(value, ensure_ascii=False)),
                    )

        has_admin = conn.execute(
            "SELECT 1 FROM users WHERE role='admin' LIMIT 1"
        ).fetchone()
        if not has_admin:
            h, salt = hash_password(ADMIN_PASSWORD)
            conn.execute(
                "INSERT OR REPLACE INTO users (username, password_hash, salt, display_name, role, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (ADMIN_USERNAME, h, salt, "Administrateur", "admin", now_iso()),
            )


def kv_get(key: str):
    with db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None


def kv_set(key: str, value: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def log_action(username: str, action: str, target: str | None = None, detail: str | None = None):
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log (username, action, target, detail, at) VALUES (?,?,?,?,?)",
            (username, action, target, detail, now_iso()),
        )


def _item_label(item) -> str:
    """Best-effort human-readable label for a record in one of our data modules."""
    if not isinstance(item, dict):
        return str(item)
    prenom, nom = item.get("prenom"), item.get("nom")
    if prenom and nom:
        return f"{prenom} {nom}"
    if nom:
        return str(nom)
    for k in ("libelle", "donateur", "operateur"):
        if item.get(k):
            return str(item[k])
    return f"#{item.get('id', '?')}"


def _item_amount(item):
    if not isinstance(item, dict):
        return None
    for k in ("montant", "montant_verse", "avance", "justifie"):
        v = item.get(k)
        if isinstance(v, (int, float)):
            return v
    return None


def diff_summary(old_raw: str | None, new_raw: str, max_len: int = 600) -> str | None:
    """Human-readable summary of what changed between two stored JSON values —
    insertions, modifications and deletions — for the activity log. Returns
    None when nothing actually changed (e.g. a resave of identical data)."""
    try:
        old_val = json.loads(old_raw) if old_raw is not None else None
        new_val = json.loads(new_raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(new_val, list) and (old_val is None or isinstance(old_val, list)):
        old_by_id, new_by_id = {}, {}
        for it in (old_val or []):
            if isinstance(it, dict) and "id" in it:
                old_by_id[it["id"]] = it
        for it in new_val:
            if isinstance(it, dict) and "id" in it:
                new_by_id[it["id"]] = it
        added_ids = [i for i in new_by_id if i not in old_by_id]
        removed_ids = [i for i in old_by_id if i not in new_by_id]
        modified_ids = [i for i in new_by_id if i in old_by_id and new_by_id[i] != old_by_id[i]]

        parts = []
        if added_ids:
            labels = []
            for i in added_ids[:6]:
                it = new_by_id[i]
                amt = _item_amount(it)
                labels.append(_item_label(it) + (f" ({amt})" if amt is not None else ""))
            more = f" (+{len(added_ids)-6} autres)" if len(added_ids) > 6 else ""
            parts.append(f"{len(added_ids)} ajout(s) : " + ", ".join(labels) + more)
        if modified_ids:
            details = []
            for i in modified_ids[:6]:
                old_it, new_it = old_by_id[i], new_by_id[i]
                changed_fields = []
                for k in set(old_it.keys()) | set(new_it.keys()):
                    if k == "id":
                        continue
                    ov, nv = old_it.get(k), new_it.get(k)
                    if ov != nv:
                        changed_fields.append(f"{k}: {ov} → {nv}")
                if changed_fields:
                    details.append(f"{_item_label(new_it)} ({'; '.join(changed_fields[:3])})")
            more = f" (+{len(modified_ids)-6} autres)" if len(modified_ids) > 6 else ""
            parts.append(f"{len(modified_ids)} modification(s) : " + " · ".join(details) + more)
        if removed_ids:
            labels = [_item_label(old_by_id[i]) for i in removed_ids[:6]]
            more = f" (+{len(removed_ids)-6} autres)" if len(removed_ids) > 6 else ""
            parts.append(f"{len(removed_ids)} suppression(s) : " + ", ".join(labels) + more)
        if not parts:
            return None
        summary = " · ".join(parts)
    elif isinstance(new_val, dict):
        old_dict = old_val if isinstance(old_val, dict) else {}
        changed = [f"{k}: {old_dict.get(k)} → {new_val.get(k)}" for k in (set(old_dict.keys()) | set(new_val.keys())) if old_dict.get(k) != new_val.get(k)]
        if not changed:
            return None
        summary = "; ".join(changed)
    else:
        summary = f"Nouvelle valeur : {new_val}"

    return summary[:max_len] + "…" if len(summary) > max_len else summary


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return h.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, expected_hash)


def get_user(username: str):
    with db() as conn:
        row = conn.execute(
            "SELECT username, password_hash, salt, display_name, role, permissions FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if not row:
            return None
        try:
            perms = json.loads(row[5]) if row[5] else []
        except (json.JSONDecodeError, TypeError):
            perms = []
        return {"username": row[0], "password_hash": row[1], "salt": row[2], "display_name": row[3], "role": row[4], "permissions": perms}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

sessions: dict[str, dict] = {}  # token -> {username, role, display_name, expiry}


def create_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    sessions[token] = {
        "username": user["username"],
        "role": user["role"],
        "display_name": user["display_name"],
        "permissions": user.get("permissions", []),
        "expiry": time.time() + SESSION_HOURS * 3600,
    }
    return token


def current_session(request: Request):
    token = request.cookies.get("session")
    s = sessions.get(token) if token else None
    if not s:
        return None
    if s["expiry"] < time.time():
        del sessions[token]
        return None
    return s


def require_writer(request: Request) -> dict:
    """Admin or collecteur — a 'consultation' account is read-only."""
    s = current_session(request)
    if not s:
        raise HTTPException(status_code=401, detail="Connexion requise")
    if s["role"] not in ("admin", "collecteur"):
        raise HTTPException(status_code=403, detail="Compte en lecture seule — écriture non autorisée")
    return s


def require_login(request: Request) -> dict:
    """Any authenticated account (admin, collecteur or consultation). There is
    no anonymous access to the platform's data anymore — every visitor must
    have a named account, even for read-only consultation."""
    s = current_session(request)
    if not s:
        raise HTTPException(status_code=401, detail="Connexion requise")
    return s


def check_module_permission(session: dict, storage_key: str):
    """For a collecteur account, verify the key's module is in their allowed list.
    Empty permissions list means unrestricted (legacy/default full access).
    Admins always pass, enforced by the caller only invoking this for collecteurs."""
    if session["role"] != "collecteur":
        return
    perms = session.get("permissions") or []
    if not perms:
        return
    allowed_modules = STORAGE_KEY_TO_MODULES.get(storage_key, [])
    if allowed_modules and not any(m in perms for m in allowed_modules):
        raise HTTPException(status_code=403, detail="Module non autorisé pour ce compte")


def require_admin(request: Request) -> dict:
    s = current_session(request)
    if not s or s["role"] != "admin":
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs")
    return s


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Maouloud 2026 API")


class LoginBody(BaseModel):
    username: str
    password: str


class StoreBody(BaseModel):
    value: str


class CreateUserBody(BaseModel):
    username: str
    password: str
    display_name: str
    role: str  # 'admin' | 'collecteur' | 'consultation'
    permissions: list[str] = []  # only meaningful for role='collecteur'; empty = unrestricted


class ResetPasswordBody(BaseModel):
    password: str


class SetPermissionsBody(BaseModel):
    permissions: list[str]


@app.on_event("startup")
def _startup():
    init_db()


@app.post("/api/login")
def login(body: LoginBody, response: Response):
    user = get_user(body.username.strip())
    if not user or not verify_password(body.password, user["salt"], user["password_hash"]):
        raise HTTPException(status_code=401, detail="Identifiants incorrects")
    token = create_session(user)
    response.set_cookie(
        "session", token,
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
        max_age=int(SESSION_HOURS * 3600),
    )
    log_action(user["username"], "login")
    return {
        "ok": True, "username": user["username"], "display_name": user["display_name"],
        "role": user["role"], "permissions": user["permissions"],
    }


@app.post("/api/logout")
def logout(request: Request, response: Response):
    s = current_session(request)
    token = request.cookies.get("session")
    if token in sessions:
        del sessions[token]
    if s:
        log_action(s["username"], "logout")
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    s = current_session(request)
    if not s:
        return {"role": "membre"}
    frontend_role = "comite" if s["role"] in ("admin", "collecteur") else "membre"
    return {
        "role": frontend_role,
        "username": s["username"],
        "display_name": s["display_name"],
        "is_admin": s["role"] == "admin",
        "account_role": s["role"],
        "permissions": s.get("permissions", []),
    }


@app.get("/api/store/{key}")
def get_store(key: str, request: Request):
    require_login(request)
    value = kv_get(key)
    if value is None:
        raise HTTPException(status_code=404, detail="Clé introuvable")
    return {"key": key, "value": value}


@app.put("/api/store/{key}")
def put_store(key: str, body: StoreBody, request: Request):
    s = require_writer(request)
    check_module_permission(s, key)
    old_value = kv_get(key)
    kv_set(key, body.value)
    detail = diff_summary(old_value, body.value)
    log_action(s["username"], "update", key, detail)
    return {"key": key, "ok": True}


@app.get("/api/users")
def list_users(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute(
            "SELECT username, display_name, role, permissions, created_at FROM users ORDER BY created_at"
        ).fetchall()
    out = []
    for r in rows:
        try:
            perms = json.loads(r[3]) if r[3] else []
        except (json.JSONDecodeError, TypeError):
            perms = []
        out.append({"username": r[0], "display_name": r[1], "role": r[2], "permissions": perms, "created_at": r[4]})
    return out


@app.post("/api/users")
def create_user(body: CreateUserBody, request: Request):
    s = require_admin(request)
    username = body.username.strip().lower()
    if not username or not body.password or len(body.password) < 4:
        raise HTTPException(status_code=400, detail="Nom d'utilisateur et mot de passe (4+ caractères) requis")
    if body.role not in ("admin", "collecteur", "consultation"):
        raise HTTPException(status_code=400, detail="Rôle invalide")
    if get_user(username):
        raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà")
    perms = [p for p in body.permissions if p in MODULES] if body.role == "collecteur" else []
    h, salt = hash_password(body.password)
    with db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, display_name, role, permissions, created_at) VALUES (?,?,?,?,?,?,?)",
            (username, h, salt, body.display_name.strip() or username, body.role, json.dumps(perms), now_iso()),
        )
    log_action(s["username"], "create_user", username, f"rôle : {body.role}" + (f" · modules : {', '.join(perms)}" if perms else (" · accès complet" if body.role == "collecteur" else "")))
    return {"ok": True}


@app.put("/api/users/{username}/permissions")
def set_permissions(username: str, body: SetPermissionsBody, request: Request):
    s = require_admin(request)
    username = username.strip().lower()
    user = get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    perms = [p for p in body.permissions if p in MODULES]
    with db() as conn:
        conn.execute("UPDATE users SET permissions=? WHERE username=?", (json.dumps(perms), username))
    # Live sessions for that user keep their old permissions until they log back in.
    log_action(s["username"], "set_permissions", username, f"modules : {', '.join(perms)}" if perms else "accès complet")
    return {"ok": True}


@app.delete("/api/users/{username}")
def delete_user(username: str, request: Request):
    s = require_admin(request)
    username = username.strip().lower()
    if username == s["username"]:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte")
    with db() as conn:
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        target = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Compte introuvable")
        if target[0] == "admin" and admin_count <= 1:
            raise HTTPException(status_code=400, detail="Impossible de supprimer le dernier administrateur")
        conn.execute("DELETE FROM users WHERE username=?", (username,))
    log_action(s["username"], "delete_user", username)
    return {"ok": True}


@app.put("/api/users/{username}/password")
def reset_password(username: str, body: ResetPasswordBody, request: Request):
    s = require_admin(request)
    username = username.strip().lower()
    if not get_user(username):
        raise HTTPException(status_code=404, detail="Compte introuvable")
    if len(body.password) < 4:
        raise HTTPException(status_code=400, detail="Mot de passe trop court (4+ caractères)")
    h, salt = hash_password(body.password)
    with db() as conn:
        conn.execute("UPDATE users SET password_hash=?, salt=? WHERE username=?", (h, salt, username))
    log_action(s["username"], "reset_password", username)
    return {"ok": True}


@app.get("/api/audit")
def get_audit(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute(
            "SELECT username, action, target, detail, at FROM audit_log ORDER BY id DESC LIMIT 300"
        ).fetchall()
    return [{"username": r[0], "action": r[1], "target": r[2], "detail": r[3], "at": r[4]} for r in rows]


# Serve the frontend. Declared last so /api/* routes above take priority.
static_dir = BASE_DIR.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
