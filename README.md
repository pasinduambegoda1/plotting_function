# Transient Plotting Tools

Two related tools:

1. **`power_test_plot.py`** — the primary tool going forward. Compares
   Active Power / Reactive Power / Voltage at the POC across PSSE, PSCAD,
   and real plant test CSVs for one grid-compliance test case.
2. **`transient_plot.py`** — a generic single-channel version (one value
   column per file) used while the approach was being worked out. Still
   useful for anything that isn't a P/Q/V power-system test.

---

## `power_test_plot.py` — PSSE / PSCAD / Test comparison

For one test case, all sources run in some steady state, a transition is
applied at 10 s, and the transient is observed for another 20 s (30 s
total):

- **PSSE** — phasor-domain simulation export. Numeric time column in
  seconds; P/Q/V columns auto-detected as whichever column ends in
  `POC P` / `POC Q` / `POC V` (space/underscore/case-insensitive), e.g.
  `STSF POC P`. Already in MW / MVar / pu.
- **PSCAD** — EMT-domain simulation export. Same auto-detection as PSSE
  by default; override with `--pscad-*-col` if a project's naming
  differs.
- **Test** — real plant measurement. Time column `Time` holding an ISO
  8601 timestamp (e.g. `2025-7-8T4:58:49.994640953Z`); value columns
  `Test P` (W), `Test Q` (Var), `Test V` (V, line-to-line) — converted to
  MW / MVar / pu.

The Test file is logged from a wall-clock timestamp at a higher, less
exact rate than the simulations, usually runs longer than 30 s, and its
transition does not land on 10 s on its own. This tool detects the moment
each source's transition actually starts and **shifts** (an offset, not a
rescale — this is a clock-offset problem, not a sample-rate problem) that
source's whole time axis so the transition lands exactly on 10 s, then
trims every source to the 0-30 s analysis window.

### Quick start

```bash
pip install -r requirements.txt

python3 power_test_plot.py \
  --test STSF1_HP3_SFPFT_01.csv \
  --psse PSSE_STSF1_HP3_SFPFT_01.csv \
  --rated-p 202 --rated-q 79.86 \
  --reference psse \
  --margins 5 \
  --outdir out/
```

This produces three plots — `out/STSF1_HP3_SFPFT_01_P.png`,
`..._Q.png`, `..._V.png` — each overlaying every source supplied, with a
margin band tracking the reference source's own curve, and rise time /
settling time annotated for every curve. It also prints the same numbers
to the console.

### Options

- `--test`, `--psse`, `--pscad` — any subset of these three file paths
  (at least one required).
- `--rated-p` (MW), `--rated-q` (MVar), `--rated-v` (pu, default `1.0`
  since voltage is expressed per-unit) — nameplate/rated values, used
  only for margin-band *width* (`margin% of rated`), not as a target the
  curve is expected to reach.
- `--v-base` — voltage base in volts (default `330000`), used only to
  convert the Test file's raw-volts `Test V` column to pu.
- `--margins 5` — margin band(s) as % of rated, drawn as an envelope
  tracking the reference curve from its first sample to its last. Pass
  several values (e.g. `--margins 2 5 10`) for nested bands.
- `--transition-time 10`, `--window 0 30` — where the transition should
  land after alignment, and the analysis window kept after trimming.
- `--reference {test,psse,pscad}` — force the reference source. Default
  priority is PSCAD > PSSE > Test, whichever sources were supplied.
- `--psse-p-col` / `-q-col` / `-v-col` / `-time-col`, and the same for
  `--pscad-*`, override auto-detected column names for a project whose
  channel naming differs (e.g. a different plant prefix than `STSF`).
- `--test-time-col` (default `Time`), `--test-p-col` (`Test P`),
  `--test-q-col` (`Test Q`), `--test-v-col` (`Test V`), `--test-p-divisor`
  / `--test-q-divisor` (default `1e6`, W→MW and Var→MVar) — override if a
  future Test export uses different column names or units.
- `--outdir`, `--case-name` — where to write the three plots and what to
  name them (default: derived from the input filenames, stripping a
  leading `PSSE_`/`PSCAD_` prefix).

### How it works

**Trigger channel** — for each source, whichever of P/Q/V shows the
largest change relative to its rated value is used to detect *when* that
source's transition happens (e.g. a Q-step test triggers off Q; P and V
barely move and are ignored for alignment purposes, though they're still
plotted and analyzed).

