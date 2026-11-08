"""Auxx-In login. Three ways in:
  - built-in users: DATA_ROOT/users.json, Werkzeug password hashes. The first
    visit (no users yet, no Navidrome) creates the admin.
  - Navidrome users, when Navidrome is on: the login is checked against
    Navidrome; Navidrome admins are Auxx-In admins.
  - a trusted reverse-proxy header (AUTH_PROXY_HEADER, e.g. Remote-User from
    Authelia / Authentik / Tinyauth). Only set it when Auxx-In can't be reached
    except through that proxy.
Sessions are Flask-Login's signed cookies."""
import hashlib
import json
import os
import random
import re
import secrets
import string
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from flask import request
from flask_login import LoginManager, UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

import config

DATA = Path(os.environ.get("DATA_ROOT", "/data"))
USERS_FILE = DATA / "users.json"
PROXY_HEADER = os.environ.get("AUTH_PROXY_HEADER", "").strip()
PROXY_ADMINS = {u.strip() for u in os.environ.get("AUTH_PROXY_ADMINS", "").split(",") if u.strip()}
NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
MIN_PASSWORD = 8
_lock = threading.Lock()


class User(UserMixin):
    def __init__(self, name, source, admin):
        self.name, self.source, self.admin = name, source, bool(admin)

    def get_id(self):
        # Signed into the session cookie, so it can't be edited by the browser.
        return f"{self.source}|{int(self.admin)}|{self.name}"

    def public(self):
        return {"name": self.name, "source": self.source, "admin": self.admin}


# ------------------------------------------------------------------ secrets
def _secret_file(name, nbytes=32):
    """A random value kept in the data folder (made on first run, owner-only)."""
    p = DATA / name
    try:
        v = p.read_text().strip()
        if v:
            return v
    except OSError:
        pass
    DATA.mkdir(parents=True, exist_ok=True)
    v = secrets.token_urlsafe(nbytes)
    p.write_text(v)
    os.chmod(p, 0o600)
    return v


def secret_key():
    return os.environ.get("SECRET_KEY") or _secret_file("secret.key")


# ------------------------------------------------------------------ built-in users
def _load():
    try:
        return json.loads(USERS_FILE.read_text()).get("users", {})
    except (OSError, ValueError):
        return {}


def _save(users):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"users": users}, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(USERS_FILE)


def list_users():
    return [{"name": n, "admin": u.get("admin", False), "created": u.get("created")}
            for n, u in sorted(_load().items(), key=lambda kv: kv[0].lower())]


def needs_setup():
    """First run: nobody can log in yet, so the login page offers to create the admin."""
    return not _load() and not config.navidrome_on() and not PROXY_HEADER


def _check_new(name, password):
    if not NAME_RE.match(name or ""):
        raise ValueError("Usernames are 1-64 letters, numbers, or . _ - @")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD} characters.")


def create_user(name, password, admin=False):
    _check_new(name, password)
    with _lock:
        users = _load()
        if any(n.lower() == name.lower() for n in users):
            raise ValueError("That username is taken.")
        users[name] = {"hash": generate_password_hash(password), "admin": bool(admin), "created": int(time.time())}
        _save(users)


def update_user(name, password=None, admin=None):
    with _lock:
        users = _load()
        if name not in users:
            raise KeyError(name)
        if password is not None:
            _check_new(name, password)
            users[name]["hash"] = generate_password_hash(password)
        if admin is not None:
            if not admin and users[name].get("admin") and _admins(users) == 1 and not config.navidrome_on():
                raise ValueError("Keep at least one admin.")
            users[name]["admin"] = bool(admin)
        _save(users)


def delete_user(name):
    with _lock:
        users = _load()
        if name not in users:
            raise KeyError(name)
        if users[name].get("admin") and _admins(users) == 1 and not config.navidrome_on():
            raise ValueError("Keep at least one admin.")
        del users[name]
        _save(users)


def _admins(users):
    return sum(1 for u in users.values() if u.get("admin"))


def check_local(name, password):
    u = _load().get(name)
    if u and check_password_hash(u["hash"], password or ""):
        return User(name, "local", u.get("admin"))
    return None


