#!/usr/bin/env python3
"""The build map: one terminal pane that shows a large multi-agent build.

It draws five blocks and redraws them every second:

    OVERALL   one bar: the weighted mean of the stage bars
    STAGES    a bar per stage, from build-map.json (the orchestrator agent keeps it)
    PR QUEUE  the open PRs by where they wait, and the merges of the last 24 hours (gh)
    LANES     one row per agent lane: bar, phase, newest open PR, age of its card
    footer    the agents by state, and FOR YOU: what waits for a person

The pattern is a read-only dashboard: a plain program, not a model, reads what the build
already writes (a JSON file, the PR list, Herdr's agent list, the lanes' card files) and
draws it. Reading it costs no tokens and interrupts no agent.

Two habits keep the pane alive. The slow sources (gh, herdr) are read on their own clock in
a background thread, so a slow call never freezes the redraw. And every source fails soft:
a read that fails shows "unknown" in its block, and the pane goes on.

Usage:
    python3 src/build_map.py --demo              the synthetic build in examples/demo
    python3 src/build_map.py [--config FILE]     your build; the default config is
                                                 $BUILD_MAP_CONFIG, else
                                                 ~/.config/playbook-herdr-build-map/config.toml
    add --once to print one frame and exit, --no-color for plain text
    --show-agents prints the agent names and states the lane reader sees, then exits
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEMO_CONFIG = ROOT / "examples" / "demo" / "config.toml"
DEMO_AT = "2026-01-15T16:08:51+00:00"  # the moment the synthetic demo data is written for
DEFAULT_CONFIG = "~/.config/playbook-herdr-build-map/config.toml"

GREEN, YELLOW, RED, MAGENTA = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[35m"
DIM, BOLD, RESET = "\x1b[2m", "\x1b[1m", "\x1b[0m"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# A "look" is how a row is drawn: its colour and its mark. An active row gets a spinner.
LOOK_COLOR = {"pending": DIM, "active": YELLOW, "done": GREEN, "failed": RED, "decide": MAGENTA}
LOOK_MARK = {"pending": "·", "done": "✓", "failed": "✗", "decide": "◆"}
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# A loud lane row works, asks or fails; a short pane keeps these longest, failing ones first.
LOUD = {"failed": "failing", "decide": "asking", "active": "working"}
BAR_MAX = 60  # a bar is never wider, so a wide pane keeps its rows readable

# Where an open PR waits: key, label, colour. See pr_stage for the rules.
QUEUE = (("draft", "building (draft)", YELLOW), ("review", "check and review", YELLOW),
        ("passed", "passed, to merge", GREEN), ("rework", "STOP, in rework", RED))
QUEUE_NUMBERS = ("passed", "rework")  # the rows that list their PR numbers
# A lane's phase word and per cent, from the stage of its newest open PR.
LANE_PHASE = {"draft": ("build", 40), "review": ("review", 60), "passed": ("pass", 85), "rework": ("fix", 50)}

FAILED_CHECKS = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING_CHECKS = {"", "PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
AGENT_STATES = {"working", "idle", "done", "blocked", "unknown"}  # Herdr's lifecycle states
WAITING = {"idle", "done"}  # Herdr: the agent waits for input
LANE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")  # Herdr's own rule for an agent name
PR_FIELDS = "number,title,state,isDraft,headRefName,reviewDecision,statusCheckRollup,mergedAt,labels"


# ---- settings ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Lane:
    name: str    # the row's name, and the card file cards/<name>.json
    agent: str   # the Herdr agent name that runs the lane
    branch: str  # the head-branch prefix of the lane's PRs


@dataclass(frozen=True)
class Config:
    title: str
    lanes: tuple[Lane, ...]
    build_map: Path
    cards: Path
    agents: str = "herdr"            # where lane states come from: "herdr" or "cards"
    herdr: str = "herdr"             # the herdr command
    agents_file: Path | None = None  # read this JSON instead of running `herdr agent list`
    repo: str = ""                   # owner/name on GitHub; empty means no PR QUEUE
    gh: str = "gh"                   # the gh command
    pr_search: str = ""              # a GitHub search that limits the PRs, e.g. created:>=2026-01-01
    pr_limit: int = 300
    pr_file: Path | None = None      # read this JSON (gh's shape) instead of running gh
    for_you_label: str = "for-you"   # an open PR with this label goes on the FOR YOU list
    refresh_lanes: int = 5           # seconds between two reads of herdr
    refresh_prs: int = 120           # seconds between two reads of gh


def load_config(path: Path) -> Config:
    """Read the TOML config. Relative paths in it are relative to the config's folder."""
    path = path.expanduser()
    if not path.is_file():
        raise SystemExit(f"build_map: no config at {path}; copy examples/config.example.toml "
                         "there and change it, or pass --config")
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"build_map: {path.name} is not valid TOML: {e}") from None
    return parse_config(raw, path.parent)


