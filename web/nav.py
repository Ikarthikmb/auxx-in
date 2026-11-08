"""Navidrome (Subsonic API): browse the library, covers, stars, downloads,
stream URLs for the radio, playlists and the Aux radio station."""
import hashlib
import json
import os
import random
import string
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config

MADE_MARK = "Made by Auxx-In"
COVERS = Path(os.environ.get("DATA_ROOT", "/data")) / "covers"


def params(**extra):
    salt = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    pw = config.get("navidrome_pass")
    base = {"u": config.get("navidrome_user"), "t": hashlib.md5((pw + salt).encode()).hexdigest(), "s": salt,
            "v": "1.16.1", "c": "aux", "f": "json"}
    return {**base, **extra}


class Refused(RuntimeError):
    """Navidrome answered, but with an error (wrong login, missing item...)."""


def url(method, **extra):
    if not config.navidrome_on():
        raise RuntimeError("Navidrome is off")
    return f"{config.get('navidrome_url').rstrip('/')}/rest/{method}?" + urllib.parse.urlencode(params(**extra), doseq=True)


def call(method, **extra):
    with urllib.request.urlopen(url(method, **extra), timeout=15) as r:
        d = json.loads(r.read()).get("subsonic-response", {})
    if d.get("status") != "ok":
        raise Refused((d.get("error") or {}).get("message", "navidrome error"))
    return d


def raw(method, **extra):
    """Binary endpoints (cover art, download): the open response."""
    return urllib.request.urlopen(url(method, **extra), timeout=30)


def stream_url(song_id):
    """What the radio plays for a Navidrome song (original file, no transcode)."""
    return url("stream", id=song_id, format="raw")


def song_out(s):
    return {"nd_id": s["id"], "title": s.get("title"), "artist": s.get("artist") or s.get("displayArtist"),
            "album": s.get("album"), "album_id": s.get("albumId"), "cover_id": s.get("coverArt"),
            "duration": s.get("duration"), "starred": bool(s.get("starred")), "year": s.get("year"),
            "track": s.get("track"), "suffix": s.get("suffix")}


def album_out(a):
    return {"id": a["id"], "name": a.get("name") or a.get("title"), "artist": a.get("artist"),
            "artist_id": a.get("artistId"), "cover_id": a.get("coverArt"), "year": a.get("year"),
            "songs": a.get("songCount"), "starred": bool(a.get("starred"))}


def artists():
    idx = (call("getArtists").get("artists") or {}).get("index") or []
    out = []
    for i in idx:
        for a in i.get("artist") or []:
            out.append({"id": a["id"], "name": a.get("name"), "albums": a.get("albumCount"),
                        "cover_id": a.get("coverArt"), "starred": bool(a.get("starred"))})
    return out


def artist(aid):
    a = call("getArtist", id=aid).get("artist") or {}
    return {"id": a.get("id"), "name": a.get("name"), "cover_id": a.get("coverArt"),
            "albums": [album_out(x) for x in a.get("album") or []]}


def album(aid):
    a = call("getAlbum", id=aid).get("album") or {}
    return {**album_out(a), "songs": [song_out(s) for s in a.get("song") or []]}


def albums(kind="newest", size=40, offset=0):
    r = call("getAlbumList2", type=kind, size=size, offset=offset).get("albumList2") or {}
    return [album_out(a) for a in r.get("album") or []]


def playlists():
    pls = (call("getPlaylists").get("playlists") or {}).get("playlist") or []
    return [{"id": p["id"], "name": p.get("name"), "songs": p.get("songCount"), "cover_id": p.get("coverArt"),
             "duration": p.get("duration"), "comment": p.get("comment") or "",
             # made by Auxx-In (the daily playlist, the IQ picks): a ✦ on the page.
             # "Made by Aux" also matches playlists from before the rename.
             "made": (p.get("comment") or "").startswith("Made by Aux") or p.get("name") == "Aux IQ"} for p in pls]


def playlist(pid):
    p = call("getPlaylist", id=pid).get("playlist") or {}
    return {"id": p.get("id"), "name": p.get("name"), "cover_id": p.get("coverArt"),
            "songs": [song_out(s) for s in p.get("entry") or []]}


def starred():
    s = call("getStarred2").get("starred2") or {}
    return {"songs": [song_out(x) for x in s.get("song") or []],
            "albums": [album_out(x) for x in s.get("album") or []]}


def search(q, songs=20, albums_n=8, artists_n=6):
    r = call("search3", query=q, songCount=songs, albumCount=albums_n, artistCount=artists_n).get("searchResult3") or {}
    return {"songs": [song_out(s) for s in r.get("song") or []],
            "albums": [album_out(a) for a in r.get("album") or []],
            "artists": [{"id": a["id"], "name": a.get("name"), "albums": a.get("albumCount"),
                         "cover_id": a.get("coverArt")} for a in r.get("artist") or []]}


def song(sid):
    return song_out(call("getSong", id=sid).get("song") or {})


def random_songs(size=50, genre=None):
    kw = {"size": size}
    if genre:
        kw["genre"] = genre
    return [song_out(s) for s in (call("getRandomSongs", **kw).get("randomSongs") or {}).get("song") or []]


def star(item_id, on=True):
    call("star" if on else "unstar", id=item_id)


def cover(cover_id, size=300):
    """Cover art bytes, cached on disk (covers rarely change)."""
    COVERS.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in f"{cover_id}-{size}" if c.isalnum() or c in "-_")
    f = COVERS / f"{safe}.img"
    if f.exists() and time.time() - f.stat().st_mtime < 30 * 86400:
        return f.read_bytes()
    with raw("getCoverArt", id=cover_id, size=size) as r:
        data = r.read()
    f.write_bytes(data)
    return data
