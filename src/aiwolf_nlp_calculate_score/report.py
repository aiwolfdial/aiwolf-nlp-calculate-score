"""参加者に見せる指標表 (5 系統) の中身と組版。

大会中に配信する `metrics.txt` (live) と、大会後に配る `all_team/` の md / csv が
同じ定義・同じ並びになるよう、表の構成をここ 1 箇所に置く。

各行は「回数 → 期待 (または合計) → スコア」の並びで、割り算すればスコアになる。
エラー挙動だけは「回数 (機会)」の文字列セル。
"""

from __future__ import annotations

import unicodedata

import pandas as pd

from .metrics import team_summary, win_rate_variants
from .parser import VILLAGER_SIDE

ROLE_ORDER = ["VILLAGER", "SEER", "MEDIUM", "BODYGUARD", "POSSESSED", "WEREWOLF"]
ROLE_LABEL = {
    "ja": {"VILLAGER": "村人", "SEER": "占い師", "MEDIUM": "霊媒師",
           "BODYGUARD": "騎士", "POSSESSED": "狂人", "WEREWOLF": "人狼"},
    "en": {"VILLAGER": "Villager", "SEER": "Seer", "MEDIUM": "Medium",
           "BODYGUARD": "Bodyguard", "POSSESSED": "Possessed", "WEREWOLF": "Werewolf"},
}

# 数えた値: 列名 -> (seats の列, 絞り込み, 補集合にするか)
_COUNTS: dict[str, tuple[str, dict | None, bool]] = {
    "追放回数": ("executed", None, False), "追放期待": ("execute_exp", None, False),
    "被占回数": ("divined_recv", None, False), "被占期待": ("divined_exp", None, False),
    "被護回数": ("guarded_recv", None, False), "被護期待": ("guarded_exp", None, False),
    "被襲回数": ("attacked_recv", None, False), "被襲期待": ("attacked_exp", None, False),
    "候補回数": ("attack_vote_recv", None, False), "候補期待": ("attack_vote_exp", None, False),
    "占的中": ("divine_hit", None, False), "占期待": ("divine_hit_exp", None, False),
    "村的中": ("vote_to_wolf", {"camp": VILLAGER_SIDE}, False),
    "村期待": ("vote_to_wolf_exp", {"camp": VILLAGER_SIDE}, False),
    "狂非狼": ("vote_to_wolf", {"role": "POSSESSED"}, True),
    "狂合計": ("vote_cast", {"role": "POSSESSED"}, False),
    "撃回数": ("vote_to_other_wolf", {"role": "WEREWOLF", "n_werewolves": 2}, False),
    "撃合計": ("vote_cast", {"role": "WEREWOLF", "n_werewolves": 2}, False),
}
_FLOAT_COUNTS = {"追放期待", "被占期待", "被護期待", "被襲期待", "候補期待", "占期待", "村期待"}

SCORE_COLUMNS = ["追放されやすさ", "占われやすさ", "守られやすさ", "襲われやすさ", "襲撃候補にされやすさ",
                 "占い精度", "投票精度_村人陣営", "人狼以外への投票率_狂人", "人狼への投票率_人狼"]
ERROR_CELLS = ["エラー_未実行", "エラー_自分", "エラー_無発言", "エラー_連投"]


def _g(seats: pd.DataFrame, col: str, flt: dict | None) -> pd.Series:
    sub = seats
    for k, v in (flt or {}).items():
        sub = sub[sub[k] >= v] if k == "n_werewolves" else sub[sub[k] == v]
    return sub.groupby("team")[col].sum()


def _err_cell(s: pd.Series, nums: list[str], dens: list[str]) -> str:
    if all(s[d] == 0 for d in dens):
        return "-"
    return ("/".join(str(int(s[n])) for n in nums) + " ("
            + "/".join(str(int(s[d])) for d in dens) + ")")


