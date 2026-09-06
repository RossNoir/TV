import sys
import os
import json
from pathlib import Path
import subprocess
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
    QLabel, QLineEdit, QComboBox, QTextEdit, QFileDialog, QMessageBox, QFrame,
    QListWidget, QListWidgetItem, QSplitter, QCheckBox, QSpinBox, QStackedWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator

# --- STYLESHEET ---
STYLESHEET = """
QWidget {
    background-color: #18191A;
    color: #EAEAEA;
    font-family: Segoe UI, sans-serif;
    font-size: 10pt;
    border: none;
}
QPushButton {
    background-color: #242526;
    border: 1px solid #3A3B3C;
    padding: 4px 10px;
    border-radius: 6px;
    font-weight: bold;
}
QPushButton:hover { background-color: #3A3B3C; border-color: #555657; }
QPushButton:pressed { background-color: #9D00FF; border-color: #B34DFF; }
QPushButton:disabled { background-color: #202122; color: #5A5A5A; border-color: #2A2B2C; }
QPushButton#primary { background-color: #6A00CC; border-color: #9D00FF; }
QPushButton#primary:hover { background-color: #7E00F0; }
QLineEdit, QTextEdit, QComboBox, QSpinBox {
    background-color: #242526;
    border: 1px solid #3A3B3C;
    padding: 4px 8px;
    border-radius: 6px;
}
QListWidget {
    background-color: #242526;
    border: 1px solid #3A3B3C;
    border-radius: 6px;
}
QTextEdit { font-family: Consolas, monospace; }
QComboBox::drop-down { border: none; background-color: #3A3B3C; width: 20px; border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QListWidget::item { padding: 5px; }
QListWidget::item:hover { background-color: #3A3B3C; border-radius: 4px; }
QListWidget::item:selected { background-color: #9D00FF; color: white; border-radius: 4px; }
QLabel#path_label { color: #9A9A9A; }
QLabel#section_header { color: #C9A0FF; font-weight: bold; font-size: 11pt; }
QLabel#placeholder { color: #6A6A6A; font-size: 12pt; }
QLabel#bumper_status { color: #9A9A9A; font-size: 9pt; }
QScrollBar:vertical { background: #18191A; width: 12px; }
QScrollBar::handle:vertical { background: #3A3B3C; min-height: 25px; border-radius: 6px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QSplitter::handle { background-color: #3A3B3C; }
QSplitter::handle:horizontal { width: 2px; }
QCheckBox::indicator { width: 18px; height: 18px; }
QCheckBox::indicator:unchecked { background-color: #3A3B3C; border-radius: 4px; }
QCheckBox::indicator:checked { background-color: #9D00FF; border-radius: 4px; }
QFrame#marathonFrame { border: 1px solid #3A3B3C; border-radius: 6px; padding: 6px; }
QFrame#detailForm { border: none; }
"""

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_cache.json")

DEFAULT_CONFIG = {
    "playlists_path": "playlists",
    "scan_modes": {},
    "custom_names": {},
    "block_counts": {},
    "custom_block_counts": {},       # legacy: channel -> {show: count}, migrated on load
    "custom_block_sequence": {},     # channel -> [{"show": name, "count": n}, ...] ordered
    "marathon_settings": {},
    "global_marathon_settings": {"enabled": False, "days": 1, "hours": 0},
    "channel_order": []
}

VIDEO_EXTENSIONS = [".mp4", ".avi", ".mkv", ".mov"]
BUMPER_FILENAME = "bumper.mp4"
PROBE_WORKERS = 4


# ---------------------------------------------------------------------------
# Source / volume helpers
# ---------------------------------------------------------------------------

