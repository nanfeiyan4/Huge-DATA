# Huge-DATA · 存储预算约束下的索引推荐

《大规模数据处理系统》选题 3「智能索引推荐与设计」的完整实现与实验结果。

**一句话**：在存储预算约束下为查询负载挑选索引——用 PostgreSQL 规划器代价（HypoPG what-if）做秒级搜索，用真实建索引＋真实执行做验证，并用执行反馈校准选择。

---

## 核心结果（全部为真实执行数据）

| 规模 | 预算 | 索引数 / 占用 | 规划代价降幅 | 总时间加速 | 几何平均加速 | 结果一致性 |
|---|---|---|---|---|---|---|
| TPC-H SF1（1.55 GB） | 20% | 9 / 199.8 MB | 8.67% | **1.10×** | **1.34×** | 22/22 一致 |
| TPC-H SF10（约 12 GB） | 5% | 5 / 490 MB | 1.92% | 0.99× | 1.08× | 22/22 一致 |
| TPC-H SF10（约 12 GB） | 20% | 7 / 1975 MB | 7.07% | **1.07×** | **1.17×** | 22/22 一致 |

其他实验（SF1，预算 20%）：

| 实验 | 结果 |
|---|---|
| 执行反馈校准（实测收益排序） | 仅用 **142.4 MB** 达到 **1.15×**（优于代价驱动的 199.8 MB / 1.10×） |
| 消融：去掉增量重估 | 几何平均 1.34× → **1.07×**（最关键组件） |
| 消融：去掉前缀冗余消除 | 1.34× → 1.31× |
| 消融：目标函数改归一化 | 1.34× → 1.03×（奖励“小表上的小改善”） |
| 代价代理可信度 | 规划代价降幅 vs 实测加速 **Spearman ρ = 0.921**（40% 预算时 0.729） |

## 三个主要发现

1. **收益集中在过滤/聚合型查询**：Q14 5.46×、Q15 3.75×、Q19 3.32×、Q6 1.96×；而 Q1/Q18 这类大范围扫描＋聚合的查询不受益（SF1 时两者占基线总时间 41%，SF10 时连同 Q9 占 46%）——"总时间加速比"会系统性低估方法价值。
2. **增量重估是收益的主要来源**：贪心若不复算边际收益，会在服务同一批查询的相近索引之间打转，预算被浪费。
3. **预算存在门槛效应**：`lineitem` 日期索引的大小恒为数据量约 13%，与规模无关。预算低于该门槛时，按"收益/存储"排序的贪心会把预算填满低价值小表索引（背包问题的 value-density trap），实测几乎无收益；跨过门槛后收益立刻出现。

## 目录结构

```
idxlab/            推荐器实现
  workload.py      SQL 解析（关系/别名/谓词/连接列；支持子查询与非限定列名）+ DDL→schema
  advisor.py       候选生成、前缀冗余消除、索引大小估算、代价模型(HypoPG/真实索引/仿真)、
                   预算贪心 + 增量重估 + k-swap、两种目标函数
  db.py            psycopg3 封装：EXPLAIN 代价、计时、结果摘要
  measure.py       固定测量协议（预热/多次取中位数/超时/结果 md5）
  cli.py           命令行：parse / pool / search / measure / compare / mocktest
  selftest.py      无数据库自检（FakeDB 模拟 hypopg）
provision/         Ubuntu 24.04 装机与数据导入（PG16 + hypopg + 调参；JOB / TPC-H 导入）
tools/             实验与分析脚本
  write_tpch_queries.py   生成 22 条 TPC-H 查询（字面量版，可直接 psql 执行）
  planner_costs.py        计算推荐集合的逐查询规划代价（RQ3）
  exec_rank.py            执行反馈校准：实测每个候选的收益后按预算装箱（RQ5）
  report_plots.py / plot_summary.py / sf10_summary.py   出图与汇总
  make_report_docx.py     从结果 JSON 生成 DOCX 报告
  run_tpch_sf1*.sh / run_sf10*.sh                       实验流水线
workload/tpch/     schema.sql、fkindexes.sql（基线索引）、queries/1..22.sql
workload/job/      JOB 113 条查询 + schema.sql（数据需另行下载，见 provision/load_job.sh）
results/sf1/       全部 SF1 原始测量 JSON（基线/各预算/消融/执行反馈/规划代价）
results/sf10/      SF10 原始测量 JSON
results/figures/   报告图表（PNG）
report/            结果报告 DOCX、报告源文件与实施方案
configs/           连接与测量参数
docs/              ECS 配置建议
```