def metric_frame(seats: pd.DataFrame, progress: pd.DataFrame | None = None) -> pd.DataFrame:
    """チーム × 全列 (進捗・勝率・数えた値・スコア・エラー挙動) の表。"""
    summary = team_summary(seats).set_index("team")
    wins = win_rate_variants(seats).set_index("team")
    out = pd.DataFrame(index=summary.index)
    out["ゲーム数"] = summary["ゲーム数"]
    out["引いた役職数"] = wins["roles_played"]
    for c in [c for c in wins.columns if c.startswith("勝率")]:
        out[c] = wins[c]
    for name, (col, flt, complement) in _COUNTS.items():
        vals = _g(seats, col, flt)
        if complement:
            total = _g(seats, "vote_cast", flt)
            vals = total.reindex(out.index).fillna(0) - vals.reindex(out.index).fillna(0)
        out[name] = vals.reindex(out.index)
        if name not in _FLOAT_COUNTS:
            out[name] = out[name].fillna(0).round().astype("Int64")
    for c in SCORE_COLUMNS:
        out[c] = summary[c]
    out["エラー_未実行"] = summary.apply(lambda s: _err_cell(
        s, ["投票未実行", "襲撃投票未実行", "占い未実行", "護衛未実行"],
        ["投票機会", "襲撃投票機会", "占い機会", "護衛機会"]), axis=1)
    out["エラー_自分"] = summary.apply(lambda s: (
        "-" if s["投票数"] == 0 and s["襲撃投票数"] == 0 else
        f"{int(s['自己投票'])}/{int(s['仲間を襲撃指名'])}/0/0 "
        f"({int(s['投票数'])}/{int(s['襲撃投票数'])}/{int(s['占い機会'])}/{int(s['護衛機会'])})"), axis=1)
    out["エラー_無発言"] = summary.apply(lambda s: _err_cell(s, ["無発言日"], ["発言日数"]), axis=1)
    out["エラー_連投"] = summary.apply(lambda s: _err_cell(s, ["同一発言連投"], ["発言数"]), axis=1)
    if progress is not None:
        out = out.reindex(out.index.union(progress.index))
        out = progress.join(out, how="right")
        out["ゲーム数"] = out["ゲーム数"].fillna(0).astype(int)
    out.index.name = "team"
    return out.reset_index()


