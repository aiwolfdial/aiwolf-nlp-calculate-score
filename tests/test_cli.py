from pathlib import Path

from aiwolf_nlp_calculate_score.cli import main


def test_check_and_run(track_dir: Path, tmp_path: Path, capsys):
    teams = tmp_path / "teams.yml"
    teams.write_text("track_5:\n  - alpha\n  - beta\n  - gamma\n  - delta2026\n  - epsilon\n")
    assert main(["check", str(track_dir), "--teams", str(teams)]) == 0
    # json が無い試合があり、一覧も無ければ check は 1 を返す
    assert main(["check", str(track_dir)]) == 1
    out = tmp_path / "out"
    assert main(["run", str(track_dir), "--teams", str(teams), "-o", str(out)]) == 0
    assert (out / "TEST_Track5" / "team_summary.csv").is_file()
    assert (out / "teams" / "TEST_Track5" / "all_team" / "ja" / "txt" / "metrics.txt").is_file()
    assert main(["teams", str(track_dir), "--teams", str(teams), "-o", str(out), "--team", "beta"]) == 0
    assert (out / "teams" / "TEST_Track5" / "beta" / "ja" / "md" / "summary.md").is_file()
    assert (out / "teams" / "by_team" / "beta" / "en" / "summary.md").is_file()
    assert not (out / "teams" / "by_team" / "alpha").exists()