## 快速开始

```bash
# 0) 环境：Ubuntu 24.04 / 8 vCPU / 30 GiB 以上
sudo apt update && sudo apt install -y postgresql-16 postgresql-16-hypopg python3-venv git
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# 1) 数据库调参与建库（shared_buffers/work_mem/jit/并行/autovacuum 等测量相关参数）
bash provision/install_pg_ubuntu.sh

# 2) 生成并导入 TPC-H（SF1 约 1 GB CSV，SF10 约 11 GB）
pip install tpchgen-cli
tpchgen-cli csv -s 1 --output-dir /root/tpch_sf1
psql -f workload/tpch/schema.sql -f workload/tpch/fkindexes.sql
# \copy 各表 -> ANALYZE（见 report 附录 A）

# 3) 推荐
python -m idxlab.cli --config configs/tpch.json parse
python -m idxlab.cli --config configs/tpch.json pool    --budget-pct 20
python -m idxlab.cli --config configs/tpch.json search  --tag greedy20 --prescreen 25
python -m idxlab.cli --config configs/tpch.json measure --tag base
python -m idxlab.cli --config configs/tpch.json measure --tag tuned20 --indices out/greedy20.json
python -m idxlab.cli --config configs/tpch.json compare --a out/base.json --b out/tuned20.json

# 4) 执行反馈校准（题目推荐方向 ③）
python tools/exec_rank.py --config configs/tpch.json \
    --sets out/greedy20.json out/norm20.json out/raw40.json \
    --budget-mb 202 --out out/exec20.json

# 无数据库也能验证解析/候选/搜索/测量逻辑
python -m idxlab.selftest
```

## 实验设置

| 项 | 配置 |
|---|---|
| 机器 | 阿里云 ECS，8 vCPU / 30 GiB / ESSD，Ubuntu 24.04.5 |
| DBMS | PostgreSQL 16.15 + hypopg 1.4.0 |
| 关键参数 | `shared_buffers=8GB`、`work_mem=256MB`、`maintenance_work_mem=2GB`、`default_statistics_target=500`、`random_page_cost=4.0`、`jit=off`、`max_parallel_workers_per_gather=0`、`autovacuum=off` |
| 负载 | TPC-H 22 条查询（含相关子查询/派生表），SF1 与 SF10 |
| 基线索引 | 8 主键 + 8 外键索引（真实部署的既有索引），其外不再新增 |
| 测量协议 | 每查询 1 次预热 + 3 次执行取中位数；每配置前 ANALYZE + CHECKPOINT；单查询 300/900 s 超时并单独记录；每次比对结果 md5 |

## 局限

- 原计划使用 JOB/IMDB（113 条复杂 join），但实验机到境外的有效带宽仅 11–15 KB/s（对照：阿里云镜像 3.7 MB/s、PyPI 镜像 12.9 MB/s），2 GB 级数据无法在合理时间内获取；主体实验改用 TPC-H（题目允许多种负载）。
- 单机、受控条件（关闭 JIT/并行/autovacuum），结论应理解为"该受控条件下的结论"。
- 每查询 3 次取中位数，未给出置信区间；±5% 以内的差异不做强结论。

## 数据与许可

仓库**不包含**任何数据集本体（TPC-H 由 `tpchgen-cli` 生成；JOB 查询来自 [gregrahn/join-order-benchmark](https://github.com/gregrahn/join-order-benchmark)，其数据需按 `provision/load_job.sh` 自行下载）。`results/` 下仅为测量输出的 JSON 与图表。