# 表の構成。groups は (名ja, 名en, [(列, 名ja, 名en)...], (スコア列, 名ja, 名en))
TABLES = [
    dict(key="progress", ja="進捗と勝率", en="Progress and win rate",
         cols=[("予定", "予定", "Planned"), ("消化", "消化", "Played"), ("残り", "残り", "Left"),
               ("ゲーム数", "集計", "Counted"), ("引いた役職数", "役職数", "Roles"),
               ("勝率_macro", "勝率M", "Win-M"), ("勝率_micro", "勝率m", "Win-m"),
               ("勝率_weighted_micro", "勝率w", "Win-w")],
         defs_ja=[("予定/消化/残り", "そのチームの予定・決着済み・未実施の試合数"),
                  ("集計", "指標の計算に使った試合数"),
                  ("役職数", "1度以上引いた役職の数"),
                  ("勝率M", "全 seat をプールした素の勝率。実際にどれだけ勝ったか"),
                  ("勝率m", "役職別勝率の単純平均。村人1体と占い師1体を同じ重みで扱う"),
                  ("勝率w", "役職別勝率を村の役職構成で平均し直したもの。配役の偏りを打ち消した勝率")],
         defs_en=[("Planned/Played/Left", "games scheduled, finished, and not yet played"),
                  ("Counted", "games used to compute the metrics"),
                  ("Roles", "number of distinct roles drawn at least once"),
                  ("Win-M", "win rate over all seats pooled. What actually happened"),
                  ("Win-m", "unweighted mean of the per-role win rates"),
                  ("Win-w", "per-role win rates re-averaged with the village's role composition. Removes the luck of the draw")]),
    dict(key="roles", ja="役職別勝率", en="Win rate by role",
         defs_ja=[(None, "その役職で戦ったときの勝率と（勝ち数 / 試合数）。その村に無い役職は列ごと出ない。試合数の少ない役職は偶然の振れが大きいので、括弧の中の試合数と併せて読むこと。")],
         defs_en=[(None, "Win rate when playing that role, with (wins / games). Roles absent from this village size are not shown. Roles with few games swing on chance, so read the counts in parentheses too.")]),
    dict(key="lift", ja="〜やすさ", en="Targeting (vs random)",
         groups=[("追放された", "Executed", [("追放回数", "回数", "count"), ("追放期待", "期待", "expected")],
                  ("追放されやすさ", "追放されやすさ", "Execution likelihood")),
                 ("占い師に占われた", "Divined by the seer", [("被占回数", "回数", "count"), ("被占期待", "期待", "expected")],
                  ("占われやすさ", "占われやすさ", "Divination targeting")),
                 ("騎士に護衛された", "Guarded by the bodyguard", [("被護回数", "回数", "count"), ("被護期待", "期待", "expected")],
                  ("守られやすさ", "守られやすさ", "Guard targeting")),
                 ("人狼に襲撃された", "Attacked by werewolves", [("被襲回数", "回数", "count"), ("被襲期待", "期待", "expected")],
                  ("襲われやすさ", "襲われやすさ", "Attack targeting")),
                 ("襲撃候補に挙がった", "Named in attack votes", [("候補回数", "回数", "count"), ("候補期待", "期待", "expected")],
                  ("襲撃候補にされやすさ", "襲撃候補にされやすさ", "Attack-vote targeting"))],
         defs_ja=[("追放されやすさ", "追放された回数 ÷ ランダムに追放されたときの期待回数"),
                  ("占われやすさ", "占われた回数 ÷ ランダムに占われたときの期待回数"),
                  ("守られやすさ", "護衛された回数 ÷ ランダムに護衛されたときの期待回数。騎士のいる村のみ"),
                  ("襲われやすさ", "人狼に襲撃された回数 ÷ ランダムに襲撃されたときの期待回数。人狼以外が対象"),
                  ("襲撃候補にされやすさ", "人狼の襲撃投票で名前が挙がった回数 ÷ ランダムに挙がったときの期待回数。人狼が1体の村では襲われやすさと一致する"),
                  (None, "※ ランダム = 1.0 とする。2.0 ならランダムの2倍。")],
         defs_en=[("Execution likelihood", "times executed / times expected at random"),
                  ("Divination targeting", "times divined / times expected at random"),
                  ("Guard targeting", "times guarded / times expected at random. Villages with a bodyguard only"),
                  ("Attack targeting", "times attacked by werewolves / times expected at random. Non-werewolves only"),
                  ("Attack-vote targeting", "times named in werewolves' attack votes / times expected at random. Equals attack targeting when the village has a single werewolf"),
                  (None, "* Random behaviour = 1.0; 2.0 means twice as often as random.")]),
    dict(key="accuracy", ja="精度", en="Accuracy (vs random)",
         groups=[("占い師のとき", "As the seer", [("占的中", "人狼を占った", "werewolves"), ("占期待", "期待", "expected")],
                  ("占い精度", "占い精度", "Divination accuracy")),
                 ("村人陣営のとき", "On the village side", [("村的中", "人狼に投票", "votes on wolves"), ("村期待", "期待", "expected")],
                  ("投票精度_村人陣営", "投票精度", "Vote accuracy"))],
         defs_ja=[("占い精度", "人狼を占った回数 ÷ ランダムに占ったときの期待回数"),
                  ("投票精度", "村人陣営のとき人狼に投票した票 ÷ ランダムに投票したときの期待票数"),
                  (None, "※ ランダム = 1.0 とする。")],
         defs_en=[("Divination accuracy", "werewolves divined / times expected at random"),
                  ("Vote accuracy", "votes on werewolves while on the village side / votes expected at random"),
                  (None, "* Random behaviour = 1.0.")]),
    dict(key="coordination", ja="連携", en="Coordination",
         groups=[("狂人のとき", "As the possessed", [("狂非狼", "人狼以外に投票", "votes on non-wolves"), ("狂合計", "投票の合計", "votes cast")],
                  ("人狼以外への投票率_狂人", "人狼を避けた率", "Non-werewolf vote rate")),
                 ("人狼のとき", "As a werewolf", [("撃回数", "仲間に投票", "votes on teammates"), ("撃合計", "投票の合計", "votes cast")],
                  ("人狼への投票率_人狼", "仲間撃ち率", "Teammate-vote rate"))],
         defs_ja=[("人狼を避けた率", "狂人のとき人狼以外に投票した割合。狂人は仲間の人狼を知らされないので、避けるには自力で人狼を推定する必要がある"),
                  ("仲間撃ち率", "人狼のとき自分以外の人狼に投票した割合。人狼が2体以上の村のみ。単純な誤りの場合もあれば、疑いを逸らして信頼を得る戦略の場合もある")],
         defs_en=[("Non-werewolf vote rate", "share of votes on non-werewolves while possessed. The possessed is not told who the werewolves are, so avoiding them requires inferring it"),
                  ("Teammate-vote rate", "share of votes on the other werewolf while a werewolf. Villages with two or more werewolves only. Not necessarily a mistake: it can be a way to deflect suspicion")]),
    dict(key="errors", ja="エラー挙動", en="Error behaviour",
         cols=[("エラー_未実行", "未実行 投票/襲撃/占い/護衛", "No action vote/attack/divine/guard"),
               ("エラー_自分", "自分を対象 投票/襲撃/占い/護衛", "Self-target vote/attack/divine/guard"),
               ("エラー_無発言", "無発言の日", "Silent days"),
               ("エラー_連投", "同一発言の連投", "Repeated talks")],
         defs_ja=[(None, "セルは 回数 (機会)。0 が正常。"),
                  ("未実行", "その行動を取れたのに記録が無い回数。機会は、投票なら生存して迎えた投票日、襲撃・占い・護衛はその役職で迎えた夜の数"),
                  ("自分を対象", "投票で自分に入れた回数、襲撃投票で人狼 (自分を含む) を指名した回数。占いと護衛の自己対象はサーバが拒否するので常に 0"),
                  ("無発言の日", "発言が Over / Skip だけだった日数 ÷ 発言できた日数"),
                  ("同一発言の連投", "直前と同じ本文を続けて発言した回数 ÷ 発言数")],
         defs_en=[(None, "Cells are count (opportunities). 0 is normal."),
                  ("No action", "times the action was possible but not recorded. Opportunities: voting days alive for votes; nights in that role for attack votes, divinations and guards"),
                  ("Self-target", "votes cast on oneself, and attack votes naming a werewolf (including oneself). The server rejects self-divination and self-guard, so those are always 0"),
                  ("Silent days", "days whose only talks were Over / Skip, over days with a talk phase"),
                  ("Repeated talks", "talks identical to the previous one by the same agent, over talks")]),
]


