# aiwolf-nlp-calculate-score

[README in English](/README.en.md)

人狼知能コンテスト（自然言語部門）の対戦ログから、チーム別のゲームスコアを算出するツールです。

## 環境構築

> [!IMPORTANT]
> Python 3.11以上と [uv](https://docs.astral.sh/uv/) が必要です。

```bash
git clone https://github.com/aiwolfdial/aiwolf-nlp-calculate-score.git
cd aiwolf-nlp-calculate-score
uv sync
```

## 使い方

### 大会中にゲームスコアを配信する

大会サーバ上で、サーバの設定ファイルがあるディレクトリで実行します。トラックごとに tmux のウィンドウが立ち、ゲームが 1 本終わるたびに `metrics.ja.txt` と `metrics.en.txt` を書き直し、全試合が終わると自動で止まります。サーバより先に立てておいて構いません。

```bash
/path/to/aiwolf-nlp-calculate-score/scripts/start_live.sh default_en_5.yml default_en_9.yml freeform_en_5.yml
```

様子を見るときは tmux のセッション `aiwolf` に入ります。トラックごとにウィンドウが分かれています。

```bash
tmux attach -t aiwolf
```

サーバの設定ファイルに `live_metrics:` セクションを足しておきます（詳細は [大会中の配信について](/doc/ja/live.md)）。

```yaml
live_metrics:
  output_dir: /var/www/html/aiwolf/2026/INLG2/MainTruck5   # 配信ディレクトリ
```

### 終了したログからゲームスコアを計算する

`aiwolf-nlp-server` が出力した `<トラック>/log/` と `<トラック>/json/` を `data/input/` に並べて実行します。決着した試合の振り分け、形式と json の確認、集計を一度に行い、`data/output/` に出力します。

```bash
uv run src/main.py run data/input
```

段階ごとに実行することもできます。

```bash
uv run src/main.py prepare data/input              # 決着した試合の log と json を success/ へコピーする（元は消さない）
uv run src/main.py check   data/input              # 形式・json の有無・異常を確認する（ファイルは書かない）
uv run src/main.py run     data/input --no-prepare # 今ある success/ をそのまま集計する
```

大会サーバのログを手元に持ってくるには `scripts/fetch_logs.sh` を使います（`ssh aiwolf` で繋がることが前提）。

### チームごとのゲームスコアをまとめる

参加者に配るチーム別のファイルを `data/output/teams/` に出力します。`--team` で特定のチームだけに絞れます。

```bash
uv run src/main.py teams data/input
```

| 置き場所 | 内容 |
|---|---|
| `teams/<トラック>/<チーム>/{ja,en}/{md,csv}/` | そのトラックでの全役職まとめ、役職別、エラー挙動 |
| `teams/by_team/<チーム>/{ja,en}/` | 出場した全トラックを 1 枚に並べたもの |

## 出力

`run` は `data/output/<トラック>/` にトラック別の CSV（`seats.csv` は 1 試合 1 エージェント単位の分子・分母、`team_summary.csv` はチーム別の指標）と、`data/output/teams/<トラック>/all_team/` に配信中と同じ構成の全チーム表（txt / md / csv）を出します。

指標は 5 系統です。

| 系統 | 指標 | 読み方 |
|---|---|---|
| 勝率 | macro / micro / weighted micro、役職別 | 高いほど良い |
| 〜やすさ | 追放・投票・占い・護衛・言及・襲撃・襲撃候補 | 1.0 がランダム相当 |
| 精度 | 占い精度、投票精度（村人陣営） | 1.0 がランダム相当 |
| 連携 | 狂人の人狼回避率、人狼の仲間撃ち率 | 良し悪しは保留 |
| エラー挙動 | 未実行・自分を対象・無発言の日・同一発言の連投 | 0 が正常 |

## ドキュメント

- [指標の定義について](/doc/ja/metrics.md)
- [大会中の配信について](/doc/ja/live.md)
- [ログの形式と読み込みについて](/doc/ja/logs.md)
