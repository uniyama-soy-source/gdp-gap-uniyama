"""内閣府（経済財政分析担当）GDPギャップの再現。手法：DP/26-8（並木 2026）。

GDPギャップ = 実質GDP / 潜在GDP - 1
潜在GDP     = 潜在TFP × 潜在資本投入^(1-α) × 潜在労働投入^α
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm

from .config import CAOConfig
from .tools import (hp, seasonal_adjust_log, wharton_capacity, best_breaks, hinge_design, bai_perron_hinge,
                    gls_ar1_uv)
from .data import q_mean
from .capital import split_stock


def labor_share(d, period):
    s = d["labor_share"]["CY"]
    return float(s.loc[period[0]:period[1]].mean())


# ---------------- 労働投入 ----------------
def _extend_path(actual_q: pd.Series, annual_growth_path: pd.Series, to: str, mode: str):
    """直近期から先を、年ごとの前年差（mode='diff'）または伸び率%（mode='pct'）で四半期按分して延長。"""
    idx = pd.period_range(actual_q.index[0], pd.Period(to, "Q"), freq="Q")
    out = actual_q.reindex(idx)
    for i in range(len(actual_q), len(idx)):
        y = idx[i].year
        g = annual_growth_path.get(y, annual_growth_path.iloc[-1])
        out.iloc[i] = out.iloc[i - 1] + g / 4 if mode == "diff" else out.iloc[i - 1] * (1 + g / 400)
    return out


def labor_block(d, cfg: CAOConfig):
    end = pd.Period(cfg.end, "Q")
    qi = pd.period_range(pd.Period(cfg.start, "Q"), end, freq="Q")
    emp = d["emp"].loc[:end]
    lf, nilf = d["lf"].loc[:end], d["nilf"].loc[:end]
    pop = lf + nilf
    part = 100 * lf / pop

    # 労働時間（毎勤 総実労働時間 季調済指数、5人以上）。1998年以前は1998-2003年の対数線形トレンドで遡及。
    h = q_mean(d["h_sa"]).loc[:end]
    t = np.arange(len(h))
    b = np.polyfit(t[:24], np.log(h.values[:24]), 1)
    back = pd.period_range(pd.Period("1980Q1", "Q"), h.index[0] - 1, freq="Q")
    hb = pd.Series(np.exp(b[1] + b[0] * np.arange(-len(back), 0)), index=back)
    hours = pd.concat([hb, h])

    # 将来予測：労働参加率（直近の前年差平均 → JILPT2030年の前年差へ線形に収束）
    ann = part.groupby(part.index.year).mean()
    last_full = max(y for y in ann.index if (part.index.year == y).sum() == 4)
    y0, y1 = cfg.lfpr_recent_years
    d_recent = ann.loc[y0:y1].diff().dropna().mean()
    j = cfg.jilpt_lfpr
    d_jilpt = (j[2030] - j[2025]) / 5
    years = list(range(last_full + 1, int(cfg.forecast_to[:4]) + 1))
    path = pd.Series(np.linspace(d_recent, d_jilpt, len(years)), index=years)
    part_ext = _extend_path(part, path, cfg.forecast_to, "diff")
    part_star = hp(part_ext.loc[pd.Period(cfg.start, "Q"):], cfg.hp_lambda).reindex(qi)

    hpath = pd.Series(cfg.hours_forecast_growth, index=years)
    hours_ext = _extend_path(hours, hpath, cfg.forecast_to, "pct")
    hours_star = np.exp(hp(np.log(hours_ext.loc[pd.Period(cfg.start, "Q"):]), cfg.hp_lambda)).reindex(qi)

    pop_star = np.exp(hp(np.log(pop.loc[pd.Period(cfg.start, "Q"):]), cfg.hp_lambda)).reindex(qi)

    # 構造失業率（UV分析、雇用失業率・雇用欠員率）
    ee, un = d["ee"].loc[:end], d["un"].loc[:end]
    U = 100 * un / (ee + un)
    vac = (d["vacancy_eff"] - d["placements"]).loc[:end]          # 千人
    V = 100 * vac / (vac + ee * 10)
    lnU, lnV = np.log(U), np.log(V)
    betas = {}
    for a, bnd in cfg.uv_periods:
        s = slice(pd.Period(a, "Q"), pd.Period(bnd, "Q"))
        _, beta, _ = gls_ar1_uv(lnU.loc[s].values, lnV.loc[s].values)
        mid = pd.Period(a, "Q") + (pd.Period(bnd, "Q") - pd.Period(a, "Q")).n // 2
        betas[mid] = beta
    bser = pd.Series(betas).reindex(lnU.index.union(pd.Index(list(betas)))).sort_index()
    bser = bser.interpolate(limit_area="inside").ffill().bfill().reindex(lnU.index)
    lnus = (lnU - bser * lnV) / (1 - bser)
    us = np.exp(lnus)                       # 構造雇用失業率（%）
    su = us * ee / (100 - us)               # 構造失業者数（雇用者数で代理）
    sur = 100 * su / (su + emp)             # 構造失業率
    sur_star = hp(sur.loc[pd.Period(cfg.start, "Q"):], cfg.hp_lambda).reindex(qi)

    emp_star = pop_star * part_star / 100 * (1 - sur_star / 100)
    out = pd.DataFrame({
        "emp": emp.reindex(qi), "hours": hours.reindex(qi),
        "emp_star": emp_star, "hours_star": hours_star,
        "part": part.reindex(qi), "part_star": part_star, "pop_star": pop_star,
        "struct_unemp": sur.reindex(qi), "struct_unemp_star": sur_star,
    })
    out["L"] = out.emp * out.hours
    out["L_star"] = out.emp_star * out.hours_star
    info = {"uv_betas": {str(k): round(v, 3) for k, v in betas.items()},
            "lfpr_recent_diff": round(float(d_recent), 3), "lfpr_jilpt_diff": round(float(d_jilpt), 3)}
    return out, info


# ---------------- 資本稼働率 ----------------
def _iip_sa_monthly(d):
    o = d["ip_mm_o"]
    st = seasonal_adjust_log(o, 12)
    off = d["ip_mm_s"]
    return pd.concat([st.loc[:off.index[0] - 1], off])


def _ita_monthly(d, link_2005: bool = False):
    sa = pd.concat([d["ita_s_link"].loc[:pd.Period("2017-12", "M")], d["ita_s"]])
    o = d["ita_o"]
    ratio = (o / d["ita_s"]).dropna()
    f = ratio.groupby(ratio.index.month).mean()
    pre = sa.loc[:pd.Period("2017-12", "M")]
    o_rec = pre * pre.index.month.map(f).values
    orig = pd.concat([o_rec, o])
    if link_2005 and "ita05_s" in d:
        # 2005年基準（2003年1月～）を、2008年の季調値の比（2020年基準接続÷2005年基準）で水準を合わせて2007年以前に接続。
        # 原指数も同じ比で接続する（2003～07年は実際の原指数）。
        k = float((d["ita_s_link"] / d["ita05_s"]).dropna().loc["2008-01":"2008-12"].mean())
        cut = pd.Period("2007-12", "M")
        sa = pd.concat([d["ita05_s"].loc[:cut] * k, sa.loc[cut + 1:]])
        orig = pd.concat([d["ita05_o"].loc[:cut] * k, orig.loc[cut + 1:]])
    return sa, orig


def _utilization(cap_m, prod_q, K_star, L_star, alpha, break_end, n_breaks, start, select="fixed"):
    capq = q_mean(cap_m)
    idx = capq.index.intersection(K_star.index).intersection(L_star.index)
    idx = idx[idx >= pd.Period(start, "Q")]
    lnA = np.log(capq.loc[idx]) - (1 - alpha) * np.log(K_star.loc[idx]) - alpha * np.log(L_star.loc[idx])
    fit_idx = idx[idx <= pd.Period(break_end, "Q")]
    y = lnA.loc[fit_idx].values
    if select == "bp":      # Bai-Perron 型の逐次検定で本数を決める（最大 n_breaks 本、5%）
        br, tests = bai_perron_hinge(y, max_breaks=n_breaks)
    else:                   # 本数を n_breaks に固定してSSE最小
        br, _ = best_breaks(y, n_breaks, min_seg=8)
        tests = None
    # 転換点はコロナ前の標本で探索し、係数は全標本で推計する（転換点の位置のみを固定）
    beta = np.linalg.lstsq(hinge_design(len(idx), br), lnA.values, rcond=None)[0]
    lnA_fit = pd.Series(hinge_design(len(idx), br) @ beta, index=idx)
    maxcap = np.exp(lnA_fit) * K_star.loc[idx] ** (1 - alpha) * L_star.loc[idx] ** alpha
    U = prod_q.reindex(idx) / maxcap
    return U, maxcap, ([fit_idx[b] for b in br], tests)


def capital_block(d, cfg: CAOConfig, alpha, lab):
    end = pd.Period(cfg.end, "Q")
    qi = pd.period_range(pd.Period(cfg.start, "Q"), end, freq="Q")
    K = split_stock(d, cfg.stock_last, cfg.end, lag=cfg.stock_lag).reindex(qi)
    Ks = pd.DataFrame({c: np.exp(hp(np.log(K[c]), cfg.hp_lambda)) for c in ["mfgT", "nmT", "intang"]})

    # 製造業・非製造業の潜在労働投入（毎勤の常用雇用指数で按分）
    sh = (q_mean(d["emfg_sa"]) / q_mean(d["e_sa"])).loc[:end]
    t = np.arange(len(sh)); b = np.polyfit(t[:32], np.log(sh.values[:32]), 1)
    back = pd.period_range(pd.Period("1980Q1", "Q"), sh.index[0] - 1, freq="Q")
    sh = pd.concat([pd.Series(np.exp(b[1] + b[0] * np.arange(-len(back), 0)), index=back), sh])
    sh = sh / sh.loc["2020Q1":"2020Q4"].mean() * 0.16          # 製造業のおおよその雇用シェアで水準を置く（比のみが効く）
    sh = sh.reindex(qi)
    L_m, L_n = lab.L_star * sh, lab.L_star * (1 - sh)

    # 製造業：鉱工業生産指数（原指数）にウォートン・スクール法
    ip_sa_q = q_mean(_iip_sa_monthly(d)).reindex(qi)
    wcap_m = wharton_capacity(d["ip_mm_o"], cfg.wharton_window)
    U_m, cap_m, br_m = _utilization(wcap_m, ip_sa_q, Ks.mfgT, L_m, alpha,
                                    cfg.capacity_break_sample_end, cfg.capacity_n_breaks, cfg.start,
                                    cfg.capacity_break_select)
    nb_n = cfg.capacity_n_breaks if cfg.capacity_n_breaks_nonmfg is None else cfg.capacity_n_breaks_nonmfg
    # 非製造業：第3次産業活動指数（2008年以降）
    ita_sa, ita_o = _ita_monthly(d, cfg.ita_link_2005)
    ita_sa_q = q_mean(ita_sa)
    wcap_n = wharton_capacity(ita_o, cfg.wharton_window)
    U_n, cap_n, br_n = _utilization(wcap_n, ita_sa_q, Ks.nmT, L_n, alpha,
                                    cfg.capacity_break_sample_end, nb_n, cfg.nonmfg_start,
                                    cfg.capacity_break_select)

    di_m = d["tankan_equip_mfg"].reindex(qi)
    di_n = d["tankan_equip_nonmfg"].reindex(qi)
    # 2008年より前の非製造業稼働率：2008年以降の 稼働率～短観DI 回帰の当てはめ値で補う
    ok = U_n.dropna().index
    rn = sm.OLS(U_n.loc[ok], sm.add_constant(di_n.loc[ok])).fit()
    U_n = U_n.reindex(qi)
    U_n = U_n.fillna(rn.params["const"] + rn.params[di_n.name] * di_n)
    U_m = U_m.reindex(qi)

    # 潜在稼働率：稼働率を短観設備判断DIで回帰した定数項（DI=0）
    rm = sm.OLS(U_m, sm.add_constant(di_m), missing="drop").fit()
    Us_m = rm.params["const"]
    rn2 = sm.OLS(U_n, sm.add_constant(di_n), missing="drop").fit()
    Us_n = rn2.params["const"]

    out = pd.DataFrame(index=qi)
    out["K"] = K.mfgT * U_m + K.nmT * U_n + K.intang
    out["K_star"] = Ks.mfgT * Us_m + Ks.nmT * Us_n + Ks.intang
    out["U_m"], out["U_n"] = U_m, U_n
    info = {"potential_util_mfg": round(float(Us_m), 4), "potential_util_nonmfg": round(float(Us_n), 4),
            "capacity_breaks_mfg": [str(x) for x in br_m[0]], "capacity_breaks_nonmfg": [str(x) for x in br_n[0]],
            "capacity_bp_tests_mfg": br_m[1], "capacity_bp_tests_nonmfg": br_n[1]}
    return out, info


def run(d, cfg: CAOConfig | None = None):
    cfg = cfg or CAOConfig()
    alpha = cfg.alpha if cfg.alpha is not None else labor_share(d, cfg.alpha_period)
    qi = pd.period_range(pd.Period(cfg.start, "Q"), pd.Period(cfg.end, "Q"), freq="Q")
    lab, linfo = labor_block(d, cfg)
    cap, cinfo = capital_block(d, cfg, alpha, lab)
    Y = d["gdp"].reindex(qi)
    lnA = np.log(Y) - (1 - alpha) * np.log(cap.K) - alpha * np.log(lab.L)
    lnA_star = hp(lnA, cfg.hp_lambda)
    lnY_star = lnA_star + (1 - alpha) * np.log(cap.K_star) + alpha * np.log(lab.L_star)
    res = pd.DataFrame(index=qi)
    res["gap"] = 100 * (Y / np.exp(lnY_star) - 1)
    res["pot_growth"] = 400 * lnY_star.diff()
    res["c_tfp"] = 400 * lnA_star.diff()
    res["c_cap"] = 400 * (1 - alpha) * np.log(cap.K_star).diff()
    res["c_hours"] = 400 * alpha * np.log(lab.hours_star).diff()
    res["c_emp"] = 400 * alpha * np.log(lab.emp_star).diff()
    res = res.join(cap[["U_m", "U_n"]]).join(lab[["part", "part_star", "struct_unemp_star"]])
    info = {"alpha": round(alpha, 4), **linfo, **cinfo}
    return res, info
