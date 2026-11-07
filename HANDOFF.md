# 引き継ぎメモ：GDPギャップ推計プロジェクト（2026-11-08 朝時点）

新しいチャットはこのメモを最初に読む。詳しい検証結果は `findings_replication.md`、使い方は `gapmodel_README.md`（プロジェクト内の claude/ 以下）。

## 目的
内閣府「GDPギャップ」と日銀「需給ギャップ」を公表前に推計して当てる。GDP速報・短観の公表後すぐ出すのが目標。民間予測（ESP）を使う超早期推計も行う。

## 作業環境
- 実体はユーザーのPC：`C:\Users\KuriyamaHiromasa\OneDrive - 松下政経塾\GDPgap`（Windows、Python 3.12）。
  - 新しいチャットでは、デスクトップアプリの「フォルダを追加」で `GDPgap` をつなぎ直す必要がある（device_request_folder_access でも可）。
- Claudeの作業環境・PC内の作業用Linux環境とも政府サイトに届かない。取得はユーザーがPowerShellで実行し、Claudeはフォルダの報告を読む。PC内Linux環境には statsmodels が入っておらず pip も届かないので、推計スクリプトはそこでは動かない（読むだけ）。
- PCへのファイル送付は device_commit_files。同じ stagedPath を再利用すると古い中身が書かれることがあったので、毎回新しいファイル名で stage し、送付後に md5sum で一致を確認する。
- e-Stat APIのアプリケーションIDは `estat_appid.txt`（.gitignore で除外。中身はチャットに出さない）。
- GitHub：`uniyama-soy-source/gdp-gap-uniyama`（公開、GitHub Desktop でコミット・プッシュはユーザーが行う）。
  - Release：`cao-2026Q3-nowcast-esp10`（11/7、7-9月期の超早期推計+0.58%）。
  - Tagの付け方：`<cao|boj>-<期>-<nowcast-espNN|qe1|qe2>`。

## 主なコマンド（GDPgap フォルダで）
- `python update_data.py`：全統計を取得して data/raw・data/published を更新（`--dry-run`、`--only gdp,iip,...`）。
  - 名前：gdp, iip, ita, mk, lfs, stock, tankan, lfs_sexage, mk_days, cao_pub, pesa, lfs_hours, boj_pub
- `python estimate_gdp_gap.py --end 2026Q3`：内閣府。推奨値は接続値（前期の公表値＋モデルの変化幅）。実額（兆円）、潜在成長率と寄与度（接続値）も出す。未公表の月次は直近2か月平均で補完。
- `python nowcast_gdp_gap.py --end 2026Q3 --gdp-saar <ESPの年率>`：GDP速報前の超早期推計。
- `python simple_nowcast.py --now <前期公表値> --g <今期の前期比>`：超単純予測（前期＋成長率−0.115）。
- `python estimate_boj_gap.py --end 2026Q3`：日銀。推奨値はモデル水準。参考として、労働時間の不規則変動を除かない版、潜在成長率（TFP込み、半期・四半期）、TFP込みギャップも出す。
- `python plot_gaps.py --end 2026Q2`：図（results/gaps_<期>.png）。
- 検証用：`backtest_realtime2.py`、`fetch_qe_vintages.py`（過去のGDP速報を data/vintages/esri に取得、76回分取得済み）。

## 精度の目安（決めたこと）
- 内閣府：接続値のリアルタイム誤差 RMSE 0.255（2020〜26年の22回）、2023年以降0.27。誤差のほぼすべては内閣府による前期の値の改定（0.25）、モデルの変化幅の誤差は0.08。
  - 前期のGDP改定率やモデルの前期改定で補正すると悪化 → 不採用。
  - 新手法（2025年12月〜）では2回とも誤差小（+0.13、−0.04）。以後1回ずつ記録して判断する。
- 日銀：需給ギャップ RMSE 0.21。2026Q2は10/5時点の情報で0.552（公表0.550）。潜在成長率は公表（半期）とRMSE 0.12。
  - 労働時間ギャップは端点で動くので、日銀公表時点（翌々月までのデータ）に合わせて推計する。

## 現在の推計値
- 内閣府 2026Q3 超早期推計（11/6、ESP10月調査 年率+0.08%）：接続値+0.58%（実額 約3.5兆円）、潜在成長率0.6%（接続値。TFP 0.4／資本 0.2／労働時間 −0.3／就業者 0.3。モデルそのままは0.52%）。超単純予測0.60%。
- 日銀 2026Q3（暫定、生産能力指数9月分は未反映）：+0.58%、潜在成長率（四半期の前年比）0.86%。

## これからの予定
- 11/12 15:00頃 ESP11月調査 → nowcast を更新し、forecasts/ に記録 → GitHub Release（cao-2026Q3-nowcast-esp11）。記録には精度0.255pt、潜在成長率は接続値とモデル値の両方を書く。
- 11/16 8:50 GDP 1次速報 → `update_data.py --only gdp` → `estimate_gdp_gap.py --end 2026Q3`。13:30に鉱工業確報（生産能力指数9月分）→ 日銀も再推計。
- 11/17 13:30 第3次産業活動指数9月分 → `--only ita` で再推計。
- 内閣府の7-9月期公表（11月末〜12月初め、推定）→ `--only cao_pub` で取り込み、答え合わせ。前期改定の幅を記録（新手法での当たり方の確認）。
- note記事「ClaudeでGDPギャップを作ってみた」（Claude Docs、https://claude.ai/artifact/JqkhFfKMn77ynB7nS6hqtb）：おまけの表の【記入】3か所（11/12、11/16、内閣府公表値）を埋める。11/8に提案（suggestion）を3件置いた：精度を22回の0.26pt（直近5回は0.16pt）に直す2件、潜在成長率0.6%がリリース記録の0.52%（モデル値）と違う理由の注記1件。ユーザーの承認待ち。公開は内閣府公表後の予定。
- forecasts/2026Q3_nowcast_ESP10.md に11/8の追記（精度0.255pt、潜在成長率の接続値0.6%）を足した。推計値は不変。
- 手順書（Claude Docs、https://claude.ai/artifact/3xd4UcdbnMDJotPH3Yc49Z）には自動取得の節を追記済み。

## 手入力のまま残っているもの
生産QNA、ESP、毎勤の製造業常用雇用（季調指数、推計では直近値で補完）、年次推計の2ファイル（ストックマトリックス・労働分配率、年1回）。

## 未コミット（11/8朝時点）
README・estimate_boj_gap.py・update_data.py・gapmodel（data/ingest/boj_potential）・simple_nowcast.py・plot_gaps.py・backtest_realtime2.py・HANDOFF.md・data/vintages/esri（76回分）・forecasts の追記などが未コミット。GitHub Desktop でコミット・プッシュする。
