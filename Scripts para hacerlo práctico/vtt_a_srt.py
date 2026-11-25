import html
import re
import sys
import unicodedata


def parse_line(line):
    m = re.match(r"^\s*\[(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\]\s*(.+?)\s*$", line)
    if not m:
        return None
    mins = int(m.group(1))
    secs = int(m.group(2))
    ms_raw = (m.group(3) or "0").ljust(3, "0")[:3]
    ms = int(ms_raw)
    t = mins * 60.0 + secs + ms / 1000.0
    txt = re.sub(r"<[^>]+>", "", m.group(4))
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt:
        return None
    return t, txt


def norm_key(txt):
    s = txt.replace("…", "...")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\.+", ".", s)
    s = re.sub(r"[^\w\s]", "", s).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fmt_srt(t):
    t = max(0.0, t)
    h = int(t // 3600)
    rem = t - h * 3600
    m = int(rem // 60)
    rem = rem - m * 60
    s = int(rem)
    ms = int(round((rem - s) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    if s == 60:
        m += 1
        s = 0
    if m == 60:
        h += 1
        m = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def read_text_with_fallback(path):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(), "utf-8 (replace)"


def main():
    if len(sys.argv) < 3:
        print("Uso: python3 vtt_a_srt.py entrada.txt salida.srt", file=sys.stderr)
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    content, _ = read_text_with_fallback(in_path)

    items = []
    for line in content.splitlines():
        parsed = parse_line(line)
        if parsed:
            items.append(parsed)

    items.sort(key=lambda x: x[0])

    merged = []
    for t, txt in items:
        key = norm_key(txt)
        if merged and merged[-1]["key"] == key and (t - merged[-1]["last_t"]) <= 4.0:
            merged[-1]["last_t"] = max(merged[-1]["last_t"], t)
            if len(txt) > len(merged[-1]["txt"]):
                merged[-1]["txt"] = txt
        else:
            merged.append({
                "start": t,
                "last_t": t,
                "txt": txt,
                "key": key,
            })

    with open(out_path, "w", encoding="utf-8", newline="\n") as out:
        idx = 1
        for i, entry in enumerate(merged):
            start_t = entry["start"]
            if i + 1 < len(merged):
                next_start = merged[i + 1]["start"]
                end_t = min(next_start, max(entry["last_t"] + 1.2, start_t + 1.5))
                if end_t <= start_t:
                    end_t = next_start
            else:
                end_t = max(entry["last_t"] + 2.0, start_t + 2.5)

            if end_t <= start_t:
                end_t = start_t + 1.5

            out.write(f"{idx}\n{fmt_srt(start_t)} --> {fmt_srt(end_t)}\n{entry['txt']}\n\n")
            idx += 1


if __name__ == "__main__":
    main()
