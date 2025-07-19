# tv_sim_x7.py
# Designed for Raspberry Pi 4 — prioritizes reliability over quick channel switching


import threading
import tkinter as tk
from tkinter import ttk, messagebox, font
import json
import time
import vlc
import os
from pathlib import Path
from datetime import datetime, timedelta
import sys
import atexit
import re # Import re for regex matching of playlist files

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
WATCH_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_state.json")

# --- Channel Loading Logic ---
try:
    with open(CONFIG_PATH) as f:
        config = json.load(f)
except FileNotFoundError:
    messagebox.showerror("Configuration Error", f"Config file not found: {CONFIG_PATH}\nPlease run the Media Analysis Tool first.")
    sys.exit(1)
except json.JSONDecodeError:
    messagebox.showerror("Configuration Error", f"Error decoding config.json. Please check its format.")
    sys.exit(1)

PLAYLISTS_PATH = Path(config.get("playlists_path", "playlists")).resolve()
CUSTOM_NAMES = config.get("custom_names", {})
# Get the desired channel order from the config file.
ORDERED_CHANNELS_FROM_CONFIG = config.get("channel_order", [])

# Scan the playlists directory for numbered playlist files.
playlist_files = list(PLAYLISTS_PATH.glob("*_playlist.json"))
playlist_map = {}
for f in playlist_files:
    # Use regex to find files named like "01_ShowName_playlist.json"
    match = re.match(r"(\d+_)(.+?)(_playlist\.json)", f.name)
    if match:
        channel_name = match.group(2) # The part between the number and "_playlist.json"
        playlist_map[channel_name] = f # Map the name to the full Path object

# Filter and sort the channels based on the order in config AND the existence of a playlist file.
RAW_CHANNELS = [ch for ch in ORDERED_CHANNELS_FROM_CONFIG if ch in playlist_map]

if not RAW_CHANNELS:
    messagebox.showwarning("No Playlists Found", "Could not find any valid, numbered playlists in the specified directory.\nPlease run the Media Analysis Tool to generate them.")

# The final list of channels for the UI, including the TV Guide.
CHANNELS = RAW_CHANNELS + ["TV Guide"]
# --- END Channel Loading Logic ---


