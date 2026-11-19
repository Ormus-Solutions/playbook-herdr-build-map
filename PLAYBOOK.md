# The build map: the playbook

This file is written for a coding agent (Claude Code, Codex, Grok) and is
readable by a person. Do the steps in order. Each step is one action, a
"Verify:" command and the result to expect. Stop at the first result that
does not match and report what you saw. The pattern is called a verification
loop: a step that checks itself stops a mistake where it starts, before ten
more steps build on it.

Commands run from the root of this repo unless a step says otherwise. Where
a step shows `acme/acme-web`, `api` or `~/projects/acme-web`, use the
reader's own repo, lane names and project folder.

## Goal

A Herdr tab named Map whose one pane shows the reader's build and redraws it
every second: OVERALL, STAGES from the project's `build-map.json`, PR QUEUE
from GitHub, one LANES row per agent lane, the agents by state and a FOR YOU
list. The orchestrator agent keeps `build-map.json` current, and each lane
agent keeps its card.

## Before you start

| Need | Check | Expected | If it fails |
|---|---|---|---|
| Python 3.11 or newer | `python3 -c 'import sys; print(sys.version_info >= (3, 11))'` | `True` | install a newer Python from python.org or your package manager |
| git | `git --version` | a version line | install git |
| Herdr | `herdr --version` | `herdr 0.9.5` or newer | install it from herdr.dev; or skip Herdr: set `agents = "cards"` (step 5) and run the pane in any terminal |
| A pane inside Herdr | `test "${HERDR_ENV:-}" = 1 && echo inside` | `inside` | open this repo in a Herdr pane and run your agent there; steps 3, 7 and 10 need it |
| GitHub CLI, signed in | `gh auth status` | a line that says you are logged in | install gh from cli.github.com and run `gh auth login`; or leave `repo` empty (no PR QUEUE) |
| Read access to the repo | `gh pr list --repo acme/acme-web --limit 1 --json number` | a JSON list, `[]` or `[{"number": ...}]` | fix the repo name in the command, or ask the repo owner for access |

## Steps

### 1. Run the demo on the synthetic data

This shows what the pane does before anything on your machine changes. The
demo reads only the files in `examples/demo`: a build map, a PR list in the
shape `gh` prints, an agent list in the shape `herdr agent list` prints, and
the lane cards.

```bash
bash examples/run.sh
```

Verify: `bash examples/run.sh | diff - examples/expected-output.txt && echo same`
Expected: `same`

### 2. Run the checks

`check.sh` runs the offline tests and the scrub (the check that nothing
private is in the repo). The tests pin the behaviour: the stage math, where a
PR waits, the lane rows, and that a failing source shows "unknown" instead of
ending the pane.

```bash
./check.sh
```

Verify: `./check.sh 2>/dev/null | tail -1`
Expected: `check: ok`

### 3. Open the demo in a Herdr tab

`scripts/open-map-tab.sh` does two things with Herdr's own CLI:
`herdr tab create --label Map --no-focus` makes a tab and its first pane, and
`herdr pane run <pane id> "<command>"` starts the program in that pane. It
prints the pane id. Your focus stays where it is.

```bash
bash scripts/open-map-tab.sh --demo
```

Verify: wait two seconds, then `herdr pane read <the pane id it printed> --source visible | grep -c 'ACME-WEB'`
Expected: `1`

Look at the tab: the spinners turn and the clock moves. Close it when you have
seen it: Ctrl-C in its pane, then close the tab in Herdr. Step 10 opens the
real one.

### 4. Copy the example config

The program takes every setting from one TOML file, so the code holds no
names, hosts or paths of yours.

```bash
mkdir -p ~/.config/playbook-herdr-build-map
cp -n examples/config.example.toml ~/.config/playbook-herdr-build-map/config.toml
```

Verify: `test -f ~/.config/playbook-herdr-build-map/config.toml && echo copied`
Expected: `copied`

### 5. Write your build into the config

Open `~/.config/playbook-herdr-build-map/config.toml` and set, from the
comments in it:

- `title`: the first line of the pane.
- `build_map` and `cards`: where the build map file and the lane cards live,
  for example `~/projects/acme-web/build-map.json` and
  `~/projects/acme-web/cards`.
- `repo`: the GitHub repo of the build, and `pr_search` to keep the list to
  this build's PRs (for example `created:>=2026-01-01`).
