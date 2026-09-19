# bench/: the standing measuring instrument

Fixed test inputs, committed as fixtures:

- `briefs.json`: briefs frozen by id. Add, never mutate: comparability is the point.
- `region_cases.json` + `sources/`: the five region-edit cases (KEEP, MODIFY, REPLACE, DELETE,
  and a multi-region combo) with their masks.
- `make_fixtures.py`: generates every image under `sources/`.

Runner: `scripts/bench.py`. Live region fan-out: `scripts/e2e_region_fanout.py`.

## The ritual

```bash
cd cockpit/server && uv run python ../../scripts/bench.py \
    --presets raw-sdxl,raw-flux2,prop-sheet --tag <tag>   # add new presets as they land
# mint the region sources: --role region_source --presets raw-sdxl --tag region-sources
# re-render a sheet:       --assemble-only --dir <BENCH_ROOT>/<dir>
# dry run, no GPU:         BENCH_ROOT=<dir> against a harness server (COCKPIT_HARNESS_EXECUTORS=1)
```

- Sheets land in `$BENCH_ROOT/<date>-<tag>/index.html`. Keep `BENCH_ROOT` outside the promote
  folder: a bench sheet is never a promoted asset.
- Fixed seed 424242; briefs frozen by id (add, never mutate). The parked ui-text briefs are for
  engines that can render text.
- Region cases: the live bench mints each source in a workspace from its brief (same preset and
  seed give identical pixels on the pinned runtime), uploads the committed masks verbatim as
  regions, runs the edit per semantic, and drops cells into the same sheet format.

## About the committed images

Every image under `sources/` is synthetic and carries no metadata. `make_fixtures.py` draws
them: the masks are plain ellipses and rectangles placed where the objects land when a source
is minted on the runtime recorded in `region_cases.json`, and each `source.png` is a layout
diagram of its case (green keep, blue modify, orange replace, red delete), not model output.
The bench therefore reports "committed-vs-minted DIFFER" for every source; that is expected.
