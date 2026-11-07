"""日本銀行 需給ギャップの再現。手法：川本・尾崎・加藤・前橋（2017）＋2026年の改定（労働時間ギャップの分解等）。

需給ギャップ = 資本投入ギャップ×(1-α) + 労働投入ギャップ×α
 資本投入ギャップ = 製造業稼働率ギャップ×wM + 非製造業稼働率ギャップ×wN（有形資産ストックのウェイト、無形は0）
 労働投入ギャップ = 労働力率ギャップ + 就業率ギャップ + 労働時間ギャップ
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm

from .config import BOJConfig
from .tools import hp, seasonal_adjust_log, segmented_trend, sa_and_tc_log
from .data import q_mean
from .capital import split_stock

SEXAGE = [f"{s}{a}" for s in "mf" for a in (15, 25, 35, 45, 55, 65)]


# ---------------- 資本投入ギャップ ----------------
def _iip_mfg_sa_q(d):
    o = d["ip_mfg_o"]
    st = seasonal_adjust_log(o, 12)
    off = d["ip_mfg_s"]
    return q_mean(pd.concat([st.loc[:off.index[0] - 1], off]))


def capital_gap(d, cfg: BOJConfig, qi):
    end = pd.Period(cfg.end, "Q")
    # 分子：生産QNAの製造業実質産出で鉱工業生産（製造業）を回帰した当てはめ値。QNA未公表期はIIPの伸びで延長。
    ip = _iip_mfg_sa_q(d)
    qna = d["mfg_output"].loc[:pd.Period(cfg.qna_last, "Q")]
    idx = qna.index.intersection(ip.index)
    r = sm.OLS(np.log(ip.loc[idx]), sm.add_constant(np.log(qna.loc[idx]))).fit()
    num = np.exp(r.params["const"] + r.params[qna.name] * np.log(qna))
    for q in pd.period_range(qna.index[-1] + 1, end, freq="Q"):
        num.loc[q] = num.loc[q - 1] * ip.loc[q] / ip.loc[q - 1]
    # 分母：生産能力指数を製造業有形資本ストック（前期末）で回帰した当てはめ値（補正生産能力）。
    # タイムトレンドを入れる定式化も試したが、公表の資本投入ギャップとの相関は
    # トレンドなし 0.99 ／ トレンドあり 0.91 で、トレンドなしを採用（findings_replication.md）。
    K = split_stock(d, cfg.stock_last, cfg.end, lag=0)
    cap = q_mean(d["cap_mfg_o"])
    Km = K.mfgT.shift(1).bfill()
    idx2 = cap.index.intersection(Km.index)
    idx2 = idx2[idx2 >= pd.Period(cfg.start, "Q")]
    X = pd.DataFrame({"lnK": np.log(Km)})
    if cfg.capacity_trend:
        X["t"] = np.arange(len(Km))
    r2 = sm.OLS(np.log(cap.loc[idx2]), sm.add_constant(X.loc[idx2])).fit()
    lncap = r2.params["const"] + r2.params["lnK"] * X.lnK.reindex(qi)     # トレンド項は補正生産能力に含めない
    lnu = np.log(num.reindex(qi)) - lncap
    p0, p1 = cfg.util_mean_period
    per = slice(pd.Period(p0, "Q"), pd.Period(p1, "Q") if p1 else None)
    gap_m = 100 * (lnu - lnu.loc[per].mean())

    # 非製造業：製造業稼働率ギャップと製造業の生産・営業用設備判断DIの関係（傾き）を、非製造業DIに当てはめ、長期平均からの乖離にする
    di_m, di_n = d["tankan_equip_mfg"].reindex(qi), d["tankan_equip_nonmfg"].reindex(qi)
    r3 = sm.OLS(gap_m, sm.add_constant(di_m), missing="drop").fit()
    b = r3.params[di_m.name]
    gap_n = b * (di_n - di_n.loc[per].mean())
    gap_n = gap_n.fillna(b * (di_m - di_m.loc[per].mean()))   # 非製造業DIの始期より前は製造業DIで代用

    Kq = K.reindex(qi)
    wM, wN = Kq.mfgT / Kq.total, Kq.nmT / Kq.total
    kgap = wM * gap_m + wN * gap_n
    info = {"iip_on_qna_slope": round(float(r.params[qna.name]), 3),
            "capacity_on_lnK": round(float(r2.params["lnK"]), 3),
            "util_on_DI_mfg": round(float(b), 3)}
    return pd.DataFrame({"kgap": kgap, "util_gap_mfg": gap_m, "util_gap_nonmfg": gap_n}), info


# ---------------- 労働投入ギャップ ----------------
def lfpr_gap(d, cfg: BOJConfig, qi):
    end = pd.Period(cfg.end, "Q")
    rows, trends, pops = {}, {}, {}
    breaks = {}
    for g in SEXAGE:
        x = seasonal_adjust_log(d[f"lfpr_{g}"].loc[:end], 4)
        pop = seasonal_adjust_log(d[f"pop_{g}"].loc[:end], 4)
        x = x.loc[pd.Period("1980Q1", "Q"):]
        tr, br = segmented_trend(x, cfg.lfpr_max_breaks, cfg.lfpr_min_seg)
        rows[g], trends[g], pops[g] = x, tr, pop
        breaks[g] = [str(b) for b in br]
    X, T, P = pd.DataFrame(rows), pd.DataFrame(trends), pd.DataFrame(pops)
    w = P.div(P.sum(axis=1), axis=0)
    act = (w * X).sum(axis=1)
    pot = (w * T).sum(axis=1)
    return (100 * np.log(act / pot)).reindex(qi), breaks


def own_beveridge(d, qi):
    """自前のU・V：雇用失業率 U=失業者/(雇用者+失業者)、欠員率 V=欠員/(欠員+雇用者)。B=V/U。"""
    ee, un = d["ee"], d["un"]
    vac = d["vacancy_eff"] - d["placements"]
    U = un / (ee + un)
    V = vac / (vac + ee * 10)
    return (V / U).reindex(qi)


def employment_gap(K_pct: pd.Series, B: pd.Series, beta: float):
    """45度線との交点を構造失業率とする：K* = K × B^(−β/(1−β))、就業率ギャップ = ln(1−K) − ln(1−K*)"""
    Ks = K_pct * B ** (-beta / (1 - beta))
    return 100 * (np.log(1 - K_pct / 100) - np.log(1 - Ks / 100))


def survey_week_holidays(index: pd.PeriodIndex) -> pd.Series:
    """労調の調査週（月末1週間。12月は20～26日）に含まれる平日の祝日数。"""
    import datetime as dt
    import holidays
    jp = holidays.Japan(years=range(index[0].year - 1, index[-1].year + 2))
    out = []
    for p in index:
        if p.month == 12:
            days = [dt.date(p.year, 12, k) for k in range(20, 27)]
        else:
            last = (pd.Timestamp(p.year, p.month, 1) + pd.offsets.MonthEnd(0)).date()
            days = [last - dt.timedelta(k) for k in range(7)]
        out.append(sum(1 for x in days if x.weekday() < 5 and x in jp))
    return pd.Series(out, index=index, dtype=float)


def lfs_hours_sa(d, end, use_tc: bool = True, forecast_ext: int = 0):
    """労調 非農林業 従業者の平均週間就業時間（月末1週間、原数値）→ 祝日調整 → STL季調 → 四半期平均。
    祝日効果は、対数値の13か月中心移動平均からの乖離を、月ダミーと調査週の平日祝日数で回帰して推計
    （1日あたり約−4.9%）。日銀2026年改定の「就業日数×1日当たり時間」分解の代替で、日数の変動のうち
    暦要因を取り除く。2011年3～8月の欠測は2010・2012年の同月平均で埋めて季調し、2011Q2は補間。"""
    h = d["lfs_ah"].copy()
    hol = survey_week_holidays(h.index)
    y = np.log(h)
    dev = y - y.rolling(13, center=True, min_periods=10).mean()
    X = pd.get_dummies(h.index.month, prefix="m", dtype=float)
    X.index = h.index
    X["hol"] = hol
    ok = dev.dropna().index
    g = sm.OLS(dev.loc[ok], X.loc[ok]).fit().params["hol"]
    ha = h * np.exp(-g * hol)
    for m in range(3, 9):
        p = pd.Period(f"2011-{m:02d}", "M")
        ha.loc[p] = 0.5 * (ha.loc[pd.Period(f"2010-{m:02d}", "M")] + ha.loc[pd.Period(f"2012-{m:02d}", "M")])
    # 日銀2026年改定 脚注13：「不規則変動を除いた値（X-12-ARIMAの傾向循環変動＜TC＞成分）を用いている」。
    # X-12は使えないため、STL季調後の系列にX-11と同じ規則で項数を選んだHenderson移動平均（端はMusgrave）をかける。
    sa, tc, n, ic = sa_and_tc_log(ha, 12, forecast_ext)
    q = q_mean(tc if use_tc else sa)
    q.loc[pd.Period("2011Q2", "Q")] = np.nan
    q = q.interpolate()
    return q.loc[:pd.Period(end, "Q")], float(g)


def month_workdays(index: pd.PeriodIndex) -> pd.Series:
    """各月の平日（土日・祝日を除く）日数。"""
    import holidays
    jp = holidays.Japan(years=range(index[0].year - 1, index[-1].year + 2))
    out = []
    for p in index:
        days = pd.date_range(p.start_time, p.end_time.normalize(), freq="D")
        out.append(sum(1 for x in days if x.weekday() < 5 and x.date() not in jp))
    return pd.Series(out, index=index, dtype=float)


def mk_days_tc(d):
    """毎勤 出勤日数（調査産業計・5人以上、1990年1月～）。1989年以前は30人以上の系列を重複期間の対数差で水準調整して接続。
    月の平日数による振れを回帰で除いてから、STL季調→TC成分。"""
    d5, d30 = d["mk_days5"], d["mk_days30"]
    ov = d5.index.intersection(d30.index)[:60]
    lvl = float((np.log(d5.loc[ov]) - np.log(d30.loc[ov])).mean())
    days = pd.concat([d30.loc[:d5.index[0] - 1] * np.exp(lvl), d5])
    wd = month_workdays(days.index)
    y = np.log(days)
    dev = y - y.rolling(13, center=True, min_periods=10).mean()
    X = pd.get_dummies(days.index.month, prefix="m", dtype=float)
    X.index = days.index
    X["wd"] = wd - wd.groupby(wd.index.month).transform("mean")
    ok = dev.dropna().index
    b = sm.OLS(dev.loc[ok], X.loc[ok]).fit().params["wd"]
    adj = days * np.exp(-b * X["wd"])
    _, tc, n, ic = sa_and_tc_log(adj, 12)
    return tc, float(b), n


def hours_gap_split(d, cfg: BOJConfig, qi):
    """日銀2026年改定：総実労働時間＝出勤日数×1日当たり労働時間。
    出勤日数（毎勤、TC）のトレンド＝区分線形回帰、1日当たり労働時間（労調の週間就業時間TC÷出勤日数TC）のトレンド＝HP。"""
    H, _ = lfs_hours_sa(d, cfg.end, True)                 # 労調 時間（祝日調整・TC、四半期）
    Dm, b, n = mk_days_tc(d)
    D = q_mean(Dm).loc[:pd.Period(cfg.end, "Q")]
    idx = H.index.intersection(D.index)
    lnD, lnh = np.log(D.loc[idx]), np.log(H.loc[idx]) - np.log(D.loc[idx])
    trD, brD = segmented_trend(lnD, cfg.days_max_breaks, cfg.days_min_seg)
    trh = hp(lnh, cfg.hp_lambda)
    gD, gh = 100 * (lnD - trD), 100 * (lnh - trh)
    info = {"days_workday_coef": round(b, 4), "days_henderson": n, "days_breaks": [str(x) for x in brD]}
    return gD.reindex(qi), gh.reindex(qi), info


def hours_gap(d, cfg: BOJConfig, qi):
    if cfg.hours_source == "lfs":
        h, _ = lfs_hours_sa(d, cfg.end, cfg.hours_tc, cfg.tc_forecast_ext)
    else:
        h = q_mean(d["h_sa"]).loc[:pd.Period(cfg.end, "Q")]
    g = 100 * (np.log(h) - hp(np.log(h), cfg.hp_lambda))
    return g.reindex(qi).fillna(0.0)          # 毎勤の場合、1997年以前は季調系列がないためギャップ0


def run(d, pub=None, cfg: BOJConfig | None = None):
    cfg = cfg or BOJConfig()
    qi = pd.period_range(pd.Period(cfg.start, "Q"), pd.Period(cfg.end, "Q"), freq="Q")
    alpha = cfg.alpha if cfg.alpha is not None else float(d["labor_share"]["CY"].loc[cfg.alpha_period[0]:cfg.alpha_period[1]].mean())
    kg, kinfo = capital_gap(d, cfg, qi)
    lg_lf, lbreaks = lfpr_gap(d, cfg, qi)
    # 失業率：労調（季調値）の完全失業率。B（V/U比）は自前推計を日銀公表Bに水準調整したもの
    K = (100 * d["un"] / d["lf"]).reindex(qi)
    B_own = own_beveridge(d, qi)
    info = {"alpha": round(alpha, 4), **kinfo}
    # 2019年までは自前のB（V/U）が日銀公表値と一致（比 1.000±0.0002）。2020年以降は日銀が欠員を補正しており
    # 公表Bの方が高い（2026Q2で1.26倍）。補正方法は非公表のため、公表済み期間は公表比、以降は直近の比を横置き。
    B = B_own.copy()
    if pub is not None and cfg.vacancy_correction == "published_ratio":
        ratio = (pub["boj_labor"]["bev"] / own_beveridge(d, pub["boj_labor"].index)).reindex(qi)
        ratio.loc[:pd.Period("2019Q4", "Q")] = 1.0
        if cfg.vacancy_ratio_until:          # 速報時点の再現：対象期の公表V/Uはまだ無いので、それ以前の比だけを使う
            ratio.loc[pd.Period(cfg.vacancy_ratio_until, "Q") + 1:] = np.nan
        ratio = ratio.ffill()
        B = B_own * ratio
        info["vacancy_correction_last"] = round(float(ratio.iloc[-1]), 4)
    lg_emp = employment_gap(K, B, cfg.uv_beta)
    out = pd.DataFrame(index=qi)
    if cfg.hours_split and cfg.hours_source == "lfs":
        gD, gh, hinfo = hours_gap_split(d, cfg, qi)
        lg_h = gD + gh
        out["lgap_hours_days"], out["lgap_hours_perday"] = gD, gh
        info.update(hinfo)
    else:
        lg_h = hours_gap(d, cfg, qi)
    out["lgap_lfpr"], out["lgap_emp"], out["lgap_hours"] = lg_lf, lg_emp, lg_h
    out["lgap"] = out[["lgap_lfpr", "lgap_emp", "lgap_hours"]].sum(axis=1)
    out = out.join(kg)
    if pub is not None and cfg.calibrate_kgap_level:
        # 稼働率の「長期平均」は日銀では1980年代からの平均と考えられ、1994年始まりの本推計とは水準がずれる。
        # 公表資本投入ギャップとの平均差を較正期間で推計し差し引く（水準のみ。形状は推計どおり）。
        c0, c1 = cfg.calib_period
        pk = pub["boj"]["boj_kgap"].reindex(qi) / (1 - alpha)
        off = float((out.kgap - pk).loc[pd.Period(c0, "Q"):pd.Period(c1, "Q")].mean())
        out["kgap_raw"] = out.kgap
        out["kgap"] = out.kgap - off
        info["kgap_offset"] = round(off, 3)
    out["c_kgap"] = (1 - alpha) * out.kgap
    out["c_lgap"] = alpha * out.lgap
    out["gap"] = out.c_kgap + out.c_lgap
    info["lfpr_breaks"] = lbreaks
    return out, info
