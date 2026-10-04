// Shared by both pages: mic capture, push-to-talk, sample clips, timed fetch.

export const TARGET_RATE = 16000;
export const MAX_SECONDS = 30;

export const fmt = (ms) => (ms == null ? "—" : ms >= 100 ? `${ms.toFixed(0)} ms` : `${ms.toFixed(1)} ms`);

// Low-pass by averaging over the decimation window, then linear interpolation.
export function resample(input, fromRate) {
  if (fromRate === TARGET_RATE) return input;
  const ratio = fromRate / TARGET_RATE;
  const out = new Float32Array(Math.floor(input.length / ratio));
  const half = Math.max(1, Math.floor(ratio / 2));
  for (let i = 0; i < out.length; i++) {
    const center = i * ratio;
    const lo = Math.max(0, Math.floor(center) - half);
    const hi = Math.min(input.length - 1, Math.floor(center) + half);
    let sum = 0;
    for (let j = lo; j <= hi; j++) sum += input[j];
    out[i] = sum / (hi - lo + 1);
  }
  return out;
}

const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("tap", Tap);
`;

/**
 * Hold `button` (or Space) to record; on release, `onAudio(samples)` gets
 * 16 kHz mono Float32Array samples.
 */
export function pushToTalk({ button, level, setStatus, onAudio, isTyping = () => false }) {
  const mic = { ctx: null, chunks: [], recording: false };

  async function ensureMic() {
    if (mic.ctx) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await ctx.audioWorklet.addModule(url);
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "tap");
    node.port.onmessage = ({ data }) => {
      let peak = 0;
      for (const v of data) peak = Math.max(peak, Math.abs(v));
      level.style.width = `${Math.min(100, peak * 140)}%`;
      if (mic.recording) mic.chunks.push(data);
    };
    source.connect(node);
    mic.ctx = ctx;
  }

  async function start() {
    if (mic.recording) return;
    try {
      await ensureMic();
    } catch (err) {
      setStatus(`mic unavailable: ${err.message}. Try a clip instead.`, "error");
      return;
    }
    await mic.ctx.resume();
    mic.chunks = [];
    mic.recording = true;
    button.classList.add("live");
    setStatus("listening… release to send", "live");
  }

  async function stop() {
    if (!mic.recording) return;
    await new Promise((r) => setTimeout(r, 120));
    mic.recording = false;
    button.classList.remove("live");
    level.style.width = "0";
    const total = mic.chunks.reduce((n, c) => n + c.length, 0);
    const raw = new Float32Array(total);
    let offset = 0;
    for (const c of mic.chunks) {
      raw.set(c, offset);
      offset += c.length;
    }
    const samples = resample(raw, mic.ctx.sampleRate);
    if (samples.length < TARGET_RATE * 0.3) {
      setStatus("too short: hold the button while you speak", "error");
      return;
    }
    await onAudio(samples.slice(0, TARGET_RATE * MAX_SECONDS));
  }

  button.addEventListener("pointerdown", (e) => {
    button.setPointerCapture(e.pointerId);
    start();
  });
  button.addEventListener("pointerup", stop);
  button.addEventListener("pointercancel", stop);
  button.addEventListener("contextmenu", (e) => e.preventDefault());
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !e.repeat && !isTyping()) {
      e.preventDefault();
      start();
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && !isTyping()) {
      e.preventDefault();
      stop();
    }
  });
}

/** Play a bundled clip out loud and return it as 16 kHz samples. */
export async function playClip(name) {
  const bytes = await (await fetch(`/static/clips/${name}.wav`)).arrayBuffer();
  const ctx = new AudioContext();
  const audio = await ctx.decodeAudioData(bytes.slice(0));
  const src = ctx.createBufferSource();
  src.buffer = audio;
  src.connect(ctx.destination);
  src.start();
  setTimeout(() => ctx.close(), audio.duration * 1000 + 200);
  return new Float32Array(resample(audio.getChannelData(0), audio.sampleRate));
}

/** fetch() that returns the parsed JSON body and the browser-side round trip. */
export async function timedJson(url, init) {
  const t0 = performance.now();
  const res = await fetch(url, init);
  const rtt = performance.now() - t0;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  return { body, rtt };
}
