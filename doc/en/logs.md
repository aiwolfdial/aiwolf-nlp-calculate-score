# Log format and loading

The input is the log written by `game_logger` of `aiwolf-nlp-server` (the format used since the 2026 tournaments) and the json written by `json_logger`.

## Log (.log)

One line per event, starting with `day,kind`. There are ten kinds, each with a fixed number of fields.

```
day,status,idx,role,ALIVE|DEAD,agent_name,character
day,talk,idx,turn,agent,text[,timestamp]
day,whisper,idx,turn,agent,text[,timestamp]
day,vote,voter,target
day,attackVote,werewolf,target
day,execute,idx,role
day,divine,seer,target,HUMAN|WEREWOLF
day,guard,bodyguard,target,target_role
day,attack,target|-1,true|false        (-1 means no attack that night)
day,result,n_villagers,n_werewolves,win_side
```

The talk / whisper text is embedded raw, without RFC 4180 quoting, and may contain commas, quotes and newlines. The loader is therefore format-specific instead of using the `csv` module.

- A record starts with `<day>,<kind>,`. Any line not of that form is appended to the previous record's text
- talk / whisper are split at the first five commas only; the rest is the text. A trailing `,<unix seconds>` is stripped as the timestamp (present in English tracks, absent in Japanese ones)
- Strict UTF-8; an undecodable file is an error for that game
- Unknown kinds and wrong field counts are warned about and skipped (`--strict` excludes the game)

## json

The communication record between the server and each agent. Only `agents` (`idx`, `name`, `role`, `team`) and `win_side` are used.

- **The json is the source of truth for team names.** The log only has agent names such as `kanolab1`
- Games without a json resolve team names by longest match against `--teams teams.yml` (the list of entrants). Without either, after confirmation, the trailing digits of the agent name are stripped
- `agents` is cross-checked against the status rows (idx, name, role) and `win_side` against the `result` row; a mismatch excludes the game

## Validation

`check` (and the start of `run`) reports:

| Kind | Content | Handling |
|---|---|---|
| Fatal | no winner, json/log mismatch, missing status rows, undecodable UTF-8, role composition differing between games | the game (or dataset) is excluded; `run` asks before continuing |
| Warning | unknown record kind, wrong field count, divination target not alive, missing json | shown, processing continues |

## Input layout

```
data/input/<tournament>_<track>/log/*.log
data/input/<tournament>_<track>/json/*.json
```

`prepare` copies the finished games found directly under `log/` into `log/success/` (and their json into `json/success/`). The json's `win_side` decides whether a game finished, and the log's `result` row confirms it is usable (games that crashed without a `result` row are not copied). `check` and `run` read only `log/success/` when it exists, otherwise `log/` itself. json is looked up in both `json/` and `json/success/`. The `<tournament>_<track>` name is what `scripts/fetch_logs.sh` derives from the server's directory layout; any other name works too.
