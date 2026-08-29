# Peer review: the geodata findings

**Status: complete and committed, awaiting adversarial review. Nothing in
`pipeline/` has been touched and nothing should be.** Two commits on
`claude/stoic-goldstine-d6b9d5` (`76317d8`, `a8856e4`) added
`docs/geodata-sources-review.md` (the original task brief) and
`docs/geodata-sources-findings.md` (the answer). No source file was modified.

## Your job

**Attack `docs/geodata-sources-findings.md`, then reach your own verdict** on
whether its recommendation — variant D, adding a WorldCover tree-cover component
alongside `c_green` rather than replacing it — should be made.

You are not being asked to agree. A review that finds the argument holds is a
good outcome; so is one that breaks it. What is *not* useful is a review that
re-derives the measurements and reports the same numbers back.

**Read the findings doc first. Do not re-derive it.** Re-run a measurement only
where you are actively testing a specific claim you doubt.

## What it claims, in one paragraph

Scenic scores road beauty from an OSM PBF plus Terrarium elevation tiles. The
findings measure all six Geofabrik New England extracts (951,182 chunks, 239,204
road-km) replicating `score.py`'s exact thresholds, and use ESA WorldCover (10 m
global land cover) as a control that is produced uniformly and knows nothing
about state borders. Three claims: **(1)** OSM's polygon layers are not uniform
across the six states — the OSM÷WorldCover completeness ratio for green runs 0.24
in Maine to 0.80 in Rhode Island, a 3.3× spread, and 38% of the composite's
weight rides on such inputs, so the `docs/new-england-rollout.md` Phase 4
region-wide re-fit is unsafe; **(2)** the "no mapped polygon" population
(26.3% of Massachusetts road-km) is under-mapped rather than featureless — 91.2%
of it has real tree cover and it is *slightly more* wooded than the credited
population; **(3)** Terrarium is adequate and 3DEP is closed at Spearman 0.97.
The recommendation is variant D, costed at roughly one new pipeline stage plus
four lines with no iOS change and no recalibration.

## The structure of the argument — attack these separately

This is the single most important thing to get right, and the easiest to get
wrong. **The doc rests on two independent arguments, and they have different
evidence standards:**

- **The uniformity argument (§1)** needs no ground truth at all. It is a
  statement about input data measured directly from the PBFs and the raster.
- **The accuracy argument (§3, §6)** rests on 76 driver marks and the doc itself
  concedes it is unprovable — separation SE is 0.080 and every variant from 0.694
  to 0.750 sits within one SE of every other.

The doc's position is that the decision should be made on the first and not the
second. **If you attack only the weak accuracy evidence and conclude "within
noise, therefore do nothing", you have not engaged the actual argument.** If you
think the uniformity argument does not in fact support the recommendation, say
so — that is the review this needs.

## Where the argument is genuinely weakest — start here

These are my own doubts, listed so you do not have to find them:

1. **Is the OSM÷WorldCover ratio a fair comparison?** The numerator is "% of
   road-km with a polygon within `DIST` of the chunk *line*" (`score.py:39-48`);
   the denominator is "% of chunk midpoints whose 90 m box is ≥10% tree". Those
   are different measurement operations. The claim is only that the ratio's *bias
   is state-invariant*, so cross-state comparison survives. Is that true? A chunk
   is up to 400 m long, so midpoint sampling covers far less ground than a
   buffered line — and if road geometry differs systematically by state (Maine's
   roads are longer and straighter), the bias may not cancel.
2. **Is `≥10% tree in a 90 m box` too low a bar?** It is true on 82–91% of
   road-km everywhere, and the doc reads that near-uniformity as "the landscape is
   similar". It could instead be an artifact of a threshold nearly everything
   clears. The doc's counter is that median tree *fraction* does vary sensibly
   (RI 0.42 → ME 0.83). Is that counter sufficient?
3. **n=6 states.** The cross-state Spearman of −0.60 has p=0.21. The doc says so
   and leans on the per-state ratio column instead. Does that column carry the
   weight placed on it?
4. **Variant D's weights are arbitrary.** It splits `c_green`'s 0.18 into
   0.09/0.09 with no justification beyond "half". Why is that the right split?
   Would 0.12/0.06 or 0.06/0.12 change the conclusion? This is unexamined.
5. **WorldCover is a 2021 snapshot against a 2026 road network.** Un-assessed in
   the doc. Forest and water move slowly; built-up does not.
6. **The "zero iOS changes" claim.** The doc argues `BEAUTY_TYPES`
   (`pipeline/router.py:164`) maps an api-name to a *column*, and the mirror
   tests (`tests/test_routing.py:36`, `:44`) assert names and labels but not
   columns — so a `BASELINE` entry needs no client work. **Verify this against
   the actual files.** If it is wrong, the cost estimate is wrong.
7. **The "no recalibration needed" claim.** Checked only against p50/p99 and the
   pinned-at-0/10 shares. `score.py`'s `calibration_report()` asserts more than
   that. Does D survive the whole report?