class ChannelPlayer:
    def __init__(self, channel_name, playlist_path, video_window_id):
        self.channel_name = channel_name
        self.playlist_path = playlist_path # Store the full path
        self.video_window_id = video_window_id
        
        self.vlc_instance = vlc.Instance(
            '--vout=xvideo',
            '--file-caching=10000',
            '--network-caching=5000',
            '--live-caching=5000',
            '--avcodec-threads=4',
            '--sout-mux-caching=5000',
            '--drop-late-frames',
            '--no-skip-frames',
            '--ignore-config',
            '--no-metadata-network-access',
            '--verbose=3'
        )
        
        self.player = None
        self.channel_start_time = time.time()
        self.playlist = self.load_playlist()
        self.current_item_name = None
        
        self.create_player()

    def create_player(self):
        if self.player:
            self.stop()
            self.player.release()
            self.player = None

        self.player = self.vlc_instance.media_player_new()
        self.set_video_output(self.video_window_id)

        self.event_manager = self.player.event_manager()
        self.event_manager.event_attach(vlc.EventType.MediaPlayerEncounteredError, self.on_vlc_error)
        self.event_manager.event_attach(vlc.EventType.MediaPlayerMediaChanged, self.on_vlc_media_changed)
        self.event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self.on_vlc_end_reached)
        print(f"VLC player created for channel: {self.channel_name}")

    def set_video_output(self, window_id):
        if sys.platform.startswith('win'):
            self.player.set_hwnd(window_id)
        else:
            self.player.set_xwindow(window_id)

    def load_playlist(self):
        """Loads the channel's playlist from the provided path."""
        try:
            with open(self.playlist_path) as f:
                return json.load(f)
        except FileNotFoundError:
            messagebox.showerror("Playlist Error", f"Playlist file not found for {self.channel_name}: {self.playlist_path}\nThis should not happen if channels loaded correctly.")
            return []
        except json.JSONDecodeError:
            messagebox.showerror("Playlist Error", f"Error decoding playlist for {self.channel_name}: {self.playlist_path}")
            return []

    def get_current_item(self):
        if not self.playlist:
            return None, None

        total_duration = sum(item["duration"] for item in self.playlist)
        if total_duration == 0:
            return None, None

        elapsed = (time.time() - self.channel_start_time) % total_duration
        
        for item in self.playlist:
            start = item["start"]
            duration = item["duration"]
            end = start + duration
            if start <= elapsed < end:
                return item, elapsed - start
        return None, None

    def stop(self):
        if self.player and self.player.is_playing():
            self.player.stop()
            # ### FIX ### Increased sleep duration for more stability in fullscreen
            time.sleep(0.7)

    def play_current(self):
        item, offset = self.get_current_item()

        if not item:
            if self.player.is_playing():
                self.stop()
            print(f"[{self.channel_name}] No current item to play.")
            return

        if item["name"] != self.current_item_name or not self.player.is_playing():
            print(f"[{self.channel_name}] Loading media: {item['name']} at {offset:.2f}s")
            self.current_item_name = item["name"]

            media = self.vlc_instance.media_new(item["path"])
            self.player.set_media(media)
            self.player.play()

            for _ in range(20):
                if self.player.is_playing():
                    break
                time.sleep(0.1)
            else:
                print(f"Warning: Player for {self.channel_name} did not start playing after preload wait.")

            if self.player.is_playing():
                self.player.set_time(int(offset * 1000))

    def on_vlc_error(self, event):
        print(f"VLC Error on channel {self.channel_name}: Event Type {event.type}. Attempting to recover.")
        self.current_item_name = None

    def on_vlc_media_changed(self, event):
        print(f"VLC Media Changed on channel {self.channel_name}")

    def on_vlc_end_reached(self, event):
        print(f"VLC End Reached on channel {self.channel_name}")


class TVSimApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TV Sim")
        self.root.geometry("800x500")
        self.fullscreen = False

        self.canvas_frame = tk.Frame(root)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self.canvas_frame, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.update_idletasks()
        self.video_window_id = self.canvas.winfo_id()

        self.canvas.bind("<Configure>", self.on_canvas_configure)

        self.button_frame = tk.Frame(root)
        self.button_frame.pack(pady=10)
        self.up_button = tk.Button(self.button_frame, text="Channel ▲", command=self.channel_up)
        self.up_button.pack(side=tk.LEFT, padx=10)
        self.down_button = tk.Button(self.button_frame, text="Channel ▼", command=self.channel_down)
        self.down_button.pack(side=tk.LEFT, padx=10)
        self.fullscreen_button = tk.Button(self.button_frame, text="Toggle Fullscreen", command=self.toggle_fullscreen)
        self.fullscreen_button.pack(side=tk.LEFT, padx=10)

        self.active_channel_index = 0
        self.active_channel = None
        self.watch_state = {}

        # --- Logic for Direct Channel Input & Cooldown ---
        self.channel_input_buffer = ""
        self.channel_input_job = None
        self.input_font = font.Font(family="Helvetica", size=100, weight="bold")
        self.channel_input_label = tk.Label(
            self.canvas, text="", font=self.input_font, fg="white", bg="black",
        )
        self.is_changing_channel = False

        # ### NEW ### Variable to manage the cursor auto-hide timer.
        self.cursor_hide_job = None

        self.load_watch_state()

        self.channel_players = {}
        for name in RAW_CHANNELS:
            playlist_path = playlist_map.get(name)
            if playlist_path:
                self.channel_players[name] = ChannelPlayer(name, playlist_path, self.video_window_id)

        if self.watch_state:
            for name, player in self.channel_players.items():
                if player and name in self.watch_state:
                    player.channel_start_time -= self.watch_state[name]

        self.channels_guide_data = {}
        self.load_all_playlists_for_guide()

        if CHANNELS:
            self.set_channel_by_index(self.active_channel_index)
        else:
            messagebox.showwarning("No Channels", "No valid channels found. Please run the Media Analysis Tool.")

        # --- Keyboard Bindings ---
        self.root.bind("<Up>", self.channel_up)
        self.root.bind("<Down>", self.channel_down)
        self.root.bind("<space>", self.toggle_fullscreen)
        for i in range(10):
            self.root.bind(str(i), self.handle_digit_press)
        self.root.bind("`", self.go_to_tv_guide)
        # ### NEW ### Bind mouse movement to its handler function.
        self.root.bind("<Motion>", self.handle_mouse_move)

        self.monitor_playback()
        self.root.after(60 * 1000, self.periodic_save_watch_state)
        self.handle_mouse_move() # Start the cursor hide timer initially

    # ### NEW ### Hides the cursor when the mouse is idle.
    def hide_cursor(self):
        self.root.config(cursor="none")

    # ### NEW ### Shows the cursor and resets the idle timer.
    def handle_mouse_move(self, event=None):
        # Make the cursor visible
        self.root.config(cursor="")
        # If a hide job is scheduled, cancel it
        if self.cursor_hide_job:
            self.root.after_cancel(self.cursor_hide_job)
        # Schedule a new hide job for 3 seconds in the future
        self.cursor_hide_job = self.root.after(3000, self.hide_cursor)


    def handle_digit_press(self, event):
        """Handles a number key press for channel input."""
        if self.is_changing_channel:
            return
            
        if self.channel_input_job:
            self.root.after_cancel(self.channel_input_job)

        self.channel_input_buffer += event.char
        self.update_channel_input_display()

        if len(self.channel_input_buffer) == 2:
            try:
                channel_num = int(self.channel_input_buffer)
                if 0 < channel_num <= len(RAW_CHANNELS):
                    self.set_channel_by_index(channel_num - 1)
                
                self.channel_input_job = self.root.after(2500, self.clear_channel_input)

            except ValueError:
                self.clear_channel_input()
        else:
            self.channel_input_job = self.root.after(2500, self.clear_channel_input)

    def update_channel_input_display(self):
        """Shows the currently typed numbers on the canvas."""
        self.channel_input_label.config(text=self.channel_input_buffer)
        self.channel_input_label.place(relx=1.0, rely=0.0, anchor='ne', x=-20, y=20)

    def clear_channel_input(self):
        """Clears the channel input buffer and hides the display label."""
        self.channel_input_buffer = ""
        if self.channel_input_job:
            self.root.after_cancel(self.channel_input_job)
            self.channel_input_job = None
        self.channel_input_label.place_forget()

    def go_to_tv_guide(self, event=None):
        if self.is_changing_channel: return
        try:
            guide_index = CHANNELS.index("TV Guide")
            self.set_channel_by_index(guide_index)
        except ValueError:
            print("TV Guide channel not found in the channel list.")

    def load_watch_state(self):
        if os.path.exists(WATCH_STATE_PATH):
            try:
                if messagebox.askyesno("Resume?", "Resume from last watch time?"):
                    with open(WATCH_STATE_PATH, "r") as f:
                        self.watch_state = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                print(f"Warning: Could not load {WATCH_STATE_PATH}. Starting fresh. Error: {e}")
                self.watch_state = {}
        else:
            self.watch_state = {}

    def periodic_save_watch_state(self):
        self.save_watch_state()
        self.root.after(60 * 1000, self.periodic_save_watch_state)

    def save_watch_state(self):
        state = {}
        now = time.time()
        for name, player in self.channel_players.items():
            if player and hasattr(player, 'channel_start_time'):
                elapsed = now - player.channel_start_time
                state[name] = elapsed
        try:
            with open(WATCH_STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving watch state to {WATCH_STATE_PATH}: {e}")

    def on_canvas_configure(self, event):
        if self.active_channel == "TV Guide":
            self.draw_tv_guide()

    def load_all_playlists_for_guide(self):
        self.channels_guide_data = {}
        for channel_name in RAW_CHANNELS:
            playlist_file = playlist_map.get(channel_name)
            if playlist_file and playlist_file.exists():
                try:
                    with open(playlist_file, 'r') as f:
                        self.channels_guide_data[channel_name] = json.load(f)
                except json.JSONDecodeError:
                    print(f"Error decoding playlist for TV Guide: {playlist_file}. Skipping.")
                    self.channels_guide_data[channel_name] = []
            else:
                self.channels_guide_data[channel_name] = []

    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        if self.fullscreen:
            self.button_frame.pack_forget()
        else:
            self.button_frame.pack(pady=10)
        if self.active_channel == "TV Guide":
            self.draw_tv_guide()

    def set_channel_by_index(self, index):
        """Switches to the channel at the given index."""
        if not CHANNELS: return
        
        self.is_changing_channel = True
        
        index %= len(CHANNELS)
        name = CHANNELS[index]

        if self.active_channel and self.active_channel in self.channel_players:
            player = self.channel_players[self.active_channel]
            player.stop() 
            if player.player:
                if sys.platform.startswith('win'):
                    player.player.set_hwnd(0)
                else:
                    player.player.set_xwindow(0)
        
        self.active_channel_index = index
        self.active_channel = name
        self.canvas.delete("all")

        if name == "TV Guide":
            for player_obj in self.channel_players.values():
                if player_obj.player:
                    player_obj.stop()
                    if sys.platform.startswith('win'):
                        player_obj.player.set_hwnd(0)
                    else:
                        player_obj.player.set_xwindow(0)
            self.draw_tv_guide()
        else:
            player = self.channel_players[name]
            player.set_video_output(self.video_window_id) 
            player.current_item_name = None
            player.play_current()
        
        self.root.after(1000, self.enable_channel_changing)

    def enable_channel_changing(self):
        """Allows channel changing again after the cooldown period."""
        self.is_changing_channel = False

    def draw_tv_guide(self):
        self.canvas.delete("all")
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1: return
        num_channels = len(RAW_CHANNELS)
        if num_channels == 0:
            self.canvas.create_text(canvas_width / 2, canvas_height / 2, text="No channels configured.", fill="white", font=("Helvetica", 16))
            return
        time_header_height = int(canvas_height * 0.08)
        channel_name_width = int(canvas_width * 0.18)
        row_height = (canvas_height - time_header_height) / num_channels
        guide_padding = 10
        left_margin = channel_name_width + guide_padding
        top_margin = time_header_height
        time_window_minutes = 120
        display_area_width = canvas_width - left_margin - guide_padding
        if display_area_width <= 0: return
        px_per_sec = display_area_width / (time_window_minutes * 60)
        channel_font_size = max(8, min(int(row_height * 0.3), 18))
        program_font_size = max(6, min(int(row_height * 0.2), 14))
        time_font_size = max(8, min(int(time_header_height * 0.35), 22))
        font_channel = ("Helvetica", channel_font_size, "bold")
        font_regular = ("Helvetica", program_font_size)
        font_time_header = ("Helvetica", time_font_size, "bold")
        now_dt = datetime.now()
        current_unix_time = time.time()
        guide_display_start_dt = now_dt.replace(minute=(now_dt.minute // 30) * 30, second=0, microsecond=0)
        guide_display_start_unix = guide_display_start_dt.timestamp()
        time_interval_sec = 30 * 60
        for i in range(int(time_window_minutes / 30) + 2):
            marker_unix_time = guide_display_start_unix + (i * time_interval_sec)
            x_pos = left_margin + (marker_unix_time - guide_display_start_unix) * px_per_sec
            if x_pos < canvas_width:
                self.canvas.create_line(x_pos, time_header_height, x_pos, canvas_height, fill="#3a3a3a", width=1)
                label_dt = datetime.fromtimestamp(marker_unix_time)
                label_text = label_dt.strftime("%I:%M %p").lstrip("0").replace(" 0", " ")
                self.canvas.create_text(x_pos, time_header_height / 2, text=label_text, anchor="center", fill="white", font=font_time_header)
        current_x_pos = left_margin + (current_unix_time - guide_display_start_unix) * px_per_sec
        self.canvas.create_line(current_x_pos, 0, current_x_pos, canvas_height, fill="red", width=2)
        for i, ch_name in enumerate(RAW_CHANNELS):
            y_start_row = top_margin + i * row_height
            y_end_row = y_start_row + row_height
            display_name = f"{i+1:02d} {CUSTOM_NAMES.get(ch_name, ch_name)}"
            self.canvas.create_text(channel_name_width / 2, y_start_row + row_height / 2, text=display_name, anchor="center", fill="white", font=font_channel, width=channel_name_width - 10)
            self.canvas.create_line(0, y_end_row, canvas_width, y_end_row, fill="#2a2a2a", width=1)
            playlist = self.channels_guide_data.get(ch_name, [])
            if not playlist: continue
            total_playlist_duration = playlist[-1]["start"] + playlist[-1]["duration"] if playlist else 0
            if total_playlist_duration == 0: continue
            player = self.channel_players.get(ch_name)
            channel_elapsed_from_start = (current_unix_time - player.channel_start_time) if player else current_unix_time 
            effective_playlist_cycle_start_unix = current_unix_time - (channel_elapsed_from_start % total_playlist_duration)
            guide_display_end_unix = guide_display_start_unix + (time_window_minutes * 60)
            for item in playlist:
                item_abs_start_unix = effective_playlist_cycle_start_unix + item["start"]
                item_abs_end_unix = item_abs_start_unix + item["duration"]
                overlap_start_unix = max(item_abs_start_unix, guide_display_start_unix)
                overlap_end_unix = min(item_abs_end_unix, guide_display_end_unix)
                if overlap_start_unix < overlap_end_unix:
                    x_start_draw = left_margin + (overlap_start_unix - guide_display_start_unix) * px_per_sec
                    x_end_draw = left_margin + (overlap_end_unix - guide_display_start_unix) * px_per_sec
                    width = max(x_end_draw - x_start_draw, 4)
                    self.canvas.create_rectangle(x_start_draw, y_start_row + 2, x_start_draw + width, y_end_row - 2, fill="#337ab7", outline="black")
                    text = os.path.splitext(item["name"])[0][:40]
                    if width > 30:
                        self.canvas.create_text(x_start_draw + 5, y_start_row + 5, text=text, anchor="nw", fill="white", font=font_regular)
            
    def monitor_playback(self):
        if self.active_channel and self.active_channel != "TV Guide":
            player = self.channel_players[self.active_channel]
            player.play_current()
        self.root.after(1000, self._monitor_playback_loop)

    def _monitor_playback_loop(self):
        if self.active_channel and self.active_channel != "TV Guide":
            player = self.channel_players.get(self.active_channel)
            if player:
                player.play_current()
        self.root.after(1000, self._monitor_playback_loop)

    def channel_up(self, event=None):
        """Switches to the previous channel and displays its number."""
        if self.is_changing_channel: return
        self.clear_channel_input()
        
        new_index = (self.active_channel_index - 1) % len(CHANNELS)
        if CHANNELS[new_index] != "TV Guide":
            channel_num_to_display = new_index + 1
            self.channel_input_buffer = f"{channel_num_to_display:02d}"
            self.update_channel_input_display()
            self.channel_input_job = self.root.after(2500, self.clear_channel_input)

        self.set_channel_by_index(new_index)

    def channel_down(self, event=None):
        """Switches to the next channel and displays its number."""
        if self.is_changing_channel: return
        self.clear_channel_input()

        new_index = (self.active_channel_index + 1) % len(CHANNELS)
        if CHANNELS[new_index] != "TV Guide":
            channel_num_to_display = new_index + 1
            self.channel_input_buffer = f"{channel_num_to_display:02d}"
            self.update_channel_input_display()
            self.channel_input_job = self.root.after(2500, self.clear_channel_input)
            
        self.set_channel_by_index(new_index)

if __name__ == '__main__':
    root = tk.Tk()
    app = TVSimApp(root)
    atexit.register(lambda: app.save_watch_state())
    root.mainloop()
