# Security

Please report security problems privately: on GitHub, open the **Security** tab of this
repository and choose **Report a vulnerability**. Don't open a public issue.

## What to know when running Auxx-In

- **Keep it on a network you trust** (your LAN or a VPN such as Tailscale/WireGuard). The page
  needs a login, but it can start downloads and change files in your music folder.
- **The radio stream (`/aux.mp3`) has no login**, because music apps play internet radio
  without one. Anyone who can reach the radio port can listen.
- **Settings are stored as plain text** in the data folder (`config.json`, `cookies.txt`,
  `users.json` with password hashes), readable only by the container's user. Back it up and
  protect it like a password file.
- **Serve it over HTTPS** if it leaves your home network, and set `COOKIE_SECURE=true`.
- **`AUTH_PROXY_HEADER`** trusts whoever sets that header. Only use it when Auxx-In can't be
  reached except through your login proxy.