# ------------------------------------------------------------------ Navidrome users
def check_navidrome(name, password):
    """The login is good if Navidrome accepts it; its admin flag comes along."""
    if not config.navidrome_on() or not name or not password:
        return None
    salt = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    q = {"u": name, "t": hashlib.md5((password + salt).encode()).hexdigest(), "s": salt,
         "v": "1.16.1", "c": "auxx-in", "f": "json", "username": name}
    url = config.get("navidrome_url").rstrip("/") + "/rest/getUser?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read()).get("subsonic-response", {})
    except Exception:
        return None
    if d.get("status") != "ok":
        return None
    return User(name, "navidrome", (d.get("user") or {}).get("adminRole"))


def check_login(name, password):
    """An Auxx-In admin is an admin. Otherwise, if the same login also works
    in Navidrome and is an admin there, they're an admin here too."""
    local = check_local(name, password)
    if local and local.admin:
        return local
    nd = check_navidrome(name, password)
    if nd and nd.admin:
        return nd
    return local or nd


# ------------------------------------------------------------------ first admin
def setup_code():
    """One-time code the create-admin form asks for, so only someone who can
    read the server's logs can claim a new install. Gone once the admin exists."""
    p = DATA / "setup.code"
    try:
        v = p.read_text().strip()
        if v:
            return v
    except OSError:
        pass
    DATA.mkdir(parents=True, exist_ok=True)
    v = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    p.write_text(v)
    os.chmod(p, 0o600)
    print(f"\n  Auxx-In first run: create the admin at the login page with setup code  {v}\n"
          f"  (also in the data folder: setup.code)\n", flush=True)
    return v


def setup_done():
    (DATA / "setup.code").unlink(missing_ok=True)


def bootstrap():
    """At start: ADMIN_USER + ADMIN_PASSWORD make the admin when there are no
    users yet; otherwise, on a new install, print the setup code."""
    name, pw = os.environ.get("ADMIN_USER", "").strip(), os.environ.get("ADMIN_PASSWORD", "")
    if name and pw and not _load():
        create_user(name, pw, admin=True)
        print(f"Auxx-In: made the admin {name!r} from ADMIN_USER. Remove ADMIN_PASSWORD from .env now.", flush=True)
    if needs_setup():
        setup_code()
    else:
        setup_done()


# ------------------------------------------------------------------ reverse proxy
def proxy_user():
    if not PROXY_HEADER:
        return None
    name = (request.headers.get(PROXY_HEADER) or "").strip()
    if not name:
        return None
    local = _load().get(name)
    return User(name, "proxy", name in PROXY_ADMINS or bool(local and local.get("admin")))


# ------------------------------------------------------------------ Flask-Login
def init(app):
    lm = LoginManager(app)

    @lm.user_loader
    def load(uid):
        try:
            source, admin, name = uid.split("|", 2)
        except ValueError:
            return None
        if source == "local":  # deleted or demoted users take effect at once
            u = _load().get(name)
            return User(name, "local", u.get("admin")) if u else None
        if source == "navidrome":
            return User(name, "navidrome", admin == "1") if config.navidrome_on() else None
        if source == "proxy":
            return User(name, "proxy", admin == "1") if PROXY_HEADER else None
        return None

    return lm


# ------------------------------------------------------------------ command line
if __name__ == "__main__":
    # docker exec -it auxx-in-web python auth.py create-admin [name]
    # Makes an admin, or gives an existing user a new password and admin.
    # The way back in if every admin is locked out.
    import getpass
    import sys
    if len(sys.argv) < 2 or sys.argv[1] != "create-admin":
        sys.exit("usage: python auth.py create-admin [username]")
    name = sys.argv[2] if len(sys.argv) > 2 else input("Username: ").strip()
    pw = getpass.getpass("Password (8+ characters): ")
    if pw != getpass.getpass("Password again: "):
        sys.exit("The passwords don't match.")
    try:
        if name in _load():
            update_user(name, password=pw, admin=True)
            print(f"{name} is an admin now, with the new password.")
        else:
            create_user(name, pw, admin=True)
            print(f"Made the admin {name}.")
        setup_done()
    except ValueError as e:
        sys.exit(str(e))
