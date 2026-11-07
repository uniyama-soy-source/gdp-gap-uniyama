"""内閣府（経済財政分析担当）GDPギャップを推計する。

使い方:
    python estimate_gdp_gap.py                      # 既定：2026Q2、推奨設定
    python estimate_gdp_gap.py --end 2026Q3 --stock-last 2026Q2
    python estimate_gdp_gap.py --breaks 1,1          # 生産能力関数の転換点数（製造業,非製造業）を変える

出力: results/gdp_gap_<期>.xlsx（サマリー／時系列／入力の到達状況／設定）と標準出力。

推奨設定（Claude、2026-10-28時点）:
  - 手法は内閣府DP/26-8（並木2026）に従う。生産能力関数の転換点は論文どおり2本（製造業・非製造業とも）。
  - 主な推計値は「公表値への接続値」：前期までの公表GDPギャップに、本モデルの前期→当期の変化幅を足したもの。
    本モデルは前期差の再現がRMSE 0.045ptと高い一方、水準は期間によって±0.5pt程度ずれるため。
    （同一ヴィンテージでの検証。実際の速報時点では前期の公表値もGDP改定などで動く点に注意）
  - モデルの水準そのもの（接続前）も併記する。
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pandas as pd

from gapmodel import data, cao
from gapmodel.config import CAOConfig
from gapmodel.report import gap_amount, pot_table, input_coverage, anchored, fit_stats, write_excel

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent

INPUTS = {
    "gdp": "実質GDP（季調、QE）",
    "stock_priv": "固定資本ストック速報 民間企業設備",
    "stock_pub": "固定資本ストック速報 公的固定資産",
    "ip_mm_o": "鉱工業生産指数（原指数）",
    "ip_mm_s": "鉱工業生産指数（季調）",
    "ita_o": "第3次産業活動指数（原指数）",
    "ita_s": "第3次産業活動指数（季調）",
    "tankan_equip_mfg": "短観 生産・営業用設備判断DI 製造業",
    "tankan_equip_nonmfg": "短観 生産・営業用設備判断DI 非製造業",
    "emp": "労調 就業者（季調）",
    "lf": "労調 労働力人口（季調）",
    "un": "労調 完全失業者（季調）",
    "ee": "労調 雇用者（季調）",
    "h_sa": "毎勤 総実労働時間指数（季調）",
    "e_sa": "毎勤 常用雇用指数（季調）",
    "emfg_sa": "毎勤 常用雇用指数 製造業（季調）",
    "vacancy_eff": "一般職業紹介 有効求人数（季調）",
    "placements": "一般職業紹介 就職件数（季調）",
}


def main():
    ap = argparse.ArgumentParser(description="内閣府GDPギャップの推計")
    ap.add_argument("--end", default="2026Q2", help="推計対象の四半期（例 2026Q2）")
    ap.add_argument("--stock-last", default=None, help="固定資本ストック速報の最終公表期（既定は対象期の前期）")
    ap.add_argument("--breaks", default="2,2", help="生産能力関数の転換点数 製造業,非製造業（既定 2,2）")
    ap.add_argument("--cut-at-end", action="store_true",
                    help="対象期末より後の月次データを使わない。既定は手元のデータをすべて使う（公式公表時点の情報に近い）")
    ap.add_argument("--no-fill", action="store_true",
                    help="対象期の最終月が未公表の月次統計を補完しない（既定は直近2か月平均で補う。超早期推計と同じ）")
    ap.add_argument("--out", default=None, help="出力Excelのパス")
    a = ap.parse_args()

    endq = pd.Period(a.end, "Q")
    m, n = (int(x) for x in a.breaks.split(","))
    cfg = CAOConfig(end=a.end, stock_last=a.stock_last or str(endq - 1),
                    capacity_n_breaks=m, capacity_n_breaks_nonmfg=n)

    d = data.load_all()
    if a.cut_at_end:
        d = data.truncate_to(d, a.end)   # 対象期末より後の月次データを使わない（対象期直後の早期推計を再現）
    if d["gdp"].dropna().index[-1] < endq:
        raise SystemExit(f"実質GDPが{a.end}まで入っていません（data/raw/qe_real_gdp_sa.csv を更新してください）")
    cov = input_coverage(d, INPUTS, a.end)

    # 対象期の最終月まで出ていない月次統計（1次QE当日の第3次産業活動指数9月分など）を、超早期推計と同じ方法で補う：
    # 季調値は直近2か月の平均、原指数は季調値×過去同月の（原指数／季調値）の平均。--no-fill で補わない（従来の延長処理）。
    fills = []
    if not a.no_fill:
        from nowcast_gdp_gap import fill_month_end
        endm = endq.asfreq("M", how="end")
        for sa_k, o_k, label in [("ita_s", "ita_o", "第3次産業活動指数"), ("ip_mm_s", "ip_mm_o", "鉱工業生産指数")]:
            d[sa_k], d[o_k], f = fill_month_end(d[sa_k], d[o_k], endm)
            if f:
                fills.append(f"{label}：{', '.join(f)} を直近2か月平均で補完")
        for k, label in [("h_sa", "毎勤 総実労働時間"), ("e_sa", "毎勤 常用雇用"), ("emfg_sa", "毎勤 常用雇用（製造業）")]:
            d[k], _, f = fill_month_end(d[k], None, endm)
            if f:
                fills.append(f"{label}：{', '.join(f)} を直近2か月平均で補完")

    res, info = cao.run(d, cfg)
    pub = data.load_published()["cao"]["gap"]
    anc, t0 = anchored(res["gap"], pub, endq)

    yoy = res[["pot_growth", "c_tfp", "c_cap", "c_hours", "c_emp"]].rolling(4).mean()
    hist = pd.DataFrame({
        "GDPギャップ（モデル）": res.gap,
        "GDPギャップ（公表）": pub.reindex(res.index),
        "潜在成長率（前年比）": yoy.pot_growth,
        "寄与：TFP": yoy.c_tfp, "寄与：資本": yoy.c_cap,
        "寄与：労働時間": yoy.c_hours, "寄与：就業者": yoy.c_emp,
        "製造業稼働率": res.U_m, "非製造業稼働率": res.U_n,
        "労働参加率": res.part, "潜在労働参加率": res.part_star,
        "構造失業率（HP後）": res.struct_unemp_star,
    }).round(3)

    last = hist.loc[endq]
    gdp_t = float(d["gdp"].astype(float).loc[endq])
    summary = pd.DataFrame([
        ("推計対象期", str(endq)),
        ("GDPギャップ（推奨：公表値への接続値）", round(anc, 2) if t0 is not None else "公表値なし"),
        ("　接続の基準期（公表値）", f"{t0}：{pub.loc[t0]:.1f}" if t0 is not None else "-"),
        ("　実額（兆円、実質・年率）", round(gap_amount(gdp_t, anc), 1) if t0 is not None else "-"),
        ("　　計算に使った実質GDP（兆円、年率）", round(gdp_t / 1000, 1)),
        ("GDPギャップ（モデル水準）", round(float(last["GDPギャップ（モデル）"]), 2)),
        ("潜在成長率（前年比）", round(float(last["潜在成長率（前年比）"]), 2)),
        ("　TFP／資本／労働時間／就業者",
         " / ".join(f"{last[k]:.2f}" for k in ["寄与：TFP", "寄与：資本", "寄与：労働時間", "寄与：就業者"])),
        ("公表値（参考、あれば）", pub.get(endq, "未公表")),
    ], columns=["項目", "値"])
    st = pd.DataFrame([{"指標": "全期間", **fit_stats(res.gap, pub)},
                       {"指標": "2023年以降", **fit_stats(res.gap, pub, "2023Q1")}])
    settings = pd.DataFrame(
        [(k, str(v)) for k, v in vars(cfg).items()] + [(f"推計結果：{k}", str(v)) for k, v in info.items()],
        columns=["設定", "値"])
    settings = pd.concat([pd.DataFrame([("補完", x) for x in fills], columns=["設定", "値"]), settings])

    pot = pot_table(res, data.load_published()["cao"], endq)
    out = Path(a.out) if a.out else ROOT / "results" / f"gdp_gap_{endq}.xlsx"
    write_excel(out, {"サマリー": summary, "潜在成長率と寄与度": pot, "公表値との当てはまり": st, "時系列": hist,
                      "入力の到達状況": cov, "設定": settings})

    print(f"=== 内閣府GDPギャップ {endq} ===")
    print(summary.to_string(index=False))
    print("\n潜在成長率と寄与度（前年比、%・%pt）\n" + pot.to_string(index=False))
    print("\n公表値との当てはまり\n", st.to_string(index=False))
    miss = cov[cov["対象期をカバー"] != "○"]
    if len(miss):
        print("\n対象期をカバーしていない入力（延長・補完で処理）:\n", miss.to_string(index=False))
    if fills:
        print("\n補完した月次統計:\n  " + "\n  ".join(fills))
    print(f"\n出力: {out}")


if __name__ == "__main__":
    main()
