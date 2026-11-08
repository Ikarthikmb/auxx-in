"""Finding songs: input -> YouTube Music song -> tagged MP3 in the library.

Matching (scoring against remixes/covers/karaoke/other-language versions, film
albums), YouTube / Spotify link handling, download + tagging, and the index of
what's already on disk. Used by app.py.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError

import config

MUSIC = Path(os.environ.get("MUSIC_ROOT", "/music"))
DATA = Path(os.environ.get("DATA_ROOT", "/data"))
COOKIES = DATA / "cookies.txt"  # Settings -> Configuration -> Downloads


def ytdlp(*args):
    """yt-dlp with the saved YouTube cookies, if any."""
    return ["yt-dlp", *(["--cookies", str(COOKIES)] if COOKIES.is_file() else []), *args]
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}
_ytm = None


def ytm():
    global _ytm
    if _ytm is None:
        from ytmusicapi import YTMusic
        _ytm = YTMusic()
    return _ytm


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ helpers
def clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def safe_name(s, fallback="Unknown"):
    s = unicodedata.normalize("NFC", clean(s)).replace("/", "-").replace("\\", "-")
    s = re.sub(r'[<>:"|?*\x00-\x1f]', "", s).strip(" .")
    return s[:120] or fallback


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def words(s):
    return set(w for w in norm(s).split() if w)


def secs(length):
    if isinstance(length, (int, float)):
        return int(length)
    try:
        parts = [int(p) for p in str(length).split(":")]
    except ValueError:
        return None
    n = 0
    for p in parts:
        n = n * 60 + p
    return n


# Movie songs: 'Kesariya (From "Brahmastra")' -> album "Brahmastra";
# "Pk (Original Motion Picture Soundtrack)" -> "Pk".
FROM_RE = re.compile(r"""[\(\[]\s*from\s+(?:"([^"]+)"|“([^”]+)”|'([^']+)'|‘([^’]+)’|([^\)\]"“'‘]+))\s*[\)\]]""", re.I)
# Promo tags labels add to a title ("- Happy Diwali"): same recording.
PROMO_RE = re.compile(r"\s*[\(\[][^\(\)\[\]]*\b(happy|diwali|holi|eid|christmas|new year|navratri|promo)\b[^\(\)\[\]]*[\)\]]", re.I)
LANGS = {"hindi", "tamil", "telugu", "malayalam", "kannada", "marathi", "bengali", "punjabi", "gujarati"}


def from_movie(s):
    m = FROM_RE.search(s or "")
    return clean(next(g for g in m.groups() if g)) if m else None
OST_RE = re.compile(r"\s*[\(\[][^\)\]]*(soundtrack|o\.?s\.?t\.?)[^\)\]]*[\)\]]", re.I)


def album_name(album, title):
    for s in (album, title):
        movie = from_movie(s)
        if movie:
            return movie
    return clean(OST_RE.sub("", album or "")) or None


VARIANT = {"remix", "lofi", "lo", "fi", "slowed", "reverb", "sped", "speed", "cover", "karaoke",
           "instrumental", "live", "acoustic", "unplugged", "reprise", "version", "mix", "dance",
           "mashup", "8d", "synthwave", "nightcore", "edit", "female", "male", "bass", "boosted",
           "dj", "flip", "rework", "extended", "teaser", "trailer", "status", "ringtone",
           "covers", "tribute", "originally", "performed", "famous", "style", "piano", "lullaby"}


def score(cand, query_words, want_secs=None, want_artists=None):
    """Higher is better. Rewards title/artist words that match what was asked
    for, punishes remixes/covers/festival versions nobody asked for."""
    t = words(cand.get("title"))
    a = words(" ".join(x["name"] for x in cand.get("artists") or []))
    # The song's own name counts most; words in brackets less, so a cover
    # titled "Song (Original Artist)" doesn't beat the original.
    core = words(re.sub(r"[\(\[].*?[\)\]]", " ", (cand.get("title") or "").split(" - ")[0])) or t
    s = len(core & query_words) * 3 + len((t - core) & query_words) + len(a & query_words) * 2
    s -= 4 * len((t & VARIANT) - query_words)
    s -= 4 * len((words((cand.get("album") or {}).get("name")) & VARIANT) - query_words - t)
    s -= 3 * len((t & LANGS) - query_words)
    promo = words(" ".join(m.group(0) for m in PROMO_RE.finditer(cand.get("title") or "")))
    s -= 0.3 * len(t - query_words - VARIANT - promo)
    movie = from_movie(cand.get("title")) or from_movie((cand.get("album") or {}).get("name"))
    if movie and words(movie) & query_words:
        s += 2
    if want_artists:
        s += 2 * len(a & want_artists)
    d = secs(cand.get("duration") or cand.get("length"))
    if want_secs and d:
        diff = abs(d - want_secs)
        s += 3 if diff <= 3 else 1 if diff <= 10 else -1 if diff > 40 else 0
    if not t & query_words:
        s -= 5  # the song's own name has to match something asked for
    alb = (cand.get("album") or {}).get("name")
    if alb and t & query_words:
        s += 1
        # Prefer the song on its album over the same song's single release.
        if norm(alb).strip() != norm(cand.get("title")).strip():
            s += 1.5
    return s


def search_song(query, want_secs=None, want_artists=None):
    q = words(query)
    try:
        res = ytm().search(query, filter="songs", limit=8)
    except Exception as e:
        log("ytm search failed:", e)
        return None
    res = [r for r in res if r.get("videoId")]
    if not res:
        return None
    # YouTube Music's own order counts a little: the original usually leads.
    best = max(enumerate(res[:8]), key=lambda x: score(x[1], q, want_secs, want_artists) + max(0, 2 - x[0]) * 0.75)[1]
    # Too little in common with what was asked for: better none than wrong.
    got = words(best.get("title")) | words(" ".join(a["name"] for a in best.get("artists") or []))
    if not words(best.get("title")) & q or len(got & q) < max(1, len(q) / 2):
        return None
    return best


def thumb(thumbs, size=600):
    if not thumbs:
        return None
    url = thumbs[-1]["url"]
    return re.sub(r"=w\d+-h\d+.*$", f"=w{size}-h{size}-l90-rj", url)


_albums = {}


def album_info(album_id):
    if not album_id:
        return {}
    if album_id not in _albums:
        try:
            _albums[album_id] = ytm().get_album(album_id)
        except Exception as e:
            log("get_album failed:", e)
            _albums[album_id] = {}
    return _albums[album_id]


def song_spec(r, source):
    """A YouTube Music song result -> what we tag and file it as."""
    artists = [x["name"] for x in r.get("artists") or [] if x.get("name")]
    alb = r.get("album") or {}
    info = album_info(alb.get("id"))
    album = album_name(alb.get("name"), r.get("title"))
    album_artist = ", ".join(x["name"] for x in info.get("artists") or [] if x.get("name")) or (artists[0] if artists else None)
    if from_movie(alb.get("name")) or from_movie(r.get("title")):
        # A film song released as its own single: file it under the film.
        album_artist = "Various Artists"
    title = clean(PROMO_RE.sub("", FROM_RE.sub("", r.get("title") or ""))) or r.get("title")
    track = None
    for i, t in enumerate(info.get("tracks") or [], 1):
        if t.get("videoId") == r["videoId"] or norm(t.get("title")) == norm(r.get("title")):
            track = i
            break
    return {
        "video_id": r["videoId"], "title": title, "artist": ", ".join(artists) or "Unknown Artist",
        "album": album or "Singles", "album_artist": album_artist or "Unknown Artist",
        "year": str(info.get("year") or r.get("year") or "") or None, "track": track,
        "cover_url": thumb(info.get("thumbnails")) or thumb(r.get("thumbnails")), "from": source,
        "duration": secs(r.get("duration_seconds") or r.get("duration")),
    }


TITLE_JUNK = re.compile(r"\s*[\(\[\|].*?(official|video|lyric|audio|4k|hd|full song|visuali[sz]er)[^\)\]\|]*[\)\]]?", re.I)


def yt_video_spec(video_id):
    """A shared YouTube video -> the proper song. The video's own title is the
    truth (YouTube Music's radio for a plain video can start elsewhere)."""
    meta = {}
    try:
        tr = ytm().get_watch_playlist(videoId=video_id, limit=1)["tracks"][0]
        if tr.get("videoId") == video_id and (tr.get("album") or {}).get("name"):
            return song_spec(tr, "YouTube")
    except Exception as e:
        log("watch playlist failed:", e)
    try:
        out = subprocess.run(ytdlp("-j", "--no-warnings", "--no-playlist", f"https://www.youtube.com/watch?v={video_id}"),
                             capture_output=True, text=True, timeout=60)
        meta = json.loads(out.stdout or "{}")
    except Exception as e:
        log("yt-dlp metadata failed:", e)
    title = meta.get("track") or meta.get("title") or ""
    artist = meta.get("artist") or meta.get("creator") or ""
    uploader = re.sub(r"\s*-\s*Topic$|VEVO$", "", meta.get("uploader") or "").strip()
    q = clean(TITLE_JUNK.sub("", title).split(" | ")[0])
    q = clean(re.sub(r"[\(\[].*?[\)\]]", "", q)) or title
    best = search_song(f"{q} {artist}".strip(), want_secs=meta.get("duration"),
                       want_artists=words(artist or uploader))
    if best:
        return song_spec(best, "YouTube")
    # Not a catalogue song (a live clip, a cover...): keep the video's audio.
    if not title:
        return None
    return {"video_id": video_id, "title": clean(TITLE_JUNK.sub("", title)) or title,
            "artist": artist or uploader or "Unknown Artist", "album": "Singles",
            "album_artist": artist or uploader or "Unknown Artist", "year": (meta.get("upload_date") or "")[:4] or None,
            "track": None, "cover_url": meta.get("thumbnail"), "from": "YouTube", "duration": secs(meta.get("duration"))}


def yt_exact_spec(video_id):
    """This exact video's audio (a live version, a cover...), not the studio
    song: tagged from YouTube Music when the video is a catalogue song itself,
    otherwise from the video's own title."""
    try:
        tr = ytm().get_watch_playlist(videoId=video_id, limit=1)["tracks"][0]
        if tr.get("videoId") == video_id and (tr.get("album") or {}).get("name"):
            return song_spec(tr, "YouTube")
    except Exception as e:
        log("watch playlist failed:", e)
    out = subprocess.run(ytdlp("-j", "--no-warnings", "--no-playlist", f"https://www.youtube.com/watch?v={video_id}"),
                         capture_output=True, text=True, timeout=60)
    meta = json.loads(out.stdout or "{}")
    title = meta.get("track") or meta.get("title")
    if not title:
        return None
    artist = meta.get("artist") or re.sub(r"\s*-\s*Topic$|VEVO$", "", meta.get("uploader") or "").strip() or "Unknown Artist"
    # The official video of a catalogue song: proper details, this video's audio.
    q = clean(re.sub(r"[\(\[].*?[\)\]]", "", clean(TITLE_JUNK.sub("", title).split(" | ")[0]))) or title
    best = search_song(f"{q} {meta.get('artist') or ''}".strip(), want_secs=meta.get("duration"), want_artists=words(artist))
    if best and meta.get("duration") and abs((secs(best.get("duration")) or 0) - meta["duration"]) <= 15:
        return {**song_spec(best, "YouTube"), "video_id": video_id, "duration": secs(meta.get("duration"))}
    title = clean(TITLE_JUNK.sub("", title)) or title
    return {"video_id": video_id, "title": title, "artist": artist, "album": from_movie(title) or "Singles",
            "album_artist": "Various Artists" if from_movie(title) else artist,
            "year": (meta.get("upload_date") or "")[:4] or None, "track": None,
            "cover_url": meta.get("thumbnail"), "from": "YouTube", "duration": secs(meta.get("duration"))}


def spotify_tracks(url):
    """Spotify link -> [(title, [artists], seconds)] plus the list name, read
    from Spotify's public embed page (no account, no API key, no spotdl)."""
    m = SP_RE.search(url)
    if not m:
        return [], None
    kind, sid = m.groups()
    req = urllib.request.Request(f"https://open.spotify.com/embed/{kind}/{sid}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", "replace")
    j = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    e = json.loads(j.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    if kind == "track":
        arts = [a.get("name") for a in e.get("artists") or []]
        return [(e.get("name") or e.get("title"), arts, (e.get("duration") or 0) // 1000 or None)], None
    tracks = [(t.get("title"), [a.strip() for a in (t.get("subtitle") or "").split(",") if a.strip()],
               (t.get("duration") or 0) // 1000 or None) for t in e.get("trackList") or [] if t.get("title")]
    return tracks, e.get("name") or e.get("title")


def yt_list(url):
    out = subprocess.run(ytdlp("-J", "--flat-playlist", "--no-warnings", url),
                         capture_output=True, text=True, timeout=120)
    d = json.loads(out.stdout or "{}")
    ids = [e["id"] for e in d.get("entries") or [] if e.get("id")]
    name = re.sub(r"^Album - ", "", d.get("title") or "") or None
    return ids, name


YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})")
SP_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(track|album|playlist|artist)/([A-Za-z0-9]+)")


def expand(q):
    """User input -> (list of "work" dicts to resolve one by one, list name)."""
    q = q.strip()
    m = re.search(r"https?://\S+", q)
    url = m.group(0) if m else None
    if not url:
        return [{"kind": "text", "q": q}], None
    sp = SP_RE.search(url)
    if sp:
        if sp.group(1) == "track":
            return [{"kind": "spotify", "url": url}], None
        tracks, name = spotify_tracks(url)
        return [{"kind": "named", "title": t, "artists": a, "secs": d} for t, a, d in tracks], name
    if "youtu" in url:
        vid = YT_ID.search(url)
        lst = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url)
        if vid:  # a song shared from inside a playlist is still that song
            return [{"kind": "yt", "id": vid.group(1)}], None
        if lst or "/playlist" in url or "/browse/" in url:
            ids, name = yt_list(url)
            return [{"kind": "yt", "id": i} for i in ids], name
    return [{"kind": "text", "q": q}], None


def resolve(w):
    if w["kind"] == "yt":
        return yt_video_spec(w["id"])
    if w["kind"] == "ytexact":
        return yt_exact_spec(w["id"])
    if w["kind"] == "ytm":  # a YouTube Music song we already have the details of
        return song_spec(w["track"], w.get("source") or "YouTube Music")
    if w["kind"] == "text":
        r = search_song(w["q"])
        return song_spec(r, "Search") if r else None
    if w["kind"] == "spotify":
        tracks, _ = spotify_tracks(w["url"])
        if not tracks:
            return None
        w = {"title": tracks[0][0], "artists": tracks[0][1], "secs": tracks[0][2]}
    arts = [a for a in w.get("artists") or [] if a]
    r = search_song(f"{w['title']} {' '.join(arts[:2])}", want_secs=w.get("secs"), want_artists=words(" ".join(arts)))
    return song_spec(r, "Spotify") if r else None


# ------------------------------------------------------------------ download + tag
def rel_path(spec):
    return Path(safe_name(spec["album_artist"], "Unknown Artist")) / safe_name(spec["album"], "Singles") / (safe_name(spec["title"], spec["video_id"]) + ".mp3")


def fetch(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "aux"}), timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "image/jpeg")


def square_jpeg(data):
    """Video thumbnails are 16:9; album covers should be square."""
    with tempfile.NamedTemporaryFile(suffix=".img", dir=str(DATA)) as src, \
            tempfile.NamedTemporaryFile(suffix=".jpg", dir=str(DATA)) as dst:
        src.write(data)
        src.flush()
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src.name, "-vf",
                            "crop='min(iw,ih)':'min(iw,ih)',scale=600:600", dst.name], timeout=30)
        return Path(dst.name).read_bytes() if r.returncode == 0 else data