def role_win_table(seats: pd.DataFrame, teams: list[str]) -> pd.DataFrame:
    counts = (seats.groupby(["team", "role"])["win"].agg(["sum", "size"]) if len(seats) else None)
    rows = []
    for team in teams:
        row = {"team": team}
        for role in ROLE_ORDER:
            cell = "-"
            if counts is not None and (team, role) in counts.index:
                wins, games = counts.loc[(team, role)]
                if games:
                    cell = f"{wins / games:.3f} ({int(wins)}/{int(games)})"
            row[role] = cell
        rows.append(row)
    out = pd.DataFrame(rows)
    empty = [r for r in ROLE_ORDER if r in out.columns and (out[r] == "-").all()]
    return out.drop(columns=empty)


def fmt(v, digits: int) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "-"
    if isinstance(v, str):
        return v
    return f"{float(v):.{digits}f}" if digits else f"{round(float(v))}"


def table_layout(spec: dict, frame: pd.DataFrame, lang: str):
    li = 1 if lang == "ja" else 2
    heads, keys, digits, is_score, spans = ["team"], [None], [None], [False], [(None, 1)]
    if "groups" in spec:
        for grp in spec["groups"]:
            gname, cols, score = grp[li - 1], grp[2], grp[3]
            if score[0] not in frame.columns:
                continue
            n = 0
            for key, ja, en in cols:
                if key not in frame.columns:
                    continue
                heads.append(ja if lang == "ja" else en)
                keys.append(key)
                digits.append(1 if key in _FLOAT_COUNTS else 0)
                is_score.append(False)
                n += 1
            heads.append(score[li])
            keys.append(score[0])
            digits.append(3)
            is_score.append(True)
            spans.append((gname, n + 1))
    else:
        for key, ja, en in spec["cols"]:
            if key not in frame.columns:
                continue
            heads.append(ja if lang == "ja" else en)
            keys.append(key)
            digits.append(3 if key.startswith("勝率") else 0)
            is_score.append(False)
            spans.append((None, 1))
    return heads, keys, digits, is_score, spans


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s: str, n: int) -> str:
    return str(s) + " " * max(0, n - width(s))


