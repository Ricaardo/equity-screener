# value_ratehike Profile — 长期价值 + 加息预期姿态

可选评分 profile，**加法、默认不变、可逆**。为"留主动进攻余地"的长期价值布局而设，
在加息预期下倾向短久期、强现金流、低杠杆、价值/质量。

## 启用

环境变量选择（不设 = 默认模型，行为完全不变）：

```bash
AH_PROFILE=value_ratehike ah-screener report
AH_PROFILE=value_ratehike ah-screener expert-score   # 视命令而定
```

未知 profile 名自动回退默认（健壮）。

## 它改了什么（A/H expert_model 线）

仅 reweight 两个 composite（`src/ah_screener/weights.py`）：

| 维度 | 默认 | value_ratehike | 方向 |
|---|---|---|---|
| fundamental_score | 0.18 | **0.26** | ↑ 价值核心 |
| technical_score | 0.14 | **0.08** | ↓ 仅择时 |
| industry_fit | 0.10 | 0.08 | ↓ |
| peer | 0.06 | 0.04 | ↓ |
| master_score | 0.20 | 0.22 | ↑ |
| china_master / liquidity | 0.28 / 0.04 | 0.28 / 0.04 | = |

大师混合（MASTER_COMPOSITE）同步倾向价值：

| 大师 | 默认 | value_ratehike |
|---|---|---|
| graham(深度价值) | 0.18 | **0.26** ↑ |
| buffett(质量/护城河) | 0.22 | **0.28** ↑ |
| lynch(GARP) | 0.18 | 0.20 ↑ |
| fisher | 0.22 | 0.18 ↓ |
| oneil(动量) | 0.20 | **0.08** ↓ |

**为何温和版不另加债务/久期 overlay**：`fundamental_score` 已内含 债务率/ROE/
经营现金流 质量，温和档 reweight 即可表达"加息下低杠杆强现金流更稳"。中/激进档
再加显式利率敏感 overlay（读 FRED 利率 × 个股 debt_asset_ratio）。

## US 线（us_screener，2026-06-06 已接）

`US_VALUE_RATEHIKE_COMPOSITE` —— 同款价值·抗加息倾斜：

| 维度 | 默认 | value_ratehike |
|---|---|---|
| fundamental | 0.24 | **0.32** ↑ |
| valuation | 0.14 | **0.22** ↑ 价值 |
| macro(利率传导) | 0.10 | **0.14** ↑ |
| technical | 0.20 | **0.10** ↓ |
| heat(动量) | 0.18 | **0.08** ↓ |
| liquidity | 0.14 | 0.14 = |

注入：`src/us_screener/scoring_us.py` `_us_score_weights()` 按 `AH_PROFILE` 选择。

## 四维落地现状

- **基本面** ✅ 重（A/H 0.26+价值大师；US 0.32）
- **技术面** ✅ 保留但轻（A/H 0.08；US 0.10，择时非驱动）
- **前景(forward)** ⚠ **按项目设计保持报告注释、不入分** —— 批量 forward 数据不免费
  (`scoring_us.py:485`)，强行入分违背项目免费数据纪律。价值倾向已由 valuation↑ 表达。
- **行情推演(scenario)** ⚠ 由独立 `potential` 模块承担（potential-scan，补充而非入分）；
  value_ratehike 候选应与 `ah-screener potential-scan` 交叉参考，而非塞进 expert_score。

## 验证状态（2026-06-06）

`ah-screener expert-validate` 当前 **samples=0**（项目仅运行 ~2 周，无足够 40 日前瞻
收益历史）—— 默认模型与本 profile **都无法现在验证**。需随每日快照积累数周后再跑。
⚠ 注意：`AH_PROFILE=... expert-score` 会**覆盖共享 DB 的默认分**；要让 profile 与默认
并存验证，需指向独立 DB（`AH_SCREENER_DB=...`）或后续加快照打标（未做）。

## ⚠ 纪律（必读）

与默认权重同性质：**手设先验，未经前瞻收益校准**。和 `weights.py` 开头的
PROVENANCE 一致——**用前必须样本外验证**：

```bash
ah-screener expert-validate          # 验证 value_ratehike 分桶前瞻超额收益
ah-screener potential walk-forward   # 阈值前瞻
```

验证通过才视为 edge；否则只当先验。默认 composition 仍由
`tests/test_scoring_weights.py` 锁定，本 profile 是独立常量、不改默认路径。

## 改动文件

- `src/ah_screener/weights.py` — 新增 `VALUE_RATEHIKE_COMPOSITE` /
  `VALUE_RATEHIKE_MASTER_COMPOSITE` / `PROFILES`
- `src/ah_screener/expert_model.py` — `_profile_dict()` 按 `AH_PROFILE` 选择，
  注入 composite(870) 与 master_composite(862)
