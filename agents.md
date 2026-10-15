# AGENTS.md — 美债期货期权 Put Ratio Spread 回测系统

> 本文件定义目标规格和验收标准，不代表所有功能已在代码中实现。若现有代码与本文不一致，应标记差异、补测试并逐步实现；不得仅凭文档宣称功能已正确。

最后更新：2026-10-15

## 1. 项目目标与原则

仓库包含 `Code/`（现有 Bloomberg 仪表盘）和 `backtest/`（独立回测系统）。除非明确要求，不要破坏现有仪表盘。回测目标是研究美国国债期货期权 Put Ratio Spread，采用严格 walk-forward、保守报价和可审计的组合账本。

不可违反：
- 任意决策只能使用决策时点已经可获得的数据。
- 每月建立新组合；每个组合独立持有至其自身到期，不因下个月建仓而移仓、平仓或修改旧组合。
- 同一组合内所有 Put 到期日必须相同，并按对应交易所规则的到期/最终结算日结算。
- 买入腿使用有效 Ask，卖出腿使用有效 Bid。无效、缺失或过时报价不得静默替换成 Mid、Last 或未来报价。
- 本项目中的 `payoff` 指组合从建仓到到期的总盈亏，而非单独的到期内在价值。
- Bloomberg 报价单位、期权乘数、期货规格、tick size、到期日和最终结算规则必须核实并记录来源。
- 合成数据只验证程序行为，不代表真实市场表现。

## 2. 策略规格

### 2.1 月度建仓与持仓生命周期

- 默认每月第一个有效交易日建仓；规则应配置化并由统一交易日历实现。
- 每个建仓日创建新的 `trade_id`。组合持有至自己的到期日，不设置主动止损、提前平仓或月度移仓。
- 不同月份的组合可能同时存续。Portfolio Ledger 必须逐笔记录，不能把新组合当成旧组合的替代。
- 到期日应由经核实的合约/交易所日历确定，不能简单假设为自然月最后一天。
- 合约选择和存续组合生命周期必须分离：新组合可以选择当月适用的合约，但旧组合的 ticker、腿、数量、到期日均不可被后续月份改写。
- 期货合约与期权系列、到期日不匹配时，按明确规则另选或跳过交易并记录原因，不得静默拼接。

### 2.2 Put Ratio Spread 参数

- 至少配置 `interval`、`ratio` 和 strike 选择规则。
- `ratio` 的方向必须明确记录为各腿的 `buy_qty`、`sell_qty`；不要只用含糊的 `ratio=2`。买入 1 张/卖出 2 张与买入 2 张/卖出 1 张是不同策略。
- 组合对象应记录每腿方向、数量、strike、ticker、expiry、成交价和现金流。
- 参数候选集应配置化，并在每个训练窗口可追溯。
- ratio 可按牛市/熊市等 regime 动态选择，但 regime 标签在决策时只能使用当时已知信息；各 regime 的参数只能由对应训练样本选择。样本不足时按配置执行 fallback（例如全样本最优或跳过），并在报告中披露。
- `BullBearRegime` 在通过 OOS 验证前只能视为实验模块，不得宣称具有可靠预测能力。

### 2.3 成交价与盈亏

- 买入腿按 Ask，卖出腿按 Bid；记录报价时间戳、字段、单位及质量状态。
- 报价缺失、非正、Bid 大于 Ask、时间过旧或市场无有效报价时，默认跳过交易并记录原因；禁止静默用 Mid/Last 补值。
- 每笔总 P&L 包含入场净权利金现金流和到期结算现金流，并使用核实后的乘数/点值及腿数量。
- 买入期权的权利金现金流为负，卖出期权为正；到期现金流按每腿方向计算。
- `trade_pnl` 是单笔组合的总盈亏；`portfolio_equity` 是按一致资金流/估值规则计算的组合权益。累计 P&L、收益率和净值不可混用。
- 初期可暂不计佣金、额外滑点、保证金与资本占用，但报告必须明确列出未计项目；不得把无资本约束的结果描述为可直接实盘执行的资金回报。

## 3. 优化目标与严格 Walk-forward

### 3.1 可配置目标函数