- One `[[lanes]]` table per agent lane, in the order you want the rows. A
  lane's PRs are the ones whose head branch starts with `<name>/`; set
  `branch` when your branches look different.
- `agents = "cards"` if your agents do not run in Herdr.

Verify: `python3 src/build_map.py --once --no-color | head -1`
Expected: your title and the time. (Lines with "unknown" are expected until
steps 6 to 9 are done.)

### 6. Start the build map file

The build map is the one file the orchestrator agent keeps. A stage gives its
progress in one of three ways: `pct` (0 to 100), `done` and `total` (pieces),
or `prs` (a list of PR numbers; the bar is merged over listed). `weight` says
how much the stage counts in OVERALL. `for_you` holds lines that wait for a
person.

```bash
cp -n examples/demo/build-map.json ~/projects/acme-web/build-map.json
```

Then change the stages to your build's own.

Verify: `python3 src/build_map.py --once --no-color | grep -c 'build-map.json could not be read'`
Expected: `0`

### 7. Give each lane's agent its lane name in Herdr

The pane finds a lane's state by the agent's name in Herdr. Herdr takes a
name only when it is a lowercase letter, then lowercase letters, digits, `-`
or `_`, 32 characters at most. When a lane's `name` breaks that rule (`Web`,
`api.v2`), set `agent = "web"` in its `[[lanes]]` table: the program refuses
such a config and names the lane. Skip this step when `agents = "cards"`.

List the panes:

```bash
herdr pane list --workspace "$HERDR_WORKSPACE_ID"
```

Match each lane to its pane by the `label`, `cwd` or `title` in that JSON, or
ask the person which pane runs which lane. Leave out `--workspace` when lanes
run in other workspaces, so the list is not kept to this one.

A pane where an agent already runs: name that agent (ask the person first: a
rename is visible in their Herdr). Herdr renames only an agent it has already
detected in the pane.

```bash
herdr agent rename <pane id> api
```

A pane at an empty shell prompt: a new agent gets its name when it starts.
Ask the person first, and use only a pane they name: this starts a new agent
session there.

```bash
herdr agent start api --kind claude --pane <pane id>
```

Verify: `python3 src/build_map.py --show-agents | tail -1`
Expected: `every lane has an agent` (otherwise it lists the lanes it did not find)

### 8. Tell the agents what to write

Paste this into the orchestrator agent's instructions (its CLAUDE.md,
AGENTS.md, or a message). Change the path:

```text
Keep ~/projects/acme-web/build-map.json current. After each merge, and when a
stage starts or ends, update its "stages" (label, weight, and pct, or done and
total, or prs) and its "for_you" list (one line per thing that waits for the
person). Write valid JSON and nothing else in that file.
```

And this into each lane agent's instructions:

```text
Keep a card at ~/projects/acme-web/cards/<your lane name>.json: {"now": "one
short line: what you do now", "updated": "<the time, ISO 8601>"}. Add
"question": "..." when you wait for the person, and "done": true when your
lane has nothing left. Name your branches <your lane name>/<topic>.
```

When `agents = "cards"`, add `"state": "working"` (or `idle`, `done`,
`blocked`) to the card text. Add `cards/` to the project's `.gitignore`: the
cards change all the time.

Verify: after each lane has had a turn, `ls ~/projects/acme-web/cards`
Expected: one `<lane>.json` per lane

### 9. Check that the lanes' PRs are found

The PR QUEUE is one `gh pr list` call every two minutes. A PR waits in one of
four places: a draft is "building"; a failing check or a request for changes
is "STOP, in rework"; green checks with an approval (or no review required)
are "passed, to merge"; the rest is "check and review". A lane's row follows
its newest open PR, found by the head branch. List the open branches:

```bash
gh pr list --repo acme/acme-web --state open --json number,headRefName --jq '.[] | "\(.number) \(.headRefName)"'
```

Each branch of a lane should start with that lane's `branch` prefix (by
default `<lane>/`). Fix the config, or ask the lane agents to name their
branches that way (step 8).

Verify: `python3 src/build_map.py --once --no-color --width 200 | grep 'PR QUEUE'`
Expected: ` PR QUEUE ... N open · M merged in 24 h` with your numbers, not
`unknown`

