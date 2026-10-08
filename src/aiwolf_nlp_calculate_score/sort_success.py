"""決着したゲームの log と json を success/ へ振り分ける (コピーのみ。元ファイルは消さない)。

`aiwolf-nlp-calculate-score prepare` と live の両方がこれを使う。

## 何を「終わった」とみなすか

**json の `win_side` が唯一の完全な判定材料**。log の `result` 行だけでは足りない。

実データ (INLG2/MainTruck5, 566ゲーム) で突き合わせた結果:

    log の result 行 : 成立 245 / NONE 319 / result 行なし 2
    json の win_side : 成立 245 (VILLAGER 137 + WEREWOLF 108) / NONE 321

**json 側は 566件すべてに勝敗が入っており、取りこぼしが無い。** log に `result` 行が
無い2件は、json では両方 `win_side: NONE` すなわち **不成立が確定したゲーム** だった。
つまりこの2件は「進行中」ではなく「中断」であり、log だけを見ていると永遠に
進行中として残り続ける。

    1785936016_AGKAI_Mackerel_wool_100%_yatolab_yshimoda   log 3361B / json 57659B (NONE)
    1785936020_CamelliaDragons_CanisLupus_Luna_NTT-HAI_...  log 2619B / json 50232B (NONE)

どちらも log は day1 の status 行で綺麗に途切れており、json は同じミリ秒に書かれて
いる。ゲーム自体は day1 で異常終了し、log にだけ `result` 行が出なかった形。

## 判定

**json を先に見る。** ゲームが終わったかどうかは json だけで決まり、log は
「その結論を指標に使える形で持っているか」の確認にしか使わない。

    json が無い / win_side が空              -> 進行中 (まだ終わっていない)
    win_side == NONE                         -> 不成立
    win_side が陣営 かつ log の result 行が一致 -> 成立
    win_side が陣営 だが log の result 行が無い/食い違う -> 壊れている

最後のケースだけ success に運ばない。log から `parse_log` が勝敗を読めないので、
運んでも集計側で弾かれるだけだから。

同じゲームの判定結果は一度決まれば変わらないので、**確定した状態は (mtime, size) を
鍵にしてキャッシュする**。MainTruck5 は566ゲームあり、毎周期 json を開き直すと
30MB 近く読むことになるが、キャッシュがあれば新しいゲームの分しか読まない。

json が取得できない環境（`json_logger` 無効など）では log の `result` 行だけで
判定する。その場合、上記2件のような中断は進行中として残り続ける。

コピーは冪等（サイズと更新時刻で判定）。元ファイルには一切触らない。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# 勝敗が付いていない印。json の win_side と log の result 行の両方で使われる。
NO_WINNER = frozenset({"", "NONE"})

SUCCESS = "success"
FAILED = "failed"
PENDING = "pending"
BROKEN = "broken"


@dataclass
class GameStatus:
    stem: str
    state: str
    log_winner: str | None = None
    json_winner: str | None = None


@dataclass
class SortStats:
    copied_log: int = 0
    copied_json: int = 0
    success: int = 0
    failed: int = 0
    pending: int = 0
    missing_json: list[str] = field(default_factory=list)
    # log に result 行が無いのに json では決着している = 指標に使えないゲーム
    broken: list[str] = field(default_factory=list)
    # success に居るのに成立していないファイル（従来スクリプトの取りこぼし）
    stale_in_success: list[str] = field(default_factory=list)
    new_games: list[str] = field(default_factory=list)

    def summary(self) -> str:
        head = f"決着 {self.success} / 不成立 {self.failed} / 進行中 {self.pending}"
        if self.copied_log == 0 and self.copied_json == 0:
            return head + ("  — すべてコピー済み (新規なし)" if self.success else "  — コピーするものなし")
        return head + f"  — 新規コピー log {self.copied_log}, json {self.copied_json}"


def game_winner(log_path: Path) -> str | None:
    """log の最後の `result` 行の勝利陣営。`result` 行が無ければ None。"""
    winner: str | None = None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split(",")
        # 2番目のフィールドが行の種類。talk 本文にカンマが入っても "talk" のまま。
        if len(parts) >= 5 and parts[1] == "result":
            winner = parts[4].strip()
    return winner


def json_winner(json_path: Path) -> str | None:
    """json の `win_side`。ファイルが無い / 未設定なら None（まだ終わっていない）。"""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    side = data.get("win_side")
    return str(side).strip() if side not in (None, "") else None


def is_success(winner: str | None) -> bool:
    return winner is not None and winner.upper() not in NO_WINNER


def _fingerprint(*paths: Path) -> tuple:
    """ファイルの (更新時刻, サイズ)。書き換わったらキャッシュを捨てるための鍵。"""
    out = []
    for p in paths:
        try:
            s = p.stat()
            out.append((s.st_mtime_ns, s.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def classify(log_path: Path, json_dir: Path | None,
             cache: dict | None = None) -> GameStatus:
    """1ゲームの状態を決める。json が終了の判定、log は指標に使えるかの確認。

    cache に dict を渡すと、確定した判定を (mtime, size) 付きで覚えて再利用する。
    進行中は確定していないので覚えない。
    """
    stem = log_path.stem
    json_path = json_dir / f"{stem}.json" if json_dir is not None else None

    key = _fingerprint(log_path, json_path) if json_path else _fingerprint(log_path)
    if cache is not None and cache.get(stem, (None, None))[0] == key:
        return cache[stem][1]

    status = _classify_uncached(log_path, json_path, stem)
    if cache is not None and status.state != PENDING:
        cache[stem] = (key, status)
    return status


def _classify_uncached(log_path: Path, json_path: Path | None, stem: str) -> GameStatus:
    if json_path is None:
        # json を使えない運用。log の result 行だけで判定する（中断は進行中に見える）。
        log_win = game_winner(log_path)
        if is_success(log_win):
            return GameStatus(stem, SUCCESS, log_win, None)
        return GameStatus(stem, FAILED if log_win is not None else PENDING, log_win, None)

    js_win = json_winner(json_path)
    if js_win is None:
        # json がまだ書かれていない = ゲームが終わっていない
        return GameStatus(stem, PENDING, None, None)
    if js_win.upper() in NO_WINNER:
        # 不成立で確定。log に result 行があるかどうかは問わない。
        return GameStatus(stem, FAILED, None, js_win)

    # json は決着したと言っている。log が指標に使える形かをここで確認する。
    log_win = game_winner(log_path)
    if log_win is None or log_win.upper() != js_win.upper():
        return GameStatus(stem, BROKEN, log_win, js_win)
    return GameStatus(stem, SUCCESS, log_win, js_win)


def _copy_if_new(src: Path, dst_dir: Path, dry_run: bool) -> bool:
    """まだ無い / 中身が変わっているときだけコピーする。コピーしたら True。"""
    dst = dst_dir / src.name
    if dst.exists():
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and int(s.st_mtime) == int(d.st_mtime):
            return False
    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)   # copy2 で mtime を保つ (冪等判定に使う)
    return True


def sort_once(log_dir: Path, json_dir: Path | None = None, *,
              dry_run: bool = False, check_stale: bool = True,
              cache: dict | None = None) -> SortStats:
    """log_dir 直下の *.log を1周見て、成立したものを success へコピーする。

    元ファイルには一切触らない（コピーのみ）。json_dir を渡すと同名の json も
    `json_dir/success/` へコピーし、進行中と中断の判別にも使う。

    cache に dict を渡すと判定結果を再利用する（常駐運用ではこれを渡すこと）。
    check_stale は起動時の検算用。毎周期やる必要は無い。
    """
    st = SortStats()
    success_log_dir = log_dir / "success"

    for log_path in sorted(log_dir.glob("*.log")):
        status = classify(log_path, json_dir, cache)

        if status.state == PENDING:
            st.pending += 1
            continue
        if status.state == FAILED:
            st.failed += 1
            continue
        if status.state == BROKEN:
            # json では決着しているが log が不完全。運ぶと集計側で弾かれるだけなので運ばない。
            st.broken.append(status.stem)
            st.failed += 1
            continue

        st.success += 1
        if _copy_if_new(log_path, success_log_dir, dry_run):
            st.copied_log += 1
            st.new_games.append(status.stem)

        if json_dir is None:
            continue
        json_path = json_dir / f"{log_path.stem}.json"
        if not json_path.is_file():
            st.missing_json.append(json_path.name)
            continue
        if _copy_if_new(json_path, json_dir / "success", dry_run):
            st.copied_json += 1

    if check_stale and success_log_dir.is_dir():
        # 従来スクリプトが未完了ゲームを入れてしまっていないかの検算。
        for copied in sorted(success_log_dir.glob("*.log")):
            if not is_success(game_winner(copied)):
                st.stale_in_success.append(copied.name)

    return st


def prepare_track(track_dir: Path, *, dry_run: bool = False, quiet: bool = False) -> SortStats | None:
    """1 トラック分を振り分けて要約を表示する。log/ が無ければ None。"""
    log_dir = track_dir / "log"
    json_dir = track_dir / "json"
    if not log_dir.is_dir():
        return None
    st = sort_once(log_dir, json_dir if json_dir.is_dir() else None, dry_run=dry_run)
    print(f"== {track_dir.name}: {st.summary()}")
    if not quiet:
        for name in st.new_games:
            print(f"   [COPY] {name}")
    if st.broken:
        print(f"   [警告] json では決着しているのに log に result 行が無い: {len(st.broken)} 件 (指標に使えないので運ばない)")
        for name in st.broken[:10]:
            print(f"          {name}")
    if st.missing_json:
        print(f"   [警告] 対応する json が無い: {len(st.missing_json)} 件 (例 {st.missing_json[0]})")
    if st.stale_in_success:
        print(f"   [警告] success にあるが決着していない: {len(st.stale_in_success)} 件 — 中身を確認すること")
        for name in st.stale_in_success[:10]:
            print(f"          {name}")
    return st
