import { TARGET_RATE, fmt, playClip, pushToTalk, timedJson } from "./audio.js";

const SVG = "http://www.w3.org/2000/svg";

const ROOMS = {
  living_room: { x: 40, y: 40, w: 420, h: 250, label: "living room", window: [140, 40, 120, "h"] },
  kitchen: { x: 460, y: 40, w: 300, h: 250, label: "kitchen", window: [560, 40, 120, "h"] },
  bedroom: { x: 40, y: 290, w: 280, h: 190, label: "bedroom", window: [120, 480, 120, "h"] },
  bathroom: { x: 320, y: 290, w: 140, h: 190, label: "bath", window: [360, 480, 60, "h"] },
  office: { x: 460, y: 290, w: 300, h: 190, label: "office", window: [560, 480, 120, "h"] },
};
const DOORS = {
  front: { x: 40, y: 140, len: 50, side: "left" },
  back: { x: 760, y: 120, len: 50, side: "right" },
  garage: { x: 760, y: 360, len: 50, side: "right" },
};
const COLORS = {
  warm: "#ffb45a", white: "#fff4dc", cool: "#bfe0ff", red: "#ff4d4d", orange: "#ff8a2a",
  green: "#5dff96", blue: "#4d8cff", purple: "#b06bff", pink: "#ff6bd0",
};
const STAGES = {
  network: { label: "network", color: "var(--s-net)" },
  queue_ms: { label: "queue", color: "var(--s-queue)" },
  whistle_ms: { label: "whistle", color: "var(--s-whistle)" },
  needle_ms: { label: "needle", color: "var(--s-needle)" },
  fused_ms: { label: "whistle+needle (fused)", color: "var(--s-fused)" },
  tools_ms: { label: "tools", color: "var(--s-tools)" },
  other: { label: "server overhead", color: "var(--s-other)" },
};
const MODE_HINTS = {
  pipeline: "transcribe() with keyword biasing, then Needle.complete(text): two calls",
  fused: "one needle_complete(audio) call: the engine transcribes and plans in C (no keywords)",
};

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, parent) => {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
};

// ---------- floor plan ----------

const fixtures = {};

function buildPlan() {
  const rooms = $("rooms");
  const fx = $("fixtures");
  for (const [id, r] of Object.entries(ROOMS)) {
    const g = el("g", { "clip-path": `url(#clip-${id})`, class: "room", "data-room": id }, rooms);
    const glow = el("circle", {
      cx: r.x + r.w / 2, cy: r.y + r.h / 2, r: Math.max(r.w, r.h) * 0.55,
      filter: "url(#soft)", class: "glow",
    }, g);
    const bulb = el("circle", { cx: r.x + r.w / 2, cy: r.y + r.h / 2, r: 6, class: "bulb" }, fx);
    const label = el("text", { x: r.x + 14, y: r.y + 26, class: "room-label" }, fx);
    label.textContent = r.label;
    const sub = el("text", { x: r.x + 14, y: r.y + 44, class: "room-sub" }, fx);

    const [wx, wy, wl] = r.window;
    const win = el("g", { class: "window" }, fx);
    el("rect", { x: wx, y: wy - 5, width: wl, height: 10, class: "window-frame" }, win);
    const hatch = el("rect", { x: wx, y: wy - 5, width: 0, height: 10, class: "window-hatch" }, win);
    fixtures[id] = { glow, bulb, sub, hatch, wl, g };
  }
  for (const [id, d] of Object.entries(DOORS)) {
    const g = el("g", { class: "door" }, fx);
    el("rect", { x: d.x - 4, y: d.y, width: 8, height: d.len, class: "door-gap" }, g);
    const swingX = d.side === "left" ? d.x + d.len : d.x - d.len;
    el("path", {
      d: `M${d.x} ${d.y} L${swingX} ${d.y} A${d.len} ${d.len} 0 0 ${d.side === "left" ? 1 : 0} ${d.x} ${d.y + d.len}`,
      class: "door-swing",
    }, g);
    const lx = d.side === "left" ? d.x + 22 : d.x - 22;
    const lock = el("g", { transform: `translate(${lx} ${d.y + d.len + 22})`, class: "lock" }, g);
    el("path", { d: "M-5 -2 V-7 A5 5 0 0 1 5 -7 V-2", class: "shackle" }, lock);
    el("rect", { x: -8, y: -2, width: 16, height: 12, rx: 2, class: "lock-body" }, lock);
    const t = el("text", { x: 0, y: 26, class: "door-label" }, lock);
    t.textContent = id;
    fixtures[`door:${id}`] = { g: lock };
  }
  const thermo = el("g", { transform: "translate(395 225)", class: "thermo" }, fx);
  el("circle", { r: 30, class: "thermo-ring" }, thermo);
  const thermoArc = el("circle", { r: 30, class: "thermo-arc", pathLength: 100 }, thermo);
  const thermoText = el("text", { y: 7, class: "thermo-text" }, thermo);
  fixtures.thermo = { g: thermo, arc: thermoArc, text: thermoText };
}

