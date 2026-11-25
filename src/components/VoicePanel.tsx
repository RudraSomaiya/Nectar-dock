import { motion } from "framer-motion";

export type VoicePhase = "listening" | "transcribing" | "thinking" | "result" | "error" | "notice";

export interface VoiceState {
  state: VoicePhase;
  mode: "command" | "dictate";
  text: string;
  label?: string;
  icon?: string;
}

export type VoiceEvent =
  | { kind: "claude-usage"; state?: undefined; action?: undefined; [k: string]: unknown }
  | { kind: "level"; values: number[]; state?: undefined; action?: undefined }
  | { kind: "timer"; action: "start" | "reset" | "pause" | "resume"; seconds?: number; state?: undefined }
  | { kind: "stopwatch"; action: "start" | "stop" | "reset"; state?: undefined }
  | { kind?: "state"; state?: VoicePhase | "idle"; mode?: "command" | "dictate"; text?: string; label?: string; icon?: string; hold_ms?: number; action?: undefined };

const LABELS: Record<VoicePhase, string> = {
  listening: "Listening",
  transcribing: "Transcribing",
  thinking: "Working on it",
  result: "Done",
  error: "Couldn't do that",
  notice: "Notification",
};

const LIVE_WIDTH = 440;
const LIVE_LINES = 2;
const CHAR_W = 7.2;
const isLive = (v: VoiceState) => v.state === "listening" || v.state === "transcribing";

/** While you talk: only the newest words that fit in two lines, so the text keeps up with you. */
function liveTail(text: string) {
  const perLine = Math.floor((LIVE_WIDTH - 86) / CHAR_W);
  const max = perLine * LIVE_LINES - 3;
  if (text.length <= max) return text;
  const cut = text.slice(text.length - max);
  return "…" + cut.slice(cut.indexOf(" ") + 1);
}

/** Width and height the notch takes while voice is showing. */
export function voiceSize(v: VoiceState) {
  // A steady size while talking: the notch doesn't jump with every update.
  if (isLive(v)) return { width: LIVE_WIDTH, height: 62 + (v.text.length > 40 ? 18 : 0) };
  const text = v.text || "";
  if (v.state === "notice") {
    const width = Math.round(Math.min(460, Math.max(320, 110 + text.length * 7.6)));
    const perLine = Math.max(30, Math.floor((width - 96) / 7.6));
    const lines = Math.max(1, Math.min(3, Math.ceil(text.length / perLine)));
    return { width, height: 64 + (lines - 1) * 18 };
  }
  const width = Math.round(Math.min(460, Math.max(300, 96 + text.length * 7.4)));
  const perLine = Math.max(30, Math.floor((width - 86) / CHAR_W));
  const lines = Math.max(1, Math.min(5, Math.ceil(text.length / perLine)));
  return { width, height: 62 + (lines - 1) * 18 };
}

/** Bars follow the real mic level (newest on the right), so a dead or wrong mic is obvious. */
function Indicator({ phase, levels, icon }: { phase: VoicePhase; levels: number[]; icon?: string }) {
  if (phase === "listening") {
    const bars = levels.length ? levels.slice(-5) : [0.04, 0.04, 0.04, 0.04, 0.04];
    // Mirror around the middle bar so it reads like a waveform: [3, 1, 0 (newest), 2, 4].
    const order = [3, 1, 0, 2, 4].map((i) => bars[bars.length - 1 - i] ?? 0.04);
    return (
      <div className="voice-bars live" aria-hidden>
        {order.map((v, i) => (
          <span key={i} style={{ transform: `scaleY(${Math.max(0.12, Math.min(1, v))})` }} />
        ))}
      </div>
    );
  }
  if (phase === "notice" && icon) {
    return <img className="voice-app-icon" src={icon} alt="" draggable={false} />;
  }
  if (phase === "transcribing" || phase === "thinking") {
    return <div className="voice-spinner" aria-hidden />;
  }
  return <div className={`voice-badge ${phase}`} aria-hidden>{phase === "result" ? "✓" : phase === "notice" ? "🔔" : "!"}</div>;
}

export function VoicePanel({ voice, levels = [] }: { voice: VoiceState; levels?: number[] }) {
  const mode = voice.state === "notice" ? "" : voice.mode === "dictate" ? "Dictate" : "Command";
  const label = voice.label || LABELS[voice.state];
  const placeholder = voice.state === "listening" ? "Speak now…" : "";
  const text = isLive(voice) ? liveTail(voice.text) : voice.text;
  return (
    <motion.div
      key="voice-content"
      className={`voice-panel ${voice.state}`}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: 0.1 } }}
      transition={{ duration: 0.16 }}
    >
      <Indicator phase={voice.state} levels={levels} icon={voice.icon} />
      <div className="voice-body">
        <div className="voice-label">{mode ? `${label} · ${mode}` : label}</div>
        <div className={`voice-text ${text ? "" : "placeholder"} ${isLive(voice) ? "live" : ""} ${voice.state === "notice" ? "notice" : ""}`}>{text || placeholder}</div>
      </div>
    </motion.div>
  );
}
