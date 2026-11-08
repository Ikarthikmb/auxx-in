"""Auxx In: share a song (or a playlist), it plays next on the Aux radio.

The Aux page is the remote (and a player): now playing, up next, the radio
switch, what plays when the queue is empty, sleep timer, downloads, the
Navidrome library and YouTube / YouTube Music search with previews. The
aux-radio container (Liquidsoap) asks this app for each next song.

Finding and downloading songs lives in songs.py, Navidrome in nav.py.
Needs yt-dlp, ffmpeg, ytmusicapi, mutagen and flask. Configuration (Navidrome,
stream address, Aux IQ) lives in config.py: environment defaults, overridden
from Settings -> Configuration.
"""
import json
import logging
import os
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date
from pathlib import Path

from datetime import timedelta

from flask import Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory
from flask_limiter import Limiter
from flask_login import current_user, login_user, logout_user
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import auth
import config
import nav
import songs
from songs import MUSIC, DATA, log, norm, words

STATIC = Path(__file__).with_name("static")
RADIO_HOST = os.environ.get("RADIO_HOST", "aux-radio")
RADIO_TELNET = int(os.environ.get("RADIO_TELNET", "1234"))
if DATA.is_dir() and not os.access(DATA, os.W_OK):
    sys.exit(f"Auxx In can't write to its data folder (AUX_DATA in .env; {DATA} in the container) as user "
             f"{os.getuid()}:{os.getgid()}. Make that folder owned by PUID:PGID from .env, e.g.\n"
             f"  sudo chown -R {os.getuid()}:{os.getgid()} ./data\nthen: docker compose up -d")
INTERNAL_KEY = os.environ.get("INTERNAL_KEY", "")
if not INTERNAL_KEY:
    # No key set: make one on first run and share it with the radio via the data folder.
    _key_file = DATA / "internal.key"
    try:
        INTERNAL_KEY = _key_file.read_text().strip()
    except OSError:
        pass
    if not INTERNAL_KEY:
        DATA.mkdir(parents=True, exist_ok=True)
        INTERNAL_KEY = secrets.token_urlsafe(24)
        _key_file.write_text(INTERNAL_KEY)
        os.chmod(_key_file, 0o600)
FALLBACK_M3U = DATA / "fallback.m3u"

app = Flask(__name__, static_folder=None)
app.secret_key = auth.secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    # Set COOKIE_SECURE=true when Auxx In is served over HTTPS.
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() == "true",
    REMEMBER_COOKIE_HTTPONLY=True, REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    WTF_CSRF_TIME_LIMIT=None,  # the token lives as long as the session
)
auth.init(app)
csrf = CSRFProtect(app)


def _limit_key():
    """Per signed-in user; per address before login."""
    return "u:" + current_user.name if current_user.is_authenticated else "ip:" + (request.remote_addr or "")


def _login_key():
    name = ((request.get_json(silent=True) or {}).get("username") or "").lower()
    return f"login:{request.remote_addr}:{name}"


limiter = Limiter(_limit_key, app=app, storage_uri="memory://")
LIMIT_ADD = os.environ.get("LIMIT_ADD", "60 per hour")        # shares (a playlist counts once)
LIMIT_SEARCH = os.environ.get("LIMIT_SEARCH", "60 per minute")  # YouTube search / preview / links
LIMIT_LOGIN = "10 per minute;50 per hour"
lock = threading.RLock()
work = threading.Condition(lock)

# ------------------------------------------------------------------ state
# queue:    songs waiting to play, in order. status: pending | resolving |
#           downloading | ready | loaded (the radio has it lined up) | failed
# jobs:     "Save" downloads that don't go in the queue
# now:      what the radio is playing; history: what it played
# recent:   songs Aux saved (Downloads tab, Navidrome sync)
# settings: what plays when the queue is empty, even volume, crossfade
STATE_FILE = DATA / "state.json"
DEFAULT_SETTINGS = {"empty": {"mode": "library", "id": None, "name": None},
                    "even_volume": True, "crossfade": True,
                    # fade: seconds of fade between songs, 0/5/10/15 (0 = off),
                    # for the page and the radio; "crossfade" follows it
                    "fade": 0,
                    # "device": the Aux page plays the queue itself; "radio": the
                    # aux-radio stream does (any Navidrome client, several listeners).
                    "play_on": "radio"}
# iq:       Aux IQ (on, mood, today's download count, recent picks)
# taste:    what Aux IQ has learnt: song / artist scores, recent likes and skips
state = {"queue": [], "jobs": [], "now": None, "history": [], "recent": [],
         "settings": dict(DEFAULT_SETTINGS), "sleep_at": None, "autoplay": {"date": "", "count": 0},
         "device_cmd": None,
         "daily": {"id": None, "date": "", "name": None},
         "iq": {"on": False, "mood": None, "date": "", "downloads": 0, "picked": []},
         "taste": {"songs": {}, "artists": {}, "rated": {}, "log": []}}


def save_state():
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_FILE)


def load_state():
    try:
        state.update(json.loads(STATE_FILE.read_text()))
    except (OSError, ValueError):
        pass
    saved = state.get("settings") or {}
    state["settings"] = {**DEFAULT_SETTINGS, **saved}
    if "fade" not in saved:  # the old on/off crossfade becomes the fade length, off to start
        state["settings"]["crossfade"] = False
    state["daily"] = {"id": None, "date": "", "name": None, **(state.get("daily") or {})}
    state["iq"] = {"on": False, "mood": None, "date": "", "downloads": 0, "picked": [], **(state.get("iq") or {})}
    state["taste"] = {"songs": {}, "artists": {}, "rated": {}, "log": [], **(state.get("taste") or {})}
    for it in state["queue"] + state["jobs"]:
        # Anything mid-flight when we stopped is retried. "loaded" songs stay
        # loaded: the radio still holds them (it calls /internal/reset when
        # it restarts itself).
        if it["status"] in ("resolving", "downloading"):
            it["status"] = "pending"


def cover_of(it):
    if not it:
        return None
    if it.get("cover_id"):
        return "api/lib/cover/" + urllib.parse.quote(str(it["cover_id"]))
    if it.get("path") or it.get("cover_url"):
        return "api/cover/" + it["id"]
    return None


def public(it):
    keys = ("id", "title", "artist", "album", "status", "error", "list_name", "from", "by",
            "progress", "nd_id", "path", "duration", "added", "why")
    d = {k: it.get(k) for k in keys}
    d["cover"] = cover_of(it)
    return d


def find_item(item_id):
    with lock:
        for it in state["queue"] + state["jobs"] + state["recent"] + state["history"] + ([state["now"]] if state["now"] else []) + [_device_peek.get("now")]:
            if it and it.get("id") == item_id:
                return it
    return None


def new_id():
    return uuid.uuid4().hex[:10]


# ------------------------------------------------------------------ adding songs
def place(items, where):
    """Put new queue items where asked: next (ahead of everything), end, now."""
    with lock:
        if where == "save":
            for it in items:
                it["save_only"] = True
            state["jobs"].extend(items)
        elif where in ("next", "now"):
            state["queue"][0:0] = items
        elif any(i.get("by") != "iq" for i in items):
            # Your own songs go ahead of Aux IQ's picks.
            at = next((n for n, i in enumerate(state["queue"]) if i.get("by") == "iq"), len(state["queue"]))
            state["queue"][at:at] = items
        else:
            state["queue"].extend(items)
        save_state()
    if where in ("next", "now"):
        reset_loaded()  # so the radio lines up the new head, not its old pick
    with work:
        work.notify_all()


def add(q, where="end", by="you", exact=False):
    """Links / names -> queue. The first song is resolved right away (so Siri
    can say what it is), the rest in the background. Returns (message, items)."""
    works, list_name = songs.expand(q)
    if exact and len(works) == 1 and works[0]["kind"] == "yt":
        works = [{"kind": "ytexact", "id": works[0]["id"]}]
    if not works:
        return "Nothing found to add.", []
    capped = len(works) > config.get("playlist_cap")
    works = works[:config.get("playlist_cap")]
    items = [{"id": new_id(), "input": q, "work": w, "status": "pending",
              "title": w.get("title") or (w.get("q") if w["kind"] == "text" else None),
              "artist": ", ".join(a for a in w.get("artists") or [] if a) or None,
              "list_name": list_name, "by": by, "added": time.time()} for w in works]
    place(items, where)
    first = items[0]
    process(first)  # synchronous for the first one
    if where == "now" and first["status"] == "ready":
        jump(first["id"])
    verb = {"next": "Playing next", "now": "Playing now", "save": "Saved", "end": "Added to the queue"}.get(where, "Added")
    if first["status"] == "failed":
        msg = "Couldn't find that song." if len(items) == 1 else f"Couldn't find the first song; adding the other {len(items) - 1}."
    elif len(items) == 1:
        msg = f"{verb}: {first['title']} by {first['artist']}."
    else:
        msg = f"{verb}: {len(items)} songs{' from ' + list_name if list_name else ''}, starting with {first['title']}."
        if capped:
            msg += f" (Capped at {config.get('playlist_cap')}.)"
    return msg, items


def nd_item(s, by="you"):
    """A Navidrome song as a queue item: ready at once, the radio streams it."""
    return {"id": new_id(), "nd_id": s["nd_id"], "status": "ready", "title": s.get("title"),
            "artist": s.get("artist"), "album": s.get("album"), "cover_id": s.get("cover_id"),
            "duration": s.get("duration"), "from": "Library", "by": by, "added": time.time()}


def process(it):
    with lock:
        if it["status"] != "pending":
            return
        it["status"] = "resolving"
        it["error"] = None
    try:
        spec = songs.resolve(it["work"])
        if not spec:
            raise RuntimeError("no matching song found")
        with lock:
            it.update({k: spec[k] for k in ("title", "artist", "album", "cover_url", "from", "video_id")})
            it["status"] = "downloading"
            it["progress"] = 0
            save_state()

        def progress(pct):
            it["progress"] = pct

        rel = songs.download(spec, progress)
        with lock:
            it["path"] = str(rel)
            it["progress"] = 100
            it["status"] = "ready"
            try:
                it["duration"] = int(MP3(MUSIC / rel).info.length)
            except Exception:
                pass
            state["recent"].insert(0, {k: it.get(k) for k in ("id", "title", "artist", "album", "path", "list_name",
                                                               "added", "by", "video_id", "cover_url", "daily")})
            state["recent"][0]["saved"] = time.time()
            state["recent"] = state["recent"][:300]
            if it.get("save_only") and it in state["jobs"]:
                state["jobs"].remove(it)
            save_state()
        log("ready:", rel)
    except Exception as e:
        log("failed:", it.get("input"), e)
        with lock:
            it["status"] = "failed"
            it["error"] = str(e)[:200]
            save_state()


def worker():
    while True:
        with work:
            it = next((i for i in state["queue"] + state["jobs"] if i["status"] == "pending"), None)
            if not it:
                work.wait(30)
                continue
        process(it)


# ------------------------------------------------------------------ radio control
def radio(cmd):
    try:
        with socket.create_connection((RADIO_HOST, RADIO_TELNET), timeout=5) as s:
            s.sendall((cmd + "\nquit\n").encode())
            out = b""
            while True:
                b = s.recv(4096)
                if not b:
                    break
                out += b
        return out.decode(errors="replace").replace("END", "").replace("Bye!", "").strip()
    except OSError as e:
        log("radio command failed:", cmd, e)
        return None


def radio_on():
    s = radio("aux.status")
    return None if s is None else s.strip().startswith("on")


def reset_loaded():
    """Undo the radio's prefetch so a reorder / play-next takes effect."""
    with lock:
        for it in state["queue"]:
            if it["status"] == "loaded":
                it["status"] = "ready"
        save_state()
    radio("refetch")


def device_mode():
    return state["settings"].get("play_on") == "device"


def device_command(kind="next"):
    """Tell the page that's playing (it checks every few seconds)."""
    with lock:
        state["device_cmd"] = {"type": kind, "at": time.time()}
        save_state()