let lastState = null;

function render(state) {
  for (const id of Object.keys(ROOMS)) {
    const light = state.lights[id];
    const fx = fixtures[id];
    const color = COLORS[light.color] || COLORS.warm;
    const level = light.brightness / 100;
    fx.glow.style.fill = color;
    fx.glow.style.opacity = (level * 0.85).toFixed(3);
    fx.bulb.style.fill = level > 0 ? color : "transparent";
    fx.bulb.style.filter = level > 0 ? `drop-shadow(0 0 ${4 + level * 10}px ${color})` : "none";
    const open = state.blinds[id];
    fx.hatch.setAttribute("width", ((fx.wl * (100 - open)) / 100).toFixed(1));
    fx.sub.textContent = `${light.brightness ? `${light.brightness}% ${light.color}` : "off"} · blinds ${open}%`;
    if (lastState && JSON.stringify(lastState.lights[id]) + lastState.blinds[id] !==
        JSON.stringify(light) + open) pulse(fx.g);
  }
  for (const id of Object.keys(DOORS)) {
    const f = fixtures[`door:${id}`];
    const locked = state.locks[id];
    f.g.classList.toggle("unlocked", !locked);
    if (lastState && lastState.locks[id] !== locked) pulse(f.g);
  }
  const t = state.thermostat_f;
  fixtures.thermo.text.textContent = `${t}°`;
  fixtures.thermo.arc.style.strokeDasharray = `${((t - 50) / 40) * 100} 100`;
  if (lastState && lastState.thermostat_f !== t) pulse(fixtures.thermo.g);
  lastState = structuredClone(state);
}

function pulse(node) {
  node.classList.remove("pulse");
  void node.getBoundingClientRect();
  node.classList.add("pulse");
}

// ---------- results ----------

const history = [];

