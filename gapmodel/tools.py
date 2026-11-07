"""共通の時系列処理：HPフィルター、季節調整、ウォートン・スクール法、区分線形回帰、構造転換点探索。"""
import numpy as np
import pandas as pd
from statsmodels.tsa.filters.hp_filter import hpfilter
from statsmodels.tsa.seasonal import STL


def hp(s: pd.Series, lam: float = 1600.0) -> pd.Series:
    s = s.dropna()
    _, trend = hpfilter(s.values, lamb=lam)
    return pd.Series(trend, index=s.index)


def seasonal_adjust_log(s: pd.Series, period: int) -> pd.Series:
    """原系列(正値)をSTLで季節調整。対数で処理し水準は保存。"""
    x = np.log(s.dropna())
    res = STL(x.values, period=period, robust=True, seasonal=13 if period == 12 else 7).fit()
    return pd.Series(np.exp(x.values - res.seasonal), index=x.index)


def seasonal_adjust_level(s: pd.Series, period: int) -> pd.Series:
    x = s.dropna()
    res = STL(x.values, period=period, robust=True, seasonal=7).fit()
    return pd.Series(x.values - res.seasonal, index=x.index)


def wharton_capacity(m: pd.Series, window: int = 12) -> pd.Series:
    """ウォートン・スクール法：前後window か月で最大の月をピークとし、ピーク間を直線で結ぶ。
    最初のピーク以前・最後のピーク以後は、それぞれ隣接ピーク値で横置き。"""
    x = m.dropna()
    v = x.values
    peaks = []
    for i in range(len(v)):
        lo, hi = max(0, i - window), min(len(v), i + window + 1)
        if v[i] >= v[lo:hi].max():
            peaks.append(i)
    cap = np.interp(np.arange(len(v)), peaks, v[peaks])
    return pd.Series(cap, index=x.index)


def hinge_design(n: int, breaks) -> np.ndarray:
    t = np.arange(n, dtype=float)
    cols = [np.ones(n), t] + [np.maximum(t - b, 0.0) for b in breaks]
    return np.column_stack(cols)


def fit_hinge(y: np.ndarray, breaks):
    X = hinge_design(len(y), breaks)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return beta, float(resid @ resid)


def best_breaks(y: np.ndarray, k: int, min_seg: int = 8, refine_iter: int = 5):
    """連続な区分線形トレンド（hinge）の屈折点を、SSE最小化で探す。
    k<=2 は全探索、それ以上は前進選択＋座標降下で局所改善（Muggeo法の実務的な代替）。"""
    n = len(y)
    cand = list(range(min_seg, n - min_seg))
    if k == 0:
        return [], fit_hinge(y, [])[1]
    if k == 1:
        best = min(((b,), fit_hinge(y, [b])[1]) for b in cand)
        return list(best[0]), best[1]
    if k == 2:
        best = (None, np.inf)
        for i, b1 in enumerate(cand):
            for b2 in cand[i:]:
                if b2 - b1 < min_seg:
                    continue
                s = fit_hinge(y, [b1, b2])[1]
                if s < best[1]:
                    best = ([b1, b2], s)
        return best
    # k >= 3: forward selection then refine
    br = []
    for _ in range(k):
        best = None
        for b in cand:
            if any(abs(b - c) < min_seg for c in br):
                continue
            s = fit_hinge(y, sorted(br + [b]))[1]
            if best is None or s < best[1]:
                best = (b, s)
        if best is None:
            break
        br = sorted(br + [best[0]])
    for _ in range(refine_iter):
        changed = False
        for j in range(len(br)):
            others = br[:j] + br[j + 1:]
            best = (br[j], fit_hinge(y, br)[1])
            for b in cand:
                if any(abs(b - c) < min_seg for c in others):
                    continue
                s = fit_hinge(y, sorted(others + [b]))[1]
                if s < best[1] - 1e-12:
                    best = (b, s)
            if best[0] != br[j]:
                br[j] = best[0]; br = sorted(br); changed = True
        if not changed:
            break
    return br, fit_hinge(y, br)[1]


