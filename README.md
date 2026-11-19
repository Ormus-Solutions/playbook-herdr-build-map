# The build map: one pane that shows a large multi-agent build

One terminal pane, in a Herdr tab, that shows where a build with many coding
agents stands: how far it is, which PRs wait where, and what each agent lane
does now.

## What it is

A small Python program that draws the whole build in one pane and redraws it
every second. It has five blocks: an OVERALL bar; the STAGES of the build,
from a file the orchestrator agent keeps (`build-map.json`); the PR QUEUE, the
open PRs grouped by where they wait, read from GitHub with `gh`; one LANES row
per agent lane, with its state read from Herdr; and a footer with the agents
by state and a FOR YOU list of what waits for a person. The pattern is a
read-only dashboard, also called an information radiator: a plain program,
not a model, reads what the build already writes and shows it where you can
see it at a glance. It works because looking costs nothing. No tokens are
spent, and no agent is interrupted to answer "where are we?". The program
reads its slow sources on their own clock in a background thread, and a
source that fails shows "unknown" in its block instead of ending the pane.

## What it saved me

Ormus, in his own words: a map view for large projects with hundreds of
agents, "a great way to stay on track with progress without being a simp and
asking one session 'how much longer till we are done'". The change is in
where the answer comes from: the state of the build is on screen all the
time, so no session has to stop its work to guess at it. A member of the crew
asked for this playbook.

## Who it is for

For anyone who runs many coding agents in parallel (five lanes or more) on
one build, in Herdr, with the work landing as GitHub PRs. It also works
without Herdr: each lane then writes its state into a small card file. Skip
it if you run one or two agents: a glance at their panes tells you as much.

## Requirements

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 or newer | `python3 --version` |
| git | any recent version | `git --version` |
| Herdr | 0.9.5, the version it was written for; optional (see "cards" in PLAYBOOK.md) | `herdr --version` |
| GitHub CLI, signed in | any recent version; optional (the PR QUEUE needs it) | `gh auth status` |

The demo needs only Python. It reads synthetic files, not Herdr or GitHub.

## Quick start

### Hand it to your agent

Clone this repo, open your coding agent (Claude Code, Codex, Grok) inside the
clone, and paste this. Change the repo name and the lanes to your own:

```text
Read PLAYBOOK.md in this repo and follow it step by step to set up the build
map for my build. The GitHub repo is OWNER/NAME and the agent lanes are
LANE1, LANE2, LANE3. After each step, run its "Verify:" command and compare
the result with "Expected:". If they differ, stop and tell me what you saw.
Ask me before any step that changes something outside this repo, and before
you rename or start any agent. When you finish, run the "Done when" checks
and show me their output.
```

### Do it by hand

1. See it work: `bash examples/run.sh` prints one frame of the synthetic
   build. `python3 src/build_map.py --demo` runs the live pane on it
   (Ctrl-C ends it).
2. In a Herdr pane, open the demo in its own tab:
   `bash scripts/open-map-tab.sh --demo`. Without Herdr, the live pane of
   step 1 is the same view.
3. Copy `examples/config.example.toml` to
   `~/.config/playbook-herdr-build-map/config.toml`. Set `title`, `repo`, the
   paths of `build_map` and `cards`, and one `[[lanes]]` table per lane.
4. Give each lane's agent the lane's name in Herdr:
   `herdr agent rename <pane id> api`. Check with
   `python3 src/build_map.py --show-agents`. Without Herdr, set
   `agents = "cards"` in the config and skip this step.
5. Copy `examples/demo/build-map.json` into your project and write your own
   stages. Tell the orchestrator agent to keep it current, and each lane agent
   to keep its card (PLAYBOOK.md step 8 has the text to paste).
6. Look at one frame: `python3 src/build_map.py --once`. Then open the tab:
   `bash scripts/open-map-tab.sh`. Without Herdr, run
   `python3 src/build_map.py` in a terminal of its own.

## Preview

The output of `bash examples/run.sh` on the synthetic data in
`examples/demo` (in the pane it has colours: yellow for work in progress,
green for done, red for a stop, magenta for a question):

