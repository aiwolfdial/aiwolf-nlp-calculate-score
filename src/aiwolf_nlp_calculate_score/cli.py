"""コマンドライン (`uv run src/main.py <コマンド>`)。

    live    -c <サーバ設定.yml> [...]                 大会中にゲームスコアを配信する (常駐)
    run     <入力> [-o 出力] [--teams teams.yml]      終了したログからゲームスコアを計算する (prepare → check → 集計)
    teams   <入力> [-o 出力] [--team 名前 ...]        チームごとのゲームスコアをまとめる
    prepare <入力> [--dry-run]                        決着した試合の log と json を success/ へコピーする (run の第 1 段階)
    check   <入力> [--teams teams.yml]                検証だけ行う (run の第 2 段階。ファイルは書かない)

<入力> は <大会>_<トラック>/log と json を持つディレクトリを並べた親ディレクトリ
(`scripts/fetch_logs.sh` が作る data/input)。1 トラック分のディレクトリを直接渡してもよい。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from .metrics import aggregate, build_seat_table, execute_hazard, team_summary
from .parser import Dataset, load_dataset
from .sort_success import prepare_track
from .team_files import write_all_team_files, write_team_files, write_team_sheet


def _dataset_dirs(root: Path) -> list[Path]:
    if (root / "log").is_dir() or any(root.glob("*.log")):
        return [root]
    return sorted(d for d in root.iterdir() if d.is_dir() and ((d / "log").is_dir() or any(d.glob("*.log"))))


def _read_teams(path: Path | None) -> list[str] | None:
    """teams.yml (運営のマッチ生成用。トラック別のチーム名一覧) を読んで全チーム名を返す。"""
    if path is None:
        return None
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    names: set[str] = set()
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                names.update(str(x) for x in v)
    elif isinstance(data, list):
        names.update(str(x) for x in data)
    if not names:
        raise SystemExit(f"{path}: チーム名が読めない (トラック名: [チーム, ...] の形か、チームのリスト)")
    return sorted(names)


def _load_all(root: Path, teams: list[str] | None, strict: bool) -> list[Dataset]:
    dirs = _dataset_dirs(root)
    if not dirs:
        raise SystemExit(f"{root}: ログが見つからない")
    return [load_dataset(d, teams=teams, strict=strict) for d in dirs]


def _report(datasets: list[Dataset], out=sys.stderr) -> tuple[int, int]:
    """検証結果を表示し、(致命的な件数, 警告の件数) を返す。"""
    n_fatal = n_warn = 0
    for ds in datasets:
        fatal = [i for i in ds.issues if i.fatal]
        warn = [i for i in ds.issues if not i.fatal]
        # 致命的 = 除外した試合数 + データセット全体に対する致命的な指摘 (役職構成の不一致など)
        n_fatal += len(ds.skipped) + sum(1 for i in fatal if i.game_id == ds.name)
        n_warn += len(warn)
        comps = {g.role_counts for g in ds.games}
        comp = ", ".join(f"{r}×{n}" for r, n in next(iter(comps))) if len(comps) == 1 else "不一致"
        src = sorted({g.team_source for g in ds.games})
        print(f"== {ds.name}: {len(ds.games)} 試合 / {len({a.team for g in ds.games for a in g.agents.values()})} チーム"
              f" / 役職構成 {comp} / チーム名の出所 {src}", file=out)
        if ds.missing_json:
            print(f"   json が無い試合 {len(ds.missing_json)} 件 (例 {ds.missing_json[0]})"
                  + (" — チーム名はエージェント名から推定" if "name" in src else ""), file=out)
        if ds.skipped:
            print(f"   除外した試合 {len(ds.skipped)} 件", file=out)
        shown: dict[str, int] = {}
        for i in fatal + warn:
            key = i.message.split(" (")[0][:60]
            shown[key] = shown.get(key, 0) + 1
        for i in fatal:
            print(f"   {i}", file=out)
        for i in warn[:8]:
            print(f"   {i}", file=out)
        if len(warn) > 8:
            print(f"   ... 警告はほか {len(warn) - 8} 件", file=out)
    return n_fatal, n_warn


def cmd_prepare(args) -> int:
    dirs = _dataset_dirs(args.input)
    if not dirs:
        raise SystemExit(f"{args.input}: log/ を持つディレクトリが見つからない")
    bad = 0
    for d in dirs:
        st = prepare_track(d, dry_run=args.dry_run, quiet=args.quiet)
        if st is not None and (st.broken or st.stale_in_success):
            bad += 1
    return 1 if bad else 0


def cmd_check(args) -> int:
    datasets = _load_all(args.input, _read_teams(args.teams), args.strict)
    n_fatal, n_warn = _report(datasets)
    missing = sum(len(d.missing_json) for d in datasets)
    print(f"\n致命的 {n_fatal} 件 / 警告 {n_warn} 件 / json 無し {missing} 試合", file=sys.stderr)
    return 1 if n_fatal or (missing and not args.teams) else 0


def _confirm(msg: str) -> bool:
    if not sys.stdin.isatty():
        print(f"{msg} — 対話できないので中止します (--yes で続行)", file=sys.stderr)
        return False
    return input(f"{msg} 続けますか [y/N]: ").strip().lower() in ("y", "yes")


def cmd_run(args) -> int:
    if not args.no_prepare:
        print("--- 準備 (決着した試合を success/ へ) ---", file=sys.stderr)
        for d in _dataset_dirs(args.input):
            prepare_track(d, quiet=True)
        print("--- 確認 ---", file=sys.stderr)
    teams = _read_teams(args.teams)
    datasets = _load_all(args.input, teams, args.strict)
    n_fatal, _n_warn = _report(datasets)
    missing = sum(len(d.missing_json) for d in datasets)
    if (n_fatal or (missing and not teams)) and not args.yes:
        what = []
        if n_fatal:
            what.append(f"致命的な問題 {n_fatal} 件 (該当試合は除外)")
        if missing and not teams:
            what.append(f"json が無い試合 {missing} 件 (チーム名をエージェント名から推定)")
        if not _confirm(" / ".join(what) + "。"):
            return 1
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for ds in datasets:
        if not ds.games:
            continue
        seats, hazard = build_seat_table(ds.games)
        frames.append(seats)
        d = out / ds.name
        d.mkdir(parents=True, exist_ok=True)
        seats.to_csv(d / "seats.csv", index=False)
        team_summary(seats).to_csv(d / "team_summary.csv", index=False)
        aggregate(seats, ["dataset", "team", "role"]).to_csv(d / "team_by_role.csv", index=False)
        aggregate(seats, ["dataset", "team", "camp"]).to_csv(d / "team_by_camp.csv", index=False)
        if not hazard.empty:
            execute_hazard(hazard).to_csv(d / "execute_by_day.csv", index=False)
        (d / "issues.txt").write_text("\n".join(str(i) for i in ds.issues) + ("\n" if ds.issues else "問題なし\n"),
                                      encoding="utf-8")
        n = len(write_all_team_files(seats, ds.name, out / "teams"))
        print(f"{ds.name}: {len(ds.games)} 試合 / {seats['team'].nunique()} チーム -> {d}  (全チーム表 {n} 件)")
    if not frames:
        print("集計できる試合が無い", file=sys.stderr)
        return 1
    print(f"\n出力先: {out}  (チーム別のファイルは `teams` コマンドで作る)")
    return 0


def cmd_teams(args) -> int:
    teams = _read_teams(args.teams)
    datasets = _load_all(args.input, teams, args.strict)
    frames = []
    for ds in datasets:
        if ds.games:
            seats, _ = build_seat_table(ds.games)
            frames.append(seats)
    if not frames:
        print("集計できる試合が無い", file=sys.stderr)
        return 1
    allseats = pd.concat(frames, ignore_index=True)
    out = args.output / "teams"
    wanted = set(args.team) if args.team else set(allseats["team"].unique())
    unknown = wanted - set(allseats["team"].unique())
    if unknown:
        print(f"出場記録が無いチーム: {sorted(unknown)}", file=sys.stderr)
    n = 0
    for ds_name in sorted(allseats["dataset"].unique()):
        seats = allseats[allseats["dataset"] == ds_name]
        for tm in sorted(wanted & set(seats["team"].unique())):
            n += len(write_team_files(allseats, ds_name, tm, out))
    for tm in sorted(wanted & set(allseats["team"].unique())):
        n += len(write_team_sheet(allseats, tm, out))
    print(f"チーム別ファイル: {len(wanted & set(allseats['team'].unique()))} チーム / {n} 件 -> {out}")
    print("  <トラック>/<チーム>/{ja,en}/   トラックごとの詳細 (全役職まとめ・役職別・エラー挙動)")
    print("  by_team/<チーム>/{ja,en}/      出場した全トラックを 1 枚に並べたもの")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aiwolf-nlp-calculate-score", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="決着した試合の log と json を success/ へコピーする (元は消さない)")
    p.add_argument("input", type=Path, help="<トラック>/log を持つディレクトリ、またはその親")
    p.add_argument("--dry-run", action="store_true", help="コピーせず結果だけ出す")
    p.add_argument("--quiet", action="store_true", help="コピーした試合名を出さない")
    p.set_defaults(fn=cmd_prepare)
    for name, fn in (("check", cmd_check), ("run", cmd_run), ("teams", cmd_teams)):
        p = sub.add_parser(name)
        p.add_argument("input", type=Path, help="data/input か、1 トラック分のディレクトリ")
        p.add_argument("--teams", type=Path, default=None, help="チーム名一覧 (teams.yml)。json が無い試合のチーム名に使う")
        p.add_argument("--strict", action="store_true", help="形式の警告もエラー扱いにして、その試合を除外する")
        if name in ("run", "teams"):
            p.add_argument("-o", "--output", type=Path, default=Path("data/output"), help="出力先 (既定 data/output)")
        if name == "run":
            p.add_argument("--yes", action="store_true", help="問題があっても確認せずに続行する")
            p.add_argument("--no-prepare", action="store_true", help="success/ への振り分けを行わず、今あるものを読む")
        if name == "teams":
            p.add_argument("--team", action="append", default=[], metavar="NAME", help="このチームだけ (複数可)。省略時は全チーム")
        p.set_defaults(fn=fn)
    sub.add_parser("live", help="大会中の常駐配信 (引数は `live -h` を参照)")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["live"]:
        from .live.run import main as live_main
        return live_main(argv[1:])
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
