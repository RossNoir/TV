# tv_sim_media_analysis_tool.py
# --- VERSION Z5 ---
# REWRITE: Final revision of "Custom" block logic to perfectly match user specification.
#          - "Alphabetical" mode now plays sequential episodes in a sequential show cycle.
#          - "Random" mode now plays random episodes in a random show cycle.
# REWRITE: "Custom" block mode logic has been completely rewritten to function as a cyclical block schedule.
#          - "Alphabetical" mode now cycles through shows in on-screen order (A, B, C, A, B, C...).
#          - "Random" mode now shuffles the order of shows in the cycle for better randomization (e.g., C, A, B, C, A, B...).
# UPDATE: Changed "Custom" block playlist logic. "alphabetical" mode now generates blocks in the on-screen order of shows,
#         while "random" mode shuffles the show blocks.
# FIX: Restored the log window to its proper position at the bottom of the application by correcting the UI packing order.
# NEW: Implemented "Custom" block selection. Users can set episode counts for each show within a channel.
#      - UI dynamically expands/collapses to show per-show controls.
#      - New playlist function `build_playlist_custom_blocks` handles randomized custom block logic.
#      - Saves custom counts to config.json.
# Reverted to individual ffprobe calls for stability.
# Includes Raspberry Pi specific ffprobe check and robust absolute path handling.
# FIX: Ensures 'block_counts' key is always present in config to prevent KeyError.
# NEW: Implements persistent caching for media analysis results.
# FIX: Correctly resolves bumper.mp4 path for playlist insertion.
# FIX: Corrected argument count for build_playlist() function call.
# FIX: Implemented random shuffling of items within show blocks when 'random' mode is selected.
# REMOVED: Time-based playlist scheduling feature.
# NEW: Improved show grouping logic for block programming (groups by top-level show folder).
# UPDATE: Added individual "Build Playlist" buttons for each channel.
# UPDATE: Added a "Build All Playlists" button.
# UPDATE: Repurposed "Scan Media & Build Playlists" to "Scan Media Library" (only scans, no playlist build).
# FIX: Ensured all new UI elements (Scan Media Library, Build All Playlists, individual Build Playlist buttons)
#      and the log text area are correctly packed and visible.
# ADDED: Scrollbar for the log text area.
# FIX: Corrected UI element packing order to ensure log window visibility.
# UPDATE: Increased default window width by 80 pixels and height by 100 pixels.
# FIX: Added robust error handling in get_video_duration to prevent 'NoneType' object has no attribute 'strip' error.
# --- VERSION WITH NUMBERED CHANNELS ---
# NEW: Added numbered, static channel list in UI.
# NEW: Playlist filenames are now prefixed with the channel number (e.g., "01_ChannelName_playlist.json").
# UPDATE: UI layout now uses .grid() for the main channel list to support the static number column.
# UPDATE: Drag-and-drop logic now correctly reorders channels and updates numbering.

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import json
from pathlib import Path
from datetime import datetime
import subprocess
import time
from collections import defaultdict
import random

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_cache.json")

DEFAULT_CONFIG = {
    "channels_path": "",
    "playlists_path": "playlists",
    "scan_modes": {},
    "custom_names": {},
    "block_counts": {},
    "custom_block_counts": {}, 
    "channel_order": []
}

VIDEO_EXTENSIONS = [".mp4", ".avi", ".mkv", ".mov"]
BUMPER_FILENAME = "bumper.mp4"

def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_cache(cache):
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, indent=2)

