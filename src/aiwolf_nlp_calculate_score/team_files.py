"""参加者に配るファイルを書き出す。

    <root>/<トラック>/all_team/{ja,en}/{txt,md,csv}/   トラック全体 (live の metrics.txt と同じ構成)
    <root>/<トラック>/<チーム>/{ja,en}/{md,csv}/        チーム別: 全役職まとめ + 役職別
    <root>/by_team/<チーム>/{ja,en}/summary.md          チーム横断: 出場した全トラックを横に並べる

数値は言語によらず同じ。変わるのは見出しとラベルだけ。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .metrics import aggregate, team_summary, win_rate_variants
from .parser import VILLAGER_SIDE
from .report import (
    ROLE_LABEL,
    ROLE_ORDER,
    csv_frame,
    metric_frame,
    render_metrics_md,
    render_metrics_text,
    role_win_table,
    width,
)

LANGUAGES = ("ja", "en")
ROLE_FILE = {"VILLAGER": "villager", "SEER": "seer", "MEDIUM": "medium",
             "BODYGUARD": "bodyguard", "POSSESSED": "possessed", "WEREWOLF": "werewolf"}

# 指標定義。num/den は seats の列。exp があれば 値 = 分子 / 期待、無ければ 分子 / 分母。
# rank は順位の向き (+1 大きいほど上位)。filter はその指標が意味を持つ seat の条件。
_M = [
    dict(key="勝率", num="win", den="games", exp=None, dir="up", rank=+1,
         ja=("勝率", "勝ち数", "試合数"), en=("Win rate", "wins", "games")),
    dict(key="追放されやすさ", num="executed", den="execute_at_risk", exp="execute_exp", dir="down", rank=-1,
         ja=("追放されやすさ", "追放された回数", "投票日数"),
         en=("Execution likelihood (vs random)", "times executed", "voting days")),
    dict(key="占われやすさ", num="divined_recv", den="divined_opp", exp="divined_exp", dir="down", rank=-1,
         ja=("占われやすさ", "占われた回数", "占いの機会"),
         en=("Divination targeting (vs random)", "times divined", "divination chances")),
    dict(key="守られやすさ", num="guarded_recv", den="guarded_opp", exp="guarded_exp", dir="up", rank=+1,
         ja=("守られやすさ", "護衛された回数", "護衛の機会"),
         en=("Guard targeting (vs random)", "times guarded", "guard chances")),
    dict(key="襲われやすさ", num="attacked_recv", den="attacked_opp", exp="attacked_exp", dir="hold", rank=-1,
         ja=("襲われやすさ", "襲撃された回数", "襲撃の機会"),
         en=("Attack targeting (vs random)", "times attacked", "attack chances"),
         note_ja="人狼に脅威とみなされた度合い。役職者ほど高く出る。高低の良し悪しは保留のうえ、順位は低い方を上位として付けている",
         note_en="How much the werewolves saw this agent as a threat; higher for role holders. The direction is left open; ranks treat lower as better."),
    dict(key="襲撃候補にされやすさ", num="attack_vote_recv", den="attack_vote_opp", exp="attack_vote_exp", dir="hold", rank=-1,
         ja=("襲撃候補にされやすさ", "襲撃投票で指名された回数", "襲撃投票の機会"),
         en=("Attack-vote targeting (vs random)", "times named in attack votes", "attack-vote chances"),
         note_ja="人狼が1体の村では襲われやすさと一致する",
         note_en="Equals attack targeting in villages with a single werewolf."),
    dict(key="投票精度", num="vote_to_wolf", den="vote_cast", exp="vote_to_wolf_exp", dir="up", rank=+1,
         filter={"camp": VILLAGER_SIDE},
         ja=("投票精度", "人狼に投票", "投票の合計"), en=("Vote accuracy (vs random)", "votes on werewolves", "votes cast")),
    dict(key="占い精度", num="divine_hit", den="divine_cast", exp="divine_hit_exp", dir="up", rank=+1,
         ja=("占い精度", "人狼を占った", "占った合計"), en=("Divination accuracy (vs random)", "werewolves divined", "divinations")),
    dict(key="人狼を避けた率", num=("補", "vote_to_wolf"), den="vote_cast", exp=None, dir="up", rank=+1,
         filter={"role": "POSSESSED"},
         ja=("人狼を避けた率", "人狼以外に投票", "投票の合計"), en=("Non-werewolf vote rate", "votes on non-werewolves", "votes cast")),
    dict(key="仲間撃ち率", num="vote_to_other_wolf", den="vote_cast", exp=None, dir="hold", rank=-1,
         filter={"role": "WEREWOLF", "n_werewolves": 2},
         ja=("仲間撃ち率", "仲間に投票", "投票の合計"), en=("Teammate-vote rate", "votes on teammates", "votes cast"),
         note_ja="単純な誤りとは限らず、疑いを逸らして信頼を得る戦略の可能性もある。高低どちらが良いかは保留のうえ、順位は低い方を上位として付けている",
         note_en="Not necessarily a mistake: voting a teammate can be a deliberate way to deflect suspicion and gain trust. The direction is left open; ranks are assigned treating lower as better."),
]
_METRICS = {m["key"]: m for m in _M}
COMMON = ["勝率", "追放されやすさ", "占われやすさ", "守られやすさ", "襲われやすさ", "襲撃候補にされやすさ"]
ROLE_METRICS = {
    "VILLAGER": COMMON + ["投票精度"], "SEER": COMMON + ["投票精度", "占い精度"],
    "MEDIUM": COMMON + ["投票精度"], "BODYGUARD": COMMON + ["投票精度"],
    "POSSESSED": COMMON + ["人狼を避けた率"], "WEREWOLF": COMMON + ["仲間撃ち率"],
}
ALL_METRICS = COMMON + ["投票精度", "占い精度", "人狼を避けた率", "仲間撃ち率"]

# エラー挙動 (チーム別ファイルの全役職まとめに付ける): (項目, 分子列, 分母列)
_ERRORS = [
    ("投票せず", "vote_missing", "execute_at_risk"), ("自分に投票", "vote_self", "vote_cast"),
    ("襲撃投票せず", "attack_vote_missing", "attack_vote_nights"), ("仲間を襲撃指名", "attack_vote_self", "attack_vote_cast"),
    ("占わず", "divine_missing", "divine_nights"), ("護衛せず", "guard_missing", "guard_nights"),
    ("無発言の日", "silent_days", "talk_days"), ("同一発言の連投", "dup_talks", "talk_n"),
]
_ERROR_EN = {"投票せず": "No vote", "自分に投票": "Self-vote", "襲撃投票せず": "No attack vote",
             "仲間を襲撃指名": "Attack vote on werewolf", "占わず": "No divination", "護衛せず": "No guard",
             "無発言の日": "Silent days", "同一発言の連投": "Repeated talks"}

TEXT = {
    "ja": {
        "dir": {"up": "高いほど良い", "down": "低いほど良い", "hold": "解釈保留"},
        "cols": ["team", "指標", "実測", "分母", "実測の意味", "分母の意味", "値", "順位", "チーム数", "向き", "備考"],
        "head": ["指標", "実測", "分母", "値", "向き", "順位"],
        "stamp": "集計日時", "summary_title": "全役職まとめ（average）",
        "summary_note": ["このトラックでの参加試合数: **{n}**"],
        "role_title": "{role} を担当したとき",
        "role_note": ["{role} で戦った試合数: **{n}** / 全{total}試合", "",
                      "順位はこのトラックで同じ役職を担当した全チームの中での順位。",
                      "同率は同じ順位にして、その分だけ次を飛ばす（1位が3チームなら次は4位）。"],
        "empty": "（この役職での試合がありません）", "notes": "備考", "by_role": "役職別勝率",
        "by_role_head": ["役職", "勝率", "勝ち数 / 試合数"], "win_defs": ["勝率の定義", "値"],
        "errors": "エラー挙動", "errors_head": ["項目", "回数", "機会"],
        "errors_note": "0 が正常。機会はその行動を取れた回数。",
        "sheet_title": "{team} — 出場した全トラック", "sheet_head": ["トラック", "試合"],
        "sheet_note": "セルは 値 (順位 / 値のあるチーム数)。順位の向きは各トラックのチーム別ファイルと同じ。",
    },
    "en": {
        "dir": {"up": "Higher is better", "down": "Lower is better", "hold": "Direction left open"},
        "cols": ["team", "metric", "observed", "denominator", "observed_meaning", "denominator_meaning",
                 "value", "rank", "teams", "direction", "note"],
        "head": ["Metric", "Observed", "Denominator", "Value", "Direction", "Rank"],
        "stamp": "Generated", "summary_title": "All roles combined (average)",
        "summary_note": ["Games played in this track: **{n}**"],
        "role_title": "When playing as {role}",
        "role_note": ["Games played as {role}: **{n}** of {total}", "",
                      "Ranks are among all teams that played this role in this track.",
                      "Ties share a rank and the following ranks are skipped (three teams tied at 1st are followed by 4th)."],
        "empty": "(no games in this role)", "notes": "Notes", "by_role": "Win rate by role",
        "by_role_head": ["Role", "Win rate", "Wins / Games"], "win_defs": ["Win rate definition", "Value"],
        "errors": "Error behaviour", "errors_head": ["Item", "Count", "Opportunities"],
        "errors_note": "0 is normal. Opportunities = times the action was possible.",
        "sheet_title": "{team} — every track entered", "sheet_head": ["Track", "Games"],
        "sheet_note": "Cells are value (rank / teams with a value). Rank direction is the same as in the per-track team files.",
    },
}


def _apply_filter(seats: pd.DataFrame, flt: dict | None) -> pd.DataFrame:
    sub = seats
    for k, v in (flt or {}).items():
        sub = sub[sub[k] >= v] if k == "n_werewolves" else sub[sub[k] == v]
    return sub


def role_table(seats: pd.DataFrame, role: str | None) -> pd.DataFrame:
    """チーム × 指標 の表 (言語非依存)。role=None なら全役職まとめ。"""
    scope = seats if role is None else seats[seats["role"] == role]
    if scope.empty:
        return pd.DataFrame()
    teams = sorted(scope["team"].unique())
    names = ROLE_METRICS[role] if role else ALL_METRICS
    rows = []
    for name in names:
        m = _METRICS[name]
        sub = _apply_filter(scope, m.get("filter"))
        if sub.empty:
            continue
        g = sub.groupby("team")
        den = g.size() if m["den"] == "games" else g[m["den"]].sum()
        num = (den - g[m["num"][1]].sum()) if isinstance(m["num"], tuple) else g[m["num"]].sum()
        if float(den.sum()) == 0:
            continue
        base = g[m["exp"]].sum() if m["exp"] else den
        val = num / base.replace(0, np.nan)
        rank = val.rank(ascending=(m["rank"] < 0), method="min")
        for team in teams:
            rows.append({"team": team, "key": name, "実測": num.get(team, 0), "分母": den.get(team, 0),
                         "値": val.get(team), "順位": rank.get(team), "チーム数": int(den.gt(0).sum())})
    out = pd.DataFrame(rows)
    if not out.empty:
        out["順位"] = out["順位"].astype("Int64")
    return out


def _fmt(v, nd=3):
    return "-" if v is None or pd.isna(v) else f"{v:.{nd}f}"


def _localize(part: pd.DataFrame, lang: str) -> pd.DataFrame:
    t = TEXT[lang]
    rows = []
    for _, r in part.iterrows():
        m = _METRICS[r["key"]]
        label, nl, dl = m[lang]
        rows.append([r["team"], label, int(r["実測"]), int(r["分母"]), nl, dl, r["値"], r["順位"],
                     r["チーム数"], t["dir"][m["dir"]], m.get(f"note_{lang}", "")])
    return pd.DataFrame(rows, columns=t["cols"])


def _md_table(part: pd.DataFrame, lang: str) -> list[str]:
    t = TEXT[lang]
    lines = ["| " + " | ".join(t["head"]) + " |", "|---|---:|---:|---:|---|---:|"]
    for _, r in part.iterrows():
        m = _METRICS[r["key"]]
        label, nl, dl = m[lang]
        rank = "-" if pd.isna(r["順位"]) else f"{int(r['順位'])} / {int(r['チーム数'])}"
        lines.append(f"| {label} | {int(r['実測'])} ({nl}) | {int(r['分母'])} ({dl}) | "
                     f"{_fmt(r['値'])} | {t['dir'][m['dir']]} | {rank} |")
    notes = [(_METRICS[r["key"]][lang][0], _METRICS[r["key"]].get(f"note_{lang}", "")) for _, r in part.iterrows()]
    notes = [(n, x) for n, x in notes if x]
    if notes:
        lines += ["", f"**{t['notes']}**", ""] + [f"- **{n}** — {x}" for n, x in notes]
    return lines


def _stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def write_team_files(seats: pd.DataFrame, dataset: str, team: str, root: Path) -> list[Path]:
    """1 チーム分 (全役職まとめ + 役職別) を全言語で書く。"""
    ds = seats[seats["dataset"] == dataset]
    if ds.empty or team not in set(ds["team"]):
        return []
    stamp = _stamp()
    n_games = int((ds["team"] == team).sum())
    wr = win_rate_variants(ds).set_index("team")
    by_role = aggregate(ds, ["dataset", "team", "role"]).set_index(["team", "role"])
    all_tbl = role_table(ds, None)
    mine = ds[ds["team"] == team]
    written: list[Path] = []

    for lang in LANGUAGES:
        t = TEXT[lang]
        md_dir = root / dataset / team / lang / "md"
        csv_dir = root / dataset / team / lang / "csv"
        md_dir.mkdir(parents=True, exist_ok=True)
        csv_dir.mkdir(parents=True, exist_ok=True)

        def emit(stem, title, note, table, extra=None, lang=lang, t=t, md_dir=md_dir, csv_dir=csv_dir):
            part = table[table["team"] == team] if not table.empty else table
            head = [f"# {team} — {dataset}", "", f"## {title}", "", f"{t['stamp']}: {stamp}", ""] + note + [""]
            body = _md_table(part, lang) if not part.empty else [t["empty"]]
            (md_dir / f"{stem}.md").write_text("\n".join(head + body + [""] + (extra or [])), encoding="utf-8")
            _localize(part, lang).to_csv(csv_dir / f"{stem}.csv", index=False)
            written.extend([md_dir / f"{stem}.md", csv_dir / f"{stem}.csv"])

        extra = [f"## {t['by_role']}", "", "| " + " | ".join(t["by_role_head"]) + " |", "|---|---:|---:|"]
        for r in ROLE_ORDER:
            if (team, r) in by_role.index:
                row = by_role.loc[(team, r)]
                extra.append(f"| {ROLE_LABEL[lang][r]} | {row['win_rate']:.3f} | {int(row['win'])} / {int(row['games'])} |")
        extra += ["", "| " + " | ".join(t["win_defs"]) + " |", "|---|---:|"]
        for c in [c for c in wr.columns if c.startswith("勝率")]:
            extra.append(f"| {c} | {wr.loc[team, c]:.3f} |")
        extra += ["", f"## {t['errors']}", "", "| " + " | ".join(t["errors_head"]) + " |", "|---|---:|---:|"]
        err_rows = []
        for label, num, den in _ERRORS:
            d = int(mine[den].sum())
            if d == 0:
                continue
            name = label if lang == "ja" else _ERROR_EN[label]
            extra.append(f"| {name} | {int(mine[num].sum())} | {d} |")
            err_rows.append({"item": name, "count": int(mine[num].sum()), "opportunities": d})
        extra += ["", t["errors_note"], ""]
        pd.DataFrame(err_rows).to_csv(csv_dir / "errors.csv", index=False)
        written.append(csv_dir / "errors.csv")

        emit("summary", t["summary_title"], [x.format(n=n_games) for x in t["summary_note"]], all_tbl, extra)
        for role in ROLE_ORDER:
            got = ds[(ds["team"] == team) & (ds["role"] == role)]
            if got.empty:
                continue
            label = ROLE_LABEL[lang][role]
            emit(f"role_{ROLE_FILE[role]}", t["role_title"].format(role=label),
                 [x.format(role=label, n=len(got), total=n_games) for x in t["role_note"]], role_table(ds, role))
    return written


def write_all_team_files(seats: pd.DataFrame, dataset: str, root: Path) -> list[Path]:
    """トラック全体の指標表を all_team/ に txt / md / csv で置く (live の metrics.txt と同じ構成)。"""
    ds = seats[seats["dataset"] == dataset]
    if ds.empty:
        return []
    stamp = _stamp()
    frame = metric_frame(ds)
    summary = team_summary(ds)
    roles = role_win_table(ds, list(frame["team"]))
    n_games = int(ds["game_id"].nunique())
    written: list[Path] = []
    for lang in LANGUAGES:
        t = TEXT[lang]
        md_dir = root / dataset / "all_team" / lang / "md"
        csv_dir = root / dataset / "all_team" / lang / "csv"
        txt_dir = root / dataset / "all_team" / lang / "txt"
        for d in (md_dir, csv_dir, txt_dir):
            d.mkdir(parents=True, exist_ok=True)
        header = [f"# {dataset}", "", f"{t['stamp']}: {stamp}", "",
                  (f"集計した試合数: **{n_games}**　　参加チーム数: **{len(frame)}**" if lang == "ja"
                   else f"Games counted: **{n_games}**　　Teams: **{len(frame)}**")]
        (md_dir / "metrics.md").write_text(render_metrics_md(frame, roles, lang, header), encoding="utf-8")
        title = "AIWolf ゲームスコア" if lang == "ja" else "AIWolf Game Scores"
        lead = [f" {title}" + " " * max(1, 100 - width(title) - len(stamp) - 2) + stamp]
        body = render_metrics_text(frame, roles, lang, lead)
        head, _, rest = body.partition("\n\n")
        target = (f"【対象】 {dataset}    {n_games} 試合 / {len(frame)} チーム" if lang == "ja"
                  else f"[Track] {dataset}    {n_games} games / {len(frame)} teams")
        (txt_dir / "metrics.txt").write_text(head + "\n\n" + target + "\n\n" + rest, encoding="utf-8")
        csv_frame(frame, summary, lang).to_csv(csv_dir / "metrics.csv", index=False)
        roles_out = roles.rename(columns={r: ROLE_LABEL[lang][r] for r in roles.columns if r != "team"})
        win_cols = [c for c in frame.columns if c.startswith("勝率")]
        totals = frame[["team", *win_cols]].rename(columns=(
            {"勝率_macro": "Win-M", "勝率_micro": "Win-m", "勝率_weighted_micro": "Win-w"} if lang == "en"
            else {"勝率_macro": "勝率M", "勝率_micro": "勝率m", "勝率_weighted_micro": "勝率w"}))
        totals.merge(roles_out, on="team", how="right").to_csv(csv_dir / "win_rate_by_role.csv", index=False)
        written += [md_dir / "metrics.md", txt_dir / "metrics.txt", csv_dir / "metrics.csv", csv_dir / "win_rate_by_role.csv"]
    return written


def _natural_key(name: str):
    import re as _re

    def key(part: str):
        return [int(x) if x.isdigit() else x for x in _re.split(r"(\d+)", part) if x != ""]
    tournament, _, track = name.partition("_")
    return (key(tournament), key(track))


def write_team_sheet(seats: pd.DataFrame, team: str, root: Path) -> list[Path]:
    """チーム横断の 1 枚: 出場した全トラックを行に、勝率と各指標を列に並べる (合成しない)。"""
    datasets = sorted({d for d in seats["dataset"].unique() if team in set(seats[seats.dataset == d]["team"])},
                      key=_natural_key)
    if not datasets:
        return []
    tables = {ds: role_table(seats[seats["dataset"] == ds], None) for ds in datasets}
    written = []
    for lang in LANGUAGES:
        t = TEXT[lang]
        out_dir = root / "by_team" / team / lang
        out_dir.mkdir(parents=True, exist_ok=True)
        labels = [_METRICS[k][lang][0] for k in ALL_METRICS]
        lines = [f"# {t['sheet_title'].format(team=team)}", "", f"{t['stamp']}: {_stamp()}", "",
                 "| " + " | ".join(t["sheet_head"] + labels) + " |", "|---|---:|" + "---:|" * len(labels)]
        rows = []
        for ds in datasets:
            tb = tables[ds]
            n = int((seats[(seats.dataset == ds) & (seats.team == team)]).shape[0])
            cells, rec = [], {"track": ds, "games": n}
            for k in ALL_METRICS:
                r = tb[(tb.team == team) & (tb.key == k)] if not tb.empty else tb
                if len(r) and pd.notna(r["値"].iloc[0]):
                    rr = r.iloc[0]
                    cells.append(f"{rr['値']:.3f} ({int(rr['順位'])}/{int(rr['チーム数'])})" if pd.notna(rr["順位"]) else f"{rr['値']:.3f}")
                    rec[k] = rr["値"]
                    rec[f"{k}_rank"] = rr["順位"]
                    rec[f"{k}_teams"] = rr["チーム数"]
                else:
                    cells.append("-")
            lines.append(f"| {ds} | {n} | " + " | ".join(cells) + " |")
            rows.append(rec)
        lines += ["", t["sheet_note"], ""]
        (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
        pd.DataFrame(rows).to_csv(out_dir / "summary.csv", index=False)
        written += [out_dir / "summary.md", out_dir / "summary.csv"]
    return written
