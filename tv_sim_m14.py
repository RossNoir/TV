# tv_sim_mpv_final.py
# Description:
# This is the final, integrated version of the TV simulator.
# It includes all performance fixes for the Raspberry Pi (gpu rendering,
# event-based seeking) and incorporates the HDMI-CEC remote control
# listener, which runs automatically in a background thread.
# GHOSTING FIX: Implemented a "blackout frame" to forcefully cover
# the TV guide during channel changes, fixing the persistent rendering bug.
# UI FIX: Disabled scroll bars and interaction on the TV guide.
# OSD STYLE FIX: Changed channel number overlay to green with a transparent background.
# NAVIGATION FIX: Channel up/down now cycles through video channels only, skipping the guide.

import sys
import os
import json
import time
from pathlib import Path
import atexit
import re
from datetime import datetime, timedelta
import locale
import threading
import subprocess
from pynput.keyboard import Key, Controller

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QVBoxLayout,
    QHBoxLayout, QFrame, QLabel, QGraphicsView, QGraphicsScene,
    QMessageBox, QStackedLayout
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush, QPen, QFont, QPainter
import mpv

# --- Configuration and Path Setup (Constants only) ---
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
WATCH_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_state.json")

# --- CEC Remote Control Mappings ---
CEC_KEY_MAP = {
    30: Key.down,          # Standard CEC code for 'Channel Up'
    31: Key.up,        # Standard CEC code for 'Channel Down'
    68: Key.up,          # Some remotes use 'Arrow Up' for channel
    69: Key.down,        # Some remotes use 'Arrow Down' for channel
    44: "`",             # CEC 'Play' -> TV Guide key in your app
    46: "`",             # CEC 'Pause' -> TV Guide key in your app
    48: Key.tab,         # CEC 'Rewind' -> Last Channel key in your app
}

# --- Global variables to hold loaded config data ---
config = {}
PLAYLISTS_PATH = None
CUSTOM_NAMES = {}
ORDERED_CHANNELS_FROM_CONFIG = []
playlist_map = {}
RAW_CHANNELS = []
CHANNELS = []