def get_volume_serial(path):
    """Best-effort drive volume serial for `path`, as an 8-hex-digit string,
    or None if it can't be determined (non-Windows, network path, drive not
    mounted, etc). Used to warn when a drive letter has been reassigned to a
    different physical disk rather than silently scanning nothing."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return None
        root = drive + "\\"
        vol_name_buf = ctypes.create_unicode_buffer(261)
        fs_name_buf = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_uint(0)
        max_component_len = ctypes.c_uint(0)
        fs_flags = ctypes.c_uint(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), vol_name_buf, ctypes.sizeof(vol_name_buf),
            ctypes.byref(serial), ctypes.byref(max_component_len), ctypes.byref(fs_flags),
            fs_name_buf, ctypes.sizeof(fs_name_buf)
        )
        if not ok:
            return None
        return f"{serial.value:08X}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cache / ffprobe helpers
# ---------------------------------------------------------------------------

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
            log_callback(f"Could not parse ffprobe output for {Path(path).name}")
        return 0.0
    except Exception as e:
        if log_callback:
            log_callback(f"An unexpected error occurred for {Path(path).name}: {e}")
        return 0.0


def analyze_media(channel_path, media_cache, log_callback):
    """Walks a channel folder, using the cache where possible and probing
    cache-misses in parallel (ffprobe is I/O bound, so threads help a lot on
    the Pi's limited cores instead of probing one file at a time)."""
    entries = []
    for root, dirs, files in os.walk(channel_path):
        for file in sorted(files):
            if file.lower() == BUMPER_FILENAME.lower():
                continue
            if not any(file.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                continue
            full_path = Path(root) / file
            abs_path_str = str(full_path.resolve())
            entries.append((file, full_path, abs_path_str))

    durations = {}
    to_probe = []
    for file, full_path, abs_path_str in entries:
        file_mtime = os.path.getmtime(full_path)
        cached_data = media_cache.get(abs_path_str)
        if cached_data and cached_data.get('mtime') == file_mtime:
            durations[abs_path_str] = cached_data.get('duration', 0.0)
            log_callback(f"  Cached: {file} ({int(durations[abs_path_str])}s)")
        else:
            to_probe.append((file, full_path, abs_path_str, file_mtime))

    if to_probe:
        with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as executor:
            future_map = {
                executor.submit(get_video_duration, full_path): (file, abs_path_str, file_mtime)
                for file, full_path, abs_path_str, file_mtime in to_probe
            }
            for future in as_completed(future_map):
                file, abs_path_str, file_mtime = future_map[future]
                duration = future.result()
                media_cache[abs_path_str] = {'duration': duration, 'mtime': file_mtime}
                durations[abs_path_str] = duration
                log_callback(f"  Analyzed: {file} ({int(duration)}s)")

    return [{"name": file, "path": abs_path_str, "duration": durations.get(abs_path_str, 0.0)}
            for file, _full_path, abs_path_str in entries]


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


def list_show_folders(channels_base_path, channel_name):
    channel_path = Path(channels_base_path) / channel_name
    if not channel_path.is_dir():
        return []
    return sorted([d.name for d in channel_path.iterdir() if d.is_dir()])


# ---------------------------------------------------------------------------
# Playlist builders
# ---------------------------------------------------------------------------

def build_playlist(channel_number, channel_name, media_items, mode, playlist_path, channels_base_path, target_duration_seconds=None):
    if not media_items:
        return

    playlist = []
    bumper_path = (Path(channels_base_path) / channel_name / BUMPER_FILENAME).resolve()
    has_bumper = bumper_path.exists()
    bumper_duration = get_video_duration(bumper_path, lambda msg: print(f"Bumper error: {msg}")) if has_bumper else 0
    current_playlist_duration = 0

    is_marathon = target_duration_seconds is not None and target_duration_seconds > 0

    item_pool = list(media_items)
    if mode == "random":
        random.shuffle(item_pool)
    else:
        item_pool.sort(key=lambda x: x["name"].lower())

    original_pool = list(item_pool)

    while True:
        if not item_pool:
            if not is_marathon:
                break
            item_pool = list(original_pool)

        item = item_pool.pop(0)

        if has_bumper:
            playlist.append({"name": BUMPER_FILENAME, "path": str(bumper_path), "duration": int(bumper_duration), "start": current_playlist_duration})
            current_playlist_duration += int(bumper_duration)

        playlist.append({"name": item["name"], "path": item["path"], "duration": int(item["duration"]), "start": current_playlist_duration})
        current_playlist_duration += int(item["duration"])

        if is_marathon and current_playlist_duration >= target_duration_seconds:
            break

    _write_playlist(playlist, channel_number, channel_name, playlist_path)


def build_playlist_blocks(channel_number, channel_name, media_items, mode, block_size, playlist_path, channels_base_path, target_duration_seconds=None):
    if block_size == 0:
        build_playlist(channel_number, channel_name, media_items, mode, playlist_path, channels_base_path, target_duration_seconds)
        return

    show_groups = get_show_groups(media_items, channels_base_path, channel_name)
    if not show_groups:
        return

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

    index_map = {show: 0 for show in show_keys}

    running = True
    while running:
        for show in show_keys:
            start_idx = index_map[show]

            if start_idx >= len(show_groups[show]):
                if not is_marathon:
                    continue
                start_idx = 0

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
            if not running:
                break

        if not is_marathon and all(index_map[s] >= len(show_groups[s]) for s in show_keys):
            running = False

    _write_playlist(playlist, channel_number, channel_name, playlist_path)


def build_playlist_custom_blocks(channel_number, channel_name, media_items, mode, sequence, playlist_path, channels_base_path, target_duration_seconds=None, log_callback=None):
    """sequence is an ORDERED list of {"show": name, "count": n} dicts, as
    arranged by the user in the Programming Order editor. Shows not present
    in the sequence are not scheduled."""
    show_groups = get_show_groups(media_items, channels_base_path, channel_name)
    if not show_groups:
        return

    if not sequence:
        if log_callback:
            log_callback(f"  No programming order defined for {channel_name}; nothing to build.")
        return

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
        for entry in sequence:
            show = entry.get("show")
            block_size = int(entry.get("count", 1))
            if show not in show_pools:
                continue

            for _ in range(block_size):
                if not show_pools[show]:
                    if not is_marathon:
                        break
                    refilled = sorted(show_groups[show], key=lambda x: x["name"].lower())
                    if mode == "random":
                        random.shuffle(refilled)
                    show_pools[show] = refilled

                if not show_pools[show]:
                    continue

                episode = show_pools[show].pop(0)

                if has_bumper:
                    playlist.append({"name": BUMPER_FILENAME, "path": str(bumper_path), "duration": int(bumper_duration), "start": current_playlist_duration})
                    current_playlist_duration += int(bumper_duration)

                playlist.append({"name": episode["name"], "path": episode["path"], "duration": int(episode["duration"]), "start": current_playlist_duration})
                current_playlist_duration += int(episode["duration"])

                if is_marathon and current_playlist_duration >= target_duration_seconds:
                    running = False
                    break
            if not running:
                break

        if not is_marathon:
            running = False

    _write_playlist(playlist, channel_number, channel_name, playlist_path)


def _write_playlist(playlist, channel_number, channel_name, playlist_path):
    Path(playlist_path).mkdir(parents=True, exist_ok=True)
    playlist_filename = f"{channel_number:02d}_{channel_name}_playlist.json"
    with open(Path(playlist_path) / playlist_filename, "w") as f:
        json.dump(playlist, f, indent=2)


# ---------------------------------------------------------------------------
# List widgets (both are plain-text items on purpose: no embedded child
# widgets means Qt's native drag-and-drop works everywhere on the row,
# instead of only in the leftover padding around form controls.)
# ---------------------------------------------------------------------------

class ChannelListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)


class SequenceListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class MediaAnalysisApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Analysis & Playlist Builder for Direct Sound Radio")
        self.setGeometry(100, 100, 1400, 760)
        self.setStyleSheet(STYLESHEET)

        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                self.config = json.load(f)
            for key, default_value in DEFAULT_CONFIG.items():
                self.config.setdefault(key, default_value)
        else:
            self.config = DEFAULT_CONFIG.copy()
        self.config.setdefault("sources", [])

        self._pending_log = []
        self._migrate_legacy_channels_path()
        self._migrate_legacy_custom_counts()

        self.media_cache = load_cache()
        self.current_channel = None
        self.channel_sources = {}  # channel_name -> source root path, rebuilt on every scan
        self._log_buffer = []

        self.init_ui()

    # -- migration -----------------------------------------------------

    def _migrate_legacy_channels_path(self):
        """Configs from before multi-drive support stored a single
        'channels_path' string. Fold it into the new 'sources' list the
        first time we see it, so nobody has to re-add their drive."""
        legacy_path = self.config.pop("channels_path", None)
        if not legacy_path:
            return
        sources = self.config.setdefault("sources", [])
        if any(s.get("path") == legacy_path for s in sources):
            return
        sources.append({"path": legacy_path, "volume_serial": get_volume_serial(legacy_path)})
        self._pending_log.append(f"Migrated existing Channels Folder into Sources: {legacy_path}")

    def _migrate_legacy_custom_counts(self):
        """Older configs stored an unordered {show: count} dict. Convert it
        to an ordered sequence (alphabetical, as it effectively behaved
        before) the first time we see it, so nobody's existing setup breaks."""
        legacy = self.config.get("custom_block_counts", {})
        sequence_map = self.config.setdefault("custom_block_sequence", {})
        for channel_name, counts in legacy.items():
            if channel_name in sequence_map:
                continue
            if not counts:
                continue
            sequence_map[channel_name] = [
                {"show": show, "count": int(count)} for show, count in sorted(counts.items())
            ]

    def get_sequence(self, channel_name):
        return self.config.setdefault("custom_block_sequence", {}).setdefault(channel_name, [])

    # -- UI construction -------------------------------------------------

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_channel_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.addWidget(self._build_log_panel())
        splitter.setSizes([380, 620, 400])
        main_layout.addWidget(splitter)

        for msg in self._pending_log:
            self.log(msg)
        self._pending_log.clear()

        if self.config.get("sources"):
            self.load_channel_folders()
        else:
            self.log("Please add at least one Source folder to begin.")

        if not is_ffprobe_installed():
            self.log("\nWARNING: ffprobe not found. Please install FFmpeg.\n")

    # -- left panel: channel list ----------------------------------------

    def _build_channel_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        sources_header = QLabel("Sources — drive/folder roots to scan for channels")
        sources_header.setObjectName("path_label")
        sources_header.setWordWrap(True)
        layout.addWidget(sources_header)

        self.source_list = QListWidget()
        self.source_list.setFixedHeight(70)
        layout.addWidget(self.source_list)

        source_button_row = QHBoxLayout()
        self.add_source_button = QPushButton("Add Source")
        self.add_source_button.clicked.connect(self.add_source)
        source_button_row.addWidget(self.add_source_button)

        self.remove_source_button = QPushButton("Remove Source")
        self.remove_source_button.clicked.connect(self.remove_source)
        source_button_row.addWidget(self.remove_source_button)
        layout.addLayout(source_button_row)

        layout.addWidget(self._build_marathon_frame())

        header = QLabel("Channels — drag to reorder")
        header.setObjectName("section_header")
        layout.addWidget(header)

        self.channel_list = ChannelListWidget()
        self.channel_list.itemSelectionChanged.connect(self.on_channel_selection_changed)
        self.channel_list.model().rowsMoved.connect(self.on_channels_reordered)
        layout.addWidget(self.channel_list, 1)

        button_row = QHBoxLayout()
        self.playlists_button = QPushButton("Select Playlists Folder")
        self.playlists_button.clicked.connect(self.select_playlists_folder)
        button_row.addWidget(self.playlists_button)
        layout.addLayout(button_row)

        button_row2 = QHBoxLayout()
        self.scan_button = QPushButton("Scan Media Library")
        self.scan_button.clicked.connect(self.scan_media_library_only)
        self.scan_button.setEnabled(False)
        button_row2.addWidget(self.scan_button)

        self.build_all_button = QPushButton("Build All Playlists")
        self.build_all_button.setObjectName("primary")
        self.build_all_button.clicked.connect(self.build_all_playlists)
        self.build_all_button.setEnabled(False)
        button_row2.addWidget(self.build_all_button)
        layout.addLayout(button_row2)

        self.toggle_log_button = QPushButton("Toggle Log")
        self.toggle_log_button.clicked.connect(self.toggle_log_panel)
        layout.addWidget(self.toggle_log_button)

        return panel

    def _build_marathon_frame(self):
        marathon_frame = QFrame()
        marathon_frame.setObjectName("marathonFrame")
        marathon_layout = QHBoxLayout(marathon_frame)

        self.global_marathon_checkbox = QCheckBox("Marathon Mode (all channels)")
        marathon_layout.addWidget(self.global_marathon_checkbox)

        self.global_days_label = QLabel("Days:")
        marathon_layout.addWidget(self.global_days_label)
        self.global_days_input = QLineEdit()
        self.global_days_input.setValidator(QIntValidator(0, 365))
        self.global_days_input.setFixedWidth(40)
        marathon_layout.addWidget(self.global_days_input)

        self.global_hours_label = QLabel("Hours:")
        marathon_layout.addWidget(self.global_hours_label)
        self.global_hours_input = QLineEdit()
        self.global_hours_input.setValidator(QIntValidator(0, 23))
        self.global_hours_input.setFixedWidth(40)
        marathon_layout.addWidget(self.global_hours_input)

        self.global_marathon_checkbox.toggled.connect(self.toggle_global_marathon_inputs)

        saved_global_marathon = self.config.get("global_marathon_settings", {"enabled": False, "days": 1, "hours": 0})
        self.global_marathon_checkbox.setChecked(saved_global_marathon["enabled"])
        self.global_days_input.setText(str(saved_global_marathon["days"]))
        self.global_hours_input.setText(str(saved_global_marathon["hours"]))
        self.toggle_global_marathon_inputs(saved_global_marathon["enabled"])

        return marathon_frame

    # -- middle panel: channel detail -------------------------------------

    def _build_detail_panel(self):
        self.detail_stack = QStackedWidget()

        placeholder = QLabel("Select a channel from the list\nto configure its programming.")
        placeholder.setObjectName("placeholder")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_stack.addWidget(placeholder)  # index 0

        form_widget = QWidget()
        form_widget.setObjectName("detailForm")
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)

        self.detail_title = QLabel("")
        self.detail_title.setObjectName("section_header")
        form_layout.addWidget(self.detail_title)

        fields = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self.on_name_edited)
        fields.addRow("Custom Name:", self.name_edit)

        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItems(["alphabetical", "random"])
        self.scan_mode_combo.currentTextChanged.connect(self.on_scan_mode_changed)
        fields.addRow("Episode Order:", self.scan_mode_combo)

        self.block_mode_combo = QComboBox()
        self.block_mode_combo.addItems(["None", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "Custom"])
        self.block_mode_combo.currentTextChanged.connect(self.on_block_mode_changed)
        fields.addRow("Block Mode:", self.block_mode_combo)

        form_layout.addLayout(fields)

        self.bumper_status_label = QLabel("")
        self.bumper_status_label.setObjectName("bumper_status")
        form_layout.addWidget(self.bumper_status_label)

        self.block_info_stack = QStackedWidget()

        simple_info = QLabel(
            "None: shows play once through in the order above, no repeats per visit.\n"
            "A number: that many episodes of each show play before moving to the next show.\n"
            "Custom: build your own show rotation and order below."
        )
        simple_info.setWordWrap(True)
        simple_info.setObjectName("bumper_status")
        self.block_info_stack.addWidget(simple_info)  # index 0

        self.block_info_stack.addWidget(self._build_sequence_editor())  # index 1

        form_layout.addWidget(self.block_info_stack, 1)

        self.build_channel_button = QPushButton("Build This Channel's Playlist")
        self.build_channel_button.setObjectName("primary")
        self.build_channel_button.clicked.connect(self.on_build_channel_clicked)
        form_layout.addWidget(self.build_channel_button)

        self.detail_stack.addWidget(form_widget)  # index 1
        return self.detail_stack

    def _build_sequence_editor(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("Programming Order — drag rows in 'Order' to change the rotation sequence")
        header.setObjectName("bumper_status")
        header.setWordWrap(True)
        layout.addWidget(header)

        lists_row = QHBoxLayout()

        avail_col = QVBoxLayout()
        avail_col.addWidget(QLabel("Available Shows"))
        self.available_list = QListWidget()
        self.available_list.itemDoubleClicked.connect(lambda _: self.add_to_sequence())
        avail_col.addWidget(self.available_list)
        lists_row.addLayout(avail_col, 1)

        mid_col = QVBoxLayout()
        mid_col.addStretch(1)
        add_btn = QPushButton("Add →")
        add_btn.clicked.connect(self.add_to_sequence)
        mid_col.addWidget(add_btn)
        remove_btn = QPushButton("← Remove")
        remove_btn.clicked.connect(self.remove_from_sequence)
        mid_col.addWidget(remove_btn)
        mid_col.addSpacing(12)
        up_btn = QPushButton("Move Up")
        up_btn.clicked.connect(self.move_sequence_item_up)
        mid_col.addWidget(up_btn)
        down_btn = QPushButton("Move Down")
        down_btn.clicked.connect(self.move_sequence_item_down)
        mid_col.addWidget(down_btn)
        mid_col.addStretch(1)
        lists_row.addLayout(mid_col)

        order_col = QVBoxLayout()
        order_col.addWidget(QLabel("Order (rotation sequence)"))
        self.sequence_list = SequenceListWidget()
        self.sequence_list.itemDoubleClicked.connect(lambda _: self.remove_from_sequence())
        self.sequence_list.currentItemChanged.connect(self.on_sequence_selection_changed)
        self.sequence_list.model().rowsMoved.connect(self.on_sequence_reordered)
        order_col.addWidget(self.sequence_list)

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("Episodes per turn:"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 30)
        self.count_spin.setEnabled(False)
        self.count_spin.valueChanged.connect(self.on_count_changed)
        count_row.addWidget(self.count_spin)
        count_row.addStretch(1)
        order_col.addLayout(count_row)

        lists_row.addLayout(order_col, 1)
        layout.addLayout(lists_row, 1)

        return container

    # -- right panel: log --------------------------------------------------

    def _build_log_panel(self):
        self.log_panel = QWidget()
        layout = QVBoxLayout(self.log_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QLabel("Log")
        header.setObjectName("section_header")
        layout.addWidget(header)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)
        return self.log_panel

    # -----------------------------------------------------------------
    # Misc small handlers
    # -----------------------------------------------------------------

    def toggle_global_marathon_inputs(self, checked):
        self.global_days_input.setEnabled(checked)
        self.global_hours_input.setEnabled(checked)
        self.global_days_label.setEnabled(checked)
        self.global_hours_label.setEnabled(checked)

    def toggle_log_panel(self):
        self.log_panel.setVisible(not self.log_panel.isVisible())

    def log(self, message):
        # Batch UI flushes instead of forcing a full repaint on every single
        # line (scanning a big library used to do this per-file, which is
        # noticeably heavier on a Pi 4 than on a desktop).
        self.log_box.append(message)
        self._log_buffer.append(message)
        if len(self._log_buffer) >= 8:
            QApplication.processEvents()
            self._log_buffer.clear()

    def _flush_log(self):
        QApplication.processEvents()
        self._log_buffer.clear()

    # -----------------------------------------------------------------
    # Folder selection / channel list population
    # -----------------------------------------------------------------

    def add_source(self):
        selected = QFileDialog.getExistingDirectory(self, "Select a Source Folder Containing Channel Subfolders")
        if not selected:
            return
        selected = str(Path(selected).resolve())
        sources = self.config.setdefault("sources", [])
        if any(str(Path(s["path"]).resolve()) == selected for s in sources if s.get("path")):
            self.log(f"Source already registered: {selected}")
            return
        sources.append({"path": selected, "volume_serial": get_volume_serial(selected)})
        self.log(f"Added source: {selected}")
        self.load_channel_folders()
        self.save_config()

    def remove_source(self):
        item = self.source_list.currentItem()
        if not item:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        sources = self.config.get("sources", [])
        if 0 <= idx < len(sources):
            removed = sources.pop(idx)
            self.log(f"Removed source: {removed.get('path')}")
        self.load_channel_folders()
        self.save_config()

    def _source_status_text(self, source):
        path = source.get("path", "")
        if not path:
            return "(empty)"
        if not Path(path).is_dir():
            return f"{path}   ⚠ not found (drive disconnected or letter changed)"
        current_serial = get_volume_serial(path)
        expected_serial = source.get("volume_serial")
        if expected_serial and current_serial and current_serial != expected_serial:
            return f"{path}   ⚠ volume serial changed (drive letter may point at a different disk)"
        return path

    def refresh_source_list(self):
        self.source_list.clear()
        for i, source in enumerate(self.config.get("sources", [])):
            list_item = QListWidgetItem(self._source_status_text(source))
            list_item.setData(Qt.ItemDataRole.UserRole, i)
            self.source_list.addItem(list_item)

    def select_playlists_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "Select Folder to Save Playlists")
        if selected:
            self.config["playlists_path"] = selected
            self.log(f"Playlists folder set to: {selected}")
            self.save_config()

    def load_channel_folders(self):
        self.channel_list.clear()
        self.current_channel = None
        self.detail_stack.setCurrentIndex(0)

        sources = self.config.get("sources", [])
        self.channel_sources = {}

        if not sources:
            self.refresh_source_list()
            self.log("No sources registered. Click 'Add Source' to select a folder containing channel subfolders.")
            self.scan_button.setEnabled(False)
            self.build_all_button.setEnabled(False)
            self.config["channel_order"] = []
            self.save_config()
            return

        all_dirs = set()
        for source in sources:
            path = source.get("path", "")
            if not path or not Path(path).is_dir():
                self.log(f"  WARNING: source not found, skipping: {path or '(empty)'}")
                continue

            current_serial = get_volume_serial(path)
            expected_serial = source.get("volume_serial")
            if expected_serial and current_serial and current_serial != expected_serial:
                self.log(f"  WARNING: volume serial for '{path}' differs from when it was added — "
                          f"the drive letter may now point at a different disk.")
            elif current_serial and not expected_serial:
                source["volume_serial"] = current_serial

            for d in Path(path).iterdir():
                if not d.is_dir():
                    continue
                channel_name = d.name
                if channel_name in self.channel_sources:
                    self.log(f"  WARNING: channel '{channel_name}' exists under both "
                              f"'{self.channel_sources[channel_name]}' and '{path}' — keeping the first "
                              f"and ignoring the duplicate. Move or rename one to resolve.")
                    continue
                self.channel_sources[channel_name] = path
                all_dirs.add(channel_name)

        self.refresh_source_list()

        ordered_channels = [ch for ch in self.config.get("channel_order", []) if ch in all_dirs]
        new_channels = sorted(list(all_dirs - set(ordered_channels)))
        final_channel_list = ordered_channels + new_channels
        self.config["channel_order"] = final_channel_list

        if not final_channel_list:
            self.log("No channel subfolders found across registered sources.")
            self.scan_button.setEnabled(False)
            self.build_all_button.setEnabled(False)
            self.save_config()
            return

        for i, channel_name in enumerate(final_channel_list):
            self._add_channel_list_item(i, channel_name)

        self.scan_button.setEnabled(True)
        self.build_all_button.setEnabled(True)
        self.log(f"{len(final_channel_list)} channel(s) loaded from {len(sources)} source(s).")
        self.save_config()

    def _channel_display_text(self, index, channel_name):
        display = self.config.get("custom_names", {}).get(channel_name, channel_name)
        return f"{index + 1:02d}   {display}"

    def _add_channel_list_item(self, index, channel_name):
        item = QListWidgetItem(self._channel_display_text(index, channel_name))
        item.setData(Qt.ItemDataRole.UserRole, channel_name)
        self.channel_list.addItem(item)

    def _find_channel_item(self, channel_name):
        for i in range(self.channel_list.count()):
            item = self.channel_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == channel_name:
                return item
        return None

    def update_channel_list_item_text(self, channel_name):
        item = self._find_channel_item(channel_name)
        if item:
            item.setText(self._channel_display_text(self.channel_list.row(item), channel_name))

    def on_channels_reordered(self, *args):
        # Fires the moment a drag-drop reorder completes, so numbering (and
        # the config's channel_order) stays in sync immediately instead of
        # waiting for the next scan/build/close to catch up.
        ordered_channel_names = [self.channel_list.item(i).data(Qt.ItemDataRole.UserRole)
                                  for i in range(self.channel_list.count())]
        self.config["channel_order"] = ordered_channel_names
        for i in range(self.channel_list.count()):
            item = self.channel_list.item(i)
            channel_name = item.data(Qt.ItemDataRole.UserRole)
            item.setText(self._channel_display_text(i, channel_name))
        if self.current_channel:
            idx = self.config["channel_order"].index(self.current_channel) + 1
            display = self.config.get("custom_names", {}).get(self.current_channel, self.current_channel)
            self.detail_title.setText(f"Ch. {idx:02d} — {display}")
        self.save_config()

    # -----------------------------------------------------------------
    # Detail panel population
    # -----------------------------------------------------------------

    def on_channel_selection_changed(self):
        items = self.channel_list.selectedItems()
        if not items:
            self.current_channel = None
            self.detail_stack.setCurrentIndex(0)
            return
        channel_name = items[0].data(Qt.ItemDataRole.UserRole)
        self.populate_detail_panel(channel_name)

    def populate_detail_panel(self, channel_name):
        self.current_channel = channel_name
        self.detail_stack.setCurrentIndex(1)

        idx = self.config["channel_order"].index(channel_name) + 1
        display = self.config.get("custom_names", {}).get(channel_name, channel_name)
        self.detail_title.setText(f"Ch. {idx:02d} — {display}")

        self.name_edit.blockSignals(True)
        self.name_edit.setText(display)
        self.name_edit.blockSignals(False)

        self.scan_mode_combo.blockSignals(True)
        self.scan_mode_combo.setCurrentText(self.config.get("scan_modes", {}).get(channel_name, "alphabetical"))
        self.scan_mode_combo.blockSignals(False)

        self.block_mode_combo.blockSignals(True)
        self.block_mode_combo.setCurrentText(self.config.get("block_counts", {}).get(channel_name, "None"))
        self.block_mode_combo.blockSignals(False)

        source_path = self.channel_sources.get(channel_name)
        bumper_path = Path(source_path) / channel_name / BUMPER_FILENAME if source_path else None
        self.bumper_status_label.setText("Bumper: found (plays between items)" if bumper_path and bumper_path.exists() else "Bumper: none")

        self._refresh_block_info_view()

    def _refresh_block_info_view(self):
        if self.block_mode_combo.currentText() == "Custom":
            self.block_info_stack.setCurrentIndex(1)
            self.refresh_sequence_lists()
        else:
            self.block_info_stack.setCurrentIndex(0)

    def on_name_edited(self):
        if not self.current_channel:
            return
        self.config.setdefault("custom_names", {})[self.current_channel] = self.name_edit.text()
        self.update_channel_list_item_text(self.current_channel)
        idx = self.config["channel_order"].index(self.current_channel) + 1
        self.detail_title.setText(f"Ch. {idx:02d} — {self.name_edit.text()}")
        self.save_config()

    def on_scan_mode_changed(self, text):
        if not self.current_channel:
            return
        self.config.setdefault("scan_modes", {})[self.current_channel] = text
        self.save_config()

    def on_block_mode_changed(self, text):
        if not self.current_channel:
            return
        self.config.setdefault("block_counts", {})[self.current_channel] = text
        self._refresh_block_info_view()
        self.save_config()

    # -----------------------------------------------------------------
    # Custom programming order editor
    # -----------------------------------------------------------------

    def refresh_sequence_lists(self):
        if not self.current_channel:
            return
        source_path = self.channel_sources.get(self.current_channel)
        show_dirs = list_show_folders(source_path, self.current_channel) if source_path else []
        sequence = self.get_sequence(self.current_channel)
        used_shows = {entry["show"] for entry in sequence}

        self.available_list.clear()
        for show in show_dirs:
            if show not in used_shows:
                self.available_list.addItem(QListWidgetItem(show))

        self.sequence_list.blockSignals(True)
        self.sequence_list.clear()
        for entry in sequence:
            list_item = QListWidgetItem(self._format_seq_entry(entry["show"], entry["count"]))
            list_item.setData(Qt.ItemDataRole.UserRole, entry["show"])
            self.sequence_list.addItem(list_item)
        self.sequence_list.blockSignals(False)

        self.count_spin.setEnabled(False)

    @staticmethod
    def _format_seq_entry(show, count):
        return f"{show}   ×{count}"

    def add_to_sequence(self):
        if not self.current_channel:
            return
        item = self.available_list.currentItem()
        if not item:
            return
        show = item.text()
        sequence = self.get_sequence(self.current_channel)
        sequence.append({"show": show, "count": 1})
        self.refresh_sequence_lists()
        self.save_config()

    def remove_from_sequence(self):
        if not self.current_channel:
            return
        item = self.sequence_list.currentItem()
        if not item:
            return
        show = item.data(Qt.ItemDataRole.UserRole)
        sequence = self.get_sequence(self.current_channel)
        self.config["custom_block_sequence"][self.current_channel] = [e for e in sequence if e["show"] != show]
        self.refresh_sequence_lists()
        self.save_config()

    def move_sequence_item_up(self):
        self._move_sequence_item(-1)

    def move_sequence_item_down(self):
        self._move_sequence_item(1)

    def _move_sequence_item(self, direction):
        if not self.current_channel:
            return
        row = self.sequence_list.currentRow()
        if row < 0:
            return
        sequence = self.get_sequence(self.current_channel)
        new_row = row + direction
        if new_row < 0 or new_row >= len(sequence):
            return
        sequence[row], sequence[new_row] = sequence[new_row], sequence[row]
        self.refresh_sequence_lists()
        self.sequence_list.setCurrentRow(new_row)
        self.save_config()

    def on_sequence_selection_changed(self, current, _previous):
        if not current or not self.current_channel:
            self.count_spin.setEnabled(False)
            return
        show = current.data(Qt.ItemDataRole.UserRole)
        sequence = self.get_sequence(self.current_channel)
        entry = next((e for e in sequence if e["show"] == show), None)
        if entry is None:
            self.count_spin.setEnabled(False)
            return
        self.count_spin.blockSignals(True)
        self.count_spin.setValue(entry["count"])
        self.count_spin.blockSignals(False)
        self.count_spin.setEnabled(True)

    def on_count_changed(self, value):
        if not self.current_channel:
            return
        current = self.sequence_list.currentItem()
        if not current:
            return
        show = current.data(Qt.ItemDataRole.UserRole)
        sequence = self.get_sequence(self.current_channel)
        for entry in sequence:
            if entry["show"] == show:
                entry["count"] = value
                current.setText(self._format_seq_entry(show, value))
                break
        self.save_config()

    def on_sequence_reordered(self, *args):
        # A drag just finished inside the sequence list itself — rebuild the
        # underlying config order from the widget's current visual order
        # without repopulating the widget (that would fight the drop that
        # Qt just performed).
        if not self.current_channel:
            return
        old_sequence = self.get_sequence(self.current_channel)
        counts_by_show = {entry["show"]: entry["count"] for entry in old_sequence}
        new_sequence = []
        for i in range(self.sequence_list.count()):
            show = self.sequence_list.item(i).data(Qt.ItemDataRole.UserRole)
            new_sequence.append({"show": show, "count": counts_by_show.get(show, 1)})
        self.config["custom_block_sequence"][self.current_channel] = new_sequence
        self.save_config()

    # -----------------------------------------------------------------
    # Save / scan / build
    # -----------------------------------------------------------------

    def save_config(self):
        self.config["global_marathon_settings"] = {
            "enabled": self.global_marathon_checkbox.isChecked(),
            "days": int(self.global_days_input.text() or 0),
            "hours": int(self.global_hours_input.text() or 0)
        }
        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Config", f"Could not save configuration: {e}")

    def scan_media_library_only(self):
        self.save_config()
        self.log("Scanning media library...")
        self.toggle_ui_state(False)

        try:
            for channel_name in self.config["channel_order"]:
                source_path = self.channel_sources.get(channel_name)
                if not source_path:
                    self.log(f"  Skipping {channel_name}: source not currently available.")
                    continue
                full_path = Path(source_path) / channel_name
                self.log(f"  Analyzing: {self.config['custom_names'].get(channel_name, channel_name)}")
                analyze_media(str(full_path), self.media_cache, self.log)

            save_cache(self.media_cache)
            self.log("Media library scan complete. Cache updated.")
        except Exception as e:
            self.log(f"An error occurred during scan: {e}")
        finally:
            self._flush_log()
            self.toggle_ui_state(True)

    def on_build_channel_clicked(self):
        if self.current_channel:
            self.build_single_playlist(self.current_channel)

    def build_single_playlist(self, channel_name):
        self.save_config()
        self.log(f"Building playlist for channel: {self.config['custom_names'].get(channel_name, channel_name)}...")
        self.toggle_ui_state(False)

        try:
            channel_number = self.config["channel_order"].index(channel_name) + 1
            self.process_playlist_build(channel_number, channel_name, target_duration_seconds=None)
            self.log(f"Playlist built for Ch. {channel_number:02d} - {self.config['custom_names'].get(channel_name, channel_name)}.")
        except Exception as e:
            self.log(f"An error occurred building playlist for {channel_name}: {e}")
            QMessageBox.critical(self, "Playlist Build Error", f"An error occurred: {e}")
        finally:
            self._flush_log()
            self.toggle_ui_state(True)

    def build_all_playlists(self):
        self.save_config()
        self.log("Building playlists for all channels...")
        self.toggle_ui_state(False)

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
            self._flush_log()
            self.toggle_ui_state(True)

    def process_playlist_build(self, channel_number, channel_name, target_duration_seconds=None):
        source_path = self.channel_sources.get(channel_name)
        if not source_path:
            self.log(f"  Skipping {channel_name}: source not currently available.")
            return
        base_path = Path(source_path)
        playlists_path = Path(self.config["playlists_path"])
        full_path = base_path / channel_name
        scan_mode = self.config["scan_modes"].get(channel_name, "alphabetical")

        channel_media_items = analyze_media(str(full_path), self.media_cache, self.log)

        block_mode = self.config.get('block_counts', {}).get(channel_name, 'None')

        if block_mode == "Custom":
            sequence = self.get_sequence(channel_name)
            build_playlist_custom_blocks(channel_number, channel_name, channel_media_items, scan_mode, sequence, playlists_path, base_path, target_duration_seconds, log_callback=self.log)
        else:
            block_size = int(block_mode) if str(block_mode).isdigit() else 0
            if block_size > 0:
                build_playlist_blocks(channel_number, channel_name, channel_media_items, scan_mode, block_size, playlists_path, base_path, target_duration_seconds)
            else:
                build_playlist(channel_number, channel_name, channel_media_items, scan_mode, playlists_path, base_path, target_duration_seconds)

    def toggle_ui_state(self, enabled):
        self.scan_button.setEnabled(enabled)
        self.build_all_button.setEnabled(enabled)
        self.add_source_button.setEnabled(enabled)
        self.remove_source_button.setEnabled(enabled)
        self.playlists_button.setEnabled(enabled)
        self.channel_list.setEnabled(enabled)
        self.build_channel_button.setEnabled(enabled)
        self.name_edit.setEnabled(enabled)
        self.scan_mode_combo.setEnabled(enabled)
        self.block_mode_combo.setEnabled(enabled)
        QApplication.processEvents()

    def closeEvent(self, event):
        self.save_config()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = MediaAnalysisApp()
    ex.show()
    sys.exit(app.exec())