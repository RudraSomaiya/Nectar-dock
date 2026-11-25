"""Checks the instant rules against real phrases (from heynixi history and common commands).
Nothing is executed. Run: python test_rules.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apps  # noqa: E402
import rules  # noqa: E402
import textops  # noqa: E402

apps.load_index()

CASES = [
    # (spoken, expected tool or None for "goes to AI")
    ("open cloud application", "open_app"),
    ("Open brief browser", "open_app"),
    ("open whisperflow", "open_app"),
    ("open task manager", "open_app"),
    ("Open Valorant", "open_app"),
    ("open youtube", "open_url"),
    ("open gmail", "open_url"),
    ("close notepad", "close_app"),
    ("close one of the brave instances", "close_app"),
    ("close brave browser", "close_app"),
    ("close all applications", None),
    ("open brave and then open notepad", None),
    ("increase brightness to maximum", "brightness"),
    ("increase brightness to 100%", "brightness"),
    ("brightness down", "brightness"),
    ("set volume to 40", "volume"),
    ("volume up", "volume"),
    ("turn the volume down by 20", "volume"),
    ("mute", "volume"),
    ("next", "media"),
    ("next song", "media"),
    ("pause the music", "media"),
    ("start a five minute timer", "timer"),
    ("set a timer for 90 seconds", "timer"),
    ("cancel the timer that is currently running", "cancel_timer"),
    ("click F on my keyboard", "press_keys"),
    ("click zero on my keyboard", "press_keys"),
    ("press ctrl plus shift plus t", "press_keys"),
    ("lock my laptop", "lock_pc"),
    ("dark mode", "theme"),
    ("what's my battery", "battery"),
    ("minimize this window", "window"),
    ("show desktop", "window"),
    ("Hey Nixi, open spotify.", "open_app"),
    ("search youtube for zerkaa gta 6 and click on the first video to play it", None),
    ("what is the capital of japan", None),
    ("shut down my laptop", None),
    ("make this text longer", None),
    # From the 25 Nov session
    ("Open Brave", "open_app"),
    ("Open a new tab", "press_keys"),
    ("Open a new browser tab", "press_keys"),
    ("close this tab", "press_keys"),
    ("reopen the last closed tab", "press_keys"),
    ("go back", "press_keys"),
    ("previous track", "media"),
    ("Start a 5 minute timer", "timer"),
    ("Show the 5 minute timer you just started", None),
    ("pause the timer", "pause_timer"),
    ("go fullscreen", "fullscreen"),
    ("Set Volume to Max", None),
    ("Set Volume to 30", "volume"),
    ("Search for Valorant", None),
    ("open add a new tap", "open_app"),  # really is that app's name
    ("Close this instance of Brave", "close_app"),
    ("Close the stab", "press_keys"),
    ("Go out of fullscreen", "fullscreen"),
    ("Open Tab 1", "browser_tab"),
    ("go to the last tab", "browser_tab"),
    ("Open the already existing Gmail tab", None),
]

ok = 0
for spoken, want in CASES:
    hit = rules.match(textops.apply_aliases(spoken))
    got = hit[0] if hit else None
    mark = "ok " if got == want else "BAD"
    ok += got == want
    extra = f" {hit[1]}" if hit else ""
    print(f"{mark} {spoken!r:70} -> {got}{extra}")
print(f"\n{ok}/{len(CASES)} as expected")
for name in ["brave", "claude", "wispr flow", "valorant", "task manager", "spotify", "notepad"]:
    a = apps.find_app(name)
    print(f"find_app({name!r}) -> {a['name'] if a else None}")
