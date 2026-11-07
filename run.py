"""内閣府GDPギャップ・日銀需給ギャップの再現を実行し、公表値と照合する。

使い方:  python run.py            → results/ に CSV・図・照合表を出力
"""
from pathlib import Path
import json
import warnings
import numpy as np
import pandas as pd

from gapmodel import data, cao, boj
from gapmodel.config import CAOConfig, BOJConfig

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)


def stats(a: pd.Series, b: pd.Series, since=None):
    j = pd.concat([a, b], axis=1).dropna()
    if since:
        j = j.loc[pd.Period(since, "Q"):]
    e = j.iloc[:, 0] - j.iloc[:, 1]
    return {"n": len(j), "RMSE": round(float(np.sqrt((e ** 2).mean())), 3),
            "bias": round(float(e.mean()), 3), "corr": round(float(j.corr().iloc[0, 1]), 3),
            "max_abs": round(float(e.abs().max()), 3)}


def main():
    d, pub = data.load_all(), data.load_published()

    # ---- 内閣府 ----
    ccfg = CAOConfig()
    rc, ic = cao.run(d, ccfg)
    pc = pub["cao"]
    rc_y = rc[["pot_growth", "c_tfp", "c_cap", "c_hours", "c_emp"]].rolling(4).mean()  # 前年比（4期平均）に換算
    cao_tab = pd.DataFrame({
        "gap": rc.gap, "gap_pub": pc.gap,
        "pot": rc_y.pot_growth, "pot_pub": pc.pot, "tfp": rc_y.c_tfp, "tfp_pub": pc.tfp,
        "cap": rc_y.c_cap, "cap_pub": pc.cap, "hours": rc_y.c_hours, "hours_pub": pc.hours,
        "emp": rc_y.c_emp, "emp_pub": pc.emp}).loc[rc.index]
    cao_tab.to_csv(OUT / "cao_replication.csv", index_label="q")

    # 感応度：資本稼働率（生産能力関数）の構造転換点の数
    sens = []
    for m, n in [(2, 2), (2, 1), (1, 1), (3, 2)]:
        r, _ = cao.run(d, CAOConfig(capacity_n_breaks=m, capacity_n_breaks_nonmfg=n))
        s_all, s_rec = stats(r.gap, pc.gap), stats(r.gap, pc.gap, "2023Q1")
        sens.append({"breaks(mfg,nonmfg)": f"{m},{n}", "RMSE_all": s_all["RMSE"], "RMSE_2023-": s_rec["RMSE"],
                     "2026Q2": round(float(r.gap.iloc[-1]), 2)})
    sens = pd.DataFrame(sens)

    # ---- 日銀 ----
    bcfg = BOJConfig()
    rb, ib = boj.run(d, pub, bcfg)
    pb, pl = pub["boj"], pub["boj_labor"]
    boj_tab = pd.DataFrame({
        "gap": rb.gap, "gap_pub": pb.boj_gap, "c_kgap": rb.c_kgap, "c_kgap_pub": pb.boj_kgap,
        "c_lgap": rb.c_lgap, "c_lgap_pub": pb.boj_lgap,
        "lgap_lfpr": rb.lgap_lfpr, "lgap_emp": rb.lgap_emp, "lgap_emp_pub": pl.empgap.reindex(rb.index),
        "lgap_hours": rb.lgap_hours, "util_gap_mfg": rb.util_gap_mfg, "util_gap_nonmfg": rb.util_gap_nonmfg,
    }).loc[rb.index]
    boj_tab.to_csv(OUT / "boj_replication.csv", index_label="q")
    rb_raw, _ = boj.run(d, pub, BOJConfig(calibrate_kgap_level=False, vacancy_correction="none"))

    summary = {
        "CAO": {"info": ic, "gap_all": stats(rc.gap, pc.gap), "gap_2023-": stats(rc.gap, pc.gap, "2023Q1"),
                "pot_growth_yoy": stats(rc_y.pot_growth, pc.pot, "1996Q1")},
        "BOJ": {"info": {k: v for k, v in ib.items() if k != "lfpr_breaks"},
                "gap_all": stats(rb.gap, pb.boj_gap), "gap_2023-": stats(rb.gap, pb.boj_gap, "2023Q1"),
                "kgap": stats(rb.c_kgap, pb.boj_kgap), "lgap": stats(rb.c_lgap, pb.boj_lgap),
                "empgap": stats(rb.lgap_emp, pl.empgap),
                "gap_all_uncalibrated": stats(rb_raw.gap, pb.boj_gap)},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    # ---- 表示 ----
    pd.set_option("display.width", 200)
    print("=== 内閣府 GDPギャップ（%）: 最新8期 ===")
    print(cao_tab[["gap", "gap_pub", "pot", "pot_pub", "tfp", "tfp_pub", "cap", "cap_pub", "hours", "hours_pub", "emp", "emp_pub"]].tail(8).round(2).to_string())
    print("\n感応度（生産能力関数の転換点数）\n", sens.to_string(index=False))
    print("\n=== 日銀 需給ギャップ（%）: 最新8期 ===")
    print(boj_tab[["gap", "gap_pub", "c_kgap", "c_kgap_pub", "c_lgap", "c_lgap_pub", "lgap_lfpr", "lgap_emp", "lgap_emp_pub", "lgap_hours"]].tail(8).round(2).to_string())
    print("\n", json.dumps(summary, ensure_ascii=False, indent=1))
    sens.to_csv(OUT / "cao_sensitivity_breaks.csv", index=False)
    chart(cao_tab, boj_tab)


def chart(ct, bt):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in font_manager.findSystemFonts():
        if "NotoSansCJK" in f or "NotoSansJP" in f:
            font_manager.fontManager.addfont(f)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
            break
    fig, ax = plt.subplots(3, 1, figsize=(10, 11))
    x = ct.index.to_timestamp()
    ax[0].plot(x, ct.gap_pub, color="#1f4e79", lw=2, label="内閣府 公表")
    ax[0].plot(x, ct.gap, color="#d9822b", lw=1.4, label="再現")
    ax[0].set_title("内閣府 GDPギャップ（%）")
    xb = bt.index.to_timestamp()
    ax[1].plot(xb, bt.gap_pub, color="#1f4e79", lw=2, label="日銀 公表")
    ax[1].plot(xb, bt.gap, color="#d9822b", lw=1.4, label="再現")
    ax[1].set_title("日銀 需給ギャップ（%）")
    ax[2].plot(xb, bt.c_kgap_pub, color="#1f4e79", lw=2, label="資本 公表")
    ax[2].plot(xb, bt.c_kgap, color="#1f4e79", lw=1, ls="--", label="資本 再現")
    ax[2].plot(xb, bt.c_lgap_pub, color="#2e7d32", lw=2, label="労働 公表")
    ax[2].plot(xb, bt.c_lgap, color="#2e7d32", lw=1, ls="--", label="労働 再現")
    ax[2].set_title("日銀 寄与度の内訳（%pt）")
    for a in ax:
        a.axhline(0, color="grey", lw=0.6)
        a.legend(loc="lower left", fontsize=9, frameon=False)
        a.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "replication.png", dpi=130)


if __name__ == "__main__":
    main()