目标函数必须可配置，默认：
```yaml
optimization:
  objective: cumulative_pnl
```
`cumulative_pnl` 表示训练窗口内组合累计总盈亏。后续可扩展风险调整收益、回撤惩罚等，但必须定义数学公式、单位、缺失样本处理和越优方向。保证金/资本约束先不启用，预留 `constraints.enabled: false` 等接口，不得声称其已经生效。

不得将尚未到期组合的最终 P&L 当作训练时点已知结果。若用 mark-to-market 评分，只能使用当时可获得的历史报价，并清楚区分未实现盈亏与最终到期盈亏。分数相同或接近时使用确定性 tie-break 规则。

### 3.2 每个样本外决策的流程

1. 确定月度决策日和当时可用信息截止时间。
2. 构建严格早于决策时点的训练窗口。
3. 在训练窗口内对每组候选参数评分。
4. 选择最优参数，并保存完整排名、最优/次优分数及差距。
5. 使用决策时点有效报价建仓，记录为 OOS 交易。
6. 将新组合加入账本并持有至自身到期；不修改已有持仓。
7. 后续月份重复训练和建仓；只在实际结算或当时可估值时更新相应持仓盈亏。
8. 汇总 OOS 交易与组合权益。

Rolling Window 使用决策时点之前最近固定长度样本；Expanding Window 使用固定起点至决策时点之前的样本。日期边界必须有测试。

### 3.3 训练窗口诊断

报告每个窗口的训练起止日期、信息截止时间、总/有效/排除样本量、排除原因、候选组合数、每组参数分数和排名、最优/次优参数、分数差和相对差、regime 标签与样本量、fallback 状态，以及关联的 OOS `trade_id`。最优和次优表现接近时应提示参数不稳定。

训练评分必须明确区分：
- **历史可执行模拟：** 每个历史月份用当时可用报价建仓并按自身规则结算；
- **参数重放：** 把当前候选参数应用到历史月份做比较。
默认优先采用历史可执行模拟。若采用参数重放，必须说明其含义和限制，并证明没有引入未来信息。

## 4. 数据与市场规格核实

数据层应统一时间戳/时区、交易日、ticker、expiry、strike、put/call、Bid/Ask/Last、标的期货、结算价等字段。缓存需记录请求参数、字段版本、数据源和拉取时间。禁止通过不透明的前向/后向填充把未来报价带入过去。

正式解读真实回测前，逐个标的核实并记录：
- Bloomberg 期货/期权 ticker 规则；
- 期货合约规格、最小价格变动与 tick value；
- 期权报价单位、每点美元价值/合约乘数及标的映射；
- 到期日、最后交易日、最终结算日和结算价格来源；
- 行权/结算方式、自动行权/交割规则、假日与提前收市规则。
每项记录 `source`、`verified_at`、`applies_to` 和备注。若 Bloomberg 与交易所资料冲突，暂停正式回测并调查。

## 5. 推荐代码架构

```text
backtest/
├── config.yaml
├── run_backtest.py
├── demo_synthetic.py
├── src/
│   ├── data/
│   │   ├── fetcher.py          # Bloomberg 取数与标准化
│   │   ├── cache.py            # 可追溯缓存
│   │   ├── schema.py           # 统一数据结构
│   │   └── validation.py       # 数据质量校验
│   ├── market/
│   │   ├── contract_specs.py   # 乘数、tick、报价与结算规格
│   │   ├── futures_calendar.py # 期货合约日历/选择
│   │   └── options_calendar.py # 期权到期/结算日
│   ├── core/
│   │   ├── instrument.py       # OptionLeg、PutRatioSpread、现金流
│   │   ├── strategy.py         # 月度建仓、候选组合生成
│   │   ├── portfolio.py        # 逐笔账本、持仓生命周期、权益
│   │   ├── engine.py           # 主流程编排
│   │   └── windows.py          # Rolling/Expanding 窗口
│   ├── optimization/
│   │   ├── objectives.py       # 目标函数
│   │   ├── grid_search.py      # 参数评分/排序
│   │   └── walk_forward.py     # 训练、选参、OOS
│   ├── timing/
│   │   ├── base.py
│   │   └── bull_bear.py        # 未验证前视为实验模块
│   ├── execution/
│   │   ├── pricing.py          # Ask 买入、Bid 卖出
│   │   └── costs.py            # 未来扩展佣金/滑点
│   ├── metrics/performance.py
│   └── report/
│       ├── reporter.py
│       └── report_template.html
└── tests/
    ├── test_data.py
    ├── test_contract_specs.py
    ├── test_calendar.py
    ├── test_instrument.py
    ├── test_execution_pricing.py
    ├── test_portfolio.py
    ├── test_windows.py
    ├── test_objectives.py
    ├── test_grid_search.py
    ├── test_walk_forward.py
    ├── test_no_lookahead.py
    ├── test_metrics.py
    └── test_report.py
```

