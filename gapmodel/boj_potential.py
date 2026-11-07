"""日銀方式の潜在成長率（TFP込み）と、TFP込みの潜在GDP。

日銀は需給ギャップ（資本・労働の投入ギャップの加重和）とは別に、生産関数から潜在成長率を出している：
  ln Y* = ln A* + (1−α) ln K + α (ln E* + ln h*)
  TFP（ソロー残差） ln A = ln Y − (1−α)(ln K + 資本投入ギャップ) − α(ln E + ln h)  … ln A* はそのHP（λ=1600）
  K：固定資本ストック（無形含む全体、前期末）、E*：潜在就業者（就業者から労働力率・就業率ギャップを除く）、
  h*：労働時間のトレンド（需給ギャップの労働時間ギャップと同じHP）。
日銀の公表（年度半期の前年比と寄与度）と比べた誤差（1996年度以降、RMSE）：潜在成長率0.12、TFP 0.06、資本 0.06、
労働時間 0.09、就業者 0.12（2026-11-08）。潜在GDPの水準と TFP込みのギャップ（実際のGDP−潜在GDP）は日銀は公表していない。
TFP込みのギャップ＝日銀の需給ギャップ＋TFPの循環（ln A − ln A*）。
GDP速報がまだない期は、潜在TFPを直近の伸びで延長して潜在成長率を出す（TFP込みギャップは出せない）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import boj
from .capital import split_stock
from .tools import hp


def half_mean(s: pd.Series) -> pd.Series:
    """四半期系列 → 年度半期（上期＝4-9月＝Q2・Q3、下期＝10-3月＝Q4・翌Q1）の平均。2四半期そろった半期のみ。"""
    q = s.index
    df = pd.DataFrame({"v": s.values, "FY": np.where(q.quarter == 1, q.year - 1, q.year),
                       "H": np.where(np.isin(q.quarter, [2, 3]), 1, 2)})
    g = df.groupby(["FY", "H"]).v
    return g.mean()[g.count() == 2]


def potential(d, res: pd.DataFrame, info: dict, cfg, lam_tfp: float = 1600) -> tuple[pd.DataFrame, pd.DataFrame]:
    """戻り値：(四半期, 年度半期)。四半期：潜在GDP（対数×100）、TFP、寄与度（前年比）、TFP込みギャップ。
    年度半期：潜在成長率と寄与度（前年比、%）。"""
    a, qi = info["alpha"], res.index
    lnY = np.log(d["gdp"].astype(float)).reindex(qi)
    lnE = np.log(d["emp"]).reindex(qi)
    lnEs = lnE - (res.lgap_lfpr + res.lgap_emp) / 100
    h, _ = boj.lfs_hours_sa(d, cfg.end, True, cfg.tc_forecast_ext)
    lnh = np.log(h).reindex(qi)
    lnhs = hp(np.log(h), cfg.hp_lambda).reindex(qi)
    K = split_stock(d, cfg.stock_last, cfg.end, lag=0)
    lnK = np.log(K.total.shift(1)).reindex(qi)
    lnA = lnY - (1 - a) * lnK - a * (lnEs + lnhs) - res.gap / 100
    lnAs = hp(lnA.dropna(), lam_tfp).reindex(qi)
    # GDP速報前の期（TFPが計算できない期）は、潜在TFPを直近の伸びで延長する
    last = lnAs.last_valid_index()
    if last is not None and last < qi[-1]:
        step = float(lnAs.loc[last] - lnAs.loc[last - 1])
        for q in pd.period_range(last + 1, qi[-1], freq="Q"):
            lnAs.loc[q] = lnAs.loc[q - 1] + step
    comp = {"tfp": lnAs, "cap": (1 - a) * lnK, "hours": a * lnhs, "emp": a * lnEs}
    lnYs = sum(comp.values())
    Q = pd.DataFrame({"潜在GDP（対数×100）": 100 * lnYs, "TFP（対数×100）": 100 * lnA, "潜在TFP（対数×100）": 100 * lnAs,
                      "潜在成長率（前年比）": 100 * lnYs.diff(4),
                      "寄与：TFP": 100 * lnAs.diff(4), "寄与：資本": 100 * comp["cap"].diff(4),
                      "寄与：労働時間": 100 * comp["hours"].diff(4), "寄与：就業者": 100 * comp["emp"].diff(4),
                      "TFP込みギャップ（実際のGDP−潜在GDP）": 100 * (lnY - lnYs),
                      "TFPの循環（ln A − ln A*）": 100 * (lnA - lnAs)}, index=qi)
    H = pd.DataFrame({k: (half_mean(v) - half_mean(v).groupby(level=1).shift(1)) * 100 for k, v in comp.items()})
    H["pot"] = H.sum(axis=1, skipna=False)
    return Q, H[["pot", "tfp", "cap", "hours", "emp"]]
