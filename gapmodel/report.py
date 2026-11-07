"""推計スクリプト共通：入力データの到達期チェック、公表値への接続（アンカー）、Excel出力。"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd


def last_period(s: pd.Series):
    s = s.dropna()
    return s.index[-1] if len(s) else None


def input_coverage(d: dict, keys: dict, end: str) -> pd.DataFrame:
    """各入力系列の最終期と、推計対象四半期をカバーしているかを一覧にする。
    keys: {系列キー: 説明}。月次系列は対象四半期の最終月まであるかで判定。"""
    endq = pd.Period(end, "Q")
    rows = []
    for k, label in keys.items():
        s = d.get(k)
        if s is None:
            rows.append({"系列": k, "内容": label, "最終期": "なし", "対象期をカバー": "×"})
            continue
        lp = last_period(s)
        if isinstance(lp, pd.Period) and lp.freqstr.startswith("M"):
            ok = lp >= endq.asfreq("M", how="end")
        elif isinstance(lp, pd.Period):
            ok = lp >= endq
        else:  # 年次（int index）
            ok = True
        rows.append({"系列": k, "内容": label, "最終期": str(lp), "対象期をカバー": "○" if ok else "×（延長で補完）"})
    return pd.DataFrame(rows)


def anchored(model: pd.Series, published: pd.Series, target: pd.Period):
    """公表値への接続：直近の公表期 t0 の公表値に、モデルの t0→target の変化幅を足す。
    水準のずれ（低周波の誤差）を消し、変化幅だけモデルに頼る。戻り値：(接続値, 基準期)"""
    pub = published.dropna()
    pub = pub[pub.index < target]
    if not len(pub):
        return np.nan, None
    t0 = pub.index[-1]
    return float(pub.loc[t0] + model.loc[target] - model.loc[t0]), t0


def fit_stats(model: pd.Series, published: pd.Series, since: str | None = None) -> dict:
    j = pd.concat([model, published], axis=1).dropna()
    if since:
        j = j.loc[pd.Period(since, "Q"):]
    if not len(j):
        return {}
    e = j.iloc[:, 0] - j.iloc[:, 1]
    de = j.iloc[:, 0].diff() - j.iloc[:, 1].diff()
    return {"期間": f"{j.index[0]}–{j.index[-1]}", "水準RMSE": round(float(np.sqrt((e ** 2).mean())), 3),
            "平均誤差": round(float(e.mean()), 3), "前期差RMSE": round(float(np.sqrt((de.dropna() ** 2).mean())), 3),
            "相関": round(float(j.corr().iloc[0, 1]), 3)}


def write_excel(path: Path, sheets: dict[str, pd.DataFrame]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            out = df.copy()
            if isinstance(out.index, pd.PeriodIndex):
                out.index = out.index.astype(str)
            out.to_excel(xw, sheet_name=name, index=not isinstance(df.index, pd.RangeIndex))
            ws = xw.sheets[name]
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width * 1.6), 60)
            ws.freeze_panes = "B2"


def gap_amount(gdp_bn: float, gap_pct: float) -> float:
    """GDPギャップの実額（兆円、実質・年率）。
    GDPギャップ＝(実際のGDP−潜在GDP)／潜在GDP なので、実額＝実際のGDP×g／(100＋g)。
    実質GDP（季調系列）は年率表示なので、そのまま年額になる。g が小さければ 実際のGDP×g／100 とほぼ同じ。"""
    return gdp_bn * gap_pct / (100 + gap_pct) / 1000


POT_ITEMS = [("潜在成長率", "pot_growth", "pot"), ("　TFP", "c_tfp", "tfp"), ("　資本", "c_cap", "cap"),
             ("　労働時間", "c_hours", "hours"), ("　就業者", "c_emp", "emp")]


def pot_table(res: pd.DataFrame, pub: pd.DataFrame, endq) -> pd.DataFrame:
    """潜在成長率（前年比）と寄与度を、モデル値と「前期の公表値＋モデルの前期→当期変化」（接続値）で並べる。
    公表値は0.1刻み。内閣府は公表のたびに過去の潜在成長率も改定するので、接続値は目安。"""
    yoy = res[[c for _, c, _ in POT_ITEMS]].rolling(4).mean()
    t0 = endq - 1
    rows = []
    for label, rc, pc in POT_ITEMS:
        m1, m0 = float(yoy[rc].get(endq, np.nan)), float(yoy[rc].get(t0, np.nan))
        p0 = pd.to_numeric(pub[pc], errors="coerce").get(t0, np.nan) if pc in pub else np.nan
        p1 = pd.to_numeric(pub[pc], errors="coerce").get(endq, np.nan) if pc in pub else np.nan
        rows.append({"項目": label, "接続値（推奨）": round(p0 + (m1 - m0), 2) if not np.isnan(p0) else np.nan,
                     f"前期の公表値（{t0}）": p0, "モデルの前期→当期変化": round(m1 - m0, 2),
                     f"モデル（{endq}）": round(m1, 2), f"公表値（{endq}、あれば）": p1})
    return pd.DataFrame(rows)