## Traps — these will cost you a day

**1. `graph_edges.parquet` and `scored_chunks.parquet` give different answers,
and both are right.** The original review's headline is "22.6% / 14,979 km" on
graph *edges*; the findings work chunk-level and report **26.3–27.0%**.
`graph.py` averages components along an edge, so an edge spanning one credited
and one uncredited chunk is no longer all-zero. **This is not a discrepancy and
is not evidence of an error.** Both were reproduced exactly (see §0 of the
findings). If you compare 26.3% against 22.6% you will "find" a bug that is a
unit mismatch.

**2. The findings' separation numbers are NOT comparable to the review's.** The
review quotes `c_curves` at 0.81 and the composite at 0.71, from
`analyze_trace.py`'s 400 m along-route window. The findings snap each mark to its
nearest graph edge instead and get 0.705 and 0.694. **This is a different
estimator, not a contradiction** — the doc says so in §3, but it looks like one
at a glance. Only comparisons *within* one table are valid. If you want to attack
this, the real question is whether the point-snap estimator biases the *ordering*
of signals, not whether the absolute numbers differ.

**3. Do not touch anything in `pipeline/`.** Two other pieces of work are queued
on those exact files — a latitude-banding fix in `elevation.py`, and a
byway-relation change to `extract.py` + `score.py`. This review produces a
document. Editing a pipeline file here is a merge conflict, not progress.

**4. WorldCover tiles are named for their SOUTH-WEST corner** — the tile index is
`floor(lon/3)*3`, **not `ceil`**. With `ceil` every point resolves to the tile 3°
east, lands outside its bounds, gets clipped to an edge column, and returns
*plausible-looking garbage* rather than raising. This bug produced a wrong version
of the §1 ratio table that read as merely surprising. If you re-run any sampling,
keep a bounds assertion.

**5. Verify retrievability, not existence.** If you propose any source the
findings missed: download it, and use a deliberately bogus URL on the same host
as a control. `highways.dot.gov` returns **403 on its own root**, so a 403 there
means nothing. `s3-us-west-2.amazonaws.com/mrlc` returns 403 for real and bogus
keys alike. State which of your claims come from a retrieved file.

**6. Already ruled out — do not re-survey.** Massachusetts-only datasets
including MassGIS (out of bounds by the original brief, except as a yardstick);
traffic and congestion data (`docs/traffic-schedule-plan.md`); the OSM byway
route relations carrying `c_scenic_tag` (that is `new-england-rollout.md` Phase
0b and belongs to someone else); Google Dynamic World (excluded by the README's
"no Google/Apple data" product claim).

## What is explicitly not yours to decide

The findings end (§7.4) by making the recommendation **conditional on whether the
New England rollout is actually happening** — strong if it is, weak if Scenic
stays Massachusetts-only. That is the product owner's call, not yours. Assess
whether the conditional is correctly drawn; do not resolve it.

## Done looks like

A new document, `docs/geodata-peer-review-verdict.md`:

1. **Your verdict on variant D** — make it, don't make it, or make it only under
   stated conditions. Lead with it.
2. **Which specific claims you tested, and how.** Separate "I re-ran this and got
   X" from "I read this and reasoned about it". Say which numbers you reproduced.
3. **Any claim you broke**, with the evidence. This is the highest-value section.
4. **Any claim you could not check**, named as such, with what it would take.
5. **Whether the two arguments (uniformity, accuracy) are correctly separated**
   and whether the recommendation follows from the one the doc rests it on.
6. **An honest-answer escape hatch is fine**: "the argument holds and I could not
   break it" is a legitimate verdict, as is "this cannot be settled without the
   prototype in §8.1". Do not manufacture disagreement to look rigorous.

## Environment

- Branch from **`claude/stoic-goldstine-d6b9d5`**, not from `main` — the
  documents under review exist only on that branch. Never commit to `main`.
  Commit your verdict doc on your branch.
- `data/`, `.venv/` and `traces/` live **only in the main checkout**, never in a
  worktree. Run any analysis from the main checkout.
- The project path contains spaces, so venv console scripts are broken. Use
  `.venv/bin/python -m pip`, never `.venv/bin/pip`.
- `data/raw/` already holds the merged 782 MB New England PBF and all six state
  extracts — per-state coverage is measurable without downloading anything.
- Tests: `SCENIC_DATA=data/processed .venv/bin/python -m pytest tests/` — 294
  pass, 0 skipped, ~2 min. You should not need to change any test.
- **Disk is at ~99%, ~6.5 GiB free.** Do not download a large dataset. The eight
  New England WorldCover tiles are 343 MB total if you need them; they are COGs
  with range requests, so windowed `/vsicurl` reads need no download at all.
- Reading the whole New England PBF with pyosmium takes minutes and ~5 GB RAM.
  Sample per-state files instead.
