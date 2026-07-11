#!/usr/bin/env python3
"""
Event-driven daemon that watches for News.app to launch.
Uses macOS NSWorkspace notifications - zero polling, zero CPU when idle.
When News opens, it spawns the main watcher.
"""

import subprocess
import time
import objc
from pathlib import Path

# PyObjC is needed for native macOS notifications
from AppKit import NSWorkspace, NSWorkspaceDidLaunchApplicationNotification, NSWorkspaceDidTerminateApplicationNotification
from Foundation import NSObject
from PyObjCTools import AppHelper

PROJECT_DIR = Path(__file__).parent
LOG_FILE = "/tmp/applenews-daemon.log"

# Stable project venv python (no `uv run` re-resolution -> instant, quiet start).
VENV_PYTHON = str(PROJECT_DIR / ".venv/bin/python3")

# Launcher run BY Terminal so the watcher inherits Terminal's Full Disk Access.
# (Full Disk Access only propagates from an FDA-holding GUI app; a process
# launched directly by launchd is denied. Terminal is that app; we just hide its
# window so nothing visibly pops up.)
WATCHER_COMMAND = str(PROJECT_DIR / "watcher_hidden.command")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    print(line)

def is_watcher_running():
    """True only if the watcher process is actually running.

    Matches the precise launch signature — the venv python interpreter running
    watch_likes.py — so it can't be fooled by unrelated processes that merely
    mention "watch_likes.py" in their argv (e.g. a shell `grep watch_likes.py`,
    or an editor). Such false positives previously made the daemon skip launching.
    """
    result = subprocess.run(["pgrep", "-af", r"watch_likes\.py"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        cmd = line.split(maxsplit=1)[1] if " " in line else ""
        # The real watcher runs the script under a python interpreter and is not
        # an incidental command line that happens to contain the script name.
        if "python" in cmd and cmd.rstrip().endswith("watch_likes.py"):
            return True
    return False

def start_watcher():
    """Launch the watcher via Terminal, then hide Terminal's window.

    Terminal hosts the watcher so it inherits Terminal's Full Disk Access (the
    only reliable way to read the Apple News container from a launchd-rooted
    setup). We immediately hide the watcher's window so nothing visibly pops up;
    the watcher runs in the hidden window for the life of the News session.

    Only the watcher's own window is hidden — any other Terminal windows you have
    open are left alone.
    """
    if not Path(VENV_PYTHON).exists():
        log(f"ERROR: venv python not found at {VENV_PYTHON}; run 'uv venv .venv' "
            f"and install deps. Cannot start watcher.")
        return

    # AppleScript: run the launcher in a NEW Terminal window, capture that exact
    # window, hide it, and don't bring Terminal to the front.
    script = f'''
    tell application "Terminal"
        set watcherTab to do script "{WATCHER_COMMAND}"
        set watcherWindow to window 1
        set visible of watcherWindow to false
    end tell
    '''
    log("Starting watcher (via hidden Terminal for Full Disk Access)...")
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            log("Watcher launched (Terminal window hidden)")
        else:
            log(f"Watcher launch osascript error: {result.stderr.strip()}")
    except Exception as e:
        log(f"Failed to launch watcher: {e}")


class AppWatcher(NSObject):
    def init(self):
        self = objc.super(AppWatcher, self).init()
        if self is None:
            return None

        # Register for app launch/terminate notifications
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(
            self, "appLaunched:", NSWorkspaceDidLaunchApplicationNotification, None
        )
        nc.addObserver_selector_name_object_(
            self, "appTerminated:", NSWorkspaceDidTerminateApplicationNotification, None
        )
        return self

    def appLaunched_(self, notification):
        app_name = notification.userInfo()["NSApplicationName"]
        if app_name == "News":
            log("News.app launched!")
            try:
                if is_watcher_running():
                    log("Watcher already running; not starting another")
                else:
                    start_watcher()
            except Exception as e:
                # PyObjC callbacks swallow exceptions silently; log them instead.
                import traceback
                log(f"ERROR in launch handler: {e}\n{traceback.format_exc()}")

    def appTerminated_(self, notification):
        app_name = notification.userInfo()["NSApplicationName"]
        if app_name == "News":
            log("News.app terminated")


def main():
    log("Event-driven daemon started - watching for News.app")

    # Check if News is already running
    result = subprocess.run(["pgrep", "-x", "News"], capture_output=True)
    if result.returncode == 0:
        log("News is already running")
        watcher_running = is_watcher_running()
        log(f"Watcher already running: {watcher_running}")
        if not watcher_running:
            start_watcher()

    # Set up the watcher. Keep a reference for the life of the process: it is the
    # only strong ref to the notification observer; dropping it would let PyObjC
    # deallocate the observer and silently stop delivery. (noqa: F841)
    watcher = AppWatcher.alloc().init()  # noqa: F841

    # Run the event loop (blocks forever, uses no CPU when idle)
    AppHelper.runConsoleEventLoop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Daemon stopped")
