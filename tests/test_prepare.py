from pathlib import Path

from aiwolf_nlp_calculate_score.cli import main
from aiwolf_nlp_calculate_score.parser import load_dataset


def test_prepare_copies_only_finished_games(raw_track_dir: Path, capsys):
    assert main(["prepare", str(raw_track_dir)]) == 0
    assert sorted(p.name for p in (raw_track_dir / "log" / "success").glob("*.log")) == ["1000_a.log", "1001_b.log"]
    assert sorted(p.name for p in (raw_track_dir / "json" / "success").glob("*.json")) == ["1000_a.json", "1001_b.json"]
    # 元ファイルは残る
    assert (raw_track_dir / "log" / "1002_c.log").is_file()
    # 2 回目は何もコピーしない (冪等)
    main(["prepare", str(raw_track_dir)])
    assert "すべてコピー済み" in capsys.readouterr().out


def test_check_reads_success_only_after_prepare(raw_track_dir: Path):
    # prepare 前: 未決着の試合は致命的として除外される
    ds = load_dataset(raw_track_dir)
    assert "1002_c" in ds.skipped and len(ds.games) == 2
    main(["prepare", str(raw_track_dir), "--quiet"])
    ds = load_dataset(raw_track_dir)
    assert [g.game_id for g in ds.games] == ["1000_a", "1001_b"] and not ds.skipped and not ds.missing_json
    assert main(["check", str(raw_track_dir)]) == 0


def test_run_does_prepare_check_and_aggregate(raw_track_dir: Path, tmp_path: Path, capsys):
    out = tmp_path / "out"
    assert main(["run", str(raw_track_dir), "-o", str(out), "--yes"]) == 0
    assert (raw_track_dir / "log" / "success" / "1001_b.log").is_file()
    assert (out / "TEST_Track5" / "team_summary.csv").is_file()
    # 2 回目の prepare は「すべてコピー済み」
    main(["prepare", str(raw_track_dir)])
    assert "すべてコピー済み" in capsys.readouterr().out
