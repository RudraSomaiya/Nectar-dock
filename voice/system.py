"""System controls: volume, media keys, brightness, theme, awake, lock, power, radios, battery."""
import ctypes
import subprocess
import threading
import winreg

import keyboard
import psutil

from common import log

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


# ---- volume -------------------------------------------------------------------------------
def _endpoint():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume():
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(level):
    level = max(0, min(100, int(level)))
    ep = _endpoint()
    ep.SetMute(0, None)
    ep.SetMasterVolumeLevelScalar(level / 100, None)
    return f"Volume {level}%."


def change_volume(delta):
    return set_volume(get_volume() + int(delta))


def mute(on=True):
    _endpoint().SetMute(1 if on else 0, None)
    return "Muted." if on else "Unmuted."


# ---- media --------------------------------------------------------------------------------
MEDIA_KEYS = {"play_pause": "play/pause media", "play": "play/pause media", "pause": "play/pause media",
              "next": "next track", "previous": "previous track", "stop": "stop media"}


def media(action):
    key = MEDIA_KEYS.get(action)
    if not key:
        return f"Unknown media action {action}."
    keyboard.send(key)
    return {"next": "Next track.", "previous": "Previous track.", "stop": "Stopped."}.get(action, "Done.")


# ---- brightness ---------------------------------------------------------------------------
def get_brightness():
    import screen_brightness_control as sbc
    try:
        return sbc.get_brightness(display=0)[0]
    except Exception:
        return None


def set_brightness(level=None, delta=None):
    import screen_brightness_control as sbc
    try:
        if delta is not None:
            cur = get_brightness() or 50
            level = cur + int(delta)
        level = max(0, min(100, int(level)))
        sbc.set_brightness(level)
        return f"Brightness {level}%."
    except Exception as e:
        log(f"brightness failed: {e}")
        return "I couldn't change the brightness on this screen."


# ---- theme --------------------------------------------------------------------------------
def set_theme(mode):
    light = 1 if mode == "light" else 0
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, light)
    winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, light)
    winreg.CloseKey(key)
    # Tell open apps the setting changed.
    user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 1000, None)
    return f"{mode.capitalize()} mode on."


# ---- keep awake ---------------------------------------------------------------------------
_awake = {"thread": None, "stop": None, "until": None}
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002


def keep_awake(minutes=None):
    stop_awake()
    ev = threading.Event()

    def run():
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        ev.wait(minutes * 60 if minutes else None)
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    t = threading.Thread(target=run, daemon=True)
    _awake.update(thread=t, stop=ev)
    t.start()
    return f"Keeping the PC awake for {minutes} minutes." if minutes else "Keeping the PC awake until you say stop."


def stop_awake():
    if _awake["stop"]:
        _awake["stop"].set()
        _awake["stop"] = None
        return "The PC can sleep again."
    return "It wasn't being kept awake."


# ---- lock / power -------------------------------------------------------------------------
def lock():
    user32.LockWorkStation()
    return "Locked."


def power(action):
    if action == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "5"], creationflags=subprocess.CREATE_NO_WINDOW)
        return "Shutting down in 5 seconds."
    if action == "restart":
        subprocess.Popen(["shutdown", "/r", "/t", "5"], creationflags=subprocess.CREATE_NO_WINDOW)
        return "Restarting in 5 seconds."
    if action == "sleep":
        threading.Timer(1.5, lambda: ctypes.windll.powrprof.SetSuspendState(0, 1, 0)).start()
        return "Going to sleep."
    if action == "sign_out":
        subprocess.Popen(["shutdown", "/l"], creationflags=subprocess.CREATE_NO_WINDOW)
        return "Signing out."
    if action == "cancel":
        subprocess.run(["shutdown", "/a"], creationflags=subprocess.CREATE_NO_WINDOW)
        return "Cancelled."
    return f"Unknown power action {action}."


# ---- Wi-Fi / Bluetooth (Windows radio switches, same as Quick Settings) --------------------
RADIO_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$g = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t, $type) { $n = $g.MakeGenericMethod($type).Invoke($null, @($t)); $n.Wait(-1) | Out-Null; $n.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | ? { $_.Kind -eq '__KIND__' } | Select-Object -First 1
if (-not $r) { 'none'; exit }
(Await ($r.SetStateAsync('__STATE__')) ([Windows.Devices.Radios.RadioAccessStatus])).ToString()
"""


def radio(kind, on):
    kind_name = "WiFi" if kind == "wifi" else "Bluetooth"
    script = RADIO_PS.replace("__KIND__", kind_name).replace("__STATE__", "On" if on else "Off")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True,
                         timeout=20, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
    label = "Wi-Fi" if kind == "wifi" else "Bluetooth"
    if out.endswith("Allowed"):
        return f"{label} {'on' if on else 'off'}."
    if out == "none":
        return f"No {label} radio found."
    log(f"radio {kind} -> {out!r}")
    return f"I couldn't switch {label}."


# ---- status -------------------------------------------------------------------------------
def battery():
    b = psutil.sensors_battery()
    if not b:
        return "No battery found."
    s = f"Battery {round(b.percent)}%"
    if b.power_plugged:
        s += ", charging" if b.percent < 100 else ", plugged in"
    elif b.secsleft and b.secsleft > 0:
        h, m = divmod(b.secsleft // 60, 60)
        s += f", about {h} h {m} min left"
    return s + "."


def stats():
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("C:\\")
    return (f"CPU {psutil.cpu_percent(interval=0.4):.0f}%, RAM {vm.percent:.0f}% "
            f"({vm.used / 2**30:.1f} of {vm.total / 2**30:.1f} GB), C: {du.percent:.0f}% full.")
