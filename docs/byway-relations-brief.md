# Read scenic byways from OSM relations instead of three hardcoded names

**Status: built and measured 2026-08-29.** The diagnosis below stands and its
measurements reproduced exactly. Three of its inferences did not — see
[What the build found](#what-the-build-found) at the end, which is the part to
read if you only read one section. The original design is
`docs/new-england-rollout.md` Phase 0b, written 2026-08-26.

## The goal

`pipeline/score.py:91` credits scenic byways by matching three road names:

```python
BYWAY_NAMES = ["mohawk trail", "jacob's ladder trail", "jacobs ladder trail"]
```

with a comment promising the names are "distinctive enough to avoid false
positives statewide". Replace this with the byway route relations OSM already
carries, and delete the list.

## Measured, and why it matters more than 0.07 suggests

**The promise fails outside Massachusetts.** Drivable ways named "Mohawk Trail":
**14 in Connecticut, 4 in Rhode Island, 2 in New Hampshire.** Ordinary suburban
streets, each collecting the full `scenic_tag` weight the moment the region
widens. That is 20 false positives waiting for the New England build.

**And the real data is sitting unused.** Scanning the merged New England PBF for
`type=route` relations mentioning scenic or byway: **54 relations across 13
networks, 53 distinct named routes, 5,550 drivable member ways, 4,026 km —
1.71% of the network.** `US:MA:Scenic` 14, `US:VT:byway` 9, plus Maine, Rhode
Island, Connecticut, New Hampshire and the multi-state Connecticut River Byway.
Kancamagus, Acadia All-American Road, Green Mountain Byway, Molly Stark,
Route 100, Rangeley Lakes, Old Canada Road, Mount Greylock.

**The tag `extract.py` currently reads is effectively dead data.** `scenic=yes`
appears on **28 ways across all six states** (MA 0, VT 0, RI 0, NH 3, ME 3,
CT 22).

**Measured 2026-08-29 on the shipped Massachusetts graph:** weighted by
`WEIGHTS` and by road length, `c_scenic_tag` carries **~0% of the raw blend**,
and covers **0.1%** of network km at >= 0.5. It is the only component
contributing nothing. So this is not a 0.07-weight tweak at the margin — it is
switching on a component that is currently off.

## The mechanism

- `pipeline/extract.py` — `Handler` has `node()` and `way()` but no
  `relation()`. Route relations are never read.
- `pipeline/extract.py:63` and around — `roads.scenic` comes from the way tag
  alone.
- `pipeline/score.py:91` — `BYWAY_NAMES`, the name-substring fallback.
- `pipeline/score.py:264-265` —
  `is_byway = name_l.apply(lambda s: any(b in s for b in BYWAY_NAMES))`, then
  `chunks["c_scenic_tag"] = (chunks["scenic"] | is_byway).astype(float)`.

## The design (from rollout Phase 0b)

1. Add a `relation()` method to `extract.py`'s `Handler`, collecting member way
   ids for relations whose `network` is in an allowlist. Relations sort last in
   a PBF, so the set is complete before `main()` builds the frame, and the set
   is idempotent across pyosmium's two passes.
2. `roads.scenic` becomes `scenic=yes OR way_id in byway_ways`.
   `roads.parquet` already carries `way_id`, so the join is free.
3. Then delete `BYWAY_NAMES` and the `is_byway` term in `score.py`.

## Traps

- **Three of the 13 matched networks are walking and cycling routes** — `nwn`,
  `lwn`, `lcn` — which matched on the word "scenic". Admit them and footpath
  routes will flag roads. They must be excluded, and the remaining 12 relations
  with no `network` tag must be vetted by name. **The 4,026 km figure is an
  upper bound that includes them.**
- **Confirm the Mohawk Trail is actually among the 14 `US:MA:Scenic` relations
  before deleting `BYWAY_NAMES`.** Jacob's Ladder is confirmed present; the
  Mohawk Trail is not. If it is missing, keep a name fallback for it *with a
  bounding-box guard*, or Massachusetts loses a benchmark that
  `tests/test_calibration.py` asserts on by name
  (`test_scenic_byways_beat_the_interstates`, and `score.py`'s own
  `calibration_report` prints "Mohawk Trail" as a benchmark row).
- **Do not rebuild over `data/processed`.** It is the live promoted build
  (rebuilt and validated 2026-08-29, 294 tests green, and copied to the serving
  box). Build into a scratch directory with the unchanged layers symlinked in —
  `docs/new-england-rollout.md` Phase 1 and the `data/lc_build` pattern both do
  this. Disk is at 99% with ~6 GiB free, so delete the scratch build when done.
- **This needs a full `extract.py` run, not `tools/build_access.py`.** The
  access layers are unaffected; `roads.parquet` is what changes, and it comes
  from the full pass.
- **It changes scores, so it changes routes.** After rebuilding, re-run
  `tools/analyze_trace.py <build> traces/*.ndjson` and check the separation on
  the 76 marks does not fall below the live build's **0.71 against a 0.63 null
  ceiling**. Byways are a precision signal, so it should rise. A change that
  improves coverage and regresses that number is not an improvement.
- **Do not touch `pipeline/elevation.py`.** Its `BBOX` now points at New England
  while `data/processed` is a Massachusetts build, so running it is a New
  England run — see `docs/new-england-rollout.md` Phase 2.
- **Do not re-open the `c_forest` work.** Land cover merged 2026-08-29 and
  `WEIGHTS["green"]` is now `WEIGHTS["forest"]`. Leave it alone.

## Done looks like

1. `extract.py` reads byway route relations behind a vetted network allowlist,
   with the allowlist and the reason for each exclusion written in a comment.
2. `roads.scenic` unions the relation membership with the `scenic=yes` tag.
3. `BYWAY_NAMES` and its `is_byway` term are gone from `score.py` — **or** a
   stated reason why a guarded fallback for the Mohawk Trail had to stay.
4. A per-state count of flagged byway km, so the New England build can be
   sanity-checked later.
5. A rebuild in a scratch directory showing `c_scenic_tag` coverage rising from
   0.1% of MA network km, the calibration benchmarks still ordered correctly,
   and mark separation at or above 0.71 against its printed null ceiling.
6. `.venv/bin/python -m pytest tests/` green — **or** a statement of which
   assertion had to change and why.

---

# What the build found

Built on branch `claude/byway-relations`. Everything below is measured, on the
2026-08-25 New England extract for the region figures and the **June**
Massachusetts extract for the build, so the OSM snapshot is held constant
against the live `data/processed`.

The headline counts above all reproduced: 54 candidate relations across 13
networks, 5,580 drivable member ways, 4,065 km (the brief said 5,550 / 4,026 —
the small gap is this pass applying `extract.py`'s own `PRIVATE_ACCESS` rule).
Three of the brief's *inferences* were wrong, and one trap it does not mention
is larger than the one it does.

## 1. The Mohawk Trail is in the relations. `BYWAY_NAMES` is gone.

The brief says "Jacob's Ladder is confirmed present; the Mohawk Trail is not",
and asks for a bounding-box-guarded name fallback if so. Not needed. Relation
**20020600 "Mohawk Trail Scenic Byway"** is one of the 14 `US:MA:Scenic`
relations, with 251 member ways in the June extract and 253 in the August one.
All 14 are present in both. `BYWAY_NAMES` and its `is_byway` term are deleted
outright, with no fallback.

## 2. Two of the three `BYWAY_NAMES` entries never matched anything

Against the live `roads.parquet`:

| pattern | drivable ways matched |
|---|---|
| `mohawk trail` | 146 |
| `jacob's ladder trail` | **0** |
| `jacobs ladder trail` | **0** |

OSM names that road **"Jacobs Ladder Road"**, not "…Trail". So the list only
ever credited the Mohawk Trail, and the Jacob's Ladder byway — a road the
README names as one of the two calibration benchmarks — has never once been
flagged by it. The relation now flags it: 13 ways, 12.5 km.

The same slip is in `calibration_report`, whose benchmark row selected
`name.str.contains("jacob")`. That matches 80 ways, 66 of them streets named
after people (Jacob Cobb Lane, Jacob Amsden Road, Jacob Gates Road…) and only
14 the byway. The row was reporting the mean score of suburban streets under
the label "Jacob's Ladder Trail". Selector fixed to `jacob'?s ladder`; the row
moves from 5.18 on 33 km of unrelated street to 6.56 on 13 km of actual byway.

## 3. The trap the brief missed is bigger than the one it flags

The brief warns that `nwn`, `lwn` and `lcn` are walking and cycling networks.
True, and measurable: those six relations road-walk **206 drivable ways, 91 km**
of ordinary road, 132 ways of it under the New England National Scenic Trail
alone. Admitting them would flag real roads.

But three more of the 13 matched networks are **general numbered-highway
systems**, each of which matched because a single member has a scenic-sounding
name or note:

| network | scenic-matching relations | relations in the network | member ways |
|---|---|---|---|
| `US:US` | 1 | 56 | 18,692 |
| `US:ME` | 1 | 187 | 7,523 |
| `US:RI` | 1 | 62 | 3,300 |

The brief's design — "relations whose `network` is in an allowlist", with the
allowlist implicitly the networks that matched — would have designated every US
and state highway in New England a scenic byway. That is ~29,500 member ways
against the ~4,500 wanted. The three candidates themselves do not survive
inspection either: both Rhode Island Route 1A relations hedge in their own tags
("sometimes signed with 'SCENIC' in the shield", "sometimes bannered as scenic,
and sometimes not"), and Maine SR 11 is merely *named* "Aroostock Scenic
Highway" — at 615 ways and 655 km the single largest candidate in the region —
while carrying no scenic designation tag.

## 4. The rule that works needs two clauses, not one

```
type=route  AND  route=road  AND  (network in BYWAY_NETWORKS  OR  scenic=yes)
```

Both halves of the `OR` are load-bearing, and this is not a stylistic point:

- **Not one of the 14 `US:MA:Scenic` relations carries `scenic=yes`** — nor
  does the Vermont half of the Connecticut River Byway. Massachusetts is
  enumerated purely by `network`, so dropping that clause takes the home state
  to zero. (All 9 `US:VT:byway` relations do carry the tag, so Vermont would
  survive either clause alone.)
- **New Hampshire, Maine, Rhode Island and Connecticut have no byway network at
  all.** Their byways — Kancamagus, Acadia All-American Road, Old Canada Road,
  Rangeley Lakes, Schoodic, Connecticut Route 169, White Mountain Trail — are
  network-less relations tagged `scenic=yes`. A network-only allowlist takes
  four of the six states to zero.

`route=road` is what excludes footpaths, and it does the job positively rather
than by blocklisting `nwn`/`lwn`/`lcn`: it also drops the three railway and
train routes ("Conway Branch", "Milford & Bennington Railroad", "Winnipesaukee
Railway") that the brief's network list would have admitted, since none of them
is a walking network.

Two candidates needed no decision at all: the Crawford House and Mount Willard
Carriage Roads are `route=road` with `scenic=yes`, but every member is a
footway, so they contribute **zero** drivable ways. Membership is unioned into
`roads.parquet`, which holds only drivable ways, so a relation made of
footpaths cannot flag anything.

`BYWAY_NETWORKS` is an explicit set rather than a pattern because of §3. It is
in `pipeline/extract.py` with the reason for each admission and each exclusion.

## 5. Per-state byway coverage (the sanity check for the NE build)

Vetted: **38 relations, 4,510 drivable member ways, 3,235 km** — not the 4,026
km headline, which was an upper bound including the walking, cycling, railway
and numbered-highway candidates.

| state | routes | drivable ways | km |
|---|---:|---:|---:|
| VT | 10 | 1,981 | 1,461.0 |
| MA | 14 | 1,487 | 737.7 |
| NH | 6 | 726 | 638.8 |
| ME | 4 | 231 | 282.7 |
| CT | 1 | 38 | 53.7 |
| NB (border overlap) | 2 | 44 | 52.4 |
| NY (border overlap) | 1 | 3 | 8.6 |
| **total** | **38** | **4,510** | **3,235** |

Rejected: 16 candidates, 1,070 drivable ways, 830 km — 739 km of it the
numbered-highway trap, 91 km the walking and cycling routes.

## 6. The Massachusetts rebuild

Into `data/byway_build`, June extract, with `relief.tif`, `elevation.tif`,
`tree_cover.parquet` and `traffic_control.parquet` symlinked from
`data/processed`. `elevation.py` was **not** run. `data/processed` untouched.

- `extract.py` **147 s**, peak RSS ~1.5 GB — the number Phase 1 was meant to
  produce, and the basis for predicting the New England run.
- Every extract layer row count identical to the live build, all eleven.
  `graph_edges` 400,983, `graph_nodes` 310,162, `turn_restrictions` 4,538 —
  all identical too.
- `scored_chunks` differs in **exactly four columns** — `scenic`,
  `c_scenic_tag`, `raw`, `score` — and nothing else. Geometry identical, all
  eight other components byte-identical, which is also the proof the symlinked
  relief was read and not regenerated.
- **`c_scenic_tag` coverage 0.1% → 1.1% of network km**, mean 0.001 → 0.009.
  Flagged ways 146 → 1,479. It is no longer the component contributing nothing.
- Gained on 2,680 chunks (696 km). **Lost on 133 chunks (36 km)**: ways named
  "Mohawk Trail" that the designated byway does not contain — 71 motorway and
  56 trunk of MA Route 2's expressway east of the byway, which OSM names
  "Mohawk Trail" far beyond the designation. Their mean score falls 3.58 → 2.76.
  That is the correction working, not damage.

Benchmarks, live → rebuilt:

| benchmark | km | live | rebuilt |
|---|---:|---:|---:|
| Greylock Notch/Rockwell | 31 | 6.62 | **7.21** |
| Jacob's Ladder Road | 13 | 5.73 | **6.56** |
| Route 6A (Old King's Highway) | 82 | 5.57 | **6.14** |
| Mohawk Trail (by name) | 81 | 5.32 | **4.96** |
| I-90 (Mass Pike) | 447 | 0.57 | 0.57 |
| I-95 | 292 | 0.50 | 0.50 |

Like for like: every row uses the *new* selectors against both builds, so the
Jacob's Ladder row is the byway in each column rather than the 80 streets the
old `contains("jacob")` row printed (which scored 5.18 over 33 km). Three
benchmarks rise because Mount Greylock, Jacob's Ladder and Old King's Highway
are all designated relations. The Mohawk Trail row falls, and should: it is
selected *by name*, and the 36 km of Route 2 expressway that the designation
excludes is still in the selection while no longer being paid for.

`test_scenic_byways_beat_the_interstates` needs byways above I-90 + 2.0 = 2.57.
Mohawk Trail lands at 4.96 and Greylock at 7.21, so the margin is 2.4 points
and no assertion had to change.

**`SCENIC_DATA=data/byway_build pytest`: 294 passed, 0 skipped.**

## 7. The drive traces cannot see this change

`tools/analyze_trace.py` on the rebuilt graph: **separation 0.73** on 79 marks
over 12 drives, against the printed 0.63 null ceiling. The gate was "not below
0.71", so it passes.

But it did not rise either, and the brief predicted it would ("byways are a
precision signal, so it should rise"). The live build scores **0.73 on the same
79 marks** — every statistic identical to two decimals across all three
windows. The reason is measurable: **0 of the 79 marks lie within 50 m of any
chunk whose score changed.** The recorded drives never touched a designated
byway, so this instrument is blind to the change rather than endorsing it. The
gate is met; nothing more should be claimed from it.

## 8. One judgement call worth knowing about

116 motorway ways are now flagged scenic, all from a single relation —
`Minuteman Highway` (`US:MA:Scenic`), which is MA Route 2's Concord Turnpike
expressway. That is OSM's record of the state designation, not an artefact of
the allowlist, and it is left in: `CLASS_ADJ["motorway"] = -0.45` costs a
motorway 4.5 points while the byway flag pays 0.84, so motorway remains the
worst-scoring class in the rebuilt data at a mean of **0.55**. The live build
already flagged 25 motorway ways this way through `BYWAY_NAMES`, so this is the
same phenomenon from a better source.
