# Metric definitions

The unit of aggregation is a **seat** (one agent in one game). A team's value is obtained by summing the seat-level numerators and denominators and then dividing. The live feed during a tournament and the post-tournament aggregation therefore always agree exactly.

Only games that reached a conclusion (`success` logs) are counted.

## Five groups

### Win rate

| Column | Definition |
|---|---|
| `勝率_macro` | games won / games played. What actually happened |
| `勝率_micro` | unweighted mean of per-role win rates (roles drawn at least once). Whether the team wins evenly across roles |
| `勝率_weighted_micro` | per-role win rates re-averaged with the village's role composition. Win rate with the luck of the draw removed |

The role composition is counted from all seats of the dataset (a 5-player village gives villager 2 : seer 1 : possessed 1 : werewolf 1). When roles were dealt in proportion, macro and weighted micro coincide; when the three differ, the draw was uneven. Per-role win rates are shown as "rate (wins / games)".

### Targeting (ratio to random)

**Observed count ÷ count expected under completely random behaviour.** 1.0 is random; 2.0 is twice as often as random.

A ratio is used instead of a rate (count ÷ opportunities) because the baseline moves with the number of candidates. In a 5-player village a vote has 4 possible targets, so even random voting yields a vote-received rate of 0.25; in a 9-player village it is 0.125. Ratios let 5- and 9-player villages, and early and late days, be compared side by side.

The expected count distributes each event that actually happened evenly over the candidates at that moment. With 5 alive and 4 others voting, each agent expects 4 × 1/4 = 1 vote; receiving 3 gives a vote targeting of 3.0.

| Column | Count | Candidates |
|---|---|---|
| `追放されやすさ` (execution) | times executed | agents alive that day (one execution per day, spread evenly) |
| `狙われやすさ` (votes) | votes received (self-votes excluded) | alive agents other than the voter |
| `占われやすさ` (divination) | times divined | agents alive that night other than the seer |
| `守られやすさ` (guard) | times guarded | agents alive that night other than the bodyguard; villages with a bodyguard only |
| `注目されやすさ` (mentions) | talks by others containing the character name | alive listeners (names mentioned per talk spread evenly) |
| `襲われやすさ` (attacks) | times attacked by werewolves | agents alive that night excluding werewolves |
| `襲撃候補にされやすさ` (attack votes) | times named in werewolves' attack votes | same; equals attack targeting when the village has one werewolf |

Because every vote or divination lands on exactly one agent, the dataset-wide weighted mean of each "received" ratio is exactly 1.0 by construction (a useful sanity check).

### Accuracy (ratio to random)

| Column | Definition |
|---|---|
| `占い精度` | werewolves divined while seer ÷ expected under random divination |
| `投票精度_村人陣営` | votes on werewolves while on the village side ÷ expected under random voting |

The expectation is the share of werewolves among the candidates. These ratios do not converge to 1.0; their distance from 1.0 is the agent's skill.

### Coordination

| Column | Definition |
|---|---|
| `人狼以外への投票率_狂人` | share of votes on non-werewolves while possessed. The possessed is not told who the werewolves are, so avoiding them requires inference |
| `人狼への投票率_人狼` | share of votes on the other werewolf while a werewolf (teammate-vote rate). Villages with two or more werewolves only. May be a mistake or a deliberate way to deflect suspicion |

### Error behaviour

Behaviour that is almost certainly unintended, counted as "count (opportunities)". No ranks.

| Item | Count | Opportunities |
|---|---|---|
| No vote | days alive without a recorded vote | voting days alive |
| Self-vote | votes on oneself | votes cast |
| No attack vote | nights as a werewolf without an attack vote | nights as a werewolf (from day 1) |
| Attack vote on werewolf | attack votes naming a werewolf (including oneself) | attack votes cast |
| No divination | nights as the seer without a divination | nights as the seer (from day 0) |
| No guard | nights as the bodyguard without a guard | nights as the bodyguard (from day 1) |
| Silent days | days whose only talks were Over / Skip | days with a talk phase |
| Repeated talks | talks identical to the previous one by the same agent | talks |

Night 0 always exists; from day 1 a night exists when the server wrote an `attack` row, which it always does. Votes, divinations and guards on dead agents, and self-divination and self-guard, are rejected by the server and never appear in logs.

## Output files

`data/output/<track>/`

| File | Content |
|---|---|
| `seats.csv` | seat-level raw data: numerators and denominators of every metric |
| `team_summary.csv` | the five groups per team (one row per `dataset`, `team`) |
| `team_by_role.csv` / `team_by_camp.csv` | team × role / side numerators, denominators and rates |
| `execute_by_day.csv` | execution rate per day, conditional on being alive that day |
| `issues.txt` | warnings raised while loading |

Since `seats.csv` carries every numerator and denominator, any other breakdown can be rebuilt with `aiwolf_nlp_calculate_score.metrics.aggregate(seats, ["dataset", "team", "role"])`.
