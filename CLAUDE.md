# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Direct Sound Radio TV Simulator** — a Python app that emulates old-school broadcast TV. It plays organized video content in continuous loops across multiple channels, maintaining a persistent master clock so the broadcast timeline continues even when the app is closed.

## Running the App

**Step 1 — Setup (first time or when media changes):**
```bash
python media_analysis_tool.py
```
Scan your media library, configure channels, and generate playlists. Outputs `config.json` and numbered playlist files in `playlists/`.

**Step 2 — Run the simulator:**
```bash
python tv_sim_m14.py
```

**Optional — Batch convert videos to MP4:**
```bash
python Tools/"MP4 Converter.py"
```

There is no automated test suite. Testing is manual (channel switching, guide rendering, state persistence after restart).

## Dependencies

**Python packages:**
```bash
pip install PyQt6 python-mpv pynput
```

**System tools (Linux/Raspberry Pi):**
```bash
sudo apt install mpv ffmpeg cec-utils
```

## Architecture

### Two-Application Design

**`media_analysis_tool.py`** — GUI setup tool (PyQt6):
- Walks `channels/ChannelName/ShowName/` folder structure
- Uses `ffprobe` to extract video durations, caching results in `media_cache.json`
- Lets users configure playback order, block sizes, marathon mode, and custom channel names
- Outputs `config.json` and `playlists/NN_ChannelName_playlist.json` files

**`tv_sim_m14.py`** — Main simulator (PyQt6 + python-mpv):
- `ChannelPlayer` — wraps an MPV player instance for one channel; uses the master timeline to calculate which video should be playing and seeks to the correct position on channel switch
- `TVSimApp(QMainWindow)` — manages the GUI, all channel players, TV guide rendering, keyboard/HDMI-CEC input, and periodic state saves

### Data Flow
```
channels/ folder
    → media_analysis_tool.py scans + configures
    → config.json + playlists/NN_*.json
    → tv_sim_m14.py loads at startup
    → syncs to persistent master clock (watch_state.json)
    → plays correct position on any channel at any time
```

### Master Clock / Persistence
- `watch_state.json` stores `elapsed_time` (seconds since broadcast start) and `last_active_channel_index`; auto-saved every 60 seconds
- On startup, the app resumes from saved `elapsed_time`, so all channels pick up exactly where they would have been
- Each `ChannelPlayer` independently calculates its current item and seek position from elapsed time

### Playlist JSON Format
Each playlist is a flat array of items with cumulative `start` times:
```json
[
  {"name": "ep1.mp4", "path": "/abs/path/ep1.mp4", "duration": 1234, "start": 0},
  {"name": "ep2.mp4", "path": "/abs/path/ep2.mp4", "duration": 1456, "start": 1234}
]
```

### Key Controls
| Key | Action |
|-----|--------|
| `0`–`9` | Channel number input (supports 2-digit, e.g. `1` then `2` = ch 12) |
| `↑` / `↓` | Channel up / down |
| `Space` | Toggle fullscreen |
| `` ` `` | Toggle TV guide |
| `Tab` | Recall last channel |

HDMI-CEC remote is handled in a background thread (`cec_listener_thread`) and maps CEC key codes to keyboard actions via `pynput`.

### Generated Files (gitignored)
- `config.json` — user channel configuration
- `watch_state.json` — runtime broadcast state
- `media_cache.json` — ffprobe duration cache
- `playlists/` — generated playlist JSONs

### Legacy File
`tv_sim_app.py` is an older version of the simulator, superseded by `tv_sim_m14.py`. It can be ignored for new development.