def segmented_trend(s: pd.Series, max_breaks: int = 10, min_seg: int = 12):
    """屈折点数をBICで選ぶ区分線形トレンド。戻り値：(trend Series, breakpoints(Period list))"""
    x = s.dropna()
    y = x.values
    n = len(y)
    best = None
    for k in range(0, max_breaks + 1):
        br, sse = best_breaks(y, k, min_seg=min_seg)
        if len(br) < k:
            break
        p = 2 + 2 * k   # 係数 + 屈折点位置
        bic = n * np.log(sse / n) + p * np.log(n)
        if best is None or bic < best[0]:
            best = (bic, br)
    br = best[1]
    beta, _ = fit_hinge(y, br)
    trend = hinge_design(n, br) @ beta
    return pd.Series(trend, index=x.index), [x.index[b] for b in br]


def gls_ar1_uv(lnU: np.ndarray, lnV: np.ndarray):
    """内閣府DP/26-8 付録2の手順：OLS→残差AR(1)→Prais-Winsten変換で α, β を推計。"""
    X = np.column_stack([np.ones_like(lnV), lnV])
    c, a = np.linalg.lstsq(X, lnU, rcond=None)[0]
    e = lnU - (c + a * lnV)
    rho = float(np.dot(e[1:], e[:-1]) / np.dot(e[:-1], e[:-1]))
    w = np.sqrt(1 - rho ** 2)
    Us = np.r_[w * lnU[0], lnU[1:] - rho * lnU[:-1]]
    Vs = np.r_[w * lnV[0], lnV[1:] - rho * lnV[:-1]]
    Rs = np.r_[w, np.full(len(lnU) - 1, 1 - rho)]
    alpha, beta = np.linalg.lstsq(np.column_stack([Rs, Vs]), Us, rcond=None)[0]
    return float(alpha), float(beta), rho


def interp_ratio_q4(annual: pd.Series, qindex: pd.PeriodIndex) -> pd.Series:
    """暦年末の比率を各年Q4に置き、Q1-Q3は線形補間、範囲外は端の値で横置き。"""
    pts = pd.Series(annual.values, index=pd.PeriodIndex([f"{y}Q4" for y in annual.index], freq="Q"))
    full = pts.reindex(qindex.union(pts.index)).interpolate(limit_area="inside")
    full = full.ffill().bfill()
    return full.reindex(qindex)


# ---------------- X-11型 傾向循環（TC）成分 ----------------
def henderson_weights(n: int) -> np.ndarray:
    """Henderson移動平均（項数n=2m+1）の対称ウェイト。"""
    m = (n - 1) // 2
    N = m + 2
    j = np.arange(-m, m + 1)
    return (315 * ((N - 1) ** 2 - j ** 2) * (N ** 2 - j ** 2) * ((N + 1) ** 2 - j ** 2) * (3 * N ** 2 - 16 - 11 * j ** 2)
            / (8 * N * (N ** 2 - 1) * (4 * N ** 2 - 1) * (4 * N ** 2 - 9) * (4 * N ** 2 - 25)))


def _musgrave(w: np.ndarray, q: int, R: float) -> np.ndarray:
    """系列端でq期先までしかない場合のMusgrave非対称ウェイト（X-11/X-12と同じ考え方）。"""
    m = (len(w) - 1) // 2
    full = dict(zip(range(-m, m + 1), w))
    j = np.arange(-m, q + 1)
    N = len(j)
    b2 = 4 / (np.pi * R ** 2)
    c = (q - m) / 2
    miss = range(q + 1, m + 1)
    W = sum(full[i] for i in miss)
    D = sum((i - c) * full[i] for i in miss)
    return np.array([full[k] + W / N + (k - c) * b2 * D / (1 + N * (N - 1) * (N + 1) * b2 / 12) for k in j])


def henderson_tc(x, n: int = 13, R: float = 3.5) -> np.ndarray:
    x = np.asarray(x, float)
    T, m, w = len(x), (n - 1) // 2, henderson_weights(n)
    out = np.empty(T)
    for t in range(T):
        lo, hi = min(m, t), min(m, T - 1 - t)
        if lo == m and hi == m:
            out[t] = w @ x[t - m:t + m + 1]
        elif lo == m:
            out[t] = _musgrave(w, hi, R) @ x[t - m:t + hi + 1]
        elif hi == m:
            out[t] = _musgrave(w, lo, R)[::-1] @ x[t - lo:t + m + 1]
        else:
            out[t] = np.mean(x[t - lo:t + hi + 1])
    return out


