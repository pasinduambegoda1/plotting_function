# Transient Response Plotting Tool

Overlays three transient (step-response) CSV logs on one plot, corrects for
a time axis that's on a different scale in one of the files, marks a ±5%
margin band that tracks the reference plot's own curve from its first
sample to its last (not a flat line at the rated value), and computes +
annotates rise time and settling time for every curve.

> **Note:** settling time currently uses a placeholder definition (see
> "How it works" below) until you provide the exact definition you want —
> the plot labels it "provisional" for that reason.

## Input format

Each CSV needs a time column and a value column. Column names are
auto-detected (anything containing "time"/"t"/"sec" for time; "value",
"signal", "response", "output", "amplitude" for the value — otherwise it
falls back to the first two columns).

## Quick start

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

## Using your own files

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

## How it works

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

- `transient_plot.py` — the tool (CLI + importable functions).
- `generate_demo_data.py` — generates `demo_data/reference.csv`,
  `demo_data/plant_A.csv` (deliberately time-compressed 2x to demonstrate
  alignment), and `demo_data/plant_B.csv` (faster, slightly underdamped
  response with a different rated value) so you can try the tool before
  pointing it at your real data.
