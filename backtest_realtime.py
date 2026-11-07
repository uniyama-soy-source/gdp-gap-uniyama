"""内閣府GDPギャップのリアルタイム検証：各1次QE時点の実質GDP（過去公表値）で推計し、内閣府の1次QE後の公表値と比べる。

前提（簡略化）：GDP以外の統計は最新の公表値を使い、対象期末までで切る（月次統計の改定は無視）。
固定資本ストックは対象期の前期まで（内閣府の1次QE後公表時点で入手可能だった範囲）。
"""
import warnings
from pathlib import Path
import numpy as np
import pandas as pd

from gapmodel import data, cao
from gapmodel.config import CAOConfig
from gapmodel.data import _read_series_file
from gapmodel.vintages import load_cao_vintages
from gapmodel.report import write_excel

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent
GDPV = _read_series_file(ROOT / "data" / "vintages" / "qe_real_gdp_1st.txt")
D = data.load_all()
issues, long = load_cao_vintages()
cur = long[long.推計 == "今回"]


def pub_value(issue_no, q):
    s = cur[(cur.号 == issue_no)].set_index("期").GDPギャップ
    return float(s.get(q, np.nan))


def run_vintage(key):
    g = GDPV[key].astype(float)
    t = g.index[-1]
    d = data.truncate_to(D, str(t))
    d["gdp"] = g
    res, _ = cao.run(d, CAOConfig(end=str(t), stock_last=str(t - 1)))
    return t, res


runs = {k: run_vintage(k) for k in GDPV}
rows = []
keys = sorted(GDPV)
for i, k in enumerate(keys):
    t, res = runs[k]
    first = issues[(issues.対象期 == t) & (issues.速報 == "1次")]
    if not len(first):
        continue
    no_first = int(first.号.iloc[0])
    before = issues[issues.公表日 < first.公表日.iloc[0]]
    no_prev = int(before.号.iloc[-1])                         # 直前の号（前期の2次QE後）
    prev_first = issues[(issues.対象期 == t - 1) & (issues.速報 == "1次")]
    actual = pub_value(no_first, t)
    pub_prev = pub_value(no_prev, t - 1)                      # 直前号の前期値
    pub_prev_1st = pub_value(int(prev_first.号.iloc[0]), t - 1) if len(prev_first) else np.nan
    m_t, m_t1 = float(res.gap.loc[t]), float(res.gap.loc[t - 1])
    row = {"対象期": str(t), "GDP公表値": k, "内閣府 1次QE後（実績）": actual,
           "A 接続（前回公表値＋モデル変化幅）": pub_prev + m_t - m_t1,
           "D 据え置き（前回公表値）": pub_prev, "C モデル水準": m_t,
           "前回公表値（前期）": pub_prev, "内閣府が前期を改定した幅": pub_value(no_first, t - 1) - pub_prev,
           "モデルの前期→当期": m_t - m_t1}
    if i > 0:
        tp, resp = runs[keys[i - 1]]
        if tp == t - 1:
            # B 前回の1次QE時点のモデル値からの変化（前期の改定もモデルで織り込む）、基準は前期の1次QE後の公表値
            row["B 接続（前期1次公表値＋モデルの改定込み変化幅）"] = pub_prev_1st + m_t - float(resp.gap.loc[t - 1])
            row["モデルによる前期の改定幅"] = m_t1 - float(resp.gap.loc[t - 1])
    rows.append(row)

R = pd.DataFrame(rows)
meth = [c for c in R.columns if c[:2] in ("A ", "B ", "C ", "D ")]
err = pd.DataFrame({c: R[c] - R["内閣府 1次QE後（実績）"] for c in meth})
summ = pd.DataFrame({"手法": meth,
                     "RMSE": [round(float(np.sqrt((err[c].dropna() ** 2).mean())), 3) for c in meth],
                     "平均誤差": [round(float(err[c].dropna().mean()), 3) for c in meth],
                     "最大誤差": [round(float(err[c].dropna().abs().max()), 3) for c in meth],
                     "回数": [int(err[c].notna().sum()) for c in meth]})
pd.set_option("display.width", 250)
print(R.round(2).to_string(index=False))
print("\n誤差（推計−実績）\n", pd.concat([R[["対象期"]], err.round(2)], axis=1).to_string(index=False))
print("\n", summ.to_string(index=False))
write_excel(ROOT / "results" / "backtest_realtime_cao.xlsx",
            {"まとめ": summ, "各期": R.round(3), "誤差": pd.concat([R[["対象期"]], err.round(3)], axis=1)})