def sa_and_tc_log(s: pd.Series, period: int = 12, forecast_ext: int = 0):
    """対数系列をSTLで季節調整し、X-11の規則（I/C比 <1:9項, 1–3.5:13項, >3.5:23項）でHenderson項数を選んでTC成分を返す。
    戻り値：(季調済み系列, TC成分, 選んだ項数, I/C比)。いずれも水準（exp戻し）。"""
    y = np.log(s.dropna())
    res = STL(y.values, period=period, robust=True, seasonal=13 if period == 12 else 7).fit()
    sa = y.values - res.seasonal
    ic = np.abs(np.diff(sa - res.trend)).mean() / np.abs(np.diff(res.trend)).mean()
    n = 9 if ic < 1 else (13 if ic < 3.5 else 23)
    R = {9: 0.99, 13: 3.5, 23: 4.5}[n]
    if forecast_ext:
        # X-12-ARIMAと同様、系列端は予測値で延長してからフィルターをかける（端点での改定を小さくする）
        from statsmodels.tsa.arima.model import ARIMA
        f = ARIMA(sa, order=(0, 1, 1), trend="t").fit().forecast(forecast_ext)
        tc = henderson_tc(np.r_[sa, f], n, R)[:len(sa)]
    else:
        tc = henderson_tc(sa, n, R)
    idx = y.index
    return pd.Series(np.exp(sa), index=idx), pd.Series(np.exp(tc), index=idx), n, float(ic)


# ---------------- Bai-Perron 型の逐次検定（連続な屈折トレンド） ----------------
# Bai & Perron (2003) Table 1 の supF(l+1|l) 臨界値（q=1：屈折1つで傾き1つが変わる、トリミング15%）
BP_CRIT_Q1 = {0.10: [7.04, 8.51], 0.05: [8.58, 10.13], 0.01: [12.29, 13.89]}


def _hac_wald_last(y, X, maxlags):
    """最後の説明変数の係数＝0 の Wald 統計量（Newey-West 分散）。"""
    import statsmodels.api as sm
    r = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    return float(r.params[-1] ** 2 / r.cov_params()[-1, -1])


def bai_perron_hinge(y: np.ndarray, max_breaks: int = 2, eps: float = 0.15, alpha: float = 0.05,
                     hac: bool = True):
    """屈折点の数を逐次 supF(l+1|l) 検定で決める（DP/26-8「最大2回、有意水準5%で自動選択」に対応）。
    残差の系列相関が強いので、既定で Newey-West の分散を使う（hac=False なら通常のF＝Wald）。
    戻り値：(屈折点の位置リスト, 各段の (統計量, 臨界値) のリスト)"""
    n = len(y)
    h = max(2, int(round(eps * n)))
    maxlags = int(np.floor(4 * (n / 100) ** (2 / 9)))
    breaks, tests = [], []
    for l in range(max_breaks):
        best = None
        for b in range(h, n - h):
            if any(abs(b - c) < h for c in breaks):
                continue
            X = hinge_design(n, sorted(breaks))
            X = np.column_stack([X, np.maximum(np.arange(n, dtype=float) - b, 0.0)])
            if hac:
                w = _hac_wald_last(y, X, maxlags)
            else:
                beta, *_ = np.linalg.lstsq(X, y, rcond=None)
                e = y - X @ beta
                s2 = e @ e / (n - X.shape[1])
                cov = s2 * np.linalg.inv(X.T @ X)
                w = beta[-1] ** 2 / cov[-1, -1]
            if best is None or w > best[1]:
                best = (b, w)
        crit = BP_CRIT_Q1[alpha][l]
        tests.append((round(best[1], 2), crit))
        if best[1] < crit:
            break
        breaks = sorted(breaks + [best[0]])
        # 既に入れた屈折点の位置を、本数を固定したSSE最小で取り直す
        breaks, _ = best_breaks(y, len(breaks), min_seg=h)
    return list(breaks), tests
