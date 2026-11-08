import json

import auth
import config
from conftest import csrf, post


def test_first_run_needs_setup_and_blocks_the_app(client):
    me = client.get("/api/me").get_json()
    assert me["setup"] is True and me["user"] is None
    assert client.get("/api/state").status_code == 401
    assert client.get("/").status_code == 302


def test_setup_creates_admin_once(client):
    r = post(client, "/api/setup", {"username": "admin", "password": "correct horse", "code": auth.setup_code()})
    assert r.status_code == 200 and r.get_json()["user"] == {"name": "admin", "source": "local", "admin": True}
    assert client.get("/api/state").status_code == 200
    assert post(client, "/api/setup", {"username": "x", "password": "another one", "code": "x"}).status_code == 409


def test_setup_rejects_short_password(client):
    r = post(client, "/api/setup", {"username": "admin", "password": "short", "code": auth.setup_code()})
    assert r.status_code == 400 and not auth.list_users()


def test_post_without_csrf_token_is_refused(client):
    r = client.post("/api/setup", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 400 and not auth.list_users()


def test_login_logout(admin):
    post(admin, "/api/logout")
    assert admin.get("/api/state").status_code == 401
    assert post(admin, "/api/login", {"username": "admin", "password": "wrong password"}).status_code == 401
    assert post(admin, "/api/login", {"username": "admin", "password": "correct horse"}).status_code == 200
    assert admin.get("/api/state").status_code == 200


def test_login_is_rate_limited(client):
    auth.create_user("bob", "correct horse")
    codes = [post(client, "/api/login", {"username": "bob", "password": "nope nope"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_listener_cannot_reach_admin_pages(admin, client):
    post(admin, "/api/users", {"username": "lis", "password": "listener pw"})
    post(admin, "/api/logout")
    post(admin, "/api/login", {"username": "lis", "password": "listener pw"})
    assert admin.get("/api/state").status_code == 200
    assert admin.get("/api/config").status_code == 403
    assert admin.get("/api/users").status_code == 403
    assert post(admin, "/api/delete-file/x").status_code == 403


def test_user_management(admin):
    assert post(admin, "/api/users", {"username": "amy", "password": "amy password"}).status_code == 200
    assert post(admin, "/api/users", {"username": "AMY", "password": "amy password"}).status_code == 400  # taken
    assert post(admin, "/api/users", {"username": "bad name!", "password": "amy password"}).status_code == 400
    assert post(admin, "/api/users/amy", {"admin": True}).status_code == 200
    assert {u["name"]: u["admin"] for u in admin.get("/api/users").get_json()["users"]} == {"admin": True, "amy": True}
    r = admin.delete("/api/users/admin", headers={"X-CSRFToken": csrf(admin)})
    assert r.status_code == 400  # can't delete yourself
    assert admin.delete("/api/users/amy", headers={"X-CSRFToken": csrf(admin)}).status_code == 200


def test_last_admin_stays_admin(admin):
    assert post(admin, "/api/users/admin", {"admin": False}).status_code == 400


def test_deleted_user_is_logged_out(admin, client):
    post(admin, "/api/users", {"username": "amy", "password": "amy password"})
    other = client.application.test_client()
    post(other, "/api/login", {"username": "amy", "password": "amy password"})
    assert other.get("/api/state").status_code == 200
    admin.delete("/api/users/amy", headers={"X-CSRFToken": csrf(admin)})
    assert other.get("/api/state").status_code == 401


def test_change_own_password(admin):
    assert post(admin, "/api/password", {"old": "wrong", "new": "brand new pw"}).status_code == 400
    assert post(admin, "/api/password", {"old": "correct horse", "new": "brand new pw"}).status_code == 200
    assert auth.check_local("admin", "brand new pw")


def test_navidrome_login(client, monkeypatch):
    config.save({"navidrome_url": "http://navidrome:4533"})

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return json.dumps(self.body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url, timeout=0):
        ok = "u=nd-admin" in url
        return Resp({"subsonic-response": {"status": "ok", "user": {"adminRole": True}} if ok else {"status": "failed"}})

    monkeypatch.setattr(auth.urllib.request, "urlopen", fake)
    assert client.get("/api/me").get_json()["setup"] is False  # Navidrome users can log in: no setup screen
    assert post(client, "/api/login", {"username": "stranger", "password": "whatever"}).status_code == 401
    r = post(client, "/api/login", {"username": "nd-admin", "password": "whatever"})
    assert r.get_json()["user"] == {"name": "nd-admin", "source": "navidrome", "admin": True}
    assert client.get("/api/config").status_code == 200
    config.save({"use_navidrome": False})  # Navidrome switched off: its sessions end
    assert client.get("/api/state").status_code == 401


def test_proxy_header(client, monkeypatch):
    monkeypatch.setattr(auth, "PROXY_HEADER", "Remote-User")
    monkeypatch.setattr(auth, "PROXY_ADMINS", {"boss"})
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"Remote-User": "pat"}).status_code == 200
    assert client.get("/api/config", headers={"Remote-User": "pat"}).status_code == 403
    assert client.get("/api/config", headers={"Remote-User": "boss"}).status_code == 200


def test_add_is_rate_limited(admin):
    codes = [post(admin, "/api/add", {"q": ""}).status_code for _ in range(4)]
    assert codes == [400, 400, 400, 429]  # LIMIT_ADD is "3 per hour" in tests


def test_internal_calls_need_the_key_not_a_login(client):
    assert client.get("/internal/next").status_code == 403
    assert client.get("/internal/next?key=test-key").status_code == 403  # header only, kept out of logs
    assert client.get("/internal/next", headers={"X-Internal-Key": "test-key"}).status_code == 200


def test_admin_cannot_change_own_role(admin):
    r = post(admin, "/api/users/admin", {"admin": False})
    assert r.status_code == 400
    assert post(admin, "/api/users", {"username": "bob", "password": "longenough"}).status_code == 200
    assert post(admin, "/api/users/bob", {"admin": True}).status_code == 200


def test_storage_is_admin_only(admin):
    d = admin.get("/api/storage").get_json()
    assert {"songs", "music", "data", "disk_free", "disk_total"} <= d.keys()
    post(admin, "/api/users", {"username": "bob", "password": "longenough"})
    post(admin, "/api/logout")
    post(admin, "/api/login", {"username": "bob", "password": "longenough"})
    assert admin.get("/api/storage").status_code == 403


def test_setup_needs_the_code_from_the_log(client):
    r = post(client, "/api/setup", {"username": "admin", "password": "correct horse", "code": "WRONG"})
    assert r.status_code == 403 and not auth.list_users()
    code = auth.setup_code()
    assert post(client, "/api/setup", {"username": "admin", "password": "correct horse", "code": code.lower()}).status_code == 200
    assert not (auth.DATA / "setup.code").exists()  # one use only


def test_admin_from_env_once(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct horse")
    auth.bootstrap()
    assert auth.list_users()[0]["name"] == "boss" and auth.list_users()[0]["admin"]
    monkeypatch.setenv("ADMIN_PASSWORD", "changed later")
    auth.bootstrap()  # users exist: ignored
    assert auth.check_local("boss", "correct horse")


def test_navidrome_admin_wins_over_same_name_listener(monkeypatch):
    auth.create_user("sam", "listener-pass")
    monkeypatch.setattr(auth, "check_navidrome", lambda n, p: auth.User(n, "navidrome", True))
    u = auth.check_login("sam", "listener-pass")
    assert u.admin and u.source == "navidrome"
    monkeypatch.setattr(auth, "check_navidrome", lambda n, p: auth.User(n, "navidrome", False))
    u = auth.check_login("sam", "listener-pass")
    assert not u.admin and u.source == "local"  # keep the Auxx In account (its password can change here)