def jump(item_id, drop_before=False):
    no_judge()
    with lock:
        it = next((i for i in state["queue"] if i["id"] == item_id), None)
        if not it:
            return False
        if drop_before:
            del state["queue"][:state["queue"].index(it)]
        state["queue"].remove(it)
        state["queue"].insert(0, it)
        save_state()
    if device_mode():
        if it["status"] in ("ready", "loaded"):
            device_command("next")
        return True
    radio_play_head(it)
    return True


def radio_play_head(it=None):
    """Make the radio drop what it has lined up (and the song it's on) and
    play the queue head now. Songs only change at a track end otherwise."""
    reset_loaded()
    # Give the radio a moment to fetch the new head before skipping to it.
    for _ in range(24):
        with lock:
            if it is None or it["status"] == "loaded":
                break
        time.sleep(0.25)
    # "loaded" = handed out; a Navidrome song still has to download before the
    # radio can switch to it, or the skip lands on a library song instead.
    for _ in range(40):
        q = radio("qlen")
        if it is None or q is None or (q.strip().isdigit() and int(q.strip()) >= 1):
            break
        time.sleep(0.25)
    radio("skip")
    if it is None or it.get("status") not in ("ready", "loaded"):
        return
    # Check it took: the radio reports the song (and it leaves the queue). A
    # radio that was just started can let the skip fall on its old song.
    for attempt in range(2):
        for _ in range(24):
            with lock:
                if not any(q is it for q in state["queue"]):
                    return
            time.sleep(0.25)
        log("radio didn't switch to", it.get("title"), "- skipping again")
        radio("skip")


NOW_KEYS = ("id", "title", "artist", "album", "path", "nd_id", "cover_id", "cover_url", "by", "video_id", "duration", "why")


def as_queue_item(e, resume=0):
    """A played (or playing) song back into the queue, ready to play again.
    resume: start this many seconds in (switching players mid-song)."""
    if not e or not (e.get("path") or e.get("nd_id")):
        return None
    it = {k: e.get(k) for k in NOW_KEYS}
    it.update(status="ready", added=time.time(), keep_from=e.get("from"))
    if resume and resume > 3 and resume < (e.get("duration") or 1e9) - 5:
        it["resume"] = round(float(resume), 1)
    return it


def drop_now_from_history():
    """state["now"] leaves history[0]; it goes back when it plays again (lock held)."""
    n, h = state["now"], state["history"]
    if n and h and h[0].get("id") == n.get("id"):
        h.pop(0)


