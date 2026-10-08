"""大会サーバの横に常駐して、ゲームが1本終わるたびに指標を出し直す。

1周ごとにやること:

    1. log/ を見て、完了したゲームの log と json を success/ へコピー（元は消さない）
    2. success/ のうち、まだ集計していないゲームだけをパースして seat 行を追記
    3. チーム別の指標表を日本語版(ja.txt)と英語版(en.txt)に書き出す

出力はこの2つのテキストだけ。出力先は web で配信されるディレクトリなので、そこで
読めない CSV は置かない。機械可読なものが要るときは、ログを持ち帰って
`aiwolf-nlp-calculate-score run` を回す。中身の数値は言語によらず同じで、見出しと説明文だけが変わる。

状態はプロセスのメモリだけに持ち、起動時に success/ を全部読み直す。245ゲームでも
0.57秒なので、状態ファイルを置いて古くなる危険を抱えるより読み直す方が安全。

    uv run src/main.py live -c freeform_en_5.yml            # 常駐
    uv run src/main.py live -c freeform_en_5.yml --once     # 1回だけ

サーバの設定ファイルがあるディレクトリで
実行すること（サーバ自身も相対パスをカレント基準で解決するため）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..metrics import build_seat_table
from ..sort_success import sort_once
from .completion import finalize_all_status, read_completion, shutdown_track
from .config import LiveSettings
from .report import build_seats, planned_counts, render_text, role_win_table, team_table
from .strings import LANGUAGES


def write_atomic(path: Path, text: str) -> None:
    """一時ファイル経由で差し替える。読み手が半端な内容を掴まないようにする。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _empty_seats() -> pd.DataFrame:
    """列だけある空の seat テーブル。1ゲームも無くても集計が通るようにする。"""
    seats, _hazard = build_seat_table([])
    return seats


