"""内閣府GDPギャップのリアルタイム検証（拡張版）：前期のGDP改定を補正する接続値を試す。

各1次QE（対象期 t）について、そのとき公表されていた実質GDP（t の1次速報）で推計し、
内閣府の1次QE後の公表値と比べる。GDP以外の統計は最新の公表値を対象期末で切って使う（月次統計の改定は無視）。

  A   接続：前回号の前期値 ＋ モデルの前期→当期変化幅（現行の推奨）
  A1  A ＋ 前期のGDP改定率（前回＝前期の2次速報 → 今回＝今期の1次速報、％）
  A2  A ＋ 前期のGDP改定率から、直近3年（t-13〜t-2）の改定率の平均を引いたもの
      （基準改定・年次推計で過去も含め水準がまとめて変わる分は、潜在GDPにも吸収されるため除く）
  B   A ＋ モデルによる前期の改定幅（前期の2次速報・前期末までのデータで推計したモデルの前期値からの変化）
  D   据え置き：前回号の前期値
GDPの過去公表値は data/vintages/esri/gaku-jk<yyq><1|2>.csv（fetch_qe_vintages.py で取得）。
出力：results/backtest_realtime2_cao.xlsx
"""
import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from gapmodel import data, cao, ingest as ig
from gapmodel.config import CAOConfig
from gapmodel.vintages import load_cao_vintages
from gapmodel.report import write_excel

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent
VDIR = ROOT / "data" / "vintages" / "esri"


