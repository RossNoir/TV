import sys
import os
import json
from pathlib import Path
import subprocess
import random
from collections import defaultdict
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QLineEdit, QComboBox, QTextEdit, QFileDialog,
                             QMessageBox, QFrame, QListWidget, QListWidgetItem, QSplitter,
                             QCheckBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator

# --- UI UPDATE: NEW MODERN STYLESHEET ---
STYLESHEET = """
QWidget {
    background-color: #18191A; /* Modern dark charcoal background */
    color: #EAEAEA;
    font-family: Segoe UI, sans-serif;
    font-size: 10pt;
    border: none;
}
QPushButton {
    background-color: #242526;
    border: 1px solid #3A3B3C;
    padding: 0px 10px;
    border-radius: 6px; /* Softer corners */
    font-weight: bold;
}
QPushButton:hover {
    background-color: #3A3B3C;
    border-color: #555657;
}
QPushButton:pressed {
    background-color: #9D00FF; /* Electric purple accent */
    border-color: #B34DFF;
}
QPushButton:disabled {
    background-color: #202122;
    color: #5A5A5A;
    border-color: #2A2B2C;
}
QLineEdit, QTextEdit, QComboBox {
    background-color: #242526;
    border: 1px solid #3A3B3C;
    padding: 0px 10px;
    border-radius: 6px;
}
QListWidget {
    background-color: #242526;
    border: 1px solid #3A3B3C;
    border-radius: 6px;
}
QTextEdit {
    font-family: Consolas, monospace;
}
QComboBox::drop-down {
    border: none;
    background-color: #3A3B3C;
    width: 20px;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}
QListWidget::item {
    padding: 1px;
}
QListWidget::item:hover {
    background-color: #3A3B3C;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #9D00FF; /* Electric purple accent */
    color: white;
    border-radius: 4px;
}
QLabel#path_label {
    color: #9A9A9A;
}
QScrollBar:vertical {
    background: #18191A;
    width: 12px;
}
QScrollBar::handle:vertical {
    background: #3A3B3C;
    min-height: 25px;
    border-radius: 6px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QSplitter::handle {
    background-color: #3A3B3C;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
}
QCheckBox::indicator:unchecked {
    background-color: #3A3B3C;
    border-radius: 4px;
}
QCheckBox::indicator:checked {
    background-color: #9D00FF;
    border-radius: 4px;
}
QFrame#marathonFrame {
    border: 1px solid #3A3B3C;
    border-radius: 6px;
}
"""

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_cache.json")