职责边界：
- `data/` 不产生信号、不决定参数。
- `market/` 提供规格、日历和合约映射。
- `instrument.py` 负责组合结构、腿级现金流和到期结算。
- `strategy.py` 根据已选参数和当日可用数据构造组合。
- `optimization/` 负责训练窗口、目标函数和候选参数排名，不直接下载数据。
- `execution/` 负责保守报价与报价有效性。
- `portfolio.py` 逐笔管理持仓；新月份不得隐式关闭或覆盖旧组合。
- `engine.py` 只编排模块并维持时间顺序。
- `metrics/` 只计算明确口径的 OOS 交易/权益指标。
- `report/` 展示结果，不重写交易或盈亏逻辑。

## 6. 组合账本与权益曲线

每笔组合至少保存：`trade_id`、决策/入场时间、入场月份、标的与期权 ticker、每腿 expiry/结算日、interval、ratio、腿方向/数量/strike、Bid/Ask/成交价/报价时间戳、每腿权利金现金流、到期现金流、乘数、总 P&L、状态（open/settled/skipped）、跳过原因、训练窗口 ID、目标函数、参数排名和 regime。

账本必须支持多笔重叠持仓：
- 已实现 P&L 仅在对应交易结算后计入。
- 若计算未实现 P&L，只能使用当日可获得的有效报价。
- 现金流、权益和收益率口径保持一致。
- 不得把到期结果回填到到期日前。
- 建立可手算的小样本，核验月度建仓、重叠持仓和到期结算。

## 7. 自动化测试与验收

**核心无前视测试：未来数据扰动测试**
1. 用基准数据运行，保存截至日期 `t` 的信号、参数选择和交易决策。
2. 人为修改 `t` 之后的期货价格、期权报价、结算价和 regime 输入。
3. 再次运行。
4. 验证截至 `t` 的所有信号、选参与交易决策完全一致。

其他必测项：
- 训练窗口不包含决策日及之后数据；
- 未到期组合不能读取未来最终结算价；
- 建仓报价时间戳不晚于决策时点；
- 同一组合所有 Put expiry 一致；
- 新月份建仓不改变旧组合的腿、数量、ticker 或 expiry；
- Ask/Bid 选择、坏报价跳过与原因留痕；
- 每腿现金流、乘数和总 P&L 的手算对照；
- 参数评分、最优/次优排名与 tie-break；
- 多笔重叠组合的账本/权益正确；
- regime 训练不依赖未来，样本不足 fallback 正确；
- 固定随机种子的合成端到端测试可复现。

在未来数据扰动测试通过、真实样本规格核对完成、抽样交易可从原始数据复算前，不得宣称“严格无前视偏差”或“实盘可行”。

## 8. 报告要求

报告至少包含：最终生效配置、数据源/时间范围/拉取时间、规格核实状态、策略与成交假设、交易成本限制、每个训练窗口样本量和参数排名、最优/次优差距、OOS 交易明细、跳过交易原因、累计 P&L、权益曲线口径、适用的绩效指标、按 regime/参数/月份拆分的结果、数据质量警告和模型局限。

若没有定义初始资本、保证金和资本占用，不能把累计 P&L 直接包装成可比较的资金收益率。未实现与已实现盈亏必须区分。

## 9. 配置建议

以下为目标结构示例；先检查现有 schema，再做兼容迁移，不要直接覆盖配置：

