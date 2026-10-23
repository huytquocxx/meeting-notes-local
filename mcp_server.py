"""Claude tool (MCP server) for local meeting notes. Everything stays on this computer.

Tools: start_recording, stop_recording, recording_status, get_meeting, list_meetings.
Claude calls them, so you do not copy and paste anything.
"""
import sys
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import notes

mcp = FastMCP("meeting-notes")
_recorder = notes.Recorder()
_jobs = {}  # folder name -> "transcribing" | "done" | "failed: reason"


def _log(msg):  # stdout belongs to the MCP protocol, so log to stderr
    print(msg, file=sys.stderr, flush=True)


def _transcribe_in_background(folder: Path):
    def run():
        try:
            notes.transcribe(folder, log=_log)
            _jobs[folder.name] = "done"
        except Exception as e:
            _jobs[folder.name] = f"failed: {e}"

    _jobs[folder.name] = "transcribing"
    threading.Thread(target=run, daemon=True).start()


def _latest():
    folders = sorted(p for p in notes.MEETINGS.glob("*") if p.is_dir()) if notes.MEETINGS.exists() else []
    return folders[-1] if folders else None


@mcp.tool()
def start_recording() -> str:
    """Start recording the user's microphone and the meeting sound on this computer.
    Tell the user to wear headphones and to let attendees know the meeting is being recorded."""
    try:
        _recorder.start()
    except Exception as e:
        return f"Could not start: {e}"
    devices = ", ".join(f"{k}: {v}" for k, v in _recorder.devices.items())
    extra = (" NOTE: " + " ".join(_recorder.notes)) if _recorder.notes else ""
    return f"Recording started ({devices}).{extra}"


@mcp.tool()
def stop_recording() -> str:
    """Stop recording and begin transcribing in the background. Then use recording_status
    until it is done, and get_meeting to read the transcript."""
    if not _recorder.running:
        return "Not recording."
    try:
        folder = _recorder.stop()
    except Exception as e:
        return f"Could not stop cleanly: {e}"
    _transcribe_in_background(folder)
    return f"Stopped. Saved audio in meeting '{folder.name}'. Transcribing now; check recording_status."


@mcp.tool()
def recording_status() -> str:
    """Say whether a recording is running and the state of the latest transcription."""
    parts = []
    if _recorder.running:
        parts.append(f"Recording for {int(time.time() - _recorder.started_at)} seconds.")
    else:
        parts.append("Not recording.")
    latest = _latest()
    if latest:
        state = _jobs.get(latest.name) or ("done" if (latest / "transcript.md").exists() else "no transcript")
        parts.append(f"Latest meeting '{latest.name}': {state}.")
    return " ".join(parts)


@mcp.tool()
def list_meetings() -> str:
    """List saved meetings, newest last."""
    folders = sorted(p for p in notes.MEETINGS.glob("*") if p.is_dir()) if notes.MEETINGS.exists() else []
    if not folders:
        return "No meetings yet."
    return "\n".join(f"{p.name}  transcript: {'yes' if (p / 'transcript.md').exists() else 'no'}" for p in folders)


@mcp.tool()
def get_meeting(meeting: str = "latest") -> str:
    """Return the transcript of a meeting together with recap instructions. Follow the instructions:
    propose speaker names with evidence, list sample lines for unnamed speakers, then recap.
    `meeting` is a folder name from list_meetings, or 'latest'."""
    folder = _latest() if meeting == "latest" else notes.MEETINGS / meeting
    if folder is None or not folder.is_dir():
        return "Meeting not found."
    request = folder / "recap-request.md"
    if not request.exists():
        return f"No transcript yet. Status: {_jobs.get(folder.name, 'unknown')}."
    return request.read_text(encoding="utf-8")


if __name__ == "__main__":
    mcp.run()
