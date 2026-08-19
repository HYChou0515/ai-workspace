"""Regenerate the four fixtures. Fixed seed — the expected values in the
scenario files are measured from exactly these bytes."""

import numpy as np
import pandas as pd

rng = np.random.default_rng(20260817)

n = 1200
pd.DataFrame(
    {
        "wafer_id": [f"W{i:05d}" for i in range(n)],
        "thickness": [f"{v:,.0f}" for v in rng.normal(1200, 45, n)],
    }
).to_csv("silent_dtype.csv", index=False)

lots, per = 8, 150
offsets = rng.normal(0, 6.0, lots)
pd.DataFrame(
    {
        "lot": np.repeat([f"L{i:02d}" for i in range(lots)], per),
        "cd": np.concatenate([rng.normal(50 + o, 0.4, per) for o in offsets]),
    }
).to_csv("within_vs_global.csv", index=False)

pd.DataFrame({"leak_na": rng.lognormal(1.0, 1.1, 3000)}).to_csv("heavy_tail.csv", index=False)
pd.DataFrame({"vt_mv": rng.normal(450, 12, 5000)}).to_csv("control_clean.csv", index=False)
