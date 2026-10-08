"""蓄積した seat からチーム別の指標表を作り、参加者が読むテキストに落とす。

指標の定義と表の構成は `aiwolf_nlp_calculate_score.report` にある。大会後に配る `all_team/` と
同じ関数を通すので、逐次で出した値とトラック終了後に流し直した値は必ず一致する。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..metrics import build_seat_table
from ..parser import VILLAGER_SIDE, WEREWOLF_SIDE, Issue, LogFormatError, parse_log
from ..report import metric_frame, pad, render_metrics_text, width
from ..report import role_win_table as _role_win_table
from .strings import TEXT

PROGRESS_COLUMNS = ["予定", "消化", "残り"]


def planned_counts(optimizer_path: Path) -> pd.DataFrame | None:
    """マッチオプティマイザ JSON からチーム別の 消化 / 残り を読む。無ければ None。"""
    try:
        d = json.loads(optimizer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    idx_team = {int(k): v for k, v in (d.get("idx_team_map") or {}).items()}
    if not idx_team:
        return None
    rows = {name: {"消化": 0, "残り": 0} for name in idx_team.values()}
    for match in d.get("ended_matches") or []:
        for idxs in match.values():
            for i in idxs:
                if (name := idx_team.get(i)) is not None:
                    rows[name]["消化"] += 1
    for sm in d.get("scheduled_matches") or []:
        for idxs in (sm.get("role_idxs") or {}).values():
            for i in idxs:
                if (name := idx_team.get(i)) is not None:
                    rows[name]["残り"] += 1
    out = pd.DataFrame.from_dict(rows, orient="index")
    out["予定"] = out["消化"] + out["残り"]
    out.index.name = "team"
    return out[PROGRESS_COLUMNS]


def build_seats(log_paths: list[Path], dataset: str, json_dir: Path | None = None,
                teams: list[str] | None = None) -> tuple[pd.DataFrame, list[str], list[Issue]]:
    """ログを読んで seat テーブルを作る。読めなかった / 決着していないゲームは名前を返す。"""
    games, skipped, issues = [], [], []
    for path in log_paths:
        jp = None
        if json_dir is not None:
            for cand in (json_dir / f"{path.stem}.json", json_dir / "success" / f"{path.stem}.json"):
                if cand.is_file():
                    jp = cand
                    break
        local: list[Issue] = []
        try:
            game = parse_log(path, dataset, jp, teams, local)
        except (LogFormatError, OSError) as e:
            skipped.append(path.stem)
            issues.append(Issue(path.stem, str(e), fatal=True))
            continue
        issues.extend(local)
        if any(i.fatal for i in local) or game.winner not in (VILLAGER_SIDE, WEREWOLF_SIDE):
            skipped.append(path.stem)
            continue
        games.append(game)
    if not games:
        return pd.DataFrame(), skipped, issues
    seats, _hazard = build_seat_table(games)
    return seats, skipped, issues


def team_table(seats: pd.DataFrame, progress: pd.DataFrame | None = None) -> pd.DataFrame:
    """チーム別の指標表。まだ 1 戦もしていないチームは指標を空にして残す。"""
    if seats is None or seats.empty:
        if progress is None or progress.empty:
            return pd.DataFrame(columns=["team"])
        out = progress.copy()
        out.index.name = "team"
        return out.reset_index()
    out = metric_frame(seats, progress)
    if progress is not None:
        order = ["team"] + PROGRESS_COLUMNS + [c for c in out.columns if c not in PROGRESS_COLUMNS + ["team"]]
        out = out[order]
    return out


def role_win_table(seats: pd.DataFrame, teams: list[str]) -> pd.DataFrame:
    return _role_win_table(seats, teams)


def render_text(table: pd.DataFrame, *, dataset: str, completion=None,
                role_table: pd.DataFrame | None = None, lang: str = "ja") -> str:
    """参加者が読む指標表を 1 言語ぶん組む。完了していれば冒頭にその旨を書く。"""
    txt = TEXT[lang]
    now = datetime.now().astimezone()
    stamp = f"{now:%Y-%m-%d %H:%M:%S %z}"
    header = [pad(" " + txt["title"], 100 - width(stamp)) + stamp]
    lead = []
    if completion is not None and completion.finished:
        lead += [txt["finished_banner"], ""]
    lead.append(f"{txt['target']} {dataset}")
    if completion is not None:
        lead.append(f"  {completion.describe(lang)}")
    lead.append("")
    if table.empty:
        rule = "=" * 100
        return "\n".join([rule] + header + [rule, ""] + lead + txt["empty"] + [""])
    body = render_metrics_text(table, role_table, lang, header)
    head, _, rest = body.partition("\n\n")
    return head + "\n\n" + "\n".join(lead) + "\n" + rest
