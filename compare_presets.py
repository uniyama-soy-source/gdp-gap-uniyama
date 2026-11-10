"""設定の組（gapmodel/config.py の CAO_PRESETS）ごとに、内閣府GDPギャップの副指標を並べて比べる。

  水準RMSE（全期間・2023年以降）、最新期の値、前期差RMSE、潜在成長率RMSE（前年比）と、
  製造業の稼働率が DP/26-8 図8 とどれだけ合うか（図8の数値は tools_read_dp268_figs.py で読み取ったもの）。
使い方：  python compare_presets.py      → 画面と results/cao_presets.csv
主指標（公表当時のデータでの誤差）は  python backtest_realtime2.py --preset <名前>  で見る。
"""
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from gapmodel import data, cao
from gapmodel.config import CAOConfig, CAO_PRESETS

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent


def rmse(a, b, since=None):
    j = pd.concat([a, b], axis=1).dropna()
    if since:
        j = j.loc[pd.Period(since, "Q"):]
    e = j.iloc[:, 0] - j.iloc[:, 1]
    return round(float(np.sqrt((e ** 2).mean())), 3), round(float(e.mean()), 2)


def main():
    d, pub = data.load_all(), data.load_published()["cao"]
    f8 = ROOT / "data" / "published" / "dp268_fig8_mfg_util.csv"
    fig8 = None
    if f8.exists():
        t = pd.read_csv(f8)
        fig8 = pd.Series(t.util_mfg.values, index=pd.PeriodIndex(t.q, freq="Q"))
    rows = []
    for name, opt in CAO_PRESETS.items():
        r, info = cao.run(d, CAOConfig(**opt))
        row = {"設定の組": name,
               "水準RMSE 全期間": rmse(r.gap, pub.gap)[0], "水準RMSE 2023年以降": rmse(r.gap, pub.gap, "2023Q1")[0],
               "平均差 2023年以降": rmse(r.gap, pub.gap, "2023Q1")[1], "最新期": round(float(r.gap.iloc[-1]), 2),
               "前期差RMSE": rmse(r.gap.diff(), pub.gap.diff())[0],
               "潜在成長率RMSE": rmse(r.pot_growth.rolling(4).mean(), pub.pot)[0],
               "製造業の転換点": "・".join(info["capacity_breaks_mfg"]), "非製造業の転換点": "・".join(info["capacity_breaks_nonmfg"])}
        if fig8 is not None:
            row["製造業稼働率の図8との差(pt)"] = rmse(100 * r.U_m, fig8)[0]
        rows.append(row)
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(out.to_string(index=False))
    out.to_csv(ROOT / "results" / "cao_presets.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
