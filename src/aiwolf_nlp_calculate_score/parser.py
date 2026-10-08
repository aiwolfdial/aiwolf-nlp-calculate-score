"""AIWolf NLP 大会ログ (.log / .json) の読み込みと検証。

ログは 1 レコード = 1 行のカンマ区切りだが、talk / whisper の本文は生のまま埋め込まれる
(RFC 4180 の引用符エスケープは無い)。本文にカンマや引用符、改行が入りうるので、
`csv` モジュールは使わず、この形式専用に読む。

    - レコードの先頭は `<日>,<種別>,`。この形をしていない行は直前のレコードの本文の続き
    - 種別ごとにフィールド数は固定。talk / whisper だけは先頭 5 フィールドで切り、残りが本文
    - 本文の末尾に `,<unix秒>` が付くトラック (英語) と付かないトラック (日本語) がある

行フォーマット (サーバ `aiwolf-nlp-server` の AppendLog 呼び出しに対応):
    day,status,idx,role,ALIVE|DEAD,agent_name,character
    day,talk,idx,turn,agent,text[,timestamp]
    day,whisper,idx,turn,agent,text[,timestamp]
    day,vote,voter,target
    day,attackVote,werewolf,target
    day,execute,idx,role
    day,divine,seer,target,HUMAN|WEREWOLF
    day,guard,bodyguard,target,target_role
    day,attack,target|-1,true|false
    day,result,n_villagers_alive,n_werewolves_alive,win_side

json はサーバと各エージェントの通信記録で、`agents` (idx / name / role / team) と
`win_side` だけを使う。チーム名の正はこちら。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

VILLAGER_SIDE_ROLES = frozenset({"VILLAGER", "SEER", "MEDIUM", "BODYGUARD"})
WEREWOLF_SIDE_ROLES = frozenset({"WEREWOLF", "POSSESSED"})
VILLAGER_SIDE = "VILLAGER"
WEREWOLF_SIDE = "WEREWOLF"

# 種別 -> フィールド数 (talk / whisper は本文を含むため下限)
RECORD_FIELDS: dict[str, int] = {
    "status": 7, "talk": 6, "whisper": 6, "vote": 4, "attackVote": 4,
    "execute": 4, "divine": 5, "guard": 5, "attack": 4, "result": 5,
}
_RECORD_HEAD = re.compile(r"^(\d+),([A-Za-z]+),")
_TRAILING_TIMESTAMP = re.compile(r",(\d{9,})$")
_TRAILING_INDEX = re.compile(r"\d+$")


class LogFormatError(ValueError):
    """ログの形式が想定と違う。ファイル名と行番号を含む。"""


@dataclass
class Issue:
    """読み込み時に見つかった問題。fatal なら集計に使えない。"""
    game_id: str
    message: str
    fatal: bool = False

    def __str__(self) -> str:
        return f"[{'ERROR' if self.fatal else 'WARN'}] {self.game_id}: {self.message}"


def camp_of(role: str) -> str:
    if role in WEREWOLF_SIDE_ROLES:
        return WEREWOLF_SIDE
    if role in VILLAGER_SIDE_ROLES:
        return VILLAGER_SIDE
    raise ValueError(f"未知の役職: {role}")


@dataclass(frozen=True)
class Agent:
    idx: int
    name: str
    team: str
    role: str
    character: str

    @property
    def camp(self) -> str:
        return camp_of(self.role)


@dataclass
class Game:
    game_id: str
    dataset: str
    path: Path
    agents: dict[int, Agent]
    alive: dict[int, set[int]]                                   # day -> その日の昼開始時の生存 idx
    votes: list[tuple[int, int, int]] = field(default_factory=list)        # day, voter, target
    attack_votes: list[tuple[int, int, int]] = field(default_factory=list)  # day, werewolf, target
    executes: list[tuple[int, int]] = field(default_factory=list)          # day, idx
    divines: list[tuple[int, int, int]] = field(default_factory=list)      # day, seer, target
    guards: list[tuple[int, int, int]] = field(default_factory=list)       # day, bodyguard, target
    attacks: list[tuple[int, int, bool]] = field(default_factory=list)     # day, target(-1=無し), success
    talks: list[tuple[int, int, str]] = field(default_factory=list)        # day, speaker, text
    whispers: list[tuple[int, int, str]] = field(default_factory=list)
    talk_times: list[int | None] = field(default_factory=list)             # talks と同じ並びの unix 秒
    winner: str = ""
    final_day: int = 0
    team_source: str = "json"                                              # json / teams / name

    @property
    def n_players(self) -> int:
        return len(self.agents)

    @property
    def n_werewolves(self) -> int:
        return sum(1 for a in self.agents.values() if a.role == "WEREWOLF")

    @property
    def role_counts(self) -> tuple[tuple[str, int], ...]:
        c: dict[str, int] = {}
        for a in self.agents.values():
            c[a.role] = c.get(a.role, 0) + 1
        return tuple(sorted(c.items()))

    def executed_on(self, day: int) -> set[int]:
        return {idx for d, idx in self.executes if d == day}

    def night_alive(self, day: int) -> set[int]:
        """その日の夜 (占い / 護衛 / 襲撃) の時点で生存している idx。"""
        return self.alive.get(day, set()) - self.executed_on(day)

    def nights(self) -> list[int]:
        """夜フェーズがあった日。0 日目は必ずある。1 日目以降はサーバが必ず書く attack 行で判定する。"""
        return sorted({0} | {d for d, _t, _s in self.attacks})


# ------------------------------------------------------------------ 行の分割
def split_records(text: str) -> list[tuple[int, str]]:
    """ファイル全体を (行番号, レコード文字列) に分ける。本文中の改行はレコードに残す。"""
    records: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.split("\n"), 1):
        if _RECORD_HEAD.match(line):
            records.append((lineno, line))
        elif records:
            n, prev = records[-1]
            records[-1] = (n, prev + "\n" + line)
        elif line.strip():
            raise LogFormatError(f"{lineno} 行目: レコードの形をしていない行で始まっている: {line[:60]!r}")
    return records


def _split_text_field(rest: str) -> tuple[str, int | None]:
    m = _TRAILING_TIMESTAMP.search(rest)
    if m:
        return rest[:m.start()], int(m.group(1))
    return rest, None


def _team_from_name(name: str) -> str:
    stripped = _TRAILING_INDEX.sub("", name)
    return stripped or name


def _team_from_list(name: str, teams: list[str]) -> str | None:
    """teams.yml の一覧から最長一致でチーム名を決める。"""
    best = None
    for t in teams:
        if name.startswith(t) and (best is None or len(t) > len(best)):
            best = t
    return best


# ------------------------------------------------------------------ json
def load_json_agents(json_path: Path) -> tuple[dict[int, dict], str | None]:
    """json から idx -> {name, role, team} と win_side を読む。"""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    agents = {int(a["idx"]): {"name": str(a.get("name", "")), "role": str(a.get("role", "")),
                              "team": str(a.get("team", ""))}
              for a in data.get("agents", []) if "idx" in a}
    side = data.get("win_side")
    return agents, (str(side) if side not in (None, "") else None)


# ------------------------------------------------------------------ 本体
def parse_log(log_path: Path, dataset: str, json_path: Path | None = None,
              teams: list[str] | None = None, issues: list[Issue] | None = None) -> Game:
    """1 ゲームを読む。形式の異常は issues に積む (fatal なら集計側で除外する)。

    チーム名の決め方 (優先順):
        1. json の agents.team
        2. teams (teams.yml の一覧) との最長一致
        3. エージェント名の末尾の数字を剥がす (警告を出す)
    """
    issues = issues if issues is not None else []
    game_id = log_path.stem
    try:
        text = log_path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise LogFormatError(f"{log_path.name}: UTF-8 として読めない (byte {e.start})") from e

    statuses: dict[int, dict[int, tuple[str, bool, str, str]]] = {}
    g = Game(game_id=game_id, dataset=dataset, path=log_path, agents={}, alive={})
    unknown: dict[str, int] = {}
    bad_fields: list[str] = []

    for lineno, rec in split_records(text):
        day_s, kind, _ = rec.split(",", 2)
        day = int(day_s)
        g.final_day = max(g.final_day, day)
        expected = RECORD_FIELDS.get(kind)
        if expected is None:
            unknown[kind] = unknown.get(kind, 0) + 1
            continue
        if kind in ("talk", "whisper"):
            parts = rec.split(",", 5)
            if len(parts) < 6:
                bad_fields.append(f"{lineno} 行目 {kind}: フィールド不足")
                continue
            text_body, ts = _split_text_field(parts[5])
            speaker = int(parts[4])
            if kind == "talk":
                g.talks.append((day, speaker, text_body))
                g.talk_times.append(ts)
            else:
                g.whispers.append((day, speaker, text_body))
            continue
        parts = rec.split(",")
        if len(parts) != expected:
            bad_fields.append(f"{lineno} 行目 {kind}: フィールド数 {len(parts)} (想定 {expected})")
            continue
        if kind == "status":
            _, _, idx, role, alive, name, character = parts
            statuses.setdefault(day, {})[int(idx)] = (role, alive == "ALIVE", name, character)
        elif kind == "vote":
            g.votes.append((day, int(parts[2]), int(parts[3])))
        elif kind == "attackVote":
            g.attack_votes.append((day, int(parts[2]), int(parts[3])))
        elif kind == "execute":
            g.executes.append((day, int(parts[2])))
        elif kind == "divine":
            g.divines.append((day, int(parts[2]), int(parts[3])))
        elif kind == "guard":
            g.guards.append((day, int(parts[2]), int(parts[3])))
        elif kind == "attack":
            g.attacks.append((day, int(parts[2]), parts[3] == "true"))
        elif kind == "result":
            g.winner = parts[4]

    for kind, n in sorted(unknown.items()):
        issues.append(Issue(game_id, f"未知の行種別 {kind!r} が {n} 行 (無視した)"))
    for msg in bad_fields:
        issues.append(Issue(game_id, msg))
    if not statuses:
        raise LogFormatError(f"{log_path.name}: status 行が無い")

    # ---- チーム名 ---------------------------------------------------------
    json_agents: dict[int, dict] = {}
    json_side: str | None = None
    if json_path is not None and json_path.is_file():
        try:
            json_agents, json_side = load_json_agents(json_path)
        except (OSError, ValueError, KeyError) as e:
            issues.append(Issue(game_id, f"json を読めない ({e}); チーム名は別の手段で決める", fatal=True))

    last = statuses[max(statuses)]
    agents: dict[int, Agent] = {}
    for idx, (role, _alive, name, character) in sorted(last.items()):
        if json_agents:
            ja = json_agents.get(idx)
            if ja is None:
                issues.append(Issue(game_id, f"json に idx={idx} が無い", fatal=True))
                team = _team_from_name(name)
            else:
                team = ja["team"]
                if ja["name"] != name or ja["role"] != role:
                    issues.append(Issue(game_id, f"json と log の食い違い idx={idx}: "
                                        f"json=({ja['name']},{ja['role']}) log=({name},{role})", fatal=True))
            g.team_source = "json"
        elif teams:
            t = _team_from_list(name, teams)
            if t is None:
                issues.append(Issue(game_id, f"エージェント {name} がチーム一覧のどれにも一致しない", fatal=True))
                t = _team_from_name(name)
            team = t
            g.team_source = "teams"
        else:
            team = _team_from_name(name)
            g.team_source = "name"
        agents[idx] = Agent(idx=idx, name=name, team=team, role=role, character=character)
    g.agents = agents
    g.alive = {day: {idx for idx, (_r, a, _n, _c) in per.items() if a} for day, per in statuses.items()}

    # ---- 検証 -------------------------------------------------------------
    if g.winner not in (VILLAGER_SIDE, WEREWOLF_SIDE):
        issues.append(Issue(game_id, f"勝敗が確定していない (result={g.winner!r})", fatal=True))
    if json_side is not None and g.winner and json_side.upper() != g.winner.upper():
        issues.append(Issue(game_id, f"json の win_side={json_side} と log の result={g.winner} が食い違う", fatal=True))
    for day, per in statuses.items():
        if set(per) != set(last):
            issues.append(Issue(game_id, f"day{day} の status 行が全員分そろっていない", fatal=True))
    for day, seer, target in g.divines:
        if target == seer:
            issues.append(Issue(game_id, f"day{day} 占い師が自分を占っている"))
        if target not in g.night_alive(day):
            issues.append(Issue(game_id, f"day{day} 占い対象が夜の時点で生存していない"))
    for day, bg, target in g.guards:
        if target == bg:
            issues.append(Issue(game_id, f"day{day} 騎士が自分を護衛している"))
    return g


# ------------------------------------------------------------------ データセット
@dataclass
class Dataset:
    name: str
    games: list[Game]
    issues: list[Issue]
    skipped: list[str]            # fatal で除外した game_id
    missing_json: list[str]


def find_logs(log_dir: Path) -> list[Path]:
    """読む .log を決める。`success/` があればそこだけ (決着済み)、無ければ直下の .log。"""
    if (log_dir / "success").is_dir():
        return sorted((log_dir / "success").glob("*.log"))
    return sorted(log_dir.glob("*.log"))


def find_json(log_path: Path, json_dirs: list[Path]) -> Path | None:
    for d in json_dirs:
        for cand in (d / f"{log_path.stem}.json", d / "success" / f"{log_path.stem}.json"):
            if cand.is_file():
                return cand
    return None


def load_dataset(dataset_dir: Path, name: str | None = None,
                 teams: list[str] | None = None, strict: bool = False) -> Dataset:
    """<dataset_dir>/log と <dataset_dir>/json を読む。

    log/success/ があればそこだけを読む (`prepare` が振り分けた決着済みの試合)。無ければ
    log/ 直下の .log を読む。log/ 自体が無ければ dataset_dir 直下の .log を読む。
    json は json/ 直下と json/success/ の両方を見る。strict なら形式の警告もエラーにする。
    """
    name = name or dataset_dir.name
    log_root = dataset_dir / "log" if (dataset_dir / "log").is_dir() else dataset_dir
    json_dirs = [d for d in (dataset_dir / "json", dataset_dir / "json" / "success") if d.is_dir()]
    games: list[Game] = []
    issues: list[Issue] = []
    skipped: list[str] = []
    missing_json: list[str] = []
    for log_path in find_logs(log_root):
        jp = find_json(log_path, json_dirs)
        if jp is None:
            missing_json.append(log_path.stem)
        local: list[Issue] = []
        try:
            game = parse_log(log_path, name, jp, teams, local)
        except LogFormatError as e:
            issues.append(Issue(log_path.stem, str(e), fatal=True))
            skipped.append(log_path.stem)
            continue
        issues.extend(local)
        if any(i.fatal for i in local) or (strict and local):
            skipped.append(log_path.stem)
            continue
        games.append(game)
    comps = {g.role_counts for g in games}
    if len(comps) > 1:
        issues.append(Issue(name, f"役職構成が試合によって違う: {sorted(comps)}", fatal=True))
    return Dataset(name, games, issues, skipped, missing_json)
