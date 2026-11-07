"""推計結果のExcelから、内閣府GDPギャップと日銀需給ギャップの図を作る。

使い方:
    python estimate_gdp_gap.py --end 2026Q2
    python estimate_boj_gap.py --end 2026Q2
    python plot_gaps.py --end 2026Q2          # → results/gaps_2026Q2.png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent

# 色（既定パレットのカテゴリ色 1〜3 番、文字色、背景）
C_PUB, C_REC, C_MODEL = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def jp_font():
    for f in font_manager.findSystemFonts():
        name = Path(f).name
        if any(k in name for k in ("NotoSansCJK", "NotoSansJP", "YuGoth", "meiryo", "Meiryo", "msgothic")):
            font_manager.fontManager.addfont(f)
            return font_manager.FontProperties(fname=f).get_name()
    return None


def load(path: Path) -> pd.DataFrame:
    h = pd.read_excel(path, sheet_name="時系列")
    h = h.rename(columns={h.columns[0]: "q"})
    h["q"] = pd.PeriodIndex(h["q"].astype(str), freq="Q")
    return h.set_index("q")


def style(ax, title):
    ax.set_facecolor(SURF)
    ax.set_title(title, loc="left", fontsize=13, color=INK, pad=10, fontweight="bold")
    ax.axhline(0, color=INK2, lw=0.8)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.set_ylabel("%", color=INK2, fontsize=9, rotation=0, labelpad=10)


def end_label(ax, x, y, text, color, dy=0):
    ax.plot([x], [y], "o", ms=5, color=color, mec=SURF, mew=1.5, zorder=5)
    ax.annotate(text, (x, y), xytext=(8, dy), textcoords="offset points", va="center", fontsize=9, color=INK)


def main():
    ap = argparse.ArgumentParser(description="ギャップの図を作る")
    ap.add_argument("--end", default="2026Q2")
    ap.add_argument("--start", default="2000Q1")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    fam = jp_font()
    if fam:
        plt.rcParams["font.family"] = fam
    cao = load(ROOT / "results" / f"gdp_gap_{a.end}.xlsx").loc[a.start:a.end]
    boj = load(ROOT / "results" / f"boj_gap_{a.end}.xlsx").loc[a.start:a.end]
    # 内閣府の推奨値（公表値への接続値）＝前期の公表値＋モデルの前期→当期変化
    rec = cao["GDPギャップ（公表）"].shift(1) + cao["GDPギャップ（モデル）"].diff()

    fig, axes = plt.subplots(2, 1, figsize=(11, 8.2), facecolor=SURF, sharex=False)
    x = cao.index.to_timestamp(how="end")
    ax = axes[0]
    style(ax, "内閣府 GDPギャップ")
    ax.plot(x, cao["GDPギャップ（公表）"], color=C_PUB, lw=2, label="公表値")
    ax.plot(x, rec, color=C_REC, lw=2, label="再現：推奨値（前期の公表値＋モデルの変化幅）")
    ax.plot(x, cao["GDPギャップ（モデル）"], color=C_MODEL, lw=1.4, ls=(0, (4, 2)), label="再現：モデル水準")
    end_label(ax, x[-1], cao["GDPギャップ（公表）"].iloc[-1], f"公表 {cao['GDPギャップ（公表）'].iloc[-1]:.1f}", C_PUB, -9)
    end_label(ax, x[-1], rec.iloc[-1], f"推奨 {rec.iloc[-1]:.2f}", C_REC, 4)
    end_label(ax, x[-1], cao["GDPギャップ（モデル）"].iloc[-1], f"モデル {cao['GDPギャップ（モデル）'].iloc[-1]:.2f}", C_MODEL, 6)
    ax.legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK)

    xb = boj.index.to_timestamp(how="end")
    ax = axes[1]
    style(ax, "日銀 需給ギャップ")
    ax.plot(xb, boj["需給ギャップ（公表）"], color=C_PUB, lw=2, label="公表値")
    ax.plot(xb, boj["需給ギャップ（モデル）"], color=C_REC, lw=2, label="再現：モデル水準（推奨）")
    raw_col = "需給ギャップ（参考：不規則変動を除かない）"
    if raw_col in boj:
        ax.plot(xb, boj[raw_col], color=C_MODEL, lw=1.4, ls=(0, (4, 2)),
                label="参考：労働時間の不規則変動を除かない場合")
        end_label(ax, xb[-1], boj[raw_col].iloc[-1], f"参考 {boj[raw_col].iloc[-1]:.2f}", C_MODEL, -21)
    end_label(ax, xb[-1], boj["需給ギャップ（公表）"].iloc[-1], f"公表 {boj['需給ギャップ（公表）'].iloc[-1]:.2f}", C_PUB, 7)
    end_label(ax, xb[-1], boj["需給ギャップ（モデル）"].iloc[-1], f"再現 {boj['需給ギャップ（モデル）'].iloc[-1]:.2f}", C_REC, -7)
    ax.legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK)

    for ax in axes:
        ax.set_xlim(x[0], x[-1] + pd.Timedelta(days=420))
    fig.text(0.01, 0.005, f"出所：内閣府、日本銀行の公表値と、手元の再現モデル（{a.end}まで、四半期）。"
             "内閣府の推奨値は前期の公表値にモデルの前期→当期変化幅を足したもの。",
             fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1), h_pad=2.5)
    out = Path(a.out) if a.out else ROOT / "results" / f"gaps_{a.end}.png"
    fig.savefig(out, dpi=200, facecolor=SURF)
    print(out)


if __name__ == "__main__":
    main()