def gdp_vintage(q: pd.Period, kind: int) -> pd.Series | None:
    f = VDIR / f"gaku-jk{q.year % 100:02d}{q.quarter}{kind}.csv"
    return ig.parse_esri_gaku(f.read_bytes()) if f.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="q0", default="2018Q1")
    ap.add_argument("--to", dest="q1", default="2026Q2")
    a = ap.parse_args()
    D = data.load_all()
    issues, long = load_cao_vintages()
    cur = long[long.推計 == "今回"]

    def pub(no, q):
        s = cur[cur.号 == no].set_index("期").GDPギャップ
        return float(s.get(q, np.nan))

    rows = []
    for t in pd.period_range(a.q0, a.q1, freq="Q"):
        first = issues[(issues.対象期 == t) & (issues.速報 == "1次")]
        g1, g2p = gdp_vintage(t, 1), gdp_vintage(t - 1, 2)
        if not len(first) or g1 is None or g2p is None:
            print(f"{t}: 公表値またはGDPの過去公表値がないため省略")
            continue
        no1 = int(first.号.iloc[0])
        prev = issues[issues.公表日 < first.公表日.iloc[0]]
        no0 = int(prev.号.iloc[-1])
        actual, pub_prev = pub(no1, t), pub(no0, t - 1)
        if np.isnan(actual) or np.isnan(pub_prev):
            print(f"{t}: 公表値の表に該当期がないため省略")
            continue
        d = data.truncate_to(D, str(t))
        d["gdp"] = g1.astype(float)
        brk_end = str(min(t, pd.Period("2019Q4", "Q")))   # 屈折点の探索期間は、その時点までのデータに限る
        try:
            res, _ = cao.run(d, CAOConfig(end=str(t), stock_last=str(t - 1), capacity_break_sample_end=brk_end))
        except Exception as e:  # noqa: BLE001  （標本が短い時期は生産能力関数の屈折点が探せない）
            print(f"{t}: 推計できないため省略（{type(e).__name__}）")
            continue
        m_t, m_t1 = float(res.gap.loc[t]), float(res.gap.loc[t - 1])
        # 前回号の時点（前期の2次速報、前期末までのデータ）でのモデルの前期値
        d0 = data.truncate_to(D, str(t - 1))
        d0["gdp"] = g2p.astype(float)
        try:
            r0, _ = cao.run(d0, CAOConfig(end=str(t - 1), stock_last=str(t - 2),
                                          capacity_break_sample_end=str(min(t - 1, pd.Period("2019Q4", "Q")))))
            m_prev_old = float(r0.gap.loc[t - 1])
        except Exception:  # noqa: BLE001
            m_prev_old = np.nan
        rv = (np.log(g1) - np.log(g2p)).dropna() * 100            # 期ごとの改定率（％）
        rev = float(rv.loc[t - 1])
        base = float(rv.loc[t - 13:t - 2].mean())
        A = pub_prev + m_t - m_t1
        rows.append({"対象期": str(t), "号": no1, "内閣府 1次QE後（実績）": actual,
                     "A 接続（現行）": A, "A1 接続＋前期のGDP改定率": A + rev,
                     "A2 接続＋前期のGDP改定率（直近3年平均との差）": A + rev - base,
                     "B 接続＋モデルによる前期の改定幅": A + (m_t1 - m_prev_old),
                     "D 据え置き": pub_prev, "モデルによる前期の改定幅": m_t1 - m_prev_old,
                     "前回号の前期値": pub_prev, "内閣府による前期の改定幅": pub(no1, t - 1) - pub_prev,
                     "前期のGDP改定率(%)": rev, "直近3年の平均改定率(%)": base,
                     "モデルの前期→当期": m_t - m_t1, "内閣府の前期→当期": actual - pub(no1, t - 1)})
        print(f"{t}: 実績 {actual:+.1f}  A {A:+.2f}  前期改定 内閣府 {rows[-1]['内閣府による前期の改定幅']:+.1f} / GDP {rev:+.2f}（差 {rev - base:+.2f}）")

    R = pd.DataFrame(rows)
    meth = [c for c in R.columns if c.split(" ")[0] in ("A", "A1", "A2", "B", "D")]
    err = pd.DataFrame({c: R[c] - R["内閣府 1次QE後（実績）"] for c in meth})

    def stats(mask, label):
        e = err[mask]
        return pd.DataFrame({"期間": label, "手法": meth,
                             "RMSE": [round(float(np.sqrt((e[c] ** 2).mean())), 3) for c in meth],
                             "平均誤差": [round(float(e[c].mean()), 3) for c in meth],
                             "最大誤差": [round(float(e[c].abs().max()), 3) for c in meth],
                             "回数": [int(e[c].notna().sum()) for c in meth]})
    q = pd.PeriodIndex(R.対象期, freq="Q")
    # 誤差の内訳：内閣府による前期の改定（接続の基準のずれ）と、前期→当期変化幅の差
    dec = pd.DataFrame([("内閣府による前期の改定（符号反転）", round(float(np.sqrt((R["内閣府による前期の改定幅"] ** 2).mean())), 3)),
                        ("前期→当期の変化幅の差（モデル−内閣府）",
                         round(float(np.sqrt(((R["モデルの前期→当期"] - R["内閣府の前期→当期"]) ** 2).mean())), 3))],
                       columns=["接続値（A）の誤差の内訳", "RMSE"])
    summ = pd.concat([stats(q >= pd.Period("2018Q1", "Q"), "全期間"),
                      stats(q >= pd.Period("2023Q1", "Q"), "2023年以降"),
                      stats((q < pd.Period("2020Q1", "Q")) | (q > pd.Period("2021Q4", "Q")), "コロナ期（2020〜21年）を除く")])
    # 前期の改定：内閣府の改定幅と GDP改定率の関係
    x1, x2, y = R["前期のGDP改定率(%)"], R["前期のGDP改定率(%)"] - R["直近3年の平均改定率(%)"], R["内閣府による前期の改定幅"]
    x3 = R["モデルによる前期の改定幅"]
    ok = x3.notna()
    rel = pd.DataFrame([
        ("モデルによる前期の改定幅", round(float(np.corrcoef(x3[ok], y[ok])[0, 1]), 2), round(float(np.polyfit(x3[ok], y[ok], 1)[0]), 2)),
        ("GDP改定率", round(float(np.corrcoef(x1, y)[0, 1]), 2), round(float(np.polyfit(x1, y, 1)[0]), 2)),
        ("GDP改定率（直近3年平均との差）", round(float(np.corrcoef(x2, y)[0, 1]), 2), round(float(np.polyfit(x2, y, 1)[0]), 2)),
    ], columns=["説明に使う変数", "内閣府の前期改定幅との相関", "傾き"])
    pd.set_option("display.width", 250)
    print("\n", summ.to_string(index=False))
    print("\n前期の改定\n", rel.to_string(index=False))
    print("\n", dec.to_string(index=False))
    write_excel(ROOT / "results" / "backtest_realtime2_cao.xlsx",
                {"まとめ": summ, "誤差の内訳": dec, "前期の改定": rel, "各期": R.round(3),
                 "誤差": pd.concat([R[["対象期"]], err.round(3)], axis=1)})


if __name__ == "__main__":
    main()