def write_fallback():
    """What the radio plays when the queue is empty, as an m3u it reloads."""
    mode = state["settings"]["empty"]
    lines = []
    try:
        if mode["mode"] in ("library", "similar", "iq"):
            lines = [str(MUSIC / e["path"]) for e in songs.library]
        elif mode["mode"] == "requests":
            pl = next((p for p in nav.playlists() if p["name"] == config.get("requests_playlist")), None)
            lines = [nav.stream_url(s["nd_id"]) for s in (nav.playlist(pl["id"])["songs"] if pl else [])]
        elif mode["mode"] == "playlist" and mode.get("id"):
            lines = [nav.stream_url(s["nd_id"]) for s in nav.playlist(mode["id"])["songs"]]
        elif mode["mode"] == "artist" and mode.get("id"):
            for a in nav.artist(mode["id"])["albums"][:30]:
                lines += [nav.stream_url(s["nd_id"]) for s in nav.album(a["id"])["songs"]]
        elif mode["mode"] == "silence":
            lines = []
    except Exception as e:
        log("fallback list failed, using the library:", e)
        lines = [str(MUSIC / e["path"]) for e in songs.library]
    tmp = FALLBACK_M3U.with_suffix(".tmp")
    tmp.write_text("#EXTM3U\n" + "\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(FALLBACK_M3U)
    radio("library.reload")


def apply_radio_settings():
    st = state["settings"]
    if st.get("play_on") == "device":
        radio("aux.stop")
    radio(f"var.set even_volume = {'true' if st['even_volume'] else 'false'}")
    radio(f"var.set crossfade = {'true' if st['crossfade'] else 'false'}")
    # The radio reads the fade length when it starts: a new length restarts it
    # (a second of silence; it calls /internal/reset and gets all this again).
    if st.get("fade"):
        f = DATA / "crossfade"
        try:
            same = f.read_text().strip() == str(st["fade"])
        except OSError:
            same = False
        if not same:
            f.write_text(str(st["fade"]))
            radio("restart")
    write_fallback()


def timers_loop():
    """Sleep timer, refreshing the empty-queue list, and similar-songs autoplay."""
    last_fallback = time.time()
    while True:
        time.sleep(5)
        try:
            with lock:
                at = state.get("sleep_at")
            if at and time.time() >= at:
                radio("aux.stop")
                with lock:
                    state["sleep_at"] = None
                    save_state()
                log("sleep timer: radio stopped")
            if time.time() - last_fallback > 1800:
                last_fallback = time.time()
                write_fallback()
            autoplay_tick()
            iq_tick()
            daily_tick()
        except Exception as e:
            log("timers:", e)


# ------------------------------------------------------------------ similar songs
_seed_cache = {}


def seed_video(it):
    if it.get("video_id"):
        return it["video_id"]
    key = f"{it.get('title')}|{it.get('artist')}"
    if key not in _seed_cache:
        r = songs.search_song(f"{it.get('title')} {it.get('artist') or ''}".strip())
        _seed_cache[key] = r["videoId"] if r else None
    return _seed_cache[key]


def autoplay_tick():
    """When the queue runs dry and "similar songs" is chosen, add three songs
    like the last ones played (YouTube Music's radio for that song)."""
    with lock:
        if state["settings"]["empty"]["mode"] != "similar":
            return
        if any(i["status"] != "failed" for i in state["queue"]):
            return
        today = date.today().isoformat()
        ap = state["autoplay"]
        if ap.get("date") != today:
            ap.update(date=today, count=0)
        if ap["count"] >= config.get("autoplay_daily"):
            return
        seeds = [h for h in state["history"][:3] if h.get("title")]
        played = {norm(h.get("title")) for h in state["history"][:60]} | {norm(r.get("title")) for r in state["recent"][:60]}
    playing = (device_mode() and time.time() - state.get("device_seen", 0) < 60) or (not device_mode() and radio_on())
    if not seeds or not playing:
        return
    vid = seed_video(random.choice(seeds))
    if not vid:
        return
    tracks = songs.ytm().get_watch_playlist(videoId=vid, limit=15).get("tracks") or []
    picks = [t for t in tracks[1:] if t.get("videoId") and norm(t.get("title")) not in played][:3]
    if not picks:
        return
    items = [{"id": new_id(), "input": "similar songs", "work": {"kind": "ytm", "track": t, "source": "Similar"},
              "status": "pending", "title": t.get("title"),
              "artist": ", ".join(a["name"] for a in t.get("artists") or []), "by": "autoplay",
              "added": time.time()} for t in picks]
    with lock:
        state["autoplay"]["count"] += len(items)
    place(items, "end")
    log("autoplay: added", [i["title"] for i in items])


# ------------------------------------------------------------------ Aux IQ
# Keeps up to IQ_BUFFER picks waiting at the end of the queue (your own songs
# go ahead of them) and tops up one at a time as they play: a song already in
# the library is used as it is, otherwise it's downloaded (IQ_DAILY a day).
#  1. candidates: YouTube Music's radio for the last few songs (a song in
#     several radios ranks higher), library songs by artists you like, and
#     with a mood, YouTube Music searches for it;
#  2. the LLM (Settings -> Configuration -> Aux IQ, song titles only) orders them and says why;
#     without it the scores below decide;
#  3. taste: skipped in the first 30 s counts against a song, played to the
#     end for it, thumbs up / down count most.
IQ_VARIANT_RE = re.compile(r"\b(remix|lo-?fi|slowed|reverb|sped up|karaoke|cover|instrumental|8d|mashup|"
                           r"reprise|unplugged|nightcore|jukebox|non ?stop|medley|version|extended)\b", re.I)
_iq = {"pool": [], "built": 0, "mood": None, "busy": False, "error": None}
_nojudge = [0.0]  # until then a song change is you steering (previous, play now), not a skip


def artist_keys(artist):
    return {norm(a) for a in re.split(r",|&|/|\||;|\bfeat\.?|\bft\.?|\bx\b|\band\b", artist or "", flags=re.I) if norm(a)}


def song_key(title, artist):
    first = sorted(artist_keys(artist))[:1]
    return songs.base_title(title) + "|" + (first[0] if first else "")


def no_judge(secs=12):
    _nojudge[0] = time.time() + secs


def taste_add(title, artist, song_d, artist_d, event=None):
    if not title:
        return
    with lock:
        t = state["taste"]
        k = song_key(title, artist)
        t["songs"][k] = round(max(-10, min(10, t["songs"].get(k, 0) + song_d)), 2)
        for a in artist_keys(artist):
            t["artists"][a] = round(max(-10, min(10, t["artists"].get(a, 0) + artist_d)), 2)
        for d in (t["songs"], t["artists"]):
            while len(d) > 3000:
                d.pop(next(iter(d)))
        if event:
            t["log"].insert(0, {"t": time.time(), "title": title, "artist": artist, "ev": event})
            del t["log"][40:]
        save_state()


def taste_judge(old):
    """A song just ended or was skipped: a quick skip counts against it, playing
    to the end for it."""
    if not old or not old.get("title") or old.get("paused") or time.time() < _nojudge[0]:
        return
    played = time.time() - (old.get("started") or time.time())
    dur = old.get("duration") or 0
    if dur > 60 and played < 30:
        taste_add(old["title"], old.get("artist"), -1, -0.3, "skipped")
    elif dur and played >= dur * 0.85:
        taste_add(old["title"], old.get("artist"), 0.5, 0.1)


def iq_active():
    """lock held. On by the button, or "Aux IQ" for an empty queue and nothing of yours waiting."""
    if state["iq"].get("on"):
        return True
    if state["settings"]["empty"]["mode"] == "iq":
        return not any(i["status"] != "failed" and i.get("by") != "iq" for i in state["queue"])
    return False


def iq_waiting():
    return [i for i in state["queue"] if i.get("by") == "iq" and i["status"] != "failed"]


def llm(prompt, timeout=30):
    """The fast model, then the backup; None if neither answers."""
    if not (config.get("llm_key") and config.get("llm_url")):
        return None
    err = None
    for model in dict.fromkeys(m for m in (config.get("llm_model"), config.get("llm_backup")) if m):
        req = urllib.request.Request(config.get("llm_url"), data=json.dumps({
            "model": model, "temperature": 0.5, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.get("llm_key")})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"] or ""
        except Exception as e:
            err = e
    raise err


def llm_json(prompt, kind="["):
    try:
        text = llm(prompt)
    except Exception as e:
        log("aux iq: llm failed:", e)
        _iq["error"] = "AI busy right now · using Auxx In’s own ranking"
        return None
    if not text:
        return None
    m = re.search(r"\[.*\]" if kind == "[" else r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def ytm_cand(t):
    return {"title": t.get("title"), "artist": ", ".join(a["name"] for a in t.get("artists") or [] if a.get("name")),
            "track": t}


def lib_index():
    idx = {}
    for e in songs.library:
        idx.setdefault(songs.base_title(e.get("title")), []).append(e)
    return idx


def lib_match(idx, title, artist):
    want = artist_keys(artist) | words(artist)
    for e in idx.get(songs.base_title(title)) or []:
        if (artist_keys(e.get("artist")) | words(e.get("artist"))) & want and (MUSIC / e["path"]).is_file():
            return e
    return None


def mood_queries(mood):
    qs = llm_json(f'Someone wants music for: "{mood}". Write 4 short YouTube Music song searches that find '
                  'fitting, well-known songs (song or artist names plus a genre or era word, no explanations). '
                  'Reply with a JSON list of strings only.')
    qs = [str(q)[:80] for q in qs or [] if q]
    return qs[:4] or [mood + " songs"]


def iq_build(mood):
    """Candidates, ranked: a list of dicts with title, artist, why, and
    track (YouTube Music) or path (already in the library)."""
    with lock:
        hist = [h for h in state["history"] if h.get("title")]
        taste = state["taste"]
        tsongs, tarts = dict(taste["songs"]), dict(taste["artists"])
        cut = time.time() - 86400
        played = {song_key(h["title"], h.get("artist")) for h in hist if (h.get("started") or 0) > cut}
        played |= {p[0] for p in state["iq"]["picked"] if p[1] > cut}
        played |= {song_key(i.get("title"), i.get("artist")) for i in state["queue"] if i.get("title")}
    seeds, seen = [], set()
    for h in hist:
        k = song_key(h["title"], h.get("artist"))
        if k not in seen and tsongs.get(k, 0) > -1:
            seen.add(k)
            seeds.append(h)
        if len(seeds) >= (1 if mood else 3):
            break
    cands = {}

    def addc(c, pts, via):
        # "|" / emoji / very long: a YouTube video title, not a song
        if (not c.get("title") or IQ_VARIANT_RE.search(c["title"]) or "|" in c["title"] or len(c["title"]) > 70
                or re.search(r"[\U0001F300-\U0001FAFF]", c["title"])):
            return
        k = song_key(c["title"], c["artist"])
        if k in played or tsongs.get(k, 0) <= -1 or any(tarts.get(a, 0) <= -3 for a in artist_keys(c["artist"])):
            return
        e = cands.setdefault(k, {**c, "key": k, "pts": 0, "via": []})
        e["pts"] += pts
        if via not in e["via"]:
            e["via"].append(via)

    radios = []
    for s in seeds:
        vid = seed_video(s)
        if vid:
            radios.append((s["title"], vid))
    if mood:
        for q in mood_queries(mood):
            try:
                res = [r for r in songs.ytm().search(q, filter="songs", limit=6) if r.get("videoId")]
            except Exception as e:
                log("aux iq: search failed:", e)
                continue
            for r in res:
                addc(ytm_cand(r), 2.5, "mood")
            if res and len(radios) < 3:
                radios.append((res[0].get("title"), res[0]["videoId"]))
    for label, vid in radios:
        try:
            tracks = songs.ytm().get_watch_playlist(videoId=vid, limit=25).get("tracks") or []
        except Exception as e:
            log("aux iq: radio failed:", e)
            continue
        w = 1.5 if radios and label == radios[0][0] and not mood else 1  # the song playing now leads
        for pos, t in enumerate(tracks[1:]):
            if t.get("videoId"):
                addc(ytm_cand(t), w * (1 + max(0, 20 - pos) / 40), label)
    # Rediscover: library songs by the artists you're on or like.
    liked = {a for a, v in tarts.items() if v >= 1}
    for s in seeds:
        liked |= artist_keys(s.get("artist"))
    lib = [e for e in songs.library if artist_keys(e.get("artist")) & liked]
    random.shuffle(lib)
    for e in lib[:12]:
        addc({"title": e.get("title"), "artist": e.get("artist"), "album": e.get("album"), "path": e["path"]}, 0.9, "library")
    if not cands:
        return []
    idx = lib_index()
    for c in cands.values():
        if not c.get("path"):
            e = lib_match(idx, c["title"], c["artist"])
            if e:
                c.update(path=e["path"], album=e.get("album"), title=e.get("title") or c["title"])
        c["score"] = (c["pts"] + 0.8 * (len([v for v in c["via"] if v not in ("library", "mood")]) - 1)
                      + tsongs.get(c["key"], 0) + 0.4 * max([tarts.get(a, 0) for a in artist_keys(c["artist"])] or [0])
                      + (0.4 if c.get("path") else 0) + random.random() * 0.3)
    ranked = sorted(cands.values(), key=lambda c: -c["score"])[:30]

    def why(c):
        seeds_in = [songs.FROM_RE.sub("", v).strip() for v in c["via"] if v not in ("library", "mood")]
        if "mood" in c["via"]:
            return f'For "{mood}"'
        if len(seeds_in) >= 2:
            return f"Like {seeds_in[0]} and {seeds_in[1]}"
        if seeds_in:
            return f"Like {seeds_in[0]}"
        return "From your library, not played lately"

    for c in ranked:
        c["why"] = why(c)
    return ranked


def iq_ai_rank(ranked, mood):
    """The LLM orders the candidates (and says why in a few words); None if it
    isn't answering."""
    if not ranked or not (config.get("llm_key") and config.get("llm_url")):
        return None
    with lock:
        hist = [h for h in state["history"] if h.get("title")]
        recent_log = list(state["taste"]["log"][:12])
    lines = "\n".join(f"{n}. {c['title']} — {c['artist']}{' (in library)' if c.get('path') else ''}"
                      for n, c in enumerate(ranked))
    just = "\n".join(f"- {h['title']} — {h.get('artist') or ''}" for h in hist[:10])
    feel = "\n".join(f"- {e['ev']}: {e['title']} — {e.get('artist') or ''}" for e in recent_log) or "- (none yet)"
    picks = llm_json(
        "You are Aux IQ, picking what plays next on a personal music radio. Keep the flow: similar energy, "
        "language and era to what was just played (or to the mood), no artist twice in a row, avoid anything "
        "like the skipped or disliked songs, prefer the liked ones. "
        f"It is {time.strftime('%A %H:%M')}.{' Mood asked for: ' + mood + '.' if mood else ''}\n\n"
        f"Just played (newest first):\n{just or '- (nothing yet)'}\n\nRecent reactions:\n{feel}\n\n"
        f"Candidates:\n{lines}\n\n"
        'Pick up to 12, in play order. For each, a reason of at most 8 words, specific (mention a song, '
        'artist, mood or vibe), no hype. Reply with JSON only: [{"i": <number>, "why": "<reason>"}]')
    if picks:
        out, used = [], set()
        for p in picks:
            i = p.get("i") if isinstance(p, dict) else None
            if isinstance(i, int) and 0 <= i < len(ranked) and i not in used:
                used.add(i)
                if p.get("why"):
                    ranked[i]["why"] = str(p["why"]).strip()[:80]
                out.append(ranked[i])
        if out:
            _iq["error"] = None
            return out + [c for n, c in enumerate(ranked) if n not in used]
    return None


def iq_rerank(built_at, mood):
    try:
        out = iq_ai_rank(list(_iq["pool"]), mood)
    except Exception as e:
        log("aux iq: ranking:", e)
        return
    if not out or _iq["built"] != built_at:
        return  # a newer list replaced it meanwhile
    left = {id(c) for c in _iq["pool"]}
    _iq["pool"] = [c for c in out if id(c) in left]
    log("aux iq: AI ordered", len(_iq["pool"]), "picks")


def iq_take(pool, last_artist, can_download):
    """The next pick: not the same artist as the song before, and only a
    library song once today's downloads are used up."""
    ok = [c for c in pool if c.get("path") or can_download]
    if not ok:
        return None
    for c in ok:
        if not (artist_keys(c["artist"]) & last_artist):
            return c
    return ok[0]


def iq_fill():
    try:
        while True:
            with lock:
                if not iq_active():
                    return
                waiting = iq_waiting()
                if len(waiting) >= max(1, min(5, config.get("iq_buffer"))) or any(i["status"] in ("pending", "resolving", "downloading") for i in waiting):
                    return
                iq = state["iq"]
                today = date.today().isoformat()
                if iq.get("date") != today:
                    iq.update(date=today, downloads=0)
                can_dl = iq["downloads"] < config.get("iq_daily")
                mood = iq.get("mood")
                last = state["queue"][-1] if state["queue"] else state["now"]
                last_artist = artist_keys((last or {}).get("artist"))
                taken = {song_key(i.get("title"), i.get("artist")) for i in state["queue"]}
            pool = [c for c in _iq["pool"] if c["key"] not in taken]
            if len(pool) < 2 or time.time() - _iq["built"] > 1200 or _iq["mood"] != mood:
                pool = iq_build(mood)
                built = time.time()
                _iq.update(pool=pool, built=built, mood=mood)
                # Picks start from the scores at once; the AI's order takes over when it answers.
                threading.Thread(target=iq_rerank, args=(built, mood), daemon=True).start()
            c = iq_take(pool, last_artist, can_dl)
            if not c:
                _iq["error"] = "Nothing new to pick" if can_dl else "Today's downloads are used up; no library match"
                return
            _iq["pool"] = [x for x in pool if x is not c]
            it = {"id": new_id(), "by": "iq", "why": c["why"], "title": c["title"], "artist": c["artist"],
                  "album": c.get("album"), "from": "Aux IQ", "added": time.time()}
            if c.get("path"):
                it.update(path=c["path"], status="ready")
            else:
                it.update(input="Aux IQ", status="pending", work={"kind": "ytm", "track": c["track"], "source": "Aux IQ"})
            with lock:
                if not iq_active():
                    return
                state["iq"]["picked"] = [p for p in state["iq"]["picked"] if p[1] > time.time() - 86400][-200:]
                state["iq"]["picked"].append([c["key"], time.time()])
                if not c.get("path"):
                    state["iq"]["downloads"] += 1
            place([it], "end")
            log("aux iq: picked", it["title"], "-", it["why"], "(library)" if c.get("path") else "(download)")
    except Exception as e:
        log("aux iq:", e)
        _iq["error"] = str(e)[:120]
    finally:
        _iq["busy"] = False


def iq_on_song():
    """A new song started: Aux IQ's waiting picks were chosen around earlier
    songs. Drop the ones that cost nothing to replace (library songs, and
    downloads not started yet: their download is given back) and pick again
    around this one. Picks already downloading or downloaded stay."""
    with lock:
        if not iq_active():
            return
        drop = [i for i in state["queue"] if i.get("by") == "iq" and
                (i["status"] == "pending" or (i["status"] == "ready" and not i.get("work")))]
        for i in drop:
            state["queue"].remove(i)
            if i.get("work") and state["iq"]["downloads"] > 0:
                state["iq"]["downloads"] -= 1
        if drop:
            save_state()
        _iq["built"] = 0
    threading.Thread(target=iq_tick, daemon=True).start()


def iq_tick(force=False):
    with lock:
        if _iq["busy"] or not iq_active():
            return
        waiting = iq_waiting()
        if len(waiting) >= max(1, min(5, config.get("iq_buffer"))) or any(i["status"] in ("pending", "resolving", "downloading") for i in waiting):
            return
        _iq["busy"] = True
    if not force:
        playing = (device_mode() and time.time() - state.get("device_seen", 0) < 90) or (not device_mode() and radio_on())
        if not playing:
            _iq["busy"] = False
            return
    threading.Thread(target=iq_fill, daemon=True).start()


# ------------------------------------------------------------------ daily playlist
# Every morning (after DAILY_HOUR) one Navidrome playlist, "Made by Auxx In" in its
# comment, is rebuilt from your listening and renamed for the day:
#  - songs you play to the end and your favourite artists,
#  - a few by artists you like that you haven't played lately,
#  - a few new songs like your favourites (downloaded; they share Aux IQ's
#    daily download allowance and join the playlist once they're saved).
# The LLM (if one is set up) picks the order and names it; without it the
# scores decide and it gets a plain name.
DAILY_HOUR = 5
DAILY_SIZE = 25
DAILY_NEW = 5
_daily = {"busy": False, "tried": 0, "error": None}


def daily_tick(force=False):
    with lock:
        if _daily["busy"] or not config.navidrome_on():  # the daily playlist lives in Navidrome
            return False
        if not force and (state["daily"].get("date") == date.today().isoformat() or time.localtime().tm_hour < DAILY_HOUR
                          or time.time() - _daily["tried"] < 1800 or not songs.library):
            return False
        _daily.update(busy=True, tried=time.time())
    threading.Thread(target=daily_build, daemon=True).start()
    return True


def daily_candidates():
    """(library songs scored, new songs from YouTube Music) for today's list."""
    with lock:
        tsongs, tarts = dict(state["taste"]["songs"]), dict(state["taste"]["artists"])
        hist = [h for h in state["history"] if h.get("title")]
    recent = {song_key(h["title"], h.get("artist")) for h in hist[:40]}
    scored, seen = [], set()
    for e in songs.library:
        if (not e.get("title") or not e.get("path") or "|" in e["title"] or len(e["title"]) > 70
                or re.search(r"official|video|lyric|mashup|nonstop|jukebox", e["title"], re.I)):
            continue  # video files, not songs (and Navidrome often can't find them)
        k = song_key(e["title"], e.get("artist"))
        a = max([tarts.get(x, 0) for x in artist_keys(e.get("artist"))] or [0])
        if k in seen or tsongs.get(k, 0) < 0 or a <= -3:
            continue
        seen.add(k)
        scored.append({"key": k, "song": tsongs.get(k, 0), "artist_s": a, "e": e, "recent": k in recent})
    loved = sorted([c for c in scored if c["song"] > 0 or c["artist_s"] >= 1],
                   key=lambda c: -(c["song"] + 0.5 * c["artist_s"] + random.random() * 0.5))[:30]
    lk = {c["key"] for c in loved}
    liked = {a for a, v in tarts.items() if v >= 0.5}
    lately = [c for c in scored if c["key"] not in lk and not c["recent"] and artist_keys(c["e"].get("artist")) & liked]
    random.shuffle(lately)
    lately = lately[:12]
    taken = lk | {c["key"] for c in lately}
    rest = [c for c in scored if c["key"] not in taken]
    random.shuffle(rest)
    lib = loved + lately + rest[:max(0, 20 - len(loved) - len(lately))]  # little history yet: some shuffle
    for c in loved:
        c["kind"] = "loved"
    for c in lib:
        c.setdefault("kind", "lately" if c in lately else "shuffle")
    # New: YouTube Music's radio for a few favourites, songs not in the library.
    new, idx = {}, lib_index()
    for c in (loved[:3] or [{"e": h} for h in hist[:3]]):
        vid = seed_video(c["e"])
        if not vid:
            continue
        try:
            tracks = songs.ytm().get_watch_playlist(videoId=vid, limit=20).get("tracks") or []
        except Exception as e:
            log("daily: radio failed:", e)
            continue
        for t in tracks[1:]:
            n = ytm_cand(t)
            if (not t.get("videoId") or not n["title"] or IQ_VARIANT_RE.search(n["title"]) or "|" in n["title"]
                    or len(n["title"]) > 70 or lib_match(idx, n["title"], n["artist"])):
                continue
            k = song_key(n["title"], n["artist"])
            if tsongs.get(k, 0) >= 0 and k not in seen:
                new.setdefault(k, {**n, "key": k, "via": c["e"].get("title")})
    return lib, list(new.values())[:15]


def daily_build():
    try:
        with lock:
            iq = state["iq"]
            today = date.today().isoformat()
            if iq.get("date") != today:
                iq.update(date=today, downloads=0)
            can_dl = max(0, min(DAILY_NEW, config.get("iq_daily") - iq["downloads"]))
            hist = [h for h in state["history"] if h.get("title")][:10]
        lib, new = daily_candidates()
        if not lib:
            _daily["error"] = "The library is empty"
            return
        new = new if can_dl else []
        lines = "\n".join(f"L{n}. {c['e']['title']} — {c['e'].get('artist') or ''} ({c['kind']})" for n, c in enumerate(lib))
        lines += "\n" + "\n".join(f"N{n}. {c['title']} — {c['artist']} (new, like {c['via']})" for n, c in enumerate(new))
        just = "\n".join(f"- {h['title']} — {h.get('artist') or ''}" for h in hist) or "- (nothing yet)"
        when = time.strftime("%A %d %B")
        out = llm_json(
            f"You make one daily playlist for a personal music library. Today is {when}. "
            f"Pick {DAILY_SIZE} songs from the candidates below and put them in a good listening order (a flow of "
            f"energy, not all one artist in a row). Mostly 'loved' songs, a few 'lately' ones (not heard in a "
            f"while), and at most {can_dl} 'new' ones (N numbers). Then name it: a creative, situational name of "
            "2-5 words that fits this day and the music (the weekday, the season, the time of year, the mood of "
            "the songs), not generic like 'Daily Mix'. And one line about it, at most 12 words.\n\n"
            f"Recently played:\n{just}\n\nCandidates:\n{lines}\n\n"
            'Reply with JSON only: {"name": "...", "about": "...", "picks": ["L3", "N0", ...]}', kind="{")
        picks_l, picks_n = [], []
        name, about = None, None
        if isinstance(out, dict):
            name = str(out.get("name") or "").strip()[:60] or None
            about = str(out.get("about") or "").strip()[:100] or None
            for p in out.get("picks") or []:
                m = re.match(r"^([LN])(\d+)$", str(p).strip())
                if not m:
                    continue
                i = int(m.group(2))
                if m.group(1) == "L" and i < len(lib) and lib[i] not in picks_l:
                    picks_l.append(lib[i])
                elif m.group(1) == "N" and i < len(new) and new[i] not in picks_n and len(picks_n) < can_dl:
                    picks_n.append(new[i])
        if len(picks_l) + len(picks_n) < 10:  # no AI answer: the scores decide
            picks_l = lib[:DAILY_SIZE - min(can_dl, len(new))]
            picks_n = new[:can_dl]
            random.shuffle(picks_l)
        name = name or time.strftime("%A Mix · %b %d")
        about = about or "Your favourites, a few you haven't played lately, and new finds"
        ids, want = [], DAILY_SIZE - len(picks_n)
        spare = [c for c in lib if c not in picks_l]  # stand-ins for songs Navidrome can't find
        for c in picks_l + spare:
            if len(ids) >= want:
                break
            try:
                sid = nd_find(c["e"]["path"])
            except Exception:
                sid = None
            if sid and sid not in ids:
                ids.append(sid)
        if not ids:
            _daily["error"] = "Navidrome didn't find the songs (asleep?)"
            return
        pid = daily_playlist(name, about, ids)
        items = [{"id": new_id(), "input": "daily playlist", "work": {"kind": "ytm", "track": c["track"], "source": "Daily"},
                  "status": "pending", "title": c["title"], "artist": c["artist"], "by": "daily", "daily": pid,
                  "save_only": True, "added": time.time()} for c in picks_n]
        with lock:
            state["daily"] = {"id": pid, "date": date.today().isoformat(), "name": name, "about": about,
                              "songs": len(ids), "new": len(items)}
            state["iq"]["downloads"] += len(items)
            save_state()
        if items:
            place(items, "save")
        _daily["error"] = None
        log("daily:", name, "-", len(ids), "songs,", len(items), "new on the way")
    except Exception as e:
        log("daily:", e)
        _daily["error"] = str(e)[:120]
    finally:
        _daily["busy"] = False


def daily_playlist(name, about, ids):
    """Today's songs into the daily playlist (made once, then reused)."""
    with lock:
        pid = state["daily"].get("id")
    pls = {p["id"]: p for p in nav.playlists()}
    if pid not in pls:
        r = nav.call("createPlaylist", name=name, songId=ids)
        pid = (r.get("playlist") or {}).get("id") or next(p["id"] for p in nav.playlists() if p["name"] == name)
    else:
        n = pls[pid].get("songs") or 0
        if n:
            nav.call("updatePlaylist", playlistId=pid, songIndexToRemove=list(range(n)))
        nav.call("updatePlaylist", playlistId=pid, name=name, songIdToAdd=ids)
    nav.call("updatePlaylist", playlistId=pid, comment=f"{nav.MADE_MARK} · {about}")
    return pid


def daily_add(pid, sid):
    """A new song for the daily playlist finished downloading."""
    with lock:
        if state["daily"].get("id") != pid:
            return  # that was yesterday's list
    if sid not in [s["nd_id"] for s in nav.playlist(pid)["songs"]]:
        nav.call("updatePlaylist", playlistId=pid, songIdToAdd=sid)


@app.post("/api/daily")
def api_daily():
    """Make today's playlist again now."""
    started = daily_tick(force=True)
    return jsonify({"ok": started, "say": "Making today's playlist…" if started else "Already making it."})


# ------------------------------------------------------------------ navidrome sync
def nd_find(path):
    """Navidrome reports tidied paths, not the real ones: match on the tags."""
    t = songs.read_tags(MUSIC / path)
    r = nav.call("search3", query=t["title"], songCount=30, albumCount=0, artistCount=0)
    for s in (r.get("searchResult3") or {}).get("song") or []:
        if norm(s.get("title")) == norm(t["title"]) and norm(s.get("album")) == norm(t["album"]):
            return s["id"]
    return None


def nd_playlist_add(name, song_id, newest_first=False):
    pl = next((p for p in nav.playlists() if p["name"] == name), None)
    if not pl:
        nav.call("createPlaylist", name=name, songId=song_id)
        return
    ids = [s["nd_id"] for s in nav.playlist(pl["id"])["songs"]]
    if song_id in ids:
        return
    if newest_first and ids:
        nav.call("updatePlaylist", playlistId=pl["id"], songIndexToRemove=list(range(len(ids))))
        nav.call("updatePlaylist", playlistId=pl["id"], songIdToAdd=[song_id] + ids)
    else:
        nav.call("updatePlaylist", playlistId=pl["id"], songIdToAdd=song_id)


def nd_radio_station():
    try:
        st = (nav.call("getInternetRadioStations").get("internetRadioStations") or {}).get("internetRadioStation") or []
        if not any(s.get("streamUrl") == config.get("stream_url") for s in st):
            nav.call("createInternetRadioStation", streamUrl=config.get("stream_url"), name="Auxx In")
            log("navidrome: added the Aux radio station")
        return True
    except Exception as e:
        log("navidrome radio station:", e)
        return False


def navidrome_loop():
    """Ask Navidrome to scan new songs, then file them into playlists. Navidrome
    may be asleep (the memory brain); items just wait until it's back."""
    radio_done = False
    scanned_at = 0
    while True:
        time.sleep(15)
        if not config.navidrome_on() or not config.get("navidrome_user"):
            continue
        try:
            if not radio_done:
                radio_done = nd_radio_station()
            with lock:
                todo = [r for r in state["recent"] if not r.get("nd") and r.get("path")]
            if not todo:
                continue
            if time.time() - scanned_at > 20 and not any(r.get("scan_asked") for r in todo):
                nav.call("startScan")
                scanned_at = time.time()
                with lock:
                    for r in todo:
                        r["scan_asked"] = True
                continue
            for r in reversed(todo):  # oldest first, so the newest ends on top
                if not (MUSIC / r["path"]).is_file():
                    r["nd"] = True
                    continue
                sid = nd_find(r["path"])
                if not sid:
                    if time.time() - scanned_at > 300:
                        nav.call("startScan")
                        scanned_at = time.time()
                    continue
                if not r.get("daily"):
                    nd_playlist_add("Aux IQ" if r.get("by") == "iq" else config.get("requests_playlist"), sid, newest_first=True)
                else:
                    daily_add(r["daily"], sid)
                # A shared playlist gets its own Navidrome playlist; a shared
                # album is already an album there.
                if r.get("list_name") and norm(r["list_name"]) != norm(r.get("album")):
                    nd_playlist_add(r["list_name"], sid)
                with lock:
                    r["nd"] = True
                    r["nd_id"] = sid
                    r.pop("scan_asked", None)
                    save_state()
        except Exception as e:
            log("navidrome:", e)


def library_loop():
    first = True
    while True:
        try:
            songs.index_library()
            if first:
                write_fallback()
                first = False
        except Exception as e:
            log("library index failed:", e)
        time.sleep(600)


# ------------------------------------------------------------------ login
# Open without a login: the login page and its API, static files, and the
# radio's /internal/ calls (those carry INTERNAL_KEY instead).
OPEN_PATHS = {"/login", "/api/me", "/api/login", "/api/setup"}
ADMIN_PATHS = ("/api/config", "/api/users", "/api/delete-file/", "/api/storage")


@app.before_request
def require_login():
    p = request.path
    if p in OPEN_PATHS or p.startswith("/static/") or p.startswith("/internal/"):
        return None
    pu = auth.proxy_user()
    if pu and (not current_user.is_authenticated or current_user.get_id() != pu.get_id()):
        login_user(pu)
    if not current_user.is_authenticated:
        if p == "/":
            return redirect("login")
        return jsonify({"ok": False, "error": "Log in first."}), 401
    if p.startswith(ADMIN_PATHS) and not current_user.admin:
        return jsonify({"ok": False, "error": "Only admins can do that."}), 403
    return None


@app.errorhandler(CSRFError)
def csrf_failed(e):
    return jsonify({"ok": False, "error": "The page is out of date. Reload it and try again."}), 400


@app.errorhandler(429)
def too_many(e):
    return jsonify({"ok": False, "error": "Too many requests. Try again in a little while."}), 429


@app.get("/login")
def login_page():
    return send_from_directory(STATIC, "login.html")


@app.get("/api/me")
def api_me():
    return jsonify({"user": current_user.public() if current_user.is_authenticated else None,
                    "setup": auth.needs_setup(), "navidrome": config.navidrome_on(),
                    "proxy": bool(auth.PROXY_HEADER), "csrf": generate_csrf()})


@app.post("/api/login")
@limiter.limit(LIMIT_LOGIN, key_func=_login_key)
def api_login():
    d = request.get_json(silent=True) or {}
    u = auth.check_login((d.get("username") or "").strip(), d.get("password") or "")
    if not u:
        return jsonify({"ok": False, "error": "Wrong username or password."}), 401
    login_user(u, remember=True, duration=timedelta(days=30))
    return jsonify({"ok": True, "user": u.public()})


@app.post("/api/setup")
@limiter.limit(LIMIT_LOGIN, key_func=lambda: "setup:" + (request.remote_addr or ""))
def api_setup():
    """First run only: create the admin."""
    if not auth.needs_setup():
        return jsonify({"ok": False, "error": "Already set up."}), 409
    d = request.get_json(silent=True) or {}
    if not secrets.compare_digest((d.get("code") or "").strip().upper(), auth.setup_code()):
        return jsonify({"ok": False, "error": "Wrong setup code. It's in the server log: docker logs auxx-in-web"}), 403
    try:
        auth.create_user((d.get("username") or "").strip(), d.get("password") or "", admin=True)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    auth.setup_done()
    u = auth.check_local(d["username"].strip(), d["password"])
    login_user(u, remember=True, duration=timedelta(days=30))
    return jsonify({"ok": True, "user": u.public()})


@app.post("/api/logout")
def api_logout():
    logout_user()
    return jsonify({"ok": True})


@app.post("/api/password")
def api_password():
    """Change your own password (built-in users)."""
    d = request.get_json(silent=True) or {}
    if current_user.source != "local":
        return jsonify({"ok": False, "error": "Change it where you log in (Navidrome or your login service)."}), 400
    if not auth.check_local(current_user.name, d.get("old") or ""):
        return jsonify({"ok": False, "error": "The current password is wrong."}), 400
    try:
        auth.update_user(current_user.name, password=d.get("new") or "")
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "say": "Password changed."})


def navidrome_has_user(name):
    """True when Navidrome says this user exists (needs the service account to
    be a Navidrome admin; otherwise we can't tell, and say no)."""
    try:
        return bool(name) and nav.call("getUser", username=name).get("user", {}).get("username") == name
    except Exception:
        return False


_storage = {"at": 0, "data": None}


def folder_bytes(root):
    total = 0
    for dirpath, _, files in os.walk(root):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


@app.get("/api/storage")
def api_storage():
    """Music and data folder sizes and free disk space (counted at most once a minute)."""
    if time.time() - _storage["at"] > 60:
        disk = shutil.disk_usage(MUSIC)
        _storage["data"] = {"songs": len(songs.library), "music": folder_bytes(MUSIC), "data": folder_bytes(DATA),
                            "disk_free": disk.free, "disk_total": disk.total}
        _storage["at"] = time.time()
    return jsonify(_storage["data"])


@app.get("/api/users")
def api_users():
    return jsonify({"users": auth.list_users(), "navidrome": config.navidrome_on()})


@app.post("/api/users")
def api_user_create():
    d = request.get_json(silent=True) or {}
    if config.navidrome_on() and navidrome_has_user((d.get("username") or "").strip()):
        return jsonify({"ok": False, "error": "That's a Navidrome user: they can log in with their Navidrome account."}), 400
    try:
        auth.create_user((d.get("username") or "").strip(), d.get("password") or "", bool(d.get("admin")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "users": auth.list_users()})


@app.post("/api/users/<name>")
def api_user_update(name):
    d = request.get_json(silent=True) or {}
    if "admin" in d and name == current_user.name:
        return jsonify({"ok": False, "error": "You can't change your own role."}), 400
    try:
        auth.update_user(name, password=d.get("password") or None,
                         admin=bool(d["admin"]) if "admin" in d else None)
    except KeyError:
        return jsonify({"ok": False, "error": "No such user."}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "users": auth.list_users()})