function showResult(result, rttMs, source) {
  render(result.state);
  $("transcript").textContent = result.transcript || "(no speech heard)";
  $("transcript").classList.toggle("muted", !result.transcript);

  const t = result.timings;
  const server = t.server_ms ?? 0;
  const parts = [];
  if (rttMs != null) parts.push(["network", Math.max(0, rttMs - server)]);
  for (const key of ["queue_ms", "whistle_ms", "needle_ms", "fused_ms", "tools_ms"]) {
    if (t[key] != null) parts.push([key, t[key]]);
  }
  const accounted = parts.filter(([k]) => k !== "network").reduce((a, [, v]) => a + v, 0);
  parts.push(["other", Math.max(0, server - accounted)]);
  const total = rttMs ?? server;
  $("total").textContent = `${fmt(total)} round trip`;
  const bar = $("waterfall");
  const legend = $("bar-legend");
  bar.replaceChildren();
  legend.replaceChildren();
  for (const [key, ms] of parts) {
    const seg = document.createElement("div");
    seg.className = "seg";
    seg.style.background = STAGES[key].color;
    seg.style.flexGrow = Math.max(ms, total * 0.004);
    seg.title = `${STAGES[key].label}: ${fmt(ms)}`;
    bar.appendChild(seg);
    if (ms < 0.05 && key !== "network") continue;
    const item = document.createElement("span");
    item.innerHTML = `<i style="background:${STAGES[key].color}"></i>${STAGES[key].label} <b>${fmt(ms)}</b>`;
    legend.appendChild(item);
  }

  const stats = [
    ["audio", t.audio_ms != null ? fmt(t.audio_ms) : "typed"],
    ["whistle ttft", t.whistle_ttft_ms != null ? fmt(t.whistle_ttft_ms) : "—"],
    ["whistle decode", t.whistle_tps != null ? `${t.whistle_tps.toFixed(0)} tok/s` : "—"],
    ["needle prefill", t.needle_prefill_tps != null ? `${t.needle_prefill_tps.toFixed(0)} tok/s` : "—"],
    ["needle decode", t.needle_decode_tps != null ? `${t.needle_decode_tps.toFixed(0)} tok/s` : "—"],
    ["real-time factor", t.audio_ms ? `${(server / t.audio_ms).toFixed(3)}×` : "—"],
    ["confidence", result.confidence != null ? result.confidence.toFixed(2) : "—"],
    ["engine RAM", t.peak_ram_mb != null ? `${t.peak_ram_mb.toFixed(0)} MB` : "—"],
  ];
  $("stats").replaceChildren(...stats.flatMap(([k, v]) => {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    return [dt, dd];
  }));

  const list = $("calls");
  list.replaceChildren();
  const describe = (c) => `${c.name}(${Object.entries(c.arguments).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})`;
  for (const c of result.calls) {
    const li = document.createElement("li");
    li.className = c.result?.error ? "err" : "ok";
    li.textContent = describe(c);
    if (c.result?.error) li.title = c.result.error;
    list.appendChild(li);
  }
  for (const c of result.suppressed) {
    const li = document.createElement("li");
    li.className = "held";
    li.textContent = describe(c);
    li.title = "Needle held this call back: an argument was not grounded in what you said";
    list.appendChild(li);
  }
  if (!list.children.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "no tool fits that request: empty call list";
    list.appendChild(li);
  }
  $("reasoning").textContent = result.reasoning ? `needle: ${result.reasoning}` : "";

  history.unshift({ source, mode: result.mode, total, text: result.transcript, n: result.calls.length });
  history.length = Math.min(history.length, 12);
  $("history").replaceChildren(...history.map((h) => {
    const tr = document.createElement("tr");
    for (const v of [h.source, h.mode, fmt(h.total), `${h.n} call${h.n === 1 ? "" : "s"}`, h.text || "—"]) {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    }
    return tr;
  }));
}

function setStatus(text, cls = "") {
  const s = $("status");
  s.textContent = text;
  s.className = `status ${cls}`;
}

async function call(url, init, source) {
  const { body, rtt } = await timedJson(url, init);
  showResult(body, rtt, source);
}

const mode = () => document.querySelector("input[name=mode]:checked").value;

async function sendAudio(samples, source) {
  setStatus(`thinking · ${(samples.length / TARGET_RATE).toFixed(1)} s of audio`, "busy");
  try {
    await call(`/api/command?mode=${mode()}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: samples.buffer,
    }, source);
    setStatus("ready");
  } catch (err) {
    setStatus(err.message, "error");
  }
}

// ---------- wiring ----------

function wire() {
  pushToTalk({
    button: $("ptt"),
    level: $("level"),
    setStatus,
    onAudio: (samples) => sendAudio(samples, "mic"),
    isTyping: () => document.activeElement === $("text"),
  });

  for (const b of document.querySelectorAll("[data-clip]")) {
    b.addEventListener("click", async () => {
      setStatus(`decoding clip ${b.dataset.clip}`, "busy");
      await sendAudio(await playClip(b.dataset.clip), "clip");
    });
  }

  $("text-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("text").value.trim();
    if (!text) return;
    setStatus("thinking", "busy");
    try {
      await call("/api/text", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
      }, "typed");
      setStatus("ready");
    } catch (err) {
      setStatus(err.message, "error");
    }
  });

  const hint = () => ($("mode-hint").textContent = MODE_HINTS[mode()]);
  for (const r of document.querySelectorAll("input[name=mode]")) r.addEventListener("change", hint);
  hint();

  $("reset").addEventListener("click", async () => {
    const res = await fetch("/api/reset", { method: "POST" });
    render(await res.json());
  });
}

buildPlan();
wire();
fetch("/api/state").then((r) => r.json()).then(render);
