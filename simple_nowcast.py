"""内閣府GDPギャップの「超単純予測」：GDPの成長率だけで、前期の公表値からの変化を出す。

考え方：GDPギャップ ≒ ln(実際のGDP) − ln(潜在GDP) なので、
  今期のギャップ ≒ 前期のギャップ ＋ 今期の成長率 − 潜在成長率（四半期）
さらに前期のギャップは、前期のGDPが改定されると動く（前期の成長率の改定分を足す）。
使う数字（すべて％、前期比、年率にしない）：
  P   前回号の前期のギャップ（公表値）
  g0  前期の成長率（前期の2次速報時点）
  g1  前期の成長率（今期の1次速報時点）
  g   今期の成長率（今期の1次速報時点）
手法（いずれも、その回より前の情報だけで係数を決める＝実際に予測できる形）
  S0  P ＋ g − c                         c＝過去の回の（g −（実績−P））の平均（＝潜在成長率の四半期分に相当）
  S1  S0 ＋（g1 − g0）                     前期の成長率の改定を足す
  S2  回帰 実績 = a ＋ b·P ＋ c0·g0 ＋ c1·g1 ＋ d·g（過去の回だけで推計、12回目から予測）
使い方：python simple_nowcast.py              # 過去の各回で検証
        python simple_nowcast.py --now 0.7 --g 0.02 --g0 0.57 --g1 0.55   # 予測（P, g, g0, g1 を％で）
"""
import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from gapmodel import ingest as ig
from gapmodel.vintages import load_cao_vintages
from gapmodel.report import write_excel

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent
VDIR = ROOT / "data" / "vintages" / "esri"


def vint(q, kind):
    f = VDIR / f"gaku-jk{q.year % 100:02d}{q.quarter}{kind}.csv"
    return ig.parse_esri_gaku(f.read_bytes()) if f.exists() else None


def growth(s, q):
    return 100 * float(np.log(s.loc[q]) - np.log(s.loc[q - 1]))


def dataset():
    issues, long = load_cao_vintages()
    cur = long[long.推計 == "今回"]
    pub = lambda no, q: float(cur[cur.号 == no].set_index("期").GDPギャップ.get(q, np.nan))  # noqa: E731
    rows = []
    for t in sorted(issues[issues.速報 == "1次"].対象期.unique()):
        first = issues[(issues.対象期 == t) & (issues.速報 == "1次")]
        v1, v0 = vint(t, 1), vint(t - 1, 2)
        if v1 is None or v0 is None:
            continue
        no1 = int(first.号.iloc[0])
        before = issues[issues.公表日 < first.公表日.iloc[0]]
        if not len(before):
            continue
        no0 = int(before.号.iloc[-1])
        y, P = pub(no1, t), pub(no0, t - 1)
        if np.isnan(y) or np.isnan(P):
            continue
        rows.append({"対象期": str(t), "実績": y, "P": P, "g0": growth(v0, t - 1), "g1": growth(v1, t - 1),
                     "g": growth(v1, t), "前期の改定（内閣府）": pub(no1, t - 1) - P})
    return pd.DataFrame(rows)


def backtest(R, min_obs=8, min_reg=12):
    out = []
    for i in range(len(R)):
        past, r = R.iloc[:i], R.iloc[i]
        row = {"対象期": r.対象期, "実績": r.実績}
        if i >= min_obs:
            c = float((past.g - (past.実績 - past.P)).mean())
            row["S0 前期＋成長率−定数"] = r.P + r.g - c
            row["S1 S0＋前期の成長率の改定"] = row["S0 前期＋成長率−定数"] + (r.g1 - r.g0)
            row["定数c（潜在成長率の四半期分）"] = c
        if i >= min_reg:
            X = np.column_stack([np.ones(len(past)), past.P, past.g0, past.g1, past.g])
            beta = np.linalg.lstsq(X, past.実績.values, rcond=None)[0]
            row["S2 回帰（過去の回で推計）"] = float(np.array([1, r.P, r.g0, r.g1, r.g]) @ beta)
        out.append(row)
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", type=float, help="前回号の前期のギャップ（P）")
    ap.add_argument("--g", type=float, help="今期の成長率（1次速報、前期比％）")
    ap.add_argument("--g0", type=float, help="前期の成長率（前期の2次速報時点）")
    ap.add_argument("--g1", type=float, help="前期の成長率（今期の1次速報時点）")
    a = ap.parse_args()
    R = dataset()
    B = backtest(R)
    meth = [c for c in B.columns if c[:2] in ("S0", "S1", "S2")]
    # 比較用：モデルの接続値（backtest_realtime2 の結果があれば）
    f = ROOT / "results" / "backtest_realtime2_cao.xlsx"
    if f.exists():
        A = pd.read_excel(f, sheet_name="各期")[["対象期", "A 接続（現行）"]]
        B = B.merge(A, on="対象期", how="left")
        meth.append("A 接続（現行）")
    rows = []
    for lab, m in [("比較できる全回", B[meth].notna().all(axis=1)),
                   ("2023年以降", B[meth].notna().all(axis=1) & (pd.PeriodIndex(B.対象期, freq="Q") >= pd.Period("2023Q1", "Q")))]:
        for c in meth:
            e = (B.loc[m, c] - B.loc[m, "実績"])
            rows.append({"期間": lab, "手法": c, "RMSE": round(float(np.sqrt((e ** 2).mean())), 3),
                         "平均誤差": round(float(e.mean()), 3), "最大誤差": round(float(e.abs().max()), 3), "回数": int(e.notna().sum())})
    S = pd.DataFrame(rows)
    X = np.column_stack([np.ones(len(R)), R.P, R.g0, R.g1, R.g])
    beta = np.linalg.lstsq(X, R.実績.values, rcond=None)[0]
    coef = pd.DataFrame({"項目": ["定数", "P（前期のギャップ）", "g0（前期の成長率・前回）", "g1（前期の成長率・今回）", "g（今期の成長率）"],
                         "係数（全回で推計）": beta.round(3), "理屈の値": ["−潜在成長率/4", 1, -1, 1, 1]})
    pd.set_option("display.width", 220)
    print(S.to_string(index=False))
    print("\n", coef.to_string(index=False))
    c_now = float((R.g - (R.実績 - R.P)).mean())
    print(f"\n定数c（全回の平均）＝{c_now:.3f}（年率換算 {4 * c_now:.2f}%）")
    if a.now is not None and a.g is not None:
        s0 = a.now + a.g - c_now
        s1 = s0 + ((a.g1 - a.g0) if a.g0 is not None and a.g1 is not None else 0.0)
        print(f"\n予測：S0 = {s0:.2f}%、S1 = {s1:.2f}%")
    write_excel(ROOT / "results" / "simple_nowcast_backtest.xlsx",
                {"まとめ": S, "係数": coef, "各期": B.round(3), "データ": R.round(3)})


if __name__ == "__main__":
    main()
