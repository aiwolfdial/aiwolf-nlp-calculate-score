"""ゲームスコアの算出。

集計の単位は **seat** (1 ゲームに参加した 1 エージェント)。チーム単位の値は seat の
分子・分母を足してから割る。したがって逐次 (live) で積み上げた値とトラック終了後に
まとめて計算した値は必ず一致する。

「〜やすさ」は **実測回数 ÷ ランダムだったときの期待回数** (1.0 がランダム相当)。
期待回数は「実際に起きた出来事を、その時点の候補者に均等に配った量」なので、
候補者の人数 (村の大きさ、生存者数) が違っても比べられる。

5 系統:
    勝率        win_rate (3 種) と役職別
    〜やすさ    execute / voted / divined / guarded / mention / attacked / attack_vote の *_lift
    精度        divine_hit_lift, vote_to_wolf_lift (村人陣営)
    連携        狂人の人狼回避率, 人狼の仲間撃ち率
    エラー挙動  未実行 (投票 / 襲撃投票 / 占い / 護衛), 自分を対象, 無発言の日, 同一発言の連投
"""

from __future__ import annotations

import re
from collections import defaultdict

import numpy as np
import pandas as pd

from .parser import VILLAGER_SIDE, Game

AMBIGUOUS_CHARACTERS = frozenset({"May", "メイ"})
_KATAKANA = r"ァ-ヶーｦ-ﾟ"
SILENT_TEXTS = frozenset({"Over", "Skip", "[PASS]"})


def _name_pattern(name: str) -> str:
    escaped = re.escape(name)
    if name.isascii():
        return rf"\b{escaped}\b"
    return rf"(?<![{_KATAKANA}]){escaped}(?![{_KATAKANA}])"


def _mention_regexes(names: list[str]) -> tuple[re.Pattern, re.Pattern]:
    ordered = sorted(set(names), key=len, reverse=True)
    alternation = "|".join(_name_pattern(n) for n in ordered)
    at_alternation = "|".join(re.escape(n) for n in ordered)
    return (re.compile(f"(?P<name>{alternation})"),
            re.compile(rf"@\s*(?P<name>{at_alternation})"))


def _mentioned_names(pattern: re.Pattern, text: str) -> set[str]:
    return {m.group("name") for m in pattern.finditer(text)}


def _split_dataset(dataset: str) -> tuple[str, str]:
    tournament, _, track = dataset.partition("_")
    return tournament, track or dataset


