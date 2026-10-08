# ログの形式と読み込みについて

対象は `aiwolf-nlp-server` の `game_logger` が書くログ（2026 年大会以降の形式）と `json_logger` の json です。

## ログ（.log）

1 行が 1 つの出来事で、先頭 2 フィールドが `日,種別`。種別は次の 10 種類で、フィールド数は固定です。

```
day,status,idx,role,ALIVE|DEAD,agent_name,character
day,talk,idx,turn,agent,text[,timestamp]
day,whisper,idx,turn,agent,text[,timestamp]
day,vote,voter,target
day,attackVote,werewolf,target
day,execute,idx,role
day,divine,seer,target,HUMAN|WEREWOLF
day,guard,bodyguard,target,target_role
day,attack,target|-1,true|false        （-1 は襲撃が無かった夜）
day,result,n_villagers,n_werewolves,win_side
```

talk / whisper の本文は生のまま埋め込まれ、RFC 4180 の引用符エスケープはありません。本文にはカンマ・引用符・改行が入りえます。そのため `csv` モジュールは使わず、この形式専用に読みます。

- レコードの先頭は `<日>,<種別>,`。この形をしていない行は直前のレコードの本文の続きとして連結する
- talk / whisper は先頭 5 つのカンマだけで切り、残りを本文とする。末尾が `,<unix 秒>` ならタイムスタンプとして剥がす（英語トラックには付き、日本語トラックには付かない）
- 文字コードは UTF-8 厳密。読めなければその試合はエラー
- 未知の種別、フィールド数の不一致は警告して無視する（`--strict` ならその試合を除外）

## json

サーバと各エージェントの通信記録です。使うのは `agents`（`idx` `name` `role` `team`）と `win_side` だけです。

- **チーム名の正は json です。** ログにはエージェント名（`kanolab1` など）しかありません
- json が無い試合は `--teams teams.yml`（出場チーム一覧）との最長一致でチーム名を決めます。どちらも無ければ、確認のうえエージェント名の末尾の数字を剥がして推定します
- json の `agents` と status 行（idx・名前・役職）、`win_side` と `result` 行は照合し、食い違えばその試合は除外します

## 検証

`check`（と `run` の冒頭）で次を確認します。

| 種類 | 内容 | 扱い |
|---|---|---|
| 致命的 | 勝敗が無い、json と log の食い違い、status 行の欠け、UTF-8 で読めない、役職構成が試合によって違う | その試合（または データセット）を除外。`run` は続行するか確認する |
| 警告 | 未知の行種別、フィールド数の不一致、占い対象が生存していない、json が無い | 表示して続行 |

## 入力の置き方

```
data/input/<大会>_<トラック>/log/*.log
data/input/<大会>_<トラック>/json/*.json
```

`prepare` は `log/` 直下の試合のうち決着したものを `log/success/`（json は `json/success/`）へコピーします。判定は json の `win_side` を正とし、log の `result` 行で指標に使える形かを確かめます（異常終了して `result` 行が無い試合は運びません）。`check` と `run` は `log/success/` があればそこだけを、無ければ `log/` 直下を読みます。json は `json/` 直下と `json/success/` の両方を見ます。`<大会>_<トラック>` という名前は `scripts/fetch_logs.sh` がサーバのディレクトリ構成から付けるもので、別の名前でも構いません。
