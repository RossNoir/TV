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

# Using Media Analysis Tool for TV Simulator

This document explains how to use the `media_analysis_tool.py` to prepare your local video files for use with the TV Simulator application.

## What This Tool Does

The TV Simulator needs to know what "channels" you have and what videos are on each channel. This tool automates that entire process. It will:

1.  **Scan your media folders**, treating each folder as a unique TV channel.
2.  **Analyze every video file** to get its exact duration.
3.  **Generate a `config.json` file**, which acts as the main settings file for the TV Simulator.
4.  **Create individual playlist files** for each channel, which contain the schedule of programs.

You only need to run this tool **once** to set up your channels. You can run it again later if you add new videos or channels to your collection.

---

## Step 1: Organize Your Media Files

This is the most important step. You must organize your video files into folders, where **each folder represents one TV channel**.

Create a main folder for your TV content (e.g., `My TV Channels`). Inside that folder, create subfolders for each channel you want.

#### Example Folder Structure:

/My TV Channels/
|
|-- 80s Cartoons/
|   |-- He-Man S01E01.mp4
|   |-- ThunderCats S01E01.mkv
|   |-- Inspector Gadget S01E01.mp4
|
|-- 90s Sitcoms/
|   |-- Seinfeld S04E11.avi
|   |-- Frasier S01E01.mp4
|
|-- Movie Channel/
|   |-- The Terminator (1984).mp4
|   |-- Ghostbusters (1984).mkv
|
|-- Commercials/
|   |-- 80s_ad_1.mp4
|   |-- 90s_ad_2.mp4

**Important:**
* The tool will also look for a folder named `Commercials`. If it finds one, it will automatically insert commercials between the main programs on your other channels.
* The names of these folders will be used to generate the channel list.

---

## Step 2: Run the Media Analysis Tool

Once your folders are organized, you can run the tool. The script below is interactive and will guide you through the process with a graphical interface.

1.  Save the Python code provided in the next section as `media_analysis_tool.py` in the same directory as your main `tv_sim_x3.py` file.
2.  Run it from your terminal: `python media_analysis_tool.py`
3.  The script will first ask you to **select your main media folder** (e.g., the `/My TV Channels/` folder from the example).
4.  It will then scan everything and create the necessary configuration files.

---

## Step 3: Understand the Generated Files

After the tool runs, you will have two new things in your project folder:

**1. `config.json`**

This is the master configuration file. It tells the TV Simulator what your channels are and in what order to display them. You can (and should) edit this file to customize your lineup.

* **`playlists_path`**: The location of the generated playlists (you can ignore this).
* **`custom_names`**: A dictionary mapping the folder names to "pretty" names for the TV Guide. You can change these.
* **`channel_order`**: A list that defines the order of your channels. **You can rearrange the items in this list to change your channel lineup.**

**2. A `playlists` folder**

This folder will contain a separate JSON file for each channel. These files contain the detailed schedule and are read by the TV Simulator. You should not need to edit these files manually.

---

## Next Steps

Once the analysis tool has finished and you've customized your `config.json`, you're ready to go! You can now run the main `tv_sim_x3.py` application to start watching your channels.



