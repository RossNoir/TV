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
# ON DEMAND: Added on-demand content browser and playback with transport controls.

import sys
import os
import json
import time
from pathlib import Path
import atexit
import re
from datetime import datetime
import locale
import threading
import subprocess
import random

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QVBoxLayout,
    QHBoxLayout, QFrame, QLabel, QGraphicsView, QGraphicsScene,
    QMessageBox, QStackedLayout, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QPen, QFont, QPainter, QLinearGradient, QPainterPath
import mpv

# --- Configuration and Path Setup ---
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
WATCH_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_state.json")

# --- TV Guide palette (Prevue-Guide-style navy/cyan/gold) ---
GUIDE_BG_TOP = QColor("#0a1128")
GUIDE_BG_BOTTOM = QColor("#131c3d")
GUIDE_HEADER_BG_TOP = QColor("#1b2a5e")
GUIDE_HEADER_BG_BOTTOM = QColor("#0d1638")
GUIDE_CORNER_BG_TOP = QColor("#2a3f82")
GUIDE_CORNER_BG_BOTTOM = QColor("#182757")
GUIDE_ROW_ALT_A = QColor("#101a3a")
GUIDE_ROW_ALT_B = QColor("#141f45")
GUIDE_CHANNEL_COL_BG = QColor("#16204a")
GUIDE_GRIDLINE = QColor("#2a3766")
GUIDE_DIVIDER = QColor("#3a4a86")
GUIDE_ACCENT_CYAN = QColor("#4fd8e8")
GUIDE_ACCENT_GOLD = QColor("#f2c94c")
GUIDE_PROGRAM_TOP = QColor("#227a9c")
GUIDE_PROGRAM_BOTTOM = QColor("#0f4a63")
GUIDE_PROGRAM_LIVE_TOP = QColor("#e0a93a")
GUIDE_PROGRAM_LIVE_BOTTOM = QColor("#a9741f")
GUIDE_TEXT_WHITE = QColor("#f5f7ff")
GUIDE_TEXT_DIM = QColor("#9fb0d8")


# --- Global variables to hold loaded config data ---
config = {}
PLAYLISTS_PATH = None
CUSTOM_NAMES = {}
ORDERED_CHANNELS_FROM_CONFIG = []
playlist_map = {}
RAW_CHANNELS = []
CHANNELS = []