```yaml
backtest:
  start_date: "2021-01-01"
  end_date: "2025-12-31"
  product_code: "TY"
  entry_schedule: "first_trading_day_of_month"
  hold_to_expiry: true
  roll_existing_positions: false

execution:
  buy_option_price: "ask"
  sell_option_price: "bid"
  invalid_quote_policy: "skip_trade"

strategy:
  interval_grid: []
  # 候选应明确包含各腿 buy_qty / sell_qty
  ratio_grid: []
  require_same_expiry: true

optimization:
  method: "grid_search"
  objective: "cumulative_pnl"
  window_type: "rolling"
  train_months: 12
  minimum_samples: 6
  regime_aware_ratio: false
  tie_break: "deterministic"

constraints:
  enabled: false

report:
  include_training_diagnostics: true
  include_trade_ledger: true
  include_skipped_trades: true
```

## 10. 开发优先级

### P0：解读真实绩效前
1. 核实合约规格、Bloomberg 报价单位、乘数、到期与最终结算规则。
2. 明确 Put Ratio Spread 腿方向、数量比例与 strike 规则。
3. 实现 Ask 买入、Bid 卖出与报价质量校验。
4. 以腿级现金流重建总 P&L，并用手算用例测试。
5. 实现 Portfolio Ledger：每笔独立持有到期，禁止隐式移仓。
6. 实现严格 walk-forward 与未来数据扰动测试。

### P1：研究可靠性
1. 目标函数接口化，默认累计总 P&L。
2. 报告窗口样本量、最优/次优参数及分差。
3. 实现 regime-aware ratio 与样本不足 fallback。
4. 报告区分 OOS P&L、未实现 P&L、权益和收益率。
5. 显式处理坏数据、缺失结算价和未核实规格。

### P2：后续增强
1. 佣金、额外滑点与执行模型。
2. 保证金、资本占用、仓位和流动性约束。
3. 更多宏观、趋势、波动率 regime，并进行 OOS 验证。
4. 参数稳定性、敏感性、压力测试和月度收益热图。

## 11. 运行方式

以仓库当前入口为准：
```bash
pip install -r requirements.txt
python demo_synthetic.py
python -m pytest tests/ -v
python run_backtest.py --config config.yaml
```
真实 Bloomberg 回测需要终端、权限及相应 Python 环境。命令行覆盖后的最终配置必须保存在报告中。

## 12. 代码规范与未确认事项

- 使用类型注解、清晰 docstring 和小而可测的纯函数；保持模块依赖方向清晰。
- 涉及交易时间、参数选择或 P&L 的修改，必须运行完整测试和未来数据扰动测试。
- 修改数据、市场规格、策略、优化、执行、账本、指标、报告、配置或入口时同步更新本文档。
- 当前实现的实际腿方向、strike 生成、训练评分方法、未到期样本处理、权益定义、报价时间语义、乘数/结算规则、多月持仓隔离和 regime 泄漏情况，都必须通过源码、测试和真实数据核查；本文件不替代这些核查。

## 13. 当前工作区核查记录（2026-10-15）

