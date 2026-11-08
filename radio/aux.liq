# Auxx-In radio: plays the shared-song queue from aux-web first; when it's empty,
# plays /aux-data/fallback.m3u (written by aux-web: the whole library, the
# Requests playlist, a playlist, an artist... or nothing). Streams MP3 at
# http://<host>:8141/aux.mp3 (in any Navidrome client: Radio -> Auxx-In).
settings.log.stdout.set(true)
settings.log.file.set(false)
settings.log.level.set(3)
settings.request.metadata_decoders.recode.exclude.set(["description", "synopsis", "comment", "lyrics"])
settings.server.telnet.set(true)
settings.server.telnet.bind_addr.set("0.0.0.0")
settings.server.telnet.port.set(1234)

web = environment.get(default="http://aux-web:8140", "AUX_WEB")
key = environment.get(default="", "INTERNAL_KEY")
# Not set: aux-web makes one on first run in the data folder.
key = if key != "" then key else string.trim(file.contents("/aux-data/internal.key")) end
# Sent as a header, not in the address, so it never shows up in logs.
auth = [("X-Internal-Key", key)]

# Switched from the Aux page (telnet "var.set even_volume = true" etc.).
even_volume = interactive.bool("even_volume", true)
crossfade_on = interactive.bool("crossfade", true)
# Fade length (5 / 10 / 15 s), chosen on the Aux page: read once here, so a new
# length restarts the radio (aux-web sends "restart", Docker starts it again).
fade_file = "/aux-data/crossfade"
fade_secs = if file.exists(fade_file) then float_of_string(default=4., string.trim(file.contents(fade_file))) else 4. end

# Next queued song from aux-web: a file path or a Navidrome stream URL.
def next_request() =
  p = string.trim(http.get(headers=auth, "#{web}/internal/next"))
  if p == "" then null else request.create(p) end
end
requests = request.dynamic(id="requests", retry_delay=2., prefetch=1, next_request)
# Keep one song loaded whenever the queue has one, so a share made while
# another song plays is ready by the time it ends. (fetch() never goes past
# prefetch=1, so this keeps exactly one song ready.)
thread.run(every=2., fun () -> if list.length(requests.queue()) < 2 then ignore(requests.fetch()) end)

library = playlist(id="library", mode="randomize", reload_mode="never", "/aux-data/fallback.m3u")

# track_sensitive: a newly shared song starts when the current one ends.
radio = fallback(id="radio", track_sensitive=true, [requests, library])

radio.on_track(synchronous=false, fun (m) -> begin
  uri = if m["initial_uri"] != "" then m["initial_uri"] else m["filename"] end
  ignore(http.post(headers=auth, data=uri, "#{web}/internal/track"))
end)

server.register("qlen", fun (_) -> string(list.length(requests.queue())))
server.register("skip", fun (_) -> begin radio.skip() "ok" end)
server.register("restart", fun (_) -> begin shutdown() "ok" end)
# Drop the prefetched song so a reorder / play-next in aux-web takes effect.
server.register("refetch", fun (_) -> begin
  requests.set_queue([])
  ignore(requests.fetch())
  "ok"
end)

# Even out loud and quiet songs (they come from many sources), then a short
# crossfade between songs. Both can be switched off from the Aux page.
# mksafe first (silence only if nothing at all can play), so the crossfade
# always has something to work on. The volume levelling goes after the
# crossfade: inside it, it made the crossfade drop out many times a second.
out = mksafe(radio)
out = crossfade(duration={if crossfade_on() then fade_secs else 0.1 end}, fade_in=fade_secs / 2., fade_out=fade_secs / 2., out)
out = normalize(enabled=even_volume, target=-14., out)

output.harbor(
  %mp3(bitrate=192, samplerate=44100, stereo=true),
  id="aux", port=8141, mount="aux.mp3", format="audio/mpeg",
  mksafe(out)
)

# A fresh radio has nothing loaded: tell aux-web to hand those songs out again
# (and to re-send the volume / crossfade / empty-queue settings).
ignore(http.post(headers=auth, data="", "#{web}/internal/reset"))
