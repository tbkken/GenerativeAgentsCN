"""Render original chapter-five teaching diagrams; no runtime data is used."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parent
FONT = None
for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
    try:
        FONT = font_manager.FontProperties(
            fname=font_manager.findfont(family, fallback_to_default=False)
        )
        break
    except ValueError:
        pass
if FONT is None:
    raise RuntimeError("Install a Chinese font before rendering these diagrams.")
plt.rcParams["svg.fonttype"] = "path"
INK = "#223c52"
MUTED = "#607888"
BLUE = "#3978aa"
GREEN = "#337d69"


def label(ax, x, y, content, size=13, color=INK, align="center"):
    ax.text(x, y, content, ha=align, va="center", fontproperties=FONT,
            fontsize=size, color=color, linespacing=1.65)


def card(ax, x, y, width, height, title, body, fill="#edf3f9"):
    ax.add_patch(FancyBboxPatch((x, y), width, height,
                              boxstyle="round,pad=0.008,rounding_size=0.012",
                              facecolor=fill, edgecolor="#bfced8", linewidth=1.2))
    label(ax, x + width / 2, y + height * .74, title, 15)
    label(ax, x + width / 2, y + height * .33, body, 12)


def arrow(ax, start, end):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>",
                                mutation_scale=16, color=MUTED, linewidth=1.4))


def canvas(height=7):
    fig, ax = plt.subplots(figsize=(15, height), dpi=160)
    fig.subplots_adjust(left=.02, right=.98, top=.98, bottom=.025)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    return fig, ax


def save(fig, name):
    for extension in ("png", "svg"):
        fig.savefig(ROOT / f"{name}.{extension}", dpi=160, facecolor="white")
    plt.close(fig)


def project_handoff():
    fig, ax = canvas()
    label(ax, .5, .935, "交接一个项目：每一步都留下下一位参与者能用的材料", 21)
    items = [
        ("01  明确需求", "问题与范围\n验收判据"),
        ("02  准备输入", "来源与信息边界\n精确 Skill"),
        ("03  配置执行", "保存与身份\n预算与全部 Run"),
        ("04  分析证据", "逐项判分\n结果与局限"),
        ("05  接收复查", "实际文件\n约定范围的验证"),
    ]
    for index, (title, body) in enumerate(items):
        x = .025 + index * .195
        card(ax, x, .53, .165, .27, title, body,
             "#eaf4ef" if index == 4 else "#edf3f9")
        if index < 4:
            arrow(ax, (x + .175, .665), (x + .182, .665))
    ax.plot([.04, .965], [.40, .40], color="#cfdae2", linewidth=1)
    label(ax, .17, .31, "设计交付", 16, BLUE)
    label(ax, .17, .21, "可审读材料＋纸面接收\n运行证据保持待执行", 12)
    label(ax, .51, .31, "运行交付", 16, GREEN)
    label(ax, .51, .21, "实际输入＋全部运行＋来源\n接收范围以记录为准", 12)
    label(ax, .84, .31, "反馈与维护", 16, BLUE)
    label(ax, .84, .21, "修改有影响说明\n原始事实保持原样", 12)
    label(ax, .5, .065, "作者工作组织示意；不是内核固定流水线，也不表示本项目已经运行。", 11, MUTED)
    save(fig, "project-handoff")


def recipient_checks():
    fig, ax = canvas(7.5)
    label(ax, .5, .935, "接收检查：完成了哪项，就记录哪项", 22)
    label(ax, .5, .866, "各项回答不同问题；签收、回放和重新执行不能互相代替。", 12, MUTED)
    rows = [
        ("设计审阅", "知道在研究什么", "委托、输入、唯一差异、判据", "无需模型调用"),
        ("文件核对", "收到的是哪份材料", "原名、类型、大小、摘要、来源", "无需模型调用"),
        ("事实回放", "保存了哪些世界事实", "正式 Run 材料、身份、帧与素材", "Replay 不调用模型"),
        ("结果复算", "结论能否按规则得到", "原始记录、逐项判分、汇总方法", "保留未知与未完成"),
        ("重新执行", "此环境下能否继续工作", "可执行输入、依赖、凭据与预算", "另记新事实与消耗"),
    ]
    heads = [("检查项", .12), ("要回答的问题", .33), ("主要依据", .625), ("记录边界", .883)]
    for title, x in heads:
        label(ax, x, .772, title, 13, MUTED)
    for index, (name, question, basis, boundary) in enumerate(rows):
        y = .683 - index * .12
        ax.add_patch(FancyBboxPatch((.025, y - .049), .95, .097,
                                   boxstyle="round,pad=0.005,rounding_size=0.008",
                                   facecolor="#eaf4ef" if index == 4 else "#f0f5f9",
                                   edgecolor="none"))
        label(ax, .12, y, name, 14, GREEN if index == 4 else BLUE)
        label(ax, .33, y, question, 13)
        label(ax, .625, y, basis, 12)
        label(ax, .883, y, boundary, 11)
    label(ax, .5, .06, "检查类型示意，不是验收结果。独立环境的主机、输入、操作和结果还须分别留证。", 11, MUTED)
    save(fig, "recipient-checks")


if __name__ == "__main__":
    project_handoff()
    recipient_checks()
