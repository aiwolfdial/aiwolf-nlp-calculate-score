# Live feed

While a track is running, the metrics are recomputed every time a game finishes and written as text. The server itself is never touched.

```
Each pass
  1. look at log/, copy finished games' log and json into success/ (originals are kept)
  2. read only the games in success/ not yet aggregated and append their seat rows
  3. write the per-team tables to metrics.ja.txt and metrics.en.txt
```

Definitions and table layout come from the same functions as `run`, so the values served during the tournament and those recomputed afterwards are identical.

## Usage

Run in the directory holding the server config (the server also resolves relative paths from the current directory).

```bash
<repo>/scripts/start_live.sh default_en_5.yml default_en_9.yml   # one resident tmux window per track (the usual way)
```

For a single pass (`<repo>` is where this repository lives):

```bash
uv run --project <repo> python <repo>/src/main.py live -c default_en_5.yml --once      # one pass
uv run --project <repo> python <repo>/src/main.py live -c default_en_5.yml --no-sort   # do not sort into success/, read what is there
```

Use tmux for the resident process (nohup may die on logout in some environments). **It can be started before the server.** While there is no log, json or match optimizer JSON yet it writes "no finished games to aggregate yet" and waits; once the server starts and games finish it picks them up, and with `--stop-when-done` it exits when every game has finished. The `success/` directories it sorts are exactly what `run` takes as input (`prepare` sees them as already copied and does nothing).

## Configuration

Add a `live_metrics:` section to the server's yml. The server ignores unknown keys.

```yaml
live_metrics:
  output_dir: /var/www/html/aiwolf/2026/INLG2/MainTruck_freeform5   # served directory
  interval: 30          # refresh interval in seconds (default 30)
  sort_success: true    # copy finished games into success/ (default true)
  dataset: ""           # name shown in the output; empty builds <tournament>_<track> from the log path
```

Paths for log, json and the match optimizer are read from the existing `game_logger`, `json_logger` and `matching` sections.

## Output

Only `metrics.ja.txt` and `metrics.en.txt` are written to `output_dir`, via a temporary file so readers never see a partial file. No CSV is written there, because the directory is served on the web; take the logs home and run `run` for machine-readable output.

Participants read these files, so server internals (json, result rows, sorting) are not mentioned; organizer-only details and warnings go to the console (stderr).

## Deciding that a game finished

The json's `win_side` is authoritative; the log is checked only for being usable.

```
no json / empty win_side                    -> in progress
win_side == NONE                            -> not concluded
win_side is a side and the log has a result row -> finished (copied into success/)
win_side is a side but the log has no result row -> broken (not copied; warned on the console)
```

Relying on the log's result row alone leaves games that crashed before writing it as "in progress" forever, hence json first. Without json logging, the log alone is used.

## Automatic stop

With `--stop-when-done`, the match optimizer JSON is used to detect that every game finished; the metrics are written with a DONE banner and the process exits. All four conditions must hold: `game_count > 0`, `ended_matches >= game_count`, `scheduled_matches` empty, and no game in progress. Games that cannot be scheduled keep the track from being marked complete, so that a human decides. To avoid false positives the condition must hold `--stop-confirmations` times in a row (default 2).

If a status monitor script (`monitor_status.py`) and the server are running under the same user with the same config file name, they are stopped with SIGTERM in that order; otherwise nothing is stopped. `--dry-run-stop` only lists the targets.