class ChannelPlayer:
    """Manages an MPV player instance for a single TV channel."""

    def __init__(self, channel_name, playlist_path, app_instance):
        self.channel_name = channel_name
        self.playlist_path = playlist_path
        self.app = app_instance
        self.initial_seek_offset = None

        self.player = mpv.MPV(
            log_handler=print,
            input_default_bindings=False,
            input_vo_keyboard=False,
            vo='gpu',
            gpu_context='x11egl',
            cache='yes',
            demuxer_max_bytes=1024 * 1024 * 50,
            demuxer_readahead_secs=300
        )

        @self.player.property_observer('eof-reached')
        def on_end_reached(_name, value):
            if value:
                self.current_item_name = None

        @self.player.property_observer('playback-time')
        def on_playback_start(_name, value):
            if value is not None and self.initial_seek_offset is not None:
                self.seek(self.initial_seek_offset)
                self.initial_seek_offset = None

        self.playlist = self.load_playlist()
        self.current_item_name = None

    def set_video_output(self, window_id):
        self.player.wid = window_id

    def load_playlist(self):
        try:
            with open(self.playlist_path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            QMessageBox.critical(None, "Playlist Error", f"Error loading playlist for {self.channel_name}: {e}")
            return []

    def get_current_item(self):
        if not self.playlist: return None, None
        total_duration = sum(item["duration"] for item in self.playlist)
        if total_duration == 0: return None, None
        elapsed = (time.time() - self.app.simulation_start_time) % total_duration
        for item in self.playlist:
            if item["start"] <= elapsed < (item["start"] + item["duration"]):
                return item, elapsed - item["start"]
        return None, None

    def stop(self):
        self.player.terminate()

    def play_current(self):
        item, offset = self.get_current_item()
        if not item:
            if self.player.playback_time: self.player.stop()
            return
        if item["name"] != self.current_item_name or not self.player.playback_time:
            self.play_item(item, offset)

    def play_item(self, item, offset=0):
        try:
            self.current_item_name = item["name"]
            self.initial_seek_offset = offset
            self.player.play(item["path"])
        except Exception as e:
            print(f"Error playing item {item['name']} with MPV: {e}")
            self.current_item_name = None

    def seek(self, offset):
        if self.player and offset > 1:
            self.player.time_pos = offset


class TVSimApp(QMainWindow):
    """The main application window for the TV simulator."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Direct Sound Radio TV Sim")
        self.setGeometry(100, 100, 800, 500)
        self.setStyleSheet("background-color: black;")

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.video_frame = QFrame(self)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.layout.addWidget(self.video_frame, 1)

        self.button_widget = QWidget(self)
        self.button_layout = QHBoxLayout(self.button_widget)
        self.layout.addWidget(self.button_widget, 0, Qt.AlignmentFlag.AlignBottom)

        self.up_button = QPushButton("Up", self)
        self.down_button = QPushButton("Down", self)
        self.fullscreen_button = QPushButton("Fullscreen", self)
        for btn in [self.up_button, self.down_button, self.fullscreen_button]:
            btn.setStyleSheet("background-color: #333; color: white; padding: 5px;")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.button_layout.addWidget(btn)
        
        self.up_button.clicked.connect(self.channel_up)
        self.down_button.clicked.connect(self.channel_down)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)

        self.video_layout = QStackedLayout(self.video_frame)
        self.video_layout.setContentsMargins(0, 0, 0, 0)

        self.blackout_frame = QFrame()
        self.blackout_frame.setStyleSheet("background-color: black;")

        self.tv_guide_view = QGraphicsView()
        self.tv_guide_view.setStyleSheet("border: 0px;")
        self.tv_guide_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tv_guide_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.video_layout.addWidget(self.tv_guide_view)
        self.video_layout.addWidget(self.blackout_frame)

        self.tv_guide_scene = QGraphicsScene(self)
        self.tv_guide_view.setScene(self.tv_guide_scene)
        self.tv_guide_view.setRenderHint(QPainter.RenderHint.Antialiasing)

        # --- OSD STYLING AND PARENTING FIX ---
        self.channel_input_label = QLabel(self.video_frame) # Parent to video_frame
        self.channel_input_label.setFont(QFont("Helvetica", 100, QFont.Weight.Bold))
        self.channel_input_label.setStyleSheet("color: #5CE65C; background-color: transparent;")
        self.channel_input_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.channel_input_label.hide()
        # --- END FIX ---

        self.active_channel_index = -1
        self.last_channel_index = 0
        self.active_channel = None
        self.simulation_start_time = 0
        self.channel_input_buffer = ""
        self.is_changing_channel = False
        
        starting_channel_index = self.load_or_initialize_timeline()
        self.channel_players = {name: ChannelPlayer(name, playlist_map[name], self) for name in RAW_CHANNELS if name in playlist_map}
        self.channels_guide_data = {}
        self.load_all_playlists_for_guide()
        
        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(1000)
        self.sync_timer.timeout.connect(self.sync_active_channel)
        
        if CHANNELS:
            self.set_channel_by_index(starting_channel_index)
        else:
            QMessageBox.warning(self, "No Channels", "No valid channels found.")
            
        self.setMouseTracking(True)
        self.central_widget.setMouseTracking(True)
        
        self.cursor_hide_timer = QTimer(self)
        self.cursor_hide_timer.setInterval(3000)
        self.cursor_hide_timer.timeout.connect(lambda: self.setCursor(Qt.CursorShape.BlankCursor))
        self.cursor_hide_timer.start()
        
        self.save_state_timer = QTimer(self)
        self.save_state_timer.timeout.connect(self.save_watch_state)
        self.save_state_timer.start(60 * 1000)
        
        self.channel_input_timer = QTimer(self)
        self.channel_input_timer.setSingleShot(True)
        self.channel_input_timer.timeout.connect(self.clear_channel_input)
        
        self.keyboard_controller = Controller()
        cec_thread = threading.Thread(target=self.cec_listener_thread, daemon=True)
        cec_thread.start()

    def cec_listener_thread(self):
        print("Starting CEC Listener thread...")
        try:
            process = subprocess.Popen(['stdbuf', '-oL', 'cec-client', '-f'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            for line in iter(process.stdout.readline, ''):
                if "key pressed:" in line:
                    match = re.search(r'\((\d+)\)', line)
                    if match:
                        key_code = int(match.group(1))
                        print(f"CEC key code detected: {key_code}")
                        key_to_simulate = CEC_KEY_MAP.get(key_code)
                        if key_to_simulate:
                            print(f"--> Match found! Simulating key press: {key_to_simulate}")
                            self.keyboard_controller.press(key_to_simulate)
                            time.sleep(0.1)
                            self.keyboard_controller.release(key_to_simulate)
        except FileNotFoundError:
            print("CEC listener could not start: 'cec-client' not found. Remote control will be disabled.")
        except Exception as e:
            print(f"An error occurred in the CEC listener thread: {e}")

    def sync_active_channel(self):
        if self.active_channel and self.active_channel != "TV Guide":
            player = self.channel_players.get(self.active_channel)
            if player:
                player.play_current()

    def set_channel_by_index(self, index, is_recall=False):
        if index == self.active_channel_index and not is_recall: return
        if not CHANNELS or self.is_changing_channel: return
        if not is_recall and self.active_channel_index != -1 and CHANNELS[self.active_channel_index] != "TV Guide": self.last_channel_index = self.active_channel_index
        self.sync_timer.stop()
        if self.active_channel and self.active_channel in self.channel_players:
            self.channel_players[self.active_channel].stop()
            self.channel_players[self.active_channel] = ChannelPlayer(self.active_channel, playlist_map[self.active_channel], self)
        
        self.is_changing_channel = True
        index %= len(CHANNELS)
        name = CHANNELS[index]
        self.active_channel_index = index
        self.active_channel = name

        if name == "TV Guide":
            self.video_layout.setCurrentWidget(self.tv_guide_view)
            self.draw_tv_guide()
        else:
            self.video_layout.setCurrentWidget(self.blackout_frame)
            self.tv_guide_scene.clear()

            player = self.channel_players[name]
            player.set_video_output(int(self.video_frame.winId()))
            player.play_current()
            self.sync_timer.start()

        QTimer.singleShot(500, self.enable_channel_changing)

    def closeEvent(self, event):
        for player in self.channel_players.values():
            player.stop()
        self.save_watch_state()
        event.accept()

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.cursor_hide_timer.start()

    def keyPressEvent(self, event):
        key = event.key()
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            self.handle_digit_press(event.text())
        elif key == Qt.Key.Key_Up:
            self.channel_up()
        elif key == Qt.Key.Key_Down:
            self.channel_down()
        elif key == Qt.Key.Key_Space:
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Tab:
            self.recall_last_channel()
        elif key == Qt.Key.Key_QuoteLeft or key == Qt.Key.Key_AsciiTilde:
            self.go_to_tv_guide()
        else:
            super().keyPressEvent(event)

    def handle_digit_press(self, digit):
        if self.is_changing_channel: return
        self.channel_input_timer.stop()
        self.channel_input_buffer += digit
        self.update_channel_input_display()
        if len(self.channel_input_buffer) == 2:
            try:
                channel_num = int(self.channel_input_buffer)
                if 0 < channel_num <= len(RAW_CHANNELS): self.set_channel_by_index(channel_num - 1)
                self.channel_input_timer.start(2500)
            except ValueError:
                self.clear_channel_input()
        else:
            self.channel_input_timer.start(2500)

    def update_channel_input_display(self):
        self.channel_input_label.setText(self.channel_input_buffer)
        self.channel_input_label.adjustSize()
        self.channel_input_label.move(self.video_frame.width() - self.channel_input_label.width() - 20, 20)
        self.channel_input_label.show()
        self.channel_input_label.raise_()

    def clear_channel_input(self):
        self.channel_input_buffer = ""
        self.channel_input_label.hide()
        self.channel_input_timer.stop()

    def go_to_tv_guide(self):
        if self.is_changing_channel: return
        try:
            guide_index = CHANNELS.index("TV Guide")
            self.set_channel_by_index(guide_index)
        except ValueError:
            print("TV Guide channel not found.")

    def load_or_initialize_timeline(self):
        if os.path.exists(WATCH_STATE_PATH):
            try:
                with open(WATCH_STATE_PATH, "r") as f:
                    saved_state = json.load(f)
                    self.simulation_start_time = time.time() - saved_state.get("elapsed_time", 0)
                    return saved_state.get("last_active_channel_index", 0)
            except (json.JSONDecodeError, Exception):
                pass
        self.simulation_start_time = time.time()
        return 0

    def save_watch_state(self):
        last_index_to_save = self.last_channel_index if self.active_channel_index != -1 and CHANNELS[self.active_channel_index] == "TV Guide" else self.active_channel_index
        state = {"elapsed_time": time.time() - self.simulation_start_time, "last_active_channel_index": last_index_to_save}
        try:
            with open(WATCH_STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving watch state: {e}")

    def resizeEvent(self, event):
        if self.active_channel == "TV Guide":
            self.draw_tv_guide()
        super().resizeEvent(event)

    def load_all_playlists_for_guide(self):
        for name in RAW_CHANNELS:
            path = playlist_map.get(name)
            if path:
                try:
                    with open(path, 'r') as f:
                        self.channels_guide_data[name] = json.load(f)
                except Exception as e:
                    self.channels_guide_data[name] = []

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.button_widget.show()
        else:
            self.showFullScreen()
            self.button_widget.hide()

    def recall_last_channel(self):
        if self.is_changing_channel: return
        current_index_before_swap = self.active_channel_index
        target_index = self.last_channel_index
        self.last_channel_index = current_index_before_swap
        if CHANNELS[target_index] != "TV Guide":
            self.channel_input_buffer = f"{target_index + 1:02d}"
            self.update_channel_input_display()
            self.channel_input_timer.start(2500)
        self.set_channel_by_index(target_index, is_recall=True)

    def enable_channel_changing(self):
        self.is_changing_channel = False

    def draw_tv_guide(self):
        self.tv_guide_scene.clear()
        self.tv_guide_scene.setBackgroundBrush(QBrush(Qt.GlobalColor.black))
        canvas_width = self.tv_guide_view.width()
        canvas_height = self.tv_guide_view.height()
        if canvas_width <= 1 or canvas_height <= 1: return
        num_channels = len(RAW_CHANNELS)
        if num_channels == 0:
            text_item = self.tv_guide_scene.addText("No channels configured.", QFont("Helvetica", 16))
            text_item.setDefaultTextColor(Qt.GlobalColor.white)
            text_item.setPos((canvas_width - text_item.boundingRect().width()) / 2, (canvas_height - text_item.boundingRect().height()) / 2)
            return
            
        time_header_height = int(canvas_height * 0.08)
        channel_name_width = int(canvas_width * 0.18)
        row_height = (canvas_height - time_header_height) / num_channels
        left_margin = channel_name_width + 10
        top_margin = time_header_height
        time_window_minutes = 120
        display_area_width = canvas_width - left_margin - 10
        if display_area_width <= 0: return
        
        px_per_sec = display_area_width / (time_window_minutes * 60)
        font_channel = QFont("Helvetica", max(8, min(int(row_height * 0.3), 18)), QFont.Weight.Bold)
        font_regular = QFont("Helvetica", max(6, min(int(row_height * 0.2), 14)))
        font_time_header = QFont("Helvetica", max(8, min(int(time_header_height * 0.35), 22)), QFont.Weight.Bold)
        
        now_dt = datetime.now()
        current_unix_time = time.time()
        guide_display_start_dt = now_dt.replace(minute=(now_dt.minute // 30) * 30, second=0, microsecond=0)
        guide_display_start_unix = guide_display_start_dt.timestamp()
        time_interval_sec = 30 * 60
        
        for i in range(int(time_window_minutes / 30) + 2):
            marker_unix_time = guide_display_start_unix + (i * time_interval_sec)
            x_pos = left_margin + (marker_unix_time - guide_display_start_unix) * px_per_sec
            if x_pos < canvas_width:
                self.tv_guide_scene.addLine(x_pos, time_header_height, x_pos, canvas_height, QPen(QColor("#3a3a3a")))
                label_dt = datetime.fromtimestamp(marker_unix_time)
                label_text = label_dt.strftime("%I:%M %p").lstrip("0").replace(" 0", " ")
                text_item = self.tv_guide_scene.addText(label_text, font_time_header)
                text_item.setDefaultTextColor(Qt.GlobalColor.white)
                text_item.setPos(x_pos - text_item.boundingRect().width() / 2, (time_header_height - text_item.boundingRect().height()) / 2)
                
        current_x_pos = left_margin + (current_unix_time - guide_display_start_unix) * px_per_sec
        now_pen = QPen(Qt.GlobalColor.red)
        now_pen.setWidth(2)
        self.tv_guide_scene.addLine(current_x_pos, 0, current_x_pos, canvas_height, now_pen)
        
        for i, ch_name in enumerate(RAW_CHANNELS):
            y_start_row = top_margin + i * row_height
            display_name = f"{i+1:02d} {CUSTOM_NAMES.get(ch_name, ch_name)}"
            ch_text_item = self.tv_guide_scene.addText(display_name, font_channel)
            ch_text_item.setDefaultTextColor(Qt.GlobalColor.white)
            ch_text_item.setTextWidth(channel_name_width - 10)
            ch_text_item.setPos((channel_name_width - ch_text_item.boundingRect().width()) / 2, y_start_row + (row_height - ch_text_item.boundingRect().height()) / 2)
            self.tv_guide_scene.addLine(0, y_start_row + row_height, canvas_width, y_start_row + row_height, QPen(QColor("#2a2a2a")))
            
            playlist = self.channels_guide_data.get(ch_name, [])
            if not playlist: continue
            total_playlist_duration = playlist[-1]["start"] + playlist[-1]["duration"] if playlist else 0
            if total_playlist_duration == 0: continue
            
            channel_elapsed_from_start = current_unix_time - self.simulation_start_time
            effective_playlist_cycle_start_unix = current_unix_time - (channel_elapsed_from_start % total_playlist_duration)
            guide_display_end_unix = guide_display_start_unix + (time_window_minutes * 60)
            
            for item in playlist:
                item_abs_start_unix = effective_playlist_cycle_start_unix + item["start"]
                item_abs_end_unix = item_abs_start_unix + item["duration"]
                overlap_start_unix = max(item_abs_start_unix, guide_display_start_unix)
                overlap_end_unix = min(item_abs_end_unix, guide_display_end_unix)
                if overlap_start_unix < overlap_end_unix:
                    x_start_draw = left_margin + (overlap_start_unix - guide_display_start_unix) * px_per_sec
                    width = max((left_margin + (overlap_end_unix - guide_display_start_unix) * px_per_sec) - x_start_draw, 4)
                    rect_brush = QBrush(QColor("#337ab7"))
                    rect_pen = QPen(Qt.GlobalColor.black)
                    self.tv_guide_scene.addRect(x_start_draw, y_start_row + 2, width, row_height - 4, rect_pen, rect_brush)
                    text = os.path.splitext(item["name"])[0][:40]
                    if width > 30:
                        prog_text_item = self.tv_guide_scene.addText(text, font_regular)
                        prog_text_item.setDefaultTextColor(Qt.GlobalColor.white)
                        prog_text_item.setPos(x_start_draw + 5, y_start_row + 5)

    def channel_up(self):
        """Switches to the previous video channel, skipping the guide and wrapping around."""
        if self.is_changing_channel: return
        self.clear_channel_input()
        
        num_video_channels = len(RAW_CHANNELS)
        if num_video_channels == 0: return

        # Determine the current index within the video-only channels
        current_video_index = self.active_channel_index if self.active_channel != "TV Guide" else num_video_channels
        
        new_index = (current_video_index - 1 + num_video_channels) % num_video_channels
        
        self.channel_input_buffer = f"{new_index + 1:02d}"
        self.update_channel_input_display()
        self.channel_input_timer.start(2500)
        self.set_channel_by_index(new_index)

    def channel_down(self):
        """Switches to the next video channel, skipping the guide and wrapping around."""
        if self.is_changing_channel: return
        self.clear_channel_input()
        
        num_video_channels = len(RAW_CHANNELS)
        if num_video_channels == 0: return

        # Determine the current index within the video-only channels
        current_video_index = self.active_channel_index if self.active_channel != "TV Guide" else -1
        
        new_index = (current_video_index + 1) % num_video_channels
        
        self.channel_input_buffer = f"{new_index + 1:02d}"
        self.update_channel_input_display()
        self.channel_input_timer.start(2500)
        self.set_channel_by_index(new_index)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    locale.setlocale(locale.LC_NUMERIC, "C")
    
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except FileNotFoundError:
        QMessageBox.critical(None, "Configuration Error", f"Config file not found: {CONFIG_PATH}\nPlease run the Media Analysis Tool first.")
        sys.exit(1)
    except json.JSONDecodeError:
        QMessageBox.critical(None, "Configuration Error", f"Error decoding config.json. Please check its format.")
        sys.exit(1)
        
    PLAYLISTS_PATH = Path(config.get("playlists_path", "playlists")).resolve()
    CUSTOM_NAMES = config.get("custom_names", {})
    ORDERED_CHANNELS_FROM_CONFIG = config.get("channel_order", [])
    
    playlist_files = list(PLAYLISTS_PATH.glob("*_playlist.json"))
    for f in playlist_files:
        match = re.match(r"(\d+_)(.+?)(_playlist\.json)", f.name)
        if match:
            channel_name = match.group(2)
            playlist_map[channel_name] = f
            
    RAW_CHANNELS = [ch for ch in ORDERED_CHANNELS_FROM_CONFIG if ch in playlist_map]
    CHANNELS = RAW_CHANNELS + ["TV Guide"]
    
    if not RAW_CHANNELS:
        QMessageBox.warning(None, "No Playlists Found", "Could not find any valid, numbered playlists in the specified directory.")
        
    player = TVSimApp()
    player.show()
    atexit.register(player.save_watch_state)
    sys.exit(app.exec())

