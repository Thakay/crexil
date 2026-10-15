// The companion page's casting and its two videos.
//
// The casting plays the three voices of one run, in the order the run recorded them, and after
// the reveal the line spoken in the picked voice, one sound at a time. A plate, or its digit,
// plays its voice from the start, pauses it while it plays and resumes it where it paused; Space
// pauses or resumes the sound in play. The keys act while the casting holds focus or is in view,
// and a focused control keeps Space for itself. A choice, or "Just show me", reveals the pick, its
// card from the record and the line's player; the reveal takes focus and is never undone. Nothing
// loads until a press, nothing is stored, and nothing loads from another site until a video's
// player is asked for.

'use strict';

// What the page shows from the project's record and the video, in one place. A null value leaves
// its part of the page disabled.
//   videoLength   the main video's length, as "1:30"
//   captions      true when the main video carries captions
//   stillOnPlate  true to show still.jpg on the main video's plate
//   voices        the three voices' lengths in seconds, in the run's order (audio/voice-1.mp3 to 3)
//   pick          the voice picked: 1, 2 or 3
//   reason        the pick's reason, exactly as the record keeps it
//   date          the day of the pick, as "14 October 2026"
//   price         the run's recorded price, as "$0.01008"
//   line          the line's length in seconds (audio/line.mp3)
//   words         the line's words, as recorded
const FACTS = /* facts */ {
  "videoLength": "2:29",
  "captions": true,
  "stillOnPlate": true,
  "voices": [19.82, 19.10, 16.30],
  "pick": 3,
  "reason": "sounds fun",
  "date": "12 October 2026",
  "price": "$0.0168",
  "line": 4.78,
  "words": "Good steel takes patience. Bad steel takes fingers."
} /* end of facts */;

const sounds = []; // the three voices, then the line: {button, fill, src, seconds, voice, audio}
let active = null; // the sound in play, or the one that played last
let frame = 0;
let statusLine = null;

const secondsText = seconds => `${seconds.toFixed(1)} s`;

function say(text) {
  if (statusLine) statusLine.textContent = text;
}

function show(sound, state) {
  sound.button.classList.toggle('playing', state === 'playing');
  if (!sound.voice) return; // the line's player shows its state on its own button
  if (state === 'playing') say(`Playing voice ${sound.voice} · ${secondsText(sound.seconds)}`);
  else if (state === 'paused') say(`Paused voice ${sound.voice} at ${sound.audio.currentTime.toFixed(1)} of ${secondsText(sound.seconds)}`);
  else if (state === 'ended') say(`Voice ${sound.voice} played to its end`);
}

function draw(sound) {
  const audio = sound.audio;
  const total = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : sound.seconds;
  const share = audio.ended ? 1 : Math.min(1, audio.currentTime / total);
  sound.fill.style.transform = `scaleX(${share})`;
}

// A sound that is no longer the one in play keeps no state and no progress: pressed again, it
// starts from the beginning.
function quiet(sound) {
  sound.button.classList.remove('playing');
  sound.fill.style.transform = 'scaleX(0)';
}

function tick() {
  cancelAnimationFrame(frame);
  const step = () => {
    if (!active || !active.audio) return;
    draw(active);
    if (!active.audio.paused) frame = requestAnimationFrame(step);
  };
  frame = requestAnimationFrame(step);
}

function failed(sound) {
  show(sound, 'stopped');
  say(sound.voice ? `Voice ${sound.voice} could not be played in this browser.`
    : 'The line could not be played in this browser.');
}

// A sound's file loads on its first press, never before.
function audioOf(sound) {
  if (!sound.audio) {
    const audio = new Audio();
    audio.preload = 'auto';
    audio.src = sound.src;
    audio.addEventListener('play', () => { if (active === sound) { show(sound, 'playing'); tick(); } });
    audio.addEventListener('pause', () => {
      if (active !== sound) return;
      if (!audio.ended) show(sound, 'paused');
      draw(sound);
    });
    audio.addEventListener('ended', () => { if (active === sound) { show(sound, 'ended'); draw(sound); } });
    audio.addEventListener('error', () => { if (active === sound) failed(sound); });
    sound.audio = audio;
  }
  return sound.audio;
}