DEFAULT_CONFIG = {
    "channels_path": "",
    "playlists_path": "playlists",
    "scan_modes": {},
    "custom_names": {},
    "block_counts": {},
    "custom_block_counts": {},
    "marathon_settings": {}, # Preserved for individual build logic if needed
    "global_marathon_settings": {"enabled": False, "days": 1, "hours": 0}, # New global setting
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
        command = "where" if os.name == 'nt' else "which"
        subprocess.run([command, "ffprobe"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def get_video_duration(path, log_callback=None):
    if not is_ffprobe_installed():
        if log_callback:
            log_callback("Error: ffprobe is required. Please install FFmpeg.")
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
            log_callback(f"Error running ffprobe for {Path(path).name}: {e.stdout.strip() if e.stdout else 'No output'}")
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

# --- FEATURE UPDATE: All builder functions now accept an optional target duration ---
def build_playlist(channel_number, channel_name, media_items, mode, playlist_path, channels_base_path, target_duration_seconds=None):
    if not media_items: return
    
    playlist = []
    bumper_path = (Path(channels_base_path) / channel_name / BUMPER_FILENAME).resolve()
    has_bumper = bumper_path.exists()
    bumper_duration = get_video_duration(bumper_path, lambda msg: print(f"Bumper error: {msg}")) if has_bumper else 0
    current_playlist_duration = 0

    is_marathon = target_duration_seconds is not None and target_duration_seconds > 0
    
    # Create a reusable pool of items
    item_pool = list(media_items)
    if mode == "random":
        random.shuffle(item_pool)
    else:
        item_pool.sort(key=lambda x: x["name"].lower())

    original_pool = list(item_pool) # A clean copy to refill from

    while True:
        if not item_pool:
            if not is_marathon: break # If not marathon, stop after one run
            item_pool = list(original_pool) # Refill pool for marathon

        item = item_pool.pop(0)

        if has_bumper:
            playlist.append({"name": BUMPER_FILENAME, "path": str(bumper_path), "duration": int(bumper_duration), "start": current_playlist_duration})
            current_playlist_duration += int(bumper_duration)
        
        playlist.append({"name": item["name"], "path": item["path"], "duration": int(item["duration"]), "start": current_playlist_duration})
        current_playlist_duration += int(item["duration"])

        if is_marathon and current_playlist_duration >= target_duration_seconds:
            break
            
    Path(playlist_path).mkdir(parents=True, exist_ok=True)
    playlist_filename = f"{channel_number:02d}_{channel_name}_playlist.json"
    with open(Path(playlist_path) / playlist_filename, "w") as f:
        json.dump(playlist, f, indent=2)

def build_playlist_blocks(channel_number, channel_name, media_items, mode, block_size, playlist_path, channels_base_path, target_duration_seconds=None):
    if block_size == 0:
        build_playlist(channel_number, channel_name, media_items, mode, playlist_path, channels_base_path, target_duration_seconds) 
        return

    show_groups = get_show_groups(media_items, channels_base_path, channel_name)
    if not show_groups: return

    show_keys = list(show_groups.keys())
    if mode == "random":
        random.shuffle(show_keys)
    else:
        show_keys.sort()

    playlist = []
    bumper_path = (Path(channels_base_path) / channel_name / BUMPER_FILENAME).resolve()
    has_bumper = bumper_path.exists()
    bumper_duration = get_video_duration(bumper_path, lambda msg: print(f"Bumper error: {msg}")) if has_bumper else 0
    current_playlist_duration = 0
    
    is_marathon = target_duration_seconds is not None and target_duration_seconds > 0
    
    # Use index map to track progress through each show's episodes
    index_map = {show: 0 for show in show_keys}

    running = True
    while running:
        for show in show_keys:
            start_idx = index_map[show]
            
            # Check if we need to refill this show's episodes
            if start_idx >= len(show_groups[show]):
                if not is_marathon: continue # Don't refill if not marathon
                start_idx = 0 # Reset for marathon
            
            end_idx = min(start_idx + block_size, len(show_groups[show]))
            
            for item in show_groups[show][start_idx:end_idx]:
                if has_bumper:
                    playlist.append({"name": BUMPER_FILENAME, "path": str(bumper_path), "duration": int(bumper_duration), "start": current_playlist_duration})
                    current_playlist_duration += int(bumper_duration)
                
                playlist.append({"name": item["name"], "path": item["path"], "duration": int(item["duration"]), "start": current_playlist_duration})
                current_playlist_duration += int(item["duration"])

                if is_marathon and current_playlist_duration >= target_duration_seconds:
                    running = False
                    break
            
            index_map[show] = end_idx
            if not running: break
        
        # If not marathon, stop if all shows have been processed once
        if not is_marathon and all(index_map[s] >= len(show_groups[s]) for s in show_keys):
            running = False

    Path(playlist_path).mkdir(parents=True, exist_ok=True)
    playlist_filename = f"{channel_number:02d}_{channel_name}_playlist.json"
    with open(Path(playlist_path) / playlist_filename, "w") as f:
        json.dump(playlist, f, indent=2)

def build_playlist_custom_blocks(channel_number, channel_name, media_items, mode, custom_counts, playlist_path, channels_base_path, target_duration_seconds=None):
    show_groups = get_show_groups(media_items, channels_base_path, channel_name)
    if not show_groups: return

    show_keys = sorted(list(show_groups.keys()))
    if mode == "random":
        random.shuffle(show_keys)

    show_pools = {}
    for show, episodes in show_groups.items():
        episodes_copy = sorted(episodes, key=lambda x: x["name"].lower())
        if mode == "random":
            random.shuffle(episodes_copy)
        show_pools[show] = episodes_copy

    playlist = []
    bumper_path = (Path(channels_base_path) / channel_name / BUMPER_FILENAME).resolve()
    has_bumper = bumper_path.exists()
    bumper_duration = get_video_duration(bumper_path, lambda msg: print(f"Bumper error: {msg}")) if has_bumper else 0
    current_playlist_duration = 0

    is_marathon = target_duration_seconds is not None and target_duration_seconds > 0
    
    running = True
    while running:
        for show in show_keys:
            block_size = custom_counts.get(show, 1)
            for _ in range(block_size):
                if not show_pools[show]:
                    if not is_marathon: break # Stop if not marathon and pool is empty
                    refilled_episodes = sorted(show_groups[show], key=lambda x: x["name"].lower())
                    if mode == "random":
                        random.shuffle(refilled_episodes)
                    show_pools[show] = refilled_episodes
                
                if not show_pools[show]: continue
                
                episode = show_pools[show].pop(0)

                if has_bumper:
                    playlist.append({ "name": BUMPER_FILENAME, "path": str(bumper_path), "duration": int(bumper_duration), "start": current_playlist_duration })
                    current_playlist_duration += int(bumper_duration)

                playlist.append({ "name": episode["name"], "path": episode["path"], "duration": int(episode["duration"]), "start": current_playlist_duration })
                current_playlist_duration += int(episode["duration"])

                if is_marathon and current_playlist_duration >= target_duration_seconds:
                    running = False
                    break
            if not running:
                break
        
        if not is_marathon:
            running = False
            
    Path(playlist_path).mkdir(parents=True, exist_ok=True)
    playlist_filename = f"{channel_number:02d}_{channel_name}_playlist.json"
    with open(Path(playlist_path) / playlist_filename, "w") as f:
        json.dump(playlist, f, indent=2)

class ChannelListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)

class MediaAnalysisApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Analysis & Playlist Builder for Direct Sound Radio")
        self.setGeometry(100, 100, 1200, 700)
        
        self.setStyleSheet(STYLESHEET)

        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                self.config = json.load(f)
            for key, default_value in DEFAULT_CONFIG.items():
                self.config.setdefault(key, default_value)
        else:
            self.config = DEFAULT_CONFIG.copy()

        self.media_cache = load_cache()
        self.channel_widgets = {}

        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0,0,0,0)
        left_layout.setSpacing(10)

        self.path_label = QLabel(f"Channels Folder: {self.config.get('channels_path', 'Not selected')}")
        self.path_label.setObjectName("path_label")
        left_layout.addWidget(self.path_label)

        # --- FEATURE UPDATE: Global Marathon Control Panel ---
        marathon_frame = QFrame()
        marathon_frame.setObjectName("marathonFrame")
        marathon_frame_layout = QHBoxLayout(marathon_frame)
        marathon_frame_layout.setContentsMargins(10, 5, 10, 5)

        self.global_marathon_checkbox = QCheckBox("Marathon All Channels")
        marathon_frame_layout.addWidget(self.global_marathon_checkbox)
        marathon_frame_layout.addStretch(1)

        self.global_days_label = QLabel("Days:")
        marathon_frame_layout.addWidget(self.global_days_label)
        self.global_days_input = QLineEdit()
        self.global_days_input.setValidator(QIntValidator(0, 365))
        self.global_days_input.setFixedWidth(40)
        marathon_frame_layout.addWidget(self.global_days_input)

        self.global_hours_label = QLabel("Hours:")
        marathon_frame_layout.addWidget(self.global_hours_label)
        self.global_hours_input = QLineEdit()
        self.global_hours_input.setValidator(QIntValidator(0, 23))
        self.global_hours_input.setFixedWidth(40)
        marathon_frame_layout.addWidget(self.global_hours_input)

        self.global_marathon_checkbox.toggled.connect(self.toggle_global_marathon_inputs)

        saved_global_marathon = self.config.get("global_marathon_settings", {"enabled": False, "days": 1, "hours": 0})
        self.global_marathon_checkbox.setChecked(saved_global_marathon["enabled"])
        self.global_days_input.setText(str(saved_global_marathon["days"]))
        self.global_hours_input.setText(str(saved_global_marathon["hours"]))
        self.toggle_global_marathon_inputs(saved_global_marathon["enabled"])

        left_layout.addWidget(marathon_frame)

        self.channel_list = ChannelListWidget()
        left_layout.addWidget(self.channel_list)

        bottom_button_layout = QHBoxLayout()
        self.select_button = QPushButton("Select Channel Folder")
        self.select_button.clicked.connect(self.select_folder)
        bottom_button_layout.addWidget(self.select_button)

        self.playlists_button = QPushButton("Select Playlists Folder")
        self.playlists_button.clicked.connect(self.select_playlists_folder)
        bottom_button_layout.addWidget(self.playlists_button)
        
        self.scan_button = QPushButton("Scan Media Library")
        self.scan_button.clicked.connect(self.scan_media_library_only)
        self.scan_button.setEnabled(False)
        bottom_button_layout.addWidget(self.scan_button)

        self.build_all_button = QPushButton("Build All Playlists")
        self.build_all_button.clicked.connect(self.build_all_playlists)
        self.build_all_button.setEnabled(False)
        bottom_button_layout.addWidget(self.build_all_button)
        
        bottom_button_layout.addStretch(1)

        self.toggle_log_button = QPushButton("Toggle Log")
        self.toggle_log_button.clicked.connect(self.toggle_log_panel)
        bottom_button_layout.addWidget(self.toggle_log_button)

        left_layout.addLayout(bottom_button_layout)

        self.log_panel = QWidget()
        right_layout = QVBoxLayout(self.log_panel)
        right_layout.setContentsMargins(0,0,0,0)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        right_layout.addWidget(self.log_box)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.log_panel)
        splitter.setSizes([700, 500])

        main_layout.addWidget(splitter)

        if self.config["channels_path"] and Path(self.config["channels_path"]).is_dir():
            self.load_channel_folders()
        else:
            self.log("Please select a 'Channels Folder' to begin.")
            
        if not is_ffprobe_installed():
            self.log("\nWARNING: ffprobe not found. Please install FFmpeg.\n")

    def toggle_global_marathon_inputs(self, checked):
        self.global_days_input.setEnabled(checked)
        self.global_hours_input.setEnabled(checked)
        self.global_days_label.setEnabled(checked)
        self.global_hours_label.setEnabled(checked)

    def toggle_log_panel(self):
        if self.log_panel.isVisible():
            self.log_panel.hide()
        else:
            self.log_panel.show()

    def log(self, message):
        self.log_box.append(message)
        QApplication.processEvents()

    def select_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "Select Folder Containing Channel Subfolders")
        if not selected:
            return
        self.config["channels_path"] = selected
        self.config["channel_order"] = []
        self.load_channel_folders()
        self.save_config()

    def select_playlists_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "Select Folder to Save Playlists")
        if selected:
            self.config["playlists_path"] = selected
            self.log(f"Playlists folder set to: {selected}")
            self.save_config()

    def load_channel_folders(self):
        selected = self.config["channels_path"]
        self.channel_list.clear()
        self.channel_widgets.clear()
        
        self.path_label.setText(f"Channels Folder: {selected}")

        all_dirs = {d.name for d in Path(selected).iterdir() if d.is_dir()}
        ordered_channels = [ch for ch in self.config.get("channel_order", []) if ch in all_dirs]
        new_channels = sorted(list(all_dirs - set(ordered_channels)))
        final_channel_list = ordered_channels + new_channels
        self.config["channel_order"] = final_channel_list

        if not final_channel_list:
            self.log("No channel subfolders found.")
            return

        for i, item in enumerate(final_channel_list):
            self._create_channel_row_ui(item)
        
        self.scan_button.setEnabled(True)
        self.build_all_button.setEnabled(True)
        self.log(f"{len(final_channel_list)} channel(s) loaded.")
        self.save_config()

    def _create_channel_row_ui(self, channel_name):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, channel_name)
        
        item_widget = QWidget()
        item_layout = QVBoxLayout()
        item_layout.setContentsMargins(5, 5, 5, 5)
        item_layout.setSpacing(6)
        
        main_row = QHBoxLayout()
        
        num_label = QLabel(f"{self.channel_list.count() + 1:02d}")
        num_label.setFixedWidth(25)
        main_row.addWidget(num_label)
        
        entry = QLineEdit(self.config.get("custom_names", {}).get(channel_name, channel_name))
        entry.setFixedHeight(32)
        entry.setPlaceholderText("Enter Custom Channel Name")
        main_row.addWidget(entry, 1)

        block_dropdown = QComboBox()
        block_dropdown.setFixedHeight(32)
        block_values = ["None", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "Custom"]
        block_dropdown.addItems(block_values)
        saved_block = self.config["block_counts"].get(channel_name, "None")
        block_dropdown.setCurrentText(saved_block)
        block_dropdown.currentTextChanged.connect(lambda text, ch=channel_name: self._toggle_special_views(ch))
        main_row.addWidget(block_dropdown)

        dropdown = QComboBox()
        dropdown.setFixedHeight(32)
        dropdown.addItems(["alphabetical", "random"])
        saved_mode = self.config["scan_modes"].get(channel_name, "alphabetical")
        dropdown.setCurrentText(saved_mode)
        main_row.addWidget(dropdown)

        build_button = QPushButton("Build")
        build_button.clicked.connect(lambda _, ch=channel_name: self.build_single_playlist(ch))
        main_row.addWidget(build_button)
        
        item_layout.addLayout(main_row)

        custom_frame = QFrame()
        custom_frame.setFrameShape(QFrame.Shape.StyledPanel)
        custom_frame_layout = QVBoxLayout(custom_frame)
        item_layout.addWidget(custom_frame)
        
        item_widget.setLayout(item_layout)

        self.channel_list.addItem(item)
        self.channel_list.setItemWidget(item, item_widget)
        item.setSizeHint(item_widget.sizeHint())
        
        self.channel_widgets[channel_name] = {
            "item": item, "num_label": num_label, "name_entry": entry, "block_dropdown": block_dropdown,
            "mode_dropdown": dropdown, "build_button": build_button,
            "custom_frame": custom_frame, "custom_widgets": {}
        }
        
        self._toggle_special_views(channel_name, initial_load=True)
        item.setSizeHint(item_layout.sizeHint())

    def _toggle_special_views(self, channel_name, initial_load=False):
        widgets = self.channel_widgets.get(channel_name)
        if not widgets: return
        
        block_mode = widgets["block_dropdown"].currentText()
        custom_frame = widgets["custom_frame"]

        if block_mode == "Custom":
            custom_frame.show()
            if custom_frame.layout().count() == 0:
                channel_path = Path(self.config["channels_path"]) / channel_name
                if not channel_path.is_dir():
                     QLabel("Channel folder not found.", custom_frame.layout())
                else:
                    show_dirs = sorted([d.name for d in channel_path.iterdir() if d.is_dir()])
                    if not show_dirs:
                        label = QLabel("No show sub-folders found.")
                        custom_frame.layout().addWidget(label)
                    else:
                        for show_name in show_dirs:
                            show_layout = QHBoxLayout()
                            show_layout.addWidget(QLabel(show_name))
                            show_count_dd = QComboBox()
                            show_count_dd.addItems([str(i) for i in range(1, 6)])
                            saved_count = self.config.get("custom_block_counts", {}).get(channel_name, {}).get(show_name, 1)
                            show_count_dd.setCurrentText(str(saved_count))
                            show_layout.addWidget(show_count_dd)
                            custom_frame.layout().addLayout(show_layout)
                            widgets["custom_widgets"][show_name] = show_count_dd
        else:
            custom_frame.hide()

        if not initial_load:
            self.save_config()
            
        widgets["item"].setSizeHint(widgets["item"].listWidget().itemWidget(widgets["item"]).sizeHint())

    def save_channel_order(self):
        ordered_channel_names = [self.channel_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.channel_list.count())]
        self.config["channel_order"] = ordered_channel_names
        return ordered_channel_names

    def update_channel_numbers(self):
        for i in range(self.channel_list.count()):
            item = self.channel_list.item(i)
            channel_name = item.data(Qt.ItemDataRole.UserRole)
            if channel_name in self.channel_widgets:
                self.channel_widgets[channel_name]["num_label"].setText(f"{i+1:02d}")

    def save_config(self):
        ordered_channels = self.save_channel_order()
        
        self.config["custom_block_counts"] = {}
        
        # Save global marathon settings
        self.config["global_marathon_settings"] = {
            "enabled": self.global_marathon_checkbox.isChecked(),
            "days": int(self.global_days_input.text() or 0),
            "hours": int(self.global_hours_input.text() or 0)
        }
        
        for ch_name in ordered_channels:
            if ch_name in self.channel_widgets:
                widgets = self.channel_widgets[ch_name]
                self.config["scan_modes"][ch_name] = widgets["mode_dropdown"].currentText()
                self.config["custom_names"][ch_name] = widgets["name_entry"].text()
                self.config["block_counts"][ch_name] = widgets["block_dropdown"].currentText()
                
                if widgets["block_dropdown"].currentText() == "Custom":
                    if ch_name not in self.config["custom_block_counts"]:
                        self.config["custom_block_counts"][ch_name] = {}
                    for show_name, dropdown in widgets["custom_widgets"].items():
                        self.config["custom_block_counts"][ch_name][show_name] = int(dropdown.currentText())

        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Config", f"Could not save configuration: {e}")
        
        self.log("Channel order and settings saved.")
        self.update_channel_numbers()

    def scan_media_library_only(self):
        self.save_config()
        self.log("Scanning media library...")
        self.toggle_ui_state(False)

        try:
            base_path = Path(self.config["channels_path"])
            for channel_name in self.config["channel_order"]:
                full_path = base_path / channel_name
                self.log(f"  Analyzing: {self.config['custom_names'].get(channel_name, channel_name)}")
                analyze_media(str(full_path), self.media_cache, self.log)
            
            save_cache(self.media_cache)
            self.log("Media library scan complete. Cache updated.")
        except Exception as e:
            self.log(f"An error occurred during scan: {e}")
        finally:
            self.toggle_ui_state(True)

    def build_single_playlist(self, channel_name):
        self.save_config()
        self.log(f"Building playlist for channel: {self.config['custom_names'].get(channel_name, channel_name)}...")
        self.toggle_ui_state(False)

        try:
            channel_number = self.config["channel_order"].index(channel_name) + 1
            # Single builds are never marathons
            self.process_playlist_build(channel_number, channel_name, target_duration_seconds=None)
            self.log(f"Playlist built for Ch. {channel_number:02d} - {self.config['custom_names'].get(channel_name, channel_name)}.")
        except Exception as e:
            self.log(f"An error occurred building playlist for {channel_name}: {e}")
            QMessageBox.critical(self, "Playlist Build Error", f"An error occurred: {e}")
        finally:
            self.toggle_ui_state(True)

    def build_all_playlists(self):
        self.save_config()
        self.log("Building playlists for all channels...")
        self.toggle_ui_state(False)

        # --- FEATURE UPDATE: Check for global marathon mode ---
        target_duration = None
        if self.global_marathon_checkbox.isChecked():
            days = int(self.global_days_input.text() or 0)
            hours = int(self.global_hours_input.text() or 0)
            if days > 0 or hours > 0:
                target_duration = (days * 86400) + (hours * 3600)
                self.log(f"--- MARATHON MODE ENABLED: Building all playlists for {days} days, {hours} hours ---")

        try:
            for i, channel_name in enumerate(self.config["channel_order"]):
                channel_number = i + 1
                self.log(f"  Building Ch. {channel_number:02d}: {self.config['custom_names'].get(channel_name, channel_name)}")
                self.process_playlist_build(channel_number, channel_name, target_duration_seconds=target_duration)
            
            save_cache(self.media_cache)
            self.log("All playlists built. Cache updated.")
        except Exception as e:
            self.log(f"An error occurred building all playlists: {e}")
            QMessageBox.critical(self, "Playlist Build Error", f"An error occurred: {e}")
        finally:
            self.toggle_ui_state(True)
            
    def process_playlist_build(self, channel_number, channel_name, target_duration_seconds=None):
        base_path = Path(self.config["channels_path"])
        playlists_path = Path(self.config["playlists_path"])
        full_path = base_path / channel_name
        scan_mode = self.config["scan_modes"].get(channel_name, "alphabetical")
        
        channel_media_items = analyze_media(str(full_path), self.media_cache, self.log)
        
        block_mode = self.config.get('block_counts', {}).get(channel_name, 'None')

        if block_mode == "Custom":
            custom_counts = self.config.get('custom_block_counts', {}).get(channel_name, {})
            build_playlist_custom_blocks(channel_number, channel_name, channel_media_items, scan_mode, custom_counts, playlists_path, base_path, target_duration_seconds)
        else:
            block_size = int(block_mode) if str(block_mode).isdigit() else 0
            if block_size > 0:
                build_playlist_blocks(channel_number, channel_name, channel_media_items, scan_mode, block_size, playlists_path, base_path, target_duration_seconds)
            else:
                build_playlist(channel_number, channel_name, channel_media_items, scan_mode, playlists_path, base_path, target_duration_seconds)

    def toggle_ui_state(self, enabled):
        self.scan_button.setEnabled(enabled)
        self.build_all_button.setEnabled(enabled)
        self.select_button.setEnabled(enabled)
        self.playlists_button.setEnabled(enabled)
        for ch_name, widgets in self.channel_widgets.items():
            widgets["build_button"].setEnabled(enabled)
            widgets["block_dropdown"].setEnabled(enabled)
            widgets["mode_dropdown"].setEnabled(enabled)
        QApplication.processEvents()
        
    def closeEvent(self, event):
        self.save_config()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = MediaAnalysisApp()
    ex.show()
    sys.exit(app.exec())
