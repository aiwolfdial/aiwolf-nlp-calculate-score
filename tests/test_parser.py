from pathlib import Path

import pytest

from aiwolf_nlp_calculate_score.parser import LogFormatError, load_dataset, parse_log, split_records


def test_text_with_commas_quotes_and_newline_is_kept(track_dir: Path):
    issues = []
    g = parse_log(track_dir / "log" / "1000_a.log", "TEST", track_dir / "json" / "1000_a.json", issues=issues)
    assert not [i for i in issues if i.fatal]
    texts = {t for _d, _s, t in g.talks}
    assert 'Hello, everyone. "Rin" here, quotes and, commas.' in texts
    joined = [t for t in texts if "\n" in t]
    assert joined == ["I am the Seer. I will check @Kenji tonight.\nSecond line of the same talk mentions @Mio."]
    assert g.talk_times[0] == 1700000001
    assert g.attacks == [(1, -1, True)]
    assert g.nights() == [0, 1]
    assert g.winner == "VILLAGER"


def test_team_names_come_from_json_then_teams_list(track_dir: Path):
    ds = load_dataset(track_dir, teams=["alpha", "beta", "gamma", "delta2026", "epsilon"])
    assert [g.game_id for g in ds.games] == ["1000_a", "1001_b"]
    a, b = ds.games
    assert a.team_source == "json" and b.team_source == "teams"
    # 末尾が数字のチーム名でも一覧があれば壊れない
    assert a.agents[4].team == "delta2026" and b.agents[4].team == "delta2026"
    assert ds.missing_json == ["1001_b"]
    assert not [i for i in ds.issues if i.fatal]


def test_name_fallback_without_json_or_teams(track_dir: Path):
    ds = load_dataset(track_dir)
    b = ds.games[1]
    assert b.team_source == "name"
    assert b.agents[4].team == "delta"      # 末尾の数字を剥がすので誤る (だから json か一覧を要求する)


def test_json_mismatch_is_fatal(track_dir: Path):
    bad = (track_dir / "json" / "1000_a.json").read_text().replace('"team": "alpha"', '"team": "alpha", "role": "SEER"')
    (track_dir / "json" / "1000_a.json").write_text(bad.replace('"role": "VILLAGER", "role": "SEER"', '"role": "SEER"'))
    ds = load_dataset(track_dir)
    assert "1000_a" in ds.skipped
    assert any("食い違い" in i.message for i in ds.issues if i.fatal)


def test_unknown_kind_and_field_count_are_warnings(track_dir: Path):
    p = track_dir / "log" / "1000_a.log"
    p.write_text(p.read_text() + "\n2,mystery,1,2\n2,vote,1\n", encoding="utf-8")
    ds = load_dataset(track_dir)
    msgs = [i.message for i in ds.issues if i.game_id == "1000_a"]
    assert any("未知の行種別" in m for m in msgs) and any("フィールド数" in m for m in msgs)
    assert "1000_a" not in ds.skipped
    assert "1000_a" in load_dataset(track_dir, strict=True).skipped


def test_invalid_utf8_is_an_error(tmp_path: Path):
    p = tmp_path / "x.log"
    p.write_bytes(b"0,status,1,VILLAGER,ALIVE,a1,\xff\n")
    with pytest.raises(LogFormatError):
        parse_log(p, "T")


def test_split_records_rejects_garbage_at_start():
    with pytest.raises(LogFormatError):
        split_records("garbage\n0,status,1,VILLAGER,ALIVE,a1,Rin")