def seat_records(game: Game) -> tuple[list[dict], list[dict]]:
    """1 ゲームから (seat 単位のレコード, 日別の追放ハザード) を作る。"""
    tournament, track = _split_dataset(game.dataset)
    idx_list = sorted(game.agents)
    roles = {i: game.agents[i].role for i in idx_list}
    wolves = {i for i, r in roles.items() if r == "WEREWOLF"}
    n_werewolves = len(wolves)
    name_re, at_re = _mention_regexes([game.agents[i].character for i in idx_list])

    def zero():
        return dict.fromkeys(idx_list, 0.0)
    voted_recv, voted_opp, voted_exp = zero(), zero(), zero()
    divined_recv, divined_opp, divined_exp = zero(), zero(), zero()
    guarded_recv, guarded_opp, guarded_exp = zero(), zero(), zero()
    attacked_recv, attacked_opp, attacked_exp = zero(), zero(), zero()
    av_recv, av_opp, av_exp = zero(), zero(), zero()
    mention_recv, mention_at_recv, mention_opp, mention_exp = zero(), zero(), zero(), zero()
    vote_cast, vote_to_wolf, vote_to_wolf_exp = zero(), zero(), zero()
    vote_to_other_wolf, vote_self = zero(), zero()
    divine_cast, divine_hit, divine_hit_exp = zero(), zero(), zero()
    # エラー挙動
    vote_missing = zero()
    av_cast, av_nights, av_missing, av_self = zero(), zero(), zero(), zero()
    divine_nights, divine_missing = zero(), zero()
    guard_nights, guard_missing = zero(), zero()
    talk_days, silent_days, talk_n, dup_talks = zero(), zero(), zero(), zero()
    executed_day: dict[int, float] = {}
    execute_exp = zero()
    at_risk: list[tuple[int, int, int]] = []

    # ---- 投票 (昼) ----------------------------------------------------------
    votes_by_day: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for day, voter, target in game.votes:
        votes_by_day[day].append((voter, target))

    for day, pairs in votes_by_day.items():
        alive = game.alive.get(day, set())
        executed_today = game.executed_on(day)
        cross = [(v, t) for v, t in pairs if v != t]
        share = 1.0 / (len(alive) - 1) if len(alive) > 1 else 0.0
        voters = {v for v, _t in pairs}
        for me in alive:
            others = [(v, t) for v, t in cross if v != me]
            voted_opp[me] += len(others)
            voted_recv[me] += sum(1 for _v, t in others if t == me)
            voted_exp[me] += len(others) * share
            at_risk.append((me, day, 1 if me in executed_today else 0))
            execute_exp[me] += len(executed_today) / len(alive)
            if me not in voters:
                vote_missing[me] += 1
        for voter, target in pairs:
            vote_cast[voter] += 1
            if voter == target:
                vote_self[voter] += 1
            if roles.get(target) == "WEREWOLF":
                vote_to_wolf[voter] += 1
                if voter != target:
                    vote_to_other_wolf[voter] += 1
            candidates = alive - {voter}
            if candidates:
                n_wolf = sum(1 for c in candidates if roles.get(c) == "WEREWOLF")
                vote_to_wolf_exp[voter] += n_wolf / len(candidates)

    # ---- 占い / 護衛 (受けた側) --------------------------------------------
    for events, recv, opp, exp in (
        (game.divines, divined_recv, divined_opp, divined_exp),
        (game.guards, guarded_recv, guarded_opp, guarded_exp),
    ):
        for day, actor, target in events:
            candidates = game.night_alive(day) - {actor}
            if not candidates:
                continue
            share = 1.0 / len(candidates)
            for me in candidates:
                opp[me] += 1
                exp[me] += share
            recv[target] = recv.get(target, 0.0) + 1

    # ---- 占い師が人狼を引けたか ----------------------------------------------
    for day, seer, target in game.divines:
        divine_cast[seer] += 1
        if roles.get(target) == "WEREWOLF":
            divine_hit[seer] += 1
        candidates = game.night_alive(day) - {seer}
        if candidates:
            n_wolf = sum(1 for c in candidates if roles.get(c) == "WEREWOLF")
            divine_hit_exp[seer] += n_wolf / len(candidates)

    # ---- 襲撃 / 襲撃投票 (受けた側)。候補はその夜の生存者から人狼を除いた者 -------
    for day, target, _success in game.attacks:
        if target < 0:
            continue
        candidates = game.night_alive(day) - wolves
        if not candidates:
            continue
        for me in candidates:
            attacked_opp[me] += 1
            attacked_exp[me] += 1.0 / len(candidates)
        if target in attacked_recv:
            attacked_recv[target] += 1
    for day, wolf, target in game.attack_votes:
        av_cast[wolf] += 1
        if target in wolves:
            av_self[wolf] += 1          # 仲間 (自分を含む) を襲撃先に指名した
            continue
        candidates = game.night_alive(day) - wolves
        if not candidates:
            continue
        for me in candidates:
            av_opp[me] += 1
            av_exp[me] += 1.0 / len(candidates)
        if target in av_recv:
            av_recv[target] += 1

    # ---- 夜の行動の未実行 ----------------------------------------------------
    acted: dict[tuple[str, int], set[int]] = defaultdict(set)
    for day, seer, _t in game.divines:
        acted[("divine", day)].add(seer)
    for day, bg, _t in game.guards:
        acted[("guard", day)].add(bg)
    for day, wolf, _t in game.attack_votes:
        acted[("attackVote", day)].add(wolf)
    for day in game.nights():
        for me in game.night_alive(day):
            role = roles[me]
            if role == "SEER":
                divine_nights[me] += 1
                divine_missing[me] += me not in acted[("divine", day)]
            elif role == "BODYGUARD" and day >= 1:
                guard_nights[me] += 1
                guard_missing[me] += me not in acted[("guard", day)]
            elif role == "WEREWOLF" and day >= 1:
                av_nights[me] += 1
                av_missing[me] += me not in acted[("attackVote", day)]

    # ---- 発言中の言及 / 無発言の日 / 連投 --------------------------------------
    per_day_talks: dict[tuple[int, int], list[str]] = defaultdict(list)
    for day, speaker, text in game.talks:
        per_day_talks[(day, speaker)].append(text.strip())
        alive = game.alive.get(day, set())
        listeners = alive - {speaker}
        if not listeners:
            continue
        named = _mentioned_names(name_re, text)
        at_named = _mentioned_names(at_re, text)
        n_named = sum(1 for me in listeners if game.agents[me].character in named)
        exp_share = n_named / len(listeners)
        for me in listeners:
            mention_opp[me] += 1
            mention_exp[me] += exp_share
            character = game.agents[me].character
            if character in named:
                mention_recv[me] += 1
            if character in at_named:
                mention_at_recv[me] += 1
    for (day, speaker), texts in per_day_talks.items():
        talk_days[speaker] += 1
        talk_n[speaker] += len(texts)
        if all(t in SILENT_TEXTS for t in texts):
            silent_days[speaker] += 1
        dup_talks[speaker] += sum(1 for i in range(1, len(texts))
                                  if texts[i] == texts[i - 1] and texts[i] not in SILENT_TEXTS)

    for day, idx in game.executes:
        executed_day[idx] = day

    final_alive = game.alive.get(max(game.alive), set())
    n_days = len(game.alive)
    rows = []
    for idx in idx_list:
        agent = game.agents[idx]
        ex_day = executed_day.get(idx)
        days_alive = sum(1 for d in game.alive if idx in game.alive[d])
        rows.append({
            "dataset": game.dataset, "tournament": tournament, "track": track,
            "n_players": game.n_players, "n_werewolves": n_werewolves,
            "game_id": game.game_id, "agent_idx": idx, "agent_name": agent.name,
            "team": agent.team, "role": agent.role, "camp": agent.camp,
            "character": agent.character, "winner": game.winner, "final_day": game.final_day,
            "win": int(agent.camp == game.winner),
            "survived": int(idx in final_alive),
            "survival_span_norm": days_alive / n_days if n_days else np.nan,
            "executed": int(ex_day is not None),
            "execute_at_risk": sum(1 for i, _d, _e in at_risk if i == idx),
            "execute_exp": execute_exp[idx],
            "executed_day": ex_day if ex_day is not None else np.nan,
            "executed_day_norm": (ex_day / game.final_day
                                  if ex_day is not None and game.final_day else np.nan),
            "voted_recv": voted_recv[idx], "voted_opp": voted_opp[idx], "voted_exp": voted_exp[idx],
            "divined_recv": divined_recv[idx], "divined_opp": divined_opp[idx], "divined_exp": divined_exp[idx],
            "guarded_recv": guarded_recv[idx], "guarded_opp": guarded_opp[idx], "guarded_exp": guarded_exp[idx],
            "attacked_recv": attacked_recv[idx], "attacked_opp": attacked_opp[idx], "attacked_exp": attacked_exp[idx],
            "attack_vote_recv": av_recv[idx], "attack_vote_opp": av_opp[idx], "attack_vote_exp": av_exp[idx],
            "mention_recv": mention_recv[idx], "mention_at_recv": mention_at_recv[idx],
            "mention_opp": mention_opp[idx], "mention_exp": mention_exp[idx],
            "vote_cast": vote_cast[idx], "vote_to_wolf": vote_to_wolf[idx],
            "vote_to_wolf_exp": vote_to_wolf_exp[idx], "vote_self": vote_self[idx],
            "vote_to_other_wolf": vote_to_other_wolf[idx],
            "divine_cast": divine_cast[idx], "divine_hit": divine_hit[idx], "divine_hit_exp": divine_hit_exp[idx],
            # エラー挙動
            "vote_missing": vote_missing[idx],
            "attack_vote_cast": av_cast[idx], "attack_vote_nights": av_nights[idx],
            "attack_vote_missing": av_missing[idx], "attack_vote_self": av_self[idx],
            "divine_nights": divine_nights[idx], "divine_missing": divine_missing[idx],
            "guard_nights": guard_nights[idx], "guard_missing": guard_missing[idx],
            "talk_days": talk_days[idx], "silent_days": silent_days[idx],
            "talk_n": talk_n[idx], "dup_talks": dup_talks[idx],
        })

    hazard = [
        {"dataset": game.dataset, "game_id": game.game_id, "team": game.agents[i].team,
         "role": game.agents[i].role, "camp": game.agents[i].camp,
         "day": d, "at_risk": 1, "executed": e}
        for i, d, e in at_risk
    ]
    return rows, hazard


