"""Offline tests for the build map. They pin the behaviour on the synthetic data in
examples/demo and on small hand-made inputs. No network, no Herdr, no gh."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import build_map as bm  # noqa: E402

DEMO = ROOT / "examples" / "demo"
NOW = bm.parse_time(bm.DEMO_AT).timestamp()  # type: ignore[union-attr]


def lane(name: str, state: str = "idle", **card: object) -> dict:
    return dict(name=name, branch=f"{name}/", state=state, now=str(card.get("now", "")),
                done=card.get("done") is True, question=str(card.get("question", "")), updated=None)


def snap(**over: object) -> dict:
    base = dict(ts=NOW, clock="16:08:51", title="T", map={"stages": []}, prs=[], prs_note="",
                agents={}, agents_note="", agents_source="herdr", lanes=[lane("api")], for_you_label="for-you")
    base.update(over)
    return base


def pr(number: int, state: str = "OPEN", **fields: object) -> dict:
    return dict(number=number, state=state, title=f"PR {number}", **fields)


def demo_config(tmp: Path, **changes: str) -> Path:
    """A copy of the demo config with some lines changed, pointing at the demo's files."""
    text = (DEMO / "config.toml").read_text(encoding="utf-8")
    for old, new in changes.items():
        assert old in text, old
        text = text.replace(old, new)
    for name in ("build-map.json", "agents.json", "prs.json", "cards"):
        text = text.replace(f'"{name}"', json.dumps(str(DEMO / name)))
    path = tmp / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class DemoTest(unittest.TestCase):
    def test_run_sh_prints_the_expected_output(self) -> None:
        out = subprocess.run(["bash", str(ROOT / "examples" / "run.sh")], capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout, (ROOT / "examples" / "expected-output.txt").read_text(encoding="utf-8"))

    def test_the_demo_shows_every_block(self) -> None:
        text = (ROOT / "examples" / "expected-output.txt").read_text(encoding="utf-8")
        for word in ("OVERALL", "STAGES", "PR QUEUE", "LANES", "AGENTS", "FOR YOU"):
            self.assertIn(word, text)
        self.assertIn("7 open · 12 merged in 24 h", text)
        self.assertIn("AGENTS 4 working · 6 waiting · 1 blocked: infra", text)
        self.assertIn(" 62%", text.splitlines()[2])  # (92*2 + 67*5 + 100*1 + 0*2) / 10

    def test_the_readme_preview_is_the_demo_output(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        preview = readme.split("## Preview", 1)[1].split("```text\n", 1)[1].split("```", 1)[0]
        self.assertEqual(preview, (ROOT / "examples" / "expected-output.txt").read_text(encoding="utf-8"))

    def test_the_colour_frame_has_the_same_text(self) -> None:
        config = bm.load_config(bm.DEMO_CONFIG)
        cache: dict = {}
        bm.fill(config, cache)
        lines = bm.render(bm.view(config, cache, bm.Clock(bm.DEMO_AT, frozen=True)), 78)
        self.assertTrue(any("\x1b[" in line for line in lines))
        expected = (ROOT / "examples" / "expected-output.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(bm.plain(lines), expected)


class StageTest(unittest.TestCase):
    def test_three_ways_to_give_progress(self) -> None:
        prs = [pr(1, "MERGED"), pr(2, "OPEN"), pr(3, "MERGED"), pr(4, "CLOSED")]
        self.assertEqual(bm.stage_view({"pct": 42}, prs)["pct"], 42)
        self.assertEqual(bm.stage_view({"pct": 140}, prs)["pct"], 100)
        self.assertEqual(bm.stage_view({"done": 3, "total": 4}, prs)["pct"], 75)
        view = bm.stage_view({"prs": [1, 2, 3, 4]}, prs)
        self.assertEqual((view["pct"], view["note"]), (50, "2 of 4 PRs merged · 1 open"))

    def test_a_stage_says_when_its_prs_are_not_in_the_list(self) -> None:
        view = bm.stage_view({"prs": [1, 2, 99]}, [pr(1, "MERGED"), pr(2)])
        self.assertEqual(view["note"], "1 of 3 PRs merged · 1 open · 1 not in the PR list")

    def test_a_frame_with_no_lanes_still_draws(self) -> None:
        self.assertIn(" LANES", "\n".join(bm.plain(bm.render(snap(lanes=[]), 60))))

    def test_a_stage_without_its_source_is_unknown(self) -> None:
        self.assertFalse(bm.stage_view({"prs": [1, 2]}, None)["known"])
        self.assertFalse(bm.stage_view({"label": "x"}, [])["known"])
        self.assertFalse(bm.stage_view({"done": 1, "total": 0}, [])["known"])
        self.assertFalse(bm.stage_view("not an object", [])["known"])

    def test_overall_is_the_weighted_mean_of_the_known_stages(self) -> None:
        stages = [bm.stage_view({"pct": 100, "weight": 3}, []), bm.stage_view({"pct": 0, "weight": 1}, []),
                  bm.stage_view({"prs": [9], "weight": 50}, None)]  # unknown: left out
        self.assertEqual(bm.overall(stages), 75)
        self.assertIsNone(bm.overall([bm.stage_view({}, [])]))


class PrTest(unittest.TestCase):
    ok = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]

    def test_the_stage_of_an_open_pr(self) -> None:
        self.assertEqual(bm.pr_stage(pr(1, isDraft=True, statusCheckRollup=self.ok)), "draft")
        self.assertEqual(bm.pr_stage(pr(1, reviewDecision="CHANGES_REQUESTED", statusCheckRollup=self.ok)), "rework")
        self.assertEqual(bm.pr_stage(pr(1, statusCheckRollup=[{"conclusion": "FAILURE"}])), "rework")
        self.assertEqual(bm.pr_stage(pr(1, statusCheckRollup=[{"state": "ERROR"}])), "rework")
        self.assertEqual(bm.pr_stage(pr(1, statusCheckRollup=[{"status": "IN_PROGRESS", "conclusion": ""}])), "review")
        self.assertEqual(bm.pr_stage(pr(1, statusCheckRollup=[{"state": "PENDING"}])), "review")
        self.assertEqual(bm.pr_stage(pr(1, reviewDecision="REVIEW_REQUIRED", statusCheckRollup=self.ok)), "review")
        self.assertEqual(bm.pr_stage(pr(1, reviewDecision="APPROVED", statusCheckRollup=self.ok)), "passed")
        self.assertEqual(bm.pr_stage(pr(1)), "passed")  # no checks and no review required

    def test_queue_counts_only_the_merges_of_the_last_24_hours(self) -> None:
        prs = [pr(1, isDraft=True), pr(2, "MERGED", mergedAt="2026-01-15T09:00:00Z"),
               pr(3, "MERGED", mergedAt="2026-01-13T09:00:00Z"), pr(4, "MERGED", mergedAt=None),
               pr(5, "CLOSED"), pr(6, statusCheckRollup=[{"conclusion": "FAILURE"}])]
        by, merged = bm.queue(prs, NOW)
        self.assertEqual(merged, 1)
        self.assertEqual(by, {"draft": [1], "review": [], "passed": [], "rework": [6]})

    def test_a_lane_follows_its_newest_open_pr(self) -> None:
        prs = [pr(10, headRefName="api/a", isDraft=True), pr(12, headRefName="api/b", isDraft=True),
               pr(14, "MERGED", headRefName="api/c"), pr(16, headRefName="apixyz/d")]
        self.assertEqual(bm.lane_pr("api/", prs)["number"], 12)  # type: ignore[index]
        self.assertIsNone(bm.lane_pr("web/", prs))
        self.assertIsNone(bm.lane_pr("api/", None))


class LaneTest(unittest.TestCase):
    def test_phase_word_and_look(self) -> None:
        rework = [pr(5, headRefName="api/x", statusCheckRollup=[{"conclusion": "FAILURE"}])]
        cases = [
            (lane("api", "working"), [], ("build", 25, "active")),
            (lane("api", "idle"), [], ("idle", 0, "pending")),
            (lane("api", "done", done=True), [], ("done", 100, "done")),
            (lane("api", "blocked"), [], ("blocked", 0, "failed")),
            (lane("api", "off"), [], ("off", 0, "pending")),
            (lane("api", "unknown"), [], ("unknown", 0, "pending")),
            (lane("api", "idle", question="which?"), [], ("idle", 0, "decide")),
            (lane("api", "working"), rework, ("fix", 50, "failed")),
            (lane("api", "working"), [pr(6, headRefName="api/y", isDraft=True)], ("build", 40, "active")),
        ]
        for row, prs, expected in cases:
            view = bm.lane_view(row, prs)
            self.assertEqual((view["word"], view["pct"], view["look"]), expected, (row, prs))

    def test_herdr_agents_are_read_whatever_the_nesting(self) -> None:
        reply = {"result": {"agents": [{"name": "api", "agent_status": "working"},
                                       {"agent_name": "web", "status": "IDLE"},
                                       {"name": "docs", "state": "sleeping"},
                                       {"pane_id": "w1:p9", "agent_status": "idle"}]}}
        self.assertEqual(bm.parse_agents(reply), {"api": "working", "web": "idle", "docs": "unknown"})
        self.assertEqual(bm.parse_agents([{"name": "a", "agent_status": "blocked"}]), {"a": "blocked"})
        self.assertEqual(bm.parse_agents("nothing"), {})


class FailSoftTest(unittest.TestCase):
    def test_failing_sources_show_unknown_and_the_frame_still_draws(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = demo_config(Path(tmp), **{
                'agents_file = "agents.json"': 'herdr = "/nonexistent/herdr"',
                'pr_file = "prs.json"': 'repo = "acme/acme-web"\ngh = "/nonexistent/gh"',
                'build_map = "build-map.json"': 'build_map = "missing.json"'})
            config = bm.load_config(path)
            cache: dict = {}
            bm.fill(config, cache)
            self.assertIsNone(cache["agents"])
            self.assertIsNone(cache["prs"])
            text = "\n".join(bm.plain(bm.render(bm.view(config, cache, bm.Clock(bm.DEMO_AT, frozen=True)), 80)))
        self.assertIn("OVERALL unknown", text)
        self.assertIn("unknown: build-map.json could not be read", text)
        self.assertIn("unknown: [Errno 2] No such file or directory: '/nonexistent/gh'", text)
        self.assertIn("AGENTS unknown: herdr agent list failed", text)
        lanes_line = next(line for line in text.splitlines() if line.startswith(" LANES"))
        self.assertIn("unknown", lanes_line)

    def test_a_broken_card_or_map_is_read_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "api.json"
            card.write_text("{not json", encoding="utf-8")
            self.assertEqual(bm.read_card(card), ({}, None))
            bad = Path(tmp) / "map.json"
            bad.write_text("[1, 2]", encoding="utf-8")
            self.assertIsNone(bm.soft(lambda: bm.read_map(bad)))

    def test_a_frame_that_raises_becomes_a_message(self) -> None:
        def broken() -> list[str]:
            raise KeyError("stages")
        lines = bm.safe_frame(broken)
        self.assertIn("KeyError", lines[0])

    def test_slow_sources_are_read_on_their_own_clock(self) -> None:
        calls = {"prs": 0, "agents": 0}
        config = bm.load_config(bm.DEMO_CONFIG)  # lanes every 5 s, PRs every 120 s
        real_prs, real_agents = bm.read_prs, bm.read_agents

        def count_prs(c: bm.Config) -> list:
            calls["prs"] += 1
            return real_prs(c)

        def count_agents(c: bm.Config) -> dict:
            calls["agents"] += 1
            return real_agents(c)
        bm.read_prs, bm.read_agents = count_prs, count_agents  # type: ignore[assignment]
        try:
            cache: dict = {}
            for t in range(config.refresh_prs):  # one fill a second, like the reader thread
                bm.fill(config, cache, at=float(t))
            self.assertEqual(calls, {"prs": 1, "agents": config.refresh_prs // config.refresh_lanes})
            bm.fill(config, cache, at=float(config.refresh_prs))
            self.assertEqual(calls["prs"], 2)
        finally:
            bm.read_prs, bm.read_agents = real_prs, real_agents  # type: ignore[assignment]

    def test_a_failing_gh_shows_its_own_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "gh"
            fake.write_text("#!/bin/sh\n"
                            "echo 'To get started with GitHub CLI, please run:  gh auth login' >&2\n"
                            "echo 'Alternatively, populate the GH_TOKEN environment variable.' >&2\n"
                            "exit 4\n", encoding="utf-8")
            fake.chmod(0o755)
            config = bm.load_config(demo_config(Path(tmp), **{
                'pr_file = "prs.json"': f'repo = "acme/acme-web"\ngh = {json.dumps(str(fake))}'}))
            cache: dict = {}
            bm.fill(config, cache)
            text = "\n".join(bm.plain(bm.render(bm.view(config, cache, bm.Clock(bm.DEMO_AT, frozen=True)), 120)))
            self.assertIn("unknown: gh: To get started with GitHub CLI, please run: gh auth login", text)
            fake.write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")  # gh works again
            bm.fill(config, cache, at=float("inf"))
            self.assertEqual((cache["prs"], cache["prs_error"]), ([], ""))

    def test_no_repo_means_the_pr_queue_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = bm.load_config(demo_config(Path(tmp), **{'pr_file = "prs.json"': ""}))
            cache: dict = {}
            bm.fill(config, cache)
            text = "\n".join(bm.plain(bm.render(bm.view(config, cache, bm.Clock(bm.DEMO_AT, frozen=True)), 80)))
        self.assertIn("not set: no repo in the config", text)


class FrameTest(unittest.TestCase):
    def demo_snap(self) -> dict:
        config = bm.load_config(bm.DEMO_CONFIG)
        cache: dict = {}
        bm.fill(config, cache)
        return bm.view(config, cache, bm.Clock(bm.DEMO_AT, frozen=True))

    def test_no_line_is_wider_than_the_pane(self) -> None:
        data = self.demo_snap()
        for width in (50, 61, 78, 120, 240):
            for line in bm.plain(bm.render(data, width)):
                self.assertLessEqual(len(line), width, (width, line))

    def test_a_short_pane_drops_detail_before_it_cuts(self) -> None:
        data = self.demo_snap()
        full = bm.plain(bm.render(data, 78))
        short = bm.plain(bm.render(data, 78, height=30))
        self.assertLessEqual(len(short), 29)
        self.assertLess(len(short), len(full))
        text = "\n".join(short)
        self.assertIn("+3 more lanes, quiet", text)  # orch, web and docs are quiet
        self.assertIn("FOR YOU", text)  # the bottom block survives
        tiny = bm.render(data, 78, height=8)
        self.assertEqual(len(tiny), 7)

    def test_a_busy_build_keeps_the_footer_and_for_you(self) -> None:
        names = [f"lane{i:03d}" for i in range(100)]
        data = snap(lanes=[lane(n, "working") for n in names], agents={n: "working" for n in names},
                    map={"stages": [{"label": "one", "pct": 50}], "for_you": ["sign the release"]})
        lines = bm.plain(bm.render(data, 80, height=40))
        text = "\n".join(lines)
        self.assertLessEqual(len(lines), 39)
        self.assertIn("AGENTS 100 working", text)
        self.assertIn(" FOR YOU", text)
        self.assertIn("· sign the release", text)
        shown = sum(1 for line in lines if line.lstrip()[:1] in bm.SPIN and "lane" in line)
        self.assertIn(f"+{100 - shown} more lanes, working", text)

    def test_a_busy_build_shows_the_failing_and_asking_lanes_first(self) -> None:
        lanes = [lane(f"work{i}", "working") for i in range(50)] + [lane(f"calm{i}") for i in range(50)]
        lanes += [lane("stuck", "blocked"), lane("asks", question="which?")]
        lines = bm.plain(bm.render(snap(lanes=lanes), 80, height=40))
        text = "\n".join(lines)
        self.assertLessEqual(len(lines), 39)
        self.assertTrue(any(line.startswith(" ✗ stuck") for line in lines), text)
        self.assertTrue(any(line.startswith(" ◆ asks") for line in lines), text)
        self.assertIn("+50 more lanes, quiet", text)
        self.assertRegex(text, r"\+\d+ more lanes, working")
        self.assertIn("· asks: which?", text)

    def test_the_spinner_turns_on_active_rows_only(self) -> None:
        data = self.demo_snap()
        a, b = bm.plain(bm.render(data, 78, tick=0)), bm.plain(bm.render(data, 78, tick=1))
        changed = [x for x, y in zip(a, b) if x != y]
        self.assertTrue(changed)
        self.assertTrue(all(x.lstrip()[0] in bm.SPIN for x in changed))

    def test_for_you_lists_questions_labelled_prs_and_map_lines(self) -> None:
        data = snap(lanes=[lane("auth", question="cookies?")],
                    prs=[pr(7, labels=[{"name": "for-you"}]), pr(8, "MERGED", labels=[{"name": "for-you"}])],
                    map={"stages": [], "for_you": ["sign in once"]})
        text = "\n".join(bm.plain(bm.render(data, 80)))
        self.assertIn("· auth: cookies?", text)
        self.assertIn("· #7 waits for you: PR 7", text)
        self.assertNotIn("#8", text)
        self.assertIn("· sign in once", text)
        self.assertIn("nothing waits for you", "\n".join(bm.plain(bm.render(snap(), 80))))

    def test_lane_states_from_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cards = Path(tmp) / "cards"
            cards.mkdir()
            (cards / "api.json").write_text('{"state": "working", "now": "x"}', encoding="utf-8")
            (cards / "web.json").write_text('{"state": "napping"}', encoding="utf-8")
            config = bm.parse_config({"agents": "cards", "cards": str(cards),
                                      "lanes": [{"name": "api"}, {"name": "web"}]}, Path(tmp))
            data = bm.view(config, {}, bm.Clock(bm.DEMO_AT, frozen=True))
        self.assertEqual([x["state"] for x in data["lanes"]], ["working", "unknown"])
        self.assertIn("AGENTS 1 working · 0 waiting (from the lane cards)", "\n".join(bm.plain(bm.render(data, 80))))


class ConfigTest(unittest.TestCase):
    def test_defaults(self) -> None:
        config = bm.parse_config({"lanes": [{"name": "api"}]}, Path("/base"))
        self.assertEqual(config.lanes, (bm.Lane("api", "api", "api/"),))
        self.assertEqual((config.build_map, config.cards), (Path("/base/build-map.json"), Path("/base/cards")))
        self.assertEqual((config.agents, config.repo, config.refresh_lanes, config.refresh_prs), ("herdr", "", 5, 120))

    def test_bad_settings_are_refused_with_a_reason(self) -> None:
        bad = [{}, {"lanes": []}, {"lanes": [{"name": "a"}, {"name": "a"}]}, {"lanes": [{"name": "../x"}]},
               {"lanes": [{"name": "a"}], "agents": "radio"}, {"lanes": [{"name": "a"}], "pr_limit": 0},
               {"lanes": [{"name": "a"}], "refresh": {"prs": "often"}}, {"lanes": [{"name": 3}]}]
        for raw in bad:
            with self.assertRaises(SystemExit, msg=raw) as caught:
                bm.parse_config(raw, Path("."))
            self.assertIn("config", str(caught.exception))

    def test_a_herdr_agent_name_follows_herdrs_rule(self) -> None:
        # Herdr: a lowercase letter, then lowercase letters, digits, - or _, 32 characters at most.
        for name in ("Web", "api.v2", "2fa", "a" * 33):
            with self.assertRaises(SystemExit, msg=name) as caught:
                bm.parse_config({"lanes": [{"name": name}]}, Path("."))
            self.assertIn("agent =", str(caught.exception))
            with self.assertRaises(SystemExit, msg=name):
                bm.parse_config({"lanes": [{"name": "web", "agent": name}]}, Path("."))
            cards = bm.parse_config({"agents": "cards", "lanes": [{"name": name}]}, Path("."))
            self.assertEqual(cards.lanes[0].name, name)  # cards mode asks Herdr nothing
        fixed = bm.parse_config({"lanes": [{"name": "Web", "agent": "web"}]}, Path("."))
        self.assertEqual(fixed.lanes[0].agent, "web")
        for good in ("a" * 32, "docs-writer", "lane_2"):
            self.assertEqual(bm.parse_config({"lanes": [{"name": good}]}, Path(".")).lanes[0].agent, good)

    def test_the_example_config_loads(self) -> None:
        config = bm.load_config(ROOT / "examples" / "config.example.toml")
        self.assertEqual([x.name for x in config.lanes], ["orch", "api", "web", "docs"])
        self.assertEqual(config.lanes[2].branch, "feature/web-")
        self.assertEqual(config.lanes[3].agent, "docs-writer")

    def test_a_missing_config_says_what_to_do(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            bm.load_config(Path("/nonexistent/config.toml"))
        self.assertIn("config.example.toml", str(caught.exception))


class ShowAgentsTest(unittest.TestCase):
    def test_it_lists_agents_and_missing_lanes(self) -> None:
        out = subprocess.run([sys.executable, str(ROOT / "src" / "build_map.py"), "--demo", "--show-agents"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("api working", out)
        self.assertIn("every lane has an agent", out)


class OpenTabTest(unittest.TestCase):
    """scripts/open-map-tab.sh against a stand-in herdr that records its arguments."""

    def run_script(self, *args: str, inside: bool = True,
                   env_extra: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            fake = Path(tmp) / "herdr"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if sys.argv[1:3] == ['tab', 'create']:\n"
                "    print(json.dumps({'result': {'tab': {'tab_id': 'w1:t9'}, 'root_pane': {'pane_id': 'w1:p42'}}}))\n"
                "else:\n"
                "    print(json.dumps({'result': {'type': 'ok'}}))\n", encoding="utf-8")
            fake.chmod(0o755)
            env = {k: v for k, v in os.environ.items() if not k.startswith(("HERDR", "BUILD_MAP"))}
            env["HERDR"] = str(fake)
            env.update(env_extra or {})
            if inside:
                env.update(HERDR_ENV="1", HERDR_WORKSPACE_ID="w1")
            done = subprocess.run(["bash", str(ROOT / "scripts" / "open-map-tab.sh"), *args],
                                  capture_output=True, text=True, env=env)
            calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return done, calls

    def test_it_creates_one_tab_and_runs_the_map_in_its_pane(self) -> None:
        done, calls = self.run_script("--demo")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls[0][:4], ["tab", "create", "--label", "Map"])
        self.assertIn("--no-focus", calls[0])
        self.assertEqual(calls[0][-2:], ["--workspace", "w1"])
        self.assertEqual(calls[1][:3], ["pane", "run", "w1:p42"])
        self.assertTrue(calls[1][3].endswith("build_map.py --demo"))
        self.assertEqual(len(calls), 2)
        self.assertIn("tab w1:t9", done.stdout)  # the id `herdr tab close` needs
        self.assertIn("pane w1:p42", done.stdout)

    def test_it_passes_build_map_config_on_to_the_new_pane(self) -> None:
        # The new pane is started by the Herdr server, so the script names the config itself.
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "acme.toml"
            config.write_text("", encoding="utf-8")
            done, calls = self.run_script(env_extra={"BUILD_MAP_CONFIG": str(config)})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(calls[1][3].endswith(f"build_map.py --config {config}"), calls[1])
            other = Path(tmp) / "other.toml"
            other.write_text("", encoding="utf-8")
            done, calls = self.run_script(str(other), env_extra={"BUILD_MAP_CONFIG": str(config)})
            self.assertTrue(calls[1][3].endswith(f"build_map.py --config {other}"), calls[1])  # the argument wins
            done, calls = self.run_script("--demo", env_extra={"BUILD_MAP_CONFIG": str(config)})
            self.assertTrue(calls[1][3].endswith("build_map.py --demo"), calls[1])
        done, calls = self.run_script(env_extra={"BUILD_MAP_CONFIG": "/nonexistent/acme.toml"})
        self.assertEqual((done.returncode, calls), (2, []))
        done, calls = self.run_script()
        self.assertTrue(calls[1][3].endswith("build_map.py"), calls[1])  # the program's own default

    def test_it_refuses_outside_herdr(self) -> None:
        done, calls = self.run_script("--demo", inside=False)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(calls, [])

    def test_a_missing_config_is_refused_before_any_tab(self) -> None:
        done, calls = self.run_script("/nonexistent/config.toml")
        self.assertEqual(done.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
