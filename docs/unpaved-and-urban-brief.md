# Vermont has the prettiest roads in New England and the worst scores

**Status: diagnosed and measured 2026-08-29, nothing changed.** No source file
touched. `UNPAVED_ADJ` is still -0.25, `WEIGHTS["urban"]` still 0.14. The
measurements below are all from the shipped New England build
(`data/processed-ne`, 940,420 chunks, 236,196 km, attributed to states by
`way_id` against the six Geofabrik extracts).

## The symptom

Length-weighted mean score per state, on the build now serving live:

| | road-km | median | mean | p90 | % ≥7 |
|---|---|---|---|---|---|
| RI | 10,559 | 5.18 | **5.14** | 7.61 | 16.0% |
| NH | 30,900 | 4.80 | 4.81 | 7.36 | 13.1% |
| CT | 39,108 | 4.73 | 4.75 | 7.16 | 11.3% |
| MA | 67,533 | 4.53 | 4.58 | 7.03 | 10.2% |
| VT | 28,392 | 4.23 | 4.38 | **7.44** | 13.2% |
| ME | 59,705 | 4.14 | **4.31** | 6.86 | 8.7% |

Rhode Island beats Vermont. Note Vermont's p90 of 7.44 — second highest in the
region. Its best roads are excellent and its median is sixth of six.

## The mechanism, and it is not what it looks like

**Vermont has the highest raw beauty in New England.** Decomposing the blend
into `weight x length-weighted mean` per component:

| component | wt | RI | NH | CT | MA | VT | ME | spread |
|---|---|---|---|---|---|---|---|---|
| `c_relief` | 0.16 | 0.042 | 0.077 | 0.067 | 0.050 | **0.108** | 0.060 | 0.066 |
| `c_urban` | 0.14 | 0.050 | 0.026 | 0.033 | 0.039 | 0.025 | 0.010 | 0.040 |
| `c_coast` | 0.13 | 0.031 | 0.000 | 0.007 | 0.011 | 0.000 | 0.013 | 0.031 |
| `c_forest` | 0.18 | 0.109 | 0.097 | 0.093 | 0.088 | 0.094 | 0.085 | 0.025 |
| `c_curves` | 0.13 | 0.052 | 0.069 | 0.061 | 0.059 | 0.067 | 0.056 | 0.017 |
| `c_water` | 0.22 | 0.049 | 0.061 | 0.044 | 0.049 | 0.054 | 0.052 | 0.016 |
| **`score_adj`** | — | -0.065 | -0.093 | -0.070 | -0.076 | **-0.160** | -0.077 | **0.095** |
| **RAW TOTAL** | | 0.339 | 0.334 | 0.309 | 0.299 | **0.355** | 0.278 | |

**Vermont's raw total, 0.355, is the highest of the six.** It finishes last
because `score_adj` takes -0.160 off it, more than double every other state.
And `score_adj`'s spread (0.095) is larger than any single component's.

`score_adj` is `CLASS_ADJ + UNPAVED_ADJ` (`score.py:290`). Split:

| | road-km | unpaved km | % unpaved | class penalty | unpaved penalty |
|---|---|---|---|---|---|
| RI | 10,559 | 132 | 1.3% | -0.062 | -0.003 |
| NH | 30,900 | 5,165 | 16.7% | -0.051 | -0.042 |
| CT | 39,108 | 308 | 0.8% | -0.068 | -0.002 |
| MA | 67,533 | 4,055 | 6.0% | -0.061 | -0.015 |
| **VT** | 28,392 | **12,796** | **45.1%** | -0.047 | **-0.113** |
| ME | 59,705 | 7,655 | 12.8% | -0.045 | -0.032 |

**45% of Vermont's road network is unpaved**, against Massachusetts' 6% — where
`UNPAVED_ADJ = -0.25` (`score.py:86`) was calibrated.

### The penalty is not tracking ugliness

Vermont's unpaved chunks, measured against Vermont as a whole:

```
40,104 chunks / 12,796 km
mean raw beauty   0.358   (all Vermont: 0.355)
mean final score  3.21    (all Vermont: 4.38)
```

**The raw beauty of a Vermont dirt road is indistinguishable from a Vermont
paved road** — 0.358 against 0.355. Every component the model measures says they
are equally scenic. A flat -0.25 (2.5 points of 10) then drops them to 3.21.

### And it is applied asymmetrically, for the same reason `c_green` was

The penalty can only fire on a road someone tagged. Surface tagging coverage:

| | tagged | paved | unpaved | **untagged** |
|---|---|---|---|---|
| **VT** | 90.1% | 45.0% | 45.1% | **9.9%** |
| NH | 73.5% | 56.8% | 16.7% | 26.5% |
| MA | 46.8% | 40.8% | 6.0% | 53.2% |
| RI | 44.8% | 43.5% | 1.3% | 55.2% |
| CT | 37.8% | 37.0% | 0.8% | 62.2% |
| **ME** | 35.9% | 23.1% | 12.8% | **64.1%** |