function play(sound) {
  const playing = sound.audio.play();
  if (playing) playing.catch(error => { if (active === sound && error.name !== 'AbortError') failed(sound); });
}

// Stop whatever else plays, then play this sound from its start.
function start(sound) {
  if (active && active !== sound) {
    const before = active;
    if (before.audio && !before.audio.paused) before.audio.pause();
    quiet(before);
    say('');
  }
  active = sound;
  const audio = audioOf(sound);
  if (audio.currentTime > 0) audio.currentTime = 0;
  play(sound);
}

// A plate, a digit or the line's player: pause the sound while it plays, resume it where it
// paused, else play it from the start.
function press(sound) {
  const audio = sound.audio;
  if (active === sound && audio && !audio.paused) audio.pause();
  else if (active === sound && audio && !audio.ended && audio.currentTime > 0) play(sound);
  else start(sound);
}

const casting = document.querySelector('[data-casting]');
const castingReady = ['voices', 'pick', 'reason', 'date', 'price', 'line', 'words'].every(key => FACTS[key] !== null);

function inPlay() {
  if (casting.contains(document.activeElement)) return true;
  const box = casting.getBoundingClientRect();
  return Math.min(box.bottom, window.innerHeight) - Math.max(box.top, 0) >= 80;
}

if (casting && !castingReady) casting.classList.add('held');

if (casting && castingReady) {
  statusLine = casting.querySelector('[data-status]');
  casting.querySelectorAll('[data-voice]').forEach((button, i) => {
    const sound = {button, fill: button.querySelector('.fill'), src: `audio/voice-${i + 1}.mp3`,
      seconds: FACTS.voices[i], voice: i + 1, audio: null};
    button.querySelector('[data-seconds]').textContent = secondsText(sound.seconds);
    button.setAttribute('aria-label', `Voice ${i + 1}, ${sound.seconds.toFixed(1)} seconds`);
    button.disabled = false;
    button.addEventListener('click', () => press(sound));
    sounds.push(sound);
  });
  const lineButton = casting.querySelector('[data-line]');
  const line = {button: lineButton, fill: lineButton.querySelector('.fill'), src: 'audio/line.mp3',
    seconds: FACTS.line, voice: null, audio: null};
  lineButton.disabled = false;
  lineButton.addEventListener('click', () => press(line));
  sounds.push(line);

  const pick = FACTS.pick;
  const others = [1, 2, 3].filter(n => n !== pick);
  casting.querySelector('[data-date]').textContent = FACTS.date;
  casting.querySelector('[data-card-title]').textContent = `Voice ${pick}, chosen over voices ${others[0]} and ${others[1]}`;
  casting.querySelector('[data-reason]').textContent = FACTS.reason;
  casting.querySelector('[data-price]').textContent = FACTS.price;
  casting.querySelector('[data-words]').textContent = FACTS.words;

  const panel = casting.querySelector('[data-reveal]');
  const chooseRow = casting.querySelector('[data-choose-row]');
  let revealed = false;
  const reveal = choice => {
    if (revealed) return;
    revealed = true;
    casting.querySelector('[data-verdict]').textContent = choice === null ? `I picked voice ${pick}.`
      : choice === pick ? `You’d cast voice ${pick}. So did I.`
        : `You’d cast voice ${choice}. I picked voice ${pick}.`;
    sounds[pick - 1].button.classList.add('picked');
    chooseRow.hidden = true;
    panel.hidden = false;
    panel.focus();
  };
  for (const button of casting.querySelectorAll('[data-choose]')) {
    button.disabled = false;
    button.addEventListener('click', () => reveal(button.dataset.choose === '' ? null : Number(button.dataset.choose)));
  }

  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.isComposing) return;
    const key = event.key === ' ' ? 'Space' : event.key;
    if (!['1', '2', '3', 'Space'].includes(key)) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target && target.closest('input, textarea, select, [contenteditable]')) return;
    if (key === 'Space' && target && target.closest('button, a, summary, iframe')) return;
    if (!inPlay()) return;
    event.preventDefault();
    if (event.repeat) return;
    press(key === 'Space' ? active || sounds[0] : sounds[Number(key) - 1]);
  });
}

