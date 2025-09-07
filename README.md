# Direct Sound Radio TV Simulator

## Project Documentation

---

## Project Overview

The TV Simulator is a Python application designed to mimic the experience of watching old-school broadcast television. It plays local video files from organized playlists in a continuous, synchronized loop, creating the illusion of distinct channels with their own schedules. The application features a dynamic, real-time TV guide, supports keyboard and HDMI-CEC remote controls, and is optimized for low-power devices like the Raspberry Pi.

---

## How It Works: The Simulation Flow

The application creates a persistent **broadcast timeline** to ensure that the content is always in sync, no matter when you start the app. It's like tuning into a real TV station that's been on the air for days.

### Startup & Timeline Sync

- On launch, the app reads `config.json` for channel order and playlist locations.
- It then reads `watch_state.json` to determine how long the "simulation" has been running.
- This ensures the timeline is continuous, picking up exactly where it left off.

### Channel Emulation

- Each channel is managed by a `ChannelPlayer` instance.
- Using the master simulation time, it calculates which video file should be playing and seeks to the correct position.
- Example: If simulation time is 5000 seconds and a show starts at 4800 seconds, the player seeks 200 seconds into that show.

### Efficient Playback

- Uses **mpv** for video playback, enabling GPU-accelerated rendering.
- Optimized for smooth performance on devices like the Raspberry Pi.

### Channel Surfing

- Change channels using number keys, up/down arrows, or a TV remote.
- The current video stops, and the new channel's `ChannelPlayer` starts at the correct timeline position.

### Dynamic TV Guide

- One channel is a live TV guide.
- When selected, video playback pauses and a dynamic schedule grid is displayed.
- Shows "on now" and "coming up next" for all channels based on the current timeline.

### HDMI-CEC Remote Control

- A background thread runs `cec-client` to listen for TV remote signals over HDMI.
- Maps remote buttons to keyboard keys for a true "lean-back" experience.

### State Saving

- Every minute, the app saves the current timeline position and last channel to `watch_state.json`.
- Ensures simulation progress is never lost, even after closing or crashes.

---

## Dependencies & Setup

### Python Libraries

Install these libraries via pip (recommended: use a virtual environment):

```bash
pip install PyQt6
pip install python-mpv
pip install pynput
```

### External Software

- **MPV Player**: Core video playback engine.  
    Install with: `sudo apt install mpv`
- **CEC-Client**: For HDMI-CEC remote control.  
    Install with: `sudo apt install cec-utils`  
    (You may need to add your user to the `video` group.)

---

## Core Classes

### `TVSimApp(QMainWindow)`

- Main application class.
- Manages GUI, user interactions, channel switching, TV guide, and lifecycle of channel players and threads.

### `ChannelPlayer`

- Dedicated media player manager for a single channel.
- Holds an mpv player, playlist, and logic to determine which video to play based on the master clock.
- Handles loading, seeking, and stopping playback.

---

## Key Methods & Threads

### `cec_listener_thread()`

- Runs in the background to monitor HDMI-CEC remote events.
- Opens a `cec-client` subprocess, parses output, and simulates keyboard presses using `pynput`.

### `set_channel_by_index(index)`

- Core function for changing channels.
- Stops the current player, sets up the new channel, and starts playback at the correct timeline position.

### `draw_tv_guide()`

- Renders the on-screen program guide.
- Calculates layout, time slots, and positions for each program.
- Draws channel names, time headers, program blocks, and a "now" line.

### `load_or_initialize_timeline()` & `save_watch_state()`

- Manage simulation persistence.
- `load_or_initialize_timeline` reads the last saved state from `watch_state.json` on startup.
- `save_watch_state` writes the current elapsed time and channel periodically and on exit.

-------------

# Media Analysis & Playlist Builder

*A Companion Tool for the Direct Sound Radio TV Simulator*

---

## Project Overview