def base_title(t):
    return norm(PROMO_RE.sub("", FROM_RE.sub("", t or ""))).split(" feat ")[0].strip()


def in_library(spec):
    """Same song already on disk (any album/folder)? Then don't download it again."""
    want_t = base_title(spec["title"])
    want_a = words(spec["artist"]) | words(spec["album_artist"])
    for e in library:
        if base_title(e["title"]) == want_t and (words(e["artist"]) & want_a or norm(e["album"]) == norm(spec["album"])):
            if (MUSIC / e["path"]).is_file():
                return Path(e["path"])
    return None


KBPS = {"0": 245, "320K": 320, "192K": 192, "128K": 128}  # MP3 quality -> about how big a minute is


def mins(sec):
    return f"{sec // 3600} h {sec % 3600 // 60} min" if sec >= 3600 else f"{sec // 60} min"


def check_room(spec):
    """Settings -> Downloads: skip songs over the size limit, and stop when
    the music disk is nearly full. Sizes are estimated from the length."""
    limit, keep = config.get("max_song_mb"), config.get("keep_free_gb")
    sec = spec.get("duration") or 0
    est = sec * KBPS.get(config.get("mp3_quality"), 245) * 1000 / 8
    if limit and est > limit * 1e6:
        raise RuntimeError(f"Too long ({mins(sec)}, about {est / 1e6:.0f} MB; limit {limit} MB)")
    if keep and shutil.disk_usage(MUSIC).free - est < keep * 1e9:
        raise RuntimeError(f"Disk almost full (keeping {keep} GB free; see Settings)")