@app.delete("/api/users/<name>")
def api_user_delete(name):
    if name == current_user.name and current_user.source == "local":
        return jsonify({"ok": False, "error": "You can't delete yourself."}), 400
    try:
        auth.delete_user(name)
    except KeyError:
        return jsonify({"ok": False, "error": "No such user."}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "users": auth.list_users()})


# ------------------------------------------------------------------ routes: page
@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_file(name):
    return send_from_directory(STATIC, name)


def body():
    d = request.get_json(silent=True) or {}
    if not d:  # iOS Shortcuts / curl may send a form or plain text
        d = request.form.to_dict() or {"q": request.get_data(as_text=True)}
    return d


@app.get("/api/state")
def api_state():
    on = radio_on()
    with lock:
        now = dict(state["now"]) if state["now"] else None
        if now:
            now["cover"] = cover_of(now)
        return jsonify({
            "now": now,
            "queue": [public(i) for i in state["queue"]],
            "jobs": [public(i) for i in state["jobs"]],
            "history": [{**{k: h.get(k) for k in ("id", "title", "artist", "album", "path", "nd_id", "from", "started", "duration")},
                         "cover": cover_of(h)} for h in state["history"][:20]],
            "stream": config.get("stream_url"),
            "navidrome": config.navidrome_on(),
            "radio": on is not None,
            "radio_on": bool(on),
            "settings": state["settings"],
            "sleep_at": state.get("sleep_at"),
            "server_time": time.time(),
            "autoplay": state["autoplay"], "autoplay_daily": config.get("autoplay_daily"),
            "play_on": state["settings"].get("play_on"),
            "device_cmd": state.get("device_cmd"),
            "iq": iq_public(),
            "rating": state["taste"]["rated"].get(song_key(now["title"], now.get("artist"))) if now and now.get("title") else None,
        })


