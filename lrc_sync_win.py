#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import win32file
    import pywintypes

    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

PIPE_NAME = r"\\.\pipe\lrc_sync_mpv"
REFERER_HEADER = "Referer: https://www.premrawat.com/gifts-album/"

SAMPLE_SONGS = [
    {
        "index": 0,
        "title": "Being Here",
        "tag": "(Amaroo 2026 Concert)",
        "audio": "https://secureaudio.timelesstoday.tv/music/Prem_Rawat/Gifts/Being-Here/audio.m3u8",
        "link_youtube": "",
        "lyrics": "Being here in this moment  Feeling the breath as it flows  Quiet inside the storm  Knowing what the heart already knows",
    },
    {
        "index": 1,
        "title": "Peace",
        "tag": "(Gifts)",
        "audio": "https://secureaudio.timelesstoday.tv/music/Prem_Rawat/Gifts/Peace/audio.m3u8",
        "link_youtube": "",
        "lyrics": "Peace is not a distant place  It waits right here within  Every breath a gentle start  Where true clarity begins",
    },
]


def format_lrc_time(seconds):
    total_centis = max(0, int(round(float(seconds) * 100)))
    mins = total_centis // 6000
    total_centis %= 6000
    secs = total_centis // 100
    centis = total_centis % 100
    return f"{mins:02d}:{secs:02d}.{centis:02d}"


def split_lyrics_into_lines(raw_text):
    if not raw_text:
        return []
    parts = re.split(r"(?:\r?\n)+| {2,}", raw_text)
    lines = []
    for p in parts:
        clean = re.sub(r"\s+", " ", p).strip()
        if clean:
            lines.append(clean)
    return lines


def extract_youtube_url(song):
    if not isinstance(song, dict):
        return ""
    val = song.get("link_youtube")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return ""


def find_mpv_executable():
    for name in ("mpv.exe", "mpv"):
        found = shutil.which(name)
        if found:
            return found
    common_paths = [
        r"C:\mpv\mpv.exe",
        r"C:\Program Files\mpv\mpv.exe",
        r"C:\Program Files (x86)\mpv\mpv.exe",
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "Microsoft", "WinGet", "Links", "mpv.exe"),
    ]
    for p in common_paths:
        if os.path.isfile(p):
            return p
    return None


