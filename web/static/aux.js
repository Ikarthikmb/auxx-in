/* Aux page: Now (Song · Lyrics · Up next) · Search · Library · Downloads · Settings. Plain JS, no build. */
(function(){
  'use strict';
  var S = null;             // /api/state
  var clockSkew = 0;        // server time - local time (seconds)
  var lyrics = null, lyricsFor = null;
  var cache = {};           // per-view data (search results, library pages)

  // ------------------------------------------------------------------ helpers
  function $(id){ return document.getElementById(id); }
  function el(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function bg(e, url){ if (!e) return e; e.style.backgroundImage = url ? 'url("' + url + '")' : ''; return e; }
  function btn(label, cls, fn, aria){ var b = el('button', cls || '', label); b.type = 'button'; if (aria) b.setAttribute('aria-label', aria); b.onclick = fn; return b; }
  var ME = null, CSRF = '';  // who's logged in (/api/me) and the CSRF token sent with every change
  function api(path, opts){
    opts = Object.assign({cache:'no-store'}, opts || {});
    if (opts.method && opts.method !== 'GET') opts.headers = Object.assign({}, opts.headers || {}, {'X-CSRFToken':CSRF});
    return fetch('api/' + path, opts).then(function(r){
      if (r.status === 401){ location.replace('login'); throw new Error('login'); }

      return r.json().catch(function(){ return {}; }).then(function(d){ if (!r.ok && !d.say && !d.error) throw new Error(r.status); return d; });
    });
  }
  function post(path, data){ return api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data || {})}); }
  var toastTimer;
  function toast(text, err){
    var t = document.querySelector('.toast'); if (!t){ t = el('div', 'toast'); document.body.appendChild(t); }
    t.className = 'toast' + (err ? ' err' : ''); t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function(){ t.hidden = true; }, 3200);
  }
  function done(d){ if (d && (d.say || d.error)) toast(d.say || d.error, d.ok === false || !!d.error); refresh(); return d; }
  function fail(){ toast('That didn\'t work.', true); }
  function mmss(s){ if (s == null || isNaN(s)) return '–:––'; s = Math.max(0, Math.floor(s)); return Math.floor(s / 60) + ':' + ('0' + s % 60).slice(-2); }
  function sub(){ return Array.prototype.filter.call(arguments, Boolean).join(' · '); }
  function nowSecs(){ return Date.now() / 1000 + clockSkew; }
  function elapsed(){
    if (deviceMode() && P.item && S && S.now && S.now.id === P.item.id) return $('audio').currentTime;
    return S && S.now && S.now.started ? nowSecs() - S.now.started : null;
  }
  function deviceMode(){ return !!S && S.play_on === 'device'; }
  function libCover(id, size){ return id ? 'api/lib/cover/' + encodeURIComponent(id) + (size ? '?size=' + size : '') : null; }
  function size(b){ return b == null ? '' : (b / 1048576).toFixed(1) + ' MB'; }
  // Icons (inline SVG, they follow the text colour).
  var ICONS = {
    play:'<path d="M8 5.6v12.8a1 1 0 0 0 1.52.85l10.2-6.4a1 1 0 0 0 0-1.7L9.52 4.75A1 1 0 0 0 8 5.6z"/>',
    pause:'<rect x="6.5" y="5" width="4" height="14" rx="1.3"/><rect x="13.5" y="5" width="4" height="14" rx="1.3"/>',
    prev:'<rect x="4.5" y="5" width="2.6" height="14" rx="1.2"/><path d="M19.5 6.3v11.4a1 1 0 0 1-1.56.83l-8.4-5.7a1 1 0 0 1 0-1.66l8.4-5.7a1 1 0 0 1 1.56.83z"/>',
    next:'<rect x="16.9" y="5" width="2.6" height="14" rx="1.2"/><path d="M4.5 6.3v11.4a1 1 0 0 0 1.56.83l8.4-5.7a1 1 0 0 0 0-1.66l-8.4-5.7A1 1 0 0 0 4.5 6.3z"/>',
    stop:'<rect x="6.5" y="6.5" width="11" height="11" rx="2.2"/>',
    power:'<g fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 3.8v7.4"/><path d="M7.2 6.6a7 7 0 1 0 9.6 0"/></g>',
    phone:'<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="7" y="2.8" width="10" height="18.4" rx="2.6"/><path d="M11 18h2"/></g>',
    radio:'<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="8.5" width="18" height="12" rx="2.6"/><circle cx="15.5" cy="14.5" r="2.5"/><path d="M6.6 12.8h4.2M6.6 16.2h4.2M7.5 8.5l9-4.8"/></g>',
    up:'<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10.5v10H4.6a1.6 1.6 0 0 1-1.6-1.6v-6.8a1.6 1.6 0 0 1 1.6-1.6H7l3.6-7.2a2.6 2.6 0 0 1 2.9 2.9l-.6 3.9h5.4a2 2 0 0 1 2 2.3l-1.2 7a2 2 0 0 1-2 1.7H7"/></g>',
    down:'<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 13.5v-10h2.4A1.6 1.6 0 0 1 21 5.1v6.8a1.6 1.6 0 0 1-1.6 1.6H17l-3.6 7.2a2.6 2.6 0 0 1-2.9-2.9l.6-3.9H5.7a2 2 0 0 1-2-2.3l1.2-7a2 2 0 0 1 2-1.7H17"/></g>',
    spark:'<path d="M12 2.6l2.1 6.2 6.3 2.2-6.3 2.2L12 19.4l-2.1-6.2L3.6 11l6.3-2.2z"/><path d="M19 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>'
  };
  function icon(name, label){
    return '<svg class="ico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' + ICONS[name] + '</svg>' + (label ? '<span>' + label + '</span>' : '');
  }
  function iconBtn(name, cls, fn, aria, label){ var b = btn('', cls, fn, aria); b.innerHTML = icon(name, label); return b; }
  function setIcon(b, name, label){ if (b && b.dataset.icon !== name + (label || '')){ b.dataset.icon = name + (label || ''); b.innerHTML = icon(name, label); } }
  function ytUrl(id){ return 'https://music.youtube.com/watch?v=' + id; }

  // ------------------------------------------------------------------ sheet (actions + preview)
  function sheet(o){
    closeSheet();
    var shade = el('div', 'shade'); shade.id = 'shade';
    var sh = el('div', 'sheet'); shade.appendChild(sh);
    shade.onclick = function(e){ if (e.target === shade) closeSheet(); };
    if (o.video) sh.appendChild(previewBox(o.video, o.cover, true));
    var head = el('div', 'head');
    if (!o.video){ head.appendChild(bg(el('div', 'cv'), o.cover)); }
    var tx = el('div'); tx.style.minWidth = '0'; tx.appendChild(el('div', 't', o.title || '')); tx.appendChild(el('div', 's', o.sub || ''));
    head.appendChild(tx); sh.appendChild(head);
    var opts = el('div', 'opts');
    (o.options || []).forEach(function(op){
      if (!op) return;
      opts.appendChild(btn(op[0], '', function(){ var keep = op[2]; if (!keep) closeSheet(); op[1](); }));
    });
    opts.appendChild(btn('Close', 'ghost', closeSheet));
    sh.appendChild(opts);
    document.body.appendChild(shade);
  }
  function closeSheet(){ var s = $('shade'); if (s){ s.querySelectorAll('audio').forEach(function(a){ a.pause(); a.removeAttribute('src'); a.load(); }); s.remove(); } }
  // Preview: the video's audio through the Aux server, over its thumbnail.
  function previewBox(video, cover, autoplay){
    var box = el('div', 'pvbox');
    bg(box, 'https://i.ytimg.com/vi/' + video + '/hqdefault.jpg');
    var a = document.createElement('audio'); a.controls = true; a.preload = 'none';
    a.src = 'api/preview/' + video;
    var note = el('div', 'pvnote', 'Preview · loading…');
    a.addEventListener('playing', function(){ note.textContent = 'Preview'; });
    a.addEventListener('error', function(){ note.textContent = 'Preview not available for this one'; });
    var yt = el('a', 'pvlink', 'Open in YouTube ↗'); yt.href = 'https://www.youtube.com/watch?v=' + video; yt.target = '_blank'; yt.rel = 'noopener';
    var bar = el('div', 'pvbar'); bar.appendChild(note); bar.appendChild(yt);
    box.appendChild(bar); box.appendChild(a);
    if (autoplay) setTimeout(function(){ a.play().catch(function(){ note.textContent = 'Tap ▶ to preview'; }); }, 50);
    return box;
  }
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') closeSheet(); });

  // Options for a song that isn't in the library yet (YouTube Music / YouTube / a link).
  function onlineOptions(url, exact, label){
    function add(where){ return function(){ toast(where === 'save' ? 'Saving…' : 'Adding…'); post('add', {q:url, where:where, exact:exact}).then(done, fail); }; }
    return [
      ['⤴  Play next', add('next')],
      ['＋  Add to queue', add('end')],
      ['▶  Play now', add('now')],
      ['♡  Save to library (don\'t queue)', add('save')],
      ['⬇  Download MP3', function(){
        toast('Getting the MP3…');
        post('add', {q:url, where:'save', exact:exact}).then(function(d){
          var it = d.items && d.items[0];
          if (d.ok && it && it.status === 'ready'){ location.href = 'api/file/' + it.id; refresh(); }
          else done(d);
        }, fail);
      }]
    ];
  }
  function openOnline(r, src){
    sheet({video:r.video_id, title:r.title, sub:sub(r.artist, r.album, r.duration),
           options:onlineOptions(src === 'yt' ? 'https://www.youtube.com/watch?v=' + r.video_id : ytUrl(r.video_id), src === 'yt')});
  }
  // Options for a Navidrome song.
  function libQueue(body, where, extra){ return function(){ post('lib/queue', Object.assign({where:where}, body, extra || {})).then(done, fail); }; }
  function openLibSong(s){
    sheet({cover:libCover(s.cover_id, 120), title:s.title, sub:sub(s.artist, s.album, mmss(s.duration)), options:[
      ['⤴  Play next', libQueue({ids:[s.nd_id]}, 'next')],
      ['＋  Add to queue', libQueue({ids:[s.nd_id]}, 'end')],
      ['▶  Play now', libQueue({ids:[s.nd_id]}, 'now')],
      [s.starred ? '♥  Remove from favorites' : '♡  Add to favorites', function(){
        post('lib/star', {id:s.nd_id, on:!s.starred}).then(function(){ s.starred = !s.starred; toast(s.starred ? 'Added to favorites.' : 'Removed from favorites.'); });
      }],
      ['⬇  Download', function(){ location.href = 'api/lib/download/' + encodeURIComponent(s.nd_id); }],
      s.album_id ? ['◎  Go to album', function(){ location.hash = '#library/album/' + s.album_id; }] : null
    ]});
  }
  // Header buttons for an album / playlist / artist.
  function collectionButtons(body){
    var row = el('div', 'ctrls'); row.style.justifyContent = 'flex-start';
    row.appendChild(btn('▶ Play now', 'primary', libQueue(body, 'now')));
    row.appendChild(btn('⤴ Play next', '', libQueue(body, 'next')));
    row.appendChild(btn('＋ Add to queue', '', libQueue(body, 'end')));
    row.appendChild(btn('⤮ Shuffle into queue', '', libQueue(body, 'end', {shuffle:true})));
    return row;
  }

  // ------------------------------------------------------------------ rows
  function songRow(o){
    var r = el('div', 'row');
    r.appendChild(bg(el('div', 'cv'), o.cover));
    var tx = el('div', 'tx');
    var t = el('div', 't', o.title || '…');
    if (o.badge) t.appendChild(el('span', 'badge ' + (o.badgeCls || ''), o.badge));
    tx.appendChild(t);
    tx.appendChild(el('div', 's', o.sub || ''));
    if (o.progress != null){ var p = el('div', 'prog'); var i = el('i'); i.style.width = o.progress + '%'; p.appendChild(i); tx.appendChild(p); }
    if (o.onTap) tx.onclick = o.onTap;
    r.appendChild(tx);
    var a = el('div', 'acts');
    (o.buttons || []).forEach(function(b){ if (b) a.appendChild(b); });
    r.appendChild(a);
    return r;
  }
  function libSongRow(s){
    return songRow({cover:libCover(s.cover_id, 120), title:s.title, sub:sub(s.artist, s.album, mmss(s.duration)),
      onTap:function(){ openLibSong(s); },
      buttons:[btn('＋', 'icon ghost', libQueue({ids:[s.nd_id]}, 'end'), 'Add to queue'),
               btn('⋯', 'icon ghost', function(){ openLibSong(s); }, 'More')]});
  }
  function tile(cover, title, subtext, href){
    var t = el('div', 'tile');
    t.appendChild(bg(el('div', 'cv'), cover));
    t.appendChild(el('div', 't', title || ''));
    t.appendChild(el('div', 's', subtext || ''));
    t.onclick = function(){ location.hash = href; };
    return t;
  }
  function albumTile(a){ return tile(libCover(a.cover_id, 300), a.name, sub(a.artist, a.year), '#library/album/' + a.id); }

  // ------------------------------------------------------------------ state + mini player
  function refresh(){
    return api('state').then(function(d){
      S = d; clockSkew = d.server_time - Date.now() / 1000;
      if (deviceMode() && P.item && (!S.now || S.now.id !== P.item.id)) S.now = Object.assign({}, P.item, {started:nowSecs() - audio.currentTime});
      devRestore();
      handleCommands();
      if (deviceMode() && S.sleep_at && nowSecs() >= S.sleep_at && !audio.paused){ audio.pause(); post('sleep', {minutes:0}); toast('Sleep timer: paused.'); }
      paintChrome();
      var t = route().tab;
      if (t === 'now'){ paintNow(); renderQueue(); }
      if (t === 'settings') paintSettings();
    }).catch(function(){});
  }
  function paintChrome(){
    var n = S.now;
    document.querySelectorAll('[data-radio-dot]').forEach(function(d){ d.className = 'dot' + (S.radio_on ? ' on' : ''); });
    var lt = document.querySelector('.tabbar [data-tab="library"]'); if (lt) lt.hidden = S.navidrome === false;
    bg($('miniCover'), n && n.cover);
    var idle = deviceMode() && !P.item;
    $('miniTitle').textContent = idle ? 'Tap ▶ to play' : n ? n.title : (S.radio_on ? 'Starting…' : 'Radio is off');
    $('miniSub').textContent = idle ? (S.queue.length ? S.queue.length + ' in the queue' : 'Library shuffle') :
      n ? sub(n.artist, deviceMode() ? 'this device' : 'Auxx-In radio') : '';
    var q = S.queue.filter(function(i){ return i.status !== 'failed'; }).length;
    var busy = S.queue.concat(S.jobs).filter(function(i){ return /pending|resolving|downloading/.test(i.status); }).length;
    document.querySelectorAll('[data-qcount]').forEach(function(b){ b.textContent = q; b.hidden = !q; });
    document.querySelectorAll('[data-dcount]').forEach(function(b){ b.textContent = busy; b.hidden = !busy; });
    setIcon($('miniListen'), deviceMode() ? (listening() ? 'pause' : 'play') : (listening() ? 'stop' : 'play'));
    $('miniListen').setAttribute('aria-label', deviceMode() ? 'Play or pause' : 'Listen here');
    updateMediaSession();
  }
  setInterval(function(){
    var e = elapsed(), d = S && S.now && S.now.duration;
    $('miniBar').style.width = e != null && d ? Math.min(100, e / d * 100) + '%' : '0';
    if (route().tab === 'now') tickNow();
    var h = $('sleepHint'); if (h && S) h.textContent = S.sleep_at ? 'Stops in ' + mmss(S.sleep_at - nowSecs()) : 'Stop playing after a while';
  }, 1000);
  $('miniText').onclick = function(){ location.hash = '#now'; };
  $('miniSkip').onclick = skip;
  $('miniListen').onclick = toggleListen;
  function skip(){
    if (deviceMode()){ devNext(); return; }
    post('skip').then(function(d){ toast(d.say, !d.ok); setTimeout(refresh, 1500); }, fail);
  }
  // Previous, in either player (the server keeps what was played): more than
  // 5 s in, the song starts again; otherwise the one before plays, then this one.
  function prev(){
    if (deviceMode()){
      if (P.item && audio.currentTime > 5){ audio.currentTime = 0; return; }
      post('previous', {position:P.item ? audio.currentTime : 0}).then(function(d){
        if (d.ok) devNext(); else toast(d.say || 'Nothing to go back to.', true);
      }, fail);
      return;
    }
    post('previous').then(function(d){ toast(d.say, !d.ok); setTimeout(refresh, 1500); }, fail);
  }

  // ---------------- This device: the page plays the queue itself.
  var P = {item:null, waiting:false, lastCmd:null, reportAt:0, peek:null, peeking:false, posAt:0, bgAt:0};
  var audio = $('audio');
  function devPlay(item){
    P.item = item; P.waiting = false; P.peek = null; P.peeking = false;
    if (FX.ctx && fadeSecs() && !item.position){ FX.state = 'start'; fxSet(0.0001); }
    audio.src = item.url;
    if (item.position){
      var at = item.position;
      audio.addEventListener('loadedmetadata', function f(){ audio.removeEventListener('loadedmetadata', f); try { audio.currentTime = at; } catch (e) {} });
    }
    audio.play().catch(function(){ toast('Tap ▶ to start.', true); });
    if (S){ S.now = Object.assign({}, item, {started:nowSecs() - (item.position || 0)}); paintChrome(); if (route().tab === 'now') paintNow(); }
    report(true);
  }
  // A locked or backgrounded phone stops running the page soon after a song
  // ends, so the next song is asked for near the end of this one (peek) and
  // started inside 'ended' itself, before any network call.
  function devPeek(){
    if (!deviceMode() || !P.item || P.peek || P.peeking || !isFinite(audio.duration)) return;
    if (audio.duration - audio.currentTime > 15) return;
    var cur = P.item; P.peeking = true;
    post('device/next', {peek:true}).then(function(d){ if (P.item === cur){ P.peeking = false; if (d.item) P.peek = d.item; } },
      function(){ if (P.item === cur) P.peeking = false; });
  }
  function devNext(usePeek){
    if (!deviceMode()) return;
    var pk = usePeek ? P.peek : null;
    if (pk) devPlay(pk);
    post('device/next', pk ? {id:pk.id} : {}).then(function(d){
      if (d.item && pk && d.item.id === pk.id && P.item === pk){ report(true); return; }
      if (d.item){ devPlay(d.item); return; }
      if (d.wait){ P.waiting = true; toast(d.say || 'Getting the next song…'); setTimeout(devNext, 3000); return; }
      if (d.empty){ audio.pause(); toast('Queue is empty.'); }
    }, function(){ setTimeout(devNext, 3000); });
  }
  // A reopened page picks up the song it was playing, paused where it left
  // off (the phone won't start sound without a tap), instead of moving on.
  var restored = false;
  function devRestore(){
    if (restored) return;
    restored = true;
    var n = S.now;
    if (!deviceMode() || P.item || !n || !(n.path || n.nd_id)) return;
    var pos = n.position != null ? n.position : (n.started ? nowSecs() - n.started : 0);
    if (n.duration && pos > n.duration - 3) return;  // it had finished: ▶ plays the next one
    P.item = Object.assign({}, n, {url:'api/stream/' + encodeURIComponent(n.id)});
    audio.src = P.item.url; audio.preload = 'metadata';
    audio.addEventListener('loadedmetadata', function f(){ audio.removeEventListener('loadedmetadata', f); try { audio.currentTime = Math.max(0, pos); } catch (e) {} });
    audio.load();
    n.started = nowSecs() - pos;
  }
  function devToggle(){
    fxInit();
    if (!P.item){ devNext(); return; }
    if (audio.paused) audio.play(); else audio.pause();
  }
  function report(force){
    if (!deviceMode() || !P.item) return;
    var t = Date.now();
    if (!force && t - P.reportAt < 5000) return;
    P.reportAt = t;
    post('device/now', {id:P.item.id, position:audio.currentTime || 0, paused:audio.paused, duration:isFinite(audio.duration) ? audio.duration : null});
  }
  audio.addEventListener('ended', function(){ if (deviceMode() && P.item) devNext(true); });
  audio.addEventListener('error', function(){ if (deviceMode() && P.item && audio.src){ toast('Couldn\'t play that one, skipping.', true); setTimeout(devNext, 1500); } });
  ['play', 'pause', 'seeked'].forEach(function(ev){ audio.addEventListener(ev, function(){ report(true); paintChrome(); if (route().tab === 'now') paintNow(); }); });
  audio.addEventListener('timeupdate', function(){
    report(false); devPeek(); fadeTick();
    var t = Date.now();
    if (t - P.posAt > 1000){ P.posAt = t; positionState(); }
    // A locked phone doesn't run the 3 s refresh: the sleep timer and skips
    // from other screens are checked here instead, while a song plays.
    if (document.hidden && t - P.bgAt > 10000){ P.bgAt = t; refresh(); }
  });
  function positionState(){
    if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState || !deviceMode() || !P.item || !isFinite(audio.duration)) return;
    try { navigator.mediaSession.setPositionState({duration:audio.duration, position:Math.min(audio.currentTime, audio.duration), playbackRate:1}); } catch (e) {}
  }

  // Fade between songs (Settings → Fade between songs). Phones ignore an
  // audio element's volume, so the sound goes through Web Audio for this; it's
  // only set up once a fade is chosen, and only from a tap.
  var FX = {ctx:null, gain:null, state:''};
  function fadeSecs(){ return (S && S.settings && S.settings.fade) || 0; }
  function fxInit(){
    if (FX.ctx || !fadeSecs()) return;
    try {
      var C = window.AudioContext || window.webkitAudioContext;
      FX.ctx = new C();
      FX.gain = FX.ctx.createGain();
      FX.ctx.createMediaElementSource(audio).connect(FX.gain);
      FX.gain.connect(FX.ctx.destination);
    } catch (e) { FX.ctx = null; }
  }
  function fxSet(v, secs){
    if (!FX.ctx) return;
    var g = FX.gain.gain, now = FX.ctx.currentTime;
    g.cancelScheduledValues(now); g.setValueAtTime(g.value, now);
    if (secs) g.linearRampToValueAtTime(v, now + secs); else g.setValueAtTime(v, now);
  }
  function fadeTick(){
    if (!FX.ctx) return;
    if (FX.ctx.state !== 'running') FX.ctx.resume();
    var f = fadeSecs(), t = audio.currentTime, d = audio.duration;
    if (!f || !deviceMode() || !isFinite(d)){ if (FX.state){ FX.state = ''; fxSet(1); } return; }
    if (d - t <= f && d > f * 3){ if (FX.state !== 'out'){ FX.state = 'out'; fxSet(0.0001, Math.max(0.2, d - t)); } }
    else if (t < f && d > f * 3){ if (FX.state !== 'in'){ FX.state = 'in'; fxSet(Math.max(0.0001, t / f)); fxSet(1, f - t); } }
    else if (FX.state){ FX.state = ''; fxSet(1); }
  }
  audio.addEventListener('seeked', function(){ if (FX.ctx){ FX.state = ''; fxSet(1); fadeTick(); } });
  audio.addEventListener('play', function(){ if (FX.ctx && FX.ctx.state !== 'running') FX.ctx.resume(); });
  function handleCommands(){
    // Skip / play-now from another screen or another device.
    var c = S.device_cmd;
    if (P.lastCmd === null){ P.lastCmd = c ? c.at : 0; return; }  // anything already there is older than this page
    if (c && c.at > P.lastCmd){
      P.lastCmd = c.at;
      post('device/now', {ack:true});
      if (deviceMode() && (P.item || !audio.paused)) devNext();
    }
  }
  // Switching players hands the song over: the other player carries on with
  // the same song, from about the same spot.
  function setPlayOn(mode){
    var body = {play_on:mode}, handoff = null;
    if (mode === 'radio'){
      body.playing = !!P.item;
      body.position = P.item ? audio.currentTime : 0;
    } else if (S && S.radio_on && S.now && (S.now.path || S.now.nd_id)){
      var pos = elapsed() || 0, dur = S.now.duration;
      if (!dur || pos < dur - 3) handoff = Object.assign({}, S.now, {url:'api/stream/' + encodeURIComponent(S.now.id), position:pos});
    }
    audio.pause(); audio.removeAttribute('src'); audio.load(); P.item = null;
    if (handoff){ S.play_on = 'device'; devPlay(handoff); }  // in the tap itself, so the phone lets it play
    toast(mode === 'radio' ? 'Switching to the Auxx-In radio…' : 'Switching to this device…');
    post('settings', body).then(function(){
      toast(mode === 'radio' ? 'Playing on the Auxx-In radio.' : handoff ? 'Playing on this device.' : 'Playing on this device. Tap ▶ to start.');
      refresh().then(function(){ if (route().tab === 'now') render(); });
    }, fail);
  }

  // Listening in this page: the radio stream, with lock-screen controls.
  function listening(){ var a = $('audio'); return !!a.src && !a.paused; }
  function toggleListen(){
    if (deviceMode()){ devToggle(); setTimeout(paintChrome, 200); return; }
    var a = $('audio');
    if (listening()){ a.pause(); a.removeAttribute('src'); a.load(); }
    else {
      if (!S.radio_on){ toast('The radio is off. Turn it on in Settings.', true); return; }
      a.src = S.stream + '?t=' + Date.now(); a.play().catch(function(){ toast('Tap again to start.', true); });
    }
    setTimeout(paintChrome, 300);
  }

  function updateMediaSession(){
    if (!('mediaSession' in navigator) || !S || !S.now) return;
    var n = S.now, art = n.cover ? [{src:new URL(n.cover, location.href).href, sizes:'300x300'}] : [];
    try {
      navigator.mediaSession.metadata = new MediaMetadata({title:n.title || 'Auxx-In', artist:n.artist || '', album:n.album || 'Auxx-In radio', artwork:art});
      navigator.mediaSession.setActionHandler('nexttrack', skip);
      navigator.mediaSession.setActionHandler('pause', function(){ toggleListen(); });
      navigator.mediaSession.setActionHandler('play', function(){ toggleListen(); });
      navigator.mediaSession.setActionHandler('previoustrack', prev);
      navigator.mediaSession.setActionHandler('seekto', deviceMode() ? function(e){ audio.currentTime = e.seekTime; } : null);
      if (deviceMode() && P.item && isFinite(audio.duration))
        navigator.mediaSession.setPositionState({duration:audio.duration, position:Math.min(audio.currentTime, audio.duration), playbackRate:1});
    } catch (e) {}
  }

  // ------------------------------------------------------------------ router
  function route(){
    var h = (location.hash || '#now').slice(1).split('/');
    return {tab:h[0] || 'now', rest:h.slice(1)};
  }
  var renderers = {};
  function render(){
    var r = route();
    if (!renderers[r.tab]) r.tab = 'now';
    document.body.dataset.tab = r.tab;
    document.querySelectorAll('[data-tab]').forEach(function(a){ a.classList.toggle('cur', a.dataset.tab === r.tab); });
    closeSheet();
    qBusy = false;
    var v = $('view'); v.textContent = '';
    renderers[r.tab](v, r.rest);
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', render);
  function topbar(v, title, backHash, right){
    var tb = el('div', 'topbar');
    if (backHash) tb.appendChild(btn('‹', 'icon ghost back', function(){ history.length > 1 ? history.back() : (location.hash = backHash); }, 'Back'));
    tb.appendChild(el('h1', '', title));
    (right || []).forEach(function(b){ tb.appendChild(b); });
    v.appendChild(tb);
  }

  // ------------------------------------------------------------------ Now: Song · Lyrics · Up next
  var NOW_VIEWS = [['song', 'Song'], ['lyrics', 'Lyrics'], ['next', 'Up next']];
  var nowView = 'song';
  try { if (NOW_VIEWS.some(function(o){ return o[0] === localStorage.getItem('aux.nowView'); })) nowView = localStorage.getItem('aux.nowView'); } catch (e) {}
  function setNowView(name){
    nowView = name;
    try { localStorage.setItem('aux.nowView', name); } catch (e) {}
    render();
  }
  function swipeViews(target){
    var x0 = null, y0 = 0;
    target.addEventListener('touchstart', function(e){ x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, {passive:true});
    target.addEventListener('touchend', function(e){
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
      if (Math.abs(dx) < 60 || Math.abs(dy) > Math.abs(dx) * 0.6) return;
      var i = NOW_VIEWS.findIndex(function(o){ return o[0] === nowView; }) + (dx < 0 ? 1 : -1);
      if (i >= 0 && i < NOW_VIEWS.length) setNowView(NOW_VIEWS[i][0]);
    });
  }
  renderers.now = function(v, rest){
    if (rest && rest[0] && NOW_VIEWS.some(function(o){ return o[0] === rest[0]; })){
      nowView = rest[0];
      try { localStorage.setItem('aux.nowView', nowView); } catch (e) {}
      history.replaceState(null, '', '#now');
    }
    var tb = el('div', 'topbar nowbar');
    var seg = el('div', 'seg'); seg.setAttribute('role', 'tablist');
    NOW_VIEWS.forEach(function(o){
      var b = btn(o[1], nowView === o[0] ? 'cur' : '', function(){ if (nowView !== o[0]) setNowView(o[0]); });
      b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', nowView === o[0] ? 'true' : 'false');
      seg.appendChild(b);
    });
    tb.appendChild(seg);
    var dot = el('span', 'dot'); dot.setAttribute('data-radio-dot', ''); tb.appendChild(dot);
    v.appendChild(tb);

    var art = el('div', 'now-art'); art.id = 'nowArt';
    art.setAttribute('role', 'button'); art.tabIndex = 0; art.setAttribute('aria-label', 'Show the cover full screen');
    art.onclick = function(){ openFull('cover'); }; art.onkeydown = function(e){ if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); openFull('cover'); } };
    var tag = el('div', 'now-tag'); tag.id = 'nowTag';
    var t = el('div', 'now-title'); t.id = 'nowTitle';
    var s = el('div', 'now-sub'); s.id = 'nowSub';
    var stage;
    if (nowView === 'song'){
      stage = el('div', 'now-stage'); stage.appendChild(art); v.appendChild(stage);
      v.appendChild(t); v.appendChild(s); v.appendChild(tag);
    } else if (nowView === 'lyrics'){
      stage = el('div', 'now-stage');
      var ly = el('div', 'lyrics big'); ly.id = 'lyrics'; stage.appendChild(ly); v.appendChild(stage);
      ly.title = 'Tap for full screen'; ly.onclick = function(){ openFull('lyrics'); };
      v.appendChild(t); v.appendChild(s);
      if (lyrics && S && S.now && lyricsFor === S.now.id) paintLyrics(); else lyricsFor = undefined;
    } else {
      stage = el('div', 'now-head');
      art.className = 'now-art sm'; stage.appendChild(art);
      var tx = el('div', 'tx'); tx.appendChild(t); tx.appendChild(s); tx.appendChild(tag); stage.appendChild(tx);
      v.appendChild(stage);
    }
    swipeViews(stage);

    var dev = deviceMode();
    var timeb = el('div', 'timebar');
    timeb.innerHTML = '<span id="nowEl">0:00</span>' + (dev ? '<input type="range" id="nowSeek" min="0" max="1000" value="0" aria-label="Position">' :
      '<div class="prog"><i id="nowBar"></i></div>') + '<span id="nowDur">–:––</span>';
    v.appendChild(timeb);
    var c = el('div', 'ctrls');
    c.appendChild(iconBtn('prev', 'tbtn', prev, 'Previous'));
    var pb = iconBtn('play', 'tbtn play', dev ? devToggle : function(){
      if (!S.radio_on){ post('radio', {on:true}).then(done, fail); return; }
      toggleListen();
    }, dev ? 'Play or pause' : 'Listen here');
    pb.id = 'nowPlay'; c.appendChild(pb);
    c.appendChild(iconBtn('next', 'tbtn', skip, 'Next'));
    v.appendChild(c);
    v.appendChild(iqRow());
    if (!dev){ var cap = el('div', 'now-cap'); cap.id = 'nowCap'; v.appendChild(cap); }
    var po = el('div', 'seg playon'); po.setAttribute('role', 'radiogroup'); po.setAttribute('aria-label', 'Play on');
    var b1 = iconBtn('phone', dev ? 'cur' : '', function(){ if (!deviceMode()) setPlayOn('device'); }, null, 'This device');
    var b2 = iconBtn('radio', dev ? '' : 'cur', function(){ if (deviceMode()) setPlayOn('radio'); }, null, 'Auxx-In radio');
    [b1, b2].forEach(function(b, i){ b.setAttribute('role', 'radio'); b.setAttribute('aria-checked', (i === 0) === dev ? 'true' : 'false'); po.appendChild(b); });
    var pw = el('div', 'playon-wrap'); pw.appendChild(po); v.appendChild(pw);
    if (dev){
      var seek = v.querySelector('#nowSeek');
      seek.addEventListener('input', function(){ if (isFinite(audio.duration)) $('nowEl').textContent = mmss(seek.value / 1000 * audio.duration); });
      seek.addEventListener('change', function(){ if (isFinite(audio.duration)) audio.currentTime = seek.value / 1000 * audio.duration; seek.dataset.drag = ''; });
      seek.dataset.drag = '';
      seek.addEventListener('pointerdown', function(){ seek.dataset.drag = '1'; });
      seek.addEventListener('pointerup', function(){ seek.dataset.drag = ''; });
    }
    if (nowView === 'next'){
      var qh = el('h2'); qh.appendChild(document.createTextNode('Up next')); qh.appendChild(el('span', 'sp'));
      qh.appendChild(btn('⤮ Shuffle', 'sm', function(){ post('queue/shuffle').then(refresh); }));
      qh.appendChild(btn('Clear', 'sm', function(){ if (confirm('Clear the whole queue? (Songs stay in your library.)')) post('queue/clear').then(refresh); }));
      v.appendChild(qh);
      var box = el('div'); box.id = 'queueBox'; v.appendChild(box);
      renderQueue();
    } else {
      var nx = el('button', 'upnext ghost'); nx.type = 'button'; nx.id = 'nowNext'; nx.onclick = function(){ setNowView('next'); };
      v.appendChild(nx);
    }
    paintNow();
  };
  // ------------------------------------------------------------------ Full screen: cover and lyrics
  // One full-screen view, two modes. Cover: the cover big over a blurred copy
  // of it, title, progress, controls. Lyrics: the lyrics over the blurred
  // cover, the current line lit; on a wide screen a small card (cover, title,
  // progress, controls) sits bottom-left, on a phone only the lyrics show.
  // Tap the cover on Now (cover) or the lyrics on Now → Lyrics (lyrics); the
  // top-right button switches. Controls fade after 5 s untouched; the screen
  // stays awake. ✕ or Esc closes; swipe down closes the cover mode.
  var full = null, fullMode = 'cover', fullIdle = null, wake = null;
  var fullLyrics = null, fullLyricsFor = null, fullCur = -2, fullUserScroll = 0;
  function bigCover(u){ return u && u.indexOf('api/lib/cover/') === 0 ? u + '?size=1200' : u; }
  function fullControls(){
    var c = el('div', 'ctrls');
    c.appendChild(iconBtn('prev', 'tbtn', prev, 'Previous'));
    c.appendChild(iconBtn('play', 'tbtn play fplay', function(){
      if (!deviceMode() && !S.radio_on){ post('radio', {on:true}).then(done, fail); return; }
      toggleListen();
    }, 'Play or pause'));
    c.appendChild(iconBtn('next', 'tbtn', skip, 'Next'));
    return c;
  }
  function fullProg(){ var p = el('div', 'prog'); p.appendChild(el('i', 'fbar')); return p; }
  function openFull(mode){
    if (!S || !S.now) return;
    if (full){ setFullMode(mode || fullMode); return; }
    full = el('div', 'full'); full.setAttribute('role', 'dialog'); full.setAttribute('aria-label', 'Now playing, full screen');
    full.appendChild(el('div', 'full-bg')).id = 'fullBg';
    var top = el('div', 'full-top hide-idle');
    top.appendChild(btn('✕', 'icon', closeFull, 'Close'));
    top.appendChild(el('div', 'full-head')).id = 'fullHead';
    var sw = btn('', 'sm'); sw.id = 'fullSwitch';
    sw.onclick = function(){ setFullMode(fullMode === 'cover' ? 'lyrics' : 'cover'); };
    top.appendChild(sw); full.appendChild(top);
    // Cover mode
    var cv = el('div', 'full-cover');
    cv.appendChild(el('div', 'full-art')).id = 'fullArt';
    cv.appendChild(el('div', 'full-title ftitle'));
    cv.appendChild(el('div', 'full-sub fsub'));
    cv.appendChild(fullProg());
    cv.appendChild(fullControls());
    full.appendChild(cv);
    // Lyrics mode
    var ly = el('div', 'full-lyrics'); ly.id = 'fullLyrics';
    ['wheel', 'touchmove'].forEach(function(t){ ly.addEventListener(t, function(){ fullUserScroll = Date.now(); }, {passive:true}); });
    full.appendChild(ly);
    var card = el('div', 'full-card hide-idle');  // fades with the other controls after 5 s untouched
    var head = el('div', 'fc-head');
    head.appendChild(el('div', 'fc-art')).id = 'fullThumb';
    var tx = el('div', 'fc-tx'); tx.appendChild(el('div', 'ftitle')); tx.appendChild(el('div', 'fsub'));
    head.appendChild(tx); head.appendChild(fullControls()); card.appendChild(head);
    card.appendChild(fullProg());
    full.appendChild(card);
    // Swipe down closes the cover mode (in lyrics mode a swipe scrolls them).
    var y0 = null;
    full.addEventListener('pointerdown', function(e){ y0 = fullMode === 'cover' ? e.clientY : null; wakeUp(); });
    full.addEventListener('pointerup', function(e){ if (y0 != null && e.clientY - y0 > 90) closeFull(); y0 = null; });
    full.addEventListener('pointermove', wakeUp);
    document.addEventListener('keydown', fullKey);
    document.body.appendChild(full);
    // A new, empty lyrics box: draw what we already have for this song, or fetch.
    if (fullLyricsFor === S.now.id && fullLyrics && !fullLyrics.failed) paintFullLyrics(); else fullLyricsFor = null;
    setFullMode(mode || 'cover');
    var pb = full.querySelector('.fplay'); if (pb) pb.focus({preventScroll:true});
    keepAwake(); wakeUp();
  }
  function setFullMode(mode){
    fullMode = mode === 'lyrics' ? 'lyrics' : 'cover';
    if (!full) return;
    full.classList.toggle('mode-lyrics', fullMode === 'lyrics');
    full.classList.toggle('mode-cover', fullMode === 'cover');
    setText('fullSwitch', fullMode === 'cover' ? 'Lyrics' : 'Cover');
    fullCur = -2; paintFull(); tickFull();
  }
  function closeFull(){
    if (!full) return;
    full.remove(); full = null; clearTimeout(fullIdle);
    document.removeEventListener('keydown', fullKey);
    if (wake){ wake.release().catch(function(){}); wake = null; }
    var a = $('nowArt'); if (a) a.focus();
  }
  function fullKey(e){ if (e.key === 'Escape') closeFull(); else wakeUp(); }
  function wakeUp(){
    if (!full) return;
    full.classList.remove('idle'); clearTimeout(fullIdle);
    fullIdle = setTimeout(function(){ if (full) full.classList.add('idle'); }, 5000);
  }
  function keepAwake(){
    if (!full || !navigator.wakeLock || document.visibilityState !== 'visible') return;
    navigator.wakeLock.request('screen').then(function(w){ wake = w; }, function(){});
  }
  document.addEventListener('visibilitychange', keepAwake);  // the lock drops when the page is hidden
  function paintFull(){
    if (!full) return;
    var n = S && S.now;
    if (!n){ closeFull(); return; }
    var u = bigCover(n.cover);
    bg($('fullArt'), u); bg($('fullBg'), u); bg($('fullThumb'), n.cover);
    full.querySelectorAll('.ftitle').forEach(function(e){ e.textContent = n.title || ''; });
    full.querySelectorAll('.fsub').forEach(function(e){ e.textContent = sub(n.artist, n.album); });
    setText('fullHead', fullMode === 'lyrics' ? (n.title || '') + (n.artist ? ' · ' + n.artist : '') : '');
    var dev = deviceMode();
    var icon_ = dev ? (listening() ? 'pause' : 'play') : !S.radio_on ? 'power' : listening() ? 'stop' : 'play';
    full.querySelectorAll('.fplay').forEach(function(b){ setIcon(b, icon_); });
    if (fullMode === 'lyrics' && fullLyricsFor !== n.id) loadFullLyrics(n.id);
  }
  function loadFullLyrics(forId){
    fullLyricsFor = forId; fullLyrics = null; fullCur = -2;
    paintFullLyrics();
    var tries = 0;
    (function ask(){
      api('lyrics').then(function(d){
        if (!full || fullLyricsFor !== forId) return;
        if (d.id && d.id !== forId && ++tries < 6){ setTimeout(ask, 1500); return; }  // server still on the song before
        fullLyrics = d; paintFullLyrics(); tickFull();
      }, function(){
        if (!full || fullLyricsFor !== forId) return;
        if (++tries < 3){ setTimeout(ask, 2000); return; }
        fullLyrics = {found:false, failed:true}; paintFullLyrics();
      });
    })();
  }
  function paintFullLyrics(){
    var box = $('fullLyrics'); if (!box) return;
    box.textContent = ''; box.scrollTop = 0;
    var L = fullLyrics;
    function note(t, withBtn){
      var m = el('div', 'fl-note', t);
      if (withBtn) m.appendChild(el('div')).appendChild(btn('Show the cover', 'sm', function(){ setFullMode('cover'); }));
      box.appendChild(m);
    }
    if (!L){ note('Loading lyrics…'); return; }
    if (!L.found){ note(L.failed ? 'Couldn\'t load lyrics.' : 'No lyrics for this song.', true); return; }
    var inner = el('div', 'fl-lines');
    if (L.synced && L.synced.length) L.synced.forEach(function(l){ inner.appendChild(el('p', null, l[1] || '♪')); });
    else {
      inner.classList.add('plain');
      (L.plain || '').split('\n').forEach(function(l){ inner.appendChild(el('p', null, l || ' ')); });
    }
    box.appendChild(inner);
    if (!(L.synced && L.synced.length)) box.appendChild(el('div', 'fl-unsynced', 'Lyrics not synced to the song'));
  }
  function tickFull(){
    if (!full || !S || !S.now) return;
    var dev = deviceMode(), e = dev ? (P.item ? audio.currentTime : null) : elapsed();
    var d = dev && isFinite(audio.duration) ? audio.duration : S.now.duration;
    var w = e != null && d ? Math.min(100, e / d * 100) + '%' : '0';
    full.querySelectorAll('.fbar').forEach(function(b){ b.style.width = w; });
    var L = fullLyrics;
    if (fullMode !== 'lyrics' || !L || !L.synced || !L.synced.length || e == null) return;
    var cur = -1;
    for (var i = 0; i < L.synced.length; i++){ if (L.synced[i][0] <= e) cur = i; else break; }
    if (cur === fullCur) return;
    fullCur = cur;
    var ps = full.querySelectorAll('.fl-lines p');
    ps.forEach(function(p, i){ p.classList.toggle('cur', i === cur); p.classList.toggle('past', i < cur); });
    // Keep the current line a third of the way down, unless they just scrolled themselves.
    var box = $('fullLyrics'), p = ps[Math.max(cur, 0)];
    if (box && p && Date.now() - fullUserScroll > 4000){
      var calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
      box.scrollTo({top:p.offsetTop - box.clientHeight / 3, behavior:calm ? 'auto' : 'smooth'});
    }
  }

  function setText(id, text){ var e = $(id); if (e) e.textContent = text; }
  function paintNow(){
    if (!S || !$('nowTitle')) return;
    var n = S.now, dev = deviceMode();
    bg($('nowArt'), n && n.cover);
    setText('nowTitle', n ? n.title : (S.radio_on ? 'Starting…' : 'The radio is off'));
    setText('nowSub', n ? sub(n.artist, n.album) : '');
    setText('nowTag', !n || (dev && !P.item) ? '' : n.by === 'iq' ? '✦ Aux IQ' + (n.why ? ' · ' + n.why : '') : n.from === 'queue' ? (n.by && n.by !== 'you' ? 'From the queue · added by ' + n.by : 'From the queue') :
      ({library:'Library shuffle', requests:'Shuffling Requests', playlist:'Shuffling ' + (S.settings.empty.name || 'a playlist'),
        artist:'Shuffling ' + (S.settings.empty.name || 'an artist'), similar:'Similar songs', silence:''})[S.settings.empty.mode] || 'Library');
    setText('nowDur', mmss(n && n.duration));
    var pb = $('nowPlay');
    if (pb){
      var name = dev ? (listening() ? 'pause' : 'play') : !S.radio_on ? 'power' : listening() ? 'stop' : 'play';
      setIcon(pb, name);
      pb.setAttribute('aria-label', dev ? (listening() ? 'Pause' : 'Play') : !S.radio_on ? 'Turn the radio on' : listening() ? 'Stop listening' : 'Listen here');
    }
    var cap = $('nowCap');
    if (cap) cap.textContent = dev ? '' : !S.radio_on ? 'The radio is off · tap to turn it on' : listening() ? 'Listening here' : 'Tap ▶ to listen here';
    if (pb){
    }
    if (dev && !P.item){ setText('nowTitle', 'Tap ▶ to play'); setText('nowSub', S.queue.length ? S.queue.length + ' songs in the queue' : 'Your library on shuffle'); }
    var nxt = S.queue.find(function(i){ return i.status !== 'failed'; });
    setText('nowNext', nxt ? 'Up next: ' + nxt.title + (nxt.artist ? ' · ' + nxt.artist : '') + '  ›' : 'Queue is empty  ›');
    paintIq();
    var key = n ? n.id : null;
    if ($('lyrics') && key !== lyricsFor){ lyricsFor = key; loadLyrics(); }
    paintFull();
    tickNow();
  }
  function tickNow(){
    tickFull();
    if (!$('nowEl') || !S) return;
    var e = elapsed(), d = S.now && S.now.duration;
    var seek = $('nowSeek');
    if (seek){
      var ad = isFinite(audio.duration) ? audio.duration : d;
      if (!seek.dataset.drag){ seek.value = ad ? Math.round(audio.currentTime / ad * 1000) : 0; $('nowEl').textContent = mmss(P.item ? audio.currentTime : 0); }
      $('nowDur').textContent = mmss(ad);
    } else {
      $('nowEl').textContent = mmss(e);
      $('nowBar').style.width = e != null && d ? Math.min(100, e / d * 100) + '%' : '0';
    }
    if (lyrics && lyrics.synced && lyrics.synced.length && e != null && $('lyrics')){
      var ps = document.querySelectorAll('#lyrics p'), cur = -1;
      for (var i = 0; i < lyrics.synced.length; i++){ if (lyrics.synced[i][0] <= e) cur = i; else break; }
      ps.forEach(function(p, i){
        var on = i === cur;
        if (on && !p.classList.contains('cur')){ var box = $('lyrics'); box.scrollTop = p.offsetTop - box.offsetTop - box.clientHeight / 3; }
        p.classList.toggle('cur', on);
      });
    }
  }
  function loadLyrics(){
    lyrics = null;
    var box = $('lyrics'); if (!box) return;
    box.textContent = ''; box.appendChild(el('div', 'empty', S.now ? 'Loading lyrics…' : 'Nothing playing'));
    if (!S.now) return;
    var forId = S.now.id, tries = 0;
    (function ask(){
      api('lyrics').then(function(d){
        if (!$('lyrics') || lyricsFor !== forId) return;
        // The server can still be on the song before (the page starts the next
        // one itself): ask again until it's on this one.
        if (d.id && d.id !== forId && ++tries < 6){ setTimeout(ask, 1500); return; }
        lyrics = d; paintLyrics();
      }, function(){
        if (!$('lyrics') || lyricsFor !== forId) return;
        if (++tries < 3){ setTimeout(ask, 2000); return; }
        box.textContent = ''; box.appendChild(el('div', 'empty', 'Couldn\'t load lyrics.'));
      });
    })();
  }
  function paintLyrics(){
    var box = $('lyrics'); if (!box || !lyrics) return;
    box.textContent = '';
    if (!lyrics.found){ box.appendChild(el('div', 'empty', 'No lyrics found for this song.')); return; }
    if (lyrics.synced && lyrics.synced.length) lyrics.synced.forEach(function(l){ box.appendChild(el('p', null, l[1] || '♪')); });
    else (lyrics.plain || '').split('\n').forEach(function(l){ box.appendChild(el('p', null, l || ' ')); });
    tickNow();
  }

  // ------------------------------------------------------------------ Aux IQ
  // ✦ IQ: tap turns it on / off, hold (or "Mood…") asks for a mood. 👍 / 👎 teach it;
  // 👎 also skips. Its picks wait at the end of Up next, tagged ✦ with the reason.
  function iqRow(){
    var r = el('div', 'iqrow');
    var dn = iconBtn('down', 'tbtn sm rate', function(){ rate(false); }, 'Dislike'); dn.id = 'rateDown';
    var b = el('button', 'iqbtn'); b.type = 'button'; b.id = 'iqBtn'; b.setAttribute('aria-label', 'Aux IQ: tap to turn on or off, hold for a mood');
    var up = iconBtn('up', 'tbtn sm rate', function(){ rate(true); }, 'Like'); up.id = 'rateUp';
    var held = false, tm = null;
    b.addEventListener('pointerdown', function(){ held = false; clearTimeout(tm); tm = setTimeout(function(){ held = true; iqMoodSheet(); }, 550); });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(function(ev){ b.addEventListener(ev, function(){ clearTimeout(tm); }); });
    b.addEventListener('contextmenu', function(e){ e.preventDefault(); });
    b.onclick = function(){ if (held){ held = false; return; } iqToggle(); };
    r.appendChild(dn); r.appendChild(b); r.appendChild(up);
    var wrap = el('div', 'iqwrap'); wrap.appendChild(r);
    var cap = el('button', 'iqcap ghost'); cap.type = 'button'; cap.id = 'iqCap'; cap.onclick = iqMoodSheet; wrap.appendChild(cap);
    return wrap;
  }
  function paintIq(){
    var b = $('iqBtn'); if (!b || !S || !S.iq) return;
    var q = S.iq, on = q.on || q.active;
    b.classList.toggle('on', !!on);
    b.innerHTML = icon('spark') + '<span>IQ</span>' + (q.mood ? '<em>' + esc(q.mood) + '</em>' : '');
    var cap = $('iqCap');
    if (cap){
      cap.textContent = !on ? 'Aux IQ picks songs like what you play · hold for a mood' :
        q.error ? q.error : q.waiting < q.buffer ? 'Lining up picks · ' + q.waiting + ' of ' + q.buffer + ' ready' :
        q.waiting + ' picks lined up' + (q.auto && !q.on ? ' (queue empty: Aux IQ)' : '') + ' · ' + q.downloads + '/' + q.daily + ' downloads today';
    }
    var u = $('rateUp'), d = $('rateDown');
    if (u){ u.classList.toggle('cur', S.rating === 1); u.setAttribute('aria-pressed', S.rating === 1 ? 'true' : 'false'); }
    if (d){ d.classList.toggle('cur', S.rating === -1); d.setAttribute('aria-pressed', S.rating === -1 ? 'true' : 'false'); }
  }
  function esc(t){ var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
  function iqToggle(){
    var on = !(S.iq && S.iq.on);
    if (S.iq){ S.iq.on = on; paintIq(); }
    post('iq', {on:on}).then(done, fail);
  }
  function rate(up){
    if (!S || !S.now){ toast('Nothing is playing.', true); return; }
    S.rating = S.rating === (up ? 1 : -1) ? null : (up ? 1 : -1); paintIq();
    post('iq/rate', {up:up}).then(function(d){
      if (d && d.say) toast(d.say);
      if (d && d.rating === -1 && deviceMode()) devNext();
      refresh();
    }, fail);
  }
  var MOODS = ['Chill', 'Rainy drive', 'Gym', 'Focus', 'Late night', 'Party', 'Feel-good', '90s Bollywood', 'Old Tamil hits', 'Romantic'];
  function iqMoodSheet(){
    closeSheet();
    var q = (S && S.iq) || {};
    var shade = el('div', 'shade'); shade.id = 'shade';
    var sh = el('div', 'sheet iqsheet'); shade.appendChild(sh);
    shade.onclick = function(e){ if (e.target === shade) closeSheet(); };
    var h = el('div', 'head'); var tx = el('div', 'tx');
    tx.appendChild(el('div', 't', '✦ Aux IQ · what\'s the mood?'));
    tx.appendChild(el('div', 's', 'Picks songs for it, then keeps the flow going.'));
    h.appendChild(tx); sh.appendChild(h);
    var f = document.createElement('form'); f.className = 'iqform';
    var inp = document.createElement('input'); inp.type = 'text'; inp.placeholder = 'e.g. rainy drive, gym, 90s Tamil'; inp.value = q.mood || '';
    inp.maxLength = 80; inp.setAttribute('aria-label', 'Mood'); inp.enterKeyHint = 'go';
    f.appendChild(inp); f.appendChild(btn('Go', 'primary', null));
    f.lastChild.type = 'submit';
    function go(m){ closeSheet(); toast('Aux IQ: finding ' + m + ' songs…'); post('iq', {mood:m}).then(done, fail); }
    f.onsubmit = function(e){ e.preventDefault(); var m = inp.value.trim(); if (m) go(m); };
    sh.appendChild(f);
    var ch = el('div', 'chips');
    MOODS.forEach(function(m){ ch.appendChild(btn(m, q.mood === m ? 'on' : '', function(){ go(m); })); });
    sh.appendChild(ch);
    var o = el('div', 'opts');
    if (q.mood) o.appendChild(btn('✕  Clear the mood (follow what plays)', '', function(){ closeSheet(); post('iq', {mood:null}).then(done, fail); }));
    o.appendChild(btn(q.on ? '⏻  Turn Aux IQ off' : '✦  Turn Aux IQ on', '', function(){ closeSheet(); iqToggle(); }));
    sh.appendChild(o);
    document.body.appendChild(shade);
    setTimeout(function(){ if (!q.mood) inp.focus(); }, 60);
  }

  // ------------------------------------------------------------------ Settings
  renderers.settings = function(v){
    topbar(v, 'Settings');
    function row(card, label, hint, control){
      var r = el('div', 'setrow'); var l = el('label', null, label);
      if (hint != null){ var h = el('span', 'hint', hint); l.appendChild(h); r.hint = h; }
      r.appendChild(l); if (control) r.appendChild(control); card.appendChild(r); return r;
    }
    function toggle(key, label, hint){
      var b = btn('', 'switch'); b.id = 'set_' + key;
      b.onclick = function(){ var body = {}; body[key] = !(S.settings[key]); post('settings', body).then(refresh); };
      return [label, hint, b];
    }

    v.appendChild(el('h2', null, 'Auxx-In radio'));
    var c1 = el('div', 'card settings'); c1.id = 'setPanel'; v.appendChild(c1);
    var rb = btn('', 'switch'); rb.id = 'set_radio';
    rb.onclick = function(){ post('radio', {on:!S.radio_on}).then(done, fail); };
    row(c1, 'Radio', 'The stream other players and the car tune in to', rb);
    [toggle('even_volume', 'Even volume', 'Evens out loud and quiet songs')]
      .forEach(function(o){ row(c1, o[0], o[1], o[2]); });
    var code = el('code', null, S ? S.stream : ''); code.id = 'streamUrl';
    var sr = row(c1, 'Stream address', null, btn('Copy', 'sm', function(){
      if (navigator.clipboard) navigator.clipboard.writeText(S.stream).then(function(){ toast('Copied.'); }, function(){ toast(S.stream); });
      else toast(S.stream);
    }));
    sr.querySelector('label').appendChild(el('span', 'hint')).appendChild(code);
    sr.querySelector('label').appendChild(el('span', 'hint', S && S.navidrome === false
      ? 'Open it in any player that takes a stream URL (VLC, a browser…)' : 'In any Navidrome client: Radio → Auxx-In'));

    v.appendChild(el('h2', null, 'Playback'));
    var c2 = el('div', 'card settings'); v.appendChild(c2);
    // When the queue is empty
    var sel = document.createElement('select'); sel.id = 'emptyMode';
    [['library', 'Shuffle my whole library'], ['requests', 'Shuffle my Requests'], ['playlist', 'Shuffle a playlist…'],
     ['artist', 'Shuffle an artist…'], ['similar', 'Similar songs (autoplay)'], ['iq', 'Aux IQ (picks like what you play)'], ['silence', 'Nothing (silence)']]
      .filter(function(o){ return !(S && S.navidrome === false && /^(requests|playlist|artist)$/.test(o[0])); })
      .forEach(function(o){ var op = el('option', null, o[1]); op.value = o[0]; sel.appendChild(op); });
    var pick = document.createElement('select'); pick.id = 'emptyPick'; pick.hidden = true;
    var pk = el('div', 'acts'); pk.style.flexWrap = 'wrap'; pk.appendChild(sel); pk.appendChild(pick);
    row(c2, 'When the queue is empty', 'What plays when nothing is queued', pk);
    function fillPick(mode, keepId){
      if (mode !== 'playlist' && mode !== 'artist'){ pick.hidden = true; return Promise.resolve(); }
      pick.hidden = false; pick.textContent = ''; pick.appendChild(el('option', null, 'Loading…'));
      return api(mode === 'playlist' ? 'lib/playlists' : 'lib/artists').then(function(list){
        pick.textContent = '';
        if (list.error){ pick.appendChild(el('option', null, 'Navidrome is asleep')); return; }
        pick.appendChild(el('option', null, mode === 'playlist' ? 'Choose a playlist' : 'Choose an artist')).value = '';
        list.forEach(function(x){ var op = el('option', null, x.name); op.value = x.id; pick.appendChild(op); });
        if (keepId) pick.value = keepId;
      });
    }
    sel.onchange = function(){
      var m = sel.value;
      if (m === 'playlist' || m === 'artist'){ fillPick(m); return; }
      pick.hidden = true;
      post('settings', {empty:{mode:m}}).then(function(){ toast('When the queue is empty: ' + sel.selectedOptions[0].textContent + '.'); refresh(); });
    };
    pick.onchange = function(){
      if (!pick.value) return;
      post('settings', {empty:{mode:sel.value, id:pick.value, name:pick.selectedOptions[0].textContent}})
        .then(function(){ toast('When the queue is empty: ' + pick.selectedOptions[0].textContent + '.'); refresh(); });
    };
    var st = S && S.settings && S.settings.empty;
    if (st){ sel.value = st.mode; fillPick(st.mode, st.id); }
    // Sleep timer
    var sl = document.createElement('select');
    [['0', 'Off'], ['15', '15 minutes'], ['30', '30 minutes'], ['45', '45 minutes'], ['60', '1 hour'], ['90', '1½ hours']]
      .forEach(function(o){ var op = el('option', null, o[1]); op.value = o[0]; sl.appendChild(op); });
    sl.onchange = function(){ post('sleep', {minutes:+sl.value}).then(function(){ toast(+sl.value ? 'Stopping in ' + sl.selectedOptions[0].textContent + '.' : 'Sleep timer off.'); refresh(); }); };
    var r2 = row(c2, 'Sleep timer', '', sl); r2.hint.id = 'sleepHint';
    // Fade between songs: this device and the Aux radio
    var fd = document.createElement('select'); fd.id = 'fadeSel';
    [['0', 'Off'], ['5', '5 seconds'], ['10', '10 seconds'], ['15', '15 seconds']]
      .forEach(function(o){ var op = el('option', null, o[1]); op.value = o[0]; fd.appendChild(op); });
    fd.onchange = function(){
      post('settings', {fade:+fd.value}).then(function(){
        if (+fd.value) fxInit();  // this change is a tap: Web Audio may start here
        toast(+fd.value ? 'Fading ' + fd.value + ' s between songs.' : 'No fade between songs.'); refresh();
      }, fail);
    };
    row(c2, 'Fade between songs', 'Fades out the end of a song and in the next (the radio crossfades)', fd);
    // Theme: this device only, kept in the browser
    var th = document.createElement('select');
    [['auto', 'Auto (like this device)'], ['light', 'Light'], ['dark', 'Dark']]
      .forEach(function(o){ var op = el('option', null, o[1]); op.value = o[0]; th.appendChild(op); });
    try { th.value = localStorage.getItem('auxx-theme') || 'auto'; } catch (e){ th.value = 'auto'; }
    th.onchange = function(){
      if (th.value === 'auto') delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = th.value;
      try { localStorage.setItem('auxx-theme', th.value); } catch (e){}
    };
    row(c2, 'Theme', 'This device only', th);

    accountCards(v, row);
    if (!(ME && ME.admin)){ paintSettings(); return; }

    // Storage (admins): what the music and data folders take up
    v.appendChild(el('h2', null, 'Storage'));
    var sc = el('div', 'card settings'); v.appendChild(sc);
    sc.appendChild(el('div', 'hint', 'Counting…'));
    api('storage').then(function(d){
      function gb(b){ return b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : Math.round(b / 1e6) + ' MB'; }
      sc.textContent = '';
      row(sc, 'Music', d.songs.toLocaleString() + ' songs · ' + gb(d.music), null);
      row(sc, 'Auxx-In data', 'Settings, queue, covers and downloads in progress · ' + gb(d.data), null);
      row(sc, 'Free space', gb(d.disk_free) + ' free of ' + gb(d.disk_total), null);
    }, function(){ sc.textContent = ''; sc.appendChild(el('div', 'hint', 'Couldn\'t count storage.')); });

    // Configuration: Navidrome, radio, library and Aux IQ, one card per section
    // (/api/config). Saved on the server, used right away. Secrets are never shown.
    var cfg = el('div'); v.appendChild(cfg);
    api('config').then(function(fields){
      var sections = {};
      fields.forEach(function(f){ (sections[f.section] = sections[f.section] || []).push(f); });
      Object.keys(sections).forEach(function(name){
        cfg.appendChild(el('h2', null, 'Configuration · ' + name));
        var card = el('div', 'card settings'); cfg.appendChild(card);
        var inputs = [], rows = [], tog = null;
        function showDetails(){ rows.forEach(function(r){ r.hidden = tog.value !== 'true'; }); }
        sections[name].forEach(function(f){
          if (f.kind === 'toggle'){
            tog = btn('', 'switch'); tog.dataset.key = f.key;
            tog.value = f.value ? 'true' : 'false'; tog.dataset.orig = tog.value;
            tog.onclick = function(){ tog.value = tog.value === 'true' ? 'false' : 'true'; paintTog(); showDetails(); };
            function paintTog(){ var on = tog.value === 'true'; tog.textContent = on ? 'On' : 'Off'; tog.classList.toggle('on', on); }
            paintTog(); row(card, f.label, f.hint, tog); inputs.push(tog);
            return;
          }
          if (f.kind === 'choice'){
            var cs = document.createElement('select'); cs.dataset.key = f.key;
            f.options.forEach(function(o){ var op = el('option', null, o[1]); op.value = o[0]; cs.appendChild(op); });
            cs.value = f.value; cs.dataset.orig = cs.value; cs.setAttribute('aria-label', f.label);
            var cr = row(card, f.label, f.hint, cs); inputs.push(cs); if (tog) rows.push(cr);
            return;
          }
          if (f.kind === 'file'){
            // Read in the browser, sent as text on Save; false removes it.
            var hold = {value:'', dataset:{key:f.key, orig:''}};
            var fi = document.createElement('input'); fi.type = 'file'; fi.accept = '.txt,text/plain'; fi.hidden = true;
            var st = el('span', 'hint', f.set ? 'Saved' : 'Not set');
            fi.onchange = function(){
              var file = fi.files[0]; if (!file) return;
              file.text().then(function(t){ hold.value = t; st.textContent = file.name + ' (press Save)'; });
            };
            var box = el('div', 'acts'); box.style.flexWrap = 'wrap'; box.appendChild(st); box.appendChild(fi);
            box.appendChild(btn('Choose file', 'sm', function(){ fi.click(); }));
            if (f.set) box.appendChild(btn('Remove', 'sm', function(){ hold.value = false; st.textContent = 'Will be removed (press Save)'; }));
            var fr = row(card, f.label, f.hint, box); inputs.push(hold); if (tog) rows.push(fr);
            return;
          }
          var inp = document.createElement('input');
          inp.type = f.kind === 'secret' ? 'password' : f.kind === 'number' ? 'number' : 'text';
          if (f.kind === 'number') inp.min = '0';
          inp.autocomplete = 'off'; inp.className = 'cfg';
          if (f.kind === 'secret') inp.placeholder = f.set ? 'Saved (type to change)' : 'Not set';
          else inp.value = f.value == null ? '' : f.value;
          inp.setAttribute('aria-label', f.label);
          inp.dataset.key = f.key; inp.dataset.orig = inp.value;
          var rr = row(card, f.label, f.hint, inp); inputs.push(inp); if (tog) rows.push(rr);
        });
        if (tog) showDetails();
        var r = el('div', 'acts'); r.style.justifyContent = 'flex-end';
        r.appendChild(btn('Save', 'primary', function(){
          var body = {}, n = 0;
          inputs.forEach(function(i){ if (i.value !== i.dataset.orig){ body[i.dataset.key] = i.value; n++; } });
          if (!n){ toast('Nothing changed.'); return; }
          post('config', body).then(function(d){
            if (d.error){ toast(d.error, true); return; }
            toast(d.changed && d.changed.length ? name + ' saved.' : 'Nothing changed.');
            refresh().then(render);
          }, fail);
        }));
        card.appendChild(r);
      });
    }, function(){ cfg.appendChild(el('div', 'empty', 'Couldn\'t load the configuration.')); });
    paintSettings();
  };
  // Account (everyone) and Users (admins): /api/password, /api/logout, /api/users.
  function accountCards(v, row){
    v.appendChild(el('h2', null, 'Account'));
    var c = el('div', 'card settings'); v.appendChild(c);
    var via = {local:'', navidrome:' · Navidrome account', proxy:' · signed in by your login service'}[ME.source] || '';
    row(c, ME.name, (ME.admin ? 'Admin' : 'Listener') + via, btn('Log out', 'sm', function(){
      post('logout').then(function(){ location.replace('login'); });
    }));
    if (ME.source === 'local'){
      var o = document.createElement('input'); o.type = 'password'; o.autocomplete = 'current-password'; o.placeholder = 'Current password'; o.className = 'cfg';
      var n = document.createElement('input'); n.type = 'password'; n.autocomplete = 'new-password'; n.placeholder = 'New password (8+ characters)'; n.className = 'cfg';
      var pw = el('div', 'acts'); pw.style.flexWrap = 'wrap'; pw.appendChild(o); pw.appendChild(n);
      pw.appendChild(btn('Change', 'sm', function(){
        post('password', {old:o.value, new:n.value}).then(function(d){
          toast(d.say || d.error || 'Done.', !!d.error); if (d.ok){ o.value = ''; n.value = ''; }
        }, fail);
      }));
      row(c, 'Password', null, pw);
    }
    if (!ME.admin) return;

    v.appendChild(el('h2', null, 'Users'));
    var u = el('div', 'card settings'); v.appendChild(u);
    var list = el('div', 'settings'); u.appendChild(list);
    function paint(d){
      if (d.error){ toast(d.error, true); return; }
      list.textContent = '';
      d.users.forEach(function(x){
        var acts = el('div', 'acts'); acts.style.flexWrap = 'wrap';
        if (x.name === ME.name){ row(list, x.name + ' (you)', x.admin ? 'Admin' : 'Listener', acts); return; }  // no changing your own role here
        acts.appendChild(btn(x.admin ? 'Make listener' : 'Make admin', 'sm', function(){ post('users/' + encodeURIComponent(x.name), {admin:!x.admin}).then(paint, fail); }));
        acts.appendChild(btn('New password', 'sm', function(){
          var p = prompt('New password for ' + x.name + ' (8+ characters):'); if (!p) return;
          post('users/' + encodeURIComponent(x.name), {password:p}).then(function(r){ if (!r.error) toast('Password changed.'); paint(r); }, fail);
        }));
        acts.appendChild(btn('Delete', 'sm', function(){
          if (!confirm('Delete the user ' + x.name + '?')) return;
          api('users/' + encodeURIComponent(x.name), {method:'DELETE'}).then(paint, fail);
        }));
        row(list, x.name, x.admin ? 'Admin' : 'Listener', acts);
      });
      if (!d.users.length) list.appendChild(el('div', 'hint', 'No Auxx-In accounts yet.'));
      if (d.navidrome) list.appendChild(el('div', 'hint', 'Navidrome users can log in with their Navidrome account too; manage them in Navidrome. Navidrome admins are admins here.'));
    }
    var nu = document.createElement('input'); nu.placeholder = 'Username'; nu.autocomplete = 'off'; nu.autocapitalize = 'none'; nu.className = 'cfg';
    var np = document.createElement('input'); np.type = 'password'; np.placeholder = 'Password (8+ characters)'; np.autocomplete = 'new-password'; np.className = 'cfg';
    var na = btn('Listener', 'switch sm'); na.onclick = function(){ na.classList.toggle('on'); na.textContent = na.classList.contains('on') ? 'Admin' : 'Listener'; };
    var add = el('div', 'acts'); add.style.flexWrap = 'wrap'; add.appendChild(nu); add.appendChild(np); add.appendChild(na);
    add.appendChild(btn('Add', 'primary sm', function(){
      post('users', {username:nu.value.trim(), password:np.value, admin:na.classList.contains('on')}).then(function(d){
        if (!d.error){ toast('Added ' + nu.value.trim() + '.'); nu.value = ''; np.value = ''; }
        paint(d);
      }, fail);
    }));
    row(u, 'Add a user', 'Admins can change Configuration and manage users; listeners can share and play.', add);
    api('users').then(paint, fail);
  }
  function paintSettings(){
    if (!S || !$('setPanel')) return;
    function sw(id, on){ var b = $(id); if (b){ b.textContent = on ? 'On' : 'Off'; b.classList.toggle('on', !!on); } }
    sw('set_radio', S.radio_on);
    sw('set_even_volume', S.settings.even_volume);
    var fd = $('fadeSel'); if (fd && document.activeElement !== fd) fd.value = String(S.settings.fade || 0);
    setText('streamUrl', S.stream);
  }

  // ------------------------------------------------------------------ Search
  var searchSrc = 'ytm', searchTimer = null;
  renderers.search = function(v){
    topbar(v, 'Search');
    var box = el('form', 'searchbox');
    var inp = document.createElement('input'); inp.type = 'search'; inp.placeholder = 'Song, artist, or paste a link'; inp.id = 'sq';
    inp.setAttribute('aria-label', 'Search'); inp.autocomplete = 'off'; inp.value = cache.q || '';
    box.appendChild(inp); box.appendChild(btn('Go', 'primary', function(){ runSearch(true); }));
    box.onsubmit = function(e){ e.preventDefault(); runSearch(true); };
    v.appendChild(box);
    var chips = el('div', 'chips');
    if (S && S.navidrome === false && searchSrc === 'lib') searchSrc = 'ytm';
    [['ytm', 'YouTube Music'], ['yt', 'YouTube'], ['lib', 'My library']].filter(function(c){
      return c[0] !== 'lib' || !(S && S.navidrome === false);
    }).forEach(function(c){
      var b = btn(c[1], searchSrc === c[0] ? 'on' : '', function(){
        searchSrc = c[0]; chips.querySelectorAll('button').forEach(function(x){ x.classList.remove('on'); }); b.classList.add('on'); runSearch(true);
      });
      chips.appendChild(b);
    });
    v.appendChild(chips);
    var res = el('div'); res.id = 'results'; v.appendChild(res);
    inp.addEventListener('input', function(){ clearTimeout(searchTimer); searchTimer = setTimeout(function(){ runSearch(false); }, 500); });
    if (cache.q) runSearch(true); else res.appendChild(el('div', 'empty', 'Search YouTube Music, YouTube or your library. Tap a result to preview it.'));
    setTimeout(function(){ if (!cache.q) inp.focus(); }, 50);
  };
  function runSearch(force){
    var inp = $('sq'); if (!inp) return;
    var q = inp.value.trim(); cache.q = q;
    var res = $('results'); if (!res) return;
    if (!q){ res.textContent = ''; return; }
    if (/^https?:\/\//i.test(q)){ linkCard(q, res); return; }
    if (q.length < 2) return;
    var key = searchSrc + '|' + q;
    if (cache.searchKey === key && !force) return;
    cache.searchKey = key;
    res.textContent = ''; res.appendChild(el('div', 'empty', 'Searching…'));
    var path = searchSrc === 'lib' ? 'lib/search?q=' + encodeURIComponent(q) : 'search?src=' + searchSrc + '&q=' + encodeURIComponent(q);
    api(path).then(function(d){
      if (cache.searchKey !== key || !$('results')) return;
      res.textContent = '';
      if (d.error){ res.appendChild(el('div', 'empty', d.error)); return; }
      if (searchSrc === 'lib'){
        if (d.artists && d.artists.length){
          res.appendChild(el('h2', null, 'Artists'));
          var l = el('div', 'list');
          d.artists.slice(0, 4).forEach(function(a){ l.appendChild(songRow({cover:libCover(a.cover_id, 120), title:a.name, sub:(a.albums || 0) + ' albums',
            onTap:function(){ location.hash = '#library/artist/' + a.id; }})); });
          res.appendChild(l);
        }
        if (d.albums && d.albums.length){
          res.appendChild(el('h2', null, 'Albums'));
          var sc = el('div', 'scroller'); d.albums.forEach(function(a){ sc.appendChild(albumTile(a)); }); res.appendChild(sc);
        }
        res.appendChild(el('h2', null, 'Songs'));
        var ls = el('div', 'list'); (d.songs || []).forEach(function(s){ ls.appendChild(libSongRow(s)); }); res.appendChild(ls);
        if (!(d.songs || []).length) res.appendChild(el('div', 'empty', 'Not in your library. Try YouTube Music.'));
        return;
      }
      var list = el('div', 'list');
      (d.results || []).forEach(function(r){
        var url = searchSrc === 'yt' ? 'https://www.youtube.com/watch?v=' + r.video_id : ytUrl(r.video_id);
        list.appendChild(songRow({cover:r.cover, title:r.title, sub:sub(r.artist, r.album, r.duration),
          onTap:function(){ openOnline(r, searchSrc); },
          buttons:[btn('＋', 'icon ghost', function(){ toast('Adding…'); post('add', {q:url, where:'end', exact:searchSrc === 'yt'}).then(done, fail); }, 'Add to queue'),
                   btn('⋯', 'icon ghost', function(){ openOnline(r, searchSrc); }, 'More')]}));
      });
      res.appendChild(list);
      if (!(d.results || []).length) res.appendChild(el('div', 'empty', 'Nothing found.'));
    }, function(){ res.textContent = ''; res.appendChild(el('div', 'empty', 'Search failed.')); });
  }
  function linkCard(q, res){
    res.textContent = ''; res.appendChild(el('div', 'empty', 'Reading the link…'));
    api('link?q=' + encodeURIComponent(q)).then(function(d){
      if (!$('results')) return;
      res.textContent = '';
      var card = el('div', 'card');
      if (d.kind === 'video'){
        card.appendChild(previewBox(d.video_id, null, false));
      } else {
        card.appendChild(el('div', 't', d.name || (d.kind === 'playlist' ? 'YouTube playlist' : 'Link')));
        card.appendChild(el('div', 's', d.count ? d.count + ' songs' + (d.first ? ' · starts with ' + d.first : '') : d.kind.replace('spotify-', 'Spotify ')));
      }
      var opts = el('div', 'ctrls'); opts.style.justifyContent = 'flex-start';
      onlineOptions(q, false).forEach(function(o){ opts.appendChild(btn(o[0].replace(/^\S+\s+/, ''), o[0].indexOf('Play next') >= 0 ? 'primary' : '', o[1])); });
      card.appendChild(opts);
      res.appendChild(card);
    });
  }

  // ------------------------------------------------------------------ Library
  var libTab = 'home';
  renderers.library = function(v, rest){
    if (rest[0] === 'album') return libAlbum(v, rest[1]);
    if (rest[0] === 'playlist') return libPlaylist(v, rest[1]);
    if (rest[0] === 'artist') return libArtist(v, rest[1]);
    topbar(v, 'Library');
    var chips = el('div', 'chips');
    [['home', 'Home'], ['artists', 'Artists'], ['playlists', 'Playlists'], ['favorites', 'Favorites'], ['albums', 'All albums']].forEach(function(c){
      chips.appendChild(btn(c[1], libTab === c[0] ? 'on' : '', function(){ libTab = c[0]; render(); }));
    });
    v.appendChild(chips);
    var box = el('div'); v.appendChild(box);
    box.appendChild(el('div', 'empty', 'Loading…'));
    function err(d){ box.textContent = ''; box.appendChild(el('div', 'empty', (d && d.error) || 'Navidrome isn\'t answering.')); }
    if (libTab === 'home') api('lib/home').then(function(d){
      if (d.error) return err(d);
      box.textContent = '';
      [['Recently added', d.newest], ['Recently played', d.recent], ['Most played', d.frequent]].forEach(function(sec){
        if (!sec[1] || !sec[1].length) return;
        box.appendChild(el('h2', null, sec[0]));
        var sc = el('div', 'scroller'); sec[1].forEach(function(a){ sc.appendChild(albumTile(a)); }); box.appendChild(sc);
      });
      box.appendChild(el('h2', null, 'Playlists'));
      var l = el('div', 'list'); d.playlists.forEach(function(p){ l.appendChild(playlistRow(p)); }); box.appendChild(l);
    }, err);
    else if (libTab === 'artists') api('lib/artists').then(function(d){
      if (d.error) return err(d);
      box.textContent = '';
      var f = document.createElement('input'); f.type = 'search'; f.placeholder = 'Filter ' + d.length + ' artists'; f.style.width = '100%'; f.style.marginBottom = '10px';
      box.appendChild(f);
      var l = el('div', 'list'); box.appendChild(l);
      function paint(){
        l.textContent = ''; var q = f.value.trim().toLowerCase(), n = 0;
        d.forEach(function(a){ if (n < 200 && (!q || (a.name || '').toLowerCase().indexOf(q) >= 0)){ n++;
          l.appendChild(songRow({cover:libCover(a.cover_id, 120), title:a.name, sub:(a.albums || 0) + ' album' + (a.albums === 1 ? '' : 's'),
            onTap:function(){ location.hash = '#library/artist/' + a.id; }})); } });
      }
      f.oninput = paint; paint();
    }, err);
    else if (libTab === 'playlists') api('lib/playlists').then(function(d){
      if (d.error) return err(d);
      box.textContent = '';
      var fc = el('div', 'chips'), l = el('div', 'list');
      [['all', 'All'], ['made', '✦ Made by Auxx-In']].forEach(function(c){
        fc.appendChild(btn(c[1], plFilter === c[0] ? 'on' : '', function(){
          plFilter = c[0]; fc.querySelectorAll('button').forEach(function(x){ x.classList.remove('on'); }); this.classList.add('on'); paint();
        }));
      });
      function paint(){
        l.textContent = '';
        var list = d.filter(function(p){ return plFilter === 'all' || p.made; });
        list.forEach(function(p){ l.appendChild(playlistRow(p)); });
        if (!list.length) l.appendChild(el('div', 'empty', 'Auxx-In makes a playlist for you every morning.'));
      }
      box.appendChild(fc); box.appendChild(l); paint();
    }, err);
    else if (libTab === 'favorites') api('lib/starred').then(function(d){
      if (d.error) return err(d);
      box.textContent = '';
      if (d.albums.length){ box.appendChild(el('h2', null, 'Albums')); var sc = el('div', 'scroller'); d.albums.forEach(function(a){ sc.appendChild(albumTile(a)); }); box.appendChild(sc); }
      box.appendChild(el('h2', null, 'Songs'));
      if (d.songs.length){ box.appendChild(collectionButtons({ids:d.songs.map(function(s){ return s.nd_id; })})); }
      var l = el('div', 'list'); d.songs.forEach(function(s){ l.appendChild(libSongRow(s)); }); box.appendChild(l);
      if (!d.songs.length) box.appendChild(el('div', 'empty', 'No favorites yet: tap ⋯ on a song → Add to favorites.'));
    }, err);
    else if (libTab === 'albums'){
      var sort = 'newest', offset = 0;
      box.textContent = '';
      var ch = el('div', 'chips');
      [['newest', 'Newest'], ['alphabeticalByName', 'A–Z'], ['random', 'Random'], ['frequent', 'Most played']].forEach(function(c){
        ch.appendChild(btn(c[1], sort === c[0] ? 'on' : '', function(){ sort = c[0]; offset = 0; grid.textContent = '';
          ch.querySelectorAll('button').forEach(function(x){ x.classList.remove('on'); }); this.classList.add('on'); more(); }));
      });
      var grid = el('div', 'grid'); var mb = btn('More', '', more);
      box.appendChild(ch); box.appendChild(grid); box.appendChild(el('div', 'ctrls')).appendChild(mb);
      function more(){ api('lib/albums?type=' + sort + '&size=40&offset=' + offset).then(function(d){
        if (d.error) return err(d); d.forEach(function(a){ grid.appendChild(albumTile(a)); }); offset += d.length; mb.hidden = d.length < 40; }); }
      more();
    }
  };
  function playlistRow(p){
    var r = songRow({cover:libCover(p.cover_id, 120), title:p.name,
      sub:p.made && p.comment ? sub(p.comment.replace(/^Made by Auxx?( In)?\s*·\s*/, ''), (p.songs || 0) + ' songs') : (p.songs || 0) + ' songs',
      onTap:function(){ location.hash = '#library/playlist/' + p.id; },
      buttons:[btn('⤮', 'icon ghost', libQueue({playlist_id:p.id}, 'end', {shuffle:true}), 'Shuffle into queue')]});
    if (p.made){ var m = el('span', 'made', '✦'); m.title = 'Made by Auxx-In'; r.querySelector('.cv').appendChild(m); }
    return r;
  }
  var plFilter = 'all';
  function libAlbum(v, id){
    topbar(v, 'Album', '#library');
    var box = el('div'); v.appendChild(box); box.appendChild(el('div', 'empty', 'Loading…'));
    api('lib/album/' + encodeURIComponent(id)).then(function(a){
      box.textContent = '';
      if (a.error){ box.appendChild(el('div', 'empty', a.error)); return; }
      v.querySelector('h1').textContent = a.name;
      var head = el('div', 'row'); head.style.border = '0'; head.style.background = 'transparent'; head.style.padding = '0';
      var cv = bg(el('div', 'cv'), libCover(a.cover_id, 300)); cv.style.width = cv.style.height = '120px'; head.appendChild(cv);
      var tx = el('div', 'tx'); tx.appendChild(el('div', 't', a.name)); var ar = el('div', 's', sub(a.artist, a.year, (a.songs || []).length + ((a.songs || []).length === 1 ? ' song' : ' songs')));
      ar.style.cursor = 'pointer'; ar.onclick = function(){ if (a.artist_id) location.hash = '#library/artist/' + a.artist_id; };
      tx.appendChild(ar); head.appendChild(tx); box.appendChild(head);
      box.appendChild(collectionButtons({album_id:a.id}));
      var l = el('div', 'list'); a.songs.forEach(function(s){ l.appendChild(libSongRow(s)); }); box.appendChild(l);
    });
  }
  function libPlaylist(v, id){
    topbar(v, 'Playlist', '#library');
    var box = el('div'); v.appendChild(box); box.appendChild(el('div', 'empty', 'Loading…'));
    api('lib/playlist/' + encodeURIComponent(id)).then(function(p){
      box.textContent = '';
      if (p.error){ box.appendChild(el('div', 'empty', p.error)); return; }
      v.querySelector('h1').textContent = p.name;
      box.appendChild(el('div', 's', p.songs.length + ' songs'));
      box.appendChild(collectionButtons({playlist_id:p.id}));
      var l = el('div', 'list'); p.songs.forEach(function(s){ l.appendChild(libSongRow(s)); }); box.appendChild(l);
    });
  }
  function libArtist(v, id){
    topbar(v, 'Artist', '#library');
    var box = el('div'); v.appendChild(box); box.appendChild(el('div', 'empty', 'Loading…'));
    api('lib/artist/' + encodeURIComponent(id)).then(function(a){
      box.textContent = '';
      if (a.error){ box.appendChild(el('div', 'empty', a.error)); return; }
      v.querySelector('h1').textContent = a.name;
      box.appendChild(collectionButtons({artist_id:a.id}));
      box.appendChild(el('h2', null, 'Albums'));
      var g = el('div', 'grid'); a.albums.forEach(function(x){ g.appendChild(albumTile(x)); }); box.appendChild(g);
    });
  }

  // ------------------------------------------------------------------ Up next (the queue, inside Now)
  var BADGE = {pending:['waiting', ''], resolving:['finding', 'warn'], downloading:['downloading', 'warn'],
               ready:['ready', 'good'], loaded:['next', 'good'], failed:['failed', 'bad']};
  var qBusy = false;  // a drag or swipe is under way: don't repaint the list under the finger
  renderers.queue = function(){ location.replace('#now/next'); };  // old links
  function queueItemSheet(it){
    sheet({cover:it.cover, title:it.title, sub:sub(it.artist, it.album), options:[
      (it.status === 'ready' || it.status === 'loaded') ? ['▶  Play now', function(){ post('play/' + it.id).then(refresh); }] : null,
      ['⤒  Move to the top', function(){ post('move/' + it.id, {to:'top'}).then(refresh); }],
      ['⤓  Move to the bottom', function(){ post('move/' + it.id, {to:'bottom'}).then(refresh); }],
      it.status === 'failed' ? ['↻  Try again', function(){ post('retry/' + it.id).then(refresh); }] : null,
      (it.path || it.nd_id) ? ['⬇  Download', function(){ location.href = 'api/file/' + it.id; }] : null,
      ['✕  Remove from queue', function(){ removeItem(it); }]
    ]});
  }
  function playFrom(it){
    if (it.status !== 'ready' && it.status !== 'loaded'){ queueItemSheet(it); return; }
    post('play/' + it.id, {drop_before:true}).then(function(d){
      if (d.ok) toast('Playing ' + (it.title || 'that song') + '.'); else fail();
      refresh();
    }, fail);
  }
  function removeItem(it){
    S.queue = S.queue.filter(function(x){ return x.id !== it.id; });
    api('item/' + it.id, {method:'DELETE'}).then(function(){ toast('Removed ' + (it.title || 'the song') + '.'); refresh(); }, fail);
  }
  function renderQueue(){
    var box = $('queueBox'); if (!box || !S || qBusy) return;
    box.textContent = '';
    var l = el('div', 'list qlist');
    S.queue.forEach(function(it){
      var b = BADGE[it.status] || [it.status, ''];
      if (it.by === 'iq' && (it.status === 'ready' || it.status === 'loaded')) b = ['✦ IQ', 'iq'];
      var handle = el('span', 'handle', '≡'); handle.setAttribute('aria-label', 'Drag to move'); handle.title = 'Drag to move';
      var r = songRow({cover:it.cover, title:it.title, badge:b[0], badgeCls:b[1],
        sub:it.status === 'failed' ? it.error : it.by === 'iq' ? sub(it.artist, it.why ? '✦ ' + it.why : null) :
          sub(it.artist, it.album, it.by && it.by !== 'you' ? 'by ' + it.by : null),
        progress:it.status === 'downloading' ? (it.progress || 0) : null,
        onTap:function(){ if (!r.dataset.swiped) playFrom(it); },
        buttons:[btn('⋯', 'icon ghost', function(){ queueItemSheet(it); }, 'More'), handle]});
      r.dataset.id = it.id;
      swipeToRemove(r, function(){ removeItem(it); });
      dragHandle(handle, r, l);
      l.appendChild(r);
    });
    box.appendChild(l);
    if (!S.queue.length) box.appendChild(el('div', 'empty', 'Nothing queued. Search or browse your library to add songs.'));
    else box.appendChild(el('div', 'hint-line', 'Drag ≡ to reorder · swipe left to remove'));
    box.appendChild(el('h2', null, 'Played'));
    var pl = el('div', 'list');
    (S.history || []).slice(1, 16).forEach(function(it){
      var again = it.nd_id && !it.path ? libQueue({ids:[it.nd_id]}, 'end') :
        it.path ? function(){ post('queue-path', {path:it.path, where:'end'}).then(done, fail); } : null;
      pl.appendChild(songRow({cover:it.cover, title:it.title, sub:sub(it.artist, it.album),
        buttons:[again ? btn('↺', 'icon ghost', again, 'Queue again') : null]}));
    });
    box.appendChild(pl);
  }
  // Drag a row by its ≡ handle; the others slide out of the way.
  function dragHandle(handle, row, list){
    var d = null;
    function pageY(e){ return e.clientY + window.scrollY; }
    handle.addEventListener('pointerdown', function(e){
      e.preventDefault();
      var rows = Array.prototype.slice.call(list.children);
      var rects = rows.map(function(x){ var b = x.getBoundingClientRect(); return {top:b.top + window.scrollY, h:b.height}; });
      var from = rows.indexOf(row);
      var gap = rows.length > 1 ? (rects[1].top - rects[0].top - rects[0].h) : 6;
      d = {rows:rows, rects:rects, from:from, to:from, y0:pageY(e), step:rects[from].h + gap};
      qBusy = true;
      try { handle.setPointerCapture(e.pointerId); } catch (x) {}
      row.classList.add('dragging');
    });
    handle.addEventListener('pointermove', function(e){
      if (!d) return;
      if (e.clientY < 70) window.scrollBy(0, -10);
      else if (e.clientY > window.innerHeight - 90) window.scrollBy(0, 10);
      var dy = pageY(e) - d.y0;
      row.style.transform = 'translateY(' + dy + 'px)';
      var c = d.rects[d.from].top + d.rects[d.from].h / 2 + dy, to = d.from;
      for (var i = 0; i < d.rects.length; i++){
        var mid = d.rects[i].top + d.rects[i].h / 2;
        if (i < d.from && c < mid){ to = i; break; }
        if (i > d.from && c > mid) to = i;
      }
      d.to = to;
      d.rows.forEach(function(x, i){
        if (i === d.from) return;
        var s = d.from < to && i > d.from && i <= to ? -d.step : to < d.from && i >= to && i < d.from ? d.step : 0;
        x.style.transform = s ? 'translateY(' + s + 'px)' : '';
      });
    });
    function end(){
      if (!d) return;
      var x = d; d = null;
      row.classList.remove('dragging');
      x.rows.forEach(function(r){ r.style.transform = ''; });
      if (x.to !== x.from){
        list.insertBefore(row, x.to > x.from ? x.rows[x.to].nextSibling : x.rows[x.to]);
        var it = S.queue.splice(x.from, 1)[0]; S.queue.splice(x.to, 0, it);
        post('move/' + row.dataset.id, {index:x.to}).then(function(){ qBusy = false; refresh(); }, function(){ qBusy = false; fail(); refresh(); });
      } else qBusy = false;
    }
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }
  // Swipe a row to the left to take it out of the queue.
  function swipeToRemove(row, fn){
    var x0 = null, y0 = 0, dx = 0, on = false;
    row.addEventListener('pointerdown', function(e){
      if (e.target.closest('button, .handle') || (e.pointerType === 'mouse' && e.button)) return;
      x0 = e.clientX; y0 = e.clientY; dx = 0; on = false;
    });
    row.addEventListener('pointermove', function(e){
      if (x0 === null) return;
      dx = e.clientX - x0;
      if (!on){
        if (dx < -12 && Math.abs(dx) > Math.abs(e.clientY - y0) * 1.5){ on = true; qBusy = true; try { row.setPointerCapture(e.pointerId); } catch (x) {} }
        else if (Math.abs(e.clientY - y0) > 12 || dx > 12){ x0 = null; return; }
      }
      if (on){ var m = Math.min(0, dx); row.style.transform = 'translateX(' + m + 'px)'; row.style.opacity = String(Math.max(.3, 1 + m / 260)); }
    });
    function up(){
      if (x0 === null) return;
      x0 = null;
      if (!on) return;
      on = false; row.dataset.swiped = '1'; setTimeout(function(){ row.dataset.swiped = ''; }, 300);
      if (dx < -90){ row.style.transform = 'translateX(-110%)'; row.style.opacity = '0'; setTimeout(function(){ qBusy = false; row.remove(); fn(); }, 160); }
      else { row.style.transform = ''; row.style.opacity = ''; qBusy = false; }
    }
    row.addEventListener('pointerup', up);
    row.addEventListener('pointercancel', up);
  }

  // ------------------------------------------------------------------ Downloads
  var dlTimer = null;
  renderers.downloads = function(v){
    topbar(v, 'Downloads', null, [btn('Clear failed', 'sm', function(){ post('downloads/clear', {what:'failed'}).then(loadDownloads); }),
                                  btn('Clear list', 'sm', function(){ if (confirm('Clear the list? Songs stay in your library.')) post('downloads/clear', {what:'saved'}).then(loadDownloads); })]);
    var box = el('div'); box.id = 'dlBox'; v.appendChild(box);
    loadDownloads();
  };
  function loadDownloads(){
    clearTimeout(dlTimer);
    if (route().tab !== 'downloads') return;
    api('downloads').then(function(d){
      var box = $('dlBox'); if (!box) return;
      box.textContent = '';
      box.appendChild(el('h2', null, 'In progress'));
      var a = el('div', 'list');
      d.active.forEach(function(it){
        var b = BADGE[it.status] || [it.status, ''];
        a.appendChild(songRow({cover:it.cover, title:it.title || 'Finding…', badge:b[0], badgeCls:b[1],
          sub:it.status === 'failed' ? (it.error || 'failed') : sub(it.artist, it.album, it.status === 'downloading' ? Math.round(it.progress || 0) + '%' : null),
          progress:it.status === 'downloading' ? (it.progress || 0) : null,
          buttons:[it.status === 'failed' ? btn('↻', 'icon ghost', function(){ post('retry/' + it.id).then(loadDownloads); }, 'Try again') : null,
                   btn('✕', 'icon ghost', function(){ api('item/' + it.id, {method:'DELETE'}).then(loadDownloads); }, 'Remove')]}));
      });
      box.appendChild(a);
      if (!d.active.length) box.appendChild(el('div', 'empty', 'Nothing downloading.'));
      box.appendChild(el('h2', null, 'Saved by Auxx-In'));
      var s = el('div', 'list');
      d.saved.forEach(function(it){
        function q(where){ return function(){ post('queue-path', {path:it.path, where:where}).then(done, fail); }; }
        function del(){
          if (confirm('Delete "' + it.title + '" from your music library? This removes the file.'))
            post('delete-file/' + it.id).then(function(r){ toast(r.say); loadDownloads(); });
        }
        var tap = !it.exists || !it.path ? null : function(){
          sheet({cover:it.cover, title:it.title, sub:sub(it.artist, it.album), options:[
            ['▶  Play now', q('now')], ['⤴  Play next', q('next')], ['＋  Add to queue', q('end')],
            ['⬇  Download MP3', function(){ location.href = 'api/file/' + it.id; }],
            ME && ME.admin ? ['🗑  Delete file', del] : null]});
        };
        s.appendChild(songRow({cover:it.cover, title:it.title, onTap:tap,
          sub:sub(it.artist, it.album, size(it.size), S && S.navidrome === false ? 'saved' : it.in_navidrome ? 'in Navidrome' : 'adding to Navidrome…', !it.exists ? 'file missing' : null),
          buttons:[it.exists ? btn('⬇', 'icon ghost', function(){ location.href = 'api/file/' + it.id; }, 'Download') : null,
                   !(ME && ME.admin) ? null : btn('🗑', 'icon ghost', del, 'Delete file')]}));
      });
      box.appendChild(s);
      if (!d.saved.length) box.appendChild(el('div', 'empty', 'Nothing yet.'));
      dlTimer = setTimeout(loadDownloads, d.active.some(function(i){ return i.status !== 'failed'; }) ? 1500 : 6000);
    });
  }

  // ------------------------------------------------------------------ start
  api('me').then(function(me){
    if (!me.user){ location.replace('login'); return; }
    ME = me.user; CSRF = me.csrf;
    return refresh().then(render);
  });
  setInterval(function(){ if (!document.hidden) refresh(); }, 3000);
  document.addEventListener('visibilitychange', function(){ if (!document.hidden) refresh(); });
})();