SEAT_COLUMNS = [
    "dataset", "tournament", "track", "n_players", "n_werewolves", "game_id",
    "agent_idx", "agent_name", "team", "role", "camp", "character", "winner",
    "final_day", "win", "survived", "survival_span_norm",
    "executed", "execute_at_risk", "execute_exp", "executed_day", "executed_day_norm",
    "voted_recv", "voted_opp", "voted_exp",
    "divined_recv", "divined_opp", "divined_exp",
    "guarded_recv", "guarded_opp", "guarded_exp",
    "attacked_recv", "attacked_opp", "attacked_exp",
    "attack_vote_recv", "attack_vote_opp", "attack_vote_exp",
    "mention_recv", "mention_at_recv", "mention_opp", "mention_exp",
    "vote_cast", "vote_to_wolf", "vote_to_wolf_exp", "vote_self", "vote_to_other_wolf",
    "divine_cast", "divine_hit", "divine_hit_exp",
    "vote_missing", "attack_vote_cast", "attack_vote_nights", "attack_vote_missing", "attack_vote_self",
    "divine_nights", "divine_missing", "guard_nights", "guard_missing",
    "talk_days", "silent_days", "talk_n", "dup_talks",
]
HAZARD_COLUMNS = ["dataset", "game_id", "team", "role", "camp", "day", "at_risk", "executed"]