```text
 ACME-WEB · BUILD                                                    16:08:51
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 OVERALL █████████████████████████████████████▎░░░░░░░░░░░░░░░░░░░░░░  62%

 STAGES
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ⠋ Phase 0 · setup    ██████████████████████████████████████████████▉░░░░  92%
     11 of 12 pieces merged · the CI cache is in review
 ⠋ Phase 1 · features ██████████████████████████████████▏░░░░░░░░░░░░░░░░  67%
     14 of 21 PRs merged · 7 open
 ✓ Preview site       ███████████████████████████████████████████████████ 100%
     the preview site answers its health check
 · Phase 2 · billing  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0%
     starts when Phase 1 passes 80%

 PR QUEUE                                          7 open · 12 merged in 24 h
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ● building (draft)   █▉░░░░░░░░░░░░░░░░░░░░░   1
 ● check and review   █████▊░░░░░░░░░░░░░░░░░   3
 ● passed, to merge   ███▉░░░░░░░░░░░░░░░░░░░   2 #118 #124
 ● STOP, in rework    █▉░░░░░░░░░░░░░░░░░░░░░   1 #127
 ● merged in 24 h     ███████████████████████  12

 LANES                                      3 working · 4 waiting · 1 blocked
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 · orch    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0% idle            3m
           merging #118 when its review lands
 ⠋ api     █████████████████████████▊░░░░░░░░░░░░░░░░░  60% review   #121  27m
           login form: tests for the error messages
 · web     ████████████████████████████████████▌░░░░░░  85% pass     #124  48m
           the price page waits for its copy
 ✓ docs    ███████████████████████████████████████████ 100% done            5h
           the guide and the API reference are merged
 ◆ auth    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0% idle            8m
           asks: cookies or tokens for the session?
 ✗ billing █████████████████████▌░░░░░░░░░░░░░░░░░░░░░  50% fix      #127  38m
           invoices: the totals test fails on rounding
 ⠋ search  █████████████████▎░░░░░░░░░░░░░░░░░░░░░░░░░  40% build    #129   7m
           building the index job
 ✗ infra   █████████████████████████▊░░░░░░░░░░░░░░░░░  60% blocked  #131
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENTS 4 working · 6 waiting · 1 blocked: infra

 FOR YOU
 · auth: cookies or tokens for the session?
 · #124 waits for you: Price page copy
 · try the new search on the preview site
 · pick a name for the docs site
```

## What each block reads

| Block | Source | Read every |
|---|---|---|
| OVERALL | the weighted mean of the known stages | each redraw |
| STAGES | `build-map.json`: per stage a `pct`, a `done` and `total`, or a list of `prs` | each redraw (a small file) |
| PR QUEUE | `gh pr list --json ...`: draft, checks, review decision, merge time | 120 s |
| LANES | `herdr agent list` (the lane's state), the lane's newest open PR (by branch prefix), its card `cards/<lane>.json` | 5 s; cards each redraw |
| AGENTS | every agent Herdr lists, by state | 5 s |
| FOR YOU | the cards' questions, open PRs with the `for-you` label, the map's `for_you` lines | each redraw |

When the pane is short, it drops detail in steps: the notes, then the blank
lines, then the quiet lanes (folded into "+N more lanes, quiet"), then the
working lanes that still do not fit (folded into "+N more lanes, working"; a
failing or asking lane is kept longest). The AGENTS line and FOR YOU stay on
screen, so a build with a hundred busy lanes still shows what waits for you.
Only a pane too short even for that loses its bottom lines.

## Files

| Path | What it is |
|---|---|
| `PLAYBOOK.md` | the steps, for an agent or a person |
| `src/build_map.py` | the reference implementation (standard library only) |
| `scripts/open-map-tab.sh` | opens a Herdr tab named Map that runs the program |
| `examples/config.example.toml` | the config to copy for your own build |
| `examples/demo/` | the synthetic build: config, build map, PR list, agent list, lane cards |
| `examples/run.sh`, `examples/expected-output.txt` | one demo frame, and the text it must print |
| `tests/` | offline tests that pin the behaviour on the synthetic data |
| `check.sh` | the one gate: tests, placeholders, scrub |
| `scripts/scrub.sh` | the scrub check, copied from playbook-packaging |
| `CHANGELOG.md` | what changed, newest first |

## Credits

Made by Ormus (Ormus Solutions). Packaged with playbook-packaging by Ormus
(Ormus Solutions). MIT license.