**Alignment** — the trigger channel's onset (first departure from its own
pre-transition baseline by 5% of its own step size, held for several
samples to reject noise) is found, and the source's time axis is shifted
by a constant so that onset lands exactly on `--transition-time`. Every
channel in that source shares the one shift (it's one clock, not three).

**Rise time** — 10%–90% of that channel's own (final − baseline) step,
using each curve's own pre/post-transition values rather than assuming a
step from 0 → rated. If a channel doesn't actually move by at least 2% of
its rated value (e.g. P during a pure Q-step test), rise time is reported
as `N/A` rather than a number driven by noise.

**Margin band / settling time (provisional)** — same design as
`transient_plot.py` below: the band tracks the reference channel's own
curve from start to end; a non-reference curve's settling time is judged
against the reference's *dynamic* curve at each instant; the reference
itself is judged against its own settled (final) value, since nameplate
capacity (rated) is only the margin's *width*, not a value every channel
is expected to approach. Settling time is still a placeholder pending
your exact definition.

**Reference availability** — if the reference source's recording ends
before 30 s (e.g. a PSSE export that only covers 20 s), the margin band
and reference curve simply stop there; other sources still plot to the
full window.

---

## `transient_plot.py` — generic single-channel comparison

Overlays three transient (step-response) CSV logs on one plot, corrects for
a time axis that's on a different scale in one of the files, marks a ±5%
margin band that tracks the reference plot's own curve from its first
sample to its last (not a flat line at the rated value), and computes +
annotates rise time and settling time for every curve.

> **Note:** settling time currently uses a placeholder definition (see
> "How it works" below) until you provide the exact definition you want —
> the plot labels it "provisional" for that reason.

### Input format

Each CSV needs a time column and a value column. Column names are
auto-detected (anything containing "time"/"t"/"sec" for time; "value",
"signal", "response", "output", "amplitude" for the value — otherwise it
falls back to the first two columns).

### Quick start

```bash
pip install -r requirements.txt   # pandas, numpy, matplotlib

# generate example data (3 synthetic CSVs under demo_data/)
python3 generate_demo_data.py

python3 transient_plot.py \
  --files demo_data/reference.csv demo_data/plant_A.csv demo_data/plant_B.csv \
  --rated 40 40 42 \
  --margins 5 \
  --transition-time 10 \
  --output demo_output.png
```

This prints rise time / settling time per file to the console and saves an
annotated PNG (`demo_output.png`) with all three curves, the margin bands,
and the numeric results printed directly on the plot.

### Using your own files

```bash
python3 transient_plot.py \
  --files fileA.csv fileB.csv fileC.csv \
  --rated 40 40 40 \
  --margins 5 \
  --output my_plot.png \
  --show                 # also pop up an interactive window
```

Options:

- `--files F1 F2 F3` — the three CSVs (any order).
- `--rated R1 R2 R3` — rated (target) value for each file, same order as `--files`.
- `--margins 5` — margin band(s), as % of the **reference's** rated value,
  drawn as an envelope that tracks the reference curve itself from its
  first sample to its last (default: a single ±5% band). Pass more than
  one value (e.g. `--margins 2 5 10`) to draw several nested bands.
- `--transition-time 10` — the time (s) at which the step should occur
  (default 10, per the "40 units at 10s" scenario).
- `--reference` — force which file is the reference: a filename, its stem,
  or an index (0/1/2). If omitted, the tool auto-picks the file whose name
  contains `reference`, `ref`, `master`, `golden`, or `baseline`
  (case-insensitive); otherwise it defaults to the first `--files` entry.
- `--labels` — custom legend labels (defaults to each file's stem).
- `--output` — output image path (default `transient_plot.png`).
- `--show` — also open an interactive matplotlib window.

It can also be used as a library:

```python
from transient_plot import analyze_and_plot

results, ref_idx, fig, ax = analyze_and_plot(
    files=["fileA.csv", "fileB.csv", "fileC.csv"],
    rated_values=[40, 40, 40],
    margins_pct=[5],
)
```

### How it works

**Reference selection** — auto-picked by filename keyword, or forced with
`--reference`. The reference's rated value defines the margin bands drawn
on the plot, and its rated value is the common target used to judge when
every curve (including the reference itself) has "settled" — per the
requirement that settling is judged relative to the reference plot, not
each file's own steady-state value.

**Time-axis alignment** — for each file, the tool finds the step *onset*:
the point where the signal first departs from its pre-transition baseline
by 5% of its rated value (held for several samples to ignore noise spikes).
It then rescales that file's whole time axis (`time' = time * scale`) so
the onset lands exactly on `--transition-time` (10s by default). A low
(5%) threshold is used deliberately — it marks *when the step begins*,
which is the same instant for every curve no matter how fast each one
subsequently rises, so a curve's own response speed can't be mistaken for
a genuine time-base problem. If a file's onset is already within 3% of the
target time, its scale is left at exactly 1.0 (untouched) rather than
being nudged for no reason. Only a file whose time column is genuinely on
a different scale (e.g. logged in different units) gets corrected — this
is what fixes the "rise position looks different" issue when one file's
clock doesn't match the others'.

**Rise time** — classic 10%–90% of that file's own rated value, using
linear interpolation between samples for sub-sample precision.

**Margin band** — for each margin, an envelope `reference.value(t) ±
margin% of the reference's rated value` is computed at every one of the
reference curve's own time samples (start to end) and drawn as a shaded
region plus dashed boundary lines that follow the reference curve's
shape — flat during the pre-transition baseline, rising through the
transient, flat again once settled. This is what makes the margin
meaningful during the rise, not just once the curve has leveled off: a
non-reference curve that departs from the reference's trajectory by more
than the margin becomes visible wherever that happens, not only at
steady state.

**Settling time (provisional)** — computed per margin. For a
non-reference curve, "settled" means it stays within the margin band of
the *reference curve's value at that same instant* continuously through
the end of the data (i.e. matches the shaded envelope above). The
reference curve itself has nothing external to compare against, so it is
judged against its own flat rated-value line instead. If a curve exits
and re-enters the band near the end (noise, oscillation), the reported
settling time is the point after its *last* excursion. `N/A` means it
never stays inside the band for the rest of the recorded window. This is
a stand-in — swap in your real definition in `settling_time()` in
`transient_plot.py` once you've specified it, and the "(provisional)"
labels/footnote can come out.

**Plot annotations** — each curve gets triangle markers at its 10%/90%
rise points and a marker at its settling point; a text box in the corner
lists every computed number (rated value, time scale applied, rise time,
settling time, steady-state value) per file.

## Files

- `power_test_plot.py` — PSSE/PSCAD/Test comparison tool (primary).
- `transient_plot.py` — generic single-channel comparison tool.
- `generate_demo_data.py` / `demo_data/` / `demo_output.png` — synthetic
  example for `transient_plot.py`.

Real test CSVs and their generated plots are not committed (see
`.gitignore`) since they're typically confidential plant/test results —
keep them in a local, un-tracked folder (e.g. `real_test_data/`).
