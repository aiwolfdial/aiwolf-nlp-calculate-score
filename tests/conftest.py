"""合成ログ (架空のチーム名と発言) で動作確認する。実際の大会ログはリポジトリに含めない。"""
import json
from pathlib import Path

import pytest

# 5 人村 1 試合。本文にカンマ・引用符・改行を含む発話、タイムスタンプ付き、襲撃無しの夜 (-1) を含む。
GAME_A = """0,status,1,VILLAGER,ALIVE,alpha1,Rin
0,status,2,SEER,ALIVE,beta1,May
0,status,3,WEREWOLF,ALIVE,gamma1,Kenji
0,status,4,POSSESSED,ALIVE,delta2026,Yumi
0,status,5,VILLAGER,ALIVE,epsilon1,Mio
0,talk,0,0,1,Hello, everyone. "Rin" here, quotes and, commas.,1700000001
0,talk,1,0,2,I am the Seer. I will check @Kenji tonight.
Second line of the same talk mentions @Mio.,1700000002
0,talk,2,0,3,Over,1700000003
0,talk,3,0,4,Over,1700000004
0,talk,4,0,5,Over,1700000005
0,divine,2,3,WEREWOLF
1,status,1,VILLAGER,ALIVE,alpha1,Rin
1,status,2,SEER,ALIVE,beta1,May
1,status,3,WEREWOLF,ALIVE,gamma1,Kenji
1,status,4,POSSESSED,ALIVE,delta2026,Yumi
1,status,5,VILLAGER,ALIVE,epsilon1,Mio
1,talk,0,0,2,Kenji is a werewolf. Vote @Kenji.,1700000010
1,talk,1,0,1,Agreed.,1700000011
1,talk,2,0,1,Agreed.,1700000012
1,talk,3,0,3,May is lying!,1700000013
1,talk,4,0,4,Skip,1700000014
1,vote,1,3
1,vote,2,3
1,vote,3,2
1,vote,4,4
1,execute,3,WEREWOLF
1,attack,-1,true
2,status,1,VILLAGER,ALIVE,alpha1,Rin
2,status,2,SEER,ALIVE,beta1,May
2,status,3,WEREWOLF,DEAD,gamma1,Kenji
2,status,4,POSSESSED,ALIVE,delta2026,Yumi
2,status,5,VILLAGER,ALIVE,epsilon1,Mio
2,result,4,0,VILLAGER"""

# 同じ構成の 2 試合目 (日本語トラック風: タイムスタンプ無し)。人狼が勝つ。
GAME_B = """0,status,1,SEER,ALIVE,alpha1,リン
0,status,2,VILLAGER,ALIVE,beta1,メイ
0,status,3,POSSESSED,ALIVE,gamma1,ケンジ
0,status,4,WEREWOLF,ALIVE,delta2026,ユミ
0,status,5,VILLAGER,ALIVE,epsilon1,ミオ
0,talk,0,0,1,占い師です。メイを占います。
0,talk,1,0,4,了解、メイン議題はそれで。
0,divine,1,2,HUMAN
1,status,1,SEER,ALIVE,alpha1,リン
1,status,2,VILLAGER,ALIVE,beta1,メイ
1,status,3,POSSESSED,ALIVE,gamma1,ケンジ
1,status,4,WEREWOLF,ALIVE,delta2026,ユミ
1,status,5,VILLAGER,ALIVE,epsilon1,ミオ
1,talk,0,0,4,リンは偽物。
1,vote,1,4
1,vote,2,1
1,vote,3,1
1,vote,4,1
1,vote,5,1
1,execute,1,SEER
1,attackVote,4,5
1,attack,5,true
2,status,1,SEER,DEAD,alpha1,リン
2,status,2,VILLAGER,ALIVE,beta1,メイ
2,status,3,POSSESSED,ALIVE,gamma1,ケンジ
2,status,4,WEREWOLF,ALIVE,delta2026,ユミ
2,status,5,VILLAGER,DEAD,epsilon1,ミオ
2,result,1,1,WEREWOLF"""


def _json_for(agents: list[tuple[int, str, str, str]], win_side: str) -> str:
    return json.dumps({"agents": [{"idx": i, "name": n, "role": r, "team": t} for i, n, r, t in agents],
                       "win_side": win_side, "entries": []})


@pytest.fixture
def track_dir(tmp_path: Path) -> Path:
    d = tmp_path / "TEST_Track5"
    (d / "log").mkdir(parents=True)
    (d / "json").mkdir()
    (d / "log" / "1000_a.log").write_text(GAME_A, encoding="utf-8")
    (d / "log" / "1001_b.log").write_text(GAME_B, encoding="utf-8")
    (d / "json" / "1000_a.json").write_text(_json_for(
        [(1, "alpha1", "VILLAGER", "alpha"), (2, "beta1", "SEER", "beta"), (3, "gamma1", "WEREWOLF", "gamma"),
         (4, "delta2026", "POSSESSED", "delta2026"), (5, "epsilon1", "VILLAGER", "epsilon")], "VILLAGER"))
    # 1001_b は json 無し (teams.yml で解決する経路のテスト用)
    return d


# 決着していない試合 (result 行なし、json の win_side が空)。prepare は success/ へ運ばない。
GAME_C_UNFINISHED = GAME_A.split("\n1,vote,1,3")[0]


@pytest.fixture
def raw_track_dir(track_dir: Path) -> Path:
    """サーバ出力そのまま (未決着の試合が混ざった log/ と json/)。"""
    (track_dir / "log" / "1002_c.log").write_text(GAME_C_UNFINISHED, encoding="utf-8")
    (track_dir / "json" / "1002_c.json").write_text(json.dumps({"agents": [], "win_side": "", "entries": []}))
    (track_dir / "json" / "1001_b.json").write_text(_json_for(
        [(1, "alpha1", "SEER", "alpha"), (2, "beta1", "VILLAGER", "beta"), (3, "gamma1", "POSSESSED", "gamma"),
         (4, "delta2026", "WEREWOLF", "delta2026"), (5, "epsilon1", "VILLAGER", "epsilon")], "WEREWOLF"))
    return track_dir
