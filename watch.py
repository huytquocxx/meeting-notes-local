"""Windows background detector with a Notion-style card and pill. Copies what REA found in Notion:
a card at the top centre when an app starts using the mic, a small draggable "listening" pill during
the call, and keeping the computer awake while recording.

It never records without a click on Start. Text is made during the call. When the mic has been free for
a minute, or you press Stop, the transcript is finished and the minutes agent writes minutes.md and a draft
email. Start it with watch.bat. Log: meetings/watch.log
"""
import atexit
import ctypes
import faulthandler
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import winreg
from pathlib import Path

import defects
import notes

POLL_SECONDS = 5
START_AFTER = 2  # polls in use before the card, so a quick mic check does not trigger it
END_AFTER = 12  # polls free before stopping, so a dropped call or a short gap does not end the recording
OFFER_SECONDS = 60  # an unanswered card counts as Dismiss
MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
IGNORED = ("python.exe", "pythonw.exe")  # the recorder itself uses the mic
KEEP_AWAKE, ALLOW_SLEEP = 0x80000001, 0x80000000

BG, BORDER, TEXT, MUTED, BLUE, RED = "#ffffff", "#e6e5e3", "#37352f", "#787774", "#2383e2", "#eb5757"
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def apps_in_key(root):
    """Names of the apps under this registry key that are using the mic now (stop time of 0)."""
    apps = []
    for i in range(winreg.QueryInfoKey(root)[0]):
        name = winreg.EnumKey(root, i)
        with winreg.OpenKey(root, name) as app:
            try:
                in_use = winreg.QueryValueEx(app, "LastUsedTimeStop")[0] == 0
                started = winreg.QueryValueEx(app, "LastUsedTimeStart")[0] != 0
            except FileNotFoundError:  # the NonPackaged folder itself has no times
                continue
        if in_use and started:
            apps.append(name.split("#")[-1])
    return apps


