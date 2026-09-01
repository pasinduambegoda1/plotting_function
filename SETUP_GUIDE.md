# Setup Guide

How to get this code running on your own computer, and how the
rise/settling-point detection actually works under the hood.

## 1. Prerequisites

- **Python 3.9+** (check with `python3 --version` / `python --version`)
- **git**
- `pip` (comes with Python)

No PSSE/PSCAD software needed — this only reads CSV exports, it doesn't
talk to those applications.

## 2. Clone the repo

```bash
git clone https://github.com/pasinduambegoda1/plotting_function.git
cd plotting_function
```

On Windows (PowerShell or cmd), the same command works as-is if `git` is
installed (e.g. via [git-scm.com](https://git-scm.com/)); `cd` into the
folder the same way.

## 3. Install dependencies

Only three packages are needed: `pandas`, `numpy`, `matplotlib`.

```bash
pip install -r requirements.txt
```

If you want to keep this isolated from other Python projects (recommended
but optional):

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 4. What's in the repo

| File | Purpose |
|---|---|
| `power_test_app.py` | **The batch application.** Point it at PSSE/PSCAD/Test root folders + a plant + a main test; it finds every matching sub-test and plots all of them. Run this day-to-day. |
| `power_test_plot.py` | The engine underneath — also runnable directly for a one-off comparison of a single PSSE/PSCAD/Test file trio, outside the folder convention. |
| `transient_plot.py` | An earlier, generic single-value-column tool, unrelated to the PSSE/PSCAD/Test P/Q/V workflow. Not needed for this. |
| `README.md` | Full option reference for all of the above. |

## 5. Folder structure your data needs to be in

For the **batch app** (`power_test_app.py`), you need three separate root
folders — one for PSSE exports, one for PSCAD exports (optional — omit
`--pscad-root` if you don't have any yet), one for Test (real plant)
exports. **All three use the same layout:**

```
<root>/<plant_name>/<main_test_name>/<sub_test_name>/...
```

- `plant_name` — e.g. `STSF1`
- `main_test_name` — e.g. `HP3` (a "hold point")
- `sub_test_name` — one of several similar runs under that main test,
  e.g. `SFPFT_01`, `SFPFT_02`

**PSSE and PSCAD**: put exactly **one CSV directly inside** each
sub-test folder — the usual wide simulation export, with every
measurement location's channels as columns (e.g. a column literally named
`STSF POC P`).

**Test**: each sub-test folder holds **one subfolder per measurement
location** — `POC`, `33B1`, `33B2`, `34INV` (only `POC` is actually
processed today; the others are wired up in the code but need their
PSSE/PSCAD column-naming convention filled in first — see
`LOCATION_CONFIG` near the top of `power_test_app.py`). Each location
folder holds one or more CSV files with columns `Time`, `Test P`,
`Test Q`, `Test V`.

### Concrete example

```
D:\GridTests\PSSE\
  STSF1\
    HP3\
      SFPFT_01\
        PSSE_STSF1_HP3_SFPFT_01.csv

D:\GridTests\TEST\
  STSF1\
    HP3\
      SFPFT_01\
        POC\
          STSF1_HP3_SFPFT_01.csv

D:\GridTests\RESULTS\        <- doesn't need to exist beforehand, created automatically
```

Running the batch app against this produces:

```
D:\GridTests\RESULTS\STSF1\HP3\SFPFT_01\POC\STSF1_HP3_SFPFT_01_P.png
D:\GridTests\RESULTS\STSF1\HP3\SFPFT_01\POC\STSF1_HP3_SFPFT_01_Q.png
D:\GridTests\RESULTS\STSF1\HP3\SFPFT_01\POC\STSF1_HP3_SFPFT_01_V.png
```

i.e. `<results_root>/<plant>/<main_test>/<sub_test>/<location>/`.

## 6. Running it

### Batch mode — the normal way to run this

```bash
python3 power_test_app.py \
  --psse-root "D:\GridTests\PSSE" \
  --test-root "D:\GridTests\TEST" \
  --plant STSF1 --main-test HP3 \
  --results-root "D:\GridTests\RESULTS" \
  --rated-p 202 --rated-q 79.86 --margins 5
```

(On Mac/Linux, use forward-slash paths instead, e.g.
`/Users/you/GridTests/PSSE`.)

Add `--pscad-root "D:\GridTests\PSCAD"` once you have real PSCAD exports
laid out the same way as PSSE — only sub-tests that exist under **all**
of the sources you supply get processed.

**Before running for real**, use `--dry-run` to sanity-check a folder
tree — it prints exactly what it *would* process without loading or
plotting anything:

```bash
python3 power_test_app.py --psse-root ... --test-root ... --plant STSF1 --main-test HP3 --results-root ... --dry-run
```

It also prints, on every run, which sub-tests were found in PSSE, which
in Test, which are common (and will actually run), and which were
skipped (present in only one source) — read that output before assuming
something silently failed.

Exit code is `0` if everything processed cleanly, `1` if there were any
errors (a missing `POC` folder, an unreadable CSV, etc.) — useful if you
ever wrap this in a script.

### Single-pair mode — for a one-off comparison

If you just want to compare one PSSE file + one Test file (skip the
folder convention entirely):

```bash
python3 power_test_plot.py \
  --psse PSSE_STSF1_HP3_SFPFT_01.csv \
  --test STSF1_HP3_SFPFT_01.csv \
  --rated-p 202 --rated-q 79.86 \
  --reference psse \
  --outdir out/
```

### Rated-value defaults

Both tools default to `--rated-p 202` (MW), `--rated-q 79.86` (MVar),
`--rated-v 1.0` (pu — voltage is per-unit, so nominal is always `1.0`
regardless of the actual kV), `--v-base 330000` (volts — only used to
convert the Test file's raw-volts `Test V` column to pu). Override any of
them per run if a different plant needs different values — these are
POC-specific numbers from the STSF1 plant, not universal constants.

## 7. How the "rising point" (transition onset) is detected

This is the core piece that makes the whole tool work, so here's exactly
what it does, using real numbers from `STSF1_HP3_SFPFT_01`.

### The problem

PSSE/PSCAD simulations are told to apply their step at exactly 10 s, so
their time column already starts near 0 and the step lands close to 10 s
by construction. The Test file has no such guarantee — its `Time` column
is a wall-clock timestamp from whenever the data logger happened to be
recording, e.g. starting 12+ seconds before the actual test event and
continuing 30+ seconds after. To compare the three curves, the tool has
to figure out **when the real transition happens** in each file's own
time base, and then shift that file so the transition lands on 10 s.

### Step 1 — baseline and final value

For a channel's raw values, the tool takes the mean of the **first 5%**
of samples as the `baseline` (steady-state before the transition) and the
mean of the **last 5%** of samples as the `final` value (steady-state
after it):

```python
def baseline_final(value, edge_fraction=0.05):
    n = max(1, int(edge_fraction * len(value)))
    return mean(value[:n]), mean(value[-n:])
```

For the real Test Q channel this gives `baseline ≈ 0.61 MVar`,
`final ≈ 15.78 MVar`.

### Step 2 — pick the "trigger channel"

A source has three channels (P, Q, V), but usually only *one* of them
actually steps in a given test (e.g. a reactive-power test moves Q while
P and V barely change). The tool computes, for each channel, how big its
step is *relative to that channel's rated value*:

```
relative_step = |final - baseline| / rated_channel
```

and uses whichever channel has the **largest relative step** as the
"trigger channel" for detecting the transition instant. This stops a
channel that's just sitting on noise from accidentally driving the
alignment.

### Step 3 — find the crossing point (the actual "rising point")

On the trigger channel, the tool computes a **low threshold** — just 5%
of the way from `baseline` toward `final`:

```
level = baseline + 0.05 * (final - baseline)
```

Then it walks forward through the samples looking for the **first** one
that crosses this level (rising, if `final > baseline`; falling, if
`final < baseline`), and — to reject a single noisy sample that happens
to poke across the threshold — requires the next ~1% of the file's total
sample count to *also* stay past it before accepting the crossing:

```python
def first_crossing_time(time, value, level, hold, rising):
    cond = (value >= level) if rising else (value <= level)
    for i in range(len(value)):
        if cond[i] and all(cond[i:i+hold]):
            # interpolate between sample i-1 and i for a precise time
            ...
```

Why a **low** (5%) threshold rather than, say, 50%? Because a low
threshold marks *when the step begins* — which is the same physical
instant for every source, no matter how fast or slow each one
subsequently responds. A higher threshold would be reached later by a
slower-responding source purely because it's slower, and the tool would
wrongly read that as "this file's clock is offset" when really it's just
a slower controller.

Once the qualifying sample is found, the tool **linearly interpolates**
between it and the previous sample to get a sub-sample-precision
crossing *time*, not just "whichever sample happened to be closest."

For the real Test Q channel, this lands at **t ≈ 12.571 s** measured from
the very first sample in that file (i.e. 12.571 s into a recording that
started well before the test event).

### Step 4 — shift (not rescale) the whole file

```
shift = transition_time - onset_time     # e.g. 10.0 - 12.571 = -2.571 s
new_time = old_time + shift
```

This is an **additive offset**, not a multiplicative rescale — deliberately.
The problem here is that the data logger's clock started at an arbitrary
point (an offset problem), not that its sample rate is wrong relative to
PSSE's (which would need rescaling). All three channels (P, Q, and V) in
that file get shifted by the *same* amount, since they share one time
column — there's one clock, not three.

For PSSE/PSCAD, the exact same detection runs too (using whichever
channel is that source's own trigger channel) — since their transition
is already close to 10 s by design, the resulting shift is normally tiny
(a few hundredths of a second, reflecting real control-loop delay rather
than a clock problem).

### Step 5 — trim to the analysis window

After shifting, everything outside the analysis window (`--window`,
default **0 s to 30 s**) is dropped. This is why the Test file — which
might span 50+ seconds of wall-clock recording — ends up plotted as a
clean 0-30 s curve alongside PSSE.

### Step 6 — rise time (a separate, later calculation)

Once the time axis is aligned, rise time is computed *per channel*, using
that channel's own aligned `baseline`/`final`, as the time between
crossing 10% and 90% of the step (again with linear interpolation, again
direction-aware for a step that goes down instead of up):

```
level_low  = baseline + 0.10 * (final - baseline)
level_high = baseline + 0.90 * (final - baseline)
rise_time  = time_of(level_high) - time_of(level_low)
```

If a channel's own step is smaller than 2% of its rated value (i.e. it
didn't really transition — e.g. P during a pure Q-step test), rise time
is reported as `N/A` rather than a number produced by noise randomly
crossing a very narrow band.

### Settling time — still a placeholder

Settling time is currently a stand-in definition (first time after which
a curve stays within the ±margin band of the reference continuously to
the end of the data) — every plot labels it "provisional" for this
reason. Swap in the exact definition once it's confirmed, in
`settling_time()` in `power_test_plot.py`.

## 8. Troubleshooting

- **"PSSE folder not found" / "Test folder not found"** — the
  `<root>/<plant>/<main_test>` path doesn't exist. Double check
  `--plant`/`--main-test` spelling matches the actual folder names
  exactly (case-sensitive on Mac/Linux).
- **A sub-test is listed as "skipped"** — it exists under only one of
  the sources you gave (e.g. PSSE has `SFPFT_02` but Test doesn't, or
  vice versa). This is expected, not a bug — the app only runs sub-tests
  common to every source supplied.
- **"expected Test folder not found: .../POC"** (an ERROR, not a skip) —
  the sub-test folder itself matched between PSSE and Test, but the
  `POC` subfolder is missing on the Test side. Check the Test sub-test
  folder actually has a `POC` subfolder with a CSV in it.
- **"Could not auto-detect a 'POC P' column"** — the PSSE/PSCAD CSV
  doesn't have a column ending in `POC P` (space/underscore-insensitive).
  Pass the real column name explicitly with `--psse-p-col` (or
  `--pscad-p-col`).
- **Rise time / settling time show `N/A`** — normal for a channel that
  doesn't actually transition in that test (see Step 6 above).
