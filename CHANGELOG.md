# Changelog

Every change a reader should know about, newest first.

## 0.1.0 - 2026-11-19

- First version: `src/build_map.py` draws OVERALL, STAGES (from `build-map.json`),
  PR QUEUE (from `gh`), one LANES row per agent lane (state from `herdr agent list`
  or from the lane cards) and a footer with the agents and a FOR YOU list.
- A `--demo` mode on synthetic data in `examples/demo`, and `--once`,
  `--no-color` and `--show-agents`.
- `scripts/open-map-tab.sh` opens a Herdr tab named Map that runs the program.
- Offline tests, and `check.sh` with the scrub from playbook-packaging.
