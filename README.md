# Put Ratio Spread Backtest System

美债期货期权 Put Ratio Spread 回测框架。

## 主要功能

- 从 Bloomberg 下载美债期货及期权历史数据（时间范围可配置）
- **每个调仓日动态选择近月期货合约及其对应期权**（第一版；后续扩展远月合约）
- 每月初自动建仓 Put Ratio Spread，持有至期权到期
- 网格搜索最优参数组合（interval + ratio）
- 支持 Rolling / Expanding 训练窗口
- 训练评分只使用决策日前已到期且结算价已知的历史组合
- 买入腿按 Ask、卖出腿按 Bid；无效报价跳过并保留原因
- 逐笔记录交易状态，未到期组合不会提前读取结算价
- 输出净值曲线与常用回测指标（annual return、max drawdown、sharpe、calmar 等）
- 生成 HTML 报告，便于审阅
- 预留择时因子框架（`TimingFactor`），支持后续按牛熊市场环境选参

## 目录结构

```
backtest/
├── config.yaml                 # 回测配置
├── run_backtest.py             # 入口脚本
├── requirements.txt            # 额外依赖
├── src/                        # 核心源码
│   ├── data/                   # 数据下载与缓存
│   ├── core/                   # 策略、回测引擎、训练窗口
│   ├── optimization/           # 网格搜索
│   ├── timing/                 # 择时因子框架
│   ├── metrics/                # 绩效指标
│   └── report/                 # HTML 报告
└── tests/                      # 单元测试
```

## 快速开始

1. 安装依赖（若未安装）：

```bash
pip install -r requirements.txt
```

2. 修改 `config.yaml` 中的参数（期货代码、回测区间、参数空间等）。

   `product_code` 可切换不同期限美债期货：`TU`（2Y）、`FV`（5Y）、`TY`（10Y）、`US`（30Y）。

   `start_date` / `end_date` 可任意指定回测区间。

3. 运行合成演示（无需 Bloomberg）：

```bash
python demo_synthetic.py
```

演示会生成 `reports/demo_synthetic.html`。合成数据只验证程序行为，不代表真实市场表现。

真实 Bloomberg 回测（需要已登录终端及相应 `xbbg` 权限）：

```bash
python run_backtest.py --config config.yaml
```

入口会在样本外起始日之前按 `data.warmup_months` 多拉取训练数据，报告保存命令行覆盖后的最终配置。

4. 查看生成的 HTML 报告：

```bash
open reports/put_ratio_backtest_YYYYMMDD.html
```

## 命令行参数

```bash
python run_backtest.py \
    --config config.yaml \
    --start-date 2020-01-01 \
    --end-date 2023-01-01 \
    --window-type rolling \
    --train-months 12
```

## 运行测试

```bash
python -m pytest tests/ -v
```

报告中的权益曲线按“初始资金 + 已结算组合累计 P&L”计算，未到期持仓不进行市值估算。当前没有保证金和资本占用约束，因此收益率指标仅作名义参考。Bloomberg 报价单位、乘数、期权与期货映射、到期及最终结算规则仍须逐项核实后，才可解读真实绩效。

## 支持的标的与回测区间

### 可切换不同期限美债期货

`config.yaml` 中的 `product_code` 可设置为任意 CME 美债期货代码：

| 期限 | Bloomberg 产品代码 |
|---|---|
| 2Y | `TU` |
| 5Y | `FV` |
| 10Y | `TY` |
| 30Y | `US` |

例如要回测 5Y：

```yaml
product_code: "FV"
product_prefix: "FV"
```

> 注意：不同期限合约的 `point_value` 可能不同，请在 CME 官网确认后修改配置。

### 可任意指定回测区间

`start_date` / `end_date` 完全可配置，也可通过命令行覆盖：

```bash
python run_backtest.py --start-date 2021-01-01 --end-date 2023-12-31
```

系统会自动下载该区间内所需的所有近月合约数据。

### Bloomberg ticker 命名规则

当前代码按已观察到的 Bloomberg ticker 格式构造代码；正式回测前仍需逐产品核实：

- 活跃合约（年份 ≥ 当前年份）：期货年份代码 1 位，例如 `TYZ6 Comdty` = 2026 年 12 月 10Y 期货
- 已到期期货合约：期货年份代码 2 位，例如 `TYZ24 Comdty` = 2024 年 12 月 10Y 期货
- Bloomberg 终端样例显示历史期权 ticker 仍使用 1 位年份码：`TYZ7P 125 Comdty` 对应 2017 年 12 月、行权价 125 的 Put；权威 ticker 规则仍需逐产品核实

程序分别按期货和期权 ticker 规则构造年份码。历史期权样例已通过 Bloomberg `BDP/NAME` 查询；正式回测前仍需记录权威来源并核验到期与结算规格。

## 核心模块说明

- `src/data/fetcher.py`: `BloombergDataFetcher` 提供期货/期权历史数据下载接口，自动将 narwhals DataFrame 转为 pandas。
- `src/data/futures_calendar.py`: 动态选择近月期货合约（H/M/U/Z 季度合约）。
- `src/core/instrument.py`: `PutRatioSpread` 实现 spread 构建与 payoff 计算。
- `src/core/engine.py`: `BacktestEngine` 按月执行回测，防止 lookahead。
- `src/optimization/grid_search.py`: `GridSearch` 在历史 episode 上搜索最优参数。
- `src/timing/base.py`: `TimingFactor` 抽象基类，支持后续扩展择时逻辑。
- `src/metrics/performance.py`: 计算 Sharpe、Max Drawdown、Calmar 等指标。
- `src/report/reporter.py`: 生成 HTML 报告。
