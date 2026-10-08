# 大会中の配信について

トラックが走っている最中に、ゲームが 1 本終わるたびに指標を計算し直してテキストに出します。サーバ本体には一切手を入れません。

```
1 周ごとに
  1. log/ を見て、決着したゲームの log と json を success/ へコピー（元は消さない）
  2. success/ のうち、まだ集計していないゲームだけを読んで seat 行を追記
  3. チーム別の指標表を metrics.ja.txt と metrics.en.txt に書き出す
```

指標の定義と表の構成は `run` と同じ関数を使うので、配信中の値とトラック終了後に流し直した値は完全に一致します。

## 使い方

サーバの設定ファイルがあるディレクトリで実行します（サーバ自身も相対パスをカレント基準で解決するため）。

```bash
<リポジトリ>/scripts/start_live.sh default_en_5.yml default_en_9.yml   # トラックごとに tmux の常駐ウィンドウを立てる (通常はこれ)
```

単発で動かしたいとき (`<リポジトリ>` はこのリポジトリの場所):

```bash
uv run --project <リポジトリ> python <リポジトリ>/src/main.py live -c default_en_5.yml --once      # 1 回だけ
uv run --project <リポジトリ> python <リポジトリ>/src/main.py live -c default_en_5.yml --no-sort   # 振り分けせず既存の success だけ読む
```

常駐は tmux で立ててください（nohup だとログアウトで落ちる環境があります）。**サーバより先に立てておいて構いません。** log も json もマッチオプティマイザ JSON も無い間は「まだ集計できるゲームがありません」と書いて待ち、サーバが起動して試合が決着し始めると拾い、全試合が終われば `--stop-when-done` で止まります。live が振り分けた `success/` は、そのまま `run` の入力になります（`prepare` はコピー済みと判定して何もしません）。

## 設定

サーバの yml に `live_metrics:` セクションを足すだけです。サーバは未知のキーを無視するので起動に影響しません。

```yaml
live_metrics:
  output_dir: /var/www/html/aiwolf/2026/INLG2/MainTruck_freeform5   # 配信ディレクトリ
  interval: 30          # 更新間隔[秒]。既定 30
  sort_success: true    # 決着ゲームを success へ逐次コピーするか。既定 true
  dataset: ""           # 出力に入れる名前。空なら log の位置から <大会>_<トラック> を組み立てる
```

log / json / マッチオプティマイザのパスは、既存の `game_logger` `json_logger` `matching` セクションから読みます。

## 出力

`output_dir` に `metrics.ja.txt` と `metrics.en.txt` の 2 つだけを置きます。一時ファイル経由で差し替えるので、読み手が半端な内容を掴むことはありません。CSV は出しません（配信ディレクトリは web で公開されるため）。機械可読なものが要るときは、ログを持ち帰って `run` を回してください。

このファイルは参加者が読みます。サーバ実装の用語（json、result 行、振り分けなど）は書かず、運営向けの内訳と警告はコンソール（stderr）に出します。

## 決着の判定

json の `win_side` を正とし、log は「その結論を指標に使える形で持っているか」の確認に使います。

```
json が無い / win_side が空                 -> 進行中
win_side == NONE                            -> 不成立
win_side が陣営 かつ log に result 行あり    -> 決着（success へコピー）
win_side が陣営 だが log に result 行なし    -> 壊れている（コピーしない。コンソールに警告）
```

log の result 行だけで判定すると、異常終了して result 行が書かれなかった試合が永遠に「進行中」として残るため、json を先に見ます。json が無効な環境では log だけで判定します。

## 自動停止

`--stop-when-done` を付けると、マッチオプティマイザ JSON を見て全試合の終了を検知し、指標に【完了】と書いてから自分を止めます。条件は `game_count > 0`、`ended_matches >= game_count`、`scheduled_matches` が空、進行中のゲームが 0 の 4 つすべてです。組めない試合が残っている場合は完了にしません（人の判断を奪わないため）。誤検知を避けるため、完了判定が `--stop-confirmations` 回（既定 2）連続で成立してから発火します。

同じ設定ファイル名で動いている状況監視スクリプト（`monitor_status.py`）とサーバ本体が同じユーザで動いていれば、監視 → サーバの順に SIGTERM で止めます。無ければ何もしません。`--dry-run-stop` で停止対象だけ確認できます。
