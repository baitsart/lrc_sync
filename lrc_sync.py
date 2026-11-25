#!/usr/bin/env python3
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

IPC_SOCKET = "/tmp/lrc_sync_mpv.sock"
REFERER_HEADER = "Referer: https://www.premrawat.com/gifts-album/"

SAMPLE_SONGS = [
    {
        "index": 0,
        "title": "Being Here",
        "tag": "(Amaroo 2026 Concert)",
        "audio": "https://secureaudio.timelesstoday.tv/music/Prem_Rawat/Gifts/Being-Here/audio.m3u8",
        "lyrics": "Being here in this moment  Feeling the breath as it flows  Quiet inside the storm  Knowing what the heart already knows",
    },
    {
        "index": 1,
        "title": "Peace",
        "tag": "(Gifts)",
        "audio": "https://secureaudio.timelesstoday.tv/music/Prem_Rawat/Gifts/Peace/audio.m3u8",
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


def mpv_ipc(command_list):
    if not os.path.exists(IPC_SOCKET):
        return None
    payload = json.dumps({"command": command_list}).encode("utf-8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            s.connect(IPC_SOCKET)
            s.sendall(payload)
            chunks = []
            while True:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data:
                    break
            raw = b"".join(chunks).decode("utf-8", errors="ignore").strip()
            for line in raw.splitlines():
                try:
                    obj = json.loads(line)
                    if "data" in obj:
                        return obj["data"]
                except json.JSONDecodeError:
                    continue
    except Exception:
        return None
    return None


class LrcSyncWindow(Gtk.Window):
    def __init__(self, initial_file=None):
        super().__init__(title="Sincronizador LRC")
        self.set_default_size(1020, 670)
        self.set_border_width(10)

        self.songs = list(SAMPLE_SONGS)
        self.current_song = None
        self.current_yt_url = ""
        self.mpv_proc = None
        self.is_paused = False
        self.current_time = 0.0
        self.drag_start_x = None
        self.drag_start_time = 0.0
        self.selected_done_index = None

        # Guardamos el índice original de cada línea estampada para poder devolverla a su sitio
        # Estructura por fila: (ts_float, tag_str, phrase_str, orig_idx_int_or_None)
        self.done_orig_indices = []

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(main_vbox)

        song_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_vbox.pack_start(song_bar, False, False, 0)

        song_bar.pack_start(Gtk.Label(label="Canción:"), False, False, 0)

        self.song_combo = Gtk.ComboBoxText()
        self.populate_song_combo()
        self.song_combo.connect("changed", self.on_song_changed)
        song_bar.pack_start(self.song_combo, True, True, 0)

        btn_json = Gtk.Button(label="📥 Cargar gifts_album.json")
        btn_json.connect("clicked", self.on_load_json)
        song_bar.pack_start(btn_json, False, False, 0)

        btn_edit = Gtk.Button(label="✏️ Editar entrada")
        btn_edit.connect("clicked", self.on_edit_entry)
        song_bar.pack_start(btn_edit, False, False, 0)

        btn_load = Gtk.Button(label="📄 Pegar / Abrir .txt")
        btn_load.connect("clicked", self.on_load_dialog)
        song_bar.pack_end(btn_load, False, False, 0)

        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_vbox.pack_start(top_bar, False, False, 0)

        self.btn_play = Gtk.Button(label="▶ Reproducir / Pausa")
        self.btn_play.set_tooltip_text("Reproducir/pausar audio (o tecla Multimedia Play/Pausa)")
        self.btn_play.connect("clicked", self.on_play_pause)
        top_bar.pack_start(self.btn_play, False, False, 0)

        self.time_event_box = Gtk.EventBox()
        self.time_event_box.set_can_focus(True)
        self.time_event_box.set_tooltip_text(
            "Arrastra horizontalmente con el mouse o usa flechas ← / → para mover el tiempo"
        )
        self.time_label = Gtk.Label()
        self.time_label.set_markup(
            "<span size='x-large' weight='bold' font_family='monospace'>00:00.00</span>"
        )
        self.time_event_box.add(self.time_label)

        self.time_event_box.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.time_event_box.connect("button-press-event", self.on_time_press)
        self.time_event_box.connect("motion-notify-event", self.on_time_drag)
        self.time_event_box.connect("button-release-event", self.on_time_release)
        self.time_event_box.connect("key-press-event", self.on_time_key)
        top_bar.pack_start(self.time_event_box, False, False, 8)

        btn_early_big = Gtk.Button(label="⏪ -0.50s")
        btn_early_big.connect("clicked", lambda *_: self.adjust_time(-0.50))
        top_bar.pack_start(btn_early_big, False, False, 0)

        btn_early = Gtk.Button(label="◀ -0.10s")
        btn_early.connect("clicked", lambda *_: self.adjust_time(-0.10))
        top_bar.pack_start(btn_early, False, False, 0)

        btn_late = Gtk.Button(label="+0.10s ▶")
        btn_late.connect("clicked", lambda *_: self.adjust_time(0.10))
        top_bar.pack_start(btn_late, False, False, 0)

        btn_late_big = Gtk.Button(label="+0.50s ⏩")
        btn_late_big.connect("clicked", lambda *_: self.adjust_time(0.50))
        top_bar.pack_start(btn_late_big, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        main_vbox.pack_start(paned, True, True, 0)

        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pending_header = Gtk.Label(xalign=0)
        self.pending_header.set_markup("<b>Pendientes (0 líneas)</b>")
        left_box.pack_start(self.pending_header, False, False, 0)

        self.pending_store = Gtk.ListStore(str)
        self.pending_view = Gtk.TreeView(model=self.pending_store)
        self.pending_view.append_column(Gtk.TreeViewColumn("Letra", Gtk.CellRendererText(), text=0))

        scroll_left = Gtk.ScrolledWindow()
        scroll_left.add(self.pending_view)
        left_box.pack_start(scroll_left, True, True, 0)
        paned.pack1(left_box, True, False)

        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        right_lbl = Gtk.Label(xalign=0)
        right_lbl.set_markup("<b>Ya hecho (selecciona una fila para mover su tiempo)</b>")
        right_box.pack_start(right_lbl, False, False, 0)

        self.done_store = Gtk.ListStore(float, str, str)
        self.done_view = Gtk.TreeView(model=self.done_store)
        self.done_view.get_selection().connect("changed", self.on_done_selection_changed)
        self.done_view.append_column(Gtk.TreeViewColumn("Tiempo", Gtk.CellRendererText(), text=1))
        self.done_view.append_column(Gtk.TreeViewColumn("Frase sincronizada", Gtk.CellRendererText(), text=2))

        scroll_right = Gtk.ScrolledWindow()
        scroll_right.add(self.done_view)
        right_box.pack_start(scroll_right, True, True, 0)
        paned.pack2(right_box, True, False)
        paned.set_position(490)

        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_vbox.pack_start(bottom_bar, False, False, 0)

        self.btn_stamp = Gtk.Button(label="⏱ Presiona cuando oigas la frase (Enter)")
        self.btn_stamp.get_style_context().add_class("suggested-action")
        self.btn_stamp.connect("clicked", self.on_stamp_line)
        bottom_bar.pack_start(self.btn_stamp, True, True, 0)

        btn_undo = Gtk.Button(label="↩ Devolver última a pendientes")
        btn_undo.connect("clicked", self.on_undo_last)
        bottom_bar.pack_start(btn_undo, False, False, 0)

        btn_save = Gtk.Button(label="💾 Guardar .lrc")
        btn_save.connect("clicked", self.on_save_lrc)
        bottom_bar.pack_start(btn_save, False, False, 0)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.set_text("Listo.")
        main_vbox.pack_start(self.status_label, False, False, 0)

        self.connect("key-press-event", self.on_window_key_press)
        self.connect("destroy", self.on_close)

        GLib.timeout_add(100, self.poll_player_time)

        if initial_file and os.path.isfile(initial_file):
            with open(initial_file, "r", encoding="utf-8") as f:
                self.load_lyrics_text(
                    f.read(), title_text=os.path.splitext(os.path.basename(initial_file))[0]
                )
        else:
            auto_json = self.find_auto_json()
            if auto_json:
                self.load_json_file(auto_json)
            else:
                self.song_combo.set_active(0)

    def find_auto_json(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        search_dirs = [
            os.getcwd(),
            script_dir,
            os.path.expanduser("~"),
            os.path.expanduser("~/Descargas"),
            os.path.expanduser("~/Downloads"),
            os.path.expanduser("~/Música"),
            os.path.expanduser("~/Music"),
        ]
        exact_names = ["gifts_album_yt.json", "gifts_album.json"]
        seen = set()
        for d in search_dirs:
            norm_d = os.path.abspath(d)
            if norm_d in seen or not os.path.isdir(norm_d):
                continue
            seen.add(norm_d)
            for name in exact_names:
                candidate = os.path.join(norm_d, name)
                if os.path.isfile(candidate):
                    return candidate

        for d in seen:
            for pattern in ("*gifts*.json", "*.json"):
                for path in sorted(glob.glob(os.path.join(d, pattern))):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                            if "title" in data[0] and ("audio" in data[0] or "lyrics" in data[0]):
                                return path
                    except Exception:
                        continue
        return None

    def load_json_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list) and len(loaded) > 0:
                self.songs = loaded
                yt_count = sum(1 for s in self.songs if extract_youtube_url(s))
                self.populate_song_combo()
                self.song_combo.set_active(0)
                self.set_status(
                    f"Cargadas {len(self.songs)} canciones ({yt_count} con YouTube) desde {path}"
                )
                return True
        except Exception as e:
            self.set_status(f"⚠️ Error al leer {path}: {e}")
        return False

    def set_status(self, msg):
        self.status_label.set_text(msg)

    def populate_song_combo(self):
        self.song_combo.remove_all()
        for idx, s in enumerate(self.songs):
            title = s.get("title") or f"Pista {idx + 1}"
            tag = s.get("tag") or ""
            label = f"{idx + 1:03d} - {title} {tag}".strip()
            self.song_combo.append_text(label)

    def on_song_changed(self, combo):
        idx = combo.get_active()
        if idx < 0 or idx >= len(self.songs):
            return
        self.current_song = self.songs[idx]
        self.current_yt_url = extract_youtube_url(self.current_song)
        self.stop_mpv()
        self.current_time = 0.0
        self.is_paused = False
        self.update_time_display()
        title = (self.current_song.get("title") or "").strip()
        self.load_lyrics_text(self.current_song.get("lyrics", ""), title_text=title)
        yt_msg = f"YouTube: {self.current_yt_url}" if self.current_yt_url else "Sin link de YouTube"
        self.set_status(
            f"Pista {idx + 1}/{len(self.songs)}: {title} ({yt_msg}) — Pulsa Reproducir/Pausa."
        )

    def start_current_song_audio(self):
        url = (self.current_song or {}).get("audio", "").strip()
        if not url:
            self.set_status("⚠️ Esta pista no tiene URL de audio.")
            return
        if not shutil.which("mpv"):
            self.set_status("⚠️ Instala 'mpv' para reproducir streams .m3u8.")
            return

        self.stop_mpv()
        if os.path.exists(IPC_SOCKET):
            try:
                os.remove(IPC_SOCKET)
            except OSError:
                pass

        try:
            self.mpv_proc = subprocess.Popen(
                [
                    "mpv",
                    "--no-video",
                    "--cache=yes",
                    f"--http-header-fields={REFERER_HEADER}",
                    "--really-quiet",
                    f"--input-ipc-server={IPC_SOCKET}",
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.is_paused = False
            self.btn_play.set_label("⏸ Pausar / Reanudar")
            self.set_status(f"Reproduciendo con caché: {self.current_song.get('title', '')}")
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
        self.is_paused = False
        self.btn_play.set_label("▶ Reproducir / Pausa")
        if os.path.exists(IPC_SOCKET):
            try:
                os.remove(IPC_SOCKET)
            except OSError:
                pass

    def on_close(self, *_):
        self.stop_mpv()
        Gtk.main_quit()

    def on_load_json(self, *_):
        fc = Gtk.FileChooserDialog(
            title="Selecciona gifts_album.json",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        fc.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if fc.run() == Gtk.ResponseType.OK:
            self.load_json_file(fc.get_filename())
        fc.destroy()

    def load_lyrics_text(self, text, title_text=""):
        self.pending_store.clear()
        self.done_store.clear()
        self.done_orig_indices.clear()
        self.selected_done_index = None
        lines = split_lyrics_into_lines(text)
        clean_title = re.sub(r"\s+", " ", (title_text or "")).strip()
        if clean_title:
            self.done_store.append([1.00, "[00:01:00]", f"Título de la canción: {clean_title}"])
            self.done_orig_indices.append(None)
        for line in lines:
            self.pending_store.append([line])
        self.pending_header.set_markup(f"<b>Pendientes ({len(lines)} líneas — primera seleccionada)</b>")
        self.select_pending_at(0)

    def select_pending_at(self, idx):
        total = len(self.pending_store)
        if total <= 0:
            return
        clamped = max(0, min(idx, total - 1))
        path = Gtk.TreePath.new_from_indices([clamped])
        self.pending_view.get_selection().select_path(path)
        self.pending_view.scroll_to_cell(path, None, False, 0, 0)

    def select_first_pending(self):
        self.select_pending_at(0)

    def get_full_entry_text(self):
        """Reconstruye el texto completo de la entrada actual (líneas ya estampadas + pendientes)."""
        collected = []
        for idx, row in enumerate(self.done_store):
            orig = self.done_orig_indices[idx] if idx < len(self.done_orig_indices) else None
            if orig is not None:
                collected.append((orig, row[2]))
        pending_lines = [row[0] for row in self.pending_store]

        # Si hay índices originales, ordenamos las ya estampadas según su orden original y mezclamos
        if collected:
            done_map = {orig: phrase for orig, phrase in collected}
            total_slots = max(max(done_map.keys()) + 1, len(done_map) + len(pending_lines))
            merged = []
            p_iter = iter(pending_lines)
            for i in range(total_slots):
                if i in done_map:
                    merged.append(done_map[i])
                else:
                    nxt = next(p_iter, None)
                    if nxt is not None:
                        merged.append(nxt)
            merged.extend(p_iter)
            return "\n".join(merged)

        return "\n".join(pending_lines)

    def on_edit_entry(self, *_):
        current_full_text = self.get_full_entry_text()
        song_title = (self.current_song or {}).get("title") or "Entrada"

        dialog = Gtk.Dialog(
            title=f"Editar entrada — {song_title}",
            transient_for=self,
            flags=0,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_size(640, 440)
        box = dialog.get_content_area()

        lbl = Gtk.Label(label="Edita toda la letra de esta entrada (una línea por frase):", xalign=0)
        box.pack_start(lbl, False, False, 4)

        scroll = Gtk.ScrolledWindow()
        textview = Gtk.TextView()
        textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        textview.get_buffer().set_text(current_full_text)
        scroll.add(textview)
        box.pack_start(scroll, True, True, 4)
        dialog.show_all()

        resp = dialog.run()
        buf = textview.get_buffer()
        new_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        dialog.destroy()

        if resp == Gtk.ResponseType.OK:
            if self.current_song is not None:
                self.current_song["lyrics"] = new_text
            title_clean = (self.current_song or {}).get("title", "")
            self.load_lyrics_text(new_text, title_text=title_clean)
            self.set_status("Entrada completa actualizada.")

    def poll_player_time(self):
        if self.drag_start_x is not None:
            return True

        if self.mpv_proc is not None:
            rc = self.mpv_proc.poll()
            if rc is not None:
                self.mpv_proc = None
                self.btn_play.set_label("▶ Reproducir / Pausa")
                if rc == 0:
                    self.set_status("Reproducción finalizada.")
                else:
                    self.set_status(f"⚠️ mpv se detuvo con código {rc}.")
                return True

            pos = mpv_ipc(["get_property", "time-pos"])
            if isinstance(pos, (int, float)):
                self.current_time = float(pos)
                self.update_time_display()
        return True

    def update_time_display(self):
        ts_str = format_lrc_time(self.current_time)
        self.time_label.set_markup(
            f"<span size='x-large' weight='bold' font_family='monospace'>{ts_str}</span>"
        )

    def on_play_pause(self, *_):
        if self.mpv_proc is not None and self.mpv_proc.poll() is None:
            paused = mpv_ipc(["get_property", "pause"])
            new_paused = not bool(paused)
            mpv_ipc(["set_property", "pause", new_paused])
            self.is_paused = new_paused
            self.set_status("Pausado" if new_paused else "Reproduciendo")
        else:
            self.start_current_song_audio()

    def on_stamp_line(self, *_):
        selection = self.pending_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter is None and len(self.pending_store) > 0:
            treeiter = self.pending_store.get_iter_first()
        if treeiter is None:
            return

        current_idx = model.get_path(treeiter).get_indices()[0]
        line_text = model[treeiter][0]
        ts = self.current_time
        self.done_store.append([ts, f"[{format_lrc_time(ts)}]", line_text])
        self.done_orig_indices.append(current_idx)

        last_idx = len(self.done_store) - 1
        last_path = Gtk.TreePath.new_from_indices([last_idx])
        self.done_view.get_selection().select_path(last_path)
        self.done_view.scroll_to_cell(last_path, None, False, 0, 0)

        model.remove(treeiter)
        self.pending_header.set_markup(f"<b>Pendientes ({len(self.pending_store)} líneas)</b>")
        self.select_pending_at(current_idx)

    def on_done_selection_changed(self, selection):
        model, treeiter = selection.get_selected()
        if treeiter is not None:
            self.selected_done_index = model.get_path(treeiter).get_indices()[0]
        else:
            self.selected_done_index = None

    def adjust_time(self, delta):
        if self.selected_done_index is not None and 0 <= self.selected_done_index < len(self.done_store):
            row = self.done_store[self.selected_done_index]
            new_ts = max(0.0, row[0] + delta)
            row[0] = new_ts
            row[1] = f"[{format_lrc_time(new_ts)}]"
            self.set_status(f"Ajustada línea {self.selected_done_index + 1} a {row[1]}")
        else:
            self.current_time = max(0.0, self.current_time + delta)
            self.update_time_display()
            if self.mpv_proc is not None and self.mpv_proc.poll() is None:
                mpv_ipc(["seek", f"{self.current_time:.2f}", "absolute"])

    def on_time_press(self, widget, event):
        widget.grab_focus()
        if event.button == 1:
            self.drag_start_x = event.x
            if self.selected_done_index is not None and 0 <= self.selected_done_index < len(self.done_store):
                self.drag_start_time = self.done_store[self.selected_done_index][0]
            else:
                self.drag_start_time = self.current_time
        return True

    def on_time_drag(self, widget, event):
        if self.drag_start_x is not None:
            dx = event.x - self.drag_start_x
            delta = (dx / 10.0) * 0.05
            new_t = max(0.0, self.drag_start_time + delta)
            if self.selected_done_index is not None and 0 <= self.selected_done_index < len(self.done_store):
                row = self.done_store[self.selected_done_index]
                row[0] = new_t
                row[1] = f"[{format_lrc_time(new_t)}]"
            else:
                self.current_time = new_t
                self.update_time_display()
        return True

    def on_time_release(self, widget, event):
        if self.drag_start_x is not None:
            self.drag_start_x = None
            if self.selected_done_index is None and self.mpv_proc is not None and self.mpv_proc.poll() is None:
                mpv_ipc(["seek", f"{self.current_time:.2f}", "absolute"])
        return True

    def on_time_key(self, widget, event):
        if event.keyval == Gdk.KEY_Left:
            self.adjust_time(-0.05)
            return True
        if event.keyval == Gdk.KEY_Right:
            self.adjust_time(0.05)
            return True
        return False

    def on_window_key_press(self, widget, event):
        if event.keyval in (Gdk.KEY_AudioPlay, Gdk.KEY_AudioPause):
            self.on_play_pause()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not self.pending_view.has_focus():
                self.on_stamp_line()
                return True
        if event.keyval == Gdk.KEY_Left:
            self.adjust_time(-0.05)
            return True
        if event.keyval == Gdk.KEY_Right:
            self.adjust_time(0.05)
            return True
        return False

    def on_undo_last(self, *_):
        if len(self.done_store) == 0:
            return
        last_idx = len(self.done_store) - 1
        last_iter = self.done_store.get_iter(Gtk.TreePath.new_from_indices([last_idx]))
        text = self.done_store[last_iter][2]
        orig_idx = self.done_orig_indices.pop() if last_idx < len(self.done_orig_indices) else None

        self.done_store.remove(last_iter)

        target_pos = len(self.pending_store)
        if orig_idx is not None:
            target_pos = max(0, min(orig_idx, len(self.pending_store)))

        if target_pos >= len(self.pending_store):
            self.pending_store.append([text])
            inserted_idx = len(self.pending_store) - 1
        else:
            ref_iter = self.pending_store.get_iter(Gtk.TreePath.new_from_indices([target_pos]))
            self.pending_store.insert_before(ref_iter, [text])
            inserted_idx = target_pos

        self.pending_header.set_markup(f"<b>Pendientes ({len(self.pending_store)} líneas)</b>")
        self.select_pending_at(inserted_idx)

    def on_load_dialog(self, *_):
        dialog = Gtk.Dialog(title="Pegar o abrir letra manual", transient_for=self, flags=0)
        dialog.add_buttons(
            "Abrir .txt", 1,
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_size(560, 380)
        box = dialog.get_content_area()
        scroll = Gtk.ScrolledWindow()
        textview = Gtk.TextView()
        textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll.add(textview)
        box.pack_start(scroll, True, True, 6)
        dialog.show_all()

        response = dialog.run()
        if response == 1:
            dialog.destroy()
            fc = Gtk.FileChooserDialog(
                title="Selecciona archivo de letra",
                parent=self,
                action=Gtk.FileChooserAction.OPEN,
            )
            fc.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
            if fc.run() == Gtk.ResponseType.OK:
                path = fc.get_filename()
                with open(path, "r", encoding="utf-8") as f:
                    self.load_lyrics_text(f.read(), title_text=os.path.splitext(os.path.basename(path))[0])
            fc.destroy()
            return
        elif response == Gtk.ResponseType.OK:
            buf = textview.get_buffer()
            txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
            if txt.strip():
                title = (self.current_song or {}).get("title", "")
                self.load_lyrics_text(txt, title_text=title)
        dialog.destroy()

    def on_save_lrc(self, *_):
        music_dir = os.path.expanduser("~/Música")
        title = (self.current_song or {}).get("title") or "cancion"
        tag = (self.current_song or {}).get("tag") or "Gifts Album"
        safe_artist = "Prem Rawat"
        safe_album = re.sub(r'[\\/:*?"<>|]', "", tag).strip() or "Gifts Album"
        safe_title = re.sub(r'[\\/:*?"<>|]', "", title).strip() or "cancion"

        target_dir = os.path.join(music_dir, "Lyrics", safe_artist, safe_album)
        os.makedirs(target_dir, exist_ok=True)
        default_path = os.path.join(target_dir, f"{safe_artist} - {safe_title}.lrc")

        fc = Gtk.FileChooserDialog(
            title="Guardar archivo .lrc",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        fc.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        fc.set_do_overwrite_confirmation(True)
        fc.set_current_folder(target_dir)
        fc.set_current_name(os.path.basename(default_path))

        response = None
        out_path = None
        try:
            response = fc.run()
            if response == Gtk.ResponseType.OK:
                out_path = fc.get_filename()
        finally:
            fc.destroy()

        if response == Gtk.ResponseType.OK and out_path:
            if not out_path.lower().endswith(".lrc"):
                out_path += ".lrc"
            with open(out_path, "w", encoding="utf-8") as f:
                if self.current_yt_url:
                    f.write(f"#Link a youtube: {self.current_yt_url}\n")
                for row in self.done_store:
                    f.write(f"{row[1]}{row[2]}\n")
            self.set_status(f"Guardado: {out_path}")


if __name__ == "__main__":
    arg_file = sys.argv[1] if len(sys.argv) > 1 else None
    win = LrcSyncWindow(arg_file)
    win.show_all()
    Gtk.main()
