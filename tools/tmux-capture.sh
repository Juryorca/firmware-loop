#!/usr/bin/env bash
# 用法: tmux-capture.sh start|send <命令>|shot <输出文件>|info|stop
SESSION="fw"
case "$1" in
  start)
    tmux kill-session -t "$SESSION" 2>/dev/null
    tmux new-session -d -s "$SESSION" -x "${FW_TERM_COLS:-120}" -y "${FW_TERM_ROWS:-45}"
    tmux send-keys -t "$SESSION" 'clear' Enter
    sleep 0.5
    echo "[+] session '$SESSION' started (${FW_TERM_COLS:-120}x${FW_TERM_ROWS:-45}, cleared)"
    ;;
  send)
    shift
    tmux send-keys -t "$SESSION" "$*" Enter
    ;;
  shot)
    OUT="${2:-screenshot.png}"
    tmux capture-pane -t "$SESSION" -e -p > "$OUT"
    echo "[+] captured: $OUT ($(wc -c < "$OUT") bytes)"
    ;;
  info)
    tmux capture-pane -t "$SESSION" -p
    ;;
  stop)
    tmux send-keys -t "$SESSION" 'exit' Enter 2>/dev/null
    sleep 1
    tmux kill-session -t "$SESSION" 2>/dev/null
    echo "[+] session '$SESSION' closed"
    ;;
  *)
    echo "用法: $0 start|send <cmd>|shot <out>|info|stop"
    ;;
esac
