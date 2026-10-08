#!/usr/bin/env bash
# 大会サーバから決着済み (success) のログと、対応する json を取得する。
#
#   data/input/<大会名>_<トラック名>/log/*.log
#   data/input/<大会名>_<トラック名>/json/*.json
#
#   使い方: scripts/fetch_logs.sh [-y 年] [-d 出力先] [大会名の一部 ...]
#   例:     scripts/fetch_logs.sh                 # 最新年度の全 MainTruck
#           scripts/fetch_logs.sh INLG            # INLG のものだけ
#           scripts/fetch_logs.sh -y 2025         # 年度を指定
#   環境変数:
#           AIWOLF_REMOTE=aiwolf                  # ~/.ssh/config の Host 名
#           AIWOLF_BASE=/var/www/html/aiwolf      # サーバ上の年度ディレクトリの親
#           INCLUDE_PRETRUCK=1                    # PreTruck も対象にする
#
# サーバ側は <年>/<大会>/<トラック>/{log,json}/ で、決着済みの log は log/success/ にある。
# json は log と同名で、json/ 直下か json/success/ のどちらかにある (トラックによって違う)。
# json はチーム名の確定と決着の検算に使うので必ず取る。

set -euo pipefail

REMOTE="${AIWOLF_REMOTE:-aiwolf}"
BASE="${AIWOLF_BASE:-/var/www/html/aiwolf}"
YEAR=""
DEST="$(pwd)/data/input"

while [ "$#" -gt 0 ]; do
    case "$1" in
        -y|--year) YEAR="${2:?-y には年度を指定してください}"; shift 2 ;;
        -d|--dest) DEST="${2:?-d には出力先を指定してください}"; shift 2 ;;
        -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) break ;;
    esac
done

if [ -z "$YEAR" ]; then
    YEAR="$(ssh "$REMOTE" "ls -1 '$BASE' 2>/dev/null" | grep -E '^[0-9]{4}$' | sort -n | tail -1 || true)"
    [ -n "$YEAR" ] || { echo "$BASE に年度ディレクトリが見つかりません" >&2; exit 1; }
    echo "年度: $YEAR (自動判別)"
else
    echo "年度: $YEAR (指定)"
fi

ROOT="$BASE/$YEAR"
echo "リモート: $REMOTE:$ROOT"
echo "出力先:   $DEST"
mapfile -t SUCCESS_DIRS < <(
    ssh "$REMOTE" "find '$ROOT' -maxdepth 4 -type d -path '*/log/success' 2>/dev/null" | sort)
[ "${#SUCCESS_DIRS[@]}" -gt 0 ] || { echo "success ディレクトリが見つかりませんでした" >&2; exit 1; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

for sd in "${SUCCESS_DIRS[@]}"; do
    rel="${sd#"$ROOT"/}"          # INLG/MainTruck5/log/success
    tournament="${rel%%/*}"
    rest="${rel#*/}"
    track="${rest%%/*}"
    dataset="${tournament}_${track}"
    case "$track" in PreTruck*) [ "${INCLUDE_PRETRUCK:-0}" = 1 ] || continue ;; esac
    if [ "$#" -gt 0 ]; then
        matched=0
        for pat in "$@"; do case "$dataset" in *"$pat"*) matched=1 ;; esac; done
        [ "$matched" = 1 ] || continue
    fi

    echo
    echo "=== $dataset ==="
    mkdir -p "$DEST/$dataset/log" "$DEST/$dataset/json"
    # success/timestamp/ は同一ゲームの複製なので取らない
    rsync -a --delete --exclude='timestamp/' "$REMOTE:$sd/" "$DEST/$dataset/log/"
    n_log=$(find "$DEST/$dataset/log" -maxdepth 1 -name '*.log' | wc -l)
    echo "  log : $n_log 件"

    # json は log と同名のものだけ。json/ 直下と json/success/ の両方を見る
    ( cd "$DEST/$dataset/log" && ls -1 ./*.log 2>/dev/null ) | sed 's|^\./||; s|\.log$|.json|' > "$tmp"
    for jd in json json/success; do
        rsync -a --files-from="$tmp" "$REMOTE:$ROOT/$tournament/$track/$jd/" "$DEST/$dataset/json/" 2>/dev/null || true
    done
    n_json=$(find "$DEST/$dataset/json" -maxdepth 1 -name '*.json' | wc -l)
    echo "  json: $n_json 件"
    [ "$n_log" -eq "$n_json" ] || echo "  ※ log と json の件数が一致しません。json の無い試合は check で報告されます"
done

echo
echo "完了: $DEST"
