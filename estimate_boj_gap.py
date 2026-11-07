"""日本銀行 需給ギャップを推計する。

使い方:
    python estimate_boj_gap.py                       # 既定：2026Q2、推奨設定
    python estimate_boj_gap.py --end 2026Q3 --qna-last 2026Q2 --stock-last 2026Q2
    python estimate_boj_gap.py --hours mk            # 労働時間を毎勤にする（比較用）

出力: results/boj_gap_<期>.xlsx（サマリー／時系列／入力の到達状況／設定）と標準出力。

推奨設定（Claude、2026-10-28時点）:
  - 手法は川本ほか(2017)＋日銀調査統計局(2026年3月)の見直し。
  - 資本：製造業稼働率＝IIP(生産QNAで回帰した当てはめ値)／補正生産能力（生産能力指数を資本ストックで回帰、トレンド項なし）。
    非製造業は製造業の 稼働率〜設備判断DI の傾きを非製造業DIに当てはめる。水準は公表値の1994–2019年平均に較正。
  - 労働：労働力率（性×年齢12区分、区分線形トレンド）＋就業率（UV、β=−0.396、45度線）＋労働時間。
    労働時間は労調 非農林業従業者の平均週間就業時間を、調査週の祝日数で調整→季調→TC成分（Henderson）→HP乖離。
    日数×1日当たり時間の分解は、最新期が不安定になるため使わない（--split で試せる）。
  - 欠員率の補正：2019年までは不要（自前のV/Uが日銀公表値と一致）。2020年以降は日銀公表のV/U比との比を使い、
    対象期以降は前期の比を横置き（対象期の公表V/Uは日銀のギャップ公表と同時にしか出ないため）。
  - 労働時間のTC成分とHPフィルターは系列端の値が後から大きく動く（対象期末で切った推計と、現在の全データでの推計の差：
    2015年以降でRMSE 0.6pt、ギャップ換算で約0.4pt。2026Q2の需給ギャップは6月分まで0.43／7月分まで0.49／8月分まで0.55）。日銀の公表（4-6月期なら10月初）は
    8月分までの月次データを使える時点なので、公表値に合わせるには手元のデータをすべて使う（既定）。
    対象期直後の早期推計（--cut-at-end）は、その分ぶれが大きい。
  - 主な推計値はモデル水準。公表値への接続値も併記する（日銀は前期差の再現がRMSE 0.15ptで、接続の利点は小さい）。
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pandas as pd

from gapmodel import data, boj
from gapmodel.config import BOJConfig
from gapmodel.report import input_coverage, anchored, fit_stats, write_excel

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent

INPUTS = {
    "ip_mfg_o": "鉱工業生産指数 製造業（原指数）",
    "ip_mfg_s": "鉱工業生産指数 製造業（季調）",
    "cap_mfg_o": "生産能力指数 製造業（確報のみ）",
    "mfg_output": "生産QNA 製造業実質産出",
    "stock_priv": "固定資本ストック速報 民間企業設備",
    "tankan_equip_mfg": "短観 生産・営業用設備判断DI 製造業",
    "tankan_equip_nonmfg": "短観 生産・営業用設備判断DI 非製造業",
    "lfpr_m25": "労調 性年齢別 労働力人口比率（代表：男25-34歳）",
    "pop_m25": "労調 性年齢別 人口（代表：男25-34歳）",
    "lf": "労調 労働力人口（季調）",
    "un": "労調 完全失業者（季調）",
    "ee": "労調 雇用者（季調）",
    "lfs_ah": "労調 平均週間就業時間 非農林業従業者",
    "vacancy_eff": "一般職業紹介 有効求人数（季調）",
    "placements": "一般職業紹介 就職件数（季調）",
}


def main():
    ap = argparse.ArgumentParser(description="日銀 需給ギャップの推計")
    ap.add_argument("--end", default="2026Q2")
    ap.add_argument("--qna-last", default=None, help="生産QNAの最終公表期（既定は対象期の前期）")
    ap.add_argument("--stock-last", default=None, help="固定資本ストック速報の最終公表期（既定は対象期の前期）")
    ap.add_argument("--hours", default="lfs", choices=["lfs", "mk"])
    ap.add_argument("--split", action="store_true", help="労働時間を日数×1日当たり時間に分解する（非推奨）")
    ap.add_argument("--vacancy-until", default=None,
                    help="日銀公表のV/U比（欠員補正）を使う最終期。既定は対象期の前期（速報時点で使える情報だけ）")
    ap.add_argument("--cut-at-end", action="store_true",
                    help="対象期末より後の月次データを使わない。既定は手元のデータをすべて使う（公式公表時点の情報に近い）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    endq = pd.Period(a.end, "Q")
    cfg = BOJConfig(end=a.end, qna_last=a.qna_last or str(endq - 1), stock_last=a.stock_last or str(endq - 1),
                    hours_source=a.hours, hours_split=a.split,
                    vacancy_ratio_until=a.vacancy_until or str(endq - 1))

    d = data.load_all()
    if a.cut_at_end:
        d = data.truncate_to(d, a.end)   # 対象期末より後の月次データを使わない（対象期直後の早期推計を再現）
    pubs = data.load_published()
    cov = input_coverage(d, INPUTS, a.end)
    res, info = boj.run(d, pubs, cfg)
    pb = pubs["boj"]
    anc, t0 = anchored(res["gap"], pb["boj_gap"], endq)
    # 参考：労働時間ギャップの不規則変動を取り除かない場合（日銀2026年改定の処理を入れる前の版）
    from dataclasses import replace as _replace
    res_raw, _ = boj.run(d, pubs, _replace(cfg, hours_tc=False))
    # 潜在成長率（TFP込み、日銀方式の再現）と、TFP込みのギャップ
    from gapmodel.boj_potential import potential
    potq, poth = potential(d, res, info, cfg)
    if "boj_pot" in pubs:
        pp = pubs["boj_pot"]
        poth = poth.join(pp[["pot", "tfp", "cap", "hours", "emp"]], rsuffix="（公表）")
    jp = {"pot": "潜在成長率", "tfp": "TFP", "cap": "資本", "hours": "労働時間", "emp": "就業者"}
    poth = poth.rename(columns={**{k: f"{v}（再現）" for k, v in jp.items()}, **{f"{k}（公表）": f"{v}（公表）" for k, v in jp.items()}})
    poth.index = [f"{fy}年度{'上' if hh == 1 else '下'}期" for fy, hh in poth.index]

    hist = pd.DataFrame({
        "需給ギャップ（モデル）": res.gap, "需給ギャップ（公表）": pb.boj_gap.reindex(res.index),
        "資本投入ギャップ寄与": res.c_kgap, "資本寄与（公表）": pb.boj_kgap.reindex(res.index),
        "労働投入ギャップ寄与": res.c_lgap, "労働寄与（公表）": pb.boj_lgap.reindex(res.index),
        "製造業稼働率ギャップ": res.util_gap_mfg, "非製造業稼働率ギャップ": res.util_gap_nonmfg,
        "労働力率ギャップ": res.lgap_lfpr, "就業率ギャップ": res.lgap_emp, "労働時間ギャップ": res.lgap_hours,
        "需給ギャップ（参考：不規則変動を除かない）": res_raw.gap,
        "労働時間ギャップ（参考：不規則変動を除かない）": res_raw.lgap_hours,
    }).round(3)
    last = hist.loc[endq]
    summary = pd.DataFrame([
        ("推計対象期", str(endq)),
        ("需給ギャップ（推奨：モデル水準）", round(float(last["需給ギャップ（モデル）"]), 2)),
        ("　資本投入ギャップ寄与", round(float(last["資本投入ギャップ寄与"]), 2)),
        ("　労働投入ギャップ寄与", round(float(last["労働投入ギャップ寄与"]), 2)),
        ("　　うち労働力率／就業率／労働時間（ギャップ、αを掛ける前）",
         " / ".join(f"{last[k]:.2f}" for k in ["労働力率ギャップ", "就業率ギャップ", "労働時間ギャップ"])),
        ("需給ギャップ（参考：労働時間の不規則変動を除かない場合）",
         round(float(last["需給ギャップ（参考：不規則変動を除かない）"]), 2)),
        ("潜在成長率（前年比、四半期、モデル）" + ("" if pd.notna(potq["TFP（対数×100）"].get(endq)) else "　※GDP速報前のため潜在TFPは直近の伸びで延長"),
         round(float(potq["潜在成長率（前年比）"].loc[endq]), 2)),
        ("　TFP／資本／労働時間／就業者",
         " / ".join(f"{potq[k].loc[endq]:.2f}" for k in ["寄与：TFP", "寄与：資本", "寄与：労働時間", "寄与：就業者"])),
        ("参考：TFP込みギャップ（実際のGDP−潜在GDP、日銀は非公表）",
         round(float(potq["TFP込みギャップ（実際のGDP−潜在GDP）"].loc[endq]), 2)
         if pd.notna(potq["TFP込みギャップ（実際のGDP−潜在GDP）"].get(endq)) else "GDP速報後に計算"),
        ("需給ギャップ（参考：公表値への接続値）", round(anc, 2) if t0 is not None else "公表値なし"),
        ("　接続の基準期（公表値）", f"{t0}：{pb.boj_gap.loc[t0]:.3f}" if t0 is not None else "-"),
        ("公表値（参考、あれば）", pb.boj_gap.get(endq, "未公表")),
    ], columns=["項目", "値"])
    st = pd.DataFrame([{"指標": "需給ギャップ 全期間", **fit_stats(res.gap, pb.boj_gap)},
                       {"指標": "需給ギャップ 2023年以降", **fit_stats(res.gap, pb.boj_gap, "2023Q1")},
                       {"指標": "資本寄与 全期間", **fit_stats(res.c_kgap, pb.boj_kgap)},
                       {"指標": "労働寄与 全期間", **fit_stats(res.c_lgap, pb.boj_lgap)},
                       {"指標": "参考：不規則変動を除かない 需給ギャップ 全期間", **fit_stats(res_raw.gap, pb.boj_gap)}])
    settings = pd.DataFrame(
        [(k, str(v)) for k, v in vars(cfg).items()] +
        [(f"推計結果：{k}", str(v)) for k, v in info.items() if k != "lfpr_breaks"],
        columns=["設定", "値"])

    out = Path(a.out) if a.out else ROOT / "results" / f"boj_gap_{endq}.xlsx"
    write_excel(out, {"サマリー": summary, "潜在成長率（半期）": poth.round(2).loc["1995年度上期":],
                      "潜在成長率（四半期）": potq.round(3), "公表値との当てはまり": st, "時系列": hist,
                      "入力の到達状況": cov, "設定": settings})

    print(f"=== 日銀 需給ギャップ {endq} ===")
    print(summary.to_string(index=False))
    print("\n公表値との当てはまり\n", st.to_string(index=False))
    miss = cov[cov["対象期をカバー"] != "○"]
    if len(miss):
        print("\n対象期をカバーしていない入力（延長・補完で処理）:\n", miss.to_string(index=False))
    print(f"\n出力: {out}")


if __name__ == "__main__":
    main()