def lpad(s: str, n: int) -> str:
    return " " * max(0, n - width(s)) + str(s)


def center(s: str, n: int) -> str:
    left = max(0, n - width(s)) // 2
    return " " * left + str(s) + " " * max(0, n - width(s) - left)


def _cells(spec, frame, lang):
    heads, keys, digits, is_score, spans = table_layout(spec, frame, lang)
    disp_h = [f"| {h} |" if is_score[i] else h for i, h in enumerate(heads)]
    rows = []
    for _, r in frame.iterrows():
        cells = []
        for i, k in enumerate(keys):
            v = r["team"] if k is None else fmt(r[k], digits[i])
            cells.append(f"| {v} |" if is_score[i] else v)
        rows.append(cells)
    return disp_h, rows, spans


def render_table_text(spec, frame, lang, role_tbl=None) -> list[str]:
    title = spec["ja"] if lang == "ja" else spec["en"]
    if spec["key"] == "roles":
        if role_tbl is None or role_tbl.empty:
            return []
        roles = [c for c in role_tbl.columns if c != "team"]
        disp_h = ["team"] + [ROLE_LABEL[lang][r] for r in roles]
        rows = [[r["team"]] + [r[c] for c in roles] for _, r in role_tbl.iterrows()]
        spans = [(None, 1)] * len(disp_h)
    else:
        disp_h, rows, spans = _cells(spec, frame, lang)
    if len(disp_h) <= 1:
        return []
    w = [max(width(c) for c in col) for col in zip(*([disp_h] + rows))]
    idx = 0
    for gname, n in spans:
        if gname is not None:
            need, have = width(f"― {gname} ―"), sum(w[idx:idx + n]) + (n - 1)
            if need > have:
                extra, rem = divmod(need - have, n)
                for j in range(n):
                    w[idx + j] += extra + (1 if j < rem else 0)
        idx += n
    out = [f"【{title}】" if lang == "ja" else f"[{title}]"]
    if any(g for g, _ in spans):
        line, idx = "  ", 0
        for gname, n in spans:
            seg = sum(w[idx:idx + n]) + (n - 1)
            line += (" " * seg if gname is None else center(f"― {gname} ―", seg)) + " "
            idx += n
        out.append(line.rstrip())
    out.append("  " + pad(disp_h[0], w[0]) + " " + " ".join(lpad(h, w[i]) for i, h in enumerate(disp_h[1:], 1)))
    out.append("  " + "-" * (sum(w) + len(w)))
    for row in rows:
        out.append("  " + pad(row[0], w[0]) + " " + " ".join(lpad(c, w[i]) for i, c in enumerate(row[1:], 1)))
    return out + [""] + _defs_text(spec, lang) + [""]


