"""推計上の「決め」をまとめた設定。

数値の根拠は README.md の「決め一覧」を参照。値を変えるときはここだけを書き換える。
"""
from dataclasses import dataclass, field


@dataclass
class CAOConfig:
    # 推計期間
    start: str = "1994Q1"
    end: str = "2026Q2"
    # 労働分配率：SNA算式（雇用者報酬/(雇用者報酬+営業余剰・混合所得(総)-家計の営業余剰・混合所得(総))）の
    # 暦年1994-2024平均。逆算値（0.594）とは約0.01差（findings_backout.md）。
    alpha: float | None = None          # None なら labor_share_sna.csv から計算
    alpha_period: tuple = (1994, 2024)
    hp_lambda: float = 1600.0
    # 資本ストック：公表時点で固定資本ストック速報が出ている最終期。これより後は直近4期平均の伸びで延長。
    stock_last: str = "2026Q1"
    stock_lag: int = 1                  # 前期末ストックを当期の資本とする
    # 稼働率
    wharton_window: int = 12            # 前後12か月で最大ならピーク
    capacity_break_sample_end: str = "2019Q4"   # Bai-Perronで構造転換点を探す期間の終期（コロナ前）
    capacity_n_breaks: int = 2                  # 製造業（Bai-Perronで2つ：DP/26-8）
    capacity_n_breaks_nonmfg: int | None = None # 非製造業（Noneなら製造業と同じ）
    capacity_break_select: str = "fixed"        # "fixed"=本数固定／"bp"=Bai-Perron型の逐次検定（上の本数が上限）
    nonmfg_start: str = "2008Q1"        # 第3次産業活動指数（総合）の始期。2005年基準を接続すれば "2003Q1"
    ita_link_2005: bool = False         # 2005年基準（2003年～）を2007年以前に接続する
    # --- 2026-11-10 追加（findings_replication.md「Fableによる検証」） ---
    # 非製造業の原指数（2017年以前）。
    # "linked"=接続指数「第３次産業総合（直接調整法）」の原指数（1988年～。2018年以降は総合の原指数と同じ値）。既定。
    # "synthetic"=季調済接続指数×2018年以降の平均季節比で作った合成値（2026-11-10までの既定。比較用に残す）。
    ita_orig_source: str = "linked"
    # 鉱工業生産の季節調整値（2017年以前）。"linked"=接続指数の季節調整済指数（実データ、1978年～）。既定。
    # "stl"=原指数に手元でSTLをかけた代用品（2026-11-10までの既定。比較用に残す）。
    iip_sa_source: str = "linked"
    # 以下は既定値のままなら結果は変わらない。
    # 生産能力関数の係数を推計する期間。"full"=全期間（従来）／"break_sample"=転換点の探索期間（コロナ禍前）
    # だけで推計し、以降は外挿。DP/26-8 図8（製造業の稼働率）は "break_sample" でないと再現できない。
    capacity_coef_sample: str = "full"            # 製造業
    capacity_coef_sample_nonmfg: str = "full"     # 非製造業（図がなく確かめられないため従来のまま）
    # 転換点を固定する（None=残差平方和最小で探す）。製造業の ("2002Q4", "2018Q1") は図8の稼働率から逆算した値。
    capacity_breaks_fixed: tuple | None = None
    capacity_breaks_fixed_nonmfg: tuple | None = None
    # 労働
    jilpt_lfpr: dict = field(default_factory=lambda: {2025: 63.0, 2030: 63.9})  # 成長率ベースライン・労働参加漸進
    lfpr_recent_years: tuple = (2023, 2025)     # 足元トレンドを測る年（前年差の平均）
    hours_forecast_growth: float = -0.25        # 労働時間の将来予測（%/年）。性年齢別横置きの構成変化の代理
    forecast_to: str = "2030Q4"
    uv_periods: tuple = (("1983Q1", "1989Q4"), ("1990Q1", "1993Q4"),
                         ("2001Q1", "2006Q4"), ("2009Q1", "2014Q4"))   # 1975年は資料期間外のため除外


@dataclass
class BOJConfig:
    start: str = "1994Q1"
    end: str = "2026Q2"
    # 労働分配率：初期値はSNA算式の長期平均。公表寄与度との照合で逆算した値に置き換え可能。
    alpha: float | None = None
    alpha_period: tuple = (1994, 2024)
    hp_lambda: float = 1600.0
    uv_beta: float = -0.396             # 公表の就業率ギャップから逆算（論文値 -0.39）
    lfpr_max_breaks: int = 10
    lfpr_min_seg: int = 12              # 区分線形回帰の最小区間（四半期）
    util_mean_period: tuple = ("1994Q1", None)   # 稼働率の長期平均をとる期間
    qna_last: str = "2026Q1"            # 公表時点で利用可能だった生産QNAの最終期
    stock_last: str = "2026Q1"
    capacity_trend: bool = False        # 生産能力の補正回帰にタイムトレンドを入れるか（逆算でFalseを採用）
    vacancy_correction: str = "published_ratio"   # "published_ratio" or "none"
    vacancy_ratio_until: str | None = None        # 公表V/U比を使う最終期（None=手元の公表値すべて）
    calibrate_kgap_level: bool = True   # 資本投入ギャップの水準を公表値の過去平均に合わせる
    calib_period: tuple = ("1994Q1", "2019Q4")   # 較正に使う期間（直近は評価用に外す）
    # 出勤日数（区分線形トレンド）×1日当たり時間（HP）への分解（日銀2026年改定）。実装済みだが、転換点数で
    # 最新期が0.36〜1.03と大きく振れ、安定して改善しないため既定はFalse（総時間TCにHP）。findings_replication.md 参照。
    hours_split: bool = False
    days_max_breaks: int = 5
    days_min_seg: int = 12
    tc_forecast_ext: int = 12           # TC抽出前にARIMA(0,1,1)で何か月延長するか（X-12流の端点処理）。0で延長なし
    hours_tc: bool = True               # 労働時間に傾向循環（TC）成分を使う（日銀2026年改定 脚注13）
    hours_source: str = "lfs"           # "lfs"=労調 平均週間就業時間（非農林業従業者）／"mk"=毎勤 総実労働時間
    # 製造工業生産の季節調整値（2017年以前）。"linked"=接続指数の季節調整済指数（実データ、1978年～）。既定。
    # "stl"=原指数に手元でSTLをかけた代用品（2026-11-10までの既定。比較用に残す）。
    iip_sa_source: str = "linked"


# 設定の組（compare_presets.py / backtest_realtime2.py の --preset で選ぶ）。"current" が既定。
CAO_PRESETS = {
    "current": {},
    # 2026-11-10までの既定：非製造業の原指数（2008〜17年）が合成値
    "synthetic": {"ita_orig_source": "synthetic", "iip_sa_source": "stl"},
    # 鉱工業の季節調整値だけ従来の代用品に戻す（影響の切り分け用）
    "iip_stl": {"iip_sa_source": "stl"},
    # 製造業の生産能力関数を DP/26-8 図8 に合わせる（転換点2つを図から決めている）
    "fig8": {"capacity_coef_sample": "break_sample", "capacity_breaks_fixed": ("2002Q4", "2018Q1")},
}
