# AGENTS.md — 项目指令与进度对齐文档

## 1. 项目总览

本仓库包含两个相互关联的美债期货期权分析系统：

1. **`Code/`** —— 现有 Bloomberg 仪表盘系统
   - 每日从 Bloomberg 抓取期货 + 期权快照
   - 生成静态 HTML 仪表盘（`Dashboard/`）
   - 支持本地 Flask 服务一键刷新
   - 入口：`fetch_data.py`、`build_dashboard.py`、`run_daily.py`、`server.py`

2. **`backtest/`** —— 新建 Put Ratio Spread 回测系统
   - 目标：在美债熊市环境下，用 Put Ratio Spread 做尾部风险保护
   - 每月初建仓，持有至期权到期
   - 网格搜索最优参数（interval + ratio）
   - 支持 Rolling / Expanding Window 训练
   - 输出净值曲线、绩效指标、HTML 报告
   - 入口：`run_backtest.py`、`demo_synthetic.py`

---

## 2. 核心需求（来自用户指令）

### 2.1 数据接口
- [x] 能够从 Bloomberg 下载过去 N 年美债期货行情数据
- [x] 能够从 Bloomberg 下载过去 N 年对应期权数据
- [x] 起始 / 终止时间可配置（config + 命令行覆盖）
- [x] 本地缓存避免重复下载
- [x] xbbg 返回 narwhals DataFrame 时需自动转为 pandas

### 2.2 策略逻辑
- [x] 每月初买入 Put Ratio Spread
- [x] 持有至期权组合到期
- [x] 每个调仓日动态选择**近月期货合约**及其对应期权
- [ ] 后续扩展：支持远月合约选择（当前为第一版近月逻辑）

### 2.3 参数优化
- [x] 网格搜索 interval × ratio
  - interval 空间：`0.25, 0.5, ..., 2.0`（步长 0.25）
  - ratio 空间：`0.5, 0.6, ..., 2.5`（步长 0.1；通过 `ratio_quantity_scale=10` 保证卖出腿为整数张）
- [x] 优化目标：从重训练日回望过去的每月累计 payoff 最大
- [x] 支持 Rolling Window / Expanding Window

### 2.4 回测正确性
- [x] 严格避免 lookahead bias
- [x] 训练数据截止到调仓日前一交易日
- [x] 行权价网格、期权报价均只使用调仓日可获得数据
- [x] 到期结算使用到期日已知期货价格
- [x] 权益曲线改为**每日 mark-to-market**：未到期持仓按期货价内在价值近似估值

### 2.5 输出与报告
- [x] 每日 MTM 净值曲线 SVG
- [x] 常用 metrics：total return、annual return、volatility、sharpe、sortino、max drawdown、calmar、win rate、avg win/loss、profit factor、skewness、kurtosis、VaR 95%、CVaR 95%
  - Sharpe 分子**不扣除无风险利率**（零利率近似）
  - 收益分母可配置：`initial_capital` / `notional` / `margin`
- [x] HTML 报告（配置摘要、指标卡片、净值曲线、参数表、交易明细、methodology notes）

### 2.6 择时因子框架
- [x] `TimingFactor` 抽象基类
- [x] `BullBearRegime` 占位实现（基于均线，默认 neutral）
- [x] 引擎已预留 regime 过滤逻辑
- [ ] 后续开发：接入宏观、波动率、趋势等择时因子

### 2.7 工程化
- [x] 模块化、可复用的 class 设计
- [x] 类型注解与中文/英文 docstring
- [x] 完整单元测试（当前 87 个用例全部通过）
- [x] 命令行入口 `run_backtest.py`
- [x] 合成数据演示 `demo_synthetic.py`

### 2.8 Bloomberg Ticker 规范
- [x] 支持不同期限美债期货：TU（2Y）、FV（5Y）、TY（10Y）、US（30Y）、WN（Ultra 30Y）
- [x] 遵循 Bloomberg 年份代码规则：
  - 活跃合约（year >= 当前年份）：1 位年份，如 `TYZ6 Comdty`
  - 已到期合约（year < 当前年份）：2 位年份，如 `TYZ24 Comdty`
