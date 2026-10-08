"""トラックが終わったかを判定し、終わっていればサーバと監視スクリプトを止める。

## 何をもって「全試合終了」とするか

**マッチオプティマイザ JSON の `ended_matches` と `game_count` を突き合わせる。**
サーバのログに出る「全てのゲームが終了しました」は当てにできない。実際
`log_freeform5.log` を検索しても、24/24 を消化した後でさえ**一度も出力されていない**。

    freeform5  game_count=24   ended=24  scheduled=0    -> 完了
    5          game_count=270  ended=245 scheduled=25   -> 未完了 (2チーム未接続で組めない)
    9          game_count=28   ended=0   scheduled=28   -> 未着手

`ended_matches` は **成立したゲームだけ** を数える（不成立は入らない）。実データで
確認済み: freeform は ended=24 に対し成立24・不成立22、MainTruck5 は ended=245 に
対し成立245。なのでログ側の成立数と必ず一致し、検算に使える。

完了条件は4つすべてを満たすこと。

    1. game_count > 0
    2. len(ended_matches) >= game_count
    3. scheduled_matches が空
    4. 進行中のゲームが0 (log はあるが json がまだ無いもの)

**組めない試合が残っている場合は完了にしない。** MainTruck5 のように残り25試合が
未接続チーム待ちで永遠に埋まらないことがあり、これを自動停止させると人の判断を
奪ってしまう。その状態は「アイドル」として報告するだけに留める。

## 止める順番

    1. 最終の指標を書く (metrics.txt に【完了】と明記)
    2. monitor_status.py を止める      <- サーバがまだ生きているうちに
    3. サーバを止める
    4. 自分も終わる

**監視を先に止める**のが肝。逆にするとサーバ停止後の status.txt が
「状態: 応答なし」で凍結してしまい、"終わったから止めた" のか "落ちた" のかが
読み手に区別できなくなる。先に止めれば「稼働中 / 消化 24/24 (100.0%)」という
正しい終端状態で凍る。

停止対象は **自分と同じ uid** かつ **cmdline に同じ設定ファイル名を含む** ものだけに
絞る。SIGKILL は使わない（送るのは SIGTERM のみ。落ちなければ警告して残す）。
"""

from __future__ import annotations

import json
import os
import re
import signal
import time
from dataclasses import dataclass
from pathlib import Path

SERVER_MARKER = "aiwolf-nlp-server"
MONITOR_MARKER = "monitor_status.py"


@dataclass
class Completion:
    """完了判定の内訳。読み手に理由が分かるよう数字をそのまま持つ。"""
    game_count: int = 0
    ended: int = 0
    scheduled: int = 0
    in_progress: int = 0
    optimizer_ok: bool = False

    @property
    def finished(self) -> bool:
        return (self.optimizer_ok and self.game_count > 0
                and self.ended >= self.game_count
                and self.scheduled == 0
                and self.in_progress == 0)

    @property
    def stalled(self) -> bool:
        """組めない試合が残ったまま止まっている状態。自動停止はさせない。"""
        return (self.optimizer_ok and self.game_count > 0
                and not self.finished and self.in_progress == 0)

    def describe(self, lang: str = "ja") -> str:
        """参加者が読む1行。実装用語は使わず、試合数だけで言い切る。"""
        from .strings import TEXT
        s = TEXT[lang]["progress"]
        if not self.optimizer_ok:
            return s["unknown"]
        if self.finished:
            return s["finished"].format(total=self.game_count)
        if self.ended == 0:
            return s["not_started"].format(total=self.game_count)
        pct = 100.0 * self.ended / self.game_count if self.game_count else 0.0
        base = s["base"].format(ended=self.ended, total=self.game_count, pct=pct)
        if self.in_progress:
            return base + s["in_progress"].format(n=self.in_progress)
        if self.scheduled:
            return base + s["remaining"].format(n=self.scheduled)
        return base