The list command above is not the call the program makes: the program adds
`--state all`, `--limit` and your `pr_search`, and runs the `gh` of the
config. So when the Verify says `unknown:`, read the words after it: they are
the error of the program's own call (for example `gh: To get started with
GitHub CLI, please run: gh auth login`). Fix what it names (the sign-in,
`repo`, `pr_search` or the `gh` path) and run the Verify again.

### 10. Open the Map tab

```bash
bash scripts/open-map-tab.sh
```

If your config is not at the default path, pass it:
`bash scripts/open-map-tab.sh ~/builds/acme.toml`. When `$BUILD_MAP_CONFIG`
is set and no argument is given, the script passes that path on as
`--config`: the new pane is started by the Herdr server, not by your shell,
so it may not see the variable.

Verify: wait five seconds, then `herdr pane read <the pane id it printed> --source visible | grep -c 'LANES'`
Expected: `1`

## Adapt it

| Setting | Where | Example |
|---|---|---|
| The config file | `--config FILE`, or `$BUILD_MAP_CONFIG` | `BUILD_MAP_CONFIG=~/builds/acme.toml` |
| The first line | config, `title` | `title = "ACME-WEB · BUILD"` |
| Stages and their weights | `build-map.json`, `stages` | `{"label": "Phase 1", "weight": 5, "prs": [106, 107]}` |
| Lines for a person | `build-map.json`, `for_you` | `"for_you": ["read the copy for the price page"]` |
| The lanes, in row order | config, `[[lanes]]` | `name = "api"` |
| A lane's Herdr agent name | config, `[[lanes]]` `agent` | `agent = "docs-writer"` |
| A lane's branch prefix | config, `[[lanes]]` `branch` | `branch = "feature/web-"` |
| Lane states without Herdr | config, `agents`, and `"state"` in each card | `agents = "cards"` |
| The herdr or gh command | config, `herdr`, `gh` | `gh = "/usr/local/bin/gh"` |
| Which PRs count | config, `pr_search`, `pr_limit` | `pr_search = "created:>=2026-01-01"` |
| No PR QUEUE | config, `repo` | `repo = ""` |
| The label for FOR YOU | config, `for_you_label` | `for_you_label = "needs-owner"` |
| Your own source script | config, `pr_file`, `agents_file` (JSON in gh's or herdr's shape) | `pr_file = "prs.json"` |
| How often the slow reads run | config, `[refresh]` `lanes`, `prs` (seconds) | `prs = 300` |
| The tab's label | env `BUILD_MAP_TAB` for `scripts/open-map-tab.sh` | `BUILD_MAP_TAB=Build` |
| Plain text | `--no-color`, or env `NO_COLOR=1` | `python3 src/build_map.py --no-color` |

## Done when

- `./check.sh` prints `check: ok`.
- `python3 src/build_map.py --show-agents | tail -1` prints
  `every lane has an agent` (skip with `agents = "cards"`).
- `python3 src/build_map.py --once --no-color | grep -c unknown` prints `0`.
- The Map tab redraws: `herdr pane read <pane id> --source visible | head -1`,
  run twice a few seconds apart, shows two different times.

## Undo

- Stop the pane with Ctrl-C and close the Map tab in Herdr (or
  `herdr tab close <tab id>`; `herdr tab list --workspace "$HERDR_WORKSPACE_ID"`
  shows the ids).
- Remove the config: `rm -r ~/.config/playbook-herdr-build-map`.
- Remove the lines from step 8 in the agents' instructions, and, if you no
  longer want them, the project's `build-map.json`, its `cards/` folder and
  the `cards/` line in its `.gitignore`.
- The agent names from step 7 end when each agent exits; rename them again
  with `herdr agent rename` if you want other names sooner.
- Delete the clone of this repo.

## Safety

- Reads: the config, the build map, the lane cards, the output of
  `herdr agent list` (agent names and states) and of `gh pr list` (PR
  numbers, titles, branches, labels, check results, review decisions, merge
  times).
- Writes: nothing to disk. It only draws in its own terminal.
  `scripts/open-map-tab.sh` creates one Herdr tab and runs one command in it.
- Never: it sends no input to any pane or agent, merges, comments, labels or
  pushes nothing, reads no secrets (gh uses its own sign-in, which the program
  never sees), and opens no network connection itself (only `gh` does, for
  its list).
- The pane shows PR titles and the cards' text. Mind a shared screen, and
  never put a secret in a card or in the build map.