Vermont's mappers tag surface on 90% of roads. Maine's on 36%. So Vermont's
dirt roads are nearly all found and penalised, while most of Maine's are
untagged and escape. **This penalty measures mapping diligence as much as road
surface** — structurally the same defect as `c_green` measuring land
designation, which `docs/geodata-sources-findings.md` established and
`c_forest` half-fixed. Vermont is being punished for being well mapped.

## The secondary finding: `c_urban` is real but is not the cause

`c_urban` (weight 0.14) is separately measured to point the *wrong way* — 0.35
separation on the drive marks, where 0.5 is a coin — and it has the second
largest spread in the table (0.040, RI 0.050 against ME 0.010).

It was the first hypothesis and **it is not sufficient.** Re-scoring the whole
region with the urban weight neutralised:

| variant | RI | NH | CT | MA | VT | ME | ranking |
|---|---|---|---|---|---|---|---|
| shipped (0.14) | 5.14 | 4.81 | 4.75 | 4.58 | 4.38 | 4.31 | RI > NH > CT > MA > VT > ME |
| halved (0.07) | 4.84 | 4.66 | 4.55 | 4.35 | 4.24 | 4.25 | RI > NH > CT > MA > ME > VT |
| removed (0.00) | 4.55 | 4.51 | 4.36 | 4.12 | 4.09 | 4.19 | RI > NH > CT > **ME** > MA > VT |
| inverted (-0.14) | 3.98 | 4.20 | 3.99 | 3.68 | 3.80 | 4.06 | NH > ME > CT > RI > VT > MA |

Removing it entirely lifts Maine two places and leaves **Rhode Island first and
Vermont last**. So it is worth fixing on its own evidence, but it is not what
puts Vermont at the bottom.

## Why this matters

The product's thesis is that it finds you a beautiful drive. A Vermont dirt road
through the Green Mountains is close to the ideal case, and the score is
currently docking it 2.5 points of 10 for its surface while its own beauty
measurement says it is exactly as pretty as the pavement next to it. The router
minimises `km x (1 - score/10)`, so that penalty directly steers routes away.

**Hypothesis, flagged as one:** the penalty is defensible as a *driver
preference* (a low car, bad weather, a hire agreement) and indefensible as a
statement about scenery. What would kill it: drive marks on unpaved Vermont road
showing the driver actually dislikes them.

## Traps

- **Do not simply delete `UNPAVED_ADJ`.** Some drivers genuinely do not want
  dirt roads, and the constant encodes a real preference. The question is
  whether it belongs in a *scenery* score at all, or whether it is a
  user-tunable avoidance like the beauty sliders. Both are defensible; measure
  both, recommend, do not unilaterally ship the deletion.
- **Do not fit anything to the 79 drive marks.** They are one driver, one day,
  eastern Massachusetts — a region that is 6% unpaved and 53% untagged. They
  physically cannot see this defect, and a re-fit against them would launder it.
  This is the single most likely way to waste this task.
- **Do not conflate "slow" with "ugly".** Unpaved roads are genuinely slower,
  and that is `SPEED_FACTOR`'s job in `router.py`, not the beauty score's. If
  the argument for the penalty is travel time, the fix belongs in a different
  file — and note `SPEED_KMH`/`SPEED_FACTOR` currently make no surface
  distinction at all, which is a separate finding worth reporting.
- **Changing the composite forces a `BETA` / `PREF_CURVE` re-sweep.**
  `router.py:60-73` records that the pair was co-fitted against the current
  score scale, and `router.py` calls `composite()` live on every request — so
  `score_adj` is inside the cost function, not a display transform. Any change
  moves which route comes back.
- **It also forces a rebuild and a 364 MB redeploy.** `data/processed-ne` is
  serving live traffic from a Windows laptop behind a Cloudflare tunnel. Build
  into a scratch directory; do not write to `data/processed` or
  `data/processed-ne`.
- **Disk is at 99% with ~4.7 GB free.** A full New England score rebuild is
  ~250 MB and takes about 11 minutes; the graph another ~200 MB and 6 minutes.
  Delete the scratch build when done. `data/processed_backup/` is 260 MB of a
  June build no current `Router` can load, if you need room.
- **A Massachusetts-only test will show almost nothing.** MA is 6% unpaved. Any
  measurement of this has to be region-wide.

## Done looks like

1. A measured option table for `UNPAVED_ADJ` — at minimum the shipped -0.25,
   a reduced value, zero, and "moved out of the score into a user-tunable
   avoidance" — each with the per-state ranking it produces and its effect on
   real routes, not just on the score distribution.
2. A recommendation on whether surface belongs in the scenery score at all,
   with the argument stated plainly enough to disagree with.
3. The tagging asymmetry addressed: any proposal must say what it does about
   Maine's 64% untagged roads, since a penalty that only fires where mappers
   were diligent is unfair whatever its magnitude.
4. `c_urban` measured separately — its own option table, and an honest statement
   of whether the 0.35 separation from 76 eastern-Massachusetts marks is enough
   to act on.
5. A statement of what cannot be settled without drive marks on northern roads,
   named specifically enough to go and collect.
6. `.venv/bin/python -m pytest tests/` green against whatever build you make —
   currently 294 pass, 0 skipped.