class OnDemandBrowser(QWidget):
    """Four-level browser: Channel → Show → Season → Episode, built from existing playlists."""

    file_selected = pyqtSignal(str)

    def __init__(self, channel_data, parent=None):
        super().__init__(parent)
        self.channel_data = channel_data   # {channel_name: [{name, path, duration, start}]}
        self.level = "channels"            # "channels" | "shows" | "seasons" | "episodes"
        self.selected_channel = None
        self.selected_show = None
        self.selected_season = None
        self.hierarchy = {}                # {show: {season_or_None: [items]}}
        self.items = []                    # data for current list rows
        self._build_ui()
        self._show_channels()

    def _build_ui(self):
        # WA_OpaquePaintEvent ensures this widget fully covers the MPV native surface
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background-color: #0d0d1a;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 20)
        layout.setSpacing(10)

        title = QLabel("ON DEMAND")
        title.setFont(QFont("Helvetica", 32, QFont.Weight.Bold))
        title.setStyleSheet("color: #e8a020; background: transparent;")
        layout.addWidget(title)

        self.path_label = QLabel()
        self.path_label.setFont(QFont("Helvetica", 13))
        self.path_label.setStyleSheet("color: #888; background: transparent;")
        layout.addWidget(self.path_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #2a2a50;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        self.list_widget = QListWidget()
        self.list_widget.setFont(QFont("Helvetica", 15))
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #0d0d1a;
                border: none;
                color: white;
                outline: none;
            }
            QListWidget::item {
                padding: 10px 16px;
                border-radius: 6px;
                margin: 2px 0px;
                background-color: #0d0d1a;
                color: white;
            }
            QListWidget::item:selected,
            QListWidget::item:selected:active,
            QListWidget::item:selected:!active {
                background-color: #1a3a6a;
                color: #f0d080;
            }
            QListWidget::item:hover:!selected {
                background-color: #1a1a3a;
            }
        """)
        # StrongFocus so Qt repaints deselected rows correctly on keyboard navigation
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.list_widget, 1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background-color: #2a2a50;")
        sep2.setFixedHeight(1)
        layout.addWidget(sep2)

        self.hint_label = QLabel()
        self.hint_label.setFont(QFont("Helvetica", 10))
        self.hint_label.setStyleSheet("color: #444; background: transparent;")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)

    # --- hierarchy ---

    def _build_hierarchy(self, playlist_items):
        """Group flat playlist items by Show/Season directory structure inferred from paths."""
        if not playlist_items:
            return {}
        paths = [item['path'] for item in playlist_items]
        try:
            common = os.path.commonpath(paths)
        except ValueError:
            common = str(Path(paths[0]).parent)
        hierarchy = {}
        for item in playlist_items:
            try:
                rel = Path(item['path']).relative_to(common)
            except ValueError:
                rel = Path(item['path']).name
            parts = rel.parts  # ('Show', 'Season', 'ep.mp4') | ('Show', 'ep.mp4') | ('ep.mp4',)
            if len(parts) >= 3:
                show, season = parts[0], parts[1]
            elif len(parts) == 2:
                show, season = parts[0], None
            else:
                show, season = "(Unsorted)", None
            hierarchy.setdefault(show, {}).setdefault(season, []).append(item)
        return hierarchy

    # --- levels ---

    def _set_nav_hint(self, back_label):
        self.hint_label.setText(
            f"UP / DOWN  Navigate    ENTER / RIGHT  Open    LEFT  Back to {back_label}    ESC  Exit On Demand"
        )

    def _show_channels(self):
        self.level = "channels"
        self.selected_channel = None
        self.list_widget.clear()
        self.items = []
        self.path_label.setText("Select a Channel")
        self.hint_label.setText(
            "UP / DOWN  Navigate    ENTER / RIGHT  Open Channel    ESC  Exit On Demand"
        )
        for i, name in enumerate(RAW_CHANNELS):
            playlist = self.channel_data.get(name, [])
            display_name = CUSTOM_NAMES.get(name, name)
            count = len(playlist)
            litem = QListWidgetItem(
                f"  {i + 1:02d}  {display_name}  —  {count} item{'s' if count != 1 else ''}"
            )
            litem.setForeground(QColor("#aaccff"))
            self.list_widget.addItem(litem)
            self.items.append(name)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _show_shows(self, channel_name):
        self.level = "shows"
        self.selected_channel = channel_name
        self.hierarchy = self._build_hierarchy(self.channel_data.get(channel_name, []))
        self.list_widget.clear()
        self.items = []
        self.path_label.setText(CUSTOM_NAMES.get(channel_name, channel_name))
        self._set_nav_hint("Channels")
        for show_name in sorted(self.hierarchy.keys()):
            total = len({item["path"] for eps in self.hierarchy[show_name].values() for item in eps})
            litem = QListWidgetItem(
                f"  {show_name}  —  {total} episode{'s' if total != 1 else ''}"
            )
            litem.setForeground(QColor("#aaccff"))
            self.list_widget.addItem(litem)
            self.items.append(show_name)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _show_seasons(self, show_name):
        self.selected_show = show_name
        seasons = self.hierarchy.get(show_name, {})
        named = sorted(s for s in seasons if s is not None)
        # If no season folders (files live directly in the show folder), skip to episodes
        if not named:
            self._show_episodes(None)
            return
        self.level = "seasons"
        self.list_widget.clear()
        self.items = []
        display_ch = CUSTOM_NAMES.get(self.selected_channel, self.selected_channel)
        self.path_label.setText(f"{display_ch}  >  {show_name}")
        self._set_nav_hint("Shows")
        for season_name in named:
            eps = seasons[season_name]
            unique = len({item["path"] for item in eps})
            litem = QListWidgetItem(
                f"  {season_name}  —  {unique} episode{'s' if unique != 1 else ''}"
            )
            litem.setForeground(QColor("#aaccff"))
            self.list_widget.addItem(litem)
            self.items.append(season_name)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _show_episodes(self, season_name):
        self.selected_season = season_name
        self.level = "episodes"
        episode_items = self.hierarchy.get(self.selected_show, {}).get(season_name, [])
        self.list_widget.clear()
        self.items = []
        display_ch = CUSTOM_NAMES.get(self.selected_channel, self.selected_channel)
        if season_name:
            self.path_label.setText(f"{display_ch}  >  {self.selected_show}  >  {season_name}")
        else:
            self.path_label.setText(f"{display_ch}  >  {self.selected_show}")
        self.hint_label.setText(
            "UP / DOWN  Navigate    ENTER / RIGHT  Play    LEFT  Back    ESC  Exit On Demand"
        )
        seen_paths = set()
        for item in sorted(episode_items, key=lambda x: x["name"].lower()):
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            name_no_ext = os.path.splitext(item["name"])[0]
            dur = self._fmt_duration(item["duration"])
            litem = QListWidgetItem(f"  {name_no_ext}  [{dur}]")
            litem.setForeground(QColor("#dddddd"))
            self.list_widget.addItem(litem)
            self.items.append(item["path"])
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    # --- controls ---

    def navigate(self, direction):
        count = self.list_widget.count()
        if count == 0:
            return
        row = (self.list_widget.currentRow() + direction) % count
        self.list_widget.setCurrentRow(row)
        self.list_widget.viewport().update()

    def select_current(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.items):
            return
        if self.level == "channels":
            self._show_shows(self.items[row])
        elif self.level == "shows":
            self._show_seasons(self.items[row])
        elif self.level == "seasons":
            self._show_episodes(self.items[row])
        else:
            self.file_selected.emit(self.items[row])

    def go_back(self):
        if self.level == "episodes":
            if self.selected_season is not None:
                self._show_seasons(self.selected_show)
            else:
                self._show_shows(self.selected_channel)
        elif self.level == "seasons":
            self._show_shows(self.selected_channel)
        elif self.level == "shows":
            self._show_channels()
        # at channels level go_back is a no-op; ESC exits via TVSimApp

    @staticmethod
    def _fmt_duration(secs):
        if secs is None:
            return "?"
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class OnDemandControls(QWidget):
    """Floating transport controls overlaid on on-demand video playback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._build_ui()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(4000)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch()

        self.bar = QWidget()
        self.bar.setStyleSheet(
            "background-color: rgba(0, 0, 0, 210); border-top: 1px solid #2a2a50;"
        )
        bar_layout = QVBoxLayout(self.bar)
        bar_layout.setContentsMargins(30, 15, 30, 18)
        bar_layout.setSpacing(6)

        self.title_label = QLabel("")
        self.title_label.setFont(QFont("Helvetica", 15, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: white; background: transparent;")
        bar_layout.addWidget(self.title_label)

        self.progress_label = QLabel("--:-- / --:--")
        self.progress_label.setFont(QFont("Courier", 12))
        self.progress_label.setStyleSheet("color: #e8a020; background: transparent;")
        bar_layout.addWidget(self.progress_label)

        hint = QLabel(
            "SPACE  Play/Pause    LEFT  -30s    RIGHT  +30s    ESC  Back to Browser"
        )
        hint.setFont(QFont("Helvetica", 10))
        hint.setStyleSheet("color: #555; background: transparent;")
        bar_layout.addWidget(hint)

        outer.addWidget(self.bar)

    def show_controls(self):
        self.show()
        self.raise_()
        self._hide_timer.start()

    def update_info(self, title, current_secs, total_secs, paused):
        self.title_label.setText(title)
        cur = self._fmt(current_secs)
        tot = self._fmt(total_secs)
        status = "|| PAUSED" if paused else "> PLAYING"
        self.progress_label.setText(f"{status}    {cur} / {tot}")

    @staticmethod
    def _fmt(secs):
        if secs is None:
            return "--:--"
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


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
            demuxer_readahead_secs=300,
            sid='no',
            sub_auto='no'
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

    _cec_signal = pyqtSignal(int)   # emitted from CEC thread; routed on the Qt main thread

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
        self.on_demand_button = QPushButton("On Demand  [O]", self)

        for btn in [self.up_button, self.down_button, self.fullscreen_button]:
            btn.setStyleSheet("background-color: #333; color: white; padding: 5px;")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.button_layout.addWidget(btn)

        self.on_demand_button.setStyleSheet(
            "background-color: #5a3800; color: #e8a020; padding: 5px; font-weight: bold;"
        )
        self.on_demand_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button_layout.addWidget(self.on_demand_button)

        self.up_button.clicked.connect(self.channel_up)
        self.down_button.clicked.connect(self.channel_down)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        self.on_demand_button.clicked.connect(self.enter_on_demand_browser)

        self.video_layout = QStackedLayout(self.video_frame)
        self.video_layout.setContentsMargins(0, 0, 0, 0)

        self.blackout_frame = QFrame()
        self.blackout_frame.setStyleSheet("background-color: black;")

        self.tv_guide_view = QGraphicsView()
        self.tv_guide_view.setStyleSheet("border: 0px;")
        self.tv_guide_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tv_guide_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.video_layout.addWidget(self.tv_guide_view)   # index 0
        self.video_layout.addWidget(self.blackout_frame)  # index 1
        # on_demand_browser added at index 2 after playlist data is loaded

        self.tv_guide_scene = QGraphicsScene(self)
        self.tv_guide_view.setScene(self.tv_guide_scene)
        self.tv_guide_view.setRenderHint(QPainter.RenderHint.Antialiasing)

        # OSD channel number label
        self.channel_input_label = QLabel(self.video_frame)
        self.channel_input_label.setFont(QFont("Helvetica", 100, QFont.Weight.Bold))
        self.channel_input_label.setStyleSheet("color: #5CE65C; background-color: transparent;")
        self.channel_input_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.channel_input_label.hide()

        # On-demand playback controls overlay (floats over video_frame, same pattern as OSD label)
        self.on_demand_controls = OnDemandControls(self.video_frame)

        # On-demand state
        self.on_demand_mode = None   # None | "browser" | "playing"
        self.on_demand_player_instance = None
        self.on_demand_current_file = None
        self.on_demand_paused = False

        self.on_demand_update_timer = QTimer(self)
        self.on_demand_update_timer.setInterval(500)
        self.on_demand_update_timer.timeout.connect(self._update_on_demand_display)

        self.active_channel_index = -1
        self.last_channel_index = 0
        self.active_channel = None
        self.simulation_start_time = 0
        self.channel_input_buffer = ""
        self.is_changing_channel = False

        self.guide_scroll_timer = QTimer(self)
        self.guide_scroll_timer.setInterval(33)  # ~30fps
        self.guide_scroll_timer.timeout.connect(self._guide_scroll_tick)
        self.guide_clock_timer = QTimer(self)
        self.guide_clock_timer.setInterval(1000)
        self.guide_clock_timer.timeout.connect(self._guide_clock_tick)
        self.GUIDE_ROWS_VISIBLE = 8
        self.GUIDE_ROWS_PER_PAGE = 4
        self.GUIDE_PAUSE_MS = 4000
        self.GUIDE_ROW_SCROLL_MS = 1000
        self._guide_scroll_top_index = 0
        self._guide_scroll_pixel_offset = 0.0
        self._guide_scroll_rows_this_page = 0
        self._guide_scroll_paused_until = 0.0

        starting_channel_index = self.load_or_initialize_timeline()
        self.channel_players = {
            name: ChannelPlayer(name, playlist_map[name], self)
            for name in RAW_CHANNELS if name in playlist_map
        }
        self.channels_guide_data = {}
        self.load_all_playlists_for_guide()

        self.on_demand_browser = OnDemandBrowser(channel_data=self.channels_guide_data)
        self.on_demand_browser.file_selected.connect(self.on_demand_play_file)
        self.video_layout.addWidget(self.on_demand_browser)   # index 2

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

        self._cec_signal.connect(self._dispatch_cec)
        cec_thread = threading.Thread(target=self.cec_listener_thread, daemon=True)
        cec_thread.start()

    # ------------------------------------------------------------------ #
    #  On-demand methods                                                   #
    # ------------------------------------------------------------------ #

    def enter_on_demand_browser(self):
        """Switch from normal TV into the on-demand file browser."""
        if self.on_demand_mode:
            return
        self.sync_timer.stop()
        if self.active_channel and self.active_channel in self.channel_players:
            self.channel_players[self.active_channel].stop()
        self.on_demand_mode = "browser"
        # Blackout first so MPV's last rendered frame doesn't bleed through the browser UI
        self.video_layout.setCurrentWidget(self.blackout_frame)
        QTimer.singleShot(150, lambda: self.video_layout.setCurrentWidget(self.on_demand_browser))

    def exit_on_demand(self):
        """Exit on-demand entirely and resume the last regular channel."""
        self._stop_on_demand_playback()
        self.on_demand_mode = None
        self.on_demand_controls.hide()
        self.on_demand_update_timer.stop()
        # Recreate and resume the active channel player (master clock kept ticking)
        if self.active_channel and self.active_channel in self.channel_players:
            name = self.active_channel
            self.channel_players[name] = ChannelPlayer(name, playlist_map[name], self)
            player = self.channel_players[name]
            player.set_video_output(int(self.video_frame.winId()))
            self.video_layout.setCurrentWidget(self.blackout_frame)
            player.play_current()
            self.sync_timer.start()

    def on_demand_play_file(self, path):
        """Begin on-demand playback of the selected file."""
        self._stop_on_demand_playback()
        self.on_demand_current_file = path
        self.on_demand_paused = False
        self.on_demand_mode = "playing"

        player = self._get_or_create_on_demand_player()
        player.wid = int(self.video_frame.winId())
        self.video_layout.setCurrentWidget(self.blackout_frame)
        player.play(path)

        self.on_demand_update_timer.start()
        self._reposition_on_demand_controls()
        self.on_demand_controls.update_info(Path(path).stem, 0, None, False)
        self.on_demand_controls.show_controls()
        # Reclaim keyboard focus from the embedded MPV window so keyPressEvent fires
        self.activateWindow()
        self.setFocus()

    def on_demand_back_to_browser(self):
        """Stop on-demand playback and return to the file browser."""
        self._stop_on_demand_playback()
        self.on_demand_update_timer.stop()
        self.on_demand_controls.hide()
        self.on_demand_mode = "browser"
        self.video_layout.setCurrentWidget(self.blackout_frame)
        QTimer.singleShot(150, lambda: self.video_layout.setCurrentWidget(self.on_demand_browser))

    def on_demand_toggle_pause(self):
        if self.on_demand_player_instance is None:
            return
        try:
            self.on_demand_player_instance.command('cycle', 'pause')
            self.on_demand_paused = bool(self.on_demand_player_instance.pause)
        except Exception as e:
            print(f"on_demand_toggle_pause error: {e}")
            return
        self.on_demand_controls.show_controls()
        self._update_on_demand_display()

    def on_demand_seek(self, delta):
        if self.on_demand_player_instance is None:
            return
        try:
            self.on_demand_player_instance.seek(delta, 'relative')
        except Exception:
            pass
        self.on_demand_controls.show_controls()
        self._update_on_demand_display()

    def _get_or_create_on_demand_player(self):
        if self.on_demand_player_instance is None:
            self.on_demand_player_instance = mpv.MPV(
                log_handler=print,
                input_default_bindings=False,
                input_vo_keyboard=False,
                vo='gpu',
                gpu_context='x11egl',
                cache='yes',
                sid='no',
                sub_auto='no',
            )
        return self.on_demand_player_instance

    def _stop_on_demand_playback(self):
        if self.on_demand_player_instance:
            try:
                self.on_demand_player_instance.stop()
            except Exception:
                pass

    def _update_on_demand_display(self):
        if self.on_demand_mode != "playing" or self.on_demand_player_instance is None:
            return
        if not self.on_demand_controls.isVisible():
            return
        try:
            current = self.on_demand_player_instance.playback_time
            total = self.on_demand_player_instance.duration
            title = Path(self.on_demand_current_file).stem if self.on_demand_current_file else ""
            self.on_demand_controls.update_info(title, current, total, self.on_demand_paused)
        except Exception:
            pass

    def _reposition_on_demand_controls(self):
        if hasattr(self, 'on_demand_controls'):
            self.on_demand_controls.setGeometry(
                0, 0, self.video_frame.width(), self.video_frame.height()
            )

    # ------------------------------------------------------------------ #
    #  Core TV simulator methods                                           #
    # ------------------------------------------------------------------ #

    def cec_listener_thread(self):
        print("Starting CEC Listener thread...")
        try:
            process = subprocess.Popen(
                ['stdbuf', '-oL', 'cec-client', '-f'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
            )
            for line in iter(process.stdout.readline, ''):
                if "key pressed:" in line:
                    match = re.search(r'\((\d+)\)', line)
                    if match:
                        key_code = int(match.group(1))
                        print(f"CEC key code detected: {key_code}")
                        self._cec_signal.emit(key_code)   # thread-safe; Qt queues to main thread
        except FileNotFoundError:
            print("CEC listener could not start: 'cec-client' not found. Remote control will be disabled.")
        except Exception as e:
            print(f"An error occurred in the CEC listener thread: {e}")

    def _dispatch_cec(self, code):
        """Handle a CEC key code on the Qt main thread (no OS-focus dependency)."""
        print(f"--> CEC dispatch: code={code}  mode={self.on_demand_mode}")
        if self.on_demand_mode == "playing":
            if code == 8:            self.on_demand_toggle_pause()
            elif code == 3:          self.on_demand_seek(-30)
            elif code == 4:          self.on_demand_seek(30)
            elif code == 49:         self.exit_on_demand()
            else:                    self.on_demand_controls.show_controls()
        elif self.on_demand_mode == "browser":
            if code == 1:            self.on_demand_browser.navigate(-1)
            elif code == 2:          self.on_demand_browser.navigate(1)
            elif code in (4, 8):     self.on_demand_browser.select_current()
            elif code == 3:          self.on_demand_browser.go_back()
            elif code == 49:         self.exit_on_demand()
        else:  # normal TV
            if code in (2, 30):      self.channel_down()
            elif code in (1, 31):    self.channel_up()
            elif code in (44, 46):   self.go_to_tv_guide()
            elif code == 48:         self.recall_last_channel()
            elif code == 49:         self.enter_on_demand_browser()

    def sync_active_channel(self):
        if self.active_channel and self.active_channel != "TV Guide":
            player = self.channel_players.get(self.active_channel)
            if player:
                player.play_current()

    def set_channel_by_index(self, index, is_recall=False):
        if self.on_demand_mode:
            return
        if index == self.active_channel_index and not is_recall: return
        if not CHANNELS or self.is_changing_channel: return
        if not is_recall and self.active_channel_index != -1 and CHANNELS[self.active_channel_index] != "TV Guide":
            self.last_channel_index = self.active_channel_index
        self.sync_timer.stop()
        if self.active_channel and self.active_channel in self.channel_players:
            self.channel_players[self.active_channel].stop()
            self.channel_players[self.active_channel] = ChannelPlayer(
                self.active_channel, playlist_map[self.active_channel], self
            )

        self.is_changing_channel = True
        index %= len(CHANNELS)
        name = CHANNELS[index]
        self.active_channel_index = index
        self.active_channel = name

        if name == "TV Guide":
            self.video_layout.setCurrentWidget(self.tv_guide_view)
            self.start_guide_scroll()
        else:
            self.stop_guide_scroll()
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
        if self.on_demand_player_instance:
            try:
                self.on_demand_player_instance.terminate()
            except Exception:
                pass
        self.save_watch_state()
        event.accept()

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.cursor_hide_timer.start()

    def keyPressEvent(self, event):
        key = event.key()

        # O key / CEC 49 — toggle on-demand from any mode
        if key == Qt.Key.Key_O:
            if self.on_demand_mode:
                self.exit_on_demand()
            else:
                self.enter_on_demand_browser()
            return

        # On-demand browser mode
        if self.on_demand_mode == "browser":
            if key == Qt.Key.Key_Up:
                self.on_demand_browser.navigate(-1)
            elif key == Qt.Key.Key_Down:
                self.on_demand_browser.navigate(1)
            elif key == Qt.Key.Key_Right:
                self.on_demand_browser.select_current()
            elif key == Qt.Key.Key_Left:
                self.on_demand_browser.go_back()
            elif key == Qt.Key.Key_Escape:
                self.exit_on_demand()
            return

        # On-demand playback mode — transport controls
        if self.on_demand_mode == "playing":
            if key == Qt.Key.Key_Space:
                self.on_demand_toggle_pause()
            elif key == Qt.Key.Key_Left:
                self.on_demand_seek(-30)
            elif key == Qt.Key.Key_Right:
                self.on_demand_seek(30)
            elif key == Qt.Key.Key_Escape:
                self.on_demand_back_to_browser()
            else:
                self.on_demand_controls.show_controls()
            return

        # Normal TV mode
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
        self.channel_input_label.move(
            self.video_frame.width() - self.channel_input_label.width() - 20, 20
        )
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
        last_index_to_save = (
            self.last_channel_index
            if self.active_channel_index != -1 and CHANNELS[self.active_channel_index] == "TV Guide"
            else self.active_channel_index
        )
        state = {
            "elapsed_time": time.time() - self.simulation_start_time,
            "last_active_channel_index": last_index_to_save,
        }
        try:
            with open(WATCH_STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving watch state: {e}")

    def resizeEvent(self, event):
        if self.active_channel == "TV Guide":
            self.draw_tv_guide()
        self._reposition_on_demand_controls()
        super().resizeEvent(event)

    def load_all_playlists_for_guide(self):
        for name in RAW_CHANNELS:
            path = playlist_map.get(name)
            if path:
                try:
                    with open(path, 'r') as f:
                        self.channels_guide_data[name] = json.load(f)
                except Exception:
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

    def start_guide_scroll(self):
        num_channels = len(RAW_CHANNELS)
        # Start mid-stream at a random channel/pause-cycle position so the
        # guide never looks like it "just started" — it reads as though it's
        # been scrolling continuously in the background the whole time.
        self._guide_scroll_top_index = random.randrange(num_channels) if num_channels else 0
        self._guide_scroll_pixel_offset = 0.0
        self._guide_scroll_rows_this_page = random.randrange(self.GUIDE_ROWS_PER_PAGE)
        self._guide_scroll_paused_until = 0.0
        self.draw_tv_guide()
        if num_channels > self.GUIDE_ROWS_VISIBLE:
            self.guide_scroll_timer.start()
        self.guide_clock_timer.start()

    def stop_guide_scroll(self):
        self.guide_scroll_timer.stop()
        self.guide_clock_timer.stop()

    def _guide_clock_tick(self):
        # Keeps the clock and "now" line live even while scrolling is
        # paused between pages, or when there are too few channels to scroll.
        # guide_scroll_timer.isActive() is true even during a pause (the timer
        # keeps firing, it just no-ops), so this can't gate on that — always redraw.
        self.draw_tv_guide()

    def _guide_scroll_tick(self):
        now = time.time()
        if now < self._guide_scroll_paused_until:
            return
        num_channels = len(RAW_CHANNELS)
        canvas_height = self.tv_guide_view.height()
        time_header_height = int(canvas_height * 0.08)
        row_height = (canvas_height - time_header_height) / self.GUIDE_ROWS_VISIBLE
        if row_height <= 0:
            return
        self._guide_scroll_pixel_offset += row_height * (self.guide_scroll_timer.interval() / self.GUIDE_ROW_SCROLL_MS)
        if self._guide_scroll_pixel_offset >= row_height:
            self._guide_scroll_pixel_offset -= row_height
            self._guide_scroll_top_index = (self._guide_scroll_top_index + 1) % num_channels
            self._guide_scroll_rows_this_page += 1
            if self._guide_scroll_rows_this_page >= self.GUIDE_ROWS_PER_PAGE:
                self._guide_scroll_rows_this_page = 0
                self._guide_scroll_paused_until = now + (self.GUIDE_PAUSE_MS / 1000.0)
        self.draw_tv_guide()

    def draw_tv_guide(self):
        self.tv_guide_scene.clear()
        canvas_width = self.tv_guide_view.width()
        canvas_height = self.tv_guide_view.height()
        if canvas_width <= 1 or canvas_height <= 1: return
        bg_gradient = QLinearGradient(0, 0, 0, canvas_height)
        bg_gradient.setColorAt(0, GUIDE_BG_TOP)
        bg_gradient.setColorAt(1, GUIDE_BG_BOTTOM)
        self.tv_guide_scene.setBackgroundBrush(QBrush(bg_gradient))
        num_channels = len(RAW_CHANNELS)
        if num_channels == 0:
            text_item = self.tv_guide_scene.addText("No channels configured.", QFont("Helvetica", 16))
            text_item.setDefaultTextColor(GUIDE_TEXT_WHITE)
            text_item.setPos(
                (canvas_width - text_item.boundingRect().width()) / 2,
                (canvas_height - text_item.boundingRect().height()) / 2,
            )
            return

        self.tv_guide_scene.setSceneRect(0, 0, canvas_width, canvas_height)

        time_header_height = int(canvas_height * 0.08)
        channel_name_width = int(canvas_width * 0.18)
        row_height = (canvas_height - time_header_height) / self.GUIDE_ROWS_VISIBLE
        left_margin = channel_name_width + 10
        top_margin = time_header_height
        time_window_minutes = 120
        display_area_width = canvas_width - left_margin - 10
        if display_area_width <= 0: return

        px_per_sec = display_area_width / (time_window_minutes * 60)
        font_channel = QFont("Helvetica", max(8, min(int(row_height * 0.3), 18)), QFont.Weight.Bold)
        font_regular = QFont("Helvetica", max(6, min(int(row_height * 0.2), 14)))
        font_time_header = QFont("Helvetica", max(8, min(int(time_header_height * 0.32), 20)), QFont.Weight.Bold)
        font_clock = QFont("Helvetica", max(9, min(int(time_header_height * 0.4), 24)), QFont.Weight.Bold)
        font_date = QFont("Helvetica", max(6, min(int(time_header_height * 0.18), 11)))

        now_dt = datetime.now()
        current_unix_time = time.time()
        guide_display_start_dt = now_dt.replace(minute=(now_dt.minute // 30) * 30, second=0, microsecond=0)
        guide_display_start_unix = guide_display_start_dt.timestamp()
        time_interval_sec = 30 * 60

        # Rows are drawn first, then the time header is masked and redrawn on
        # top of them — a scrolled-in row's content can extend above
        # top_margin mid-animation, and z-order is draw order, so the header
        # has to be painted last or scrolling rows visually cover the times.
        visible_rows = min(self.GUIDE_ROWS_VISIBLE + 1, num_channels)
        for i in range(visible_rows):
            ch_idx = (self._guide_scroll_top_index + i) % num_channels
            ch_name = RAW_CHANNELS[ch_idx]
            y_start_row = top_margin + i * row_height - self._guide_scroll_pixel_offset

            # Stripe alternates by channel index (not scroll slot i) so each
            # channel keeps a stable color as it scrolls through the window.
            row_bg = GUIDE_ROW_ALT_A if ch_idx % 2 == 0 else GUIDE_ROW_ALT_B
            self.tv_guide_scene.addRect(
                0, y_start_row, canvas_width, row_height, QPen(Qt.PenStyle.NoPen), QBrush(row_bg)
            )
            self.tv_guide_scene.addRect(
                0, y_start_row, channel_name_width, row_height, QPen(Qt.PenStyle.NoPen), QBrush(GUIDE_CHANNEL_COL_BG)
            )

            display_name = f"{ch_idx+1:02d} {CUSTOM_NAMES.get(ch_name, ch_name)}"
            ch_text_item = self.tv_guide_scene.addText(display_name, font_channel)
            ch_text_item.setDefaultTextColor(GUIDE_TEXT_WHITE)
            ch_text_item.setTextWidth(channel_name_width - 10)
            ch_text_item.setPos(
                (channel_name_width - ch_text_item.boundingRect().width()) / 2,
                y_start_row + (row_height - ch_text_item.boundingRect().height()) / 2,
            )
            self.tv_guide_scene.addLine(
                0, y_start_row + row_height, canvas_width, y_start_row + row_height, QPen(GUIDE_GRIDLINE)
            )

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
                    width = max(
                        (left_margin + (overlap_end_unix - guide_display_start_unix) * px_per_sec) - x_start_draw, 4
                    )
                    block_h = row_height - 4
                    is_live = item_abs_start_unix <= current_unix_time < item_abs_end_unix
                    block_gradient = QLinearGradient(0, y_start_row + 2, 0, y_start_row + 2 + block_h)
                    if is_live:
                        block_gradient.setColorAt(0, GUIDE_PROGRAM_LIVE_TOP)
                        block_gradient.setColorAt(1, GUIDE_PROGRAM_LIVE_BOTTOM)
                        block_pen = QPen(GUIDE_ACCENT_GOLD)
                        block_pen.setWidth(2)
                        text_color = QColor("#241a00")
                    else:
                        block_gradient.setColorAt(0, GUIDE_PROGRAM_TOP)
                        block_gradient.setColorAt(1, GUIDE_PROGRAM_BOTTOM)
                        block_pen = QPen(GUIDE_DIVIDER)
                        block_pen.setWidth(1)
                        text_color = GUIDE_TEXT_WHITE
                    block_path = QPainterPath()
                    block_path.addRoundedRect(x_start_draw, y_start_row + 2, width, block_h, 5, 5)
                    self.tv_guide_scene.addPath(block_path, block_pen, QBrush(block_gradient))
                    text = os.path.splitext(item["name"])[0][:40]
                    if width > 30:
                        prog_text_item = self.tv_guide_scene.addText(text, font_regular)
                        prog_text_item.setDefaultTextColor(text_color)
                        prog_text_item.setPos(x_start_draw + 5, y_start_row + 5)

        # Opaque header band, covering any row content that scrolled up past
        # top_margin, then the header content on top of it.
        header_gradient = QLinearGradient(0, 0, 0, time_header_height)
        header_gradient.setColorAt(0, GUIDE_HEADER_BG_TOP)
        header_gradient.setColorAt(1, GUIDE_HEADER_BG_BOTTOM)
        self.tv_guide_scene.addRect(
            0, 0, canvas_width, time_header_height, QPen(Qt.PenStyle.NoPen), QBrush(header_gradient)
        )

        # Corner box: live clock + date, above the channel-name column.
        corner_gradient = QLinearGradient(0, 0, 0, time_header_height)
        corner_gradient.setColorAt(0, GUIDE_CORNER_BG_TOP)
        corner_gradient.setColorAt(1, GUIDE_CORNER_BG_BOTTOM)
        self.tv_guide_scene.addRect(
            0, 0, channel_name_width, time_header_height, QPen(Qt.PenStyle.NoPen), QBrush(corner_gradient)
        )
        clock_text = now_dt.strftime("%I:%M:%S %p").lstrip("0")
        date_text = now_dt.strftime("%A, %B %d")
        clock_item = self.tv_guide_scene.addText(clock_text, font_clock)
        clock_item.setDefaultTextColor(GUIDE_ACCENT_CYAN)
        date_item = self.tv_guide_scene.addText(date_text, font_date)
        date_item.setDefaultTextColor(GUIDE_TEXT_DIM)
        clock_block_h = clock_item.boundingRect().height() + date_item.boundingRect().height()
        top_y = (time_header_height - clock_block_h) / 2
        clock_item.setPos((channel_name_width - clock_item.boundingRect().width()) / 2, top_y)
        date_item.setPos(
            (channel_name_width - date_item.boundingRect().width()) / 2, top_y + clock_item.boundingRect().height()
        )

        for i in range(int(time_window_minutes / 30) + 2):
            marker_unix_time = guide_display_start_unix + (i * time_interval_sec)
            x_pos = left_margin + (marker_unix_time - guide_display_start_unix) * px_per_sec
            if x_pos < canvas_width:
                self.tv_guide_scene.addLine(x_pos, time_header_height, x_pos, canvas_height, QPen(GUIDE_GRIDLINE))
                label_dt = datetime.fromtimestamp(marker_unix_time)
                label_text = label_dt.strftime("%I:%M %p").lstrip("0").replace(" 0", " ")
                text_item = self.tv_guide_scene.addText(label_text, font_time_header)
                text_item.setDefaultTextColor(GUIDE_TEXT_WHITE)
                text_item.setPos(
                    x_pos - text_item.boundingRect().width() / 2,
                    (time_header_height - text_item.boundingRect().height()) / 2,
                )

        # Divider between the channel-name column and the schedule grid.
        divider_pen = QPen(GUIDE_DIVIDER)
        divider_pen.setWidth(2)
        self.tv_guide_scene.addLine(channel_name_width, 0, channel_name_width, canvas_height, divider_pen)

        # Soft glow band behind the current-time column, then a crisp line on top.
        current_x_pos = left_margin + (current_unix_time - guide_display_start_unix) * px_per_sec
        glow_color = QColor(GUIDE_ACCENT_GOLD)
        glow_color.setAlpha(35)
        self.tv_guide_scene.addRect(
            current_x_pos - 4, top_margin, 8, canvas_height - top_margin, QPen(Qt.PenStyle.NoPen), QBrush(glow_color)
        )
        now_pen = QPen(GUIDE_ACCENT_GOLD)
        now_pen.setWidth(2)
        self.tv_guide_scene.addLine(current_x_pos, 0, current_x_pos, canvas_height, now_pen)

    def channel_up(self):
        if self.on_demand_mode: return
        if self.is_changing_channel: return
        self.clear_channel_input()
        num_video_channels = len(RAW_CHANNELS)
        if num_video_channels == 0: return
        current_video_index = self.active_channel_index if self.active_channel != "TV Guide" else num_video_channels
        new_index = (current_video_index - 1 + num_video_channels) % num_video_channels
        self.channel_input_buffer = f"{new_index + 1:02d}"
        self.update_channel_input_display()
        self.channel_input_timer.start(2500)
        self.set_channel_by_index(new_index)

    def channel_down(self):
        if self.on_demand_mode: return
        if self.is_changing_channel: return
        self.clear_channel_input()
        num_video_channels = len(RAW_CHANNELS)
        if num_video_channels == 0: return
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
        QMessageBox.critical(
            None, "Configuration Error",
            f"Config file not found: {CONFIG_PATH}\nPlease run the Media Analysis Tool first."
        )
        sys.exit(1)
    except json.JSONDecodeError:
        QMessageBox.critical(None, "Configuration Error", "Error decoding config.json. Please check its format.")
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
