import { TARGET_RATE, fmt, playClip, pushToTalk, timedJson } from "./audio.js";

const HOUSE_WORDS =
  "kitchen, living room, bedroom, bathroom, office, thermostat, blinds, garage, front door, back door";
const STAGES = {
  network: { label: "network", color: "var(--s-net)" },
  queue_ms: { label: "queue", color: "var(--s-queue)" },
  ttft: { label: "first token (encode + prefill)", color: "var(--s-whistle)" },
  decode: { label: "decode", color: "var(--s-fused)" },
  other: { label: "server overhead", color: "var(--s-other)" },
};

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

function setStatus(text, cls = "") {
  const s = $("status");
  s.textContent = text;
  s.className = `status ${cls}`;
}

// ---------- waveform ----------

const scope = { samples: null, words: [], highlight: -1 };

function drawScope() {
  const canvas = $("wave");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);

  const line = css("--line");
  const dim = css("--line-dim");
  const muted = css("--muted");
  const accent = css("--accent");
  const waveTop = 34;
  const waveH = h - waveTop - 22;
  const mid = waveTop + waveH / 2;

  g.strokeStyle = dim;
  g.lineWidth = 1;
  g.beginPath();
  g.moveTo(0, mid + 0.5);
  g.lineTo(w, mid + 0.5);
  g.stroke();

  const samples = scope.samples;
  if (!samples) return;
  const seconds = samples.length / TARGET_RATE;
  const x = (t) => (t / seconds) * w;

  scope.words.forEach((word, i) => {
    const x0 = x(word.start);
    const x1 = Math.max(x0 + 2, x(word.end));
    const on = i === scope.highlight;
    g.fillStyle = on ? "rgba(255, 180, 90, 0.22)" : `rgba(79, 209, 255, ${0.05 + word.probability * 0.1})`;
    g.fillRect(x0, waveTop, x1 - x0, waveH);
    g.fillStyle = on ? accent : line;
    g.fillRect(x0, waveTop, 1, waveH);
    g.globalAlpha = 0.35 + word.probability * 0.65;
    g.font = `italic 15px ${css("--serif")}`;
    g.fillStyle = on ? accent : "#fff";
    const next = scope.words[i + 1];
    g.save();
    g.beginPath();
    g.rect(x0, 0, (next ? x(next.start) : w) - x0 - 2, waveTop);
    g.clip();
    g.fillText(word.word, x0 + 3, waveTop - 10);
    g.restore();
    g.globalAlpha = 1;
  });

  const perPx = samples.length / w;
  g.fillStyle = line;
  for (let px = 0; px < w; px++) {
    let lo = 0;
    let hi = 0;
    const end = Math.min(samples.length, Math.floor((px + 1) * perPx));
    for (let i = Math.floor(px * perPx); i < end; i++) {
      const v = samples[i];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    const top = mid - hi * (waveH / 2) * 0.95;
    const bottom = mid - lo * (waveH / 2) * 0.95;
    g.fillRect(px, top, 1, Math.max(1, bottom - top));
  }

  g.fillStyle = muted;
  g.font = `10px ${css("--mono")}`;
  const step = seconds > 8 ? 2 : seconds > 3 ? 1 : 0.5;
  for (let t = 0; t <= seconds; t += step) {
    g.fillRect(x(t), h - 18, 1, 5);
    g.fillText(`${t.toFixed(step < 1 ? 1 : 0)}s`, x(t) + 3, h - 6);
  }
}

// ---------- results ----------

const history = [];

function renderTranscript(result) {
  const p = $("transcript");
  p.classList.toggle("muted", !result.transcript);
  if (!result.transcript) {
    p.textContent = "(no speech heard)";
    return;
  }
  if (!result.words.length) {
    p.textContent = result.transcript;
    return;
  }
  p.replaceChildren(...result.words.flatMap((word, i) => {
    const span = document.createElement("span");
    span.className = "word";
    span.textContent = word.word;
    span.style.opacity = (0.35 + word.probability * 0.65).toFixed(2);
    span.title = `${word.start.toFixed(2)}–${word.end.toFixed(2)} s · p=${word.probability.toFixed(2)}`;
    span.addEventListener("mouseenter", () => {
      scope.highlight = i;
      drawScope();
    });
    span.addEventListener("mouseleave", () => {
      scope.highlight = -1;
      drawScope();
    });
    return [span, document.createTextNode(" ")];
  }));
}