# ------------------------------------------------------------------ routes: queue
@app.post("/api/add")
@limiter.limit(LIMIT_ADD)
def api_add():
    d = body()
    q = (d.get("q") or d.get("url") or d.get("text") or "").strip()
    if not q:
        return jsonify({"ok": False, "say": "Nothing to add."}), 400
    where = d.get("where") or ("now" if str(d.get("now", "")).lower() in ("1", "true", "yes") else "end")
    if where not in ("next", "end", "now", "save"):
        where = "end"
    msg, items = add(q, where=where, by=d.get("by") or "you", exact=bool(d.get("exact")))
    ok = bool(items) and items[0]["status"] != "failed"
    return jsonify({"ok": ok, "say": msg, "items": [public(i) for i in items]}), 200 if ok else 404


@app.post("/api/skip")
def api_skip():
    with lock:
        nxt = next((i for i in state["queue"] if i["status"] in ("ready", "loaded")), None)
    if device_mode():
        device_command("next")
        return jsonify({"ok": True, "say": f"Skipping. Next: {nxt['title']}." if nxt else "Skipping."})
    ok = radio("skip") is not None
    say = f"Skipping. Next: {nxt['title']}." if nxt else "Skipping."
    return jsonify({"ok": ok, "say": say if ok else "The radio isn't running."})


@app.get("/api/now")
def api_now():
    with lock:
        n = state["now"]
        nxt = next((i for i in state["queue"] if i["status"] in ("ready", "loaded", "downloading")), None)
    say = f"Playing {n['title']} by {n['artist']}." if n else "Nothing is playing."
    if nxt:
        say += f" Next: {nxt['title']}."
    return jsonify({"now": n, "say": say})


@app.post("/api/play/<item_id>")
def api_play(item_id):
    d = request.get_json(silent=True) or {}
    return jsonify({"ok": jump(item_id, bool(d.get("drop_before")))})


@app.post("/api/move/<item_id>")
def api_move(item_id):
    d = request.get_json(silent=True) or {}
    with lock:
        q = state["queue"]
        i = next((n for n, it in enumerate(q) if it["id"] == item_id), None)
        if i is None:
            abort(404)
        it = q.pop(i)
        if d.get("to") == "top":
            j = 0
        elif d.get("to") == "bottom":
            j = len(q)
        elif d.get("index") is not None:
            j = max(0, min(len(q), int(d["index"])))
        else:
            j = max(0, min(len(q), i + int(d.get("by", -1))))
        q.insert(j, it)
        save_state()
    reset_loaded()
    return jsonify({"ok": True})


@app.delete("/api/item/<item_id>")
def api_remove(item_id):
    with lock:
        state["queue"] = [i for i in state["queue"] if i["id"] != item_id]
        state["jobs"] = [i for i in state["jobs"] if i["id"] != item_id]
        save_state()
    reset_loaded()
    return jsonify({"ok": True})


@app.post("/api/queue/clear")
def api_queue_clear():
    with lock:
        state["queue"] = []  # downloads already running still finish into the library
        save_state()
    reset_loaded()
    return jsonify({"ok": True})


@app.post("/api/queue/shuffle")
def api_queue_shuffle():
    with lock:
        q = state["queue"]
        random.shuffle(q)
        save_state()
    reset_loaded()
    return jsonify({"ok": True})


@app.post("/api/queue-path")
def api_queue_path():
    """A song file already on disk (e.g. from Played) -> the queue."""
    d = request.get_json(silent=True) or {}
    rel = d.get("path") or ""
    p = (MUSIC / rel).resolve()
    if not str(p).startswith(str(MUSIC.resolve()) + "/") or not p.is_file():
        abort(404)
    t = songs.read_tags(p)
    it = {"id": new_id(), "input": rel, "work": None, "status": "ready", "path": rel, "title": t["title"],
          "artist": t["artist"], "album": t["album"], "from": "Library", "by": "you", "added": time.time()}
    where = d.get("where") if d.get("where") in ("next", "end", "now") else "end"
    place([it], where)
    if where == "now":
        jump(it["id"])
    return jsonify({"ok": True, "say": f"Added to the queue: {it['title']}."})


# ------------------------------------------------------------------ routes: this device (the page as the player)
_fallback_recent = []


