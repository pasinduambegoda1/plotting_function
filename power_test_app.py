"""
Directory-driven batch application for PSSE / PSCAD / Test comparisons.

Expects three separate root folders (one each for PSSE, PSCAD, Test), all
sharing the same layout:

    <root>/<plant_name>/<main_test_name>/<sub_test_name>/...

e.g. `PSSE_ROOT/STSF1/HP3/SFPFT_01/...` — plant STSF1, main test "HP3"
(a hold point), sub-test "SFPFT_01" (one of several similar sub-tests run
under that hold point).

- PSSE and PSCAD sub-test folders each hold exactly one CSV directly
  inside them (the usual single wide simulation export with every
  location's channels as columns — POC today; 33kV bus 1/2 and inverter
  level once their column naming is known, see LOCATION_CONFIG below).
- Test sub-test folders hold one subfolder per measurement location
  (POC, 33B1, 33B2, 34INV — only POC is processed today, see
  ACTIVE_LOCATIONS), each containing one or more CSV files.

Run per (plant, main test) — e.g. STSF1 / HP3. PSSE is always the
reference. The app:

  1. Lists the sub-test folders under PSSE for this plant/main-test.
  2. Lists the sub-test folders under Test for the same plant/main-test.
  3. Only processes sub-tests common to both (reports the rest as
     skipped — a sub-test PSSE wasn't run for, or that Test doesn't have
     a matching folder for, isn't run).
  4. For each common sub-test and each active location: requires that
     location's folder to exist under the Test sub-test folder — if it
     doesn't, that's reported as an ERROR (not a silent skip), per the
     PSSE-vs-Test rule above only applying at the sub-test level, not the
     location level. If it exists, every CSV inside it is plotted against
     the PSSE reference for that location.
  5. Results are written to a mirrored folder structure under
     --results-root: <results_root>/<plant>/<main_test>/<sub_test>/<location>/

Usage:
    python3 power_test_app.py \
        --psse-root /data/PSSE --test-root /data/TEST \
        --plant STSF1 --main-test HP3 \
        --results-root /data/results \
        --rated-p 202 --rated-q 79.86
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from power_test_plot import (
    CHANNELS, analyze, load_sim_source, load_test_source, plot_channel,
)

# Which measurement locations to actually process today, and (for PSSE and
# PSCAD, whose data all lives in one wide CSV per sub-test) which column
# keyword identifies that location's P/Q/V columns in that CSV. Add a new
# location here once its PSSE/PSCAD column naming convention is known — the
# Test-side folder check picks it up automatically, no other code changes
# needed.
LOCATION_CONFIG = {
    "POC": {"sim_column_keyword": "POC"},
    "33B1": {"sim_column_keyword": None},   # TODO: fill in once known
    "33B2": {"sim_column_keyword": None},   # TODO: fill in once known
    "34INV": {"sim_column_keyword": None},  # TODO: fill in once known
}
ACTIVE_LOCATIONS = ["POC"]


@dataclass
class BatchReport:
    processed: list = field(default_factory=list)   # (sub_test, location, file)
    skipped_subtests: list = field(default_factory=list)   # (sub_test, reason)
    errors: list = field(default_factory=list)       # (sub_test, location, message)

    def ok(self) -> bool:
        return not self.errors


def list_subdirs(path: Path) -> dict:
    if not path.is_dir():
        return {}
    return {p.name: p for p in path.iterdir() if p.is_dir()}


def single_csv_in(path: Path) -> Path:
    csvs = sorted(path.glob("*.csv"))
    if len(csvs) == 0:
        raise FileNotFoundError(f"no CSV file found in {path}")
    if len(csvs) > 1:
        raise ValueError(f"expected exactly one CSV in {path}, found {len(csvs)}: {[c.name for c in csvs]}")
    return csvs[0]


def run_batch(
    psse_root: Path,
    test_root: Path,
    plant: str,
    main_test: str,
    results_root: Path,
    rated: dict,
    margins_pct: list,
    transition_time: float,
    window: tuple,
    pscad_root: Optional[Path] = None,
    test_kwargs: Optional[dict] = None,
    dry_run: bool = False,
) -> BatchReport:
    test_kwargs = test_kwargs or {}
    report = BatchReport()

    psse_dir = psse_root / plant / main_test
    test_dir = test_root / plant / main_test
    pscad_dir = (pscad_root / plant / main_test) if pscad_root else None

    if not psse_dir.is_dir():
        raise FileNotFoundError(f"PSSE folder not found: {psse_dir}")
    if not test_dir.is_dir():
        raise FileNotFoundError(f"Test folder not found: {test_dir}")
    if pscad_dir is not None and not pscad_dir.is_dir():
        raise FileNotFoundError(f"PSCAD folder not found: {pscad_dir}")

    psse_subtests = list_subdirs(psse_dir)
    test_subtests = list_subdirs(test_dir)
    pscad_subtests = list_subdirs(pscad_dir) if pscad_dir is not None else None

    common = set(psse_subtests) & set(test_subtests)
    if pscad_subtests is not None:
        common &= set(pscad_subtests)

    for name in sorted(set(psse_subtests) - common):
        report.skipped_subtests.append((name, "in PSSE but no matching folder in Test" +
                                         (" (or PSCAD)" if pscad_subtests is not None else "")))
    for name in sorted(set(test_subtests) - common):
        report.skipped_subtests.append((name, "in Test but no matching folder in PSSE"))
    if pscad_subtests is not None:
        for name in sorted(set(pscad_subtests) - common):
            report.skipped_subtests.append((name, "in PSCAD but no matching folder in PSSE/Test"))

    print(f"PSSE sub-tests found  : {sorted(psse_subtests)}")
    print(f"Test sub-tests found  : {sorted(test_subtests)}")
    if pscad_subtests is not None:
        print(f"PSCAD sub-tests found : {sorted(pscad_subtests)}")
    print(f"Common sub-tests (will run): {sorted(common)}")
    for name, reason in report.skipped_subtests:
        print(f"  SKIP sub-test '{name}': {reason}")

    for sub_test in sorted(common):
        try:
            psse_csv = single_csv_in(psse_subtests[sub_test])
        except (FileNotFoundError, ValueError) as e:
            report.errors.append((sub_test, None, f"PSSE: {e}"))
            print(f"  ERROR [{sub_test}] PSSE: {e}")
            continue

        pscad_csv = None
        if pscad_subtests is not None:
            try:
                pscad_csv = single_csv_in(pscad_subtests[sub_test])
            except (FileNotFoundError, ValueError) as e:
                report.errors.append((sub_test, None, f"PSCAD: {e}"))
                print(f"  ERROR [{sub_test}] PSCAD: {e}")
                continue

        for location in ACTIVE_LOCATIONS:
            loc_cfg = LOCATION_CONFIG.get(location, {})
            sim_keyword = loc_cfg.get("sim_column_keyword")
            if sim_keyword is None:
                report.errors.append((sub_test, location, f"no PSSE/PSCAD column pattern configured for location '{location}' yet"))
                print(f"  ERROR [{sub_test}/{location}]: no PSSE/PSCAD column pattern configured yet — add it to LOCATION_CONFIG")
                continue

            test_location_dir = test_subtests[sub_test] / location
            if not test_location_dir.is_dir():
                report.errors.append((sub_test, location, f"Test folder missing: {test_location_dir}"))
                print(f"  ERROR [{sub_test}/{location}]: expected Test folder not found: {test_location_dir}")
                continue

            test_csvs = sorted(test_location_dir.glob("*.csv"))
            if not test_csvs:
                report.errors.append((sub_test, location, f"no CSV files in {test_location_dir}"))
                print(f"  ERROR [{sub_test}/{location}]: no CSV files found in {test_location_dir}")
                continue

            if dry_run:
                for test_csv in test_csvs:
                    print(f"  WOULD PROCESS [{sub_test}/{location}]: PSSE={psse_csv.name} TEST={test_csv.name}"
                          + (f" PSCAD={pscad_csv.name}" if pscad_csv else ""))
                    report.processed.append((sub_test, location, test_csv.name))
                continue

            try:
                t_psse, ch_psse = load_sim_source(str(psse_csv), location_keyword=sim_keyword)
            except Exception as e:
                report.errors.append((sub_test, location, f"failed to load PSSE file {psse_csv}: {e}"))
                print(f"  ERROR [{sub_test}/{location}]: failed to load PSSE file {psse_csv}: {e}")
                continue

            sources_base = {"psse": (str(psse_csv), t_psse, ch_psse)}
            if pscad_csv is not None:
                try:
                    t_pscad, ch_pscad = load_sim_source(str(pscad_csv), location_keyword=sim_keyword)
                    sources_base["pscad"] = (str(pscad_csv), t_pscad, ch_pscad)
                except Exception as e:
                    report.errors.append((sub_test, location, f"failed to load PSCAD file {pscad_csv}: {e}"))
                    print(f"  ERROR [{sub_test}/{location}]: failed to load PSCAD file {pscad_csv}: {e}")
                    continue

            for test_csv in test_csvs:
                try:
                    t_test, ch_test = load_test_source(str(test_csv), **test_kwargs)
                except Exception as e:
                    report.errors.append((sub_test, location, f"failed to load Test file {test_csv}: {e}"))
                    print(f"  ERROR [{sub_test}/{location}]: failed to load Test file {test_csv}: {e}")
                    continue

                sources = dict(sources_base)
                sources["test"] = (str(test_csv), t_test, ch_test)

                try:
                    results = analyze(
                        sources, rated=rated, margins_pct=margins_pct,
                        transition_time=transition_time, window=window, reference="psse",
                    )
                except Exception as e:
                    report.errors.append((sub_test, location, f"analysis failed for {test_csv}: {e}"))
                    print(f"  ERROR [{sub_test}/{location}]: analysis failed for {test_csv}: {e}")
                    continue

                out_dir = results_root / plant / main_test / sub_test / location
                out_dir.mkdir(parents=True, exist_ok=True)
                stem = test_csv.stem
                for ch in CHANNELS:
                    title = f"{plant} / {main_test} / {sub_test} / {location} — {ch}"
                    plot_channel(results, ch, rated, margins_pct, title, str(out_dir / f"{stem}_{ch}.png"))

                report.processed.append((sub_test, location, test_csv.name))
                print(f"  OK [{sub_test}/{location}]: {test_csv.name} -> {out_dir}")

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--psse-root", required=True)
    parser.add_argument("--test-root", required=True)
    parser.add_argument("--pscad-root", default=None)
    parser.add_argument("--plant", required=True, help="Plant name, e.g. STSF1")
    parser.add_argument("--main-test", required=True, help="Main test name, e.g. HP3")
    parser.add_argument("--results-root", required=True)

    parser.add_argument("--rated-p", type=float, default=202.0)
    parser.add_argument("--rated-q", type=float, default=79.86)
    parser.add_argument("--rated-v", type=float, default=1.0)
    parser.add_argument("--v-base", type=float, default=330000.0)
    parser.add_argument("--margins", nargs="+", type=float, default=[5])
    parser.add_argument("--transition-time", type=float, default=10.0)
    parser.add_argument("--window", nargs=2, type=float, default=[0.0, 30.0])

    parser.add_argument("--test-time-col", default="Time")
    parser.add_argument("--test-p-col", default="Test P")
    parser.add_argument("--test-q-col", default="Test Q")
    parser.add_argument("--test-v-col", default="Test V")
    parser.add_argument("--test-p-divisor", type=float, default=1e6)
    parser.add_argument("--test-q-divisor", type=float, default=1e6)

    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without loading/plotting anything")
    args = parser.parse_args()

    rated = {"P": args.rated_p, "Q": args.rated_q, "V": args.rated_v}
    test_kwargs = dict(
        v_base=args.v_base, p_divisor=args.test_p_divisor, q_divisor=args.test_q_divisor,
        time_col=args.test_time_col, p_col=args.test_p_col, q_col=args.test_q_col, v_col=args.test_v_col,
    )

    report = run_batch(
        psse_root=Path(args.psse_root), test_root=Path(args.test_root),
        plant=args.plant, main_test=args.main_test, results_root=Path(args.results_root),
        rated=rated, margins_pct=args.margins, transition_time=args.transition_time,
        window=tuple(args.window), pscad_root=Path(args.pscad_root) if args.pscad_root else None,
        test_kwargs=test_kwargs, dry_run=args.dry_run,
    )

    print(f"\n{'DRY RUN ' if args.dry_run else ''}Summary: {len(report.processed)} processed, "
          f"{len(report.skipped_subtests)} sub-test(s) skipped, {len(report.errors)} error(s)")
    sys.exit(1 if report.errors else 0)


if __name__ == "__main__":
    main()
