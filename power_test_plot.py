"""
Power-plant transient test comparison: PSSE / PSCAD / real plant Test data.

Compares Active Power (MW), Reactive Power (MVar), and Voltage (pu) at the
POC across up to three sources for one test case:

  - PSSE   : phasor-domain simulation export. Time column "Time(s)"; P/Q/V
             columns auto-detected as whichever column ends in "POC P" /
             "POC Q" / "POC V" (case/space/underscore-insensitive). Already
             in MW / MVar / pu.
  - PSCAD  : EMT-domain simulation export. Same auto-detection as PSSE by
             default; override with --pscad-*-col if its naming differs.
  - Test   : real plant measurement. Time column "Time" holding an ISO 8601
             timestamp (e.g. "2025-7-8T4:58:49.994640953Z"); value columns
             "Test P" (W), "Test Q" (Var), "Test V" (V, line-to-line),
             converted to MW / MVar / pu.

All three run in some steady state, then a transition is applied at 10 s
and the transient is observed for another 20 s (30 s total). PSSE/PSCAD
are simulation-controlled so their transition lands at ~10 s already; the
Test file is extracted from a wall-clock timestamp log recorded at a
higher, less exact rate, is longer than 30 s, and its transition does NOT
land on 10 s on its own. For every source this tool detects the moment
the transition actually starts and SHIFTS (not rescales — this is a
clock-offset problem, not a sample-rate problem) that file's whole time
axis so the transition lands exactly on --transition-time (10 s), then
trims every source to the analysis window (0-30 s by default).

Produces three separate plots (Active Power, Reactive Power, Voltage),
each overlaying every source supplied, with a margin band that tracks the
reference source's own curve (default: PSCAD > PSSE > Test, whichever is
present — pass --reference to force it), and rise time / settling time
annotated for every curve on every plot.

Usage:
    python3 power_test_plot.py \
        --test STSF1_HP3_SFPFT_01.csv \
        --psse PSSE_STSF1_HP3_SFPFT_01.csv \
        --rated-p 202 --rated-q 79.86 \
        --margins 5 \
        --outdir out/
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

CHANNELS = ("P", "Q", "V")
CHANNEL_LABELS = {"P": "Active Power (MW)", "Q": "Reactive Power (MVar)", "V": "Voltage (pu)"}
SOURCE_ORDER_FOR_REFERENCE = ("pscad", "psse", "test")


# --------------------------------------------------------------------------
# Column auto-detection
# --------------------------------------------------------------------------

def _normalize(col: str) -> str:
    return re.sub(r"\s+", " ", col.strip().replace("_", " ")).upper()


def find_time_col(columns) -> str:
    for c in columns:
        if _normalize(c).startswith("TIME"):
            return c
    return columns[0]


def find_poc_col(columns, letter: str) -> str:
    """Find the column ending in 'POC <letter>' (e.g. 'STSF POC P')."""
    for c in columns:
        n = _normalize(c)
        if "POC" in n and n.split(" ")[-1] == letter:
            return c
    raise ValueError(
        f"Could not auto-detect a 'POC {letter}' column among: {list(columns)}. "
        f"Pass the column name explicitly (e.g. --psse-{letter.lower()}-col)."
    )


# --------------------------------------------------------------------------
# Loaders — each returns (time_seconds, {'P': arr, 'Q': arr, 'V': arr})
# --------------------------------------------------------------------------

def load_sim_source(path: str, p_col=None, q_col=None, v_col=None, time_col=None) -> tuple[np.ndarray, dict]:
    """Loader for PSSE/PSCAD-style exports: numeric time column already in
    seconds, POC P/Q/V columns already in MW/MVar/pu."""
    df = pd.read_csv(path)
    time_col = time_col or find_time_col(df.columns)
    p_col = p_col or find_poc_col(df.columns, "P")
    q_col = q_col or find_poc_col(df.columns, "Q")
    v_col = v_col or find_poc_col(df.columns, "V")
    t = pd.to_numeric(df[time_col], errors="coerce").to_numpy(dtype=float)
    channels = {
        "P": pd.to_numeric(df[p_col], errors="coerce").to_numpy(dtype=float),
        "Q": pd.to_numeric(df[q_col], errors="coerce").to_numpy(dtype=float),
        "V": pd.to_numeric(df[v_col], errors="coerce").to_numpy(dtype=float),
    }
    return t, channels


def load_test_source(
    path: str,
    v_base: float,
    p_divisor: float = 1e6,
    q_divisor: float = 1e6,
    time_col: str = "Time",
    p_col: str = "Test P",
    q_col: str = "Test Q",
    v_col: str = "Test V",
) -> tuple[np.ndarray, dict]:
    """Loader for real plant test logs: ISO-8601 timestamp column, P in W,
    Q in Var, V in volts (line-to-line), converted to MW / MVar / pu."""
    df = pd.read_csv(path)
    ts = pd.to_datetime(df[time_col])
    t = (ts - ts.iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    channels = {
        "P": pd.to_numeric(df[p_col], errors="coerce").to_numpy(dtype=float) / p_divisor,
        "Q": pd.to_numeric(df[q_col], errors="coerce").to_numpy(dtype=float) / q_divisor,
        "V": pd.to_numeric(df[v_col], errors="coerce").to_numpy(dtype=float) / v_base,
    }
    return t, channels


# --------------------------------------------------------------------------
# Crossing / onset / rise / settling helpers
# --------------------------------------------------------------------------

def first_crossing_time(time: np.ndarray, value: np.ndarray, level: float, hold: int = 1, rising: bool = True) -> Optional[float]:
    """First (interpolated) time `value` crosses through `level`.

    `rising=True` looks for value going >= level; `rising=False` for value
    going <= level (a falling step). `hold` requires the next `hold`
    samples to also satisfy the condition, to reject noise spikes.
    """
    cond = (value >= level) if rising else (value <= level)
    idx = None
    for i in range(len(value)):
        if cond[i] and np.all(cond[i : i + hold]):
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


def baseline_final(value: np.ndarray, edge_fraction: float = 0.05) -> tuple[float, float]:
    n = max(1, int(edge_fraction * len(value)))
    return float(np.mean(value[:n])), float(np.mean(value[-n:]))


def detect_onset(time: np.ndarray, value: np.ndarray, onset_frac: float = 0.05, hold_frac: float = 0.01) -> Optional[float]:
    """Time the signal first departs from its own baseline by `onset_frac`
    of its own (final - baseline) step size, in whichever direction the
    step actually goes."""
    baseline, final = baseline_final(value)
    level = baseline + onset_frac * (final - baseline)
    hold = max(1, int(hold_frac * len(value)))
    rising = final >= baseline
    return first_crossing_time(time, value, level, hold=hold, rising=rising)


def rise_time(
    time: np.ndarray, value: np.ndarray, baseline: float, final: float,
    rated: float, low: float = 0.1, high: float = 0.9, min_step_frac: float = 0.02,
) -> dict:
    """Rise time between crossing `low`/`high` fractions of the step
    (final - baseline). If the channel barely moves (step size below
    `min_step_frac` of `rated`), there is no real transition to time —
    return N/A rather than a number driven by noise.
    """
    if rated and abs(final - baseline) < min_step_frac * abs(rated):
        return {"t_low": None, "t_high": None, "v_low": None, "v_high": None, "rise_time": None}
    rising = final >= baseline
    level_low = baseline + low * (final - baseline)
    level_high = baseline + high * (final - baseline)
    t_low = first_crossing_time(time, value, level_low, rising=rising)
    t_high = first_crossing_time(time, value, level_high, rising=rising)
    rt = (t_high - t_low) if (t_low is not None and t_high is not None) else None
    return {"t_low": t_low, "t_high": t_high, "v_low": level_low, "v_high": level_high, "rise_time": rt}


def settling_time(time: np.ndarray, value: np.ndarray, target, band_abs: float) -> Optional[float]:
    """First time after which `value` stays within `band_abs` of `target`
    (scalar or same-length array) continuously through the end of data."""
    within = np.abs(value - target) <= band_abs
    outside_idx = np.where(~within)[0]
    if len(outside_idx) == 0:
        return float(time[0])
    last_outside = outside_idx[-1]
    if last_outside == len(time) - 1:
        return None
    return float(time[last_outside + 1])


# --------------------------------------------------------------------------
# Per-file alignment
# --------------------------------------------------------------------------

@dataclass
class SourceData:
    name: str
    path: str
    time: np.ndarray  # aligned + trimmed
    channels: dict  # 'P'/'Q'/'V' -> aligned + trimmed array
    shift: float
    trigger_channel: str
    is_reference: bool = False


def align_and_trim(
    name: str,
    path: str,
    time: np.ndarray,
    channels: dict,
    rated: dict,
    transition_time: float,
    window: tuple[float, float],
) -> SourceData:
    # Pick the channel with the largest relative step (vs its rated value)
    # as the trigger for detecting the transition instant in this file.
    best_channel, best_rel = None, -1.0
    for ch, arr in channels.items():
        baseline, final = baseline_final(arr)
        rel = abs(final - baseline) / rated[ch] if rated[ch] else abs(final - baseline)
        if rel > best_rel:
            best_rel, best_channel = rel, ch

    onset = detect_onset(time, channels[best_channel])
    shift = (transition_time - onset) if onset is not None else 0.0
    shifted_time = time + shift

    mask = (shifted_time >= window[0]) & (shifted_time <= window[1])
    trimmed_time = shifted_time[mask]
    trimmed_channels = {ch: arr[mask] for ch, arr in channels.items()}

    return SourceData(
        name=name, path=path, time=trimmed_time, channels=trimmed_channels,
        shift=shift, trigger_channel=best_channel,
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def analyze(
    sources: dict[str, tuple[str, np.ndarray, dict]],  # name -> (path, time, channels)
    rated: dict,  # 'P'/'Q'/'V' -> rated value
    margins_pct: list[float],
    transition_time: float = 10.0,
    window: tuple[float, float] = (0.0, 30.0),
    reference: Optional[str] = None,
) -> tuple[dict[str, SourceData], str]:
    aligned: dict[str, SourceData] = {}
    for name, (path, time, channels) in sources.items():
        aligned[name] = align_and_trim(name, path, time, channels, rated, transition_time, window)

    if reference is not None:
        if reference not in aligned:
            raise ValueError(f"--reference {reference!r} was not among the supplied sources {list(aligned)}")
        ref_name = reference
    else:
        ref_name = next((n for n in SOURCE_ORDER_FOR_REFERENCE if n in aligned), next(iter(aligned)))
    aligned[ref_name].is_reference = True

    ref = aligned[ref_name]

    results = {"reference": ref_name, "sources": aligned, "metrics": {}}
    for name, src in aligned.items():
        results["metrics"][name] = {}
        for ch in CHANNELS:
            value = src.channels[ch]
            baseline, final = baseline_final(value)
            rt = rise_time(src.time, value, baseline, final, rated[ch])

            if name == ref_name:
                # Nothing external to compare the reference against: judge it
                # against its own settled (final) value, not the absolute
                # rated capacity — a channel that never approaches full
                # rated capacity (e.g. Q during a small-signal test) should
                # still be able to "settle".
                settle_target = final
            else:
                settle_target = np.interp(src.time, ref.time, ref.channels[ch])
            settling = {
                m: settling_time(src.time, value, settle_target, (m / 100.0) * rated[ch])
                for m in margins_pct
            }
            results["metrics"][name][ch] = {
                "baseline": baseline, "final": final, "rise": rt, "settling": settling,
            }

    return results


SOURCE_COLORS = {"psse": "tab:blue", "pscad": "tab:orange", "test": "tab:red"}
BAND_COLOR = "dimgray"


def plot_channel(results: dict, channel: str, rated: dict, margins_pct: list[float], title: str, output: str):
    ref_name = results["reference"]
    sources = results["sources"]
    metrics = results["metrics"]
    ref = sources[ref_name]

    fig, ax = plt.subplots(figsize=(13, 7.5))
    margin_alphas = {m: a for m, a in zip(sorted(margins_pct, reverse=True), (0.10, 0.18, 0.28))}

    # Margin band always uses a fixed neutral color, deliberately excluded
    # from SOURCE_COLORS, so it never becomes visually indistinguishable
    # from one of the (at most 3) source curves.
    ref_value = ref.channels[channel]
    for m in sorted(margins_pct, reverse=True):
        band = (m / 100.0) * rated[channel]
        ax.fill_between(ref.time, ref_value - band, ref_value + band, color=BAND_COLOR,
                         alpha=margin_alphas.get(m, 0.15), zorder=0)
        ax.plot(ref.time, ref_value + band, "--", color=BAND_COLOR, linewidth=1.4, alpha=0.9, zorder=1)
        ax.plot(ref.time, ref_value - band, "--", color=BAND_COLOR, linewidth=1.4, alpha=0.9, zorder=1,
                label=f"±{m:g}% margin around {ref_name.upper()} ({m:g}% of rated {rated[channel]:g})")

    ax.axvline(10.0, color="gray", linestyle=":", linewidth=1, alpha=0.7, zorder=1)

    text_lines = []
    for i, (name, src) in enumerate(sources.items()):
        color = SOURCE_COLORS.get(name, plt.cm.tab10.colors[i % 10])
        value = src.channels[channel]
        label = f"{name.upper()}{' (reference)' if src.is_reference else ''}"
        lw = 2.6 if src.is_reference else 1.8
        ax.plot(src.time, value, "-", color=color, linewidth=lw, label=label, zorder=2)

        m_ch = metrics[name][channel]
        rt = m_ch["rise"]
        if rt["t_low"] is not None and rt["t_high"] is not None:
            ax.plot(rt["t_low"], rt["v_low"], "^", color=color, markersize=8, zorder=3)
            ax.plot(rt["t_high"], rt["v_high"], "^", color=color, markersize=8, zorder=3)

        for m in margins_pct:
            st = m_ch["settling"][m]
            if st is not None:
                st_idx = min(np.searchsorted(src.time, st), len(value) - 1)
                ax.plot(st, value[st_idx], "D", color=color, markersize=9,
                        markeredgecolor="black", markeredgewidth=0.5, zorder=3)

        rt_str = f"{rt['rise_time']:.3f}s" if rt["rise_time"] is not None else "N/A"
        settling_str = ", ".join(
            f"{m:g}%: {m_ch['settling'][m]:.3f}s" if m_ch["settling"][m] is not None else f"{m:g}%: N/A"
            for m in margins_pct
        )
        shift_note = f", time shift {src.shift:+.4f}s (trigger: {src.trigger_channel})" if abs(src.shift) > 1e-6 else ""
        text_lines.append(
            f"{label}{shift_note}\n"
            f"   baseline={m_ch['baseline']:.3f}  final={m_ch['final']:.3f}\n"
            f"   rise time (10-90%) = {rt_str}\n"
            f"   settling time (provisional*) -> {settling_str}"
        )

    ax.set_xlabel("Time (s) — transition aligned to t=10s")
    ax.set_ylabel(CHANNEL_LABELS[channel])
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)

    summary_text = "\n".join(text_lines) + (
        "\n\n*settling time uses a placeholder definition (last exit from the\n"
        " band, held to end of data) — will be replaced once you give the\n"
        " exact definition you want."
    )
    ax.text(
        0.02, 0.98, summary_text, transform=ax.transAxes, fontsize=7.5, va="top", ha="left",
        family="monospace", bbox=dict(boxstyle="round", facecolor="white", alpha=0.88, edgecolor="gray"),
    )

    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)
    print(f"Saved {output}")


def derive_case_name(test_path, psse_path, pscad_path) -> str:
    for path, prefix in ((test_path, None), (psse_path, "PSSE_"), (pscad_path, "PSCAD_")):
        if path is None:
            continue
        stem = Path(path).stem
        if prefix and stem.upper().startswith(prefix.upper()):
            stem = stem[len(prefix):]
        return stem
    return "transient_test"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test", help="Real plant test CSV")
    parser.add_argument("--psse", help="PSSE simulation export CSV")
    parser.add_argument("--pscad", help="PSCAD simulation export CSV")

    parser.add_argument("--rated-p", type=float, default=202.0, help="Rated active power, MW (default: 202)")
    parser.add_argument("--rated-q", type=float, default=79.86, help="Rated reactive power, MVar (default: 79.86)")
    parser.add_argument("--rated-v", type=float, default=1.0, help="Rated (nominal) voltage in pu (default: 1.0)")
    parser.add_argument("--v-base", type=float, default=330000.0, help="Voltage base in volts, used to convert the Test file's V to pu (default: 330000)")

    parser.add_argument("--margins", nargs="+", type=float, default=[5], help="Margin band(s) as %% of rated value (default: 5)")
    parser.add_argument("--transition-time", type=float, default=10.0, help="Time (s) the transition should land on after alignment (default: 10)")
    parser.add_argument("--window", nargs=2, type=float, default=[0.0, 30.0], metavar=("START", "END"), help="Analysis window in seconds after alignment (default: 0 30)")
    parser.add_argument("--reference", choices=["test", "psse", "pscad"], default=None, help="Force the reference source (default: pscad > psse > test, whichever is supplied)")

    parser.add_argument("--psse-time-col", default=None)
    parser.add_argument("--psse-p-col", default=None)
    parser.add_argument("--psse-q-col", default=None)
    parser.add_argument("--psse-v-col", default=None)
    parser.add_argument("--pscad-time-col", default=None)
    parser.add_argument("--pscad-p-col", default=None)
    parser.add_argument("--pscad-q-col", default=None)
    parser.add_argument("--pscad-v-col", default=None)
    parser.add_argument("--test-time-col", default="Time")
    parser.add_argument("--test-p-col", default="Test P")
    parser.add_argument("--test-q-col", default="Test Q")
    parser.add_argument("--test-v-col", default="Test V")
    parser.add_argument("--test-p-divisor", type=float, default=1e6)
    parser.add_argument("--test-q-divisor", type=float, default=1e6)

    parser.add_argument("--outdir", default=".", help="Directory to write the three plots into")
    parser.add_argument("--case-name", default=None, help="Base name for output files (default: derived from the input filenames)")
    args = parser.parse_args()

    if not any([args.test, args.psse, args.pscad]):
        parser.error("at least one of --test / --psse / --pscad is required")

    rated = {"P": args.rated_p, "Q": args.rated_q, "V": args.rated_v}

    raw_sources = {}
    if args.psse:
        t, ch = load_sim_source(args.psse, p_col=args.psse_p_col, q_col=args.psse_q_col,
                                 v_col=args.psse_v_col, time_col=args.psse_time_col)
        raw_sources["psse"] = (args.psse, t, ch)
    if args.pscad:
        t, ch = load_sim_source(args.pscad, p_col=args.pscad_p_col, q_col=args.pscad_q_col,
                                 v_col=args.pscad_v_col, time_col=args.pscad_time_col)
        raw_sources["pscad"] = (args.pscad, t, ch)
    if args.test:
        t, ch = load_test_source(args.test, v_base=args.v_base, p_divisor=args.test_p_divisor,
                                  q_divisor=args.test_q_divisor, time_col=args.test_time_col,
                                  p_col=args.test_p_col, q_col=args.test_q_col, v_col=args.test_v_col)
        raw_sources["test"] = (args.test, t, ch)

    results = analyze(
        raw_sources, rated=rated, margins_pct=args.margins,
        transition_time=args.transition_time, window=tuple(args.window), reference=args.reference,
    )

    print(f"Reference source: {results['reference'].upper()}")
    for name, src in results["sources"].items():
        print(f"\n{name.upper()} ({src.path}){' [REFERENCE]' if src.is_reference else ''}")
        print(f"  time shift applied : {src.shift:+.4f}s (trigger channel: {src.trigger_channel})")
        for ch in CHANNELS:
            m = results["metrics"][name][ch]
            rt = m["rise"]["rise_time"]
            print(f"  [{ch}] baseline={m['baseline']:.4f} final={m['final']:.4f} "
                  f"rise_time={f'{rt:.3f}s' if rt is not None else 'N/A'}")
            for margin in args.margins:
                st = m["settling"][margin]
                print(f"      settling ({margin:g}% band) = {f'{st:.3f}s' if st is not None else 'N/A'}")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    case_name = args.case_name or derive_case_name(args.test, args.psse, args.pscad)
    channel_titles = {
        "P": f"{case_name} — Active Power (POC)",
        "Q": f"{case_name} — Reactive Power (POC)",
        "V": f"{case_name} — Voltage (POC)",
    }
    for ch in CHANNELS:
        plot_channel(results, ch, rated, args.margins, channel_titles[ch], str(outdir / f"{case_name}_{ch}.png"))


if __name__ == "__main__":
    main()
