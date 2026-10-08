#!/usr/bin/env bash
# 大会サーバ上で、トラックごとの live 配信を tmux の常駐ウィンドウに立てる (運営用)。
#
#   <リポジトリ>/scripts/start_live.sh default_en_5.yml default_en_9.yml freeform_en_5.yml
#   TMUX_SESSION=foo scripts/start_live.sh ...
#
# サーバの設定ファイルがあるディレクトリで実行する。冪等 (同名ウィンドウがあれば作らない)。
# nohup ではなく tmux を使うのは、ログアウトで落ちる環境があるため。
# サーバ本体の起動はしない (起動タイミングは人が決める)。

set -euo pipefail
[ "$#" -gt 0 ] || { echo "使い方: $0 <サーバ設定.yml> ..." >&2; exit 1; }
SESSION="${TMUX_SESSION:-aiwolf}"
HERE="$(pwd)"                                                     # サーバ設定のあるディレクトリ
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"            # このリポジトリ

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -c "$HERE"
    echo "セッション $SESSION を作成しました"
fi
existing="$(tmux list-windows -t "$SESSION" -F '#{window_name}')"
for cfg in "$@"; do
    name="live-$(basename "$cfg" .yml)"
    cmd="uv run --project '$REPO' python '$REPO/src/main.py' live -c $cfg --stop-when-done"
    if grep -qx "$name" <<<"$existing"; then
        echo "$name: 既にあります（そのまま）"
        continue
    fi
    tmux new-window -d -t "$SESSION" -n "$name" -c "$HERE"
    tmux send-keys -t "$SESSION:$name" "$cmd" Enter
    echo "$name: 起動しました  ($cmd)"
done
tmux list-windows -t "$SESSION" -F '  #{window_index}: #{window_name}  [#{pane_current_command}]'
