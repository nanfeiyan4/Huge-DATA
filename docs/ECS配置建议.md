# 阿里云 ECS 配置建议（选题 3 实验用）

## 一句话结论

**买 `ecs.g7.2xlarge`（8 vCPU / 32 GiB）+ ESSD PL1 100 GB 系统盘 + Ubuntu 24.04 LTS x86_64，按量付费，跑完打快照再释放。**
预算紧就降到 8 vCPU / 16 GiB，实验照样能做完，只是 JOB 全量测量会慢 1.5–2 倍。

## 规格对照

| 档位 | 规格 | 内存账 | 适用场景 | 相对墙钟时间 |
|---|---|---|---|---|
| 最低可用 | 4C16G（`ecs.g7.xlarge`） | shared_buffers 4 GB | 主战场改用 TPC-H SF10，JOB 只抽样 30 条 | 1.8–2.5× |
| **推荐** | **8C32G（`ecs.g7.2xlarge`）** | shared_buffers 8 GB | JOB 113 条全量 + 3 档预算 + 5 组消融 | 1.0× |
| 舒适 | 16C64G（`ecs.g7.4xlarge`） | shared_buffers 16 GB | 想额外做"并行度"对照组、多档并行跑 | 0.6× |

### 为什么是这个配置

- **内存**：IMDB 解压后 CSV 约 3.5 GB，导入 PG 后库约 5–7 GB，加索引后 8–12 GB。`shared_buffers` 8 GB + OS 页缓存 + `work_mem` 才能让"无索引基线"稳定；16 GB 机器只能给 4 GB，噪声明显加大。
- **CPU**：无索引时 JOB 有全表扫描，单条查询可能几十秒到几分钟。113 条 × 5 次重复 × 多组配置，**单核性能直接决定你要等多少小时**。选 c7/g7 这类计算型/通用型（主频高），别用共享型。
- **磁盘**：ESSD PL1 100 GB 足够（数据 6 GB + 索引最多 4 GB + WAL）。**不要用高效云盘**：IOPS 抖动大，"无索引基线"会不可复现。
- **网络**：要下载 `imdb.tgz`（约 1.5 GB）。按使用流量 + 峰值 100 Mbps，几分钟下完。

## 下单清单

| 项 | 选择 |
|---|---|
| 地域 | 华东1（杭州）或华北2（北京），离你近 |
| 实例规格 | `ecs.g7.2xlarge`（8 vCPU / 32 GiB），按量付费 |
| 镜像 | **Ubuntu 24.04 LTS 64 位**（x86_64）——apt 源里直接有 `postgresql-16` 和 `postgresql-16-hypopg` |
| 系统盘 | ESSD PL1，100 GiB |
| 公网带宽 | 按使用流量计费，峰值 100 Mbps（或固定 5 Mbps，够用但下载慢） |
| 安全组 | 只放行 22/tcp（建议限制来源 IP）；**不要开 5432 到公网** |
| 登录方式 | 密钥对 |
| 时长 | 按量付费；整套实验预计 20–40 机时（导入、建索引、多轮测量） |

### 不要买

- ❌ **突发性能实例（t5 / t6）**：CPU 积分耗尽后主频被压，测出来的时间不可复现，会直接毁掉报告的可信度。
- ❌ **共享型 / 轻量应用服务器**：CPU 被邻居抢占，测量噪声不可控。
- ❌ **ARM 实例（倚天 yitian）**：更便宜，但 hypopg 等扩展的编译与兼容性有额外风险，课程项目不值得冒。
- ❌ GPU 实例：本题不需要 GPU。

## 开机后第一件事（脚本会做，见 `provision/`）

```bash
# 1) 系统与 PG
sudo apt update && sudo apt install -y postgresql-16 postgresql-16-hypopg postgresql-client-16 \
     python3-venv python3-pip git wget unzip
# 2) 测量稳定性：固定 CPU 频率策略、关透明大页、降低 swap 抖动
sudo apt install -y linux-tools-common cpufrequtils || true
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null || \
  echo "WARN: 无法设置 CPU governor（云上常被宿主机接管），请在报告效度威胁里说明"
echo never | sudo tee /sys/kernel/mm/transparent_hugepage/enabled
sudo sysctl -w vm.swappiness=1
# 3) 把这台机器当成专用实验机：不要在上面跑别的服务
```

> 云上 `scaling_governor` 常常不可写（由宿主机统一管理）。写不进去不是问题，**但要如实记录**，这属于报告"效度威胁（validity threats）"的标准内容。

## 成本与省钱

- 按量付费，8C32G 大约每小时 1–2 元量级（以控制台报价为准）；整套实验 20–40 机时 → 几十元。
- **导入数据 + 装好环境后立刻打自定义镜像/快照**，之后实验出问题可以秒级恢复，不用重装、不用重下 1.5 GB 数据。
- 测量期间机器必须独占；不要和别人共享，也不要同时开 Jupyter 之类的后台程序。

## 交付物对照

装在机器上的东西最终要能复现：`provision/install_pg_ubuntu.sh`（装 PG + 调参 + 建库）
→ `provision/load_job.sh`（导 IMDB + 建 PK 索引 + ANALYZE）
→ `python -m idxlab.cli measure --tag base`（基线）
→ `python -m idxlab.cli search ...`（推荐）
→ `python -m idxlab.cli measure --tag tuned --indices out/greedy.json`（验证）
报告里"实验设置"一节直接引用这些脚本与参数文件即可。
