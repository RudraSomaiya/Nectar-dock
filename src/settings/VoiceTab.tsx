import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Mic, Sparkles, Timer, Bell, Keyboard, Gauge } from "lucide-react";
import { SettingRow } from "./SettingRow";
import { Dropdown } from "./Dropdown";
import { SettingSection } from "./SettingSection";

/** Settings for Nectar Voice (voice/core.py reads these keys from settings.json). */
function useStored(key: string, fallback: string, allowed: string[]) {
  const [value, setValue] = useState(() => {
    const saved = localStorage.getItem(key);
    return saved && allowed.includes(saved) ? saved : fallback;
  });
  const change = (next: string) => {
    setValue(next);
    localStorage.setItem(key, next);
    invoke("save_setting", { key, value: next }).catch(() => { });
  };
  return [value, change] as const;
}

const MODES = ["raw", "clean", "polished"];
const IDLE = ["300", "600", "1800", "0"];

export function VoiceTab() {
  const [mode, setMode] = useStored("nectar-voice-dictation", "polished", MODES);
  const [idle, setIdle] = useStored("nectar-voice-idle", "600", IDLE);
  const [notify, setNotify] = useStored("nectar-voice-notifications", "true", ["true", "false"]);
  const [usage, setUsage] = useStored("nectar-voice-claude-usage", "false", ["true", "false"]);

  return (
    <>
      <SettingSection id="voice-dictation" title="Dictation" defaultOpen>
      <SettingRow
        icon={Sparkles}
        label="Dictation style"
        desc={
          mode === "raw"
            ? "Raw: exactly what the speech model heard. Nothing is sent online."
            : mode === "clean"
              ? "Clean: removes um/uh, applies \"scratch that\" and spoken punctuation. Runs on this PC."
              : "Polished: Groq also fixes grammar, punctuation and misheard words. Falls back to raw when offline."
        }
      >
        <Dropdown
          value={mode}
          onChange={setMode}
          options={[
            { value: "raw", label: "Raw" },
            { value: "clean", label: "Clean" },
            { value: "polished", label: "Polished" },
          ]}
        />
      </SettingRow>
      <SettingRow icon={Keyboard} label="Keys" desc="Hold Right Alt to dictate, Right Ctrl for commands." divider={false}>
        <span className="setting-desc">Right Alt · Right Ctrl</span>
      </SettingRow>

      </SettingSection>
      <SettingSection id="voice-model" title="Speech model" defaultOpen>
      <SettingRow
        icon={Timer}
        label="Unload when idle"
        desc="The speech model frees about 700 MB of RAM and its GPU memory after this long unused. The next use takes a few seconds to load."
      >
        <Dropdown
          value={idle}
          onChange={setIdle}
          options={[
            { value: "300", label: "5 min" },
            { value: "600", label: "10 min" },
            { value: "1800", label: "30 min" },
            { value: "0", label: "Never" },
          ]}
        />
      </SettingRow>
      <SettingRow icon={Mic} label="Microphone" desc="Voice uses the Windows default input unless you pick one: say “use the laptop mic” or “which mic are you using”." divider={false}>
        <span className="setting-desc">By voice</span>
      </SettingRow>

      </SettingSection>
      <SettingSection id="voice-notch" title="Notch" defaultOpen>
      <SettingRow
        icon={Bell}
        label="Show notifications"
        desc="Windows notifications (WhatsApp, Discord, Claude…) appear in the notch for a few seconds."
      >
        <Dropdown
          value={notify}
          onChange={setNotify}
          options={[
            { value: "true", label: "On" },
            { value: "false", label: "Off" },
          ]}
        />
      </SettingRow>
      <SettingRow
        icon={Gauge}
        label="Claude usage widget"
        desc="Fills the “Claude usage” status widget (add it under Notch › Widgets) with your 5-hour and weekly limits. It reads the sign-in Claude Code saved on this PC and asks only api.anthropic.com, every 5 minutes."
        divider={false}
      >
        <Dropdown
          value={usage}
          onChange={setUsage}
          options={[
            { value: "true", label: "On" },
            { value: "false", label: "Off" },
          ]}
        />
      </SettingRow>
      </SettingSection>
    </>
  );
}
