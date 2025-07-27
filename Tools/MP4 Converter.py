import os
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

class ReencoderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TV Channel File Re-Encoder")

        self.file_queue = queue.Queue()
        self.processing = False
        self.destination_folder = ""

        # UI Elements
        self.frame = ttk.Frame(self.root, padding="10")
        self.frame.grid(row=0, column=0, sticky="nsew")

        self.label = ttk.Label(self.frame, text="Select folder with MKV, MP4, or AVI files:")
        self.label.grid(row=0, column=0, sticky="w")

        self.select_button = ttk.Button(self.frame, text="Choose Source Folder", command=self.select_folder)
        self.select_button.grid(row=1, column=0, pady=5)

        self.dest_label = ttk.Label(self.frame, text="Select destination folder for converted files:")
        self.dest_label.grid(row=2, column=0, sticky="w")

        self.dest_button = ttk.Button(self.frame, text="Choose Destination Folder", command=self.select_destination)
        self.dest_button.grid(row=3, column=0, pady=5)

        self.preset_label = ttk.Label(self.frame, text="Encoding Speed Preset:")
        self.preset_label.grid(row=4, column=0, sticky="w")

        self.preset_var = tk.StringVar(value="fast")
        self.preset_menu = ttk.Combobox(self.frame, textvariable=self.preset_var, state="readonly")
        self.preset_menu['values'] = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow")
        self.preset_menu.grid(row=5, column=0, pady=5)

        self.threads_label = ttk.Label(self.frame, text="Number of Threads:")
        self.threads_label.grid(row=6, column=0, sticky="w")

        self.threads_var = tk.IntVar(value=2)
        self.threads_spinbox = tk.Spinbox(self.frame, from_=1, to=16, textvariable=self.threads_var, width=5)
        self.threads_spinbox.grid(row=7, column=0, pady=5, sticky="w")

        self.queue_label = ttk.Label(self.frame, text="Queue:")
        self.queue_label.grid(row=8, column=0, sticky="w")

        self.queue_box = tk.Listbox(self.frame, width=60, height=10)
        self.queue_box.grid(row=9, column=0, pady=5)

        self.start_button = ttk.Button(self.frame, text="Start Re-Encoding", command=self.start_processing)
        self.start_button.grid(row=10, column=0, pady=10)

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            for filename in os.listdir(folder):
                if filename.lower().endswith((".mkv", ".mp4", ".avi")):
                    input_path = os.path.join(folder, filename)
                    self.file_queue.put((input_path, filename))
                    self.queue_box.insert(tk.END, f"Queued: {filename}")

    def select_destination(self):
        self.destination_folder = filedialog.askdirectory()
        if not self.destination_folder:
            messagebox.showwarning("Warning", "No destination folder selected.")

    def start_processing(self):
        if not self.processing and not self.file_queue.empty() and self.destination_folder:
            self.processing = True
            threading.Thread(target=self.process_queue, daemon=True).start()
        elif not self.destination_folder:
            messagebox.showwarning("Missing Destination", "Please select a destination folder before starting.")

    def process_queue(self):
        while not self.file_queue.empty():
            input_path, filename = self.file_queue.get()
            output_path = os.path.join(self.destination_folder, os.path.splitext(filename)[0] + ".mp4")
            self.update_status(f"Processing: {filename}")

            try:
                self.run_ffmpeg(input_path, output_path)
                self.update_status(f"Done: {filename}")
            except Exception as e:
                self.update_status(f"Error: {filename}")
                print(f"Failed to encode {filename}: {e}")

            self.file_queue.task_done()

        self.processing = False
        messagebox.showinfo("Finished", "All files processed!")

    def run_ffmpeg(self, input_path, output_path):
        preset = self.preset_var.get()
        threads = str(self.threads_var.get())

        command = [
            "ffmpeg",
            "-i", input_path,
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-threads", threads,
            output_path
        ]
        subprocess.run(command, check=True)

    def update_status(self, msg):
        self.queue_box.insert(tk.END, msg)
        self.queue_box.yview(tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = ReencoderApp(root)
    root.mainloop()