def fallback_pick():
    """A song for the page when the queue is empty, from the same list the
    radio would shuffle (library / Requests / a playlist / an artist)."""
    try:
        lines = [l for l in FALLBACK_M3U.read_text().splitlines() if l and not l.startswith("#")]
    except OSError:
        lines = []
    if not lines:
        return None
    fresh = [l for l in lines if l not in _fallback_recent] or lines
    pick = random.choice(fresh)
    _fallback_recent.append(pick)
    del _fallback_recent[:-50]
    if pick.startswith("http"):
        m = ND_ID_RE.search(pick)
        if not m:
            return None
        s = nav.song(urllib.parse.unquote(m.group(1)))
        return {"id": "nd-" + s["nd_id"], "nd_id": s["nd_id"], "title": s["title"], "artist": s["artist"],
                "album": s["album"], "cover_id": s["cover_id"], "duration": s["duration"], "from": "library"}
    try:
        rel = str(Path(pick).relative_to(MUSIC))
    except ValueError:
        return None
    t = songs.read_tags(MUSIC / rel)
    return {"id": "lib-" + uuid.uuid5(uuid.NAMESPACE_URL, pick).hex[:8], "path": rel, "from": "library", **t}


def playable(it):
    d = public(it)
    d["url"] = "api/stream/" + urllib.parse.quote(it["id"])
    d["duration"] = it.get("duration") or duration_of(it)
    return d


def set_now(now):
    now["started"] = time.time()
    if not now.get("duration"):
        now["duration"] = duration_of(now)
    taste_judge(state["now"])
    with lock:
        state["now"] = now
        state["history"].insert(0, now)
        state["history"] = state["history"][:60]
        save_state()
    iq_on_song()
    prefetch_lyrics()


_device_peek = {}  # the empty-queue pick handed out early (peek), kept for the real call


@app.post("/api/device/next")
def api_device_next():
    """The page finished a song (or wants the next): hand it the next one.
    peek: just say which song is next (nothing changes), so a locked phone can
    start it the moment this one ends. id: the song the page already started."""
    d = request.get_json(silent=True) or {}
    peek, want = bool(d.get("peek")), d.get("id")
    with lock:
        state["device_seen"] = time.time()
        it = next((i for i in state["queue"] if want and i["id"] == want and i["status"] in ("ready", "loaded")), None)
        # The first ready song; songs still downloading keep their place at
        # the top and play once they're ready, so the music never stops.
        it = it or next((i for i in state["queue"] if i["status"] in ("ready", "loaded")), None)
        if it and peek:
            p = playable(it)
            p["position"] = it.get("resume") or 0
            return jsonify({"item": p})
        if it:
            state["queue"].remove(it)
            now = {k: it.get(k) for k in NOW_KEYS}
            now["from"] = it.get("keep_from") or "queue"
            now["resume"] = it.get("resume") or 0
    if not it:
        if state["settings"]["empty"]["mode"] == "silence":
            busy = next((i for i in state["queue"] if i["status"] in ("pending", "resolving", "downloading")), None)
            if busy:
                return jsonify({"wait": True, "say": f"Getting {busy.get('title') or 'the next song'}…"})
            return jsonify({"empty": True})
        pk = _device_peek.pop("now", None)
        now = pk if pk and (peek or pk["id"] == want) else fallback_pick()
        if not now:
            return jsonify({"empty": True})
        if peek:
            _device_peek["now"] = now
            p = playable(now)
            p["position"] = 0
            return jsonify({"item": p})
    resume = now.pop("resume", 0)
    set_now(now)
    if resume:
        now["started"] -= resume
    d = playable(now)
    d["position"] = resume
    return jsonify({"item": d})


@app.post("/api/device/now")
def api_device_now():
    """The page reports where it is in the song (lyrics, other screens)."""
    d = request.get_json(silent=True) or {}
    with lock:
        state["device_seen"] = time.time()
        n = state["now"]
        if n and d.get("id") == n.get("id"):
            pos = float(d.get("position") or 0)
            n["started"] = time.time() - pos
            n["position"] = pos  # where the page last was: a reopened page carries on from here
            n["paused"] = bool(d.get("paused"))
            if d.get("duration"):
                n["duration"] = d["duration"]
        elif d.get("item"):  # going back to a song played earlier
            it = find_item(d["item"]) or {}
            if it:
                now = {k: it.get(k) for k in ("id", "title", "artist", "album", "path", "nd_id", "cover_id",
                                              "cover_url", "by", "video_id", "duration", "from")}
                now["started"] = time.time() - float(d.get("position") or 0)
                state["now"] = now
        if d.get("ack"):
            state["device_cmd"] = None
    return jsonify({"ok": True})


@app.get("/api/stream/<item_id>")
def api_stream(item_id):
    """The song file for the page's player (files and Navidrome songs)."""
    it = find_item(item_id)
    if not it:
        abort(404)
    if it.get("path"):
        p = (MUSIC / it["path"]).resolve()
        if not str(p).startswith(str(MUSIC.resolve()) + "/") or not p.is_file():
            abort(404)
        return send_file(p, conditional=True, max_age=3600)
    if it.get("nd_id"):
        return api_lib_stream(it["nd_id"])
    abort(404)


@app.get("/api/lib/stream/<sid>")
def api_lib_stream(sid):
    """A Navidrome song through this server (the page never sees the login)."""
    headers = {}
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    try:
        r = urllib.request.urlopen(urllib.request.Request(nav.stream_url(sid), headers=headers), timeout=20)
    except urllib.error.HTTPError as e:
        return Response(status=e.code)
    except Exception:
        abort(502)
    out = {k: r.headers[k] for k in ("Content-Length", "Content-Range") if r.headers.get(k)}
    out["Accept-Ranges"] = "bytes"
    return Response(iter(lambda: r.read(65536), b""), status=r.status,
                    mimetype=r.headers.get("Content-Type", "audio/mpeg"), headers=out)


# ------------------------------------------------------------------ routes: radio
@app.post("/api/radio")
def api_radio():
    on = bool((request.get_json(silent=True) or {}).get("on"))
    radio("aux.start" if on else "aux.stop")
    for _ in range(12):  # the radio takes a moment to switch
        if radio_on() == on:
            break
        time.sleep(0.25)
    with lock:
        if on is False:
            state["sleep_at"] = None
        save_state()
    return jsonify({"ok": True, "radio_on": bool(radio_on()), "say": "Radio on." if on else "Radio off."})


@app.post("/api/sleep")
def api_sleep():
    minutes = float((request.get_json(silent=True) or {}).get("minutes") or 0)
    with lock:
        state["sleep_at"] = time.time() + minutes * 60 if minutes > 0 else None
        save_state()
    return jsonify({"ok": True, "sleep_at": state["sleep_at"]})


@app.post("/api/settings")
def api_settings():
    d = request.get_json(silent=True) or {}
    with lock:
        st = state["settings"]
        if "empty" in d and isinstance(d["empty"], dict):
            mode = d["empty"].get("mode")
            if mode in ("library", "requests", "playlist", "artist", "similar", "iq", "silence"):
                st["empty"] = {"mode": mode, "id": d["empty"].get("id"), "name": d["empty"].get("name")}
        for k in ("even_volume", "crossfade"):
            if k in d:
                st[k] = bool(d[k])
        if d.get("fade") in (0, 5, 10, 15):
            st["fade"] = d["fade"]
            st["crossfade"] = d["fade"] > 0
        switched = d.get("play_on") in ("device", "radio") and d["play_on"] != st.get("play_on")
        if d.get("play_on") in ("device", "radio"):
            st["play_on"] = d["play_on"]
        handoff = None   # radio -> device: the song the radio is on, and where
        head = None      # device -> radio: the song the page was on, back at the head
        if switched:
            no_judge(20)
            for it in state["queue"]:  # nothing stays "lined up" in the other player
                if it["status"] == "loaded":
                    it["status"] = "ready"
            n = state["now"]
            if n:
                pos = d.get("position")
                pos = float(pos) if pos is not None else time.time() - (n.get("started") or time.time())
                if st["play_on"] == "radio":
                    if d.get("playing", True):
                        head = as_queue_item(n, resume=pos)
                        if head:
                            drop_now_from_history()
                            state["queue"].insert(0, head)
                elif n.get("path") or n.get("nd_id"):
                    handoff = (n, max(0.0, pos))
        save_state()
    if switched and st["play_on"] == "radio":
        def start_radio():
            radio("aux.start")
            for _ in range(12):  # the output takes a moment to come up
                if radio_on():
                    break
                time.sleep(0.25)
            time.sleep(0.5)
            # Without a skip the radio carries on with the song it was paused on.
            radio_play_head(head)
        threading.Thread(target=start_radio, daemon=True).start()
    threading.Thread(target=apply_radio_settings, daemon=True).start()
    out = {"ok": True, "settings": state["settings"]}
    if handoff:
        dur = handoff[0].get("duration") or 0
        if not dur or handoff[1] < dur - 3:
            out["resume"] = {**playable(handoff[0]), "position": round(handoff[1], 1)}
    return jsonify(out)


@app.get("/api/config")
def api_config():
    return jsonify(config.public())