This tool is the essential first step for setting up the TV Simulator. Its primary purpose is to scan your local video library, measure the duration of each file, and then build structured JSON playlists that the TV Simulator app uses to create its channels. It provides a graphical user interface (GUI) to manage your channel lineup, customize their names, and define sophisticated playback rules like episode blocking and marathon scheduling.

---

## How It Works: From Files to Channels

This tool acts as the "station programmer" for your TV Simulator, turning a simple folder structure into a fully functional broadcast schedule. Here’s the process:

1. **Folder Structure is Key:**  
    The tool expects a specific folder layout. You select a main `Channels` folder, and each subfolder within it is treated as a separate channel (e.g., `/Channels/Cartoons`, `/Channels/SciFi`). Within each channel folder, you can have further subfolders for individual TV shows.

2. **Media Scanning & Caching:**  
    When you click **Scan Media Library**, the tool recursively scans each channel folder for video files. For each file, it uses `ffprobe` (a part of FFmpeg) to get its precise duration. To save time on subsequent scans, this information is stored in a `media_cache.json` file. On future runs, it only re-scans files that have been changed or added.

3. **Channel Configuration (GUI):**  
    The main window displays all discovered channels. Here you can:
    - **Set Channel Order:** Drag and drop channels to define the final channel numbers.
    - **Customize Names:** Give channels friendly names that will appear in the TV guide.
    - **Set Playback Mode:** Choose between "alphabetical" for a predictable order or "random" for a shuffled playback.
    - **Define Blocking:** Group episodes of the same show together in "blocks" (e.g., play 3 episodes of a show before moving to the next). You can set a uniform block size for all shows on a channel or define custom block sizes for each individual show.

4. **Playlist Building:**  
    When you click **Build Playlist**, the tool takes all your settings and generates a `.json` playlist file for that channel. This file is a structured list of every video to be played, including its name, path, duration, and a calculated start time within the playlist's total duration.

5. **Marathon Mode:**  
    A global "Marathon" option allows you to generate playlists that last for a specific duration (e.g., 3 days and 12 hours). When enabled, the tool will loop through the channel's content, refilling its pool of shows as needed until the target duration is met. This is perfect for creating long, continuous blocks of themed content.

6. **Generating the Master Config:**  
    All your settings—channel order, custom names, block rules—are saved into a single `config.json` file. This is the master configuration file that the TV Simulator application reads on startup to build its channels and guide.

---

## Dependencies & Setup

### Python Libraries

These libraries are required and can be installed via pip. It's recommended to use a virtual environment.

```sh
pip install PyQt6
```

### External Software

This application relies on external command-line tools that must be installed on your system.

- **FFmpeg (with ffprobe):**  
  Absolutely essential. The tool uses `ffprobe` to analyze video files and get their duration. You must install FFmpeg and ensure that `ffprobe` is accessible in your system's PATH.

---

## Core Class

### `MediaAnalysisApp(QWidget)`

The main application class that builds the entire graphical user interface. It handles loading and saving the configuration, displaying the channel list, managing all the UI widgets for settings, and triggering the scanning and playlist building functions based on user interaction.

---

## Key Functions

- **`analyze_media(...)`**  
  Walks through a given channel's directory, finds all valid video files, and determines their duration. It intelligently uses a cache to avoid re-analyzing files that haven't changed, significantly speeding up subsequent scans.

- **`get_video_duration(...)`**  
  A utility function that calls the `ffprobe` command-line tool as a subprocess to extract the duration (in seconds) from a single video file.

- **`build_playlist(...)`, `build_playlist_blocks(...)`, `build_playlist_custom_blocks(...)`**  
  This family of functions is responsible for constructing the final JSON playlist files. Based on the selected mode (None, Blocks, Custom), they arrange the analyzed media items in the correct order, insert bumpers, calculate start times for each entry, and write the final structured data to a JSON file. They also handle the logic for Marathon Mode, looping content to meet a target duration.

- **`save_config()`**  
  Gathers all the current settings from the UI—channel order, custom names, block modes, marathon settings—and saves them to the master `config.json` file. This function is called whenever a change is made, ensuring that the configuration is always up-to-date.
