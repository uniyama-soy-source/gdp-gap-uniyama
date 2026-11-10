"""DP/26-8（内閣府 2026）の図7・図8から、線の座標を読み取って数値に戻す。

図はベクター（線の座標がPDFに入っている）なので、目盛りで換算すれば元の数値がほぼそのまま戻る。
  図8（PDF15枚目、印字p.13）：製造業の資本稼働率（四半期、1994Q1〜2025Q4）と潜在資本稼働率
  図7（PDF14枚目、印字p.12）：鉱工業生産の原数値・季節調整値、暫定生産能力（月次）、最大生産能力（2015〜2025年）
出力：data/published/dp268_fig8_mfg_util.csv、data/published/dp268_fig7_capacity.csv

使い方：  pip install pdfplumber  のあと  python tools_read_dp268_figs.py [PDFの場所]
（PDFは https://www5.cao.go.jp/keizai3/discussion-paper/dp268.pdf 。既定の場所は docs/dp268.pdf。PDFはコミットしない）
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pdfplumber

from gapmodel import data

ROOT = Path(__file__).resolve().parent
PUB = ROOT / "data" / "published"
BLUE, ORANGE, GREY, YELLOW = (0.36, 0.61, 0.84), (0.93, 0.49, 0.19), (0.65, 0.65, 0.65), (1.0, 0.75, 0.0)


def _curve(page, color, min_pts):
    c = [np.array(x["pts"]) for x in page.curves
         if tuple(round(v, 2) for v in (x.get("stroking_color") or ())) == color and len(x["pts"]) >= min_pts]
    if not c:
        raise SystemExit("図の線が見つかりません（PDFの版が違う可能性）")
    return max(c, key=len)


def main():
    pdf = pdfplumber.open(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "dp268.pdf")

    # ---- 図8：縦軸の目盛りの文字（65〜100）の位置で換算
    p = pdf.pages[14]
    ticks = [(float(w["text"]), (w["top"] + w["bottom"]) / 2) for w in p.extract_words()
             if w["text"] in ("65", "70", "75", "80", "85", "90", "95", "100") and w["x0"] < 110 and 330 < w["top"] < 460]
    c8 = np.polyfit([t[1] for t in ticks], [t[0] for t in ticks], 1)
    b = _curve(p, BLUE, 100)
    u = pd.Series(np.polyval(c8, b[:, 1]), index=pd.period_range("1994Q1", periods=len(b), freq="Q"))
    us = float(np.polyval(c8, _curve(p, ORANGE, 2)[:, 1]).mean())
    out8 = pd.DataFrame({"q": u.index.astype(str), "util_mfg": u.round(2).values, "potential_util_mfg": round(us, 2)})
    out8.to_csv(PUB / "dp268_fig8_mfg_util.csv", index=False)
    print(f"図8：{u.index[0]}〜{u.index[-1]}（{len(u)}期）、潜在稼働率 {us:.2f}%、最小 {u.idxmin()} {u.min():.1f}、最大 {u.idxmax()} {u.max():.1f}")

    # ---- 図7：青線（原数値）＝鉱工業生産の原指数、を使って縦軸を換算
    p = pdf.pages[13]
    blue, orange, grey, yellow = (_curve(p, c, n) for c, n in ((BLUE, 100), (ORANGE, 100), (GREY, 50), (YELLOW, 20)))
    mi = pd.period_range("2015-01", periods=len(blue), freq="M")
    ipo = data.load_all()["ip_mm_o"].reindex(mi)
    c7 = np.polyfit(blue[:, 1], ipo.values, 1)
    print(f"図7：縦軸の換算の残差（標準偏差） {np.std(ipo.values - np.polyval(c7, blue[:, 1])):.2f}（鉱工業指数のその後の改定を含む）")
    pos = lambda a: np.interp(a[:, 0], blue[:, 0], np.arange(len(blue)))   # noqa: E731  横位置 → 月の番号
    interp = lambda a: np.interp(np.arange(len(blue)), pos(a), np.polyval(c7, a[:, 1]))   # noqa: E731
    out7 = pd.DataFrame({"month": mi.astype(str), "orig": np.polyval(c7, blue[:, 1]).round(2),
                         "sa": np.polyval(c7, orange[:, 1]).round(2),
                         "wharton_capacity": interp(grey).round(2), "max_capacity": interp(yellow).round(2)})
    out7.to_csv(PUB / "dp268_fig7_capacity.csv", index=False)
    print(f"図7：{mi[0]}〜{mi[-1]}、暫定生産能力 {out7.wharton_capacity.iloc[0]}→{out7.wharton_capacity.max()}→{out7.wharton_capacity.iloc[-1]}、"
          f"最大生産能力 {out7.max_capacity.iloc[0]}→{out7.max_capacity.iloc[-1]}")


if __name__ == "__main__":
    main()