// The video plates: each plate's watch link, the link under it, is its one source of truth. A
// YouTube or Loom link plays in place through the host's privacy-minded embed, loaded only on
// press; any other link opens in a new tab. A link that is not filled in leaves its plate unfilled.
// One video plays at a time: playing one puts any other plate back as it was, and pauses the
// sound in play. A chapter link plays the whole session in place from the chapter's start on
// YouTube; with a modifier key, or on another host, it opens the host at that time instead. The
// facts' length, captions and still belong to the main video; the whole session's plate carries
// its own, and asks YouTube to show its caption track.
function embedOf(watch) {
  let url;
  try {
    url = new URL(watch);
  } catch {
    return null;
  }
  const host = url.hostname.replace(/^(www|m)\./, '');
  let id = null;
  if (host === 'youtu.be') id = url.pathname.slice(1).split('/')[0];
  else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
    id = url.searchParams.get('v') || (url.pathname.match(/^\/(?:embed|shorts|live)\/([\w-]+)/) || [])[1];
  }
  if (id && /^[\w-]{6,20}$/.test(id)) return `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0`;
  if (host === 'loom.com') {
    const match = url.pathname.match(/^\/(?:share|embed)\/([0-9a-f]{16,})/);
    if (match) return `https://www.loom.com/embed/${match[1]}?autoplay=1`;
  }
  return null;
}

const plates = [];

function playIn(plate, start = 0) {
  if (active && active.audio && !active.audio.paused) active.audio.pause();
  for (const other of plates) {
    if (other !== plate && other.playing) {
      other.video.replaceChildren(...other.idle);
      other.playing = false;
    }
  }
  const player = document.createElement('iframe');
  player.src = start ? `${plate.embed}&start=${start}` : plate.embed;
  player.title = plate.video.dataset.title;
  player.allow = 'autoplay; encrypted-media; fullscreen; picture-in-picture';
  plate.video.replaceChildren(player);
  plate.playing = true;
  return player;
}

for (const video of document.querySelectorAll('[data-video]')) {
  const link = document.querySelector(`[data-video-link="${video.dataset.video}"]`);
  if (!link) continue;
  const watch = link.getAttribute('href') || '';
  const ready = /^https:\/\//.test(watch);
  const button = video.querySelector('.video-play');
  if (video.dataset.video === 'main') {
    if (FACTS.videoLength) video.querySelector('[data-video-length]').textContent = ` · ${FACTS.videoLength}`;
    if (FACTS.captions) video.querySelector('[data-captions]').hidden = false;
    if (FACTS.stillOnPlate) {
      const still = document.createElement('img');
      still.className = 'video-still';
      still.src = 'still.jpg';
      still.alt = '';
      video.prepend(still);
      video.classList.add('has-still');
    }
  }
  if (!ready) {
    video.classList.add('unfilled');
    button.setAttribute('aria-disabled', 'true');
  }
  let embed = ready ? embedOf(watch) : null;
  const youtube = Boolean(embed) && embed.startsWith('https://www.youtube-nocookie.com/');
  if (youtube && 'captionsOn' in video.dataset) embed += '&cc_load_policy=1';
  const plate = {video, embed, youtube, idle: [...video.childNodes], playing: false};
  plates.push(plate);
  button.addEventListener('click', () => {
    if (!ready) return;
    if (!embed) {
      window.open(watch, '_blank', 'noopener');
      return;
    }
    playIn(plate).focus();
  });
}

const session = plates.find(plate => plate.video.dataset.video === 'session');
for (const chapter of document.querySelectorAll('.chapters a[data-at]')) {
  chapter.addEventListener('click', event => {
    if (!session || !session.youtube || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey
      || event.altKey) return;
    event.preventDefault();
    const player = playIn(session, Number(chapter.dataset.at));
    session.video.scrollIntoView({block: 'center', behavior: 'smooth'});
    player.focus({preventScroll: true});
  });
}
