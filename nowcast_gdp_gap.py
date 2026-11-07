"""超早期推計：GDP速報の前に、実質GDPの民間予測を置いて内閣府GDPギャップを推計する。

使い方:
    python nowcast_gdp_gap.py --end 2026Q3 --gdp-saar 0.08 --source "ESPフォーキャスト2026年10月調査"

やること
  1. 対象期の実質GDP（季調）を「前期の実質GDP ×（1＋予測の前期比年率）^(1/4)」で置く。
  2. 1次QEより後に出る月次統計（第3次産業活動指数の対象期最終月など）が欠けていれば補う：
     季調値は直近2か月の平均、原指数は季調値×過去同月の（原指数／季調値）の平均。
  3. estimate_gdp_gap と同じ推奨設定で推計し、公表値への接続値を出す。
  4. GDP予測が外れた場合の感応度（前期比年率 −2%〜＋2%）も出す。
出力: results/nowcast_gdp_gap_<期>.xlsx と標準出力。
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from gapmodel import data, cao
from gapmodel.config import CAOConfig
from gapmodel.report import anchored, gap_amount, pot_table, write_excel

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent


def fill_month_end(sa: pd.Series, orig: pd.Series | None, endm: pd.Period):
    """対象期の最終月まで、季調値を直近2か月平均で、原指数を季節比で延長する。補った月を返す。"""
    filled = []
    sa, orig = sa.copy(), (orig.copy() if orig is not None else None)
    while sa.dropna().index[-1] < endm:
        m = sa.dropna().index[-1] + 1
        sa.loc[m] = sa.dropna().iloc[-2:].mean()
        if orig is not None:
            ratio = (orig / sa).dropna()
            ratio = ratio[ratio.index.month == m.month]
            orig.loc[m] = sa.loc[m] * ratio.mean()
        filled.append(str(m))
    return sa.sort_index(), (orig.sort_index() if orig is not None else None), filled


def main():
    ap = argparse.ArgumentParser(description="民間予測を使った内閣府GDPギャップの超早期推計")
    ap.add_argument("--end", default="2026Q3")
    ap.add_argument("--gdp-saar", type=float, required=True, help="対象期の実質GDP 前期比年率（%）の予測")
    ap.add_argument("--source", default="民間予測", help="予測の出典（出力に記録）")
    ap.add_argument("--stock-last", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    endq = pd.Period(a.end, "Q")
    endm = endq.asfreq("M", how="end")
    d = data.load_all()
    log = []

    # 1. 月次統計の欠けを補う（第3次産業活動指数、鉱工業）
    for sa_k, o_k, label in [("ita_s", "ita_o", "第3次産業活動指数"), ("ip_mm_s", "ip_mm_o", "鉱工業生産指数")]:
        d[sa_k], d[o_k], f = fill_month_end(d[sa_k], d[o_k], endm)
        if f:
            log.append(f"{label}：{', '.join(f)} を直近2か月平均（季調）で補完")
    for k, label in [("h_sa", "毎勤 総実労働時間"), ("e_sa", "毎勤 常用雇用"), ("emfg_sa", "毎勤 常用雇用（製造業）")]:
        d[k], _, f = fill_month_end(d[k], None, endm)
        if f:
            log.append(f"{label}：{', '.join(f)} を直近2か月平均で補完")

    # 2. 実質GDPを予測で置く
    gdp = d["gdp"].astype(float).copy()
    if gdp.index[-1] >= endq:
        raise SystemExit(f"{endq} の実質GDPは既に入っています（超早期推計は不要）")
    if gdp.index[-1] != endq - 1:
        raise SystemExit("前期の実質GDPがありません")

    def run_with(saar: float):
        g = gdp.copy()
        g.loc[endq] = g.loc[endq - 1] * (1 + saar / 100) ** 0.25
        dd = dict(d)
        dd["gdp"] = g
        cfg = CAOConfig(end=a.end, stock_last=a.stock_last or str(endq - 1))
        res, info = cao.run(dd, cfg)
        return res, info, g

    res, info, g = run_with(a.gdp_saar)
    pub = data.load_published()["cao"]["gap"]
    anc, t0 = anchored(res["gap"], pub, endq)
    log.append(f"実質GDP {endq}：{a.source} の前期比年率 {a.gdp_saar:+.2f}%（前期比 {((1 + a.gdp_saar / 100) ** 0.25 - 1) * 100:+.3f}%）"
               f" → {g.loc[endq]:,.0f}（前期 {g.loc[endq - 1]:,.0f}、単位は10億円）")

    sens = []
    for s in [-2.0, -1.0, 0.0, a.gdp_saar, 1.0, 2.0]:
        r, _, gg = run_with(s)
        an, _ = anchored(r["gap"], pub, endq)
        sens.append({"GDP前期比年率の想定（%）": s, "GDPギャップ（接続値）": round(an, 2),
                     "実額（兆円、実質・年率）": round(gap_amount(float(gg.loc[endq]), an), 1),
                     "GDPギャップ（モデル水準）": round(float(r["gap"].loc[endq]), 2),
                     "潜在成長率（前年比）": round(float(r["pot_growth"].rolling(4).mean().loc[endq]), 2)})
    sens = pd.DataFrame(sens).drop_duplicates().sort_values("GDP前期比年率の想定（%）")
    slope = np.polyfit(sens.iloc[:, 0], sens["GDPギャップ（接続値）"], 1)[0]

    yoy = res["pot_growth"].rolling(4).mean()
    summary = pd.DataFrame([
        ("推計対象期", str(endq)),
        ("GDP予測", f"{a.source}：前期比年率 {a.gdp_saar:+.2f}%"),
        ("GDPギャップ（推奨：接続値）", round(anc, 2)),
        ("　接続の基準期（公表値）", f"{t0}：{pub.loc[t0]:.1f}"),
        ("　前期→当期の変化幅（モデル）", round(float(res['gap'].loc[endq] - res['gap'].loc[t0]), 2)),
        ("　実額（兆円、実質・年率）", round(gap_amount(float(g.loc[endq]), anc), 1)),
        ("　　計算に使った実質GDP（兆円、年率、予測で置いた値）", round(float(g.loc[endq]) / 1000, 1)),
        ("GDPギャップ（モデル水準）", round(float(res['gap'].loc[endq]), 2)),
        ("潜在成長率（前年比）", round(float(yoy.loc[endq]), 2)),
        ("GDP予測が前期比年率1%外れたときのギャップの変化", f"約{slope:.2f}pt"),
    ], columns=["項目", "値"])
    hist = pd.DataFrame({"GDPギャップ（モデル）": res.gap, "GDPギャップ（公表）": pub.reindex(res.index),
                         "潜在成長率（前年比）": yoy, "実質GDP": g.reindex(res.index)}).round(3)
    notes = pd.DataFrame({"補完・前提": log + [f"推計設定：{k}={v}" for k, v in info.items()]})

    out = Path(a.out) if a.out else ROOT / "results" / f"nowcast_gdp_gap_{endq}.xlsx"
    pot = pot_table(res, data.load_published()["cao"], endq)
    write_excel(out, {"サマリー": summary, "潜在成長率と寄与度": pot, "GDP予測の感応度": sens, "時系列": hist.loc["2015Q1":], "補完と前提": notes})
    print(f"=== 内閣府GDPギャップ 超早期推計 {endq} ===")
    print(summary.to_string(index=False))
    print("\n潜在成長率と寄与度（前年比、%・%pt）\n" + pot.to_string(index=False))
    print("\nGDP予測の感応度\n", sens.to_string(index=False))
    print("\n補完と前提\n" + "\n".join(log))
    print(f"\n出力: {out}")


if __name__ == "__main__":
    main()
