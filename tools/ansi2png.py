#!/home/juryorca/ENTER/envs/pwn/bin/python
"""Faithful ANSI (tmux capture-pane -e) -> terminal-look PNG via chromium headless.

The window width is computed from the longest visible line so the terminal
background fills the image edge-to-edge (no bands); body and terminal share the
same background color, making rounding leftovers invisible.
"""
import html
import math
import os
import re
import subprocess
import sys
import tempfile

BASIC = ["#000000","#cd3131","#0dbc79","#e5e510","#2472c8","#bc3fbc","#11a8cd","#e5e510","#666666","#f14c4c","#23d18b","#f5f543","#3b8eea","#d670d6","#29b8db","#f5f543"]
def xterm(n):
    if n < 16: return BASIC[n]
    if n < 232:
        n -= 16; r=n//36; g=(n%36)//6; b=n%6
        v=[0,95,135,175,215,255]
        return f"#{v[r]:02x}{v[g]:02x}{v[b]:02x}"
    n -= 232; c=8+n*10
    return f"#{c:02x}{c:02x}{c:02x}"

def ansi_to_html(raw):
    out, fg, bg, bold = [], None, None, False
    i = 0
    def span(s):
        if not s: return
        st = []
        if fg: st.append(f"color:{fg}")
        if bg: st.append(f"background:{bg}")
        if bold: st.append("font-weight:bold")
        out.append(f'<span style="{";".join(st)}">{html.escape(s)}</span>' if st else html.escape(s))
    while i < len(raw):
        c = raw[i]
        if c == "\x1b" and i+1 < len(raw) and raw[i+1] == "[":
            m = re.match(r"\x1b\[([0-9;?]*)([A-Za-z])", raw[i:])
            if m:
                params, cmd = m.group(1), m.group(2)
                if cmd == "m":
                    nums = [int(x) for x in params.split(";") if x] or [0]
                    k = 0
                    while k < len(nums):
                        n = nums[k]
                        if n == 0: fg=bg=None; bold=False
                        elif n == 1: bold=True
                        elif n == 22: bold=False
                        elif 30 <= n <= 37: fg=BASIC[n-30]
                        elif n == 39: fg=None
                        elif 40 <= n <= 47: bg=BASIC[n-40]
                        elif n == 49: bg=None
                        elif 90 <= n <= 97: fg=BASIC[n-90+8]
                        elif 100 <= n <= 107: bg=BASIC[n-100+8]
                        elif n in (38,48):
                            tgt = "fg" if n==38 else "bg"
                            if k+2 < len(nums) and nums[k+1]==5:
                                col = xterm(nums[k+2]); k += 2
                                if tgt=="fg": fg=col
                                else: bg=col
                        k += 1
                i += m.end(); continue
            i += 1; continue
        if c == "\r": i += 1; continue
        span(c); i += 1
    return "".join(out)

# DejaVu Sans Mono advance at 15px = 15 * 1233/2048 ~= 9.03px
CHAR_W = 15 * 1233 / 2048
PAD_X = 26
PAD_Y = 20

def main(ansi_path, png_path):
    with open(ansi_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    raw = raw.rstrip("\n")
    lines = raw.count("\n") + 1
    stripped = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw)
    max_len = max((len(l.rstrip()) for l in stripped.split("\n")), default=0)
    width = min(1920, max(900, math.ceil(max_len * CHAR_W) + PAD_X * 2 + 8))
    height = max(2, lines) * 21 + PAD_Y * 2
    body = ansi_to_html(raw)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html,body {{ margin:0; padding:0; background:#1b1b1b; }}
      pre {{ font: 15px/21px "DejaVu Sans Mono", monospace; color:#cccccc;
            background:#1b1b1b; margin:0; padding:{PAD_Y}px {PAD_X}px;
            width:{width}px; box-sizing:border-box; }}
    </style></head><body><pre>{body}</pre></body></html>"""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(page); tmp = f.name
    subprocess.run(["chromium","--headless=new","--disable-gpu","--no-sandbox",
                    "--hide-scrollbars",f"--window-size={width},{height}",
                    f"--screenshot={png_path}", tmp], check=True,
                   capture_output=True, timeout=120)
    os.unlink(tmp)
    print(f"[+] {png_path} ({lines} lines, max {max_len} cols -> {width}x{height})")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