def render_table_md(spec, frame, lang, role_tbl=None) -> list[str]:
    title = spec["ja"] if lang == "ja" else spec["en"]
    if spec["key"] == "roles":
        if role_tbl is None or role_tbl.empty:
            return []
        roles = [c for c in role_tbl.columns if c != "team"]
        disp_h = ["team"] + [ROLE_LABEL[lang][r] for r in roles]
        rows = [[r["team"]] + [r[c] for c in roles] for _, r in role_tbl.iterrows()]
    else:
        heads, keys, digits, is_score, spans = table_layout(spec, frame, lang)
        if len(heads) <= 1:
            return []
        groups = []
        for gname, n in spans:
            groups += [gname] * n
        disp_h = [h if groups[i] is None else f"{groups[i]}<br>{h}" for i, h in enumerate(heads)]
        rows = [[(r["team"] if k is None else fmt(r[k], digits[i])) for i, k in enumerate(keys)]
                for _, r in frame.iterrows()]
    out = [f"## {title}", "", "| " + " | ".join(disp_h) + " |", "|" + "---|" + "---:|" * (len(disp_h) - 1)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out + [""] + _defs_md(spec, lang) + [""]


def _wrap(text: str, limit: int) -> list[str]:
    out, cur = [], ""
    for tok in text.replace("。", "。\u200b").split("\u200b"):
        for word in ([tok] if width(tok) <= limit else tok.split(" ")):
            cand = (cur + (" " if cur and not cur.endswith("。") else "") + word).strip()
            if cur and width(cand) > limit:
                out.append(cur)
                cur = word.strip()
            else:
                cur = cand
    return out + ([cur] if cur else [])


def _defs_text(spec, lang, term_w: int = 16, limit: int = 80) -> list[str]:
    lines = []
    for term, desc in spec[f"defs_{lang}"]:
        if term is None:
            lines += ["    " + x for x in _wrap(desc, limit + term_w)]
            continue
        w = max(term_w, width(term) + 2)
        body = _wrap(desc, limit)
        lines.append("    " + pad(term, w) + body[0])
        lines += ["    " + " " * w + x for x in body[1:]]
    return lines


def _defs_md(spec, lang) -> list[str]:
    return [f"- **{term}** — {desc}" if term else f"- {desc}" for term, desc in spec[f"defs_{lang}"]]


def render_metrics_text(frame, role_tbl, lang, header: list[str]) -> str:
    body = []
    for spec in TABLES:
        body += render_table_text(spec, frame, lang, role_tbl)
    rule = "=" * max([100] + [width(x) for x in body])
    return "\n".join([rule] + header + [rule, ""] + body)


def render_metrics_md(frame, role_tbl, lang, header: list[str]) -> str:
    body = []
    for spec in TABLES:
        body += render_table_md(spec, frame, lang, role_tbl)
    return "\n".join(header + [""] + body)


def csv_frame(frame: pd.DataFrame, summary: pd.DataFrame, lang: str) -> pd.DataFrame:
    """all_team 用の CSV。表に出している列をその言語のラベルで並べ、エラー挙動は項目別の数値列にする。"""
    cols, names = ["team"], ["team"]
    for spec in TABLES:
        if spec["key"] in ("roles", "errors"):
            continue
        heads, keys, _d, is_score, spans = table_layout(spec, frame, lang)  # noqa: RUF059
        flat = []
        for gname, n in spans:
            flat += [gname] * n
        for i, (h, k) in enumerate(zip(heads[1:], keys[1:]), 1):
            if k in cols:
                continue
            g = flat[i] if i < len(flat) else None
            cols.append(k)
            names.append(h if (g is None or is_score[i]) else f"{g}_{h}")
    out = frame[cols].copy()
    out.columns = names
    err = summary.set_index("team")[["投票未実行", "投票機会", "自己投票", "投票数",
                                     "襲撃投票未実行", "襲撃投票機会", "仲間を襲撃指名", "襲撃投票数",
                                     "占い未実行", "占い機会", "護衛未実行", "護衛機会",
                                     "無発言日", "発言日数", "同一発言連投", "発言数"]]
    if lang == "en":
        err.columns = ["vote_missing", "vote_opportunities", "self_votes", "votes_cast",
                       "attack_vote_missing", "attack_vote_opportunities", "attack_votes_on_werewolf", "attack_votes_cast",
                       "divine_missing", "divine_opportunities", "guard_missing", "guard_opportunities",
                       "silent_days", "talk_days", "repeated_talks", "talks"]
    return out.merge(err.reset_index(), on="team", how="left")