def download(spec, progress=None):
    rel = rel_path(spec)
    dest = MUSIC / rel
    if dest.exists():
        return rel
    have = in_library(spec)
    if have:
        log("already in library:", have)
        return have
    check_room(spec)
    limit = config.get("max_song_mb")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(DATA)) as tmp:
        cmd = ytdlp("-f", "bestaudio", "-x", "--audio-format", "mp3", "--audio-quality", config.get("mp3_quality"),
                    *(["--max-filesize", f"{limit}M"] if limit else []),
                    "--no-playlist", "--no-warnings", "-o", f"{tmp}/a.%(ext)s",
                    f"https://music.youtube.com/watch?v={spec['video_id']}")
        proc = subprocess.Popen(cmd + ["--newline"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        tail = []
        for line in proc.stdout:
            tail = (tail + [line.strip()])[-3:]
            m = re.search(r"\[download\]\s+([\d.]+)%", line)
            if m and progress:
                progress(float(m.group(1)))
        proc.wait(timeout=600)
        got = next(Path(tmp).glob("a.mp3"), None)
        if limit and (any("max-filesize" in t for t in tail) or (got and got.stat().st_size > limit * 1e6)):
            raise RuntimeError(f"Too big (over the {limit} MB limit in Settings)")
        if proc.returncode != 0 or not got:
            raise RuntimeError(next((t for t in reversed(tail) if "ERROR" in t), tail[-1] if tail else "download failed")[:200])
        try:
            tags = ID3(got)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(TIT2(encoding=3, text=spec["title"]))
        tags.add(TPE1(encoding=3, text=spec["artist"]))
        tags.add(TPE2(encoding=3, text=spec["album_artist"]))
        tags.add(TALB(encoding=3, text=spec["album"]))
        if spec.get("year"):
            tags.add(TDRC(encoding=3, text=spec["year"]))
        if spec.get("track"):
            tags.add(TRCK(encoding=3, text=str(spec["track"])))
        if spec.get("cover_url"):
            try:
                img, mime = fetch(spec["cover_url"])
                if "ytimg.com" in spec["cover_url"]:
                    img, mime = square_jpeg(img), "image/jpeg"
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=img))
            except Exception as e:
                log("cover failed:", e)
        tags.save(got, v2_version=3)
        shutil.move(str(got), dest)
    os.chmod(dest, 0o644)
    return rel


# ------------------------------------------------------------------ library index
library = []  # [{path, title, artist, album}]


def read_tags(p):
    t = {"title": p.stem, "artist": p.parent.parent.name, "album": p.parent.name}
    try:
        tags = ID3(p)
        for key, frame in (("title", "TIT2"), ("artist", "TPE1"), ("album", "TALB")):
            if tags.get(frame):
                t[key] = str(tags[frame].text[0])
    except Exception:
        pass
    return t


def index_library():
    global library
    cache_f = DATA / "library.json"
    try:
        cache = {e["path"]: e for e in json.loads(cache_f.read_text())}
    except (OSError, ValueError):
        cache = {}
    out = []
    for root, dirs, files in os.walk(MUSIC):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() not in AUDIO_EXTS:
                continue
            rel = str(p.relative_to(MUSIC))
            try:
                mt = int(p.stat().st_mtime)
            except OSError:
                continue
            e = cache.get(rel)
            if not e or e.get("mtime") != mt:
                e = {"path": rel, "mtime": mt, **read_tags(p)}
            out.append(e)
    library = out
    tmp = cache_f.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(cache_f)
    log(f"library: {len(out)} songs")