@app.post("/api/config")
def api_config_save():
    try:
        changed = config.save(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if config.navidrome_on() and {"use_navidrome", "navidrome_url", "navidrome_user", "navidrome_pass", "stream_url"} & set(changed):
        # Register the radio station at the (new) stream address in the (new) Navidrome.
        threading.Thread(target=nd_radio_station, daemon=True).start()
    return jsonify({"ok": True, "changed": changed, "config": config.public()})


@app.post("/api/previous")
def api_previous():
    """Previous, in either player: more than 5 s in, the song starts again;
    otherwise the song before it plays, and this one comes right after."""
    no_judge()
    d = request.get_json(silent=True) or {}
    with lock:
        n = state["now"]
        pos = d.get("position")
        pos = float(pos) if pos is not None else (time.time() - n["started"] if n and n.get("started") else 0)
        drop_now_from_history()
        h = state["history"]
        if pos > 5 or not h:
            items = [as_queue_item(n)]
        else:
            items = [as_queue_item(h.pop(0)), as_queue_item(n)]
        items = [i for i in items if i]
        if not items:
            return jsonify({"ok": False, "say": "Nothing to go back to."})
        state["queue"][0:0] = items
        save_state()
    if device_mode():
        return jsonify({"ok": True, "device": True})
    radio_play_head(items[0])
    return jsonify({"ok": True, "say": "Back to " + (items[0].get("title") or "the last song") + "."})


# ------------------------------------------------------------------ routes: Aux IQ
def iq_public():
    """lock held"""
    iq = state["iq"]
    return {"on": bool(iq.get("on")), "mood": iq.get("mood"), "auto": state["settings"]["empty"]["mode"] == "iq",
            "active": iq_active(), "waiting": len(iq_waiting()), "buffer": max(1, min(5, config.get("iq_buffer"))),
            "downloads": iq.get("downloads", 0) if iq.get("date") == date.today().isoformat() else 0,
            "daily": config.get("iq_daily"), "busy": _iq["busy"], "error": _iq["error"], "ai": bool(config.get("llm_key") and config.get("llm_url"))}


@app.post("/api/iq")
def api_iq():
    """{on: bool} starts / stops Aux IQ; {mood: "rainy drive"} sets a mood (and
    starts it), {mood: null} clears it. A new mood replaces the picks still
    waiting that the radio hasn't lined up."""
    d = request.get_json(silent=True) or {}
    with lock:
        iq = state["iq"]
        if "mood" in d:
            mood = (str(d["mood"] or "")).strip()[:80] or None
            if mood != iq.get("mood"):
                iq["mood"] = mood
                _iq["pool"] = []
                state["queue"] = [i for i in state["queue"]
                                  if not (i.get("by") == "iq" and i["status"] in ("pending", "ready", "failed"))]
            if mood:
                iq["on"] = True
        if "on" in d:
            iq["on"] = bool(d["on"])
        _iq["error"] = None
        save_state()
        on, mood = iq["on"], iq.get("mood")
    if on:
        iq_tick(force=True)
        say = f"Aux IQ on: {mood}." if mood else "Aux IQ on. Picking songs…"
    else:
        say = "Aux IQ off. Its picks stay in Up next."
    with lock:
        return jsonify({"ok": True, "say": say, "iq": iq_public()})


@app.post("/api/iq/rate")
def api_iq_rate():
    """{up: true|false} for the song playing now; thumbs down also skips it."""
    d = request.get_json(silent=True) or {}
    with lock:
        n = state["now"]
        if not n or not n.get("title"):
            return jsonify({"ok": False, "say": "Nothing is playing."})
        k = song_key(n["title"], n.get("artist"))
        prev = state["taste"]["rated"].get(k)
        up = bool(d.get("up"))
        val = 1 if up else -1
    if prev == val:  # tapping it again takes it back
        taste_add(n["title"], n.get("artist"), -3 if up else 5, -1 if up else 1)
        with lock:
            state["taste"]["rated"].pop(k, None)
            save_state()
        return jsonify({"ok": True, "rating": None})
    undo = (-3 if prev == 1 else 5 if prev == -1 else 0)
    taste_add(n["title"], n.get("artist"), undo + (3 if up else -5), (1 if up else -1) * (1 if prev is None else 2),
              "liked" if up else "disliked")
    with lock:
        state["taste"]["rated"][k] = val
        save_state()
    if up:
        return jsonify({"ok": True, "rating": 1, "say": "Liked. Aux IQ will play more like this."})
    no_judge()
    if device_mode():
        device_command("next")
    else:
        radio("skip")
    return jsonify({"ok": True, "rating": -1, "say": "Got it, skipping. Less like this."})


# ------------------------------------------------------------------ routes: downloads
@app.get("/api/downloads")
def api_downloads():
    with lock:
        active = [public(i) for i in state["jobs"] + state["queue"]
                  if i["status"] in ("pending", "resolving", "downloading", "failed")]
        saved = []
        for r in state["recent"]:
            if r.get("hidden"):
                continue
            p = MUSIC / r["path"] if r.get("path") else None
            try:
                size = p.stat().st_size if p else None
            except OSError:
                size = None
            saved.append({**{k: r.get(k) for k in ("id", "title", "artist", "album", "path", "by", "list_name", "saved", "added")},
                          "size": size, "exists": size is not None, "in_navidrome": bool(r.get("nd")),
                          "cover": cover_of(r)})
    return jsonify({"active": active, "saved": saved[:150]})


@app.post("/api/retry/<item_id>")
@limiter.limit(LIMIT_ADD)
def api_retry(item_id):
    with lock:
        it = next((i for i in state["queue"] + state["jobs"] if i["id"] == item_id), None)
        if not it:
            abort(404)
        it["status"] = "pending"
        it["error"] = None
        save_state()
    with work:
        work.notify_all()
    return jsonify({"ok": True})


@app.post("/api/downloads/clear")
def api_downloads_clear():
    what = (request.get_json(silent=True) or {}).get("what")
    with lock:
        if what == "failed":
            state["queue"] = [i for i in state["queue"] if i["status"] != "failed"]
            state["jobs"] = [i for i in state["jobs"] if i["status"] != "failed"]
        elif what == "saved":
            for r in state["recent"]:
                r["hidden"] = True  # only the list; the songs stay in the library
        save_state()
    return jsonify({"ok": True})


@app.post("/api/clear-failed")
def api_clear_failed():
    with lock:
        state["queue"] = [i for i in state["queue"] if i["status"] != "failed"]
        state["jobs"] = [i for i in state["jobs"] if i["status"] != "failed"]
        save_state()
    return jsonify({"ok": True})


@app.post("/api/delete-file/<item_id>")
def api_delete_file(item_id):
    """Delete a song Aux saved (asks the page to confirm first)."""
    with lock:
        r = next((x for x in state["recent"] if x["id"] == item_id), None)
        if not r or not r.get("path"):
            abort(404)
        p = (MUSIC / r["path"]).resolve()
        if not str(p).startswith(str(MUSIC.resolve()) + "/"):
            abort(400)
        used = sum(1 for x in state["recent"] if x.get("path") == r["path"])
    if p.is_file() and used == 1:
        p.unlink()
        for d in (p.parent, p.parent.parent):
            try:
                d.rmdir()  # only if now empty
            except OSError:
                break
    with lock:
        state["recent"] = [x for x in state["recent"] if x["id"] != item_id]
        songs.library[:] = [e for e in songs.library if e["path"] != r["path"]]
        save_state()
    try:
        if r.get("nd_id"):  # take it out of Requests too
            pl = next((x for x in nav.playlists() if x["name"] == config.get("requests_playlist")), None)
            if pl:
                idx = [i for i, x in enumerate(nav.playlist(pl["id"])["songs"]) if x["nd_id"] == r["nd_id"]]
                if idx:
                    nav.call("updatePlaylist", playlistId=pl["id"], songIndexToRemove=idx)
        nav.call("startScan")
    except Exception as e:
        log("navidrome after delete:", e)
    return jsonify({"ok": True, "say": f"Deleted {r.get('title')}."})


def send_song(path, title, artist):
    p = (MUSIC / path).resolve()
    if not str(p).startswith(str(MUSIC.resolve()) + "/") or not p.is_file():
        abort(404)
    name = songs.safe_name(f"{artist} - {title}" if artist else title, "song") + p.suffix
    return send_file(p, as_attachment=True, download_name=name)


@app.get("/api/file/<item_id>")
def api_file(item_id):
    it = find_item(item_id)
    if it and it.get("path"):
        return send_song(it["path"], it.get("title"), it.get("artist"))
    if it and it.get("nd_id"):
        return api_lib_download(it["nd_id"])
    abort(404)


# ------------------------------------------------------------------ routes: library
def nd_problem(e=None):
    """What to tell people when a Navidrome request can't be made or fails."""
    if not config.navidrome_on():
        return "Navidrome is off (Settings → Configuration)."
    if not (config.get("navidrome_user") and config.get("navidrome_pass")):
        return "Add the Navidrome username and password in Settings → Configuration."
    if isinstance(e, nav.Refused):
        return f"Navidrome said: {e}"
    return "Navidrome isn't answering (it may be asleep)."


def nd_route(fn):
    try:
        return jsonify(fn())
    except Exception as e:
        log("navidrome:", e)
        return jsonify({"error": nd_problem(e)}), 503


@app.get("/api/lib/home")
def api_lib_home():
    return nd_route(lambda: {"newest": nav.albums("newest", 18), "recent": nav.albums("recent", 12),
                             "frequent": nav.albums("frequent", 12), "playlists": nav.playlists()})


@app.get("/api/lib/artists")
def api_lib_artists():
    return nd_route(nav.artists)


@app.get("/api/lib/artist/<aid>")
def api_lib_artist(aid):
    return nd_route(lambda: nav.artist(aid))


@app.get("/api/lib/album/<aid>")
def api_lib_album(aid):
    return nd_route(lambda: nav.album(aid))


@app.get("/api/lib/albums")
def api_lib_albums():
    kind = request.args.get("type", "newest")
    if kind not in ("newest", "recent", "frequent", "random", "alphabeticalByName", "starred", "highest"):
        kind = "newest"
    return nd_route(lambda: nav.albums(kind, int(request.args.get("size", 40)), int(request.args.get("offset", 0))))


@app.get("/api/lib/playlists")
def api_lib_playlists():
    return nd_route(nav.playlists)


@app.get("/api/lib/playlist/<pid>")
def api_lib_playlist(pid):
    return nd_route(lambda: nav.playlist(pid))


@app.get("/api/lib/starred")
def api_lib_starred():
    return nd_route(nav.starred)


@app.get("/api/lib/search")
def api_lib_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"songs": [], "albums": [], "artists": []})
    return nd_route(lambda: nav.search(q))


@app.post("/api/lib/star")
def api_lib_star():
    d = request.get_json(silent=True) or {}
    return nd_route(lambda: (nav.star(d["id"], bool(d.get("on", True))), {"ok": True})[1])


@app.get("/api/lib/cover/<cid>")
def api_lib_cover(cid):
    try:
        data = nav.cover(cid, int(request.args.get("size", 300)))
    except Exception:
        abort(404)
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "max-age=604800"})


@app.get("/api/lib/download/<sid>")
def api_lib_download(sid):
    try:
        s = nav.song(sid)
        r = nav.raw("download", id=sid)
    except Exception:
        abort(404)
    name = songs.safe_name(f"{s.get('artist')} - {s.get('title')}", "song") + "." + (s.get("suffix") or "mp3")
    return Response(iter(lambda: r.read(65536), b""), mimetype=r.headers.get("Content-Type", "audio/mpeg"),
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})


@app.post("/api/lib/queue")
def api_lib_queue():
    """Navidrome songs / an album / a playlist / an artist -> the queue."""
    d = request.get_json(silent=True) or {}
    where = d.get("where") if d.get("where") in ("next", "end", "now") else "end"
    try:
        if d.get("album_id"):
            got, name = nav.album(d["album_id"])["songs"], None
        elif d.get("playlist_id"):
            pl = nav.playlist(d["playlist_id"])
            got, name = pl["songs"], pl["name"]
        elif d.get("artist_id"):
            got, name = [], None
            for a in nav.artist(d["artist_id"])["albums"][:20]:
                got += nav.album(a["id"])["songs"]
        else:
            got, name = [nav.song(i) for i in d.get("ids") or []], None
    except Exception as e:
        log("navidrome:", e)
        return jsonify({"ok": False, "say": nd_problem(e)}), 503
    if d.get("shuffle"):
        random.shuffle(got)
    items = [nd_item(s) for s in got[:200]]
    for it in items:
        it["list_name"] = name
    if not items:
        return jsonify({"ok": False, "say": "Nothing to add."}), 404
    place(items, where)
    if where == "now":
        jump(items[0]["id"])
    verb = {"next": "Playing next", "now": "Playing now", "end": "Added to the queue"}[where]
    say = f"{verb}: {items[0]['title']}." if len(items) == 1 else f"{verb}: {len(items)} songs."
    return jsonify({"ok": True, "say": say})


