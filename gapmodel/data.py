"""data/raw のキャッシュを読み、四半期(PeriodIndex)・月次の系列に整える。

キャッシュ形式: 1行1系列で `name|start|v1,v2,...`。start は 198001(月次) か 1980Q1(四半期)。
取得元URLは README.md と fetch.py を参照。
"""
from pathlib import Path
import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
PUB = Path(__file__).resolve().parent.parent / "data" / "published"


def _read_series_file(path: Path, default_start: str | None = None) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "|" not in line:
            continue
        parts = line.split("|")
        name = parts[0]
        if len(parts) == 3:
            start, vals = parts[1], parts[2]
        else:
            start, vals = default_start, parts[1]
        v = [float(x) if x.strip() not in ("", "NaN") else np.nan for x in vals.split(",")]
        if "Q" in start:
            idx = pd.period_range(start=pd.Period(start, "Q"), periods=len(v), freq="Q")
        else:
            idx = pd.period_range(start=pd.Period(f"{start[:4]}-{start[4:]}", "M"), periods=len(v), freq="M")
        out[name] = pd.Series(v, index=idx, name=name)
    return out


def q_mean(s: pd.Series) -> pd.Series:
    """月次→四半期平均（3か月揃った期のみ）。"""
    g = s.groupby(s.index.asfreq("Q"))
    m = g.mean()
    return m[g.count() == 3]


def load_all() -> dict:
    d = {}
    d.update(_read_series_file(RAW / "iip_monthly.txt"))
    d.update(_read_series_file(RAW / "ita_monthly.txt"))
    d.update(_read_series_file(RAW / "lfs_sa_quarterly.txt"))
    d.update(_read_series_file(RAW / "lfs_sexage_quarterly.txt", "1980Q1"))
    d.update(_read_series_file(RAW / "mk_sa_monthly.txt"))
    d.update(_read_series_file(RAW / "pesa_quarterly.txt"))
    d.update(_read_series_file(RAW / "qna_mfg_output.txt"))
    lh = _read_series_file(RAW / "lfs_hours_monthly.txt")
    d["lfs_ah"], d["lfs_nh"] = lh["ah"], lh["nh"]
    if (RAW / "ita_2005base.txt").exists():
        d.update(_read_series_file(RAW / "ita_2005base.txt"))
    dd = _read_series_file(RAW / "mk_days_monthly.txt")
    d["mk_days5"], d["mk_days30"] = dd["days_5"], dd["days_30"]
    tk = _read_series_file(RAW / "tankan_di.txt", "1980Q1")
    d.update({f"tankan_{k}": v for k, v in tk.items()})

    gdp = pd.read_csv(RAW / "qe_real_gdp_sa.csv")
    d["gdp"] = pd.Series(gdp.rgdp.values, index=pd.PeriodIndex(gdp.q, freq="Q"), name="gdp")

    st = []
    for r in (RAW / "stock_raw.txt").read_text().strip().strip(";").split(";"):
        c, a, b = r.split(",")
        yy = int(c[:2]); y = 1900 + yy if yy >= 80 else 2000 + yy
        st.append((pd.Period(f"{y}Q{c[2]}", "Q"), float(a), float(b)))
    S = pd.DataFrame(st, columns=["q", "priv", "pub"]).set_index("q")
    d["stock_priv"], d["stock_pub"] = S.priv, S.pub

    d["matrix"] = pd.read_csv(RAW / "stock_matrix_ratios.csv", comment="#").set_index("year")
    d["labor_share"] = pd.read_csv(RAW / "labor_share_sna.csv", index_col=0)
    return d


def load_published() -> dict:
    out = {}
    b = pd.read_csv(PUB / "boj_gap.csv")
    out["boj"] = b.set_index(pd.PeriodIndex(b.q, freq="Q")).drop(columns="q")
    if (PUB / "boj_potential.csv").exists():   # 日銀の潜在成長率と寄与度（年度半期、前年比）
        out["boj_pot"] = pd.read_csv(PUB / "boj_potential.csv").set_index(["FY", "H"])
    rows = []
    for r in (PUB / "cao_q_raw.txt").read_text().strip().strip(";").split(";"):
        p = r.split(",")
        yy = int(p[0][:2]); y = 1900 + yy if yy >= 80 else 2000 + yy
        vals = [np.nan if x == "-" else float(x) for x in p[1:]]
        rows.append((pd.Period(f"{y}Q{p[0][2]}", "Q"), *vals))
    out["cao"] = pd.DataFrame(rows, columns=["q", "gap", "pot", "tfp", "cap", "hours", "emp"]).set_index("q")
    raw = (PUB / "boj_extra_raw.txt").read_text().strip().split("\n")
    lab = []
    for r in raw[0].split("|")[1].strip(";").split(";"):
        p = r.split(",")
        yy = int(p[0][:2]); y = 1900 + yy if yy >= 80 else 2000 + yy
        lab.append((pd.Period(f"{y}Q{p[0][2]}", "Q"), float(p[1]), float(p[2]),
                    float(p[3]) if len(p) > 3 and p[3] else np.nan))
    out["boj_labor"] = pd.DataFrame(lab, columns=["q", "bev", "unemp", "empgap"]).set_index("q")
    return out


def truncate_to(d: dict, end: str) -> dict:
    """推計対象期より後のデータを落とす（速報時点で手に入る情報だけにする）。
    月次は対象四半期の最終月まで、四半期は対象期まで。年次（労働分配率・ストックマトリックス）はそのまま。"""
    endq = pd.Period(end, "Q")
    endm = endq.asfreq("M", how="end")
    out = {}
    for k, v in d.items():
        if isinstance(v, pd.Series) and isinstance(v.index, pd.PeriodIndex):
            out[k] = v.loc[:endm] if v.index.freqstr.startswith("M") else v.loc[:endq]
        else:
            out[k] = v
    return out
