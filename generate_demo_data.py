"""Generate 3 synthetic transient CSVs to demonstrate transient_plot.py.

- reference.csv : first-order step response, step at t=10s, rated=40
- plant_A.csv   : same dynamics, but its time column is compressed 2x
                   (so the transition appears at t=5s instead of t=10s) -
                   this is the "different time scale" case
- plant_B.csv   : normal time scale, faster response with a small
                   overshoot, slightly different rated value (42)
"""

from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)

OUT_DIR = Path(__file__).parent / "demo_data"
OUT_DIR.mkdir(exist_ok=True)


def first_order_step(t, step_time, rated, tau, noise_std=0.15):
    y = np.where(t < step_time, 0.0, rated * (1 - np.exp(-(t - step_time) / tau)))
    y = y + rng.normal(0, noise_std, size=t.shape)
    y[t < step_time] = rng.normal(0, noise_std * 0.3, size=y[t < step_time].shape)
    return y


def second_order_step(t, step_time, rated, wn, zeta, noise_std=0.15):
    tau = np.clip(t - step_time, 0, None)
    wd = wn * np.sqrt(max(1 - zeta ** 2, 1e-6))
    phi = np.arccos(zeta)
    y = rated * (1 - np.exp(-zeta * wn * tau) / np.sqrt(1 - zeta ** 2) * np.cos(wd * tau - phi))
    y = np.where(t < step_time, 0.0, y)
    y = y + rng.normal(0, noise_std, size=t.shape)
    y[t < step_time] = rng.normal(0, noise_std * 0.3, size=y[t < step_time].shape)
    return y


t = np.arange(0, 25, 0.02)

# --- reference.csv : rated 40, tau=1.5s, step at 10s
ref_val = first_order_step(t, step_time=10.0, rated=40.0, tau=1.5)
pd.DataFrame({"time": t, "value": ref_val}).to_csv(OUT_DIR / "reference.csv", index=False)

# --- plant_A.csv : same physical dynamics, but time axis is compressed 2x
#     (logged/exported at a different time base) -> transition lands at t=5s
plant_a_val = first_order_step(t, step_time=10.0, rated=40.0, tau=1.5)
plant_a_time_scaled = t * 0.5  # compress so 10s transition now sits at 5s
pd.DataFrame({"time": plant_a_time_scaled, "value": plant_a_val}).to_csv(OUT_DIR / "plant_A.csv", index=False)

# --- plant_B.csv : normal time scale, underdamped 2nd-order response, rated 42
plant_b_val = second_order_step(t, step_time=10.0, rated=42.0, wn=2.2, zeta=0.55)
pd.DataFrame({"time": t, "value": plant_b_val}).to_csv(OUT_DIR / "plant_B.csv", index=False)

print(f"Wrote demo CSVs to {OUT_DIR}")
