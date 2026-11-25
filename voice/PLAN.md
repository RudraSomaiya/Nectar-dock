# Nectar Voice: plan

Voice dictation and voice commands built into the Nectar notch. It replaces heynixi. Our own code, nothing copied from heynixi.

## Parts

| Part | Language | Always running? | Job |
|---|---|---|---|
| **Nectar** (notch, optional dock) | Rust + React | yes | Shows listening/thinking/result in the notch; starts and stops the voice core |
| **voice-core** | Python | yes, small (~40–60 MB) | Hotkeys, microphone, voice detection, fast command rules, Groq calls, runs the tools |
| **stt-worker** | Python | **only when needed** | Whisper large-v3-turbo on the GPU (CUDA). Starts on the first key press and quits after a set idle time |

Reused, not downloaded again: the Whisper turbo model and the CUDA/cuDNN files already on this PC. The Groq key is also reused.

## Idle RAM (measured on this laptop, 25 Nov)

| | RAM |
|---|---|
| Python process alone | 29 MB |
| With Whisper loaded on the GPU | **~715 MB** RAM + ~1.6 GB GPU memory (mostly the CUDA libraries) |
| After unloading the model *inside the same process* | still ~700 MB, so unloading isn't enough. The worker has to quit to give the memory back |

Load time: 3.2 s (files in Windows' cache) to 7.8 s (cold disk), plus the first transcription warming up (0.8–4 s).

How we handle it:
1. **The worker quits when idle** (default 10 min, configurable; 0 = always loaded). That frees all ~715 MB of RAM and the GPU memory.
2. **The worker starts the moment you press the key**, so it loads while you're still talking. Recording starts in voice-core right away, so no words are lost.
3. **If you finish speaking before the worker is ready**, there are two choices (a setting): wait the extra 2–3 s, or send just that one clip to Groq's Whisper (~0.4 s, but the audio leaves the PC).
4. **While warm but unused**, the worker's working set is trimmed so Task Manager shows little until it's used again.
5. In Nectar: turn on Memory Saver (`nectar-ram-saver`) and load windows only when opened (already built in).

Target: about **60–100 MB in total when idle** (Nectar plus voice-core), against heynixi's ~430 MB that never goes away.

## How commands flow
1. **Fast rules** (instant, no AI): open/close/switch app, volume, brightness, media keys, timers, simple key presses, saved sites. Apps are matched by how they **sound**, plus your own aliases (brief → Brave, cloud → Claude). If a rule isn't sure, it passes the request to the AI instead of guessing.
2. **AI router (Groq)**: picks a category first, then sends only that category's tools using Groq's built-in function calling. That keeps prompts around 1–2k tokens instead of heynixi's 5.5k.
3. **Plans**: one sentence can become several steps (open → new tab → search → click first result → fullscreen). Each step is checked before the next one runs.
4. **Context**: selected text, a dropped file, the file selected in Explorer, and the active window go to the AI only when the request needs them.
5. **Safety**: it never presses buy/pay/send/delete by itself. Shutdown, closing everything, and file moves always ask first.

Models: `qwen/qwen3.8-27b` (main and vision), then `openai/gpt-oss-120b`, then `gpt-oss-20b` as fallbacks when a rate limit hits.

## Tools (target: all of heynixi's ~90 plus ~30 extras)
The tool count doesn't make prompts bigger, because only the relevant category is sent.

**Phase 1: dictation and core commands**
- Dictate: hold a key, text is typed into the box you're in. Clean (rules only) or Polished (Groq), personal dictionary, spoken fixes ("scratch that", "no I mean"), your own snippets
- Rewrite selected text with any wording; write a draft straight into the box you're in (or to the clipboard)
- Apps: open/focus, close (one window, all windows, tray apps, "everything except X"), list running apps, sound-alike names and aliases
- Windows: minimize, maximize, snap, move to another monitor, show desktop
- Media: play/pause, next, previous, what's playing, volume up/down/set/mute
- System: brightness (laptop screen and external monitors), dark/light mode, keep awake, lock, sleep / restart / shut down (with a confirmation), Wi-Fi and Bluetooth on/off, battery and stats
- Keys: press any key or shortcut ("press F", "press zero", "ctrl shift t")
- Timers: timer, stopwatch, reminders, list, **cancel**
- Questions: short answers in the notch

**Phase 2: browser and screen**
- Web: open URL, search on any site, saved sites by name, new tab/close tab/reload/back
- YouTube: search, play first/nth result, fullscreen, skip
- Screen actions using Windows' accessibility system: click X, tick a box, choose an option, fill in a field, focus a box
- Screen understanding with the vision model: "what's on my screen", "what does this error say", "where is X" (draws a ring around it)
- Text on screen to clipboard (Windows' built-in OCR): "copy the password in row 3", "copy the text in this image"
- Camera: open it **and take the photo**; screenshot / snip

**Phase 3: files, writing, productivity**
- Files: find, open recent, a real AI summary of a dropped or Explorer-selected file (PDF, DOCX, PPTX, XLSX, image, audio), questions about it, convert/resize images, make folder, tidy a folder (plan, confirm, undo)
- Email/messages: Gmail draft, reply in the open chat, WhatsApp/Slack/Discord drafts (always ask before sending)
- Productivity: tasks, notes, daily note, habits, water tracker, focus/Pomodoro, day briefing, calendar add/list/free time
- Memory and routines: "remember that…", "create a routine called start my day: …"
- Research: search the web, read a few pages, answer with sources, shown in the notch
- Teleprompter; a multi-step agent mode with safe tools only

**Extras heynixi doesn't have (ideas)**
- Clipboard history by voice ("paste the second-last thing I copied")
- "Explain the selected code / error" and "translate the selection to Hindi"
- Per-app dictation styles you define (formal in Gmail, casual in WhatsApp)
- Undo for the last voice action
- A command log page in Nectar's settings

## Moving off heynixi
1. Phase 1 works, and you use it for a day.
2. Move the Whisper model and CUDA files to `D:\Programs\nectar-voice\` and remove heynixi's junctions first, so its uninstaller can't delete them.
3. Uninstall heynixi. The Groq bridge goes with it.