- 当前工作区实际是回测项目根目录：`src/`、`tests/`、`config.yaml`、`run_backtest.py` 和 `demo_synthetic.py` 均直接位于根目录；本工作区未发现 `backtest/` 子目录或 `Code/` 仪表盘目录。运行命令时应在该目录执行。
- 已实现并通过合成测试核查：训练样本只纳入决策日前已到期且存在精确结算期货价的历史组合；新交易保留独立交易编号；未结算组合不读取未来结算值；报价按卖出腿 Bid、买入腿 Ask，缺失/非正/交叉报价会跳过；训练诊断与交易账本写入 HTML 报告。
- `equity_curve` 目前定义为初始资金加已结算组合累计 P&L，未实现持仓不做市值估算；不得解读为包含保证金、资本占用或流动性约束的可执行资金回报。
- 当前默认 `settlement_field: PX_LAST` 只是日频到期价格代理，不代表已核实的交易所最终结算价；真实回测前需核实字段来源并在配置中指定。
- 合成端到端报告输出为 `reports/demo_synthetic.html`。合成结果仅验证程序行为。
- Bloomberg 的期权系列与期货映射、报价单位、乘数、tick、交易所到期/最终结算规则及日报价的盘中可得时间仍未完成权威规格核实；真实绩效须待记录来源并复算样本交易后再解释。
- 2026-10-15 验证：`python -m pytest tests/ -v`，56 项通过；`python demo_synthetic.py` 成功生成 HTML 报告。
- 2026-10-16 Bloomberg 数据兼容修复：`bdh` 若返回 `ticker/date/field/value` 长表，`BloombergDataFetcher._flatten_bdh` 会按请求 ticker 和字段 pivot 为标准日频宽表；期货和期权共用转换，缺少目标 ticker/字段或存在重复 date/field 时明确报错，不会补造行情。
- 2026-10-16 Bloomberg 样例核查：真实运行越过期货下载后，原生成的历史期权 ticker（如 `TYZ17P`）收到 `BAD_SEC`。终端 `BDP/NAME` 查询确认 `TYZ7P 125 Comdty` 和 `TYZ6P 110 Comdty` 可识别，而 `TYZ17P 125 Comdty` 无效；代码现将期权年份码与期货年份码分开，并将期权 BDH 改为每批最多 80 个 ticker，以减少大量逐 ticker 请求。该终端样例不替代权威 Bloomberg/交易所规格核实。
- 2026-10-16 修复期权预取范围：旧实现只按全样本最低价与最高价各生成一段行权价，造成中间价位月份没有对应报价；现按每个首个交易日的近月期货价格和最近可用 expiry 生成该月所需 strike，避免构造不相关的全历史行权价笛卡尔积。
- 2026-10-16 真实回测：`python run_backtest.py --config config.yaml` 成功读取 25 个期货合约和 1,800 个期权 ticker，运行 60 个决策月，建仓/结算 6 笔，54 笔因没有有效的同到期 Bid/Ask 腿组合而跳过；报告为 `reports/put_ratio_backtest_20240101.html`。报告结果受限于有效报价覆盖率，并且规格尚未完成权威核实，不能据此宣称策略表现可靠或可实盘。
- 2026-10-16 合成端到端报告 `reports/demo_synthetic.html` 成功生成；合成结果只验证程序行为，不代表真实市场表现。
- 2026-10-16 最终验证：`python -m pytest tests/ -v`，63 项通过；`python demo_synthetic.py` 成功生成 HTML 报告。
- 2026-10-16 流动性与比例网格更新：默认 `ratio_range` 为 0.0–2.0、步长 0.1；`ratio_quantity_scale: 10` 固定买入腿为每基础单位 10 张，使 0.1 比例增量对应整数卖出张数（ratio=0 表示纯买 Put）。账本仍逐腿记录实际数量。
- 2026-10-16 报价筛选更新：训练评分和样本外建仓共用正值、非交叉双边报价校验及 `execution.max_bid_ask_spread_64: 12` 门槛（按当前 1/64 报价单位假设，规格仍待核实）；每次决策按到期先后尝试候选期权系列，只有无任何可执行系列时才跳过。取数预取所有可用候选到期系列的月度行权价，避免回测层尝试了未下载的 ticker。
- 2026-10-16 报告呈现更新：Walk-Forward Diagnostics 和 Trade Details 各自放入有水平/垂直滚动条的容器；方法说明披露比例数量映射及报价宽度门槛，并说明日频数据没有盘口数量，筛选不代表深度保证。
- 2026-10-16 回归验证：`python -m pytest tests/ -v`，69 项通过；合成端到端回测成功生成 `reports/demo_synthetic.html`，测试结果仅验证程序行为，不代表真实市场表现。
- 2026-10-16 Bloomberg 端到端复跑：期权预取扩大到同一近月期货的全部可用后续 expiry 后，下载 2,121 个期权 ticker；60 个决策月建立 60 笔组合，58 笔已结算、2 笔在区间末仍 open、0 笔因报价组合跳过。新报告为 `reports/put_ratio_backtest_20240101.html`。旧版 6 笔/54 跳过主要因只检查最近 expiry，且该单一系列缺少满足有效 Bid/Ask 的腿组合；新版在 12/64 最大显示价差过滤下按到期先后查找备用系列。报告仍受报价规格/最终结算字段未核实、盘口深度缺失、成本与保证金未计等限制，不可据此宣称可实盘。
- 2026-10-16 Ratio correction: candidates now span 0.1 to 2.0 in 0.1 steps; ratio=0 is invalid in config parsing, grid search, and spread construction. Ratio quantity scale remains 10.
- 2026-10-16 Report layout: Walk-Forward Training Diagnostics now has a compact summary row per window; full window metadata, exclusions, stability details, and candidate rankings are available in an expandable, scrollable detail panel.
