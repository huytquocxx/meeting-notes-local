# Local meeting notes

Records your mic and the meeting sound, turns it into a transcript on your own computer, and hands it to
Claude for a recap. Works two ways: you run it yourself, or Claude runs it as a tool.

Everything is processed on this computer. Only the transcript text goes to Claude, when you or Claude send it.
Tell people in the meeting that you are recording.

## Install on another computer
1. Install Python 3.10 or newer from python.org (Windows: tick "Add to PATH").
2. Unzip `meeting-notes-portable.zip` somewhere permanent (the Claude tool remembers the path).
3. Run setup once:
   - **Windows:** double-click `setup.bat`
   - **Mac:** double-click `setup.command` (first time: right-click, Open)

   It installs the packages and downloads the models (about 600 MB, first time only).

## Mac: capture meeting sound
Windows records meeting sound directly. A Mac cannot, so it needs the free **BlackHole** driver:
1. Install BlackHole 2ch (blackhole-2ch from existential.audio, or `brew install blackhole-2ch`).
2. Open **Audio MIDI Setup**, click **+**, choose **Create Multi-Output Device**.
3. Tick your headphones **and** BlackHole 2ch. Set that Multi-Output Device as your sound output.
   You still hear the meeting, and BlackHole copies it for the recorder.
4. When macOS asks, allow Terminal (or your Python) to use the microphone.

Without BlackHole, the Mac version records your microphone only and says so when it starts.
The Mac version is untested: it was built and tested on Windows only.

## Use it yourself
1. Start recording: Windows `record.bat`, Mac `record.command`.
2. Press Ctrl+C when the meeting ends. It transcribes by itself.
3. A ready-made recap request is copied to your clipboard. Paste it into Claude and press Enter.
4. Files are saved in `meetings/<date_time>/`: audio, `transcript.md`, `recap-request.md`.

## Let Claude run it (Claude tool)
Claude can then start and stop recordings and read the transcript itself, with no pasting.
Tools: `start_recording`, `stop_recording`, `recording_status`, `list_meetings`, `get_meeting`.

Replace `INSTALL_FOLDER` with the folder where you unzipped it.

**Claude Code:**
- Windows: `claude mcp add meeting-notes -- INSTALL_FOLDER\.venv\Scripts\python.exe INSTALL_FOLDER\mcp_server.py`
- Mac: `claude mcp add meeting-notes -- INSTALL_FOLDER/.venv/bin/python INSTALL_FOLDER/mcp_server.py`

**Claude Desktop:** open Settings, Developer, Edit Config, and add this inside `mcpServers`
(Mac path: `INSTALL_FOLDER/.venv/bin/python`; on Windows double the backslashes):
```json
"meeting-notes": {
  "command": "INSTALL_FOLDER\\.venv\\Scripts\\python.exe",
  "args": ["INSTALL_FOLDER\\mcp_server.py"]
}
```
Restart Claude, then say "start recording my meeting" and, later, "stop and recap it".

## Who said what
- Your mic is **You**. Everyone in the meeting sound is split by voice into **Speaker 1**, **Speaker 2**, and so on.
- It cannot know real names by itself. The recap request asks Claude to work them out from what people say,
  show the evidence, and list sample lines for anyone it cannot name. Reply with corrections
  ("Speaker 2 is Beatrice") and Claude redoes the recap.
- If you are unsure who someone is, say so. Claude keeps them as "Speaker N" and does not guess.
- **Wear headphones.** With speakers, your mic hears the others and repeats their words as "You".
- Tested with two computer voices taking turns, and it separated them correctly. Real people who talk over
  each other, or sound alike, will be harder.

## Good to know
- The transcript appears after you stop, not live.
- Mixed languages (for example English and Vietnamese): the model picks one language per recording and may
  get the other wrong.
- `meetings/` holds real audio. Delete folders you no longer need.
- Delete the `models` folder and the speech model cache only if you want to free disk space; setup downloads them again.