# ------------------------------------------------------------------ routes: search
@app.get("/api/search")
@limiter.limit(LIMIT_SEARCH)
def api_search():
    """src=ytm (YouTube Music songs) | yt (YouTube videos) | lib (Navidrome)."""
    q = (request.args.get("q") or "").strip()
    src = request.args.get("src", "ytm")
    if len(q) < 2:
        return jsonify({"results": []})
    out = []
    try:
        if src == "lib":
            return nd_route(lambda: nav.search(q))
        if src == "ytm":
            for r in songs.ytm().search(q, filter="songs", limit=12)[:12]:
                if r.get("videoId"):
                    out.append({"video_id": r["videoId"], "title": r.get("title"),
                                "artist": ", ".join(a["name"] for a in r.get("artists") or []),
                                "album": (r.get("album") or {}).get("name"), "duration": r.get("duration"),
                                "cover": songs.thumb(r.get("thumbnails"), 120)})
        elif src == "yt":
            p = subprocess.run(songs.ytdlp(f"ytsearch12:{q}", "--flat-playlist", "-J", "--no-warnings"),
                               capture_output=True, text=True, timeout=40)
            for e in (json.loads(p.stdout or "{}").get("entries") or []):
                if e.get("id") and len(e["id"]) == 11:
                    d = e.get("duration")
                    out.append({"video_id": e["id"], "title": e.get("title"), "artist": e.get("channel") or e.get("uploader"),
                                "duration": f"{int(d) // 60}:{int(d) % 60:02d}" if d else None,
                                "cover": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg", "views": e.get("view_count")})
    except Exception as e:
        log("search failed:", e)
    return jsonify({"results": out})


_preview_urls = {}


def preview_source(video_id):
    """Direct audio URL for a YouTube video (cached ~1h; they expire)."""
    hit = _preview_urls.get(video_id)
    if hit and time.time() - hit[1] < 3000:
        return hit[0]
    p = subprocess.run(songs.ytdlp("-f", "bestaudio[ext=m4a]/bestaudio", "-g", "--no-playlist", "--no-warnings",
                                    f"https://www.youtube.com/watch?v={video_id}"), capture_output=True, text=True, timeout=60)
    u = (p.stdout or "").strip().splitlines()
    if p.returncode != 0 or not u:
        raise RuntimeError("no audio")
    _preview_urls[video_id] = (u[0], time.time())
    if len(_preview_urls) > 100:
        _preview_urls.pop(next(iter(_preview_urls)))
    return u[0]


@app.get("/api/preview/<video_id>")
@limiter.limit(LIMIT_SEARCH)
def api_preview(video_id):
    """Listen before choosing: the video's audio, passed through this server
    (YouTube's audio links only work for the machine that asked for them, and
    YouTube's embedded player refuses to play on this page)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        abort(404)
    try:
        src = preview_source(video_id)
    except Exception:
        abort(404)
    headers = {"User-Agent": "Mozilla/5.0"}
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    try:
        r = urllib.request.urlopen(urllib.request.Request(src, headers=headers), timeout=20)
    except urllib.error.HTTPError as e:
        if e.code == 416:
            return Response(status=416)
        _preview_urls.pop(video_id, None)
        abort(502)
    out = {k: r.headers[k] for k in ("Content-Length", "Content-Range", "Accept-Ranges") if r.headers.get(k)}
    out["Accept-Ranges"] = "bytes"
    return Response(iter(lambda: r.read(65536), b""), status=r.status,
                    mimetype=r.headers.get("Content-Type", "audio/mp4"), headers=out)


@app.get("/api/link")
@limiter.limit(LIMIT_SEARCH)
def api_link():
    """What a pasted link is, for the preview card."""
    q = (request.args.get("q") or "").strip()
    vid = songs.YT_ID.search(q)
    if vid and "youtu" in q:
        return jsonify({"kind": "video", "video_id": vid.group(1)})
    sp = songs.SP_RE.search(q)
    if sp:
        try:
            tracks, name = songs.spotify_tracks(q)
        except Exception:
            tracks, name = [], None
        return jsonify({"kind": "spotify-" + sp.group(1), "name": name or (tracks[0][0] if tracks else None),
                        "count": len(tracks), "first": (tracks[0][0] + " · " + ", ".join(tracks[0][1])) if tracks else None})
    if "youtu" in q and "list=" in q:
        return jsonify({"kind": "playlist"})
    return jsonify({"kind": "unknown"})


# ------------------------------------------------------------------ routes: now playing extras
_lyrics = {}


def duration_of(it):
    if it.get("duration"):
        return it["duration"]
    if it.get("path"):
        try:
            return int(MP3(MUSIC / it["path"]).info.length)
        except Exception:
            return None
    return None


def lrclib(path, qs):
    u = f"https://lrclib.net/api/{path}?" + urllib.parse.urlencode(qs)
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Auxx In (self-hosted music player)"}), timeout=10) as r:
        return json.loads(r.read())


def lyrics_names(title, artist):
    """What to look lyrics up by: the main artist, and the title without
    "Artist - " in front, (Official Video), (feat. ...) and (From "Film")."""
    artist = re.split(r",|/|&| feat\.? | ft\.? | x ", artist or "", flags=re.I)[0].strip()
    t = songs.TITLE_JUNK.sub("", songs.PROMO_RE.sub("", songs.FROM_RE.sub("", title or "")))
    t = re.sub(r"\s*[\(\[][^\)\]]*\b(feat|ft|prod|with)\b[^\)\]]*[\)\]]", "", t, flags=re.I)
    if " - " in t:
        left, right = t.split(" - ", 1)
        if not artist or songs.words(left) & songs.words(artist):
            t, artist = right, artist or left.strip()
    return songs.clean(t) or t, artist


_lyrics_busy = {}  # key -> Event while a lookup runs, so the page and the background don't both ask


def lyrics_key(n):
    return f"{n.get('title')}|{n.get('artist')}"


def find_lyrics(n):
    """Lyrics for a song, from LRCLIB (free, open); remembered for the last 200 songs."""
    key = lyrics_key(n)
    with lock:
        if key in _lyrics:
            return _lyrics[key]
        busy = _lyrics_busy.get(key)
        if not busy:
            _lyrics_busy[key] = threading.Event()
    if busy:  # someone is already looking: wait for that answer
        busy.wait(45)
        return _lyrics.get(key) or {"found": False, "synced": [], "plain": None}
    try:
        title, artist = lyrics_names(n["title"], n.get("artist"))
        album = n.get("album") or ""
        album = "" if album.startswith("[") or "unknown" in album.lower() else album
        dur = duration_of(n)
        q = {"track_name": title, "artist_name": artist}
        # Exact first (album and length help), then looser searches.
        tries = [("get", {**q, **({"album_name": album} if album else {}), **({"duration": dur} if dur else {})}),
                 ("search", q), ("search", {"q": f"{artist} {title}".strip()})]
        found, failed = None, False
        for path, qs in tries:
            try:
                d = lrclib(path, qs)
            except urllib.error.HTTPError as e:
                failed = failed or e.code != 404  # 404 = not there; anything else = try again later
                continue
            except Exception:
                failed = True
                continue
            if isinstance(d, list):
                # A search: the same title (not a remix) and the closest length, synced first.
                d = [x for x in d if x.get("syncedLyrics") or x.get("plainLyrics")]
                d = min(d, default=None, key=lambda x: (songs.base_title(x.get("trackName")) != songs.base_title(title),
                                                        abs((x.get("duration") or 0) - (dur or 0)) > 5,
                                                        not x.get("syncedLyrics")))
            if d and (d.get("syncedLyrics") or d.get("plainLyrics")):
                found = d
                break
        synced = []
        for line in ((found or {}).get("syncedLyrics") or "").splitlines():
            m = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", line)
            if m:
                synced.append([int(m.group(1)) * 60 + float(m.group(2)), m.group(3)])
        res = {"found": bool(found), "synced": synced, "plain": (found or {}).get("plainLyrics")}
        if not found and failed:  # LRCLIB was down or slow: don't remember "no lyrics"
            return res
        with lock:
            _lyrics[key] = res
            if len(_lyrics) > 200:
                _lyrics.pop(next(iter(_lyrics)))
        return res
    finally:
        with lock:
            _lyrics_busy.pop(key).set()


_lyrics_used = {"at": 0}  # when someone last opened Lyrics (or the full-screen lyric line)
LYRICS_WARM = 30 * 60


def prefetch_lyrics():
    """When a song starts and someone has used lyrics in the last 30 minutes,
    look this song's up in the background so they're ready. Otherwise nothing
    is fetched until someone asks."""
    if time.time() - _lyrics_used["at"] > LYRICS_WARM:
        return
    with lock:
        n = dict(state["now"]) if state.get("now") else None
    if not n or not n.get("title"):
        return

    def run():
        try:
            find_lyrics(n)
        except Exception as e:
            log("lyrics:", e)
    threading.Thread(target=run, daemon=True).start()


@app.get("/api/lyrics")
def api_lyrics():
    """Lyrics for the song playing now."""
    with lock:
        n = dict(state["now"]) if state["now"] else None
    _lyrics_used["at"] = time.time()
    if not n or not n.get("title"):
        return jsonify({"found": False})
    return jsonify({**find_lyrics(n), "started": n.get("started"), "duration": duration_of(n), "id": n.get("id")})


@app.get("/api/cover/<item_id>")
def api_cover(item_id):
    it = find_item(item_id)
    if not it:
        abort(404)
    if it.get("path"):
        try:
            pics = ID3(MUSIC / it["path"]).getall("APIC")
            if pics:
                return Response(pics[0].data, mimetype=pics[0].mime or "image/jpeg",
                                headers={"Cache-Control": "max-age=86400"})
        except Exception:
            pass
    if it.get("cover_url"):
        try:
            data, mime = songs.fetch(it["cover_url"])
            return Response(data, mimetype=mime, headers={"Cache-Control": "max-age=86400"})
        except Exception:
            pass
    abort(404)


# ------------------------------------------------------------------ routes: the radio (internal)
@app.before_request
def internal_only():
    if request.path.startswith("/internal/") and not secrets.compare_digest(
            request.headers.get("X-Internal-Key", ""), INTERNAL_KEY):
        abort(403)


@app.get("/internal/next")
@csrf.exempt
def internal_next():
    """The next queued song for the radio to load. Songs already handed out
    ("loaded") are skipped, and so are songs still downloading: they keep
    their place at the top and play once they're ready."""
    with lock:
        if device_mode():
            return ""
        for it in state["queue"]:
            if it["status"] == "loaded":
                continue
            if it["status"] == "ready":
                it["status"] = "loaded"
                save_state()
                uri = nav.stream_url(it["nd_id"]) if it.get("nd_id") and not it.get("path") else str(MUSIC / it["path"])
                if it.get("resume"):
                    uri = f'annotate:liq_cue_in="{it["resume"]}":{uri}'
                return uri
    return ""


@app.post("/internal/reset")
@csrf.exempt
def internal_reset():
    """The radio (re)started and forgot what it had loaded."""
    with lock:
        for it in state["queue"]:
            if it["status"] == "loaded":
                it["status"] = "ready"
        save_state()
    threading.Thread(target=apply_radio_settings, daemon=True).start()
    return "ok"


ND_ID_RE = re.compile(r"[?&]id=([^&]+)")
ANNOTATE_RE = re.compile(r'^annotate:(?:[a-z_]+="[^"]*",?)*:')


@app.post("/internal/track")
@csrf.exempt
def internal_track():
    uri = ANNOTATE_RE.sub("", request.get_data(as_text=True).strip())
    if device_mode():
        # The page is the player: a radio left running (other listeners) must
        # not take over "now playing" or the history.
        return "ignored"
    resume = 0
    nd_id = None
    rel = None
    if uri.startswith("http"):
        m = ND_ID_RE.search(uri)
        nd_id = urllib.parse.unquote(m.group(1)) if m else None
    else:
        try:
            rel = str(Path(uri).relative_to(MUSIC))
        except ValueError:
            rel = None
    with lock:
        it = next((i for i in state["queue"] if i["status"] in ("loaded", "ready") and
                   ((rel and i.get("path") == rel) or (nd_id and i.get("nd_id") == nd_id and not i.get("path")))), None)
        if it:
            state["queue"].remove(it)
            now = {k: it.get(k) for k in NOW_KEYS}
            now["from"] = it.get("keep_from") or "queue"
            resume = it.get("resume") or 0
            save_state()
    if not it:
        if nd_id:
            try:
                s = nav.song(nd_id)
            except Exception:
                s = {"title": "Library song", "artist": "", "album": ""}
            now = {"id": "nd-" + nd_id, "nd_id": nd_id, "title": s.get("title"), "artist": s.get("artist"),
                   "album": s.get("album"), "cover_id": s.get("cover_id"), "duration": s.get("duration"), "from": "library"}
        else:
            p = Path(uri)
            t = songs.read_tags(p) if p.is_file() else {"title": p.stem, "artist": "", "album": ""}
            now = {"id": "lib-" + uuid.uuid5(uuid.NAMESPACE_URL, uri).hex[:8], "path": rel, "from": "library", **t}
    now["started"] = time.time() - resume
    if not now.get("duration"):
        now["duration"] = duration_of(now)
    taste_judge(state["now"])
    with lock:
        state["now"] = now
        state["history"].insert(0, now)
        state["history"] = state["history"][:60]
        save_state()
    iq_on_song()
    prefetch_lyrics()
    return "ok"


if __name__ == "__main__":
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    DATA.mkdir(parents=True, exist_ok=True)
    auth.bootstrap()
    for t in DATA.glob("tmp*"):  # half-finished downloads from before a restart
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)
    load_state()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=library_loop, daemon=True).start()
    threading.Thread(target=navidrome_loop, daemon=True).start()
    threading.Thread(target=timers_loop, daemon=True).start()
    # waitress: a production server in pure Python. Threads: song streams and
    # previews hold one each while they play, so leave room for them.
    serve(app, host="0.0.0.0", port=8140, threads=int(os.environ.get("WEB_THREADS", "32")), ident="Auxx In")