- [x] 期权 ticker 同步遵循上述规则：`TYZ24P 110 Comdty`

---

## 3. 目录结构

```
us_treasuries_options/
├── AGENTS.md                    # 本文件
├── Code/                        # 现有仪表盘系统
│   ├── fetch_data.py
│   ├── build_dashboard.py
│   ├── run_daily.py
│   ├── server.py
│   ├── config.yaml
│   └── template.html
├── Dashboard/                   # 现有仪表盘输出
├── data/                        # 现有数据快照
└── backtest/                    # 新建回测系统
    ├── README.md
    ├── config.yaml
    ├── run_backtest.py
    ├── demo_synthetic.py
    ├── requirements.txt
    ├── conftest.py
    ├── src/
    │   ├── data/
    │   │   ├── fetcher.py       # Bloomberg 下载 + narwhals→pandas
    │   │   ├── option_ticker.py # 期权 ticker 构造 / 解析
    │   │   ├── futures_calendar.py # 近月合约选择 / 换月逻辑
    │   │   └── cache.py         # 本地 pickle 缓存
    │   ├── core/
    │   │   ├── instrument.py       # OptionContract / PutRatioSpread
    │   │   ├── strategy.py         # MonthlyPutRatioSpreadStrategy
    │   │   ├── engine.py           # BacktestEngine
    │   │   ├── windows.py          # RollingWindow / ExpandingWindow
    │   │   ├── config_validator.py # 配置校验器
    │   │   └── backtest_builder.py # 数据下载 + 组件组装
    │   ├── optimization/
    │   │   └── grid_search.py   # 参数网格搜索
    │   ├── timing/
    │   │   ├── base.py          # TimingFactor 抽象基类
    │   │   └── bull_bear.py     # 牛熊判断占位实现
    │   ├── metrics/
    │   │   └── performance.py   # 绩效指标计算
    │   └── report/
    │       ├── reporter.py      # HTML 报告生成
    │       └── report_template.html
    └── tests/                   # 87 个单元测试
        ├── test_data.py
        ├── test_engine.py
        ├── test_futures_calendar.py
        ├── test_grid_search.py
        ├── test_instrument.py
        ├── test_metrics.py
        ├── test_report.py
        ├── test_run_backtest.py
        ├── test_timing.py
        ├── test_windows.py
        └── test_config_validator.py
```

---

## 4. 使用方式

### 4.1 安装依赖
```bash
cd backtest/
pip install -r requirements.txt
```

### 4.2 运行真实 Bloomberg 回测
```bash
cd backtest/
python run_backtest.py --config config.yaml
```

命令行覆盖示例：
```bash
python run_backtest.py \
    --start-date 2021-01-01 \
    --end-date 2023-12-31 \
    --window-type rolling \
    --train-months 12
```

### 4.3 切换标的期限
修改 `backtest/config.yaml`：
```yaml
product_code: "FV"      # 5Y
product_prefix: "FV"
point_value: 1000       # 请按 CME 官网确认
```

### 4.4 运行合成数据演示
```bash
cd backtest/
python demo_synthetic.py
```

### 4.5 运行测试
```bash
cd backtest/
python -m pytest tests/ -v
```

---

## 5. 当前状态

| 模块 | 状态 | 备注 |
|---|---|---|
| 数据下载接口 | ✅ | 支持期货/期权历史数据，narwhals→pandas，本地缓存；期权链支持 bloomberg_chain / inferred |
| 近月合约选择 | ✅ | H/M/U/Z 季度合约，自动换月 |
| 期权 ticker 构造 | ✅ | 遵循 Bloomberg 活跃/已到期年份规则；到期日按 CME 规则计算 |
| Put Ratio Spread | ✅ | payoff、realized_pnl、每日 mark-to-market |
| 网格搜索 | ✅ | interval 0.25–2.0 / ratio 0.5–2.5，累计 payoff 目标 |
| 训练窗口 | ✅ | Rolling / Expanding |
| 回测引擎 | ✅ | 月度再平衡，无 lookahead，每日 MTM |
| 绩效指标 | ✅ | 16 项指标；Sharpe 不扣无风险利率；分母可配置 initial_capital / notional / margin |
| HTML 报告 | ✅ | 净值曲线、指标卡片、参数表、交易明细、methodology notes |
| 配置校验与构建 | ✅ | ConfigValidator + BacktestBuilder |
| 择时因子框架 | ✅ 占位 | 基类已定义，默认 neutral |
| 单元测试 | ✅ | 87/87 通过 |
| 远月合约支持 | ⏳ | 后续版本开发 |
| 高级择时因子 | ⏳ | 后续接入宏观/波动率因子 |

