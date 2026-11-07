"""資本ストックの分割（内閣府DP/26-8 第3章1)の方法、日銀も同じ分割を使う）。

固定資本ストック速報（民間企業設備・公的固定資産）を、ストックマトリックスから作る暦年末比率で
 ①民間製造業・有形 ②民間非製造業・有形 ③民間・無形 ④公的企業・有形+住宅 ⑤公的企業・無形 に分け、
 製造業有形=①、非製造業有形=②+④、無形=③+⑤ とする。
"""
import numpy as np
import pandas as pd
from .tools import interp_ratio_q4


def extend_stock(s: pd.Series, last: str, end: str) -> pd.Series:
    """公表時点で未公表の期を、直近4四半期の平均伸び率（前期比）で延長する。"""
    s = s.loc[:pd.Period(last, "Q")]
    g = np.log(s).diff().iloc[-4:].mean()
    idx = pd.period_range(s.index[0], pd.Period(end, "Q"), freq="Q")
    out = s.reindex(idx)
    for i in range(len(s), len(idx)):
        out.iloc[i] = out.iloc[i - 1] * np.exp(g)
    return out


def split_stock(d: dict, stock_last: str, end: str, lag: int = 1) -> pd.DataFrame:
    priv = extend_stock(d["stock_priv"], stock_last, end)
    pub = extend_stock(d["stock_pub"], stock_last, end)
    m = d["matrix"]
    idx = priv.index
    s_privI = interp_ratio_q4(m.privI / (m.privT + m.privI), idx)
    s_mfgT = interp_ratio_q4(m.mfgT / m.privT, idx)
    s_pe = interp_ratio_q4(m.pubEnt_all / m.public_all, idx)
    s_peI = interp_ratio_q4(m.pubEnt_intang / m.pubEnt_all, idx)
    out = pd.DataFrame(index=idx)
    out["mfgT"] = priv * (1 - s_privI) * s_mfgT
    out["nmT"] = priv * (1 - s_privI) * (1 - s_mfgT) + pub * s_pe * (1 - s_peI)
    out["intang"] = priv * s_privI + pub * s_pe * s_peI
    out["total"] = out.sum(axis=1)
    if lag:
        out = out.shift(lag)
    return out.dropna()