def parse_config(raw: dict[str, Any], base: Path) -> Config:
    """Check the settings and fill the defaults. Pure: it reads no file."""
    def fail(why: str) -> None:
        raise SystemExit(f"build_map: config: {why}")

    def text(table: dict[str, Any], key: str, default: str = "") -> str:
        value = table.get(key, default)
        if not isinstance(value, str):
            fail(f"'{key}' must be text")
        return value

    def whole(table: dict[str, Any], key: str, default: int) -> int:
        value = table.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            fail(f"'{key}' must be a whole number, 1 or more")
        return value

    def place(key: str, default: str = "") -> Path | None:
        value = text(raw, key, default)
        return base / Path(value).expanduser() if value else None

    items = raw.get("lanes")
    if not isinstance(items, list) or not items:
        fail("add at least one [[lanes]] table with a name")
    lanes: list[Lane] = []
    for item in items:
        if not isinstance(item, dict):
            fail("every entry of 'lanes' must be a [[lanes]] table")
        name = text(item, "name")
        if not LANE_NAME.fullmatch(name):
            fail(f"lane name {name!r}: use letters, digits, dot, dash or underscore")
        if any(lane.name == name for lane in lanes):
            fail(f"lane '{name}' is listed twice")
        lanes.append(Lane(name, text(item, "agent", name), text(item, "branch", f"{name}/")))
    agents = text(raw, "agents", "herdr")
    if agents not in ("herdr", "cards"):
        fail("'agents' must be \"herdr\" or \"cards\"")
    for lane in lanes if agents == "herdr" else ():
        if not AGENT_NAME.fullmatch(lane.agent):
            fail(f"lane '{lane.name}': {lane.agent!r} is not a name Herdr takes for an agent (a lowercase "
                 "letter, then lowercase letters, digits, - or _, 32 characters at most); set "
                 "agent = \"...\" in its [[lanes]] table")
    refresh = raw.get("refresh", {})
    if not isinstance(refresh, dict):
        fail("'refresh' must be a [refresh] table")
    build_map, cards = place("build_map", "build-map.json"), place("cards", "cards")
    assert build_map is not None and cards is not None
    return Config(
        title=text(raw, "title", "BUILD"), lanes=tuple(lanes), build_map=build_map, cards=cards,
        agents=agents, herdr=text(raw, "herdr", "herdr"), agents_file=place("agents_file"),
        repo=text(raw, "repo"), gh=text(raw, "gh", "gh"), pr_search=text(raw, "pr_search"),
        pr_limit=whole(raw, "pr_limit", 300), pr_file=place("pr_file"),
        for_you_label=text(raw, "for_you_label", "for-you"),
        refresh_lanes=whole(refresh, "lanes", 5), refresh_prs=whole(refresh, "prs", 120))


# ---- pure helpers: time, bars, text -------------------------------------------------------------