def read_completion(optimizer_path: Path, in_progress: int = 0) -> Completion:
    try:
        d = json.loads(optimizer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Completion(in_progress=in_progress, optimizer_ok=False)
    return Completion(
        game_count=int(d.get("game_count") or 0),
        ended=len(d.get("ended_matches") or []),
        scheduled=len(d.get("scheduled_matches") or []),
        in_progress=in_progress,
        optimizer_ok=True,
    )


def find_processes(*markers: str) -> list[tuple[int, str]]:
    """自分と同じ uid のプロセスから、cmdline が markers を全部含むものを返す。

    pgrep に頼らず /proc を直接見る。uid を確認するので他人のプロセスは拾わない。
    """
    me = os.getuid()
    mine = os.getpid()
    found: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == mine:
            continue
        try:
            if entry.stat().st_uid != me:
                continue
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        cmdline = raw.decode("utf-8", "replace").replace("\0", " ").strip()
        if cmdline and all(m in cmdline for m in markers):
            found.append((pid, cmdline))
    return sorted(found)


def terminate(pids: list[int], timeout: float = 30.0) -> list[int]:
    """SIGTERM を送って終了を待つ。落ちなかった pid を返す（SIGKILL はしない）。"""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            return []
        time.sleep(0.5)
    return [p for p in pids if _alive(p)]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# monitor_status.py が書く行を狙い撃ちする。日本語版と英語版の両方に対応する。
#   「  状態  : 稼働中」/「  State : running」 -> サーバ状態の行 (最初の1つだけ)
#   末尾の自動生成フッタ
# チーム一覧の見出しにも「状態」「Status」は出るがコロンが続かないので当たらない。
STATUS_PATTERNS = {
    "ja": (
        re.compile(r"^(?P<head>[ \t]*状態[ \t]+:[ \t]*).*$", re.MULTILINE),
        re.compile(r"^ 次回更新まで .*? / このファイルは自動生成されます$", re.MULTILINE),
        "停止（全試合終了により {when} に停止）",
        " 全試合終了により停止しました / この内容は確定版で、以降更新されません",
        "\n【停止済み】 {when} 全試合終了により監視とサーバを停止しました\n",
    ),
    "en": (
        re.compile(r"^(?P<head>[ \t]*State[ \t]+:[ \t]*).*$", re.MULTILINE),
        re.compile(r"^ next update in .*? / this file is generated automatically$",
                   re.MULTILINE),
        "stopped (all games finished; stopped at {when})",
        " Stopped because every game finished / this is the final content, "
        "it will not be updated again",
        "\n[STOPPED] {when} every game finished; the monitor and server were stopped\n",
    ),
}

# 以前の版が末尾に付けていた注意書き。書き換え方式に移行したので剥がす。
RE_OLD_NOTE = re.compile(r"\n-{10,}\n【停止済み】.*$", re.DOTALL)


def finalize_all_status(status_path: Path, when: str) -> list[Path]:
    """status.<lang>.txt を全部「停止済み」に書き換える。書き換えたパスを返す。

    monitor_status.py が `status.txt` の拡張子を差し替えて言語別に出しているので、
    設定上の `status.txt` から同じ規則でパスを組み立てる。設定のパスそのものが
    存在する場合（旧版の単一ファイル運用）も面倒を見る。
    """
    done = []
    targets = [(lang, status_path.with_suffix(f".{lang}.txt")) for lang in STATUS_PATTERNS]
    if status_path.is_file():
        targets.append(("ja", status_path))
    for lang, path in targets:
        if path.is_file() and finalize_status(path, when, lang):
            done.append(path)
    return done


def finalize_status(status_path: Path, when: str, lang: str = "ja") -> bool:
    """凍結する status.txt を「停止済み」の内容に書き換える。

    監視をサーバより先に止める都合で、最後のスナップショットには
    「状態: 稼働中」が残る。だが**スクリプトが止まる = 全試合終了 = サーバも停止**
    なので、終端の内容は確定している。注意書きを足して読み手に補正させるより、
    状態行そのものを事実に合わせて書き換える方が素直。

    書き換えるのは状態行と自動生成フッタの2箇所だけ。進捗や接続状況は停止直前の
    事実なのでそのまま残す。この時点で監視プロセスは居ないので書き手は競合しない。
    """
    try:
        text = status_path.read_text(encoding="utf-8")
    except OSError:
        return False

    re_state, re_footer, state_text, footer_text, fallback = STATUS_PATTERNS[lang]
    text = RE_OLD_NOTE.sub("", text).rstrip("\n") + "\n"

    text, n_state = re_state.subn(
        lambda m: m.group("head") + state_text.format(when=when), text, count=1)
    text, n_footer = re_footer.subn(footer_text, text)

    if not n_state and not n_footer:
        # 想定した行が無い = 書式が変わった。黙って何もしないより印を残す。
        text += fallback.format(when=when)

    try:
        tmp = status_path.with_suffix(status_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, status_path)
    except OSError:
        return False
    return True


def shutdown_track(config_name: str, *, dry_run: bool = False,
                   log=print) -> bool:
    """監視スクリプト → サーバ の順で止める。全部止まったら True。

    config_name は設定ファイル名 (例 "freeform_en_5.yml")。同じ設定で動いている
    プロセスだけが対象なので、他トラックのサーバは巻き込まない。
    """
    targets = [
        ("監視スクリプト", find_processes(MONITOR_MARKER, config_name)),
        ("サーバ", find_processes(SERVER_MARKER, config_name)),
    ]

    for label, procs in targets:
        if not procs:
            log(f"  {label}: 動いていません")
            continue
        for pid, cmdline in procs:
            log(f"  {label}: pid={pid}  {cmdline[:90]}")
        if dry_run:
            log(f"  {label}: --dry-run のため止めません")
            continue
        left = terminate([p for p, _c in procs])
        if left:
            log(f"  [警告] {label} が止まりませんでした: {left} — 手動で確認してください")
            return False
        log(f"  {label}: 停止しました")
    return True