def mic_apps(ignored=IGNORED):
    """Apps using the mic now, skipping the recorder."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY) as root:
        apps = apps_in_key(root)
        try:
            with winreg.OpenKey(root, "NonPackaged") as desktop_apps:
                apps += apps_in_key(desktop_apps)
        except FileNotFoundError:
            pass
    return [a for a in apps if a.lower() not in ignored]


class Pill:
    """The on-screen card and pill. Slots: offer, pill, done. Other threads use post()."""

    def __init__(self):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        self.root = tk.Tk()
        self.root.withdraw()
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.slots = {}
        self.queue = queue.Queue()
        self.drain()

    def post(self, fn, *args):
        self.queue.put((fn, args))

    def drain(self):
        try:
            while not self.queue.empty():
                fn, args = self.queue.get()
                fn(*args)
        finally:  # one failed card must not stop all later cards
            self.root.after(200, self.drain)

    def close(self, slot):
        win = self.slots.pop(slot, None)
        if win:
            win.destroy()

    def window(self, slot, width, height, x=None, y=8):
        self.close(slot)
        w, h, s = int(width * self.scale), int(height * self.scale), self.scale
        x = (self.root.winfo_screenwidth() - w) // 2 if x is None else self.root.winfo_screenwidth() - w - int(x * s)
        win = tk.Toplevel(self.root, bg=BORDER, padx=1, pady=1)
        win.overrideredirect(True)
        win.attributes("-topmost", True, "-alpha", 0.0)
        win.geometry(f"{w}x{h}+{x}+{int(y * s)}")
        self.slots[slot] = win
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        corner = ctypes.c_int(2)  # Windows 11 rounded corners
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        self.fade(win, 0.0)
        body = tk.Frame(win, bg=BG, padx=int(14 * s), pady=int(8 * s))
        body.pack(fill="both", expand=True)
        return win, body

    def fade(self, win, alpha):
        if win.winfo_exists() and alpha < 1:
            win.attributes("-alpha", min(alpha + 0.1, 1.0))
            win.after(16, self.fade, win, alpha + 0.1)

    def label(self, parent, text, size=10, color=TEXT, bold=False):
        font = ("Segoe UI", size, "bold" if bold else "normal")
        return tk.Label(parent, text=text, bg=BG, fg=color, font=font, anchor="w", justify="left")

    def button(self, parent, text, command, primary=False):
        bg, fg = (BLUE, "#ffffff") if primary else ("#f1f1ef", TEXT)
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                         relief="flat", bd=0, padx=12, pady=3, cursor="hand2", font=("Segoe UI", 9))

    def offer(self, apps, start, dismiss):
        win, body = self.window("offer", 480, 112)
        self.label(body, "Meeting detected", 11, bold=True).pack(fill="x")
        self.label(body, f"{', '.join(apps)} is using your mic. Tell people before you record.", 9, MUTED).pack(fill="x")
        row = tk.Frame(body, bg=BG)
        row.pack(side="bottom", fill="x")
        self.button(row, "Start transcribing", start, primary=True).pack(side="left")
        self.button(row, "Dismiss", dismiss).pack(side="left", padx=6)
        win.after(OFFER_SECONDS * 1000, dismiss)

    def listening(self, started_at, stop):
        win, body = self.window("pill", 220, 44, x=16)
        dot = self.label(body, "●", 10, RED)
        dot.pack(side="left")
        clock = self.label(body, "Listening 00:00", 9)
        clock.pack(side="left", padx=6)
        self.button(body, "Stop", stop).pack(side="right")

        def update():
            minutes, seconds = divmod(int(time.time() - started_at), 60)
            clock.config(text=f"Listening {minutes:02d}:{seconds:02d}")
            win.after(1000, update)

        def press(event):
            win.grab = (event.x_root - win.winfo_x(), event.y_root - win.winfo_y())

        def drag(event):
            win.geometry(f"+{event.x_root - win.grab[0]}+{event.y_root - win.grab[1]}")

        update()
        for part in (body, clock, dot):
            part.bind("<Button-1>", press)
            part.bind("<B1-Motion>", drag)

    def working(self, text):
        _, body = self.window("pill", 220, 44, x=16)
        self.label(body, text, 9, MUTED).pack(side="left")

    def done(self, title, text, folder):
        _, body = self.window("done", 480, 112, y=128)
        self.label(body, title, 11, bold=True).pack(fill="x")
        self.label(body, text, 9, MUTED).pack(fill="x")
        row = tk.Frame(body, bg=BG)
        row.pack(side="bottom", fill="x")
        self.button(row, "Open folder", lambda: (os.startfile(folder), self.close("done")), primary=True).pack(side="left")
        self.button(row, "Close", lambda: self.close("done")).pack(side="left", padx=6)


class Watcher:
    def __init__(self, ui, in_use=mic_apps, recorder=notes.LiveRecorder, log=print):
        self.ui, self.in_use, self.new_recorder, self.log = ui, in_use, recorder, log
        self.busy = self.idle = self.saving = 0
        self.recorder = None
        self.offered = self.declined = False

    def tick(self):
        apps = self.in_use()
        self.busy = self.busy + 1 if apps else 0
        self.idle = 0 if apps else self.idle + 1
        if self.recorder:
            if self.idle >= END_AFTER:
                self.finish()
        elif self.offered:
            if self.idle >= START_AFTER:  # the call ended before an answer
                self.offered = False
                self.ui.close("offer")
        elif self.declined:
            if self.idle >= END_AFTER:
                self.declined = False
        elif self.busy >= START_AFTER:
            self.offered = True
            self.ui.offer(apps, lambda: self.start(apps), self.dismiss)

    def dismiss(self):
        if self.offered:
            self.offered, self.declined = False, True
            self.ui.close("offer")

    def start(self, apps):
        self.offered = False
        self.ui.close("offer")
        try:
            self.recorder = self.new_recorder()
            self.recorder.start()
            self.log(f"Recording {self.recorder.folder} ({', '.join(apps)})")
            self.ui.listening(self.recorder.started_at, self.finish)
        except Exception:
            self.recorder, self.declined = None, True
            self.log(traceback.format_exc())
            self.ui.done("Could not start recording", "See meetings/watch.log", str(notes.MEETINGS))

    def finish(self):
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        self.declined = bool(self.in_use())  # Stop pressed during the call: do not offer again for this call
        self.saving += 1
        self.ui.working("Transcribing...")
        # in the background, so a back-to-back meeting is still noticed
        threading.Thread(target=self.save, args=(recorder,)).start()

    def save(self, recorder):
        try:
            folder = recorder.stop()
            self.log(f"Saved {folder}")
            try:
                defects.export(folder)
            except Exception:  # the agent reports that the defect check was skipped
                self.log(traceback.format_exc())
            self.ui.post(self.ui.working, "Writing minutes...")
            try:
                notes.run_agent(folder)
                result = ("Minutes ready", f"Minutes and a draft email are in {Path(folder).name}.", str(folder))
            except Exception:
                self.log(traceback.format_exc())
                result = ("Transcript ready", f"The agent failed. Ask Claude for the minutes of {Path(folder).name}.", str(folder))
        except Exception:
            self.log(traceback.format_exc())
            result = ("Transcript failed", "Your audio is safe. See meetings/watch.log", str(recorder.folder))
        self.ui.post(self.saved, result)

    def saved(self, result):
        self.saving -= 1
        if not self.recorder and not self.saving:
            self.ui.close("pill")
        self.ui.done(*result)


def main():
    # pythonw has no console, so output goes to a file
    notes.MEETINGS.mkdir(exist_ok=True)
    sys.stdout = sys.stderr = open(notes.MEETINGS / "watch.log", "a", encoding="utf-8", buffering=1)
    mutex = kernel32.CreateMutexW(None, False, "meeting-notes-watch")
    if ctypes.get_last_error() == 183:  # already running
        return
    print(f"Started {time.ctime()}")
    # so a quiet stop leaves a trace: a crash in Tk or sound card code, a normal exit, or Windows closing apps
    faulthandler.enable(sys.stderr)
    atexit.register(lambda: print(f"Stopped {time.ctime()}"))
    ui = Pill()
    ui.root.protocol("WM_SAVE_YOURSELF", lambda: print(f"Windows asked apps to close {time.ctime()}"))
    watcher = Watcher(ui)
    alive = notes.MEETINGS / "watch.alive"  # its time shows when the detector last ran

    def loop():
        try:
            alive.write_text(time.ctime(), encoding="utf-8")
            watcher.tick()
            # as Notion does: a sleeping computer would stop the recording and the transcript
            kernel32.SetThreadExecutionState(KEEP_AWAKE if watcher.recorder or watcher.saving else ALLOW_SLEEP)
        except Exception:
            traceback.print_exc()
        ui.root.after(POLL_SECONDS * 1000, loop)

    loop()
    ui.root.mainloop()
    del mutex


if __name__ == "__main__":
    main()
