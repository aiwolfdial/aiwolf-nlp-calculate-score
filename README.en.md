# aiwolf-nlp-calculate-score

[README in Japanese](/README.md)

Calculates team-level game scores from AIWolf Competition (Natural Language Division) game logs.

## Environment Setup

> [!IMPORTANT]
> Python 3.11 or higher and [uv](https://docs.astral.sh/uv/) are required.

```bash
git clone https://github.com/aiwolfdial/aiwolf-nlp-calculate-score.git
cd aiwolf-nlp-calculate-score
uv sync
```

## Usage

### Serving game scores during a tournament

Run on the tournament server, in the directory that holds the server config files. One tmux window per track is started; `metrics.ja.txt` and `metrics.en.txt` are rewritten every time a game finishes, and the process stops by itself once every game has finished. It can be started before the server.

```bash
/path/to/aiwolf-nlp-calculate-score/scripts/start_live.sh default_en_5.yml default_en_9.yml freeform_en_5.yml
```

To watch it, attach to the tmux session `aiwolf`; each track has its own window.

```bash
tmux attach -t aiwolf
```

Add a `live_metrics:` section to each server config beforehand (see [Live feed](/doc/en/live.md)).

```yaml
live_metrics:
  output_dir: /var/www/html/aiwolf/2026/INLG2/MainTruck5   # served directory
```

### Computing game scores from finished logs

Place the `<track>/log/` and `<track>/json/` directories written by `aiwolf-nlp-server` under `data/input/` and run. Finished games are sorted out, format and json availability are checked, and the aggregation is written to `data/output/`, all in one go.

```bash
uv run src/main.py run data/input
```

The steps can also be run separately.

```bash
uv run src/main.py prepare data/input              # copy finished games' log and json into success/ (originals are kept)
uv run src/main.py check   data/input              # validate format, json availability and anomalies (writes nothing)
uv run src/main.py run     data/input --no-prepare # aggregate what is already in success/
```

`scripts/fetch_logs.sh` fetches the tournament server's logs to your machine (assumes `ssh aiwolf` reaches the server).

### Summarizing game scores per team

Writes the per-team files handed out to participants under `data/output/teams/`. `--team` restricts the output to given teams.

```bash
uv run src/main.py teams data/input
```

| Location | Content |
|---|---|
| `teams/<track>/<team>/{ja,en}/{md,csv}/` | that team's all-role summary, per-role tables and error behaviour in the track |
| `teams/by_team/<team>/{ja,en}/` | every track the team entered, side by side on one page |

## Output

`run` writes per-track CSVs to `data/output/<track>/` (`seats.csv` holds numerators and denominators per game and agent, `team_summary.csv` the per-team metrics) and whole-track tables (txt / md / csv, in the same layout as the live feed) to `data/output/teams/<track>/all_team/`.

Metrics come in five groups.

| Group | Metrics | Reading |
|---|---|---|
| Win rate | macro / micro / weighted micro, by role | higher is better |
| Targeting | execution, votes, divination, guard, mentions, attacks, attack votes | 1.0 = random |
| Accuracy | divination accuracy, vote accuracy (village side) | 1.0 = random |
| Coordination | possessed's non-werewolf vote rate, werewolves' teammate-vote rate | direction left open |
| Error behaviour | missing actions, self-targeting, silent days, repeated talks | 0 is normal |

## Documentation

- [Metric definitions](/doc/en/metrics.md)
- [Live feed](/doc/en/live.md)
- [Log format and loading](/doc/en/logs.md)