def is_ffprobe_installed():
    try:
        subprocess.run(["which", "ffprobe"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def get_video_duration(path, log_callback=None):
    if not is_ffprobe_installed():
        messagebox.showerror(
            "Error: ffprobe Not Found",
            "ffprobe is required. Please install FFmpeg: sudo apt install ffmpeg"
        )
        return 0.0

    if not Path(path).exists():
        if log_callback:
            log_callback(f"Warning: File not found for duration check: {Path(path).name}")
        return 0.0

    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of",
            "default=noprint_wrappers=1:nokey=1", str(path)
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True, text=True)
        
        if result.stdout is None:
            if log_callback:
                log_callback(f"Error: ffprobe returned no stdout for {Path(path).name}")
            return 0.0

        return float(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        if log_callback:
            log_callback(f"Error running ffprobe for {Path(path).name}: {e.stderr.strip()}")
        return 0.0
    except ValueError:
        if log_callback:
            log_callback(f"Could not parse ffprobe output for {Path(path).name}: {result.stdout.strip()}")
        return 0.0
    except Exception as e:
        if log_callback:
            log_callback(f"An unexpected error occurred for {Path(path).name}: {e}")
        return 0.0

def analyze_media(channel_path, media_cache, log_callback):
    result = []
    for root, dirs, files in os.walk(channel_path):
        for file in sorted(files):
            if file.lower() == BUMPER_FILENAME.lower():
                continue

            if not any(file.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                continue
            full_path = Path(root) / file
            abs_path_str = str(full_path.resolve())
            
            file_mtime = os.path.getmtime(full_path)
            cached_data = media_cache.get(abs_path_str)

            if cached_data and cached_data.get('mtime') == file_mtime:
                duration = cached_data.get('duration', 0.0)
                log_callback(f"  Cached: {file} ({int(duration)}s)")
            else:
                duration = get_video_duration(full_path, log_callback)
                media_cache[abs_path_str] = {'duration': duration, 'mtime': file_mtime}
                log_callback(f"  Analyzed: {file} ({int(duration)}s)")
            
            result.append({"name": file, "path": abs_path_str, "duration": duration})
    return result

def get_show_groups(media_items, channels_base_path, channel_name):
    """Helper function to group media items by their parent folder (show name)."""
    show_groups = defaultdict(list)
    channel_dir_path = Path(channels_base_path) / channel_name

    for item in media_items:
        item_path = Path(item['path'])
        try:
            relative_path_from_channel = item_path.relative_to(channel_dir_path)
            show_name = relative_path_from_channel.parts[0]
        except ValueError:
            show_name = item_path.parent.name
        show_groups[show_name].append(item)
    return show_groups

def build_playlist(channel_number, channel_name, media_items, mode, playlist_path, channels_base_path):
    if mode == "random":
        random.shuffle(media_items)
    else:
        media_items.sort(key=lambda x: x["name"].lower())

    playlist = []
    bumper_path = (Path(channels_base_path) / channel_name / BUMPER_FILENAME).resolve()
    has_bumper = bumper_path.exists()
    
    bumper_duration = get_video_duration(bumper_path, lambda msg: print(f"Bumper error: {msg}")) if has_bumper else 0
    current_time = 0

    for item in media_items:
        if has_bumper:
            playlist.append({
                "name": BUMPER_FILENAME, "path": str(bumper_path),
                "duration": int(bumper_duration), "start": current_time
            })
            current_time += int(bumper_duration)
        
        entry = {
            "name": item["name"], "path": item["path"],
            "duration": int(item["duration"]), "start": current_time
        }
        playlist.append(entry)
        current_time += entry["duration"]

    Path(playlist_path).mkdir(parents=True, exist_ok=True)
    playlist_filename = f"{channel_number:02d}_{channel_name}_playlist.json"
    with open(Path(playlist_path) / playlist_filename, "w") as f:
        json.dump(playlist, f, indent=2)

def build_playlist_blocks(channel_number, channel_name, media_items, mode, block_size, playlist_path, channels_base_path):
    if block_size == 0:
        build_playlist(channel_number, channel_name, media_items, mode, playlist_path, channels_base_path) 
        return

    show_groups = get_show_groups(media_items, channels_base_path, channel_name)

    show_keys = list(show_groups.keys())
    
    # Shuffle episodes within each show group if mode is random
    if mode == "random":
        random.shuffle(show_keys)
        for group in show_groups.values():
            random.shuffle(group)
    else: # Alphabetical
        show_keys.sort()
        for group in show_groups.values():
            group.sort(key=lambda x: x["name"].lower())


    playlist = []
    bumper_path = (Path(channels_base_path) / channel_name / BUMPER_FILENAME).resolve()
    has_bumper = bumper_path.exists()
    bumper_duration = get_video_duration(bumper_path, lambda msg: print(f"Bumper error: {msg}")) if has_bumper else 0
    current_time = 0
    index_map = {show: 0 for show in show_keys}

    while any(index_map[show] < len(show_groups[show]) for show in show_keys):
        for show in show_keys:
            start_idx = index_map[show]
            end_idx = min(start_idx + block_size, len(show_groups[show]))
            
            for item in show_groups[show][start_idx:end_idx]:
                if has_bumper:
                    playlist.append({
                        "name": BUMPER_FILENAME, "path": str(bumper_path),
                        "duration": int(bumper_duration), "start": current_time
                    })
                    current_time += int(bumper_duration)
                
                entry = {
                    "name": item["name"], "path": item["path"],
                    "duration": int(item["duration"]), "start": current_time
                }
                playlist.append(entry)
                current_time += entry["duration"]
            
            index_map[show] = end_idx

    Path(playlist_path).mkdir(parents=True, exist_ok=True)
    playlist_filename = f"{channel_number:02d}_{channel_name}_playlist.json"
    with open(Path(playlist_path) / playlist_filename, "w") as f:
        json.dump(playlist, f, indent=2)

def build_playlist_custom_blocks(channel_number, channel_name, media_items, mode, custom_counts, playlist_path, channels_base_path):
    """
    Builds a playlist by cycling through shows, playing a custom number of episodes for each.
    - alphabetical: Cycles shows alphabetically, plays episodes sequentially.
    - random: Cycles shows randomly, plays episodes randomly.
    """
    show_groups = get_show_groups(media_items, channels_base_path, channel_name)

    # Determine the cycle order of shows and shuffle episodes ONLY if in random mode.
    show_keys = sorted(list(show_groups.keys()))  # Default to alphabetical (on-screen) order

    if mode == "random":
        random.shuffle(show_keys)  # Shuffle the show cycle order
        # Shuffle episodes within each show for variety
        for group in show_groups.values():
            random.shuffle(group)

    playlist = []
    bumper_path = (Path(channels_base_path) / channel_name / BUMPER_FILENAME).resolve()
    has_bumper = bumper_path.exists()
    bumper_duration = get_video_duration(bumper_path, lambda msg: print(f"Bumper error: {msg}")) if has_bumper else 0
    current_time = 0
    
    # Map to track the next episode index for each show
    index_map = {show: 0 for show in show_keys}

    # Loop as long as there are episodes left to schedule in any show
    while any(index_map[show] < len(show_groups[show]) for show in show_keys):
        # Cycle through the shows in the determined order
        for show in show_keys:
            # Check if this show has any episodes left to process
            if index_map[show] >= len(show_groups[show]):
                continue

            # Get the custom block size for this specific show, defaulting to 1
            block_size = custom_counts.get(show, 1)
            
            start_idx = index_map[show]
            end_idx = min(start_idx + block_size, len(show_groups[show]))
            
            # Add the block of episodes to the playlist
            for item in show_groups[show][start_idx:end_idx]:
                if has_bumper:
                    playlist.append({
                        "name": BUMPER_FILENAME, "path": str(bumper_path),
                        "duration": int(bumper_duration), "start": current_time
                    })
                    current_time += int(bumper_duration)
                
                entry = {
                    "name": item["name"], "path": item["path"],
                    "duration": int(item["duration"]), "start": current_time
                }
                playlist.append(entry)
                current_time += entry["duration"]
            
            # Update the index for the next turn
            index_map[show] = end_idx

    Path(playlist_path).mkdir(parents=True, exist_ok=True)
    playlist_filename = f"{channel_number:02d}_{channel_name}_playlist.json"
    with open(Path(playlist_path) / playlist_filename, "w") as f:
        json.dump(playlist, f, indent=2)


class MediaAnalysisApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Media Analysis & Playlist Builder")
        self.root.geometry("720x680") # Increased height for log

        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                self.config = json.load(f)
            for key, default_value in DEFAULT_CONFIG.items():
                self.config.setdefault(key, default_value)
        else:
            self.config = DEFAULT_CONFIG.copy()

        self.media_cache = load_cache()

        self.channel_widgets = {}
        self.channel_rows = []
        self.drag_data = {'widget': None, 'y': 0}

        # --- Top Frame for Controls ---
        top_frame = tk.Frame(root)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        self.path_label = tk.Label(top_frame, text=f"Channels Folder: {self.config.get('channels_path', 'Not selected')}", fg="gray")
        self.path_label.pack(pady=2)

        buttons_frame = tk.Frame(top_frame)
        buttons_frame.pack(pady=5)
        self.select_button = tk.Button(buttons_frame, text="Select Channel Folder", command=self.select_folder)
        self.select_button.pack(side=tk.LEFT, padx=5)
        self.playlists_button = tk.Button(buttons_frame, text="Select Playlists Folder", command=self.select_playlists_folder)
        self.playlists_button.pack(side=tk.LEFT, padx=5)

        self.action_buttons_frame = tk.Frame(top_frame)
        self.action_buttons_frame.pack(pady=5)
        self.scan_button = tk.Button(self.action_buttons_frame, text="Scan Media Library", command=self.scan_media_library_only, state=tk.DISABLED)
        self.scan_button.pack(side=tk.LEFT, padx=5)
        self.build_all_button = tk.Button(self.action_buttons_frame, text="Build All Playlists", command=self.build_all_playlists, state=tk.DISABLED)
        self.build_all_button.pack(side=tk.LEFT, padx=5)

        # --- Log Frame at the Bottom ---
        self.log_frame = tk.Frame(root, height=150)
        self.log_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=5)
        self.log_frame.pack_propagate(False) # Prevent resizing
        
        self.log_box = tk.Text(self.log_frame, wrap="word")
        self.log_scrollbar = ttk.Scrollbar(self.log_frame, command=self.log_box.yview)
        self.log_box.config(yscrollcommand=self.log_scrollbar.set)
        self.log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- Main Frame for Channel List (fills remaining space) ---
        main_frame = tk.Frame(root)
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10)

        self.channel_list_canvas = tk.Canvas(main_frame)
        self.v_scroll = ttk.Scrollbar(main_frame, orient="vertical", command=self.channel_list_canvas.yview)
        self.channel_list_frame = tk.Frame(self.channel_list_canvas)
        
        self.channel_list_canvas.configure(yscrollcommand=self.v_scroll.set)
        
        self.v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.channel_list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas_window = self.channel_list_canvas.create_window((0, 0), window=self.channel_list_frame, anchor="nw")

        self.channel_list_frame.bind("<Configure>", self.on_frame_configure)
        self.channel_list_canvas.bind('<Configure>', self.on_canvas_configure)

        if self.config["channels_path"] and Path(self.config["channels_path"]).is_dir():
            self.load_channel_folders()
        else:
            self.log("Please select a 'Channels Folder' to begin.")
            
        if not is_ffprobe_installed():
            self.log_box.insert(tk.END, "\nWARNING: ffprobe not found. Please install FFmpeg.\n")
            self.log_box.see(tk.END)

    def on_frame_configure(self, event=None):
        self.channel_list_canvas.configure(scrollregion=self.channel_list_canvas.bbox("all"))

    def on_canvas_configure(self, event=None):
        self.channel_list_canvas.itemconfig(self.canvas_window, width=event.width)

    def log(self, message):
        self.log_box.insert(tk.END, message + "\n")
        self.log_box.see(tk.END)
        self.root.update_idletasks()

    def select_folder(self):
        selected = filedialog.askdirectory(title="Select Folder Containing Channel Subfolders")
        if not selected:
            return
        self.config["channels_path"] = selected
        self.config["channel_order"] = []
        self.load_channel_folders()
        self.save_config()

    def select_playlists_folder(self):
        selected = filedialog.askdirectory(title="Select Folder to Save Playlists")
        if selected:
            self.config["playlists_path"] = selected
            self.log(f"Playlists folder set to: {selected}")
            self.save_config()

    def load_channel_folders(self):
        selected = self.config["channels_path"]
        
        self.channel_widgets.clear()
        self.channel_rows.clear()

        self.path_label.config(text=f"Channels Folder: {selected}", fg="black")

        for widget in self.channel_list_frame.winfo_children():
            widget.destroy()

        all_dirs = {d.name for d in Path(selected).iterdir() if d.is_dir()}
        ordered_channels = [ch for ch in self.config.get("channel_order", []) if ch in all_dirs]
        new_channels = sorted(list(all_dirs - set(ordered_channels)))
        final_channel_list = ordered_channels + new_channels
        self.config["channel_order"] = final_channel_list

        if not final_channel_list:
            self.log("No channel subfolders found.")
            return

        self.channel_list_frame.grid_columnconfigure(0, weight=1)

        for i, item in enumerate(final_channel_list):
            self._create_channel_row_ui(item, i)
        
        self.scan_button.config(state=tk.NORMAL)
        self.build_all_button.config(state=tk.NORMAL)
        self.log(f"{len(final_channel_list)} channel(s) loaded.")
        self.save_config()

    def _create_channel_row_ui(self, channel_name, row_index):
        container = tk.Frame(self.channel_list_frame, name=f"container_{channel_name.lower()}")
        container.grid(row=row_index, column=0, pady=2, sticky="ew")
        container.channel_name = channel_name 
        self.channel_rows.append(container)

        main_row = tk.Frame(container)
        main_row.pack(fill=tk.X, expand=True)
        
        num_label = tk.Label(main_row, text=f"{row_index+1:02d}", font=("TkDefaultFont", 10, "bold"), width=3)
        num_label.pack(side=tk.LEFT, padx=(5,0))
        
        label = tk.Label(main_row, text=channel_name, width=15, anchor="w")
        label.pack(side=tk.LEFT, padx=5)

        entry = tk.Entry(main_row)
        entry.insert(0, self.config.get("custom_names", {}).get(channel_name, channel_name))
        entry.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)

        block_values = ["None", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "Custom"]
        block_dropdown = ttk.Combobox(main_row, values=block_values, state="readonly", width=8)
        saved_block = self.config["block_counts"].get(channel_name, "None")
        block_dropdown.set(saved_block)
        block_dropdown.pack(side=tk.LEFT, padx=5)
        block_dropdown.bind("<<ComboboxSelected>>", lambda event, ch=channel_name: self._toggle_custom_view(ch))

        dropdown = ttk.Combobox(main_row, values=["alphabetical", "random"], state="readonly", width=12)
        saved_mode = self.config["scan_modes"].get(channel_name, "alphabetical")
        dropdown.set(saved_mode)
        dropdown.pack(side=tk.LEFT, padx=5)

        build_button = tk.Button(main_row, text="Build", command=lambda ch=channel_name: self.build_single_playlist(ch))
        build_button.pack(side=tk.LEFT, padx=5)

        custom_frame = tk.Frame(container, bd=1, relief=tk.SOLID)
        
        self.channel_widgets[channel_name] = {
            "num_label": num_label, "name_entry": entry, "block_dropdown": block_dropdown,
            "mode_dropdown": dropdown, "build_button": build_button, "container": container,
            "custom_frame": custom_frame, "custom_widgets": {}
        }
        
        for widget in (container, main_row, label, num_label):
            widget.bind("<ButtonPress-1>", self.on_drag_start)
            widget.bind("<B1-Motion>", self.on_drag_motion)
            widget.bind("<ButtonRelease-1>", self.on_drag_end)

        self._toggle_custom_view(channel_name, initial_load=True)

    def _toggle_custom_view(self, channel_name, initial_load=False):
        widgets = self.channel_widgets.get(channel_name)
        if not widgets: return
        
        block_mode = widgets["block_dropdown"].get()
        custom_frame = widgets["custom_frame"]

        if block_mode == "Custom":
            if not custom_frame.winfo_viewable():
                custom_frame.pack(fill=tk.X, expand=True, padx=30, pady=5, after=widgets["block_dropdown"].master)
                channel_path = Path(self.config["channels_path"]) / channel_name
                
                for w in custom_frame.winfo_children(): w.destroy()
                widgets["custom_widgets"] = {}

                if not channel_path.is_dir():
                     tk.Label(custom_frame, text="Channel folder not found.", fg="red").pack()
                     return

                show_dirs = sorted([d.name for d in channel_path.iterdir() if d.is_dir()])
                
                if not show_dirs:
                    tk.Label(custom_frame, text="No show sub-folders found.", fg="gray").pack(pady=5)
                else:
                    for i, show_name in enumerate(show_dirs):
                        show_frame = tk.Frame(custom_frame)
                        show_frame.pack(fill=tk.X, padx=10, pady=2)
                        tk.Label(show_frame, text=show_name, width=15, anchor="w").pack(side=tk.LEFT)
                        show_count_dd = ttk.Combobox(show_frame, values=[1,2,3,4,5], state="readonly", width=5)
                        saved_count = self.config.get("custom_block_counts", {}).get(channel_name, {}).get(show_name, 1)
                        show_count_dd.set(saved_count)
                        show_count_dd.pack(side=tk.LEFT, padx=5)
                        widgets["custom_widgets"][show_name] = show_count_dd
        else:
            if custom_frame.winfo_viewable():
                custom_frame.pack_forget()

        if not initial_load:
            self.save_channel_order()

    def on_drag_start(self, event):
        widget = event.widget
        while widget and not hasattr(widget, 'channel_name'):
            widget = widget.master
        if widget in self.channel_rows:
            self.drag_data["widget"] = widget
            self.drag_data["y"] = event.y_root
            widget.config(bg="#d0ebff")

    def on_drag_motion(self, event):
        widget = self.drag_data["widget"]
        if widget:
            y = event.y_root
            delta_y = y - self.drag_data["y"]
            current_index = self.channel_rows.index(widget)

            if delta_y > 20 and current_index < len(self.channel_rows) - 1:
                self.channel_rows.insert(current_index + 1, self.channel_rows.pop(current_index))
                self.drag_data["y"] = y
                self.reorder_ui()
            elif delta_y < -20 and current_index > 0:
                self.channel_rows.insert(current_index - 1, self.channel_rows.pop(current_index))
                self.drag_data["y"] = y
                self.reorder_ui()

    def reorder_ui(self):
        for i, row_widget in enumerate(self.channel_rows):
            row_widget.grid(row=i, column=0, pady=2, sticky="ew")
            ch_name = row_widget.channel_name
            self.channel_widgets[ch_name]["num_label"].config(text=f"{i+1:02d}")
            
    def on_drag_end(self, event):
        widget = self.drag_data["widget"]
        if widget:
            widget.config(bg=self.root.cget('bg'))
        self.drag_data["widget"] = None
        self.save_channel_order()

    def save_channel_order(self):
        if not self.channel_rows: return

        ordered_channels = [row.channel_name for row in self.channel_rows]
        self.config["channel_order"] = ordered_channels

        self.config["custom_block_counts"] = {}
        for ch_name in self.channel_widgets:
            widgets = self.channel_widgets[ch_name]
            self.config["scan_modes"][ch_name] = widgets["mode_dropdown"].get()
            self.config["custom_names"][ch_name] = widgets["name_entry"].get()
            self.config["block_counts"][ch_name] = widgets["block_dropdown"].get()
            
            if widgets["block_dropdown"].get() == "Custom":
                if ch_name not in self.config["custom_block_counts"]:
                    self.config["custom_block_counts"][ch_name] = {}
                for show_name, dropdown in widgets["custom_widgets"].items():
                    self.config["custom_block_counts"][ch_name][show_name] = int(dropdown.get())

        self.save_config()
        self.log("Channel order and settings saved.")

    def save_config(self):
        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            messagebox.showerror("Error Saving Config", f"Could not save configuration: {e}")

    def scan_media_library_only(self):
        self.save_channel_order()
        self.log("Scanning media library...")
        self.toggle_ui_state(tk.DISABLED)

        try:
            base_path = Path(self.config["channels_path"])
            for channel_name in self.config["channel_order"]:
                full_path = base_path / channel_name
                self.log(f"  Analyzing: {self.config['custom_names'].get(channel_name, channel_name)}")
                analyze_media(full_path, self.media_cache, self.log)
            
            save_cache(self.media_cache)
            self.log("Media library scan complete. Cache updated.")
        except Exception as e:
            self.log(f"An error occurred during scan: {e}")
        finally:
            self.toggle_ui_state(tk.NORMAL)
    
    def build_single_playlist(self, channel_name):
        self.save_channel_order()
        self.log(f"Building playlist for channel: {self.config['custom_names'].get(channel_name, channel_name)}...")
        self.toggle_ui_state(tk.DISABLED)

        try:
            channel_number = self.config["channel_order"].index(channel_name) + 1
            self.process_playlist_build(channel_number, channel_name)
            self.log(f"Playlist built for Ch. {channel_number:02d} - {self.config['custom_names'].get(channel_name, channel_name)}.")
        except Exception as e:
            self.log(f"An error occurred building playlist for {channel_name}: {e}")
            messagebox.showerror("Playlist Build Error", f"An error occurred: {e}")
        finally:
            self.toggle_ui_state(tk.NORMAL)

    def build_all_playlists(self):
        self.save_channel_order()
        self.log("Building playlists for all channels...")
        self.toggle_ui_state(tk.DISABLED)

        try:
            for i, channel_name in enumerate(self.config["channel_order"]):
                channel_number = i + 1
                self.log(f"  Building Ch. {channel_number:02d}: {self.config['custom_names'].get(channel_name, channel_name)}")
                self.process_playlist_build(channel_number, channel_name)
            
            save_cache(self.media_cache)
            self.log("All playlists built. Cache updated.")
        except Exception as e:
            self.log(f"An error occurred building all playlists: {e}")
            messagebox.showerror("Playlist Build Error", f"An error occurred: {e}")
        finally:
            self.toggle_ui_state(tk.NORMAL)

    def process_playlist_build(self, channel_number, channel_name):
        base_path = Path(self.config["channels_path"])
        playlists_path = Path(self.config["playlists_path"])
        full_path = base_path / channel_name
        scan_mode = self.config["scan_modes"].get(channel_name, "alphabetical")
        
        channel_media_items = analyze_media(full_path, self.media_cache, self.log)
        
        block_mode = self.config.get('block_counts', {}).get(channel_name, 'None')

        if block_mode == "Custom":
            custom_counts = self.config.get('custom_block_counts', {}).get(channel_name, {})
            build_playlist_custom_blocks(channel_number, channel_name, channel_media_items, scan_mode, custom_counts, playlists_path, base_path)
        else:
            block_size = int(block_mode) if str(block_mode).isdigit() else 0
            if block_size > 0:
                build_playlist_blocks(channel_number, channel_name, channel_media_items, scan_mode, block_size, playlists_path, base_path)
            else:
                build_playlist(channel_number, channel_name, channel_media_items, scan_mode, playlists_path, base_path)

    def toggle_ui_state(self, state):
        self.scan_button.config(state=state)
        self.build_all_button.config(state=state)
        self.select_button.config(state=state)
        self.playlists_button.config(state=state)
        for ch_name, widgets in self.channel_widgets.items():
            widgets["build_button"].config(state=state)
            widgets["block_dropdown"].config(state=state)
            widgets["mode_dropdown"].config(state=state)
        self.root.update_idletasks()

if __name__ == '__main__':
    root = tk.Tk()
    app = MediaAnalysisApp(root)
    root.mainloop()