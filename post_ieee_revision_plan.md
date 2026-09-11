# IEEE Sensors Journal desk-rejection 後の改訂・再投稿計画

## 1. デスクリジェクション理由との対応方針

IEEE Sensors Journal からの Editorial comments（a, b, c）に対する対応を、Discussion 追加と併せて整理する。

| コメント | 指摘の本質 | 対応方針 | 作業量 |
|---|---|---|---|
| (a) 単純な event-only ベースラインだけで、EDnCNN など最新学習ベース denoiser と比較していない | 実用上の優位性が示されていない | EBSSA/DVSNOISE20 上で、可能な範囲で学習ベース・対照法（EDnCNN, WedNet, ASTEDNet, MLPF, EDformer, Shiba et al. Contrast Maximisation 等）を追加評価。座標・時刻のみで動作する「トレーニング不要」という立場を明確にし、公平でない条件を明記する。 | Medium–High |
| (b) 図の軸ラベル・目盛り・凡例フォントが小さく、sub-figure ラベルも不統一 | 印刷/PDF で判読不能、仕上がり不良 | `matplotlib` のフォントを全面的に拡大し、sub-panel ラベル・線幅も統一して再生成 | Low |
| (c) EBSSA 1 データセット・1 センサーモデルに限定 | 汎化性・一般化が未検証 | DVSNOISE20、SciDVS 等の追加データ・シミュレーション、照度変化や他 DVS ハードウェアでの妥当性確認を追加（または将来課題として明示） | High |

## 2. 今回反映済み：Discussion の構成変更

`create_ieee_docx.py` で Discussion を以下の構成に改訂済み。

- 7.1 **Implications**：Fano/SR 手法の意義を「最も SNR が高い denoiser」ではなく「物理的に解釈可能な、トレーニング不要でイベントのみで動作する最適閾値設計ルール」として位置づける。最新の高 SNR 学習手法には劣る可能性があることを認めつつ、A5 シミュレーション 5.4× 平均 SNR 向上（最大 9.95×）、EBSSA 実データの ROC-AUC・SPR・NRR 指標と連動して論じる。
- 7.2 **Limitations and future work**：元の内容を維持し、EDnCNN 等との拡張ベンチマーク・SciDVS/DVSNOISE20 検証・Starlink による Cal-6 整備を将来課題として列挙。

生成物：`manuscript_ieee_sensors.docx` / `manuscript_ieee_sensors.pdf` および付属ファイル一式を `build_ieee_submission.py` で再生成済み。

## 3. 全体構成の再検討案

次回投稿用に、大きく 3 通りの再構成を提示する。

### A. センサー・測定系誌（Sensors / IEEE TIM / Electronics）

**アングル**：「DVS ピクセルの物理モデルに基づく、トレーニング不要のノイズ逆問題 denoising」

- Abstract・Introduction で sensor characterization / measurement science を強調。
- Methods に A5 モデル、Fano フィルタ、PI-DC-DVS を据え、Calibration tier（Cal-1 ~ Cal-6）を明瞭にする。
- Results に EBSSA 実データ評価 + A5 シミュレーションを配置。
- Discussion で (a)(b)(c) を先送りではなく「既に実施した対応」として示す必要がある。

### B. 信号処理・画像処理誌（Signal Processing / IEEE Access）

**アングル**：「閾値検出器のための確率共鳴最適化に基づく event denoising」

- 理論的ポジションを強化：閾値検出器の共通枠組みとして DVS 以外にも適用可能な可能性を強調。
- ベンチマークを充実させ、信号処理コミュニティで使われる DVSNOISE20・E-MLB 等で比較。
- ハードウェア実装の詳細は補足資料に回す。

### C. 光学・宇宙応用誌（Optics Express / Aerospace）

**アングル**：「Event camera を用いた低輝度衛星追尾における背景ノイズ除去」

- Introduction を space situational awareness（SSA）/ 暗弱天体観測に特化。
- DVS の光子計測特性を強調し、光学系の検出限界 magnitude 議論を前面に出す。
- 図の可視性向上が特に重要（暗背景上の軌道）。

## 4. ベンチマーク拡張の具体案

IEEE Sensors 編集の (a) に答えるため、比較候補を優先度つきで整理する。

| 手法 | 参考 | 必要な入力 | EBSSA で実行可能か | 備考 |
|---|---|---|---|---|
| EDnCNN (Baldwin et al., CVPR 2020) | [25] | 強度画像 or イベント + ラベル | 困難（ラベル・フレームなし） | DVSNOISE20 で再現可。引用は維持しつつ fair 比較でないことを明記 |
| WedNet (Fang et al., 2024) | - | イベントウィンドウ | 可能か要確認 | リアルタイム性を強調する論文 |
| ASTEDNet | - | イベントストリーム | 可能か要確認 | フレーム変換不要 |
| AEDNet | - | イベントデータ | 要確認 | イベントのみ |
| MLPF (Rios-Navarro et al., CVPRW 2023) | - | イベント + メタパラメータ | 要確認 | ピクセルレベル分類 |
| EDformer (ECCV 2024) | - | イベントトークン | 困難 | 大規模アーキテクチャ |
| Shiba et al. (extended Contrast Maximisation) | `mlst_submission/main.tex` | イベント + 自己運動 | 要確認 | 現状で最も概念が近い |

