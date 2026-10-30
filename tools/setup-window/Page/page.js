"use strict";
/* The Estia Setup window's page. Setup (the engine, started in Terminal) sends one message at a time through the
   window (window.estia.receive); the page draws each question by its kind and posts back what was typed or picked
   (the "estia" handler). It never checks, keeps or logs an answer: setup does, and says back why one was refused.
   Every step runs in the window (owner, 29 Oct): its lines in the log under "Step n of m", its questions as screens;
   admin rights come from macOS's own box, which the window's admin runner asks for: the page never sees an admin
   command and has no password field for one. What the window does for the page itself (a folder picker, Copy,
   Terminal at the hand-over, Close) goes to the "native" handler.
   The intro (the wordmark, af_heart's recording with its captions, the moving ring) is the approved preview's, 26 Oct.
   No markup is ever built from text: every word is set as text. */
(() => {
  const $ = (id) => document.getElementById(id);
  const handlers = (window.webkit && window.webkit.messageHandlers) || {};
  const post = (message) => { if (handlers.estia) handlers.estia.postMessage(message); };
  const native = (message) => { if (handlers.native) handlers.native.postMessage(message); };

  // the page's own words (every other word comes from setup)
  const WORDS = {
    play: "Play intro", pause: "Pause", resume: "Resume", next: "Continue", skip: "Skip", again: "Play again",
    skipped: "Intro skipped.", noPlay: "The intro could not play here.", noLoad: "The intro could not load.",
    yes: "Yes", no: "No", folder: "Choose a folder", copy: "Copy", copied: "Copied.", change: "Change",
    keep: "Keep", asked: "asked again", terminal: "Switch to Terminal", close: "Close",
    // setup's end without its last word (a Ctrl-C in Terminal, a crash, Terminal closed): never as if it were done
    // (owner, 29 Oct); the window looks like the installer, so Terminal may have been closed (round 1), and an AI-run
    // has no Terminal it was started in (review W12): both ways to start again, the command with Copy
    ended: "Setup stopped without its last word; every answer is kept. "
      + "To go on, start it again: ask your AI, or run this in Terminal:\n$ cd ~/estia && scripts/setup.sh",
    waiting: "Waiting, no need to press anything.", steps: " Steps below.", recommended: "Recommended",
    more: "More", less: "Less", stopHere: "Stop here", answerBefore: "Your answer: ",
    stopped: "Stopped. Your answers are kept; running scripts/setup.sh again goes on.",
  };
  const of = (n, m) => `${n} of ${m}`;
  const groupOf = (n, m) => `Group ${n} of ${m}`;
  const LIVE = new Set(["text", "number", "hidden", "pin"]);   // checked by setup while typing (150 ms after a key)
  const OTHER = "other";                                         // an option whose answer is typed
  // what a typed line holds at most: a captcha link fits, and a line to setup stays under its 64 KiB
  const LINE_MAX = 16384, NAME_MAX = 256;

  const audio = new Audio();
  const ring = $("ring"), caption = $("caption"), statusLine = $("status"), live = $("live");
  const scroller = $("body"), counter = $("count"), introPanel = $("p-intro"), panel = $("p-screen");
  const backBtn = $("back"), muteBtn = $("mute"), altBtn = $("alt"), mainBtn = $("main");
  const altRec = $("alt-rec"), mainRec = $("main-rec");   // the small line under a button (owner, 27 Oct)
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let still = motion.matches;
  let introState = "idle";   // idle | playing | paused | ended | skipped
  let introDone = false, level = 0, raf = 0, last = 0, shown = -1;

  function say(msg) { live.textContent = ""; setTimeout(() => { live.textContent = msg; }, 30); }

  /* ------------------------------------------------------------------ the intro: loudness and captions */
  function loudness(t) {
    const i = t / HOP, a = Math.floor(i), f = i - a;
    const x = ENV[a] ?? 0, y = ENV[a + 1] ?? x;
    return x + (y - x) * f;
  }
  function sentenceAt(t) {
    let k = 0;
    for (let i = 0; i < SENTENCES.length; i++) if (t >= SENTENCES[i][0]) k = i;
    return k;
  }
  const TAGLINE = "An insanely secure & private thinking partner, living at home.";
  function showTagline() { shown = -1; caption.textContent = TAGLINE; }
  function showCaption(k) {
    if (k === shown) return;
    shown = k;
    caption.textContent = SENTENCES[k][1];
    caption.classList.remove("swap");
    void caption.offsetWidth;
    caption.classList.add("swap");
  }

  /* The mark. The square never moves; the white disc is fixed; the ring is the line.
     A write head starts at 12 o'clock and moves clockwise while she speaks (one turn in 4.5 s). Each frame the sound just
     played is written at the head's angle and stays there, so the trace grows around the circle like a recording; arcs
     the head has not reached are the plain circle. After 200 ms of quiet (a pause) the whole trace sinks back into the
     circle together (300 ms, eased), the ring is plain again, and the head returns to 12 o'clock for the next stretch of
     speech. A stretch longer than one turn keeps writing over the start. The ring itself never bends: at rest it is 7
     wide, and while a wave is drawn it stays a static line (4.5) under the wave (1.9).
     Web Audio: clip -> AnalyserNode (2048 samples) -> Mute gain -> speakers. The newest samples are pre-emphasised so the
     line is sharp; each of the 130 points keeps the strongest sample written to it and shows it as a spike (about 7
     units at the loudest, on the 100 grid). Loudness is the same pre-emphasised peak against the clip's loud-speech
     level. No Web Audio: the audio element plays the clip and the same head follows the measured loudness (ENV):
     spike height = loudness. Reduced motion: a still mark. */
  const R0 = 42.25, TAU = Math.PI * 2;
  const FAT = 7, THIN = 4.5;   // the ring: 7 at rest, a thin static line while a wave is drawn on top of it
  const waveEl = $("wave");
  const smooth = (x) => { const c = Math.min(1, Math.max(0, x)); return c * c * (3 - 2 * c); };

  const FFT = 2048, PRE = 0.95, MAXD = 7, FLOOR = 0.3;   // below FLOOR of loud speech a point stays on the circle
  const NP = 130, TURN = 4.5, HOLD = 0.2, FADE = 0.3, GATE = 0.04;   // points, seconds per turn, quiet, fade, gate
  const wave = new Float32Array(FFT);
  const rec = new Float32Array(NP), has = new Uint8Array(NP), gone = new Float32Array(NP), line = new Float32Array(NP);
  const COS = Array.from({ length: NP }, (_, i) => Math.cos(i * TAU / NP - Math.PI / 2));   // point 0: 12 o'clock
  const SIN = Array.from({ length: NP }, (_, i) => Math.sin(i * TAU / NP - Math.PI / 2));
  let ref = 0.12, act = 0, alive = false, plain = true;   // ref: loud-speech level of the clip, measured when decoded
  let head = 0, tip = -1, writing = false, quiet = 0, fade = 0, fadeT = 0;   // head in turns; fade: 1 to 0

  const spike = (u) => { const x = Math.min(1, Math.max(0, (Math.abs(u) / ref - FLOOR) / (1 - FLOOR))); return Math.sign(u) * MAXD * Math.pow(x, 1.3); };
  const hp = (j) => wave[j] - PRE * (j ? wave[j - 1] : wave[j]);   // pre-emphasised sample
  const jag = (i) => {                                              // fallback only: a fixed jagged pattern per point
    const a = Math.sin(i * 12.9898) * 43758.5453, b = Math.sin(i * 78.233) * 12345.6789;
    return (a - Math.floor(a) < 0.5 ? -1 : 1) * (0.4 + 0.6 * (b - Math.floor(b)));
  };

  function put(i, u) {                                              // a point keeps the strongest sample it gets
    if (i !== tip) { tip = i; rec[i] = u; has[i] = 1; }
    else if (Math.abs(u) > Math.abs(rec[i])) rec[i] = u;
  }
  function startTrace() { rec.fill(0); has.fill(0); head = 0; tip = -1; quiet = 0; writing = true; }
  function liftTrace() {                                            // a pause: everything written so far fades together
    if (!writing) return;
    writing = false;
    for (let i = 0; i < NP; i++) gone[i] = has[i] ? spike(rec[i]) : gone[i] * fade;
    has.fill(0);
    fade = 1; fadeT = 0;
  }
  function clearTrace() {
    writing = false; has.fill(0); gone.fill(0); fade = 0; act = 0; level = 0; alive = false; plain = true;
    waveEl.setAttribute("d", ""); ring.setAttribute("stroke-width", FAT);
  }
  function tracePath() {
    for (let i = 0; i < NP; i++) line[i] = has[i] ? spike(rec[i]) : gone[i] * fade;
    for (let i = 1; i < NP; i += 2) line[i] = -0.55 * Math.abs(line[i]);   // every other point inward: teeth
    for (let i = 0; i < NP; i += 2) line[i] = Math.abs(line[i]);
    const X = new Float32Array(NP), Y = new Float32Array(NP);
    for (let i = 0; i < NP; i++) { const r = R0 + line[i]; X[i] = 50 + r * COS[i]; Y[i] = 50 + r * SIN[i]; }
    // a closed Catmull-Rom curve through the points: every tooth gets a rounded tip and rounded valleys
    const f = (v) => v.toFixed(2);
    let d = "M" + f(X[0]) + " " + f(Y[0]);
    for (let i = 0; i < NP; i++) {
      const a = (i + NP - 1) % NP, b = i, c = (i + 1) % NP, e = (i + 2) % NP;
      const c1x = X[b] + (X[c] - X[a]) / 6, c1y = Y[b] + (Y[c] - Y[a]) / 6;
      const c2x = X[c] - (X[e] - X[b]) / 6, c2y = Y[c] - (Y[e] - Y[b]) / 6;
      d += "C" + f(c1x) + " " + f(c1y) + " " + f(c2x) + " " + f(c2y) + " " + f(X[c]) + " " + f(Y[c]);
    }
    return d + "Z";
  }
  function drawTrace(dt, on) {
    let lv = 0, K = 0;
    if (on) {
      if (mode === "web") {
        analyser.getFloatTimeDomainData(wave);
        K = Math.min(FFT, Math.max(1, Math.round(dt * ctx.sampleRate)));   // the samples played since the last frame
        for (let j = FFT - K; j < FFT; j++) lv = Math.max(lv, Math.abs(hp(j)));
        lv /= ref;
      } else lv = loudness(clipTime() + 0.03);
    }
    level = lv;
    if (!on) liftTrace();
    else if (lv > GATE) { quiet = 0; if (!writing) startTrace(); }
    else if (writing) { quiet += dt; if (quiet >= HOLD) liftTrace(); }
    if (writing) {
      const h1 = head + dt / TURN;
      if (head >= 1 && lv <= GATE) { /* past a full turn only sound is written: the quiet tail never wipes the start */ }
      else if (mode === "web") {
        for (let j = FFT - K; j < FFT; j++) put(Math.floor(((head + (j - FFT + K + 0.5) / K * (h1 - head)) % 1) * NP), hp(j));
      } else {
        for (let p = Math.floor(head * NP), e = Math.floor(h1 * NP); p <= e; p++) put(p % NP, jag(p % NP) * lv * ref);
      }
      head = h1;
    }
    act += ((writing ? 1 : 0) - act) * (1 - Math.exp(-dt / 0.05));
    if (fade > 0) { fadeT += dt; fade = fadeT >= FADE ? 0 : 1 - smooth(fadeT / FADE); }
    if (!writing && fade === 0) {
      act = 0; alive = false;
      if (!plain) { plain = true; waveEl.setAttribute("d", ""); ring.setAttribute("stroke-width", FAT); }
      return;
    }
    alive = true; plain = false;
    ring.setAttribute("stroke-width", (FAT - (FAT - THIN) * Math.max(writing ? act : 0, fade)).toFixed(2));
    waveEl.setAttribute("d", tracePath());
  }

  function frame(now) {
    const dt = Math.min(0.25, (now - last) / 1000);   // real elapsed time: slow frames keep the clock
    last = now;
    const on = clipOn();
    if (on) showCaption(sentenceAt(clipTime()));
    if (!still) drawTrace(dt, on);
    raf = on || (!still && alive) ? requestAnimationFrame(frame) : 0;
  }
  function start() { if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); } }
  motion.addEventListener("change", (e) => {
    still = e.matches;
    if (still) clearTrace();
    else start();
  });

  /* The clip: af_heart's recording, the page's own (intro-clip.js: CLIP, the approved preview's, byte for byte). Web
     Audio plays it from the decoded samples (the ring reads the same samples); the audio element, from a blob of the
     same bytes, is the fallback. Nothing is loaded for it. It plays only on a click. */
  const AC = window.AudioContext || window.webkitAudioContext;
  let mode = AC ? "web" : "tag";                       // web | tag
  let ctx = null, buf = null, analyser = null, gain = null, node = null, clip = null;
  let isOn = false, pos = 0, t0 = 0, muted = false, loading = false, blobbed = false;
  const clipOn = () => mode === "web" ? isOn : !audio.paused;
  const clipTime = () => mode === "web" ? (isOn ? Math.min(buf.duration, pos + ctx.currentTime - t0) : pos) : audio.currentTime;

  async function clipBytes() {
    if (!clip) clip = Uint8Array.from(atob(CLIP.slice(CLIP.indexOf(",") + 1)), (c) => c.charCodeAt(0)).buffer;
    return clip.slice(0);                              // decodeAudioData takes its buffer: a copy each time
  }
  function measure(x) {                                // 98th percentile of the pre-emphasised clip
    const v = new Float32Array(Math.ceil(x.length / 3));
    for (let i = 1, k = 0; i < x.length; i += 3, k++) v[k] = Math.abs(x[i] - PRE * x[i - 1]);
    v.sort();
    return v[Math.floor(v.length * 0.98)] || 0.1;
  }
  function begin() {
    const s = ctx.createBufferSource();
    s.buffer = buf;
    s.connect(analyser);
    s.onended = () => { if (node !== s) return; node = null; isOn = false; pos = 0; onEnd(); };
    s.start(0, pos);
    node = s; t0 = ctx.currentTime; isOn = true;
    onPlay();
  }
  function halt() {                                    // stop the source, keep the place
    if (!node) return;
    pos = clipTime();
    node.onended = null;
    try { node.stop(); } catch { /* already stopped */ }
    node.disconnect();
    node = null; isOn = false;
  }
  async function playWeb(again) {
    if (!ctx) {
      ctx = new AC();
      analyser = ctx.createAnalyser();
      analyser.fftSize = FFT;
      analyser.smoothingTimeConstant = 0;
      gain = ctx.createGain();
      gain.gain.value = muted ? 0 : 1;
      analyser.connect(gain);
      gain.connect(ctx.destination);
    }
    const wake = ctx.state === "running" ? null : ctx.resume();   // called inside the click, before any await
    if (!buf) { buf = await ctx.decodeAudioData(await clipBytes()); ref = measure(buf.getChannelData(0)); }
    if (wake) await wake;
    if (again || pos >= buf.duration) pos = 0;
    begin();
  }
  async function useBlob() {
    if (blobbed) return true;
    try {
      audio.src = URL.createObjectURL(new Blob([await clipBytes()], { type: "audio/mp4" }));
      blobbed = true;
      return true;
    } catch { return false; }
  }
  async function playTag(again) {
    if (!(await useBlob())) { statusLine.textContent = WORDS.noLoad; return; }
    if (again || audio.ended) audio.currentTime = 0;
    try { await audio.play(); } catch { statusLine.textContent = WORDS.noPlay; }
  }
  async function play() {
    if (loading) return;
    statusLine.textContent = "";
    const again = introState === "ended" || introState === "skipped";
    if (mode === "web") {
      loading = true;
      try { await playWeb(again); loading = false; return; }
      catch { mode = "tag"; loading = false; }         // no Web Audio here: the audio element and the ENV ring
    }
    await playTag(again);
  }
  function pauseClip() {
    if (mode === "web") { if (isOn) { halt(); onPause(); } }
    else audio.pause();
  }
  function setIntro(state) {
    introState = state;
    caption.setAttribute("aria-live", state === "playing" ? "off" : "polite");   // silent while the clip plays
    if (!introDone) introActions();
  }
  function skip() {
    if (mode === "web") { halt(); pos = 0; } else { audio.pause(); audio.currentTime = 0; }
    setIntro("skipped");
    showTagline();
    say(WORDS.skipped);
    start();
  }
  function onPlay() { setIntro("playing"); start(); }
  function onPause() { if (introState === "playing") setIntro("paused"); start(); }
  function onEnd() { setIntro("ended"); showCaption(SENTENCES.length - 1); start(); }
  audio.addEventListener("play", onPlay);
  audio.addEventListener("pause", () => { if (!audio.ended) onPause(); });
  audio.addEventListener("ended", onEnd);
  muteBtn.addEventListener("click", () => {
    muted = !muted;
    audio.muted = muted;
    if (gain) gain.gain.setTargetAtTime(muted ? 0 : 1, ctx.currentTime, 0.015);
    muteBtn.setAttribute("aria-pressed", String(muted));   // the label stays "Mute"; aria-pressed carries the state
  });
  clipBytes().catch(() => { /* said when played */ });

  /* ------------------------------------------------------------------ the action row */
  let onMain = null, onAlt = null, onBack = null;
  function actions({ main = null, alt = null, back = null, mute = false } = {}) {
    onMain = main && main.run; onAlt = alt && alt.run; onBack = back;
    mainBtn.hidden = !main;
    if (main) { label(mainBtn, main); mainBtn.disabled = Boolean(main.disabled); }
    mainRec.textContent = unmarked((main && main.rec) || "");
    altBtn.hidden = !alt;
    if (alt) { label(altBtn, alt); altBtn.disabled = false; }
    altRec.textContent = unmarked((alt && alt.rec) || "");
    backBtn.hidden = !back;
    backBtn.disabled = false;
    muteBtn.hidden = !mute;
  }
  function label(button, action) {         // a button's label, and its Recommended pill (a yes or no)
    button.replaceChildren(el("span", { class: "label", text: unmarked(action.label) }), action.tag ? pill(action) : "");
  }
  mainBtn.addEventListener("click", () => { if (onMain && !mainBtn.disabled) onMain(); });
  altBtn.addEventListener("click", () => { if (onAlt) onAlt(); });
  backBtn.addEventListener("click", () => { if (onBack) onBack(); });

  function introActions() {
    const main = {
      idle: WORDS.play, playing: WORDS.pause, paused: WORDS.resume, ended: WORDS.next, skipped: WORDS.next,
    }[introState];
    const alt = introState === "ended" ? WORDS.again : introState === "skipped" ? WORDS.play : WORDS.skip;
    actions({
      main: {
        label: main,
        run: () => {
          if (introState === "playing") pauseClip();
          else if (introState === "ended" || introState === "skipped") leaveIntro();
          else play();
        },
      },
      alt: {
        label: alt,
        run: () => { if (introState === "ended" || introState === "skipped") play(); else skip(); },
      },
      mute: introState === "playing",
    });
  }

  /* ------------------------------------------------------------------ building blocks (text only, never markup) */
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children) if (child) node.append(child);
    return node;
  }
  /* setup's words, as Terminal shows them, drawn for the window (owner, 27 Oct: "leave some blank rows between text
     paragraphs"): a blank line starts a paragraph; inside one, the lines join (Terminal keeps them short); a list's
     lines ("1. ", "• ") are its items, a line indented under an item goes on with it; a line "$ …" is a command; a
     word between ** is bold, an https address a link (opened in the Mac's browser). Every word is set as text. A
     mark is read as setup reads it (setup_questions.BOLD: a pair on one line, no * inside); where a word is drawn as
     text only (a heading, a button, a small line, a field's label, an answer), `unmarked` leaves the marks out. */
  const ITEM = /^\s*(\d+\.|•|-) (.*)$/;
  const LINK = /https:\/\/[^\s)]+[^\s).,;:]/g;
  const MARK = /\*\*([^*\n]+)\*\*/g;
  const unmarked = (text) => String(text || "").replace(MARK, "$1");
  function linked(node, part) {               // a part's https links as their own nodes
    let from = 0;
    for (const found of part.matchAll(LINK)) {
      node.append(document.createTextNode(part.slice(from, found.index)), el("a", { href: found[0], text: found[0] }));
      from = found.index + found[0].length;
    }
    node.append(document.createTextNode(part.slice(from)));
  }
  function rich(node, text) {                 // text into `node`: **bold** words and https links as their own nodes
    const words = String(text || "");
    let from = 0;
    for (const found of words.matchAll(MARK)) {
      linked(node, words.slice(from, found.index));
      node.append(el("strong", { text: found[1] }));
      from = found.index + found[0].length;
    }
    linked(node, words.slice(from));
    return node;
  }
  function blocks(text, className) {
    const out = [];
    for (const block of (text || "").split(/\n\s*\n/)) {
      const lines = block.split("\n").filter((line) => line.trim());
      if (!lines.length) continue;
      let list = null, item = null, words = [];
      const flush = () => { if (words.length) out.push(rich(el("p", { class: className }), words.join(" "))); words = []; };
      for (const line of lines) {
        const listed = ITEM.exec(line);
        if (line.trim().startsWith("$ ")) { flush(); list = item = null; out.push(commandRow(line.trim())); }
        else if (listed) {
          flush();
          const ordered = /\d/.test(listed[1]);
          if (!list || (list.tagName === "OL") !== ordered) { list = el(ordered ? "ol" : "ul", { class: `${className} steps` }); out.push(list); }
          item = rich(el("li"), listed[2]);
          list.append(item);
        } else if (item && /^\s{3,}/.test(line)) item.append(document.createTextNode(" "), rich(el("span"), line.trim()));
        else { list = item = null; words.push(line.trim()); }
      }
      flush();
    }
    return out;
  }
  function paragraphs(text, className) { return blocks(text, className); }
  /* a command setup shows for typing by hand, with Copy (owner, 29 Oct: "add this little copy icon"): Copy takes it
     without its "$ " */
  function commandRow(line) {
    const command = line.trim().slice(2);
    const copy = el("button", { class: "small", type: "button", text: WORDS.copy, "aria-label": `${WORDS.copy}: ${command}` });
    copy.addEventListener("click", () => { native({ t: "copy", text: command }); say(WORDS.copied); });
    return el("div", { class: "cmd-row" }, el("pre", { class: "cmd", text: line.trim() }), copy);
  }
  /* the small pill on the option a question recommends (owner, 29 Oct: "'recommended:' with below the YES looks
     cheap"): on the option itself, at its right, the page's accent colour */
  const pill = (option) => option && option.tag ? el("span", { class: "pill", text: unmarked(option.tag) }) : null;
  function guidePanel(option, id) {
    const guide = option.guide;
    if (!guide) return null;
    const pad = el("div", { class: "pad" });
    if (guide.needs) pad.append(rich(el("p"), guide.needs));
    if (guide.steps.length) pad.append(el("ol", {}, ...guide.steps.map((step) => rich(el("li"), step))));
    if (guide.check) pad.append(rich(el("p"), guide.check));
    for (const [says, fix] of guide.errors) pad.append(el("p", {}, el("strong", { text: unmarked(says) }), rich(el("span"), " " + fix)));
    if (guide.links.length) {
      pad.append(el("ul", {}, ...guide.links.map(([label, href]) => el("li", {}, el("a", { href, text: unmarked(label) })))));
    }
    const more = el("div", { class: "more", id }, el("div", { class: "clip" }, pad));
    more.inert = true;
    return more;
  }
  function setMore(more, open) { if (!more) return; more.classList.toggle("open", open); more.inert = !open; }
  function moreFold(text) {                  // the details, folded under "More" (owner, 27 Oct: "security lots of text")
    const pad = el("div", { class: "pad" }, ...blocks(text, "help"));
    const more = el("div", { class: "more", id: "more-fold" }, el("div", { class: "clip" }, pad));
    more.inert = true;
    const toggle = el("button", { class: "ghost fold", type: "button", "aria-expanded": "false", "aria-controls": "more-fold", text: WORDS.more });
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? WORDS.less : WORDS.more;
      setMore(more, open);
      if (open) reveal(more);
    });
    return [el("div", {}, toggle), more];
  }
  function reveal(more) { setTimeout(() => more.scrollIntoView({ block: "nearest", behavior: still ? "auto" : "smooth" }), 190); }
  function keptText(screen) {
    const value = screen.kept;
    const label = (id) => (screen.options.find((option) => option.id === id) || { label: id }).label;
    if (typeof value === "boolean") return value ? WORDS.yes : WORDS.no;
    if (typeof value === "string") return unmarked(label(value));
    if (Array.isArray(value)) return value.map((id) => unmarked(label(id))).join(", ");
    return Object.values(value).filter(Boolean).join(", ");
  }

  /* ------------------------------------------------------------------ screens, by kind */
  let current = null;          // { screen, value(), ready(), message, focus, waiting, done }
  let pending = null;          // a screen that came while the intro shows
  let lastShown = "";          // the line setup showed back for the last answer
  let workTimer = 0;
  let handedWhy = "";          // setup's hand-over: it goes on in Terminal (the window closed for it, or broke)
  let over = false;            // setup's last word came (the closing list, or the question handed to Terminal)
  let stoppedHere = false;     // Stop here drew "Stopped": a question setup sent before it read the stop is dropped
  // a key card's groups as typed, by its screen: back on the card when setup asks it again (Signal refused them);
  // gone at any other question and at the end, and never sent anywhere but setup
  const typedCards = new Map();
  // Signal's own screens between two tries of the cards (its limit's login_again, its TOTP): the groups stay
  // (review W13: the cards reopened empty under "Every group is kept.")
  const KEEPS_CARDS = new Set(["login_again", "totp"]);
  const log = [];
  const LOG_MAX = 2000;        // lines the log keeps (a step's whole output, a build's too)
  let againLast = false;       // the last line came as a meter's (progress `again`): the next such one replaces it
  const DOTS_LINE = /^\s*(.+?) [○●]{5}(?: .*)?$/;   // "  her model ●●○○○", and "… still working"
  // setup's line as a step starts (prompts.SETUP_STEP): the window keeps it at the top while the step runs
  const STEP_LINE = /^Step (\d+) of (\d+): (.+)$/;
  let stepLine = "";
  const stepTitle = () => stepLine;
  const dotsOf = (line) => { const found = DOTS_LINE.exec(line); return found ? found[1] : null; };

  function heading(screen) {
    const parts = [el("h2", { id: "h-screen", tabindex: "-1", text: unmarked(screen.title) })];
    parts.push(...paragraphs(screen.lead, "lead"));
    if (typeof screen.more === "string" && screen.more) parts.push(...moreFold(screen.more));
    for (const [text, cls] of [[screen.ask, "ask"], [screen.help, "help"], [screen.stop, "help"]]) parts.push(...paragraphs(text, cls));
    // a screen gone back to: its answer before, in Terminal's words (only such a screen carries one)
    if (screen.kept !== undefined && screen.kind !== "kept") parts.push(el("p", { class: "help kept-line", text: WORDS.answerBefore + keptText(screen) }));
    // the question's own links (the captcha page): opened in the Mac's browser, never in this window
    if (Array.isArray(screen.links) && screen.links.length) {
      parts.push(el("ul", { class: "links" }, ...screen.links.map(([label, href]) => el("li", {}, el("a", { href, text: unmarked(label) })))));
    }
    return parts;
  }

  // an option's label with its pill at the right when recommended, its one-line why under it (owner, 29 Oct)
  const optionText = (option) => el("span", { class: "x-text" },
    el("span", { class: "x-head" }, rich(el("span", { class: "x-name" }), option.label), pill(option)),
    option.line ? rich(el("span", { class: "x-line" }), option.line) : null);

  function chooser(screen) {               // choice: one row per option, Enter's first; a field for her own name
    const name = "q-" + screen.id;
    const ids = screen.options.map((option) => option.id);
    const kept = typeof screen.kept === "string" ? screen.kept : null;
    let picked = kept !== null && ids.includes(kept) ? kept : kept !== null && ids.includes(OTHER) ? OTHER
      : ids.includes(screen.recommended) ? screen.recommended : ids[0];
    const typedOne = screen.options.find((o) => o.id === OTHER);
    const other = el("input", { class: "text other", type: "text", id: "f-other", placeholder: screen.placeholder, maxlength: String(NAME_MAX), autocomplete: "off", spellcheck: "false" });
    if (picked === OTHER && kept !== null) other.value = kept;
    const list = el("ul", { class: "list", role: "radiogroup", "aria-labelledby": "h-screen" });
    const radios = [];
    for (const option of screen.options) {
      if (option.id === OTHER) continue;   // drawn as its field, under the list
      const input = el("input", { type: "radio", name, value: option.id, checked: option.id === picked });
      input.addEventListener("change", () => { picked = option.id; refresh(); });
      radios.push(input);
      list.append(el("li", {}, el("label", { class: "x-row" }, input, optionText(option))));
    }
    const typing = () => {                 // typing her own name picks it; an empty field gives the pick back
      if (other.value.trim()) { picked = OTHER; for (const radio of radios) radio.checked = false; }
      else if (picked === OTHER) { picked = ids.includes(screen.recommended) ? screen.recommended : radios.length ? radios[0].value : OTHER; for (const radio of radios) radio.checked = radio.value === picked; }
      refresh();
    };
    other.addEventListener("input", typing);
    other.addEventListener("keydown", (e) => { if (e.key === "Enter") mainBtn.click(); });
    const field = typedOne ? el("div", { class: "field other-field" }, el("label", { for: "f-other", text: unmarked(typedOne.label) }), other) : null;
    return {
      parts: [list, field].filter(Boolean),
      value: () => picked === OTHER ? other.value : picked,
      ready: () => picked !== undefined && (picked !== OTHER || other.value.trim() !== ""),
      focus: list.querySelector("input:checked") || list.querySelector("input") || other,
    };
  }

  function cards(screen) {                 // cards: one card per option; the picked one's guide opens under it
    const ids = screen.options.map((option) => option.id);
    let picked = typeof screen.kept === "string" && ids.includes(screen.kept) ? screen.kept
      : ids.includes(screen.recommended) ? screen.recommended : ids[0];
    const group = el("div", { class: "cards", role: "radiogroup", "aria-labelledby": "h-screen" });
    const buttons = [];
    screen.options.forEach((option, index) => {
      const card = el("button", { class: "card", type: "button", role: "radio", "aria-checked": String(option.id === picked) },
        el("p", { class: "card-head" }, rich(el("span", { class: "card-label" }), option.label), pill(option)),
        option.line ? rich(el("p", { class: "card-line" }), option.line) : null);
      const more = guidePanel(option, `g-${screen.id}-${index}`);
      card.addEventListener("click", () => {
        picked = option.id;
        for (const [other, otherMore] of buttons) { other.setAttribute("aria-checked", String(other === card)); setMore(otherMore, other === card); }
        if (more) reveal(more);
        refresh();
      });
      buttons.push([card, more]);
      group.append(card);
      if (more) { group.append(more); setMore(more, option.id === picked); }
    });
    return { parts: [group], value: () => picked, ready: () => picked !== undefined, focus: buttons.length ? buttons[0][0] : null };
  }

  function typed(screen, hide = false) {   // text, number, hidden, pin: one line, checked by setup while typing
    const secret = hide || screen.kind === "hidden" || screen.kind === "pin";
    const input = el("input", {
      class: "text", type: secret ? "password" : "text", placeholder: screen.placeholder, maxlength: String(LINE_MAX),
      autocomplete: "off", spellcheck: "false", autocapitalize: "off", inputmode: screen.kind === "number" ? "tel" : null,
      "aria-labelledby": "h-screen", "aria-describedby": "message",
    });
    // setup says which lines it checks while typing (never one whose check reaches the network: Ollama's key)
    const checksWhileTyping = typeof screen.live === "boolean" ? screen.live : LIVE.has(screen.kind);
    if (typeof screen.kept === "string" && !secret) input.value = screen.kept;
    let timer = 0;
    input.addEventListener("input", () => {
      refresh();
      clearTimeout(timer);
      if (!checksWhileTyping) return;
      timer = setTimeout(() => { if (input.value && current && !current.waiting) post({ t: "check", id: screen.id, value: input.value }); }, 150);
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") mainBtn.click(); });
    return {
      parts: [input],
      value: () => input.value,
      ready: () => input.value.trim() !== "" || (screen.recommended !== null && !secret),
      focus: input,
      clear: () => { if (secret) input.value = ""; },
    };
  }

  function code(screen) {                  // six boxes, sent at the sixth digit
    const boxes = Array.from({ length: 6 }, (_, i) => el("input", {
      type: "password", inputmode: "numeric", maxlength: "1", autocomplete: "one-time-code", "aria-label": of(i + 1, 6),
    }));
    const value = () => boxes.map((box) => box.value).join("");
    const put = (digits, from) => {
      let i = from;
      for (const digit of digits) { if (i >= boxes.length) break; boxes[i++].value = digit; }
      (boxes[Math.min(i, boxes.length - 1)]).focus();
      refresh();
      if (value().length === 6) mainBtn.click();
    };
    boxes.forEach((box, i) => {
      box.addEventListener("input", () => { const digits = box.value.replace(/\D/g, ""); box.value = ""; put(digits, i); });
      box.addEventListener("keydown", (e) => {
        if (e.key === "Backspace" && !box.value && i > 0) { boxes[i - 1].value = ""; boxes[i - 1].focus(); e.preventDefault(); refresh(); }
      });
      box.addEventListener("paste", (e) => { e.preventDefault(); put((e.clipboardData.getData("text") || "").replace(/\D/g, ""), i); });
    });
    return {
      parts: [el("div", { class: "boxes", role: "group", "aria-labelledby": "h-screen" }, ...boxes)],
      value, ready: () => value().length === 6, focus: boxes[0],
      clear: () => { for (const box of boxes) box.value = ""; },
    };
  }

  function keyCard(screen) {               // the key card: one box per group, as the service's own screen shows them
    if (!screen.layout) return typed(screen, true);   // no card for it: one hidden line
    const layout = screen.layout;
    const sizes = Array.isArray(layout.sizes) && layout.sizes.length ? layout.sizes
      : Array.from({ length: layout.rows * layout.columns }, () => layout.group);
    const count = sizes.length, perRow = Math.ceil(count / layout.rows);
    const joiner = typeof layout.joiner === "string" ? layout.joiner.trim() : "";
    const flow = joiner !== "" || sizes.some((size) => size !== sizes[0]);   // the Account ID's 8-4-4-4-12, one line
    const before = typedCards.get(screen.id) || [];   // the groups typed before, when setup asks this card again
    const status = el("p", { class: "help", "aria-live": "polite" });
    const boxes = sizes.map((size, i) => el("input", {
      type: layout.visible ? "text" : "password", maxlength: String(size), autocomplete: "off", spellcheck: "false",
      autocapitalize: "off", "aria-label": `${layout.label}, ${groupOf(i + 1, count)}`,
    }));
    boxes.forEach((box, i) => {
      box.value = String(before[i] || "").slice(0, sizes[i]);
      if (flow) box.style.width = `${(sizes[i] * 0.68 + 1.4).toFixed(2)}em`;
    });
    const cells = [];
    boxes.forEach((box, i) => {
      if (joiner && i % perRow) cells.push(el("span", { class: "joiner", "aria-hidden": "true", text: joiner }));
      cells.push(box);
    });
    const grid = el("div", { class: flow ? "grid flow" : "grid", role: "group", "aria-label": layout.label }, ...cells);
    if (!flow) grid.style.gridTemplateColumns = `repeat(${perRow}, max-content)`;
    const full = (i) => boxes[i].value.length === sizes[i];
    const mark = () => {
      const next = boxes.findIndex((box, i) => !full(i));
      boxes.forEach((box, i) => { box.classList.toggle("done", full(i)); box.classList.toggle("marked", i === next); });
      refresh();
    };
    const spill = (text, from) => {      // a paste fills this group and the ones after it
      const kept = text.replace(/[\s\u00ad\u200b-\u200d\u2060\ufeff-]/g, "");
      let i = from, at = 0;
      for (; at < kept.length && i < count; at += sizes[i], i++) boxes[i].value = kept.slice(at, at + sizes[i]);
      boxes[Math.min(i, count - 1)].focus();
      mark();
    };
    boxes.forEach((box, i) => {
      box.addEventListener("focus", () => { status.textContent = groupOf(i + 1, count); });
      box.addEventListener("input", () => {
        if (box.value.length > sizes[i]) { spill(box.value, i); return; }
        if (full(i)) {   // fixed in its place; the next empty group is marked
          const next = boxes.findIndex((other, j) => j > i && !full(j));
          if (next >= 0) boxes[next].focus();
        }
        mark();
      });
      box.addEventListener("keydown", (e) => {
        if (e.key === "Backspace" && !box.value && i > 0) { e.preventDefault(); const prev = boxes[i - 1]; prev.value = prev.value.slice(0, -1); prev.focus(); mark(); }
        if (e.key === "Enter") mainBtn.click();
      });
      box.addEventListener("paste", (e) => { e.preventDefault(); spill(e.clipboardData.getData("text") || "", i); });
    });
    setTimeout(mark, 0);
    return {
      parts: [grid, status],
      value: () => boxes.map((box) => box.value).join(joiner || " "),
      ready: () => boxes.every((box, i) => full(i)),
      focus: boxes[Math.max(0, boxes.findIndex((box, i) => !full(i)))],
      keep: () => typedCards.set(screen.id, boxes.map((box) => box.value)),
    };
  }

  function folder(screen) {                // the Mac's folder picker, through the window
    let path = typeof screen.kept === "string" ? screen.kept : screen.recommended || "";
    const shownPath = el("code", { text: path });
    shownPath.hidden = !path;
    const pick = el("button", { class: "small", type: "button", text: WORDS.folder });
    pick.addEventListener("click", () => native({ t: "pick_folder" }));
    const tagged = screen.options.find((option) => option.tag && option.id === screen.recommended);
    const mark = pill(tagged);
    const rec = el("p", { class: "x-line", text: tagged ? unmarked(tagged.line) : "" });
    const show = () => { const on = Boolean(tagged) && path === tagged.id; if (mark) mark.hidden = !on; rec.hidden = !on || !rec.textContent; };
    show();
    return {
      parts: [el("div", { class: "folder" }, shownPath, mark, pick), rec],
      value: () => path, ready: () => path !== "", focus: pick,
      picked: (chosen) => { path = chosen; shownPath.textContent = chosen; shownPath.hidden = !chosen; show(); refresh(); },
    };
  }

  function fieldsOf(screen) {              // fields: one line each, any may stay empty
    const kept = screen.kept && typeof screen.kept === "object" && !Array.isArray(screen.kept) ? screen.kept : {};
    const inputs = screen.fields.map((field) => {
      const input = el("input", { class: "text", type: "text", id: `f-${field.id}`, placeholder: field.placeholder, maxlength: String(field.max_chars), autocomplete: "off" });
      input.value = kept[field.id] || "";
      return [field, input];
    });
    inputs.forEach(([, input], i) => input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      if (i + 1 < inputs.length) inputs[i + 1][1].focus(); else mainBtn.click();
    }));
    return {
      parts: inputs.map(([field, input]) => el("div", { class: "field" }, el("label", { for: input.id, text: unmarked(field.label) }), input)),
      value: () => Object.fromEntries(inputs.map(([field, input]) => [field.id, input.value])),
      ready: () => true,
      focus: inputs.length ? inputs[0][1] : null,
    };
  }

  function switches(screen) {              // switches: names with one line each; a guide opens only when picked
    const kept = Array.isArray(screen.kept) ? screen.kept : null;
    const list = el("ul", { class: "list", role: "list", "aria-labelledby": "h-screen" });
    const boxes = [];
    screen.options.forEach((option, index) => {
      const more = guidePanel(option, `g-${screen.id}-${index}`);
      const on = kept ? kept.includes(option.id) : option.on;
      const box = el("input", { type: "checkbox", checked: on, "aria-expanded": more ? String(on) : null, "aria-controls": more ? more.id : null });
      box.addEventListener("change", () => {
        if (more) { setMore(more, box.checked); box.setAttribute("aria-expanded", String(box.checked)); if (box.checked) reveal(more); }
        say(`${unmarked(option.label)} ${box.checked ? "picked." : "not picked."}${box.checked && more ? WORDS.steps : ""}`);
      });
      const item = el("li", {}, el("label", { class: "x-row" }, box, optionText(option)));
      if (more) { more.classList.add("x-more"); item.append(more); setMore(more, on); }
      boxes.push([option, box]);
      list.append(item);
    });
    return {
      parts: [list],
      value: () => boxes.filter(([, box]) => box.checked).map(([option]) => option.id),
      ready: () => true,
      focus: boxes.length ? boxes[0][1] : null,
    };
  }

  function waiting(screen) {               // wait: what to send, large, with Copy; setup goes on by itself
    const words = screen.options.map((option) => {
      const copy = el("button", { class: "small", type: "button", text: WORDS.copy });
      copy.addEventListener("click", () => { native({ t: "copy", text: option.label }); say(WORDS.copied); });
      return el("div", { class: "folder" }, el("p", { class: "big-word", text: option.label }), copy);
    });
    return { parts: [el("div", { class: "words" }, ...words), el("p", { class: "waiting", text: WORDS.waiting })], value: () => null, ready: () => false, focus: null, still: true };
  }

  function keptList(screen) {              // the answers kept from the last run: Change asks one again
    const again = new Set();
    const rows = screen.options.map((option) => {
      const text = el("span", { class: "kept-line", text: `${unmarked(option.label)}: ${unmarked(option.line)}` });
      const change = el("button", { class: "small", type: "button", text: WORDS.change, "aria-pressed": "false" });
      change.addEventListener("click", () => {
        const on = !again.has(option.id);
        if (on) again.add(option.id); else again.delete(option.id);
        change.textContent = on ? WORDS.keep : WORDS.change;
        change.setAttribute("aria-pressed", String(on));
        text.textContent = `${unmarked(option.label)}: ${on ? WORDS.asked : unmarked(option.line)}`;
        text.classList.toggle("again", on);
      });
      return el("li", {}, text, change);
    });
    return { parts: [el("ul", { class: "kept-rows" }, ...rows)], value: () => [...again], ready: () => true, focus: rows.length ? rows[0].querySelector("button") : null };
  }

  function yesNo() {                      // yes or no: the two buttons, each saying what it does in the line above
    return { parts: [], value: () => null, ready: () => true, focus: null };
  }

  const DRAW = {
    choice: chooser, cards: cards, yes_no: yesNo, text: typed, number: typed, hidden: typed, pin: typed, code: code,
    key: keyCard, folder: folder, fields: fieldsOf, switches: switches, wait: waiting, kept: keptList,
  };

  function showPanel() { introPanel.hidden = true; panel.hidden = false; scroller.scrollTop = 0; }

  function draw(screen) {
    if (screen.id === "stop") { stopScreen(screen); return; }
    clearTimeout(workTimer);
    if (screen.kind !== "key" && !KEEPS_CARDS.has(screen.id)) typedCards.clear();
    showPanel();
    panel.replaceChildren();
    counter.textContent = screen.steps ? of(screen.step, screen.steps) : "";
    const message = el("p", { class: "message", id: "message", "aria-live": "polite" });
    const kind = DRAW[screen.kind];
    if (!kind) { workView(screen.title); return; }   // info, terminal, progress: words, no answer
    const body = kind(screen);
    panel.append(...heading(screen), ...body.parts, message, rich(el("p", { class: "fine" }), screen.note || ""));
    current = { screen, ...body, message, waiting: false, done: false };
    // the quiet way out (her number: "Not at hand? Stop here"): setup stops, every answer so far kept
    const stop = typeof screen.stop === "string" && screen.stop ? { label: WORDS.stopHere, run: stopHere } : null;
    if (screen.kind === "yes_no") {
      const yes = screen.kept !== undefined ? screen.kept === true : screen.recommended === "yes";
      const button = (id, fallback) => screen.options.find((option) => option.id === id) || { label: fallback, tag: "", line: "" };
      const [first, second] = yes ? [button("yes", WORDS.yes), button("no", WORDS.no)] : [button("no", WORDS.no), button("yes", WORDS.yes)];
      actions({
        main: { label: first.label, tag: first.tag, rec: first.line, run: () => answer(yes) },
        alt: { label: second.label, tag: second.tag, rec: second.line, run: () => answer(!yes) },
        back: screen.back ? goBack : null,
      });
    } else if (body.still) {
      actions({ alt: stop });   // a wait (the one-time word): setup goes on by itself, so only its way out
    } else {
      // a question that can wait (a web key): its own button leaves it for later, as Enter does in Terminal
      const later = typeof screen.later === "string" && screen.later ? { label: screen.later, run: () => answer(screen.kind === "fields" ? {} : "") } : null;
      actions({ main: { label: WORDS.next, run: () => answer(current.value()), disabled: !body.ready() }, alt: later || stop, back: screen.back ? goBack : null });
    }
    (body.focus || $("h-screen")).focus({ preventScroll: true });
  }

  function refresh() { if (current && !current.waiting && current.ready) mainBtn.disabled = !current.ready(); }

  function hold() {                        // an answer, Back or Stop sent: every button off until setup's word
    current.waiting = true;
    mainBtn.disabled = true;
    altBtn.disabled = true;
    backBtn.disabled = true;
  }

  function goBack() {                      // one question back in the plan; setup keeps every answer
    if (!current || current.waiting || current.done) return;
    hold();
    if (current.keep) current.keep();
    post({ t: "back" });
  }

  function stopHere() {                    // setup stops, every answer kept; running it again goes on
    if (current && (current.waiting || current.done)) return;
    if (current) hold();
    over = true;
    stoppedHere = true;
    post({ t: "stop" });
    current = null;
    showPanel();
    counter.textContent = "";
    // the log stays under it: a step with admin rights finishes first, and setup says so there
    const lines = el("pre", { class: "log", role: "log", text: log.join("\n") });
    panel.replaceChildren(el("h2", { id: "h-screen", tabindex: "-1", text: WORDS.stopped }), lines);
    lines.scrollTop = lines.scrollHeight;
    actions({ main: { label: WORDS.close, run: () => native({ t: "close" }) } });
    mainBtn.focus({ preventScroll: true });
  }

  function answer(value) {
    if (!current || current.waiting || current.done) return;
    hold();
    if (current.keep) current.keep();
    post({ t: "answer", id: current.screen.id, value });
  }

  function checked(message) {
    if (!current || message.id !== current.screen.id || current.done) return;
    const box = current.message;
    if (message.live) {
      if (current.waiting) return;
      box.classList.toggle("ok", message.ok);
      box.textContent = unmarked(message.ok ? message.shown : message.message);
      return;
    }
    altBtn.disabled = false;
    if (!message.ok) {                       // refused: one sentence, and the same screen again, Back on again
      current.waiting = false;
      backBtn.disabled = false;
      if (current.clear) current.clear();
      box.classList.remove("ok");
      box.textContent = unmarked(message.message);
      refresh();
      if (current.focus) current.focus.focus();
      return;
    }
    current.done = true;
    lastShown = message.shown;
    box.classList.add("ok");
    box.textContent = unmarked(message.shown);
    workTimer = setTimeout(() => workView(), 400);   // the next question usually comes first
  }

  /* Between questions and during the steps: the step ("Step 3 of 9: her model") at the top, what it prints in the log
     under it, as it comes (owner, 29 Oct: "Terminal in the window"); Stop here while a step runs (setup stops; one
     with admin rights finishes first). */
  function workView(title) {
    current = null;
    showPanel();
    counter.textContent = "";
    const lines = el("pre", { class: "log", role: "log", text: log.join("\n") });
    const shownTitle = title || stepTitle();
    panel.replaceChildren(...[shownTitle ? el("h2", { id: "h-screen", tabindex: "-1", text: unmarked(shownTitle) }) : null, ...paragraphs(lastShown, "lead"), lines].filter(Boolean));
    lines.scrollTop = lines.scrollHeight;
    actions(stepTitle() && !over ? { alt: { label: WORDS.stopHere, run: stopHere } } : {});
  }

  /* Setup's hand-over (the window closed for it, or broke): it goes on in the Terminal window it was started in,
     which the app brings to the front. Its words' first paragraph is the heading ("Setup goes on in the Terminal
     window you started it in. …"); Switch to Terminal brings it up again; the log stays under it, until Close. */
  function handedOver(why) {
    clearTimeout(workTimer);
    current = null;
    showPanel();
    counter.textContent = "";
    const open = el("button", { class: "small", type: "button", text: WORDS.terminal });
    open.addEventListener("click", () => native({ t: "terminal" }));
    const lines = el("pre", { class: "log", role: "log", text: log.join("\n") });
    const [first, ...rest] = String(why || "").split(/\n\s*\n/);
    panel.replaceChildren(el("h2", { id: "h-screen", tabindex: "-1", text: unmarked(first) }), ...paragraphs(rest.join("\n\n"), "lead"), el("div", {}, open), lines);
    lines.scrollTop = lines.scrollHeight;   // the newest lines
    $("h-screen").focus({ preventScroll: true });
  }

  function finished(lines) {               // the end: the closing list, or that setup goes on in Terminal
    clearTimeout(workTimer);
    if (pending && pending.t === "terminal") handedWhy = pending.why;
    current = null;
    pending = null;
    over = true;
    typedCards.clear();
    if (!introDone) { introDone = true; pauseClip(); }
    const close = { label: WORDS.close, run: () => native({ t: "close" }) };
    if (!lines.length && handedWhy) {   // setup goes on in Terminal: its screen stays, with Close
      handedOver(handedWhy);
      actions({ main: close });
      mainBtn.focus({ preventScroll: true });
      return;
    }
    showPanel();
    counter.textContent = "";
    const shownLog = el("pre", { class: "log", role: "log", text: log.join("\n") });
    panel.replaceChildren(...lines.flatMap((text) => blocks(text, "lead")), shownLog);
    shownLog.scrollTop = shownLog.scrollHeight;
    actions({ main: close });
    mainBtn.focus({ preventScroll: true });
  }

  /* Setup stopped at a step (its ✗ and its fix): its own screen, with space (owner, 29 Oct: "we need more space, make
     it readable, im lost"): what was found as the heading, why it matters, the fix, then how to go on with the command
     to copy; the step it stopped at stays in the corner. It stays until it is closed. */
  function stopScreen(screen) {
    clearTimeout(workTimer);
    current = null;
    pending = null;
    over = true;
    typedCards.clear();
    if (!introDone) { introDone = true; pauseClip(); }
    showPanel();
    counter.textContent = screen.steps ? of(screen.step, screen.steps) : "";
    const shownLog = el("pre", { class: "log", role: "log", text: log.join("\n") });
    panel.replaceChildren(el("h2", { id: "h-screen", tabindex: "-1", text: unmarked(screen.title) }), ...blocks(screen.lead, "lead"), shownLog);
    shownLog.scrollTop = shownLog.scrollHeight;
    actions({ main: { label: WORDS.close, run: () => native({ t: "close" }) } });
    $("h-screen").focus({ preventScroll: true });
  }

  function leaveIntro() {
    introDone = true;
    if (clipOn()) pauseClip();
    const screen = pending;
    pending = null;
    if (!screen) workView();
    else if (screen.t === "terminal") handedWhy = screen.why;   // its closing word (an empty done) draws it
    else draw(screen);
  }

  /* ------------------------------------------------------------------ what setup sends */
  function receive(message) {
    switch (message.t) {
      case "hello": break;
      case "screen":
        if (stoppedHere && message.id !== "stop") break;   // never a live question over "Stopped" (review W21)
        if (!introDone) pending = message; else draw(message);
        break;
      case "checked": checked(message); break;
      case "progress": {
        const again = message.again === true && againLast;   // a meter's line: in the place of the one before
        for (const line of message.line.split("\n")) {
          // a download's five dots take the place of its line before (setup_progress): they light up in place
          const step = STEP_LINE.exec(line.trim());
          if (step) { stepLine = line.trim(); if (current === null && introDone && !over) workView(); }
          const mine = dotsOf(line);
          if ((again || (mine !== null && log.length && dotsOf(log[log.length - 1]) === mine)) && log.length) log[log.length - 1] = line;
          else log.push(line);
        }
        againLast = message.again === true;
        if (log.length > LOG_MAX) log.splice(0, log.length - LOG_MAX);
        const shownLog = panel.querySelector(".log");
        if (shownLog && !panel.hidden) { shownLog.textContent = log.join("\n"); shownLog.scrollTop = shownLog.scrollHeight; }
        break;
      }
      case "terminal": handedWhy = message.why; if (!introDone) pending = message; break;
      case "done": finished(message.lines); break;
      default: break;
    }
  }

  window.estia = Object.freeze({
    receive,
    picked: (path) => { if (current && current.picked) current.picked(String(path)); },
    ended: () => { if (!over) finished([WORDS.ended]); },   // after setup's last word, what it showed stays
  });

  showTagline();
  introActions();
  post({ t: "ready", v: 2 });
})();
