import config
import nav
from conftest import post


def test_secrets_are_never_sent_back(admin):
    post(admin, "/api/config", {"llm_key": "sk-secret", "navidrome_pass": "pw"})
    body = admin.get("/api/config").get_data(as_text=True)
    assert "sk-secret" not in body and '"pw"' not in body
    assert config.get("llm_key") == "sk-secret"


def test_blank_secret_keeps_the_saved_one(admin):
    post(admin, "/api/config", {"llm_key": "sk-1"})
    post(admin, "/api/config", {"llm_key": ""})
    assert config.get("llm_key") == "sk-1"


def test_numbers_and_choices_are_checked(admin):
    r = post(admin, "/api/config", {"playlist_cap": "12", "mp3_quality": "999", "bogus": 1}).get_json()
    assert r["changed"] == ["playlist_cap"] and config.get("playlist_cap") == 12
    assert config.get("mp3_quality") == "0"


def test_cookies_file(admin):
    assert post(admin, "/api/config", {"youtube_cookies": "not cookies"}).status_code == 400
    ok = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx"
    assert post(admin, "/api/config", {"youtube_cookies": ok}).status_code == 200
    assert config.FILES["youtube_cookies"].stat().st_mode & 0o777 == 0o600
    post(admin, "/api/config", {"youtube_cookies": False})
    assert not config.FILES["youtube_cookies"].exists()


def test_navidrome_off_without_an_address(admin):
    assert admin.get("/api/state").get_json()["navidrome"] is False
    assert admin.get("/api/lib/home").status_code == 503
    post(admin, "/api/config", {"navidrome_url": "http://nd:4533/"})
    assert admin.get("/api/state").get_json()["navidrome"] is True
    assert nav.url("ping").startswith("http://nd:4533/rest/ping?")
    post(admin, "/api/config", {"use_navidrome": "false"})
    assert admin.get("/api/state").get_json()["navidrome"] is False


def test_library_says_when_navidrome_login_is_missing(admin):
    import config
    config.save({"use_navidrome": True, "navidrome_url": "http://127.0.0.1:9", "navidrome_user": "", "navidrome_pass": ""})
    r = admin.get("/api/lib/home")
    assert r.status_code == 503
    assert "username and password" in r.get_json()["error"]


def test_download_limits(monkeypatch, tmp_path):
    import pytest
    import songs
    monkeypatch.setattr(songs, "MUSIC", tmp_path)
    config.save({"max_song_mb": 100, "keep_free_gb": 0})
    songs.check_room({"duration": 240})  # a 4-minute song: fine
    with pytest.raises(RuntimeError, match="Too long"):
        songs.check_room({"duration": 3 * 3600})
    config.save({"max_song_mb": 0})  # 0 = no limit
    songs.check_room({"duration": 3 * 3600})
    config.save({"keep_free_gb": 10 ** 6})
    with pytest.raises(RuntimeError, match="Disk almost full"):
        songs.check_room({"duration": 240})


def test_env_comment_is_not_a_value(monkeypatch):
    import config
    monkeypatch.setenv("NAVIDROME_URL", "# e.g. http://navidrome:4533")
    assert config.get("navidrome_url") == ""
    assert config.navidrome_on() is False
