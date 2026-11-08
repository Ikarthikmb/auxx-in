"""How Auxx-In names and finds songs (songs.py): no network."""
from pathlib import Path

import songs


def cand(title, artists=(), album=None, duration=None):
    return {"title": title, "artists": [{"name": a} for a in artists],
            "album": {"name": album} if album else None, "duration": duration}


def test_movie_songs_go_in_the_movie_album():
    assert songs.album_name(None, 'Kesariya (From "Brahmastra")') == "Brahmastra"
    assert songs.album_name("Pk (Original Motion Picture Soundtrack)", "Love is a Waste of Time") == "Pk"
    assert songs.album_name("Divide", "Perfect") == "Divide"


def test_original_beats_remix_and_cover():
    q = songs.words("perfect ed sheeran")
    original = songs.score(cand("Perfect", ["Ed Sheeran"], "Divide"), q)
    assert original > songs.score(cand("Perfect (Remix)", ["Ed Sheeran"]), q)
    assert original > songs.score(cand("Perfect - Piano Cover", ["Some Channel"]), q)


def test_remix_wins_when_asked_for():
    q = songs.words("perfect remix")
    assert songs.score(cand("Perfect (Remix)", ["Ed Sheeran"]), q) > songs.score(cand("Perfect", ["Ed Sheeran"]), q)


def test_title_must_match_something():
    q = songs.words("kesariya")
    assert songs.score(cand("Something Else", ["Kesariya Band"]), q) < songs.score(cand("Kesariya", ["Arijit Singh"]), q)


def test_duration_breaks_ties():
    q = songs.words("song")
    assert songs.score(cand("Song", duration="3:30"), q, want_secs=210) > songs.score(cand("Song", duration="5:00"), q, want_secs=210)


def test_safe_file_names():
    assert songs.safe_name('AC/DC: "Back" <In> Black?') == "AC-DC Back In Black"
    assert songs.safe_name("  ...  ") == "Unknown"
    spec = {"album_artist": "", "album": "", "title": "", "video_id": "abc123def45"}
    assert songs.rel_path(spec) == Path("Unknown Artist/Singles/abc123def45.mp3")


def test_links_are_recognised():
    assert songs.expand("https://youtu.be/dQw4w9WgXcQ")[0] == [{"kind": "yt", "id": "dQw4w9WgXcQ"}]
    assert songs.expand("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVM")[0][0]["id"] == "dQw4w9WgXcQ"
    assert songs.expand("https://open.spotify.com/intl-de/track/4uLU6hMCjMI75M1A2tKUQC")[0][0]["kind"] == "spotify"
    assert songs.expand("tum hi ho")[0] == [{"kind": "text", "q": "tum hi ho"}]


def test_secs():
    assert songs.secs("1:02:03") == 3723 and songs.secs(95) == 95 and songs.secs("x") is None


def test_ytdlp_uses_cookies_when_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(songs, "COOKIES", tmp_path / "cookies.txt")
    assert songs.ytdlp("-j") == ["yt-dlp", "-j"]
    songs.COOKIES.write_text("x")
    assert songs.ytdlp("-j") == ["yt-dlp", "--cookies", str(songs.COOKIES), "-j"]


def test_lyrics_names_tidy_messy_titles():
    import app
    assert app.lyrics_names("The Score - Victorious (Official Lyric Video)", "The Score") == ("Victorious", "The Score")
    assert app.lyrics_names("Hall of Fame (feat. will.i.am)", "The Script") == ("Hall of Fame", "The Script")
    assert app.lyrics_names('Chhaila (From "Bhoomi 2024")', "Salim–Sulaiman/Shreya Ghoshal")[1] == "Salim–Sulaiman"
    assert app.lyrics_names("Numb", "Linkin Park") == ("Numb", "Linkin Park")


def test_lyrics_are_remembered_but_outages_are_not(monkeypatch):
    import urllib.error
    import app
    app._lyrics.clear()
    calls = []

    def down(path, qs):
        calls.append(path)
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(app, "lrclib", down)
    song = {"title": "Numb", "artist": "Linkin Park", "duration": 185}
    assert app.find_lyrics(song)["found"] is False
    assert app.lyrics_key(song) not in app._lyrics  # asked again next time

    def up(path, qs):
        calls.append(path)
        if path == "get":
            raise urllib.error.HTTPError("u", 404, "not found", {}, None)
        return [{"trackName": "Numb", "duration": 186, "syncedLyrics": "[00:12.50] I'm tired", "plainLyrics": "I'm tired"}]
    monkeypatch.setattr(app, "lrclib", up)
    calls.clear()
    got = app.find_lyrics(song)
    assert got["found"] and got["synced"] == [[12.5, "I'm tired"]]
    app.find_lyrics(song)
    assert calls == ["get", "search"]  # the second time comes from memory


def test_lyrics_prefetch_only_while_someone_uses_them(monkeypatch):
    import app
    asked = []
    monkeypatch.setattr(app, "find_lyrics", lambda n: asked.append(n["title"]))
    monkeypatch.setitem(app.state, "now", {"title": "Numb", "artist": "Linkin Park"})
    monkeypatch.setitem(app._lyrics_used, "at", 0)
    app.prefetch_lyrics()
    assert asked == []  # nobody has opened lyrics: nothing fetched
    monkeypatch.setitem(app._lyrics_used, "at", app.time.time())
    app.prefetch_lyrics()
    for t in app.threading.enumerate():
        if t is not app.threading.current_thread() and t.daemon:
            t.join(0.5)
    assert asked == ["Numb"]


def test_original_beats_cover_naming_the_original_artist():
    import songs
    q = songs.words("rick astley never gonna give you up")
    original = {"title": "Never Gonna Give You Up", "artists": [{"name": "Rick Astley"}],
                "album": {"name": "Whenever You Need Somebody"}, "duration": "3:34"}
    cover = {"title": "Never Gonna Give You Up (Rick Astley)", "artists": [{"name": "Stegosaurus Rex"}],
             "album": {"name": "The Dino Soars"}, "duration": "4:28"}
    assert songs.score(original, q) > songs.score(cover, q)
    dash = {"title": "Believer - Imagine Dragons", "artists": [{"name": "Blanks"}], "album": {"name": "Believer - Imagine Dragons"}}
    real = {"title": "Believer", "artists": [{"name": "Imagine Dragons"}], "album": {"name": "Evolve"}}
    q2 = songs.words("believer imagine dragons")
    assert songs.score(real, q2) > songs.score(dash, q2)