def build_seat_table(games: list[Game]) -> tuple[pd.DataFrame, pd.DataFrame]:
    seat_rows: list[dict] = []
    hazard_rows: list[dict] = []
    for game in games:
        rows, hazard = seat_records(game)
        seat_rows.extend(rows)
        hazard_rows.extend(hazard)
    return (pd.DataFrame(seat_rows or None, columns=SEAT_COLUMNS if not seat_rows else None),
            pd.DataFrame(hazard_rows or None, columns=HAZARD_COLUMNS if not hazard_rows else None))


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    with np.errstate(divide="ignore", invalid="ignore"):
        return num.div(den.replace(0, np.nan))


_SUM_COLS = [c for c in SEAT_COLUMNS if c not in (
    "dataset", "tournament", "track", "n_players", "n_werewolves", "game_id", "agent_idx",
    "agent_name", "team", "role", "camp", "character", "winner", "final_day",
    "survival_span_norm", "executed_day", "executed_day_norm")]


def aggregate(seats: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """seat テーブルを keys でまとめ、分子・分母を残したまま率と倍率を付ける。"""
    grouped = seats.groupby(keys, dropna=False)
    out = grouped[_SUM_COLS].sum()
    out.insert(0, "games", grouped.size())
    out["mean_survival_span"] = grouped["survival_span_norm"].mean()
    out["mean_executed_day"] = grouped["executed_day"].mean()
    out["mean_executed_day_norm"] = grouped["executed_day_norm"].mean()
    out["win_rate"] = _safe_div(out["win"], out["games"])
    out["survival_rate"] = _safe_div(out["survived"], out["games"])
    out["execute_rate"] = _safe_div(out["executed"], out["games"])
    out["execute_lift"] = _safe_div(out["executed"], out["execute_exp"])
    out["voted_rate"] = _safe_div(out["voted_recv"], out["voted_opp"])
    out["voted_lift"] = _safe_div(out["voted_recv"], out["voted_exp"])
    out["divined_rate"] = _safe_div(out["divined_recv"], out["divined_opp"])
    out["divined_lift"] = _safe_div(out["divined_recv"], out["divined_exp"])
    out["guarded_rate"] = _safe_div(out["guarded_recv"], out["guarded_opp"])
    out["guarded_lift"] = _safe_div(out["guarded_recv"], out["guarded_exp"])
    out["attacked_lift"] = _safe_div(out["attacked_recv"], out["attacked_exp"])
    out["attack_vote_lift"] = _safe_div(out["attack_vote_recv"], out["attack_vote_exp"])
    out["mention_rate"] = _safe_div(out["mention_recv"], out["mention_opp"])
    out["mention_at_rate"] = _safe_div(out["mention_at_recv"], out["mention_opp"])
    out["mention_lift"] = _safe_div(out["mention_recv"], out["mention_exp"])
    out["vote_to_wolf_rate"] = _safe_div(out["vote_to_wolf"], out["vote_cast"])
    out["vote_to_wolf_lift"] = _safe_div(out["vote_to_wolf"], out["vote_to_wolf_exp"])
    out["self_vote_rate"] = _safe_div(out["vote_self"], out["vote_cast"])
    out["divine_hit_rate"] = _safe_div(out["divine_hit"], out["divine_cast"])
    out["divine_hit_lift"] = _safe_div(out["divine_hit"], out["divine_hit_exp"])
    return out.reset_index()


ROLE_WEIGHTS: dict[str, float] | None = None
WIN_RATE_COLUMNS = ["勝率_macro", "勝率_micro", "勝率_weighted_micro"]


def role_weights_from_seats(seats: pd.DataFrame) -> pd.Series:
    return seats.groupby(["dataset", "role"]).size().rename("role_weight")


def win_rate_variants(seats: pd.DataFrame,
                      weights: dict[str, float] | None = None) -> pd.DataFrame:
    """dataset × team ごとに 3 種類の勝率 (macro / micro / weighted micro)。"""
    by_role = seats.groupby(["dataset", "team", "role"], dropna=False).agg(
        role_games=("win", "size"), role_wins=("win", "sum")).reset_index()
    by_role["role_win_rate"] = by_role["role_wins"] / by_role["role_games"]
    weights = ROLE_WEIGHTS if weights is None else weights
    if weights is None:
        by_role = by_role.merge(role_weights_from_seats(seats).reset_index(),
                                on=["dataset", "role"], how="left")
    else:
        missing = sorted(set(by_role["role"]) - set(weights))
        if missing:
            raise ValueError(f"weights に重みの無い役職がある: {missing}")
        by_role["role_weight"] = by_role["role"].map(weights).astype(float)
    by_role["_weighted"] = by_role["role_weight"] * by_role["role_win_rate"]
    out = by_role.groupby(["dataset", "team"]).agg(
        wins=("role_wins", "sum"), games=("role_games", "sum"),
        roles_played=("role", "nunique"), 勝率_micro=("role_win_rate", "mean"),
        _weight_sum=("role_weight", "sum"), _weighted_sum=("_weighted", "sum"))
    out["勝率_macro"] = _safe_div(out["wins"], out["games"])
    out["勝率_weighted_micro"] = _safe_div(out["_weighted_sum"], out["_weight_sum"])
    return out[["games", "roles_played", *WIN_RATE_COLUMNS]].reset_index()


# team_summary の列 (5 系統)。内部キーは日本語のまま (出力 CSV の列名)。
LIFT_COLUMNS = ["追放されやすさ", "狙われやすさ", "占われやすさ", "守られやすさ", "注目されやすさ",
                "襲われやすさ", "襲撃候補にされやすさ"]
ACCURACY_COLUMNS = ["占い精度", "投票精度_村人陣営"]
COORDINATION_COLUMNS = ["人狼以外への投票率_狂人", "人狼への投票率_人狼"]
ERROR_COLUMNS = ["投票未実行", "投票機会", "自己投票", "投票数",
                 "襲撃投票未実行", "襲撃投票機会", "仲間を襲撃指名", "襲撃投票数",
                 "占い未実行", "占い機会", "護衛未実行", "護衛機会",
                 "無発言日", "発言日数", "同一発言連投", "発言数"]


def team_summary(seats: pd.DataFrame) -> pd.DataFrame:
    """チーム間比較用の横持ちサマリ (dataset × team)。"""
    base = aggregate(seats, ["dataset", "team"]).set_index(["dataset", "team"])
    win_rates = win_rate_variants(seats).set_index(["dataset", "team"])

    def _vote_stats(mask: pd.Series):
        sub = seats[mask]
        if sub.empty:
            return (pd.Series(dtype=float),) * 3
        cols = ["vote_cast", "vote_to_wolf", "vote_to_wolf_exp", "vote_to_other_wolf"]
        g = sub.groupby(["dataset", "team"])[cols].sum()
        return (_safe_div(g["vote_to_wolf"], g["vote_cast"]),
                _safe_div(g["vote_to_wolf"], g["vote_to_wolf_exp"]),
                _safe_div(g["vote_to_other_wolf"], g["vote_cast"]))

    _, correct_villager_lift, _ = _vote_stats(seats["camp"] == VILLAGER_SIDE)
    wolf_by_possessed, _, _ = _vote_stats(seats["role"] == "POSSESSED")
    _, _, teammate_shot = _vote_stats((seats["role"] == "WEREWOLF") & (seats["n_werewolves"] >= 2))

    s = pd.DataFrame(index=base.index)
    s["ゲーム数"] = base["games"]
    s["引いた役職数"] = win_rates["roles_played"]
    for col in WIN_RATE_COLUMNS:
        s[col] = win_rates[col]
    s["生存率"] = base["survival_rate"]
    s["追放されやすさ"] = base["execute_lift"]
    s["狙われやすさ"] = base["voted_lift"]
    s["占われやすさ"] = base["divined_lift"]
    s["守られやすさ"] = base["guarded_lift"]
    s["注目されやすさ"] = base["mention_lift"]
    s["襲われやすさ"] = base["attacked_lift"]
    s["襲撃候補にされやすさ"] = base["attack_vote_lift"]
    s["占い精度"] = base["divine_hit_lift"]
    s["投票精度_村人陣営"] = correct_villager_lift
    s["人狼以外への投票率_狂人"] = 1 - wolf_by_possessed
    s["人狼への投票率_人狼"] = teammate_shot
    s["投票未実行"] = base["vote_missing"]
    s["投票機会"] = base["execute_at_risk"]
    s["自己投票"] = base["vote_self"]
    s["投票数"] = base["vote_cast"]
    s["襲撃投票未実行"] = base["attack_vote_missing"]
    s["襲撃投票機会"] = base["attack_vote_nights"]
    s["仲間を襲撃指名"] = base["attack_vote_self"]
    s["襲撃投票数"] = base["attack_vote_cast"]
    s["占い未実行"] = base["divine_missing"]
    s["占い機会"] = base["divine_nights"]
    s["護衛未実行"] = base["guard_missing"]
    s["護衛機会"] = base["guard_nights"]
    s["無発言日"] = base["silent_days"]
    s["発言日数"] = base["talk_days"]
    s["同一発言連投"] = base["dup_talks"]
    s["発言数"] = base["talk_n"]
    return s.reset_index()


def execute_hazard(hazard: pd.DataFrame) -> pd.DataFrame:
    """DAY 別の被追放率 (その日を生存して迎えた回数を分母にした条件付き率)。"""
    g = hazard.groupby(["dataset", "team", "day"])[["at_risk", "executed"]].sum()
    g["execute_rate_given_alive"] = _safe_div(g["executed"], g["at_risk"])
    return g.reset_index()
