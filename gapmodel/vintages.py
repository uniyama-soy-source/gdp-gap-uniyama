"""内閣府「今週の指標」から拾ったGDPギャップの公表時点別の値（data/published/cao_vintages_raw.txt）を読む。"""
from pathlib import Path
import pandas as pd

PUB = Path(__file__).resolve().parent.parent / "data" / "published"


def load_cao_vintages() -> tuple[pd.DataFrame, pd.DataFrame]:
    """戻り値：(issues, long)。issues＝号ごとの情報、long＝号×推計（今回/前回）×期 の値。"""
    iss, rows = [], []
    for l in (PUB / "cao_vintages_raw.txt").read_text(encoding="utf-8").splitlines():
        if not l.strip() or l.startswith("#"):
            continue
        p = l.split("|")
        if p[0] == "R":
            _, no, kind, start, vals = p[:5]
            v = [float(x) for x in vals.split(",") if x.strip()]
            for i, x in enumerate(v):
                rows.append({"号": int(no), "推計": kind, "期": pd.Period(start, "Q") + i, "GDPギャップ": x})
        else:
            iss.append({"号": int(p[0]), "公表日": pd.Timestamp(p[1]), "対象期": pd.Period(p[2], "Q"),
                        "速報": p[3], "本文の値": float(p[4]), "メモ": p[5] if len(p) > 5 else ""})
    issues = pd.DataFrame(iss).sort_values("公表日").reset_index(drop=True)
    long = pd.DataFrame(rows).merge(issues[["号", "公表日", "対象期", "速報"]], on="号")
    return issues, long