**推奨**：まず EBSSA 上で「座標・時刻のみ」で動作する軽量な event-only baseline をすべて実装済みなので、次に DVSNOISE20 上で EDnCNN / simple CNN / MLPF 等を再現し、event-only 版と比較する。 EBSSA でラベルがない場合は DVSNOISE20 のラベルを用いて ROC-AUC/F1 を出し、EBSSA では定性・定量両面で結果を示す。

## 5. 図表改善チェックリスト

`results_in_engineering_submission/generate_sr_figures.py` の `plt.rcParams` と inline `fontsize` を更新する。

- [ ] `font.size` 10 → 14
- [ ] `axes.labelsize` 11 → 16
- [ ] `axes.titlesize` 12 → 16
- [ ] `legend.fontsize` 9 → 12
- [ ] `xtick.labelsize` / `ytick.labelsize` 9 → 12
- [ ] 散布図・等高線図の inline `fontsize=7/8/9` も 12–14 に統一
- [ ] sub-figure ラベル（a, b, c…）を `fig.text` で一括配置、サイズ 16
- [ ] 軸・曲線の linewidth を 1.5–2.0 に統一
- [ ] カラーマップの colorbar ラベルも拡大
- [ ] 生成後、実際に縮小印刷・2 段組で判読テスト

## 6. 再投稿先候補比較表

| 候補 | Publisher | IF (参考) | APC | ハイブリッド | Scope / Fit | 作業コスト | 推奨 |
|---|---|---|---|---|---|---|---|
| **Sensors** | MDPI | 3.5 (2024) / 4.0 (2025) | CHF 2,600 | なし (Gold OA) | センサー科学・技術。DVS イベントセンサーに強い親和性。 | Low–Medium（ベンチマーク追加・図修正で対応可） | ◎ |
| **IEEE Trans. Instrum. Meas.** (TIM) | IEEE | 5.9 (2024) / 7.0 (2025) | 購読誌無料、OA $2,645+ (2025) | あり | 計測・計器・センサー。実験的妥当性を重視。 | High（学習ベース比較・多センサー検証が必須） | ◎ |
| **Signal Processing** | Elsevier | 3.4–3.7 | OA ~$3,190 | あり | 信号処理理論・応用。手法一般化に向く。 | Medium（再フォーマット + ベンチマーク） | ○ |
| **Optics Express** | Optica | 3.3–3.4 | $2,300–2,550 (15 ページ以内) | なし (Gold OA) | 光学・フォトニクス革新。DVS は光検出器、SSA 適用も可能。 | Medium（光学側の導入を強化、ページ制限留意） | ○ |
| **IEEE Access** | IEEE | 3.6 (2024) / 4.2 (2025) | $2,160 | なし (Gold OA) | 学際・迅速出版。スコープは広い。 | Low（軽微な再フォーマットで可） | △（学術的ブランド弱） |
| **Electronics** | MDPI | 2.9 (2025) | CHF 2,400 | なし (Gold OA) | エレクトロニクス応用。DVS ピクセル回路にも近い。 | Low–Medium | △（IF 低め） |
| **Journal of Computational Mathematics and Data Science** | Elsevier | 2.7–4.4 | $1,370 | なし (Gold OA) | 計算数学・データ科学。DVS センサー実験が少なすぎる。 | High（大幅な再構成が必要） | ×（スコープ外） |
| **Journal of Statistical Planning and Inference** | Elsevier | ~0.8–1.0 | OA $3,170 | あり | 統計理論。応用センサー研究の場ではない。 | Very high | ×（スコープ外） |

**作業コスト目安**：Low = 1 セッション程度、Medium = 2–3 セッション、High = 4 セッション以上。

## 7. 推奨アクション

1. **Discussion 追加・原稿生成** → 済（`create_ieee_docx.py` 更新、`manuscript_ieee_sensors.docx/pdf` 再生成）。
2. **図表フォント拡大** → 次の大改訂で必須。`generate_sr_figures.py` の `rcParams` を修正し、全 PNG + PPTX を再生成する。
3. **ベンチマーク拡張** → 優先度最高。DVSNOISE20（および可能であれば EBSSA 内の追加記録）で EDnCNN・MLPF・Shiba et al. 等と比較。
4. **汎化性実験** → 照度変化・SciDVS・追加 DVS モデル（DAVIS346 等）で定性評価。不可能な場合は「Limitations」で将来課題として具体的に述べる。
5. **次の投稿先選定** → 推奨は **Sensors (MDPI)** または **IEEE TIM**。速さを取るなら Sensors、IF・ブランドを取るなら TIM（ただしベンチマーク充実が必須）。

## 8. 生成物

- `dvs_noise_inverse_problem/ieee_sensors_journal_submission/manuscript_ieee_sensors.docx`
- 付属：`highlights_ieee_sensors.docx`, `title_page_ieee_sensors.docx`, `cover_letter_ieee_sensors.docx`, `figures_ieee_sensors.pptx`, `graphical_abstract_ieee_sensors.png`
- 図ファイル：`ieee_figures/`, `editorial_figures/`

すべて `build_ieee_submission.py` から再生成可能。
