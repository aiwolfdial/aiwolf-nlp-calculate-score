"""サーバの設定ファイル(yml)から、逐次処理に必要なパスを読む。

`monitor_status.py` と同じ方針で、**サーバ本体にも設定ファイルの既存セクションにも
手を入れない**。yml に `live_metrics:` セクションを足すだけで動く
（サーバの設定読み込みは未知のキーを無視するので、同居させても起動に影響しない）。

    live_metrics:
      output_dir: /var/www/html/aiwolf/2026/INLG2/MainTruck_freeform5
      interval: 30          # 更新間隔[秒]。既定 30
      sort_success: true    # 完了ゲームを success へ逐次コピーするか。既定 true
      dataset: ""           # 出力に入れるデータセット名。空なら log の位置から組み立てる

パスは `output_dir` 以外すべてサーバの既存セクションから読む。二重管理を避けるため
`game_logger` / `json_logger` / `matching` の値をそのまま使う。
"""

from __future__ import annotations

from pathlib import Path

import yaml


class LiveSettings:
    """1トラック分の設定。"""

    def __init__(self, config_path: Path):
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        live = cfg.get("live_metrics") or {}
        game_logger = cfg.get("game_logger") or {}
        json_logger = cfg.get("json_logger") or {}
        matching = cfg.get("matching") or {}

        self.config_path = config_path
        self.raw = cfg

        # 相対パスは設定ファイルの位置ではなく実行時のカレントを基準にする。
        # サーバもカレント基準で解決するため、同じディレクトリで動かす前提に合わせる。
        self.log_dir = Path(game_logger.get("output_dir") or "./log")
        self.json_dir = Path(json_logger.get("output_dir") or "./json")
        self.json_enabled = bool(json_logger.get("enable", True))
        self.optimizer_path = Path(matching.get("output_path") or "./match_optimizer.json")

        # 停止時に「止めた」印を書き込む先。monitor_status.py と同じ設定を読む。
        mon = cfg.get("status_monitor") or {}
        self.status_path = Path(mon["output_path"]) if mon.get("output_path") else None

        self.output_dir = Path(live.get("output_dir") or self.log_dir.parent)
        self.interval = float(live.get("interval") or 30)
        self.sort_success = bool(live.get("sort_success", True))
        self.dataset = str(live.get("dataset") or "") or self._default_dataset()

    def _default_dataset(self) -> str:
        """log の置き場所から `<大会名>_<トラック名>` を組み立てる。

        `fetch_logs.sh` が data/input に掘るディレクトリ名と同じ規則にしてあるので、
        ここの出力をあとから `run` の集計と突き合わせられる。
        """
        track = self.log_dir.parent
        tournament = track.parent
        parts = [p.name for p in (tournament, track) if p.name not in ("", "/", ".")]
        return "_".join(parts) if parts else "live"

    @property
    def success_log_dir(self) -> Path:
        return self.log_dir / "success"

    @property
    def success_json_dir(self) -> Path:
        return self.json_dir / "success"