---

## 6. 已知问题与注意事项

1. **Bloomberg 连接**：运行 `run_backtest.py` 需要 Bloomberg 终端已登录，并安装 `xbbg`。
2. **数据量**：下载 5 年全部 strike 的期权历史数据可能耗时较长，首次下载后会被缓存到 `backtest/cache/`。
3. **point_value**：不同期限美债期货的每点美元金额可能不同，切换 `product_code` 后请确认 `point_value`。
4. **换月规则**：当前为“进入交割月首日之后换月至下一季月”，后续可按需求调整为更精细的换月规则。
5. **期权到期日**：`cme_option_expiry()` 使用美国联邦假日日历近似 CME 节假日；真实回测前请核验交易所节假日安排，必要时通过 `expiry_overrides` 覆盖。
6. **结算字段**：默认使用 `SETTLE_PRICE`，缺失时回退到 `PX_LAST`；真实绩效解读前请确认该字段与交易所最终结算价一致。
7. **权益曲线**：已改为每日 mark-to-market，未到期持仓使用内在价值近似，未考虑时间价值与波动率。
8. **收益分母**：`performance.denominator` 支持 `initial_capital` / `notional` / `margin`，其中 margin 为简化模型（`notional * margin_ratio`），不代表实际保证金要求。
9. **合成数据**：`demo_synthetic.py` 使用固定价格生成的期权链，仅供流程验证，不代表真实市场。

---

## 7. 后续开发方向

1. **远月合约支持**
   - 在 `futures_calendar.py` 中扩展 `front_month_ticker()`，支持选择第 N 个季月合约。
   - 例如：`front_month_ticker(d, TY, nth=2)` 选择第二个近月合约。

2. **择时因子开发**
   - 继承 `src/timing/base.py` 的 `TimingFactor`。
   - 实现 `fit()` 与 `regime()` 方法。
   - 在 `BacktestEngine._select_params()` 中按 regime 过滤训练 episode。
   - 可选因子：
     - 趋势类：期货价格 vs 50/200 日均线
     - 宏观类：收益率曲线斜率、信用利差
     - 波动率类：MOVE 指数风格指标

3. **滑点与手续费**
   - 当前按 sell@Bid / buy@Ask 入场，未额外建模滑点与佣金，后续可加入。

4. **仓位与资金管理**
   - 当前为固定合约数；已加入简化名义仓位 / margin 占用分母，后续可加入波动率目标、Kelly 仓位等。

5. **报告增强**
   - 增加月度收益热图、滚动夏普、参数敏感性分析等。

6. **节假日与结算规则核验**
   - 用精确 CME 节假日数据替换 `USFederalHolidayCalendar` 近似。
   - 核验并锁定各 product 的官方最终结算字段。

---

## 8. 代码规范

- 类名：`PascalCase`
- 函数/方法：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 类型注解：使用 Python `typing`，关键参数与返回值明确
- Docstring：中英双语，说明功能、参数、返回值
- 行宽：建议不超过 100 字符
- 测试：使用 `pytest`，函数命名 `test_<模块>_<场景>_<期望>`
- 增量修改优先使用 `Edit`，全新文件使用 `Write`

---

## 9. 修改任何 AGENTS.md 记录模块时的同步要求

当新增、删除或修改以下模块时，请同步更新本文件：
- 数据层（`src/data/`）
- 策略/引擎（`src/core/`）
- 优化（`src/optimization/`）
- 择时（`src/timing/`）
- 指标（`src/metrics/`）
- 报告（`src/report/`）
- 配置（`config.yaml`）
- 入口脚本（`run_backtest.py`、`demo_synthetic.py`）

---

*最后更新：2026-10-16*
