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

## Automatic meeting detection (Windows only)
Double-click `watch.bat` once. It stays in the background, like Notion's meeting detection:
1. When an app such as Teams or Zoom starts using your mic, a card appears at the top of the screen.
2. Click **Start transcribing**. It never records without that click. A small pill shows the time; drag it anywhere.
3. Press **Stop**, or just leave the call. A minute after the mic is free, it stops. Text is made during the
   call, so the transcript is ready shortly after.
4. The minutes agent starts by itself. A card tells you when `minutes.md` and a draft `follow-up-email.md` are ready.

One-time setup for the agent: run `claude auth login`, copy `agent-config.example.md` to `agent-config.md`, and
fill it in. The agent follows `agent-instructions.md`. It can only read this folder, write inside `meetings/`,
and search your Outlook calendar. Everything else, including sending, is refused. It needs the Claude Code login
and the Microsoft 365 connector. Your transcripts, invite lists and defect details go to Claude each time.

Lines are labelled **You** or **Others**, without Speaker 1, 2, to keep it fast. On a 10-core laptop this took
about 80% of the processor time it had, so a busy computer can leave some work to finish after the call.
If live transcription fails, it transcribes the saved audio after the call instead.

It keeps the computer awake while recording and transcribing. Any app using the mic can trigger the card,
so press Dismiss if it is not a meeting. A log is saved in `meetings/watch.log`.

## If a recording is interrupted
Audio is saved to disk while you record, so a crash, a closed window or a forced stop loses only the last
second or two. To rescue it, run in the project folder:
`.venv\Scripts\python notes.py recover meetings\<folder>` (Windows) or
`.venv/bin/python notes.py recover meetings/<folder>` (Mac). It rebuilds the audio and transcribes it.
Look for `.raw` files in `meetings/<folder>`: they are the unfinished recording.

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
- With `record.bat` or the Claude tool, the transcript is made after you stop, and takes about as long as the meeting.
- Mixed languages (for example English and Vietnamese): the model picks one language per recording and may
  get the other wrong.
- `meetings/` holds real audio. Delete folders you no longer need.
- Delete the `models` folder and the speech model cache only if you want to free disk space; setup downloads them again.