class LiveRunner:
    def __init__(self, st: LiveSettings, do_sort: bool = True, teams: list[str] | None = None):
        self.st = st
        self.teams = teams
        self.do_sort = do_sort and st.sort_success
        self.text_paths = {lang: st.output_dir / name
                           for lang, name in LANGUAGES.items()}
        self.seats = _empty_seats()
        self.skipped: list[str] = []
        self.first_pass = True
        self.completion = None
        # 完了判定が連続して成立した回数。1周期のブレで止めないための保険。
        self.finished_streak = 0
        # 確定したゲームの判定結果。毎周期 json を開き直さないためのキャッシュ。
        self.classify_cache: dict = {}

    def _known_games(self) -> set[str]:
        if self.seats.empty or "game_id" not in self.seats.columns:
            return set()
        return set(self.seats["game_id"].astype(str))

    def tick(self) -> bool:
        """1周。出力を書き換えたら True。"""
        st = self.st
        sort_stats = None
        if self.do_sort:
            # success の検算は起動時だけでよい（毎周期やると全ファイルを読み直す）
            sort_stats = sort_once(
                st.log_dir, st.json_dir if st.json_enabled else None,
                cache=self.classify_cache, check_stale=self.first_pass)

        # success に居るゲームだけを集計対象にする。振り分けを止めていても、
        # 既存の success をそのまま読むので手動運用と併用できる。
        src_dir = st.success_log_dir if st.success_log_dir.is_dir() else st.log_dir
        known = self._known_games()
        new_paths = [p for p in sorted(src_dir.glob("*.log")) if p.stem not in known]

        if new_paths:
            json_dir = st.json_dir if st.json_enabled and st.json_dir.is_dir() else None
            fresh, skipped, issues = build_seats(new_paths, st.dataset, json_dir, self.teams)
            self.skipped.extend(skipped)
            for i in issues:
                print(f"  {i}", file=sys.stderr)
            if not fresh.empty:
                self.seats = (fresh if self.seats.empty
                              else pd.concat([self.seats, fresh], ignore_index=True))

        # 完了判定は毎周期やる。新規ゲームが無い周期こそ「終わった」瞬間なので、
        # 早期 return する前に見る必要がある。
        in_progress = sort_stats.pending if sort_stats is not None else 0
        self.completion = read_completion(st.optimizer_path, in_progress)
        self.finished_streak = self.finished_streak + 1 if self.completion.finished else 0

        if not new_paths and not self.first_pass and self.finished_streak != 1:
            return False

        progress = planned_counts(st.optimizer_path)
        table = team_table(self.seats, progress)

        st.output_dir.mkdir(parents=True, exist_ok=True)
        roles = role_win_table(self.seats, list(table["team"]))
        for lang, path in self.text_paths.items():
            write_atomic(path, render_text(
                table, dataset=st.dataset, completion=self.completion,
                role_table=roles, lang=lang))

        self._report_to_operator(sort_stats, table)
        self.first_pass = False
        n_games = self.seats["game_id"].nunique() if len(self.seats) else 0
        print(f"更新: {n_games}ゲーム / {len(self.seats)}seat"
              + (f" (新規 {len(new_paths)}件)" if new_paths else ""), file=sys.stderr)
        return True

    def _report_to_operator(self, sort_stats, table: pd.DataFrame) -> None:
        """運営だけが必要な内訳と警告。metrics.txt は参加者が見るので出さない。"""
        if sort_stats is not None:
            print(f"  成立 {sort_stats.success} / 不成立 {sort_stats.failed} / "
                  f"進行中 {sort_stats.pending}", file=sys.stderr)
            if sort_stats.broken:
                print(f"  [警告] json では決着しているのに log に result 行が無い: "
                      f"{len(sort_stats.broken)}件（指標に使えないので運んでいません）",
                      file=sys.stderr)
            if sort_stats.stale_in_success:
                print(f"  [警告] success に成立していないファイルが "
                      f"{len(sort_stats.stale_in_success)}件混ざっています", file=sys.stderr)
        if self.skipped:
            print(f"  [警告] 読めずに除外したゲーム {len(self.skipped)}件", file=sys.stderr)
        # 消化(予定表側)と集計(ログ側)がずれたらログの取りこぼし。参加者には出さない。
        if {"消化", "ゲーム数"} <= set(table.columns):
            gap = table[table["消化"].fillna(0) != table["ゲーム数"].fillna(0)]
            if not gap.empty:
                print(f"  [警告] 消化と集計がずれているチーム: "
                      f"{', '.join(gap['team'].astype(str))}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aiwolf-nlp-calculate-score live", description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--config", default="freeform_en_5.yml",
                    help="サーバと同じ設定ファイル")
    ap.add_argument("--once", action="store_true", help="1回だけ処理して終了する")
    ap.add_argument("--interval", type=float, default=None, help="更新間隔[秒]を上書き")
    ap.add_argument("--no-sort", action="store_true",
                    help="success への振り分けをせず、既存の success だけを読む")
    ap.add_argument("--output-dir", type=Path, default=None, help="出力先を上書き")
    ap.add_argument("--stop-when-done", action="store_true",
                    help="全試合が終わったら監視スクリプトとサーバを止めて自分も終了する")
    ap.add_argument("--stop-confirmations", type=int, default=2,
                    help="完了と判定するのに必要な連続回数 (既定 2)")
    ap.add_argument("--dry-run-stop", action="store_true",
                    help="--stop-when-done の停止対象を表示するだけで実際には止めない")
    ap.add_argument("--teams", type=Path, default=None, help="チーム名一覧 (teams.yml)。json が無いときに使う")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"設定ファイルが見つかりません: {config_path}", file=sys.stderr)
        candidates = sorted(p.name for p in Path(".").glob("*.yml"))
        if candidates:
            print("候補: " + ", ".join(candidates), file=sys.stderr)
        return 1

    st = LiveSettings(config_path)
    if args.interval is not None:
        st.interval = args.interval
    if args.output_dir is not None:
        st.output_dir = args.output_dir

    teams = None
    if args.teams is not None:
        from ..cli import _read_teams
        teams = _read_teams(args.teams)
    runner = LiveRunner(st, do_sort=not args.no_sort, teams=teams)

    print(f"対象     : {st.dataset}", file=sys.stderr)
    print(f"log      : {st.log_dir}", file=sys.stderr)
    print(f"json     : {st.json_dir if st.json_enabled else '(無効)'}", file=sys.stderr)
    print(f"出力     : {st.output_dir}", file=sys.stderr)
    print(f"振り分け : {'する' if runner.do_sort else 'しない'}", file=sys.stderr)
    if args.stop_when_done:
        print(f"自動停止 : 全試合終了を{args.stop_confirmations}回連続で確認したら"
              f"監視スクリプトとサーバを止めます", file=sys.stderr)

    while True:
        try:
            runner.tick()
        except KeyboardInterrupt:
            print("\n終了します", file=sys.stderr)
            return 0
        except Exception as e:  # 監視自体は落とさない
            print(f"エラー: {e!r}", file=sys.stderr)

        if args.stop_when_done and runner.finished_streak >= args.stop_confirmations:
            # 指標はこの周期で既に書き終わっている（【完了】入り）。
            # 監視スクリプトを先に、サーバを後に止める。逆にすると status.txt が
            # 「応答なし」で凍り、終わったのか落ちたのかが読み手に分からなくなる。
            print(f"\n全試合終了を確認しました: {runner.completion.describe()}",
                  file=sys.stderr)
            ok = shutdown_track(config_path.name, dry_run=args.dry_run_stop,
                                log=lambda m: print(m, file=sys.stderr))
            if args.dry_run_stop:
                print("--dry-run-stop のため自分は動き続けます", file=sys.stderr)
            else:
                # 凍結した status.txt に「意図して止めた」ことを書き残す。
                # 監視を先に止めた都合で「状態: 稼働中」のまま固まるため。
                if ok and st.status_path is not None:
                    stamp = f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}"
                    for done in finalize_all_status(st.status_path, stamp):
                        print(f"  {done.name} を停止済みの内容に書き換えました",
                              file=sys.stderr)
                print("終了します", file=sys.stderr)
                return 0 if ok else 1

        if args.once:
            return 0
        try:
            time.sleep(st.interval)
        except KeyboardInterrupt:
            print("\n終了します", file=sys.stderr)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
