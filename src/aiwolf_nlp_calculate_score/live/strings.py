"""live が参加者に見せる文言 (言語別)。表の中身は `report.TABLES` にあり、ここは見出しと進捗だけ。"""

from __future__ import annotations

LANGUAGES = {"ja": "metrics.ja.txt", "en": "metrics.en.txt"}

TEXT: dict[str, dict] = {
    "ja": {
        "title": "AIWolf ゲームスコア",
        "target": "【対象】",
        "finished_banner": "【完了】 このトラックは全試合を終了しました。以降このファイルは更新されません。",
        "empty": ["【チーム別 指標】", "  (まだ集計できるゲームがありません)"],
        "progress": {
            "unknown": "進捗は不明です",
            "finished": "全 {total} 試合が終了しました",
            "not_started": "まだ試合が行われていません（予定 {total} 試合）",
            "base": "{ended} / {total} 試合が終了（{pct:.1f}%）",
            "in_progress": "、進行中 {n} 試合",
            "remaining": "、残り {n} 試合",
        },
    },
    "en": {
        "title": "AIWolf Game Scores",
        "target": "[Track]",
        "finished_banner": "[DONE] Every game in this track has finished. This file will not be updated again.",
        "empty": ["[Metrics by team]", "  (no finished games to aggregate yet)"],
        "progress": {
            "unknown": "Progress unknown",
            "finished": "All {total} games have finished",
            "not_started": "No games have been played yet ({total} scheduled)",
            "base": "{ended} / {total} games finished ({pct:.1f}%)",
            "in_progress": ", {n} in progress",
            "remaining": ", {n} remaining",
        },
    },
}
