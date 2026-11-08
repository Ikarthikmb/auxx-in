# Contributing

Thanks for helping. Small, focused pull requests are easiest to review.

## Develop

```sh
cp .env.example .env            # songs go to ./music unless you set MUSIC_DIR
cp docker-compose.build.yml docker-compose.override.yml   # build from your checkout (git-ignored)
docker compose up -d --build    # http://127.0.0.1:8140
```

Tests and lint, without Docker (Python 3.13):

```sh
pip install -r requirements-dev.txt
ruff check .
pytest -q
./scripts/check-public.sh       # nothing private in the repo
```

## Guidelines

- The page is plain JavaScript with no build step; keep it that way.
- New settings go in `web/config.py` (`FIELDS`), so they show up in Settings → Configuration.
- Anything that needs Navidrome must keep working (or hide itself) when Navidrome is off.
- Matching changes (`web/songs.py`) need a test in `tests/test_matching.py`.
- Never commit `.env`, the data folder, cookies or logs. `scripts/check-public.sh` checks this.

By contributing you agree your work is released under the [GPL-3.0](LICENSE).