def parse_time(value: Any) -> datetime | None:
    """An ISO time ("2026-01-15T16:08:51Z"); a time without a zone is local. None if it is not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age(then: float | None, now: float) -> str:
    if then is None:
        return ""
    s = max(0.0, now - then)
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s // 60)}m"
    return f"{int(s // 3600)}h" if s < 86400 else f"{int(s // 86400)}d"


def cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def hbar(pct: float, width: int) -> str:
    """A bar of eighth blocks: 0 to 100 per cent over `width` cells, never wider."""
    if width <= 0:
        return ""
    eighths = round(max(0.0, min(100.0, pct)) / 100 * width * 8)
    full, part = divmod(eighths, 8)
    tail = " ▏▎▍▌▋▊▉"[part] if part and full < width else ""
    return ("█" * full + tail).ljust(width, "░")[:width]


def look_of(pct: int) -> str:
    return "done" if pct >= 100 else "active" if pct > 0 else "pending"


def paint(text: str, look: str) -> str:
    return f"{LOOK_COLOR.get(look, '')}{text}{RESET}"


def mark(look: str, tick: int) -> str:
    return SPIN[tick % len(SPIN)] if look == "active" else LOOK_MARK.get(look, "·")


# ---- pure: stages, PRs, lanes -------------------------------------------------------------------


def stage_view(stage: Any, prs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """One stage of build-map.json as label, weight, pct, known, note and look.

    A stage gives its progress in one of three ways: "pct" (0 to 100), "done" and "total"
    (pieces), or "prs" (PR numbers: merged over listed, from the PR source). A stage that
    gives none, or whose PRs cannot be read, is unknown and left out of OVERALL."""
    stage = stage if isinstance(stage, dict) else {}
    label = str(stage.get("label") or stage.get("key") or "stage")
    weight = stage.get("weight", 1)
    weight = weight if isinstance(weight, (int, float)) and not isinstance(weight, bool) and weight >= 0 else 1
    note = str(stage.get("note") or "")

    def unknown(why: str) -> dict[str, Any]:
        return dict(label=label, weight=weight, pct=0, known=False, note=f"unknown: {why}", look="pending")

    if "prs" in stage:
        numbers = [n for n in stage["prs"] if isinstance(n, int)] if isinstance(stage["prs"], list) else []
        if not numbers:
            return unknown("'prs' lists no PR numbers")
        if prs is None:
            return unknown("the PRs could not be read")
        states = {p.get("number"): p.get("state") for p in prs}
        merged = sum(1 for n in numbers if states.get(n) == "MERGED")
        in_work = sum(1 for n in numbers if states.get(n) == "OPEN")
        missing = sum(1 for n in numbers if n not in states)  # outside pr_search or pr_limit
        pct = round(100 * merged / len(numbers))
        note = note or f"{merged} of {len(numbers)} PRs merged · {in_work} open"
        note += f" · {missing} not in the PR list" if missing else ""
    elif "done" in stage and "total" in stage:
        done, total = stage["done"], stage["total"]
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (done, total)) or total <= 0 or done < 0:
            return unknown("'done' and 'total' must be whole numbers, total above 0")
        pct = round(100 * min(done, total) / total)
        note = note or f"{done} of {total} done"
    elif "pct" in stage:
        value = stage["pct"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return unknown("'pct' must be a number")
        pct = round(max(0, min(100, value)))
    else:
        return unknown("give 'pct', 'done' and 'total', or 'prs'")
    return dict(label=label, weight=weight, pct=pct, known=True, note=note, look=look_of(pct))


def overall(stages: list[dict[str, Any]]) -> int | None:
    """The weighted mean of the known stage bars, 0 to 100; None when no stage is known."""
    parts = [(s["pct"], s["weight"]) for s in stages if s["known"]]
    weight = sum(w for _, w in parts)
    if not weight:
        return None
    return min(100, round(sum(p * w for p, w in parts) / weight))


def checks_state(rollup: Any) -> str:
    """failing, pending, passing or none, from gh's statusCheckRollup (check runs and statuses)."""
    if not isinstance(rollup, list) or not rollup:
        return "none"
    pending = False
    for check in rollup:
        if not isinstance(check, dict):
            continue
        value = str(check.get("conclusion") or check.get("state") or "").upper()
        if value in FAILED_CHECKS:
            return "failing"
        status = str(check.get("status") or "COMPLETED").upper()
        if value in PENDING_CHECKS or status != "COMPLETED":
            pending = True
    return "pending" if pending else "passing"


