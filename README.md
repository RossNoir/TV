# TV Simulator

A Python-based TV simulator that uses VLC to play local media files in a continuous, channel-surfing experience. The application creates a simulated broadcast schedule, allowing users to switch between "channels" that play back-to-back media from organized playlists.

## Features

* **Channel Surfing:** Use the up/down arrow keys or enter channel numbers directly to switch between channels.
* **TV Guide:** A dynamic, on-screen guide shows what's currently "airing" on each channel.
* **Persistent Timeline:** The simulation maintains a consistent timeline. When you close and reopen the app, you can resume from where you left off, as if the channels were broadcasting the whole time.
* **Customizable Channels:** Channels and their content are defined by simple playlist files.
* **Fullscreen & UI Toggles:** Supports a clean, fullscreen viewing experience.

## Setup & Usage

1.  **Prerequisites:**
    * Python 3
    * VLC Media Player
    * The `python-vlc` library (`pip install python-vlc`)

2.  **Configuration:**
    * Run the `media_analysis_tool.py` (or equivalent script) to scan your media folders and generate the necessary `config.json` and playlist files.
    * Organize your media into folders, where each folder represents a channel.

3.  **Running the Simulator:**
    * Execute the main Python script: `python tv_sim_x3.py`

## Key Bindings

* **Up/Down Arrows:** Change channel.
* **Number Keys (0-9):** Enter a two-digit channel number directly.
* **Backtick (`):** Open the TV Guide.
* **F11:** Toggle fullscreen mode.