def mpv_pipe_cmd(command_list):
    if not HAS_WIN32:
        return None
    payload = (json.dumps({"command": command_list}) + "\n").encode("utf-8")
    try:
        handle = win32file.CreateFile(
            PIPE_NAME,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        try:
            win32file.WriteFile(handle, payload)
            _, data = win32file.ReadFile(handle, 4096)
            raw = data.decode("utf-8", errors="ignore").strip()
            for line in raw.splitlines():
                try:
                    obj = json.loads(line)
                    if "data" in obj:
                        return obj["data"]
                except json.JSONDecodeError:
                    continue
        finally:
            win32file.CloseHandle(handle)
    except Exception:
        return None
    return None


class LrcSyncWinApp:
    def __init__(self, root, initial_file=None):
        self.root = root
        self.root.title("Sincronizador LRC (Windows)")
        self.root.geometry("1040x680")
        self.root.minsize(820, 520)

        self.songs = list(SAMPLE_SONGS)
        self.current_song = None
        self.current_yt_url = ""
        self.mpv_exe = find_mpv_executable()
        self.mpv_proc = None
        self.is_playing = False
        self.is_paused = False
        self.current_time = 0.0
        self.last_tick_mono = None
        self.drag_start_x = None
        self.drag_start_time = 0.0
        self.selected_done_index = None

        self.pending_lines = []
        self.done_rows = []  # lista de dicts: {"seconds": float, "stamp": str, "text": str}

        self._build_ui()
        self._bind_events()

        if initial_file and os.path.isfile(initial_file):
            with open(initial_file, "r", encoding="utf-8") as f:
                self.load_lyrics_text(f.read(), title_text=os.path.splitext(os.path.basename(initial_file))[0])
        else:
            auto_json = self.find_auto_json()
            if auto_json:
                self.load_json_file(auto_json)
            else:
                self.song_combo.current(0)
                self.on_song_changed()

        self.root.after(100, self.poll_player_time)

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Fila superior: selector de canción y carga de JSON / TXT
        song_bar = ttk.Frame(main_frame)
        song_bar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(song_bar, text="Canción:").pack(side=tk.LEFT, padx=(0, 6))

        self.song_combo = ttk.Combobox(song_bar, state="readonly")
        self.song_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.populate_song_combo()

        ttk.Button(song_bar, text="Cargar gifts_album_yt.json", command=self.on_load_json).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(song_bar, text="Pegar / Abrir .txt", command=self.on_load_dialog).pack(side=tk.RIGHT)

        # Barra de reproducción, reloj y ajustes de tiempo
        top_bar = ttk.Frame(main_frame)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        self.btn_play = ttk.Button(top_bar, text="▶ Reproducir / Pausa", command=self.on_play_pause)
        self.btn_play.pack(side=tk.LEFT, padx=(0, 8))

        self.time_var = tk.StringVar(value="00:00.00")
        self.time_lbl = tk.Label(
            top_bar,
            textvariable=self.time_var,
            font=("Consolas", 18, "bold"),
            padx=10,
            pady=2,
            relief=tk.GROOVE,
            cursor="sb_h_double_arrow",
        )
        self.time_lbl.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(top_bar, text="⏪ -0.50s", command=lambda: self.adjust_time(-0.50)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_bar, text="◀ -0.10s", command=lambda: self.adjust_time(-0.10)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_bar, text="+0.10s ▶", command=lambda: self.adjust_time(0.10)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_bar, text="+0.50s ⏩", command=lambda: self.adjust_time(0.50)).pack(side=tk.LEFT, padx=2)

        # Panel central dividido: Pendientes vs Ya hecho
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        left_frame = ttk.Frame(paned, padding=(0, 0, 5, 0))
        self.pending_var = tk.StringVar(value="Pendientes (0 líneas)")
        ttk.Label(left_frame, textvariable=self.pending_var, font=("TkDefaultFont", 10, "bold")).pack(
            anchor=tk.W, pady=(0, 4)
        )

        self.pending_listbox = tk.Listbox(left_frame, selectmode=tk.SINGLE, exportselection=False)
        scroll_left = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.pending_listbox.yview)
        self.pending_listbox.configure(yscrollcommand=scroll_left.set)
        self.pending_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_left.pack(side=tk.RIGHT, fill=tk.Y)
        paned.add(left_frame, weight=1)

        right_frame = ttk.Frame(paned, padding=(5, 0, 0, 0))
        ttk.Label(
            right_frame,
            text="Ya hecho (selecciona una fila para mover su tiempo)",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, 4))

        self.done_tree = ttk.Treeview(
            right_frame,
            columns=("tiempo", "frase"),
            show="headings",
            selectmode="browse",
        )
        self.done_tree.heading("tiempo", text="Tiempo")
        self.done_tree.heading("frase", text="Frase sincronizada")
        self.done_tree.column("tiempo", width=110, anchor=tk.W, stretch=False)
        self.done_tree.column("frase", width=360, anchor=tk.W, stretch=True)

        scroll_right = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.done_tree.yview)
        self.done_tree.configure(yscrollcommand=scroll_right.set)
        self.done_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_right.pack(side=tk.RIGHT, fill=tk.Y)
        paned.add(right_frame, weight=1)

        # Barra inferior de acciones
        bottom_bar = ttk.Frame(main_frame)
        bottom_bar.pack(fill=tk.X, pady=(0, 6))

        ttk.Button(
            bottom_bar,
            text="⏱ Presiona cuando oigas la frase (Enter)",
            command=self.on_stamp_line,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(bottom_bar, text="↩ Devolver última a pendientes", command=self.on_undo_last).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(bottom_bar, text="💾 Guardar .lrc", command=self.on_save_lrc).pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Listo.")
        ttk.Label(main_frame, textvariable=self.status_var, anchor=tk.W).pack(fill=tk.X)

    def _bind_events(self):
        self.song_combo.bind("<<ComboboxSelected>>", self.on_song_changed)
        self.done_tree.bind("<<TreeviewSelect>>", self.on_done_selection_changed)

        self.time_lbl.bind("<ButtonPress-1>", self.on_time_press)
        self.time_lbl.bind("<B1-Motion>", self.on_time_drag)
        self.time_lbl.bind("<ButtonRelease-1>", self.on_time_release)

        self.root.bind("<Return>", lambda _e: self.on_stamp_line())
        self.root.bind("<KP_Enter>", lambda _e: self.on_stamp_line())
        self.root.bind("<Left>", lambda e: self._on_arrow_key(e, -0.05))
        self.root.bind("<Right>", lambda e: self._on_arrow_key(e, 0.05))
        self.root.bind("<Shift-Left>", lambda e: self._on_arrow_key(e, -10.0))
        self.root.bind("<Shift-Right>", lambda e: self._on_arrow_key(e, 10.0))
        self.root.bind("<space>", lambda _e: self.on_play_pause())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _on_arrow_key(self, event, delta):
        widget_class = event.widget.winfo_class() if event and event.widget else ""
        if widget_class in ("Entry", "TCombobox", "Text"):
            return
        self.adjust_time(delta)
        return "break"

    def set_status(self, msg):
        self.status_var.set(msg)

    def find_auto_json(self):
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(os.getcwd(), "gifts_album_yt.json"),
            os.path.join(home, "gifts_album_yt.json"),
            os.path.join(home, "Downloads", "gifts_album_yt.json"),
            os.path.join(home, "Descargas", "gifts_album_yt.json"),
            os.path.join(os.getcwd(), "gifts_album.json"),
            os.path.join(home, "Downloads", "gifts_album.json"),
            os.path.join(home, "Descargas", "gifts_album.json"),
            os.path.join(home, "gifts_album.json"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    def load_json_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list) and len(loaded) > 0:
                self.songs = loaded
                yt_count = sum(1 for s in self.songs if extract_youtube_url(s))
                self.populate_song_combo()
                self.song_combo.current(0)
                self.on_song_changed()
                self.set_status(
                    f"Cargadas {len(self.songs)} canciones ({yt_count} con YouTube) desde {os.path.basename(path)}"
                )
                return True
        except Exception as e:
            self.set_status(f"⚠️ Error al leer {path}: {e}")
        return False

    def populate_song_combo(self):
        labels = []
        for idx, s in enumerate(self.songs):
            title = s.get("title") or f"Pista {idx + 1}"
            tag = s.get("tag") or ""
            labels.append(f"{idx + 1:03d} - {title} {tag}".strip())
        self.song_combo["values"] = labels

    def on_song_changed(self, _event=None):
        idx = self.song_combo.current()
        if idx < 0 or idx >= len(self.songs):
            return
        self.current_song = self.songs[idx]
        self.current_yt_url = extract_youtube_url(self.current_song)
        self.stop_mpv()
        self.current_time = 0.0
        self.last_tick_mono = None
        self.update_time_display()

        title = (self.current_song.get("title") or "").strip()
        self.load_lyrics_text(self.current_song.get("lyrics", ""), title_text=title)
        yt_msg = f"YouTube: {self.current_yt_url}" if self.current_yt_url else "Sin link de YouTube"
        self.set_status(f"Pista {idx + 1}/{len(self.songs)}: {title} ({yt_msg}) — Pulsa Reproducir/Pausa.")

    def start_current_song_audio(self, start_at=None):
        url = (self.current_song or {}).get("audio", "").strip()
        if not url:
            self.set_status("⚠️ Esta pista no tiene URL de audio.")
            return
        if not self.mpv_exe:
            self.mpv_exe = find_mpv_executable()
        if not self.mpv_exe:
            self.set_status("⚠️ Instala 'mpv' en Windows para reproducir streams .m3u8.")
            return

        self.stop_mpv()

        cmd = [
            self.mpv_exe,
            "--no-video",
            "--cache=yes",
            f"--http-header-fields={REFERER_HEADER}",
            "--really-quiet",
        ]
        if HAS_WIN32:
            cmd.append(f"--input-ipc-server={PIPE_NAME}")
        if start_at is not None and start_at > 0.15:
            cmd.append(f"--start={start_at:.2f}")
        cmd.append(url)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.mpv_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self.is_playing = True
            self.is_paused = False
            self.last_tick_mono = time.monotonic()
            self.btn_play.configure(text="⏸ Pausar / Reanudar")
            self.set_status(f"Reproduciendo: {self.current_song.get('title', '')}")
        except Exception as e:
            self.set_status(f"⚠️ Error al iniciar mpv: {e}")

    def stop_mpv(self):
        if self.mpv_proc and self.mpv_proc.poll() is None:
            try:
                self.mpv_proc.terminate()
                self.mpv_proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.mpv_proc.kill()
                except Exception:
                    pass
        self.mpv_proc = None
        self.is_playing = False
        self.is_paused = False
        self.last_tick_mono = None
        self.btn_play.configure(text="▶ Reproducir / Pausa")

    def on_close(self):
        self.stop_mpv()
        self.root.destroy()

    def on_load_json(self):
        path = filedialog.askopenfilename(
            title="Selecciona gifts_album_yt.json",
            filetypes=[("Archivos JSON", "*.json"), ("Todos los archivos", "*.*")],
        )
        if path:
            self.load_json_file(path)

    def load_lyrics_text(self, text, title_text=""):
        self.pending_lines = split_lyrics_into_lines(text)
        self.done_rows = []
        self.selected_done_index = None

        clean_title = re.sub(r"\s+", " ", (title_text or "")).strip()
        if clean_title:
            self.done_rows.append({
                "seconds": 1.00,
                "stamp": "[00:01:00]",
                "text": f"Título de la canción: {clean_title}",
            })

        self._refresh_pending_listbox()
        self._refresh_done_tree()
        self.select_first_pending()

    def _refresh_pending_listbox(self):
        self.pending_listbox.delete(0, tk.END)
        for line in self.pending_lines:
            self.pending_listbox.insert(tk.END, line)
        self.pending_var.set(f"Pendientes ({len(self.pending_lines)} líneas — primera seleccionada)")

    def _refresh_done_tree(self, select_idx=None):
        for item in self.done_tree.get_children():
            self.done_tree.delete(item)
        for idx, row in enumerate(self.done_rows):
            iid = str(idx)
            self.done_tree.insert("", tk.END, iid=iid, values=(row["stamp"], row["text"]))
        if select_idx is not None and 0 <= select_idx < len(self.done_rows):
            iid = str(select_idx)
            self.done_tree.selection_set(iid)
            self.done_tree.focus(iid)
            self.done_tree.see(iid)
            self.selected_done_index = select_idx

    def select_first_pending(self):
        if len(self.pending_lines) > 0:
            self.pending_listbox.selection_clear(0, tk.END)
            self.pending_listbox.selection_set(0)
            self.pending_listbox.activate(0)
            self.pending_listbox.see(0)

    def poll_player_time(self):
        if self.drag_start_x is None and self.is_playing and not self.is_paused:
            if self.mpv_proc is not None and self.mpv_proc.poll() is not None:
                rc = self.mpv_proc.returncode
                self.mpv_proc = None
                self.is_playing = False
                self.last_tick_mono = None
                self.btn_play.configure(text="▶ Reproducir / Pausa")
                self.set_status("Reproducción finalizada." if rc == 0 else f"⚠️ mpv se detuvo (código {rc}).")
            else:
                pos = mpv_pipe_cmd(["get_property", "time-pos"]) if HAS_WIN32 else None
                if isinstance(pos, (int, float)):
                    self.current_time = float(pos)
                    self.last_tick_mono = time.monotonic()
                else:
                    now = time.monotonic()
                    if self.last_tick_mono is not None:
                        self.current_time += max(0.0, now - self.last_tick_mono)
                    self.last_tick_mono = now
                self.update_time_display()

        self.root.after(100, self.poll_player_time)

    def update_time_display(self):
        self.time_var.set(format_lrc_time(self.current_time))

    def on_play_pause(self):
        if self.is_playing and self.mpv_proc is not None and self.mpv_proc.poll() is None:
            if HAS_WIN32:
                paused = mpv_pipe_cmd(["get_property", "pause"])
                new_paused = not bool(paused)
                mpv_pipe_cmd(["set_property", "pause", new_paused])
                self.is_paused = new_paused
                self.last_tick_mono = None if new_paused else time.monotonic()
            else:
                if not self.is_paused:
                    self.stop_mpv()
                    self.is_paused = True
                    self.set_status(f"Pausado en {format_lrc_time(self.current_time)}")
                    return
            self.set_status("Pausado" if self.is_paused else "Reproduciendo")
        else:
            start_pos = self.current_time if self.current_time > 0.15 else None
            self.start_current_song_audio(start_at=start_pos)

    def on_stamp_line(self):
        if not self.pending_lines:
            return
        sel = self.pending_listbox.curselection()
        idx = sel[0] if sel else 0
        line_text = self.pending_lines.pop(idx)

        ts = self.current_time
        self.done_rows.append({
            "seconds": ts,
            "stamp": f"[{format_lrc_time(ts)}]",
            "text": line_text,
        })

        new_done_idx = len(self.done_rows) - 1
        self._refresh_pending_listbox()
        self._refresh_done_tree(select_idx=new_done_idx)
        self.select_first_pending()

    def on_done_selection_changed(self, _event=None):
        sel = self.done_tree.selection()
        if sel:
            try:
                self.selected_done_index = int(sel[0])
            except ValueError:
                self.selected_done_index = None
        else:
            self.selected_done_index = None

    def adjust_time(self, delta):
        if self.selected_done_index is not None and 0 <= self.selected_done_index < len(self.done_rows):
            row = self.done_rows[self.selected_done_index]
            new_ts = max(0.0, row["seconds"] + delta)
            row["seconds"] = new_ts
            row["stamp"] = f"[{format_lrc_time(new_ts)}]"
            idx = self.selected_done_index
            self._refresh_done_tree(select_idx=idx)
            self.set_status(f"Ajustada línea {idx + 1} a {row['stamp']}")
        else:
            self.current_time = max(0.0, self.current_time + delta)
            self.last_tick_mono = time.monotonic()
            self.update_time_display()
            if self.is_playing and self.mpv_proc is not None and self.mpv_proc.poll() is None:
                if HAS_WIN32:
                    mpv_pipe_cmd(["seek", f"{self.current_time:.2f}", "absolute"])
                else:
                    self.start_current_song_audio(start_at=self.current_time)

    def on_time_press(self, event):
        self.drag_start_x = event.x
        if self.selected_done_index is not None and 0 <= self.selected_done_index < len(self.done_rows):
            self.drag_start_time = self.done_rows[self.selected_done_index]["seconds"]
        else:
            self.drag_start_time = self.current_time

    def on_time_drag(self, event):
        if self.drag_start_x is None:
            return
        dx = event.x - self.drag_start_x
        delta = (dx / 10.0) * 0.05
        new_t = max(0.0, self.drag_start_time + delta)
        if self.selected_done_index is not None and 0 <= self.selected_done_index < len(self.done_rows):
            row = self.done_rows[self.selected_done_index]
            row["seconds"] = new_t
            row["stamp"] = f"[{format_lrc_time(new_t)}]"
            self._refresh_done_tree(select_idx=self.selected_done_index)
        else:
            self.current_time = new_t
            self.update_time_display()

    def on_time_release(self, _event):
        if self.drag_start_x is None:
            return
        self.drag_start_x = None
        self.last_tick_mono = time.monotonic()
        if self.selected_done_index is None and self.is_playing and self.mpv_proc and self.mpv_proc.poll() is None:
            if HAS_WIN32:
                mpv_pipe_cmd(["seek", f"{self.current_time:.2f}", "absolute"])
            else:
                self.start_current_song_audio(start_at=self.current_time)

    def on_undo_last(self):
        if not self.done_rows:
            return
        last = self.done_rows.pop()
        self.pending_lines.insert(0, last["text"])
        self._refresh_pending_listbox()
        self._refresh_done_tree(select_idx=len(self.done_rows) - 1 if self.done_rows else None)
        self.select_first_pending()

    def on_load_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Pegar o abrir letra manual")
        dlg.geometry("560x380")
        dlg.transient(self.root)

        txt_widget = tk.Text(dlg, wrap=tk.WORD)
        txt_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        def open_txt_file():
            path = filedialog.askopenfilename(
                parent=dlg,
                title="Selecciona archivo de letra",
                filetypes=[("Archivos de texto", "*.txt"), ("Todos los archivos", "*.*")],
            )
            if path:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                title = (self.current_song or {}).get("title") or os.path.splitext(os.path.basename(path))[0]
                self.load_lyrics_text(content, title_text=title)
                dlg.destroy()

        def apply_pasted():
            raw = txt_widget.get("1.0", tk.END).strip()
            if raw:
                title = (self.current_song or {}).get("title", "")
                self.load_lyrics_text(raw, title_text=title)
            dlg.destroy()

        ttk.Button(btn_frame, text="Abrir .txt", command=open_txt_file).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Cancelar", command=dlg.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="Aceptar", command=apply_pasted).pack(side=tk.RIGHT)

    def on_save_lrc(self):
        music_dir = os.path.join(os.path.expanduser("~"), "Music")
        title = (self.current_song or {}).get("title") or "cancion"
        tag = (self.current_song or {}).get("tag") or "Gifts Album"
        safe_artist = "Prem Rawat"
        safe_album = re.sub(r'[\\/:*?"<>|]', "", tag).strip() or "Gifts Album"
        safe_title = re.sub(r'[\\/:*?"<>|]', "", title).strip() or "cancion"

        target_dir = os.path.join(music_dir, "Lyrics", safe_artist, safe_album)
        os.makedirs(target_dir, exist_ok=True)
        default_name = f"{safe_artist} - {safe_title}.lrc"

        out_path = filedialog.asksaveasfilename(
            title="Guardar archivo .lrc",
            initialdir=target_dir,
            initialfile=default_name,
            defaultextension=".lrc",
            filetypes=[("Archivos LRC", "*.lrc"), ("Todos los archivos", "*.*")],
        )
        if not out_path:
            return

        try:
            with open(out_path, "w", encoding="utf-8") as f:
                if self.current_yt_url:
                    f.write(f"#Link a youtube: {self.current_yt_url}\n")
                for row in self.done_rows:
                    f.write(f"{row['stamp']}{row['text']}\n")
            self.set_status(f"Guardado: {out_path}")
        except Exception as e:
            messagebox.showerror("Error al guardar", str(e))


def main():
    arg_file = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    LrcSyncWinApp(root, initial_file=arg_file)
    root.mainloop()


if __name__ == "__main__":
    main()