def pr_stage(pr: dict[str, Any]) -> str:
    """Where an open PR waits, from fields every GitHub repo has:

    draft   the PR is a draft: an agent still builds it
    rework  a check fails, or a reviewer asked for changes
    passed  the checks pass (or there are none) and the review is approved (or not required)
    review  everything else: checks still run, or a required review has not come"""
    if pr.get("isDraft"):
        return "draft"
    checks = checks_state(pr.get("statusCheckRollup"))
    review = str(pr.get("reviewDecision") or "").upper()
    if review == "CHANGES_REQUESTED" or checks == "failing":
        return "rework"
    if checks in ("passing", "none") and review in ("APPROVED", ""):
        return "passed"
    return "review"


def merged_at(pr: dict[str, Any]) -> float | None:
    when = parse_time(pr.get("mergedAt"))
    return when.timestamp() if when else None


def queue(prs: list[dict[str, Any]], now: float) -> tuple[dict[str, list[int]], int]:
    """The open PRs by stage ({stage: [numbers]}), and how many merged in the last 24 hours."""
    by: dict[str, list[int]] = {key: [] for key, _, _ in QUEUE}
    merged = 0
    for pr in prs:
        if pr.get("state") == "OPEN" and isinstance(pr.get("number"), int):
            by[pr_stage(pr)].append(pr["number"])
        elif pr.get("state") == "MERGED":
            at = merged_at(pr)
            if at is not None and 0 <= now - at <= 86400:
                merged += 1
    return {key: sorted(numbers) for key, numbers in by.items()}, merged


