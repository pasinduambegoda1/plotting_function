"""
Transient response plotting & analysis tool.

Takes three CSV files, each logging a transient (a value stepping from 0 up
towards a "rated" value at ~10 s), overlays them on one plot, time-aligns
them if their time axes are scaled differently, marks 2%/5%/10% margin
bands around the reference plot's rated value, and computes + annotates
rise time and settling time for every curve.

Usage (CLI):
    python transient_plot.py \
        --files reference.csv plant_A.csv plant_B.csv \
        --rated 40 40 40 \
        --margins 2 5 10 \
        --transition-time 10 \
        --output transient_plot.png

If --reference is omitted, the file whose name contains a reference-style
keyword ("reference", "ref", "master", "golden", "baseline") is auto
selected; otherwise the first file is used.

Can also be imported and used programmatically via `analyze_and_plot(...)`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REFERENCE_KEYWORDS = ("reference", "ref", "master", "golden", "baseline")


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def detect_columns(df: pd.DataFrame) -> tuple[str, str]:
    """Guess which column is time and which is the signal value."""
    cols_lower = {c.lower().strip(): c for c in df.columns}

    time_col = None
    for key, orig in cols_lower.items():
        if key in ("t", "s", "sec", "secs", "seconds") or "time" in key:
            time_col = orig
            break
    if time_col is None:
        time_col = df.columns[0]

    value_col = None
    for key, orig in cols_lower.items():
        if orig == time_col:
            continue
        if key in ("value", "signal", "y", "amplitude", "response", "output", "level"):
            value_col = orig
            break
        if "value" in key or "signal" in key or "response" in key:
            value_col = orig
            break
    if value_col is None:
        remaining = [c for c in df.columns if c != time_col]
        if not remaining:
            raise ValueError(f"Could not find a value column distinct from time column {time_col!r}")
        value_col = remaining[0]

    return time_col, value_col


def load_signal(path: str) -> tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(path)
    time_col, value_col = detect_columns(df)
    df = df[[time_col, value_col]].apply(pd.to_numeric, errors="coerce").dropna()
    df = df.sort_values(time_col)
    return df[time_col].to_numpy(dtype=float), df[value_col].to_numpy(dtype=float)


def detect_reference_index(files: list[str], explicit: Optional[str] = None) -> int:
    if explicit is not None:
        for i, f in enumerate(files):
            if f == explicit or Path(f).name == explicit or Path(f).stem == explicit:
                return i
        try:
            return int(explicit)
        except ValueError:
            raise ValueError(f"--reference {explicit!r} does not match any file and is not a valid index")

    for i, f in enumerate(files):
        stem = Path(f).stem.lower()
        if any(k in stem for k in REFERENCE_KEYWORDS):
            return i
    return 0


# --------------------------------------------------------------------------
# Signal analysis
# --------------------------------------------------------------------------

def first_crossing_time(
    time: np.ndarray, value: np.ndarray, level: float, hold: int = 1
) -> Optional[float]:
    """First (interpolated) time at which `value` rises through `level`.

    `hold` requires the next `hold` samples to also be at/above `level`,
    which filters out single-sample noise spikes crossing the threshold.
    """
    above = value >= level
    idx = None
    for i in range(len(value)):
        if above[i] and np.all(above[i : i + hold]):
            idx = i
            break
    if idx is None:
        return None
    if idx == 0:
        return float(time[0])
    t0, t1 = time[idx - 1], time[idx]
    v0, v1 = value[idx - 1], value[idx]
    if v1 == v0:
        return float(t1)
    frac = (level - v0) / (v1 - v0)
    return float(t0 + frac * (t1 - t0))


def align_time_axis(
    time: np.ndarray,
    value: np.ndarray,
    rated: float,
    nominal_transition_time: float,
    onset_frac: float = 0.05,
    snap_tolerance: float = 0.03,
) -> tuple[np.ndarray, float]:
    """Scale the time axis so the step *onset* lands on `nominal_transition_time`.

    The onset is detected as the point where the curve first departs from
    its pre-transition baseline by `onset_frac` of the rated value (default
    5%), held for a few consecutive samples to reject noise. A low
    threshold like this is deliberate: it tracks *when the step begins*,
    which is the same instant for every curve regardless of how fast each
    one subsequently rises. Anchoring on a higher threshold (e.g. 50%)
    would instead be biased by each curve's own response speed, and could
    "correct" a plot that never actually had a time-scale problem.

    If the detected onset is already within `snap_tolerance` (fractional)
    of the nominal time, the scale is snapped to exactly 1.0 so curves with
    a correct time base are left untouched.
    """
    n_baseline = max(1, int(0.05 * len(value)))
    baseline = float(np.mean(value[:n_baseline]))
    level = baseline + onset_frac * (rated - baseline)
    hold = max(1, int(0.01 * len(value)))
    t_detect = first_crossing_time(time, value, level, hold=hold)
    if not t_detect or t_detect <= 0:
        scale = 1.0
    else:
        scale = nominal_transition_time / t_detect
        if abs(scale - 1.0) <= snap_tolerance:
            scale = 1.0
    return time * scale, scale


def rise_time(time: np.ndarray, value: np.ndarray, rated: float, low: float = 0.1, high: float = 0.9) -> dict:
    t_low = first_crossing_time(time, value, low * rated)
    t_high = first_crossing_time(time, value, high * rated)
    rt = (t_high - t_low) if (t_low is not None and t_high is not None) else None
    return {"t_low": t_low, "t_high": t_high, "rise_time": rt, "low_frac": low, "high_frac": high}


def settling_time(time: np.ndarray, value: np.ndarray, target, band_abs: float) -> Optional[float]:
    """First time after which `value` stays within `band_abs` of `target` forever.

    `target` may be a scalar (a fixed level) or an array the same length as
    `time`/`value` (e.g. the reference curve's own value at each instant),
    matching the moving ±margin band drawn on the plot.
    """
    within = np.abs(value - target) <= band_abs
    outside_idx = np.where(~within)[0]
    if len(outside_idx) == 0:
        return float(time[0])
    last_outside = outside_idx[-1]
    if last_outside == len(time) - 1:
        return None  # never settles within the observed window
    return float(time[last_outside + 1])


def steady_state_value(time: np.ndarray, value: np.ndarray, tail_fraction: float = 0.1) -> float:
    n_tail = max(1, int(tail_fraction * len(value)))
    return float(np.mean(value[-n_tail:]))


@dataclass
class CurveResult:
    label: str
    path: str
    time: np.ndarray
    value: np.ndarray
    rated: float
    time_scale: float
    rise: dict
    settling: dict = field(default_factory=dict)  # margin_pct -> settling time
    steady_state: float = 0.0
    is_reference: bool = False


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def analyze(
    files: list[str],
    rated_values: list[float],
    margins_pct: list[float],
    transition_time: float = 10.0,
    reference: Optional[str] = None,
    labels: Optional[list[str]] = None,
) -> tuple[list[CurveResult], int]:
    if len(files) != len(rated_values):
        raise ValueError("files and rated_values must have the same length")
    labels = labels or [Path(f).stem for f in files]

    ref_idx = detect_reference_index(files, explicit=reference)

    raw = [load_signal(f) for f in files]

    aligned = []
    for (time, value), rated in zip(raw, rated_values):
        t_aligned, scale = align_time_axis(time, value, rated, transition_time)
        aligned.append((t_aligned, value, scale))

    ref_rated = rated_values[ref_idx]
    ref_time, ref_value, _ = aligned[ref_idx]

    results: list[CurveResult] = []
    for i, (label, path, (t_aligned, value, scale), rated) in enumerate(
        zip(labels, files, aligned, rated_values)
    ):
        rt = rise_time(t_aligned, value, rated)
        # Target for "settled" is the reference curve's own value at each
        # instant (not a fixed rated-value line) so the margin tracks the
        # reference plot's trajectory from start to end. The reference
        # curve itself has nothing to compare against, so it is judged
        # against its own flat rated-value line instead.
        if i == ref_idx:
            settle_target = ref_rated
        else:
            settle_target = np.interp(t_aligned, ref_time, ref_value)
        settling = {
            m: settling_time(t_aligned, value, settle_target, (m / 100.0) * ref_rated)
            for m in margins_pct
        }
        results.append(
            CurveResult(
                label=label,
                path=path,
                time=t_aligned,
                value=value,
                rated=rated,
                time_scale=scale,
                rise=rt,
                settling=settling,
                steady_state=steady_state_value(t_aligned, value),
                is_reference=(i == ref_idx),
            )
        )

    return results, ref_idx


def plot_results(results: list[CurveResult], ref_idx: int, margins_pct: list[float], output: Optional[str] = None):
    ref = results[ref_idx]
    ref_rated = ref.rated

    fig, ax = plt.subplots(figsize=(12, 7))

    colors = plt.cm.tab10.colors
    margin_alphas = {m: a for m, a in zip(sorted(margins_pct, reverse=True), (0.10, 0.18, 0.28))}

    # Margin band(s): an envelope that TRACKS the reference curve's own
    # value at every instant (ref.value(t) ± margin% of rated), drawn as a
    # shaded fill + dashed boundary lines from the reference's first sample
    # to its last — not a flat line at the rated value. This way the band
    # is meaningful during the rise too, not just once things have settled.
    for m in sorted(margins_pct, reverse=True):
        band = (m / 100.0) * ref_rated
        upper = ref.value + band
        lower = ref.value - band
        ax.fill_between(ref.time, lower, upper, color="tab:green", alpha=margin_alphas.get(m, 0.15), zorder=0)
        ax.plot(ref.time, upper, "--", color="tab:green", linewidth=1.4, alpha=0.9, zorder=1)
        ax.plot(
            ref.time, lower, "--", color="tab:green", linewidth=1.4, alpha=0.9, zorder=1,
            label=f"±{m:g}% margin around reference curve ({m:g}% of rated {ref_rated:g})",
        )

    text_lines = []
    for i, res in enumerate(results):
        color = colors[i % len(colors)]
        style = "-" if not res.is_reference else "-"
        lw = 2.5 if res.is_reference else 1.8
        marker_label = f"{res.label}{' (reference)' if res.is_reference else ''}"
        ax.plot(res.time, res.value, style, color=color, linewidth=lw, label=marker_label)

        # mark rise-time window
        if res.rise["t_low"] is not None and res.rise["t_high"] is not None:
            ax.plot(res.rise["t_low"], res.rise["low_frac"] * res.rated, "^", color=color, markersize=8)
            ax.plot(res.rise["t_high"], res.rise["high_frac"] * res.rated, "^", color=color, markersize=8)

        # mark settling points for every margin
        markers = {margins_pct[0]: "o", margins_pct[len(margins_pct) // 2]: "s", margins_pct[-1]: "D"} if len(margins_pct) >= 1 else {}
        for m in margins_pct:
            st = res.settling.get(m)
            if st is not None:
                st_idx = np.searchsorted(res.time, st)
                st_idx = min(st_idx, len(res.value) - 1)
                ax.plot(st, res.value[st_idx], markers.get(m, "*"), color=color, markersize=9,
                        markeredgecolor="black", markeredgewidth=0.5)

        rt_str = f"{res.rise['rise_time']:.3f}s" if res.rise["rise_time"] is not None else "N/A"
        settling_str = ", ".join(
            f"{m:g}%: {res.settling[m]:.3f}s" if res.settling[m] is not None else f"{m:g}%: N/A"
            for m in margins_pct
        )
        scale_note = f", time scale x{res.time_scale:.4f}" if abs(res.time_scale - 1.0) > 1e-6 else ""
        text_lines.append(
            f"{marker_label}: rated={res.rated:g}{scale_note}\n"
            f"   rise time (10-90%) = {rt_str}\n"
            f"   settling time (provisional*) -> {settling_str}\n"
            f"   steady-state value = {res.steady_state:.3f}"
        )

    ax.set_xlabel("Time (s) — aligned to reference transition")
    ax.set_ylabel("Value")
    ax.set_title("Transient Response Comparison")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3)

    summary_text = "\n".join(text_lines) + (
        "\n\n*settling time uses a placeholder definition (last exit from the\n"
        " band, held to end of data) — will be replaced once you give the\n"
        " exact definition you want."
    )
    ax.text(
        0.02, 0.98, summary_text,
        transform=ax.transAxes, fontsize=8, va="top", ha="left",
        family="monospace",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.85, edgecolor="gray"),
    )

    fig.tight_layout()
    if output:
        fig.savefig(output, dpi=150)
        print(f"Saved plot to {output}")
    return fig, ax


def analyze_and_plot(
    files: list[str],
    rated_values: list[float],
    margins_pct: list[float] = (5,),
    transition_time: float = 10.0,
    reference: Optional[str] = None,
    labels: Optional[list[str]] = None,
    output: Optional[str] = None,
):
    margins_pct = list(margins_pct)
    results, ref_idx = analyze(files, rated_values, margins_pct, transition_time, reference, labels)
    fig, ax = plot_results(results, ref_idx, margins_pct, output)
    return results, ref_idx, fig, ax


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--files", nargs=3, required=True, metavar=("FILE1", "FILE2", "FILE3"), help="Three CSV files, each with a time column and a value column")
    parser.add_argument("--rated", nargs=3, type=float, required=True, metavar=("R1", "R2", "R3"), help="Rated (target) value for each file, same order as --files")
    parser.add_argument("--margins", nargs="+", type=float, default=[5], help="Margin band(s) as %% of the reference rated value, drawn up/down across the whole plot (default: 5)")
    parser.add_argument("--transition-time", type=float, default=10.0, help="Nominal time (s) at which the step transition should occur (default: 10)")
    parser.add_argument("--reference", default=None, help="Reference file: filename, stem, or index (0/1/2). If omitted, auto-detected from filenames containing 'ref'/'reference'/etc, else the first file.")
    parser.add_argument("--labels", nargs=3, default=None, help="Custom legend labels for the three files")
    parser.add_argument("--output", default="transient_plot.png", help="Output image path (default: transient_plot.png)")
    parser.add_argument("--show", action="store_true", help="Display the plot interactively")
    args = parser.parse_args()

    results, ref_idx, fig, ax = analyze_and_plot(
        files=args.files,
        rated_values=args.rated,
        margins_pct=args.margins,
        transition_time=args.transition_time,
        reference=args.reference,
        labels=args.labels,
        output=args.output,
    )

    print(f"\nReference plot: {results[ref_idx].label} ({results[ref_idx].path})")
    for res in results:
        print(f"\n{res.label} ({res.path}){' [REFERENCE]' if res.is_reference else ''}")
        print(f"  rated value        : {res.rated:g}")
        print(f"  time scale applied : x{res.time_scale:.4f}")
        rt = res.rise["rise_time"]
        print(f"  rise time (10-90%) : {rt:.3f} s" if rt is not None else "  rise time (10-90%) : N/A")
        for m in args.margins:
            st = res.settling[m]
            print(f"  settling time ({m:g}% band) : {st:.3f} s" if st is not None else f"  settling time ({m:g}% band) : N/A")
        print(f"  steady-state value : {res.steady_state:.3f}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