function showResult(result, rtt, samples, source) {
  const t = result.timings;
  scope.samples = samples;
  scope.words = result.words;
  scope.highlight = -1;
  drawScope();
  $("scope-len").textContent = `${(t.audio_ms / 1000).toFixed(2)} s · ${samples.length.toLocaleString()} samples @ 16 kHz`;
  $("lang").textContent = result.language ? `language: ${result.language}` : "";
  renderTranscript(result);

  const server = t.server_ms ?? 0;
  const ttft = Math.min(t.whistle_ttft_ms ?? 0, t.whistle_ms);
  const parts = [
    ["network", Math.max(0, rtt - server)],
    ["queue_ms", t.queue_ms ?? 0],
    ["ttft", ttft],
    ["decode", Math.max(0, t.whistle_ms - ttft)],
    ["other", Math.max(0, server - (t.queue_ms ?? 0) - t.whistle_ms)],
  ];
  $("total").textContent = `${fmt(rtt)} round trip`;
  const bar = $("waterfall");
  const legend = $("bar-legend");
  bar.replaceChildren();
  legend.replaceChildren();
  for (const [key, ms] of parts) {
    const seg = document.createElement("div");
    seg.className = "seg";
    seg.style.background = STAGES[key].color;
    seg.style.flexGrow = Math.max(ms, rtt * 0.004);
    seg.title = `${STAGES[key].label}: ${fmt(ms)}`;
    bar.appendChild(seg);
    if (ms < 0.05 && key !== "network") continue;
    const item = document.createElement("span");
    item.innerHTML = `<i style="background:${STAGES[key].color}"></i>${STAGES[key].label} <b>${fmt(ms)}</b>`;
    legend.appendChild(item);
  }

  const rtf = t.whistle_ms / t.audio_ms;
  const confidence = result.words.length
    ? result.words.reduce((a, w) => a + w.probability, 0) / result.words.length
    : null;
  const stats = [
    ["audio", fmt(t.audio_ms)],
    ["whistle total", fmt(t.whistle_ms)],
    ["first token", fmt(t.whistle_ttft_ms)],
    ["decode", t.whistle_tps != null ? `${t.whistle_tps.toFixed(0)} tok/s` : "—"],
    ["real-time factor", `${rtf.toFixed(3)}×`],
    ["vs real time", `${(1 / rtf).toFixed(0)}× faster`],
    ["words", result.words.length ? String(result.words.length) : "—"],
    ["mean confidence", confidence != null ? confidence.toFixed(2) : "—"],
  ];
  $("stats").replaceChildren(...stats.flatMap(([k, v]) => {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    return [dt, dd];
  }));

  history.unshift({ source, audio: t.audio_ms, whistle: t.whistle_ms, ttft: t.whistle_ttft_ms, rtf, text: result.transcript });
  history.length = Math.min(history.length, 20);
  $("history").replaceChildren(...history.map((h) => {
    const tr = document.createElement("tr");
    for (const v of [h.source, fmt(h.audio), fmt(h.whistle), fmt(h.ttft), h.rtf.toFixed(3), h.text || "—"]) {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    }
    return tr;
  }));
  $("summary").textContent = history.length > 1
    ? `${history.length} runs · median whistle ${fmt(median(history.map((h) => h.whistle)))} · ` +
      `ttft ${fmt(median(history.map((h) => h.ttft ?? 0)))} · rtf ${median(history.map((h) => h.rtf)).toFixed(3)}`
    : "";
}

// ---------- requests ----------

function query() {
  const params = new URLSearchParams();
  const language = $("language").value;
  if (language) params.set("language", language);
  params.set("words", $("words").checked ? "true" : "false");
  for (const k of $("keywords").value.split(",")) {
    if (k.trim()) params.append("keywords", k.trim());
  }
  return params;
}

async function transcribe(samples, source) {
  setStatus(`transcribing · ${(samples.length / TARGET_RATE).toFixed(1)} s of audio`, "busy");
  try {
    const { body, rtt } = await timedJson(`/api/transcribe?${query()}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: samples.buffer,
    });
    showResult(body, rtt, samples, source);
    setStatus("ready");
  } catch (err) {
    setStatus(err.message, "error");
  }
}

// ---------- wiring ----------

pushToTalk({
  button: $("ptt"),
  level: $("level"),
  setStatus,
  onAudio: (samples) => transcribe(samples, "mic"),
  isTyping: () => document.activeElement === $("keywords"),
});

for (const b of document.querySelectorAll("[data-clip]")) {
  b.addEventListener("click", async () => {
    const lang = $("language");
    const clipLang = b.dataset.lang || "en";
    if (lang.value && lang.value !== clipLang) lang.value = "";
    setStatus(`decoding clip ${b.dataset.clip}`, "busy");
    await transcribe(await playClip(b.dataset.clip), "clip");
  });
}

$("house-words").addEventListener("click", () => {
  $("keywords").value = HOUSE_WORDS;
});

window.addEventListener("resize", drawScope);
drawScope();