def lane_pr(branch: str, prs: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The lane's newest open PR: the highest number whose head branch starts with `branch`."""
    if not branch or not prs:
        return None
    mine = [p for p in prs if p.get("state") == "OPEN" and isinstance(p.get("number"), int)
            and str(p.get("headRefName") or "").startswith(branch)]
    return max(mine, key=lambda p: p["number"]) if mine else None


def lane_view(lane: dict[str, Any], prs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """A lane's row: word, pct and look, from its newest open PR, else from its card and state.

    The look: failed when its PR is in rework or the agent is blocked, decide when its card
    asks a question, done when its card says done, active while the agent works."""
    pr = lane_pr(lane["branch"], prs)
    stage = pr_stage(pr) if pr else None
    state = lane["state"]
    if stage:
        word, pct = LANE_PHASE[stage]
    elif lane["done"]:
        word, pct = "done", 100
    elif state == "working":
        word, pct = "build", 25
    else:
        word, pct = ("idle" if state in WAITING else state), 0
    if state == "blocked":
        word = "blocked"
    if stage == "rework" or state == "blocked":
        look = "failed"
    elif lane["question"]:
        look = "decide"
    elif lane["done"] and not stage:
        look = "done"
    elif state == "working":
        look = "active"
    else:
        look = "pending"
    return dict(lane, word=word, pct=pct, look=look, pr=pr["number"] if pr else None, stage=stage)


def parse_agents(data: Any) -> dict[str, str]:
    """name -> state from the JSON of `herdr agent list`.

    The reader walks the whole reply and keeps every object that has a name and a state, so
    it does not depend on how the reply nests its list. A state Herdr does not document is
    kept as "unknown"."""
    out: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            state = next((node[k] for k in ("agent_status", "status", "state") if isinstance(node.get(k), str)), None)
            name = next((node[k] for k in ("name", "agent_name") if isinstance(node.get(k), str) and node[k]), None)
            if state and name:
                out[name] = state.lower() if state.lower() in AGENT_STATES else "unknown"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return out


# ---- pure: the frame ---------------------------------------------------------------------------


def state_counts(states: list[str]) -> tuple[int, int, int]:
    return (sum(1 for s in states if s == "working"), sum(1 for s in states if s in WAITING),
            sum(1 for s in states if s == "blocked"))


def counts_text(states: list[str]) -> str:
    working, waiting, blocked = state_counts(states)
    return f"{working} working · {waiting} waiting" + (f" · {blocked} blocked" if blocked else "")


def for_you(snap: dict[str, Any], views: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(look, text) of what waits for a person: lane questions, labelled PRs, the map's own lines."""
    items = [("decide", f"{v['name']}: {v['question']}") for v in views if v["question"]]
    label = snap["for_you_label"]
    for pr in snap["prs"] or []:
        names = {str(x.get("name")) for x in pr.get("labels") or [] if isinstance(x, dict)}
        if pr.get("state") == "OPEN" and label and label in names:
            items.append(("decide", f"#{pr.get('number')} waits for you: {pr.get('title') or ''}".rstrip(": ")))
    lines = (snap["map"] or {}).get("for_you") or []
    items += [("pending", str(x)) for x in lines if isinstance(x, str)] if isinstance(lines, list) else []
    return items


def frame(snap: dict[str, Any], width: int, tick: int, level: int, loud_rows: int | None = None) -> list[str]:
    """The whole frame at one level of detail. Level 0 shows everything; 1 keeps the line under
    a lane only when it works, asks or fails; 2 drops blank lines, stage notes, empty queue rows
    and lane lines; 3 also folds the quiet lanes into one line and keeps three FOR YOU items.
    At level 3, `loud_rows` caps the loud lane rows (failing first, then asking, then working);
    the rest fold into one more line, so the footer and FOR YOU keep their place."""
    now = snap["ts"]
    rule = DIM + " " + "━" * (width - 2) + RESET
    blank = [""] if level < 2 else []

    def section(title: str, right: str = "") -> list[str]:
        gap = " " * max(1, width - 2 - len(title) - len(right))
        return [BOLD + cut(f" {title}{gap}{right}".rstrip(), width) + RESET, rule]

    title = cut(snap["title"], max(1, width - 12))
    out = [BOLD + f" {title}{' ' * max(1, width - 2 - len(title) - len(snap['clock']))}{snap['clock']}" + RESET, rule]

    # OVERALL and STAGES
    bmap = snap["map"]
    raw_stages = bmap.get("stages") if isinstance(bmap, dict) else None
    stages = [stage_view(s, snap["prs"]) for s in raw_stages] if isinstance(raw_stages, list) else []
    total = overall(stages)
    if total is None:
        out.append(paint(" OVERALL unknown", "pending"))
    else:
        out.append(paint(f" OVERALL {hbar(total, min(BAR_MAX, width - 15))} {total:>3}%", look_of(total)))
    if bmap is None:
        out += blank + section("STAGES", "unknown: build-map.json could not be read")
    elif not stages:
        out += blank + section("STAGES", "none: add 'stages' to build-map.json")
    else:
        out += blank + section("STAGES")
        label_w = min(30, width - 24, max(len(s["label"]) for s in stages))
        bar_w = max(4, min(BAR_MAX, width - label_w - 9))
        for s in stages:
            pct = f"{s['pct']:>3}%" if s["known"] else "  ? "
            out.append(paint(f" {mark(s['look'], tick)} {s['label'][:label_w]:<{label_w}} {hbar(s['pct'], bar_w)} {pct}",
                             s["look"]))
            if s["note"] and level < 2:
                out.append(paint(cut("     " + s["note"], width), "pending"))

    # PR QUEUE
    prs = snap["prs"]
    if prs is None:
        out += blank + section("PR QUEUE", snap["prs_note"])
    else:
        by, merged24 = queue(prs, now)
        n_open = sum(len(v) for v in by.values())
        out += blank + section("PR QUEUE", f"{n_open} open · {merged24} merged in 24 h")
        scale = max(1, merged24, *(len(v) for v in by.values()))
        queue_w = max(6, min(30, (width - 32) // 2))
        rows = [(label, color, len(by[key]), by[key] if key in QUEUE_NUMBERS else []) for key, label, color in QUEUE]
        rows.append(("merged in 24 h", GREEN, merged24, []))
        for label, color, n, numbers in rows:
            if level >= 2 and not n:
                continue
            tail = "".join(f" #{x}" for x in numbers)
            out.append(color + cut(f" ● {label:<18} {hbar(100 * n / scale, queue_w)} {n:>3}{tail}", width) + RESET)

    # LANES
    views = [lane_view(lane, prs) for lane in snap["lanes"]]
    states = [v["state"] for v in views]
    known = [s for s in states if s not in ("reading", "unknown")]
    right = counts_text(known) if known or not states else snap["agents_note"] or "unknown"
    out += blank + section("LANES", right)
    name_w = min(16, max([4, *(len(v["name"]) for v in views)]))
    loud_order = sorted((i for i, v in enumerate(views) if v["look"] in LOUD),
                        key=lambda i: (list(LOUD).index(views[i]["look"]), i))
    kept = set(loud_order if level < 3 or loud_rows is None else loud_order[:max(0, loud_rows)])
    quiet, folded = 0, []
    for i, v in enumerate(views):
        loud = v["look"] in LOUD
        if level >= 3 and not loud:
            quiet += 1
            continue
        if loud and i not in kept:
            folded.append(v["look"])
            continue
        head = f" {mark(v['look'], tick)} {v['name'][:name_w]:<{name_w}}"
        meta = f"{v['word'][:7]:<7} {('#' + str(v['pr'])) if v['pr'] else '':>5} {age(v['updated'], now):>4}"
        bar_w = max(4, min(BAR_MAX, width - len(head) - len(meta) - 7))
        out.append(paint(cut(f"{head} {hbar(v['pct'], bar_w)} {v['pct']:>3}% {meta}".rstrip(), width), v["look"]))
        detail = f"asks: {v['question']}" if v["question"] else v["now"]
        if detail and (level == 0 or (level == 1 and loud)):
            out.append(paint(cut(" " * (name_w + 4) + detail, width), "decide" if v["question"] else "pending"))
    if quiet:
        out.append(paint(f" +{quiet} more lanes, quiet", "pending"))
    if folded:
        kinds = [(word, folded.count(look)) for look, word in LOUD.items() if look in folded]
        what = kinds[0][0] if len(kinds) == 1 else " · ".join(f"{n} {word}" for word, n in kinds)
        worst = next(look for look in LOUD if look in folded)
        out.append(paint(f" +{len(folded)} more lanes{',' if len(kinds) == 1 else ':'} {what}", worst))

    # footer: the agents, then FOR YOU
    out.append(rule)
    agents = snap["agents"]
    if snap["agents_source"] == "cards":
        out.append(BOLD + cut(f" AGENTS {counts_text(known)} (from the lane cards)", width) + RESET)
    elif agents is None:
        out.append(BOLD + cut(f" AGENTS {snap['agents_note'] or 'unknown'}", width) + RESET)
    else:
        blocked = sorted(name for name, state in agents.items() if state == "blocked")
        text = f" AGENTS {counts_text(list(agents.values()))}" + (f": {' '.join(blocked)}" if blocked else "")
        out.append((RED if blocked else BOLD) + cut(text, width) + RESET)
    items = for_you(snap, views)
    if level >= 3 and len(items) > 3:
        items = items[:3] + [("pending", f"+{len(items) - 3} more")]
    out += blank + [BOLD + " FOR YOU" + RESET]
    out += [paint(cut(" · " + text, width), look) for look, text in items]
    if not items:
        out.append(paint(" nothing waits for you", "pending"))
    return out


def render(snap: dict[str, Any], width: int, tick: int = 0, height: int | None = None) -> list[str]:
    """The frame, no line wider than `width` (colours do not count). In a pane `height` lines
    tall it drops detail level by level before it cuts anything. At the last level it folds the
    loud lanes that do not fit into one line, so AGENTS and FOR YOU stay on screen in a busy
    build; only a pane too short even for that loses its bottom lines."""
    width = max(50, width)
    lines: list[str] = []
    for level in range(4):
        lines = frame(snap, width, tick, level)
        if height is None or len(lines) <= height - 1:
            return lines
    # At level 3 a loud lane is one row. Drop as many as the frame is too tall, plus one for
    # the line that folds them.
    loud = sum(1 for lane in snap["lanes"] if lane_view(lane, snap["prs"])["look"] in LOUD)
    over = len(lines) - (height - 1)
    if loud:
        lines = frame(snap, width, tick, 3, loud_rows=loud - over - 1)
    return lines[: max(1, height - 1)]


def plain(lines: list[str]) -> list[str]:
    return [ANSI.sub("", line) for line in lines]


# ---- the sources: each one may fail, and the pane goes on -----------------------------------------


def soft(fn: Callable[[], Any], default: Any = None) -> Any:
    """Run a read; a failure gives `default`, which the frame shows as unknown."""
    try:
        return fn()
    except Exception:  # noqa: BLE001  any read may fail (a missing tool, bad JSON, a timeout)
        return default


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_map(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("build-map.json must hold one JSON object")
    return data


def read_prs(config: Config) -> list[dict[str, Any]] | None:
    """The build's PRs: from pr_file, else one gh call. None when neither is set."""
    if config.pr_file is not None:
        data = read_json(config.pr_file)
    elif config.repo:
        argv = [config.gh, "pr", "list", "--repo", config.repo, "--state", "all",
                "--limit", str(config.pr_limit), "--json", PR_FIELDS]
        if config.pr_search:
            argv += ["--search", config.pr_search]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            raise RuntimeError((r.stderr.strip().splitlines() or [f"gh exit {r.returncode}"])[-1])
        data = json.loads(r.stdout)
    else:
        return None
    if not isinstance(data, list):
        raise ValueError("the PR list must be a JSON list")
    return [p for p in data if isinstance(p, dict)]


def read_agents(config: Config) -> dict[str, str]:
    """name -> state of every agent Herdr knows: from agents_file, else `herdr agent list`."""
    if config.agents_file is not None:
        return parse_agents(read_json(config.agents_file))
    r = subprocess.run([config.herdr, "agent", "list"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or [f"herdr exit {r.returncode}"])[-1])
    return parse_agents(json.loads(r.stdout))


def read_card(path: Path) -> tuple[dict[str, Any], float | None]:
    """A lane's card and its file time; a missing or broken card is an empty one."""
    try:
        data = read_json(path)
        return (data if isinstance(data, dict) else {}), path.stat().st_mtime
    except (OSError, ValueError):
        return {}, None


def fill(config: Config, cache: dict[str, Any], at: float | None = None) -> None:
    """Read the slow sources that are due into the cache: herdr every refresh_lanes seconds,
    gh every refresh_prs seconds. The redraw reads only the cache, so it never waits."""
    t = time.monotonic() if at is None else at
    if config.agents == "herdr" and t - cache.get("agents_at", float("-inf")) >= config.refresh_lanes:
        cache["agents"], cache["agents_at"] = soft(lambda: read_agents(config)), t
    if t - cache.get("prs_at", float("-inf")) >= config.refresh_prs:
        cache["prs"], cache["prs_at"] = soft(lambda: read_prs(config)), t


class Clock:
    """Now, in seconds. Pinned with --at (the demo pins it to DEMO_AT), it still moves on from
    that moment unless frozen, so the demo pane ticks like a real one."""

    def __init__(self, at: str | None = None, frozen: bool = False) -> None:
        self.started, self.frozen = time.time(), frozen
        when = parse_time(at) if at else None
        if at and when is None:
            raise SystemExit(f"build_map: --at {at!r} is not an ISO time like {DEMO_AT}")
        self.base = when.timestamp() if when else self.started
        self.tz: tzinfo | None = when.tzinfo if when else None

    def now(self) -> float:
        return self.base if self.frozen else self.base + (time.time() - self.started)

    def label(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.tz).strftime("%H:%M:%S")


def view(config: Config, cache: dict[str, Any], clock: Clock) -> dict[str, Any]:
    """The snapshot the frame draws: the cache, plus the small files (the map, the cards), read
    fresh each time because they are cheap."""
    now = clock.now()
    if not config.repo and config.pr_file is None:
        prs_note = "not set: no repo in the config"
    elif "prs" not in cache:
        prs_note = "reading"
    else:
        prs_note = "unknown: the PRs could not be read"
    agents = cache.get("agents") if config.agents == "herdr" else None
    if config.agents == "herdr" and "agents" not in cache:
        agents_note = "reading"
    elif config.agents == "herdr" and agents is None:
        agents_note = "unknown: herdr agent list failed"
    else:
        agents_note = ""
    lanes = []
    for lane in config.lanes:
        card, mtime = read_card(config.cards / f"{lane.name}.json")
        if config.agents == "cards":
            state = str(card.get("state") or "unknown")
            state = state if state in AGENT_STATES else "unknown"
        elif agents is None:
            state = "reading" if agents_note == "reading" else "unknown"
        else:
            state = agents.get(lane.agent, "off")
        updated = parse_time(card.get("updated"))
        question = card.get("question")
        lanes.append(dict(name=lane.name, branch=lane.branch, state=state, now=str(card.get("now") or ""),
                          done=card.get("done") is True, question=question if isinstance(question, str) else "",
                          updated=updated.timestamp() if updated else mtime))
    return dict(ts=now, clock=clock.label(now), title=config.title, map=soft(lambda: read_map(config.build_map)),
                prs=cache.get("prs"), prs_note=prs_note, agents=agents, agents_note=agents_note,
                agents_source=config.agents, lanes=lanes, for_you_label=config.for_you_label)


# ---- the pane ------------------------------------------------------------------------------------


def safe_frame(make: Callable[[], list[str]]) -> list[str]:
    """A frame that fails becomes a short message: the pane keeps running and tries again."""
    try:
        return make()
    except Exception as e:  # noqa: BLE001  a bad frame must not end the pane
        return [f" build map: {type(e).__name__}: {e}", " (the pane keeps running and tries again each second)"]


def run_pane(config: Config, clock: Clock, color: bool, width: int | None, height: int | None) -> int:
    cache: dict[str, Any] = {}
    stop = threading.Event()

    def reader() -> None:  # the slow reads live here, so a slow gh never stops the spinner
        while not stop.is_set():
            soft(lambda: fill(config, cache))
            stop.wait(1)

    threading.Thread(target=reader, daemon=True).start()
    tick = 0
    sys.stdout.write("\x1b[?25l")  # hide the cursor while the pane redraws
    try:
        while True:
            size = shutil.get_terminal_size((100, 40))
            lines = safe_frame(lambda: render(view(config, cache, clock), width or size.columns - 1, tick,
                                              height or size.lines))
            lines = lines if color else plain(lines)
            # cursor home, each line cleared to its end, then the rest: no flicker from a full clear
            sys.stdout.write("\x1b[H" + "".join(line + "\x1b[K\n" for line in lines) + "\x1b[J")
            sys.stdout.flush()
            tick += 1
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()


def show_agents(config: Config) -> int:
    """Print what the lane reader sees in Herdr, and which lanes it cannot find."""
    if config.agents != "herdr":
        print('agents = "cards": the lane states come from the lane cards, not from Herdr')
        return 0
    try:
        agents = read_agents(config)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        print(f"build_map: herdr agent list failed: {e}", file=sys.stderr)
        return 1
    for name, state in sorted(agents.items()):
        print(f"{name} {state}")
    missing = [lane.agent for lane in config.lanes if lane.agent not in agents]
    print(f"lanes not found: {' '.join(missing)}" if missing else "every lane has an agent")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw a large multi-agent build in one terminal pane.")
    parser.add_argument("--config", type=Path, help=f"the config file (default: $BUILD_MAP_CONFIG or {DEFAULT_CONFIG})")
    parser.add_argument("--demo", action="store_true", help="run on the synthetic build in examples/demo")
    parser.add_argument("--once", action="store_true", help="print one frame and exit")
    parser.add_argument("--width", type=int, help="the frame width (default: the terminal's)")
    parser.add_argument("--height", type=int, help="the frame height (default: the terminal's; none with --once)")
    parser.add_argument("--no-color", action="store_true", help="plain text, no colours (NO_COLOR does the same)")
    parser.add_argument("--at", help="pin the clock to an ISO time, for tests and previews")
    parser.add_argument("--show-agents", action="store_true", help="print the agents the lane reader sees, then exit")
    args = parser.parse_args(argv)
    if args.demo and args.config:
        parser.error("--demo uses examples/demo/config.toml; leave out --config")
    path = DEMO_CONFIG if args.demo else args.config or Path(os.environ.get("BUILD_MAP_CONFIG") or DEFAULT_CONFIG)
    config = load_config(path)
    if args.show_agents:
        return show_agents(config)
    color = not (args.no_color or os.environ.get("NO_COLOR"))
    at = args.at or (DEMO_AT if args.demo else None)
    if args.once:
        clock, cache = Clock(at, frozen=True), {}
        fill(config, cache)
        width = args.width or shutil.get_terminal_size((100, 40)).columns - 1
        lines = render(view(config, cache, clock), width, 0, args.height)
        print("\n".join(lines if color else plain(lines)))
        return 0
    return run_pane(config, Clock(at), color, args.width, args.height)


if __name__ == "__main__":
    sys.exit(main())
