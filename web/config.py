"""Auxx In configuration: environment defaults, overridden by what is saved from
Settings -> Configuration (DATA_ROOT/config.json). Read with get(); changes
take effect right away, no restart."""
import json
import os
import threading
from pathlib import Path

FILE = Path(os.environ.get("DATA_ROOT", "/data")) / "config.json"

# (section, key, env var, default, label, hint, kind: toggle | text | number | secret)
FIELDS = [
    ("Navidrome", "use_navidrome", "USE_NAVIDROME", True, "Use Navidrome",
     "Library tab, playlists, favorites and the \"Auxx In\" station in Navidrome clients", "toggle"),
    ("Navidrome", "navidrome_url", "NAVIDROME_URL", "", "Server address",
     "As Aux reaches it, e.g. http://navidrome:4533", "text"),
    ("Navidrome", "navidrome_user", "NAVIDROME_USER", "", "Username", None, "text"),
    ("Navidrome", "navidrome_pass", "NAVIDROME_PASS", "", "Password", None, "secret"),
    ("Radio", "stream_url", "STREAM_URL", "http://localhost:8141/aux.mp3", "Stream address",
     "What Navidrome clients tune in to; added to Navidrome as the \"Auxx In\" radio station", "text"),
    ("Library", "requests_playlist", "REQUESTS_PLAYLIST", "Requests", "Requests playlist",
     "Navidrome playlist shared songs are saved to", "text"),
    ("Library", "playlist_cap", "PLAYLIST_CAP", 50, "Playlist cap",
     "Most songs taken from one shared playlist", "number"),
    ("Library", "autoplay_daily", "AUTOPLAY_DAILY", 40, "Similar songs a day",
     "Most autoplay downloads a day", "number"),
    ("Downloads", "mp3_quality", "MP3_QUALITY", "0", "MP3 quality", None, "choice"),
    ("Downloads", "max_song_mb", "MAX_SONG_MB", 100, "Largest song (MB)",
     "Longer or bigger songs aren't downloaded. 0 = no limit", "number"),
    ("Downloads", "keep_free_gb", "KEEP_FREE_GB", 5, "Keep free space (GB)",
     "Downloads stop when the music disk has less free than this. 0 = no limit", "number"),
    ("Downloads", "youtube_cookies", "", "", "YouTube cookies",
     "cookies.txt (Netscape format) from a browser signed in to YouTube. Only needed when "
     "YouTube asks to confirm you're not a bot, or for age-restricted songs.", "file"),
    ("Aux IQ", "llm_url", "LLM_URL", "", "LLM endpoint",
     "OpenAI-compatible /v1/chat/completions URL", "text"),
    ("Aux IQ", "llm_key", "LLM_KEY", "", "API key", None, "secret"),
    ("Aux IQ", "llm_model", "LLM_MODEL", "gemini-3.1-flash-lite", "Model", "A fast non-thinking model", "text"),
    ("Aux IQ", "llm_backup", "LLM_BACKUP", "gemini-3.5-flash-lite", "Backup model", None, "text"),
    ("Aux IQ", "iq_buffer", "IQ_BUFFER", 5, "Picks waiting", "1 to 5", "number"),
    ("Aux IQ", "iq_daily", "IQ_DAILY", 20, "Downloads a day", None, "number"),
]
_BY_KEY = {f[1]: f for f in FIELDS}
OPTIONS = {"mp3_quality": [["0", "Best (VBR, ~245 kbps)"], ["320K", "320 kbps"], ["192K", "192 kbps"],
                           ["128K", "128 kbps"]]}
# "file" fields are kept as their own file in the data folder, not in config.json.
FILES = {"youtube_cookies": FILE.with_name("cookies.txt")}
_lock = threading.Lock()


def _load():
    try:
        d = json.loads(FILE.read_text())
        return {k: v for k, v in d.items() if k in _BY_KEY}
    except (OSError, ValueError):
        return {}


_saved = _load()


def get(key):
    _, _, env, default, _, _, kind = _BY_KEY[key]
    if kind == "file":
        return FILES[key].is_file()
    v = _saved.get(key)
    if v is None:
        v = os.environ.get(env, default)
        if isinstance(v, str) and v.strip().startswith("#"):
            v = default  # "KEY=   # note" in a .env file reaches us as "# note"
    if kind == "number":
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    if kind == "toggle":
        return _bool(v)
    return str(v)


def _bool(v):
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")


def navidrome_on():
    """Made for Navidrome, works without it: off when switched off or no address is set."""
    return get("use_navidrome") and bool(get("navidrome_url"))


def public():
    """For the Settings page: secrets are never sent back, only whether they're set."""
    out = []
    for section, key, _, _, label, hint, kind in FIELDS:
        v = get(key)
        out.append({"section": section, "key": key, "label": label, "hint": hint, "kind": kind,
                    "value": None if kind in ("secret", "file") else v, "set": bool(v),
                    **({"options": OPTIONS[key]} if key in OPTIONS else {})})
    return out


def save(updates):
    """Saves known keys. A blank secret is left as it was. Returns the keys that changed."""
    changed = []
    with _lock:
        for key, v in (updates or {}).items():
            if key not in _BY_KEY:
                continue
            kind = _BY_KEY[key][6]
            if kind == "secret" and (v is None or v == ""):
                continue
            if kind == "file":  # text = save it, false = remove it, blank = keep
                if v is False and FILES[key].is_file():
                    FILES[key].unlink()
                    changed.append(key)
                elif isinstance(v, str) and v.strip():
                    if "\t" not in v:
                        raise ValueError("That doesn't look like a cookies.txt file.")
                    tmp = FILES[key].with_suffix(".tmp")
                    tmp.write_text(v if v.endswith("\n") else v + "\n")
                    os.chmod(tmp, 0o600)
                    tmp.replace(FILES[key])
                    changed.append(key)
                continue
            if kind == "choice" and str(v) not in [o[0] for o in OPTIONS[key]]:
                continue
            if kind == "number":
                try:
                    v = max(0, int(v))
                except (TypeError, ValueError):
                    continue
            elif kind == "toggle":
                v = _bool(v)
            else:
                v = str(v).strip()
            if v != get(key):
                _saved[key] = v
                changed.append(key)
        if changed:
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_saved, indent=1))
            os.chmod(tmp, 0o600)
            tmp.replace(FILE)
    return changed
