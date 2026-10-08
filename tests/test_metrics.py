from pathlib import Path

import pandas as pd

from aiwolf_nlp_calculate_score.metrics import aggregate, build_seat_table, team_summary
from aiwolf_nlp_calculate_score.parser import load_dataset
from aiwolf_nlp_calculate_score.report import metric_frame
from aiwolf_nlp_calculate_score.team_files import write_all_team_files, write_team_files, write_team_sheet

TEAMS = ["alpha", "beta", "gamma", "delta2026", "epsilon"]


def _seats(track_dir):
    ds = load_dataset(track_dir, teams=TEAMS)
    seats, hazard = build_seat_table(ds.games)
    return seats, hazard


def test_received_lifts_average_to_one(track_dir: Path):
    seats, _ = _seats(track_dir)
    for recv, exp in (("voted_recv", "voted_exp"), ("divined_recv", "divined_exp"),
                      ("attacked_recv", "attacked_exp"), ("attack_vote_recv", "attack_vote_exp")):
        assert abs(seats[recv].sum() / seats[exp].sum() - 1.0) < 1e-9


def test_error_behaviour_counts(track_dir: Path):
    seats, _ = _seats(track_dir)
    s = seats.set_index(["game_id", "agent_idx"])
    # 試合 A: 5 人生存の投票日に 4 票 -> epsilon1 (idx 5) が無投票。delta2026 (idx 4) は自己投票
    assert s.loc[("1000_a", 5), "vote_missing"] == 1 and s.loc[("1000_a", 4), "vote_self"] == 1
    # 試合 A の夜 1: 人狼は追放済みなので襲撃投票の機会は無い。占い師は夜 0, 1 の 2 回占えて 1 回占った
    assert s.loc[("1000_a", 2), "divine_nights"] == 2 and s.loc[("1000_a", 2), "divine_missing"] == 1
    # 試合 B: 人狼 (idx 4) は夜 1 に襲撃投票した。試合 A の alpha1 は同じ発言を 2 回続けた
    assert s.loc[("1001_b", 4), "attack_vote_nights"] == 1 and s.loc[("1001_b", 4), "attack_vote_missing"] == 0
    assert s.loc[("1000_a", 1), "dup_talks"] == 1
    # Over / Skip だけの日
    assert s.loc[("1000_a", 3), "silent_days"] == 1


def test_katakana_mention_boundary(track_dir: Path):
    seats, _ = _seats(track_dir)
    s = seats.set_index(["game_id", "agent_idx"])
    # 「メイン議題」は「メイ」への言及ではない。「メイを占います」は言及
    assert s.loc[("1001_b", 2), "mention_recv"] == 1


def test_summary_and_files(track_dir: Path, tmp_path: Path):
    seats, _ = _seats(track_dir)
    summ = team_summary(seats)
    assert set(summ["team"]) == set(TEAMS)
    row = summ.set_index("team").loc["beta"]
    assert row["ゲーム数"] == 2 and row["勝率_macro"] == 0.5
    frame = metric_frame(seats)
    assert "エラー_未実行" in frame.columns
    out = tmp_path / "out"
    assert write_all_team_files(seats, "TEST_Track5", out)
    assert write_team_files(seats, "TEST_Track5", "beta", out)
    assert write_team_sheet(seats, "beta", out)
    md = (out / "TEST_Track5" / "beta" / "ja" / "md" / "summary.md").read_text()
    assert "追放されやすさ" in md and "エラー挙動" in md
    csv = pd.read_csv(out / "TEST_Track5" / "all_team" / "en" / "csv" / "metrics.csv")
    assert "Attack targeting" in csv.columns
    by_role = aggregate(seats, ["dataset", "team", "role"])
    assert by_role["games"].sum() == len(seats)
