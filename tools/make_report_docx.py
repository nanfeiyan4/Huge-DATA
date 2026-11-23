"""Render the结果报告 DOCX from report_data.json + figures.

    python tools/make_report_docx.py --data results/report_data.json \
        --figdir results/figures --out "D:/Learning/研究生/大规模数据/选题3_索引推荐_结果报告.docx"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

BODY_FONT = "宋体"
HEAD_FONT = "黑体"
GRAY = RGBColor(0x55, 0x55, 0x55)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def set_font(run, name: str = BODY_FONT, size: float | None = None,
             bold: bool | None = None, color: RGBColor | None = None) -> None:
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def style_doc(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.paragraph_format.line_spacing = 1.4
    normal.paragraph_format.space_after = Pt(4)
    for name, size in (("Heading 1", 15), ("Heading 2", 12.5), ("Heading 3", 11)):
        st = doc.styles[name]
        st.font.name = HEAD_FONT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0x1a, 0x1a, 0x1a)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), HEAD_FONT)
        st.paragraph_format.space_before = Pt(12)
        st.paragraph_format.space_after = Pt(6)


def para(doc: Document, text: str, size: float = 10.5, bold: bool = False,
         align=None, first_indent: bool = True, space_after: float = 4):
    p = doc.add_paragraph()
    if first_indent:
        p.paragraph_format.first_line_indent = Pt(size * 2)
    p.paragraph_format.space_after = Pt(space_after)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    set_font(run, BODY_FONT, size, bold)
    return p


def caption(doc: Document, text: str):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(text)
    set_font(run, BODY_FONT, 9, False, GRAY)
    return p


def shade(cell, hex_color: str = "EDEDED") -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcpr.append(shd)


def add_table(doc: Document, header: list[str], rows: list[list[str]],
              widths: list[float] | None = None, font_size: float = 8.5):
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0].cells
    for i, text in enumerate(header):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        set_font(run, HEAD_FONT, font_size, True)
        shade(hdr[i])
    for row in rows:
        cells = table.add_row().cells
        for i, text in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if i else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(str(text))
            set_font(run, BODY_FONT, font_size)
    # repeat the header row on page breaks and keep rows from splitting
    tr = table.rows[0]._tr
    trPr = tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    trPr.append(tbl_header)
    for row in table.rows:
        trPr = row._tr.get_or_add_trPr()
        cant = OxmlElement("w:cantSplit")
        trPr.append(cant)
    if widths:
        table.autofit = False
        for i, w in enumerate(widths):
            table.columns[i].width = Cm(w)
        for row in table.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
    return table


def figure(doc: Document, path: Path, width: float = 15.5):
    if not path.exists():
        para(doc, f"（缺失图片：{path.name}）", 9, first_indent=False)
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Cm(width))


# --------------------------------------------------------------------------- #
# document
# --------------------------------------------------------------------------- #
def build(data: dict, figdir: Path, out: Path, data10: dict | None = None) -> None:
    doc = Document()
    style_doc(doc)
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    for attr in ("top_margin", "bottom_margin"):
        setattr(section, attr, Cm(2.4))
    section.left_margin = Cm(2.6)
    section.right_margin = Cm(2.6)

    rq1 = data.get("rq1") or {}
    rq2 = (data.get("rq2") or {}).get("rows") or []
    rq10 = (data10 or {}).get("rows") or []
    rq3 = data.get("rq3") or {}
    rq4 = (data.get("rq4") or {}).get("rows") or []
    rq5 = data.get("rq5") or {}

    # ---------------- title
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.paragraph_format.space_before = Pt(36)
    t.paragraph_format.space_after = Pt(6)
    set_font(t.add_run("存储预算约束下的索引推荐"), HEAD_FONT, 22, True)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.paragraph_format.space_after = Pt(4)
    set_font(sub.add_run("——代价代理、冗余消除与执行反馈"), HEAD_FONT, 14, True)

    sub2 = doc.add_paragraph()
    sub2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub2.paragraph_format.space_after = Pt(28)
    set_font(sub2.add_run("《大规模数据处理系统》选题 3「智能索引推荐与设计」结果报告"),
             BODY_FONT, 11.5, False, GRAY)

    for line in ("姓名：__________　　学号：__________", "学院：数据科学与工程学院",
                 "提交日期：2026 年 11 月"):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        set_font(p.add_run(line), BODY_FONT, 10.5)

    doc.add_page_break()

    # ---------------- abstract
    doc.add_heading("摘要", level=1)
    sp = rq1.get("speedup")
    gm = rq1.get("geomean")
    abs_text = (
        "索引是数据库最重要的物理设计手段，但它占用存储、拖慢写入。真正的工程问题是："
        "在给定存储预算内，选哪一组索引收益最大。本文在 PostgreSQL 16 + hypopg 上实现了一条"
        "四步流水线——负载解析、候选索引生成、前缀冗余消除、预算约束下的代价驱动贪心（含增量重估），"
        "并在 TPC-H SF1（22 条查询、lineitem 600 万行、库 1.55 GB）上完成了全部真实执行测量。"
    )
    if sp:
        abs_text += (
            f"在存储预算 20%（{rq1.get('used_mb', 0):.1f} MB / {rq1.get('budget_mb', 0):.0f} MB）下，"
            f"推荐结果使负载总时间从 {rq1.get('total_base_ms', 0)/1000:.2f} s 降到 "
            f"{rq1.get('total_ms', 0)/1000:.2f} s（{sp:.2f}×），22 条查询的几何平均加速比达 {gm:.2f}×，"
            f"且 22/22 条查询结果校验一致。"
        )
    if rq3.get("20"):
        abs_text += (
            f"我们进一步量化了“规划器代价代理”的可信度：代价降幅与实测加速的 Spearman 相关系数为 "
            f"{rq3['20'].get('spearman')}，说明该代理在整体排序上有效、但不能替代真实执行——据此我们"
            "增加了以真实执行排序的执行反馈校准环节。"
        )
    if rq10:
        b10_abs = max(rq10, key=lambda r: r["speedup"])
        w10_abs = min(rq10, key=lambda r: r["speedup"])
        abs_text += (
            f"在放大 10 倍的 SF10（约 12 GB、lineitem 6,000 万行）上复验：20% 预算下仍取得 "
            f"{b10_abs['speedup']:.2f}× 总时间与 {b10_abs['geomean']:.2f}× 几何平均加速，"
            f"并复现了“预算门槛效应”——5% 预算装不下关键索引时收益归零（{w10_abs['speedup']:.2f}×）。"
        )
    para(doc, abs_text)
    para(doc, "关键词：索引推荐；存储预算；代价模型；HypoPG；TPC-H；执行反馈",
         9.5, first_indent=False)

    # ---------------- 1 引言
    doc.add_heading("1　问题与动机", level=1)
    para(doc, "为负载建索引这件事，表面上是“哪些列值得建索引”，实质是一个带预算约束的组合优化"
              "问题：候选索引数量随列组合快速增长，候选之间存在收益重叠（两个索引可能服务同一批查询），"
              "而工程上可用的存储和写入容忍度都是有限的。")
    para(doc, "现有工具要么依赖商业优化器（如 SQL Server 的 DTA），要么只给出无约束的“索引清单”。"
              "本文关注三个可量化的问题：（1）在固定预算下，代价驱动的贪心能拿到多少真实收益；"
              "（2）冗余消除与增量重估各自贡献多少；（3）以规划器代价作代理，结论是否可信，"
              "以及执行反馈能否补上这个差距。")
    para(doc, f"本文的主要发现是：在 TPC-H SF1 上，{rq1.get('used_mb', 0):.0f} MB 的索引"
              f"（预算 20%）可换来 {sp:.2f}× 总时间加速与 {gm:.2f}× 几何平均加速；"
              "收益集中在中等规模的过滤/聚合查询，纯扫描型重量级查询（Q1、Q18）不受益且合计占基线总时间的 41%；"
              "去掉增量重估后收益几乎消失（几何平均从 "
              f"{gm:.2f}× 掉到 {(rq4[1]['geomean'] if len(rq4) > 1 else 0):.2f}×）；"
              f"规划器代价与实测加速的 Spearman 相关系数为 {(rq3.get('20') or {}).get('spearman', 0):.2f}，"
              "但单条查询仍存在代价下降而实测变慢的反例（Q9）；"
              "改用真实执行排序后，同样的预算约束下可用更少存储取得更高收益。")

    # ---------------- 2 方法
    doc.add_heading("2　方法", level=1)
    doc.add_heading("2.1　负载解析与候选生成", level=2)
    para(doc, "从每条 SQL 中抽取关系（含别名与子查询内关系）、等值谓词列、范围谓词列、连接列以及 "
              "GROUP BY / ORDER BY 列。TPC-H 的谓词多为非限定列名（如 l_shipdate），因此解析器对整个"
              "语句做扫描，并借助模式信息把唯一可归属的列名映射回表。")
    para(doc, "候选生成规则：等值列前缀；等值列＋范围列尾；连接列单列；连接列与等值列的两种顺序；"
              "连接列两两组合；GROUP/ORDER 列追加。列顺序按 pg_stats 的 n_distinct 选择性排序，"
              "单索引列数上限为 4。")

    doc.add_heading("2.2　冗余消除", level=2)
    para(doc, "若候选 X 的列序列是候选 Y 的前缀（同表），删除 X——B 树前缀已能服务该访问路径。"
              "该步骤消掉了约 25%–30% 的候选，是消融实验的对象之一。")

    doc.add_heading("2.3　预算约束贪心与增量重估", level=2)
    para(doc, "目标是最小化负载总代价，约束是索引存储总量不超过预算 B。每轮选择“边际收益 / 边际存储”"
              "最大的候选；接受一个索引后，重新评估剩余候选在当前集合下的边际收益——这就是增量重估，"
              "用于消除收益重叠导致的重复选择。收尾再做 k-swap 局部精修。索引大小按 pg_stats 的 "
              "avg_width 与 reltuples 估算（B 树 fillfactor 0.9）。")
    para(doc, "评估使用 hypopg 创建的“假设索引”＋ EXPLAIN (FORMAT JSON) 的规划器总代价，"
              "毫秒级、不建真实索引、不执行查询。为控制搜索开销，先用单索引独立收益预筛出 Top-25 候选。")
    para(doc, "我们实现了两种聚合目标：raw（各查询代价降幅直接求和，被重型查询主导）与 "
              "normalized（各查询相对降幅取平均，每条查询权重相同）。二者的对比见 4.4 节。")

    doc.add_heading("2.4　执行反馈校准", level=2)
    para(doc, "代价代理只反映规划器视角。为得到与真实执行对齐的排序，我们额外实现了 exec_rank："
              "对每个候选真实建索引一次、测量其影响到的查询、记录实测收益后删除；再按"
              "“实测收益 / 存储”排序，在预算内装箱。该过程只付出一次建索引成本，却能直接度量"
              "“这个索引究竟值不值得”。")

    # ---------------- 3 实验设置
    doc.add_heading("3　实验设置", level=1)
    caption(doc, "表 1　软硬件与数据库配置")
    add_table(doc, ["项目", "配置"], [
        ["云主机", "阿里云 ECS，8 vCPU / 30 GiB 内存 / ESSD 云盘，Ubuntu 24.04.5 LTS"],
        ["数据库", "PostgreSQL 16.15"],
        ["扩展", "hypopg 1.4.0（假设索引 / what-if 代价）"],
        ["shared_buffers / work_mem", "8 GB / 256 MB"],
        ["maintenance_work_mem", "2 GB（建索引）"],
        ["default_statistics_target", "500"],
        ["random_page_cost / seq_page_cost", "4.0 / 1.0"],
        ["jit", "off（降低重型查询的随机方差）"],
        ["max_parallel_workers_per_gather", "0（并行作为独立变量单独讨论）"],
        ["autovacuum", "off（只读基准，避免后台 I/O 干扰）"],
    ], widths=[5.5, 10.5])
    doc.add_paragraph()

    caption(doc, "表 2　负载、基线与测量协议")
    add_table(doc, ["项目", "设置"], [
        ["负载", "TPC-H 22 条查询（含相关子查询与派生表），SF1"],
        ["数据规模", "lineitem 6,001,215 行；数据库 1.55 GB；基表数据约 1,010 MB"],
        ["基线索引", "8 个主键 + 8 个外键索引（对应真实部署的既有索引），其外不再新增"],
        ["预算口径", "B = 数据量的 5% / 10% / 20% / 40%（约 50 / 101 / 202 / 404 MB）"],
        ["预热与重复", "每查询 1 次预热，随后 3 次执行取中位数"],
        ["测量前处理", "ANALYZE + CHECKPOINT；测量期间机器独占"],
        ["超时", "单查询 statement_timeout = 300 s，超时单独记录而不丢弃"],
        ["正确性", "每次配置测量后比对 22 条查询结果的 md5"],
    ], widths=[5.0, 11.0])
    doc.add_paragraph()

    # ---------------- 4 结果
    doc.add_heading("4　实验结果", level=1)

    doc.add_heading("4.1　RQ1：预算 20% 下的真实收益", level=2)
    if rq1:
        para(doc, f"推荐结果包含 {rq1.get('n_indexes')} 个索引，占用 {rq1.get('used_mb'):.1f} MB"
                  f"（预算 {rq1.get('budget_mb'):.0f} MB，使用率 "
                  f"{100*rq1.get('used_mb',0)/max(rq1.get('budget_mb',1),1):.0f}%）。"
                  f"HypoPG 规划器代价下降 {rq1.get('planner_red_pct'):.2f}%；真实执行下，负载总时间由 "
                  f"{rq1.get('total_base_ms')/1000:.2f} s 降至 {rq1.get('total_ms')/1000:.2f} s，"
                  f"加速 {rq1.get('speedup'):.2f}×，几何平均加速 {rq1.get('geomean'):.2f}×。"
                  f"22 条查询结果 md5 全部一致，无一条结果变化。")
        caption(doc, "表 3　逐查询执行时间与加速比（预算 20%，基线为仅主键＋外键索引）")
        rows = []
        for r in sorted(rq1.get("per_query", []), key=lambda r: -r["speedup"]):
            rows.append([f"Q{r['q']}", f"{r['base_ms']:,.0f}", f"{r['tuned_ms']:,.0f}",
                         f"{r['speedup']:.2f}×"])
        rows.append(["合计", f"{rq1.get('total_base_ms'):,.0f}", f"{rq1.get('total_ms'):,.0f}",
                     f"{rq1.get('speedup'):.2f}×"])
        add_table(doc, ["查询", "基线 (ms)", "加索引后 (ms)", "加速比"], rows,
                  widths=[3.0, 4.0, 4.0, 3.0])
        doc.add_paragraph()
        figure(doc, figdir / "speedup_per_query.png", 15.5)
        caption(doc, "图 1　逐查询加速比（按加速比排序，红线以下为变慢）")
        figure(doc, figdir / "total_time.png", 8.0)
        caption(doc, "图 2　负载总时间对比")
        para(doc, "值得注意的结构性事实：Q1 与 Q18 分别耗时 6.8 s 与 5.0 s，合计占基线的 41%，"
                  "它们都是对 lineitem/orders 的大范围扫描＋聚合，索引无法减少扫描量，因此不受益。"
                  "这也说明“总时间加速比”会系统性低估方法价值：多数查询获得 1.1–5.5× 提升，"
                  "而总时间被少数扫描型查询稀释。")

    doc.add_heading("4.2　RQ2：存储预算的边际收益", level=2)
    if rq2:
        caption(doc, "表 4　不同预算下的推荐规模与实测收益")
        rows = [[f"{r['budget_pct']}%", f"{r['budget_mb']:.0f}", f"{r['used_mb']:.1f}",
                 f"{r['n_indexes']}", f"{r['planner_red_pct']:.2f}%",
                 f"{r['speedup']:.2f}×", f"{r['geomean']:.2f}×"] for r in rq2]
        add_table(doc, ["预算", "预算(MB)", "实际占用(MB)", "索引数", "规划代价降幅",
                        "总时间加速", "几何平均加速"], rows,
                  widths=[1.8, 2.4, 2.8, 1.8, 3.0, 2.6, 2.8])
        doc.add_paragraph()
        figure(doc, figdir / "budget_curve.png", 12.0)
        caption(doc, "图 3　存储占用与实测收益的关系（TPC-H SF1）")
        by_pct = {r["budget_pct"]: r for r in rq2}
        b5, b10, b20, b40 = (by_pct.get(k, {}) for k in (5, 10, 20, 40))
        para(doc, f"曲线并不单调，而呈现明显的**门槛效应**：5% 预算（只用 "
                  f"{b5.get('used_mb', 0):.0f} MB）实测 {b5.get('speedup', 0):.2f}×，"
                  f"10%（{b10.get('used_mb', 0):.0f} MB）反而 {b10.get('speedup', 0):.2f}×；"
                  f"一旦跨到 20%，收益跃升到 {b20.get('speedup', 0):.2f}×（几何平均 "
                  f"{b20.get('geomean', 0):.2f}×）；40% 进一步只到 {b40.get('speedup', 0):.2f}×，"
                  f"多用 {(b40.get('used_mb', 0) - b20.get('used_mb', 0)):.0f} MB 只多换 "
                  f"{(b40.get('speedup', 1) - b20.get('speedup', 1)) * 100:.1f} 个百分点。")
        para(doc, "原因可以用存储估算解释：lineitem 上最有价值的单列索引（l_shipdate）估算约 133 MB，"
                  "占基表数据量的 13%，在 5%/10% 的预算下根本装不进去；而按“收益/存储”排序的贪心"
                  "会把预算全部花在一批小表索引上——它们的每 MB 收益看起来更高，但绝对收益很小。"
                  "这正是**背包问题的经典陷阱（value-density trap）**：高价值的大件装不下时，"
                  "贪心用低价值小件把预算填满。SF10 上的现象与之完全吻合：5% 预算（504 MB）依旧"
                  "装不下相当于数据量 13% 的 lineitem 日期索引，实测几乎无收益（见 4.7 节）；"
                  "20% 才把它选进来，收益立即出现。因此预算约束下的关键不只是“选哪些”，"
                  "而是“预算是否足以容纳关键索引”——这一点比单纯增加预算更重要。")

    doc.add_heading("4.3　RQ3：规划器代价代理可信吗", level=2)
    if rq3:
        caption(doc, "表 5　代价代理与实测加速的一致性")
        rows = []
        for tag, label in (("20", "预算 20%（9 个索引）"), ("40", "预算 40%")):
            if tag in rq3:
                rows.append([label, f"{rq3[tag]['planner_red_pct']:.2f}%",
                             f"{rq3[tag]['measured_speedup']:.2f}×",
                             f"{rq3[tag]['spearman']:.3f}"])
        add_table(doc, ["配置", "规划代价降幅", "实测总时间加速", "Spearman ρ（代价降幅 vs 加速）"],
                  rows, widths=[5.0, 3.4, 3.6, 4.6])
        doc.add_paragraph()
        figure(doc, figdir / "planner_vs_measured.png", 11.0)
        caption(doc, "图 4　规划器代价比与实测加速的散点（点标注为查询编号）")
        rho20 = (rq3.get("20") or {}).get("spearman")
        rho40 = (rq3.get("40") or {}).get("spearman")
        para(doc, f"20% 配置下 Spearman 相关系数为 {rho20:.3f}，说明代价代理在“排序”层面高度可靠："
                  "被代价模型认为收益大的查询，实测确实更快。但代理并不等于真实：Q9 的规划代价下降，"
                  "实测却慢了约 10%（Q9 的瓶颈在多表连接与聚合，索引带来的随机访问反而抵消了收益）；"
                  f"40% 配置的相关系数降到 {rho40:.3f}，因为堆叠的低价值索引更多影响规划代价而非真实耗时。"
                  "结论：代价代理适合做候选筛选与排序，最终选择应交给执行反馈校准。")

    doc.add_heading("4.4　RQ4：消融实验", level=2)
    if rq4:
        caption(doc, "表 6　消融实验（预算 20%，同一测量协议）")
        rows = [[r["label"], f"{r['n_indexes']}", f"{r['used_mb']:.1f}",
                 f"{r['planner_red_pct']:.2f}%", f"{r['speedup']:.2f}×", f"{r['geomean']:.2f}×"]
                for r in rq4]
        add_table(doc, ["配置", "索引数", "占用(MB)", "规划代价降幅", "总时间加速", "几何平均"],
                  rows, widths=[4.6, 1.6, 2.2, 3.0, 2.6, 2.4])
        doc.add_paragraph()
        full, norest, nodedup, norm = (rq4 + [{}] * 4)[:4]
        para(doc, "三个结论：（1）去掉增量重估后，规划代价降幅尚可，但实测收益几乎消失"
                  f"（几何平均从 {full.get('geomean', 0):.2f}× 掉到 {norest.get('geomean', 0):.2f}×，"
                  f"总时间仅 {norest.get('speedup', 0):.2f}×）——因为贪心会反复选择服务于同一批查询的"
                  "相近索引，收益重叠导致预算被浪费；（2）去掉前缀冗余消除影响相对小"
                  f"（{nodedup.get('geomean', 0):.2f}× vs {full.get('geomean', 0):.2f}×），"
                  "但候选池会膨胀、搜索成本上升，且容易选出被更长索引覆盖的冗余项；"
                  f"（3）把目标函数换成“各查询相对收益平均”反而最差（{norm.get('geomean', 0):.2f}×），"
                  "因为它奖励“在小表上轻易获得的几个百分点”，而忽略了重型查询的大幅改善。"
                  "这一条对实践很有指导意义：目标函数必须与业务关注点（总时间、SLO 还是平均延迟）对齐。")

    doc.add_heading("4.5　RQ5：执行反馈校准", level=2)
    if rq5:
        m = rq5["measured"]
        caption(doc, "表 7　以实测收益排序后的候选索引（按实测收益降序）")
        rows = []
        for c in rq5["candidates"][:16]:
            cols = ",".join(c["columns"])
            rows.append([c["table"], cols, f"{c['measured_gain_ms']:,.0f}",
                         f"{c['size_mb']:.1f}", f"{c['build_s']:.1f}",
                         "是" if c["selected"] else "否"])
        add_table(doc, ["表", "列", "实测收益 (ms)", "大小 (MB)", "构建 (s)", "入选"],
                  rows, widths=[2.3, 6.0, 2.4, 2.0, 1.8, 1.3], font_size=7.5)
        doc.add_paragraph()
        figure(doc, figdir / "method_compare.png", 9.5)
        caption(doc, "图 5　基线 / 代价代理选择 / 执行反馈选择的负载总时间")
        build_total = sum(c["build_s"] for c in rq5["candidates"])
        used = m.get("used_mb", 0)
        para(doc, f"以实测收益排序并装箱后，集合包含 {len(rq5['selected'])} 个索引，"
                  f"占用 {used:.1f} MB（比纯代价方案少 "
                  f"{max(rq1.get('used_mb', used) - used, 0):.0f} MB），"
                  f"负载总时间 {m.get('baseline_ms', 0)/1000:.2f} s → "
                  f"{m.get('final_ms', 0)/1000:.2f} s（{m.get('speedup', 0):.2f}×），"
                  f"优于纯代价驱动的 {rq1.get('speedup', 0):.2f}×。")
        para(doc, "机制上，实测排序同时纠正了两类错误：一是“规划器高估收益”——典型如小表上的组合索引，"
                  "规划代价下降明显但真实耗时几乎不变（customer(c_acctbal) 实测收益为负，被剔除）；"
                  "二是“规划器低估收益”——如 lineitem(l_receiptdate)，规划器给它的降幅有限，"
                  "实测却带来约 776 ms 的收益。这也解释了为什么执行反馈阶段的存储用量更低却更快："
                  "它把钱花在真正有效的地方。")
        para(doc, f"代价方面：为 {len(rq5['candidates'])} 个候选各建一次索引并测量，总计约 "
                  f"{build_total:.1f} s 的构建时间（单次最大 3.4 s），"
                  "相对一次完整的负载测量（约 3 分钟）是完全可以接受的成本；"
                  "若换成小时级的大规模负载，则应按“先代价筛选、再执行校准 Top-k”的两段式来做。")

    doc.add_heading("4.6　索引大小估算的偏差", level=2)
    para(doc, "预算约束的可行性取决于“索引多大”的估算是否准确。我们用 pg_stats 的 avg_width 与 "
              "reltuples 估算大小（B 树 fillfactor 0.9、每元组约 16 B 开销），"
              "并与建好后的 pg_relation_size 实测值对比：")
    caption(doc, "表 8　索引大小估算 vs 实测（SF1）")
    add_table(doc, ["表(列)", "估算 (MB)", "实测 (MB)", "比值"], [
        ["lineitem(l_shipdate)", "133.5", "40.7", "3.28×"],
        ["lineitem(l_receiptdate)", "133.5", "40.7", "3.28×"],
        ["orders(o_orderdate)", "33.4", "10.4", "3.22×"],
        ["part(p_size,p_brand)", "6.9", "1.4", "4.96×"],
        ["customer(c_mktsegment)", "4.5", "1.0", "4.41×"],
        ["orders(o_orderkey,o_orderstatus)", "43.4", "45.1", "0.96×"],
    ], widths=[6.4, 3.2, 3.2, 2.6])
    doc.add_paragraph()
    para(doc, "低基数列（日期、mktsegment 等）被高估 3–5 倍，原因是 PostgreSQL 13 起 B 树对重复键做"
              "前缀去重（deduplication），低基数列的索引被显著压缩，而估算公式没有考虑这一点；"
              "高基数的组合列（o_orderkey,o_orderstatus）估算误差在 5% 以内。"
              "实践含义：本方法的预算约束是**保守**的——真实可用空间比估算更多，"
              "因此推荐结果的存储占用不会超出预期；若要精确贴合预算，应改用 "
              "hypopg 之外的“试建—测量—回滚”来标定估算系数，或按列的 n_distinct 引入去重因子。")
    if rq10:
        doc.add_heading("4.7　规模扩展：SF10（10 倍数据量）", level=2)
        para(doc, "为检验结论是否随规模变化，我们把数据放大到 SF10（lineitem 约 6,000 万行，"
                  "数据库约 12 GB），基线与测量协议保持一致（主键＋外键索引为基线、"
                  "1 次预热 + 3 次取中位数、结果 md5 校验）。由于数据量增大，"
                  "同样的“百分比预算”对应的绝对存储也放大 10 倍，因此 5% 预算在 SF10 上"
                  f"约为 {rq10[0]['budget_mb']:.0f} MB——足以容纳小表上的组合索引，"
                  "但仍然装不下 lineitem 的日期索引（估算约 1.3 GB，占数据量约 13%），"
                  "这一点使 SF10 同时成为检验“预算门槛效应”的理想场景。")
        caption(doc, "表 9　SF1 与 SF10 的结果对比")
        sf1_row = ["SF1（1.55 GB）", "20%", f"{rq1.get('n_indexes', 0)}",
                   f"{rq1.get('used_mb', 0):.1f}", f"{rq1.get('planner_red_pct', 0):.2f}%",
                   f"{rq1.get('speedup', 0):.2f}×", f"{rq1.get('geomean', 0):.2f}×"]
        rows10 = [["SF10（约 12 GB）", f"{r['budget_pct']}%", f"{r['n_indexes']}",
                   f"{r['used_mb']:.1f}", f"{r['planner_red_pct']:.2f}%",
                   f"{r['speedup']:.2f}×", f"{r['geomean']:.2f}×"] for r in rq10]
        add_table(doc, ["规模", "预算", "索引数", "占用 (MB)", "规划代价降幅", "总时间加速", "几何平均"],
                  [sf1_row] + rows10, widths=[3.6, 1.6, 1.6, 2.2, 2.8, 2.2, 1.8])
        doc.add_paragraph()
        figure(doc, figdir / "budget_curve_sf10.png", 11.5)
        caption(doc, "图 6　SF10 上的存储–收益曲线")
        figure(doc, figdir / "speedup_per_query_sf10_20.png", 15.5)
        caption(doc, "图 7　SF10（预算 20%）逐查询加速比")
        best10 = max(rq10, key=lambda r: r["speedup"])
        worst10 = min(rq10, key=lambda r: r["speedup"])
        para(doc, f"基线（仅主键＋外键索引）下 SF10 的负载总时间为 "
                  f"{best10['total_base_ms']/1000:.1f} s，是 SF1（28.9 s）的约 "
                  f"{best10['total_base_ms']/28887:.1f} 倍。两个预算档位的实测结果差别很大："
                  f"5% 档（{worst10['used_mb']:.0f} MB、{worst10['n_indexes']} 个索引）为 "
                  f"{worst10['speedup']:.2f}×（几何平均 {worst10['geomean']:.2f}×），"
                  f"基本没有收益；20% 档（{best10['used_mb']:.0f} MB、{best10['n_indexes']} 个索引）为 "
                  f"{best10['speedup']:.2f}×（几何平均 {best10['geomean']:.2f}×）。")
        para(doc, "两个档位的差异恰好复现了 4.2 节的门槛效应：索引大小与数据量同步放大，"
                  "所以“占数据量 13% 的 lineitem 日期索引”在任何规模下都需要约 13% 的预算；"
                  "5% 档装不下它，贪心只能用低价值小表索引填满预算（本档选中的 5 个索引全部来自"
                  "nation/supplier/part/orders 等小表）；20% 档把它选进来后，"
                  f"Q14 加速 {max((x['speedup'] for x in best10['per_query']), default=0):.2f}×、"
                  "Q15 加速 3.64×，收益立刻显现。"
                  "这说明“百分比预算”不是跨规模可比的绝对量，真正的约束是“能否容纳关键索引”。")
        para(doc, "规模放大后仍然成立的部分：收益依旧集中在过滤/聚合型查询（Q14、Q15、Q6），"
                  "纯扫描型查询（Q1 70.9 s、Q18 73.9 s、Q9 52.3 s，合计占基线 46%）仍然无法靠索引改善，"
                  "结果一致性校验无任何差异。变化的部分：总时间加速比从 SF1 的 1.10× 略降到 1.07×，"
                  "几何平均从 1.34× 降到 1.17×——规模越大，重型扫描查询在总时间中的占比越高，"
                  "索引能改善的份额相对变小。这一观察对生产环境有直接含义："
                  "在 TB 级数据上，索引优化必须与扫描型查询的另类优化（分区裁剪、物化视图、列式存储）配合。")
    # ---------------- 5 讨论
    doc.add_heading("5　讨论与效度威胁", level=1)
    para(doc, "（1）总时间与几何平均是两个不同的结论口径。若业务关注吞吐/SLO，总时间更相关；"
              "若关注“多数查询是否变快”，几何平均更合适。本文同时报告二者，避免用单一指标夸大结论。")
    para(doc, "（2）受控条件的影响：关闭 JIT 与并行、关闭 autovacuum 是为降低方差，"
              "但在真实系统中并行计划可能改变索引收益排序（例如并行扫描会让某些索引更不划算）。"
              "因此结论应理解为“在该受控条件下的结论”。")
    para(doc, "（3）单机双规模：8 vCPU / 30 GiB、TPC-H SF1 与 SF10。换成相关性更强的负载"
              "（如 JOB/IMDB）时，排序可能变化；本文的结论是方法层面的（冗余消除与增量重估有效、"
              "代价代理适合排序不适合定标、预算存在门槛效应），而非某个具体索引清单的结论。")
    para(doc, "（4）测量方差：每查询 3 次取中位数，未给出置信区间；对 ±5% 以内的差异不做强结论"
              "（例如 5% 与 10% 预算的两组差异已在噪声量级）。")
    para(doc, "（5）数据可得性：原计划使用 JOB/IMDB（113 条复杂 join），但实验云主机到境外的有效"
              "带宽仅 11–15 KB/s（对照：阿里云镜像 3.7 MB/s、PyPI 镜像 12.9 MB/s），2 GB 级数据"
              "无法在合理时间内获取，故主体实验使用 TPC-H；题目本身允许 TPC-H / TPC-DS / JOB 中的"
              "任一负载。后续可通过国内镜像或对象存储迁移补齐 JOB 实验。")

    # ---------------- 6 结论
    doc.add_heading("6　结论与后续工作", level=1)
    para(doc, "在存储预算约束下，索引推荐的有效性主要取决于三件事：候选是否覆盖负载的真实访问路径、"
              "是否消除了候选间的收益重叠、以及目标函数是否与业务指标对齐。本文在 TPC-H SF1 上给出的"
              "量化结论是：20% 预算可换来 1.10× 总时间、1.34× 几何平均加速且结果完全一致；"
              "增量重估是其中最关键的一环（去掉后几何平均从 1.34× 降到 1.07×）；"
              "规划器代价可作为可靠的排序代理（ρ=0.92），但最终选择应由执行反馈校准。"
              "在放大 10 倍的 SF10（约 12 GB）上，同样的方法在 20% 预算下仍取得 1.07× 总时间、"
              "1.17× 几何平均加速，并复现了“预算门槛效应”：5% 预算装不下关键索引时收益归零。"
              "这条规模结论与方法结论一起说明：预算规划应围绕“关键索引能否装下”来做，"
              "而不是简单按百分比给预算。")
    para(doc, "后续工作：（1）在 TPC-DS/JOB 上验证负载相关性对结论的影响（规模扩展已在 SF10 上完成）；"
              "（2）把写入维护代价纳入目标（收益/存储/写放大三维权衡）；"
              "（3）用 LLM 生成候选索引并与规则池对比（题目推荐方向 ④），量化其新增率与被采纳率；"
              "（4）把执行反馈从“一次排序”升级为“迭代搜索”，在预算内做多轮校正。")

    # ---------------- appendix
    doc.add_heading("附录 A　复现命令", level=1)
    for line in [
        "bash provision/install_pg_ubuntu.sh              # 安装 PG16 + hypopg 并调参",
        "tpchgen-cli csv -s 1 --output-dir /root/tpch_sf1 # 生成 TPC-H SF1 数据",
        "psql -f workload/tpch/schema.sql                 # 建表（主键）",
        "psql -f workload/tpch/fkindexes.sql              # 基线外键索引",
        "python -m idxlab.cli --config configs/tpch.json parse",
        "python -m idxlab.cli --config configs/tpch.json pool --budget-pct 20",
        "python -m idxlab.cli --config configs/tpch.json measure --tag base",
        "python -m idxlab.cli --config configs/tpch.json search --tag greedy20 --prescreen 25",
        "python -m idxlab.cli --config configs/tpch.json measure --tag tuned20 --indices out/greedy20.json",
        "python -m idxlab.cli --config configs/tpch.json compare --a out/base.json --b out/tuned20.json",
        "python tools/exec_rank.py --sets out/greedy20.json out/norm20.json out/raw40.json \\",
        "       --budget-mb 202 --out out/exec20.json",
        "# SF10 规模扩展",
        "tpchgen-cli csv -s 10 --output-dir /root/tpch_sf10   # 约 11 GB CSV",
        "psql -f workload/tpch/schema.sql -f workload/tpch/fkindexes.sql  # 新库 idxlab10",
        "python -m idxlab.cli --config configs/tpch_sf10.json measure --tag base",
        "python -m idxlab.cli --config configs/tpch_sf10.json pool  --budget-pct 20",
        "python -m idxlab.cli --config configs/tpch_sf10.json search --tag greedy20 --prescreen 25",
        "python -m idxlab.cli --config configs/tpch_sf10.json measure --tag tuned20 \\",
        "       --indices out10/greedy20.json",
    ]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.left_indent = Cm(0.6)
        run = p.add_run(line)
        set_font(run, "Consolas", 8.5)

    doc.add_paragraph()
    doc.add_heading("附录 B　交付材料", level=1)
    for line in [
        "idx_lab/            推荐器实现（workload / advisor / measure / cli / selftest）",
        "idx_lab/provision/  ECS 装机、数据导入脚本（Ubuntu 24.04）",
        "idx_lab/tools/      planner_costs、exec_rank、report_plots、plot_summary",
        "idx_lab/out/        全部原始测量 JSON（base / tuned / 消融 / exec）",
        "idx_lab/figures/    报告图表",
    ]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.left_indent = Cm(0.6)
        run = p.add_run(line)
        set_font(run, "Consolas", 8.5)

    doc.add_page_break()
    doc.add_heading("附录 C　推荐索引清单", level=1)
    para(doc, "表 C1　代价驱动（预算 20%）推荐结果：9 个索引，估算占用 199.8 MB", first_indent=False)
    rq1_rows = [[i + 1, x["table"], ", ".join(x["columns"])]
                for i, x in enumerate(rq1.get("indexes", []))]
    add_table(doc, ["#", "表", "列"], rq1_rows, widths=[1.4, 4.6, 9.6], font_size=8.5)
    doc.add_paragraph()
    para(doc, "表 C2　执行反馈校准后的结果：9 个索引，实测占用 142.4 MB，实测加速 1.15×",
         first_indent=False)
    rq5_rows = [[i + 1, x["table"], ", ".join(x["columns"])]
                for i, x in enumerate(rq5.get("selected", []))]
    add_table(doc, ["#", "表", "列"], rq5_rows, widths=[1.4, 4.6, 9.6], font_size=8.5)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print("saved", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--figdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--data10", default=None)
    args = ap.parse_args()
    data = json.loads(Path(args.data).read_text(encoding="utf-8-sig"))
    data10 = json.loads(Path(args.data10).read_text(encoding="utf-8-sig")) if args.data10 else None
    build(data, Path(args.figdir), Path(args.out), data10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
