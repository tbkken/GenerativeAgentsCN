"""Render original chapter diagrams as PNG and SVG; no model or network calls."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent
INK = "#20344B"
BLUE = "#276FBF"
MUTED = "#546779"


def choose_font():
    for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC"):
        try:
            return font_manager.FontProperties(
                fname=font_manager.findfont(family, fallback_to_default=False)
            )
        except ValueError:
            continue
    raise RuntimeError("Install a Chinese font: Microsoft YaHei, SimHei or Noto Sans CJK SC.")


FONT = choose_font()
plt.rcParams["svg.fonttype"] = "path"


def label(ax, x, y, text, size=13, color=INK, weight="normal"):
    ax.text(x, y, text, ha="center", va="center", fontsize=size,
            color=color, fontproperties=FONT, fontweight=weight, linespacing=1.65)


def box(ax, x, y, w, h, title, body, fill="#EDF4FC"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.009,rounding_size=0.018",
                               linewidth=1.2, edgecolor="#A6BDD4", facecolor=fill))
    label(ax, x + w / 2, y + h - 0.08, title, size=16, weight="bold")
    label(ax, x + w / 2, y + h / 2 - 0.04, body)


def arrow(ax, start, end, color=BLUE, connectionstyle="arc3,rad=0"):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=17,
                                linewidth=1.6, color=color, connectionstyle=connectionstyle))


def canvas(width, height):
    fig, ax = plt.subplots(figsize=(width, height), dpi=160)
    fig.patch.set_facecolor("white")
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    fig.subplots_adjust(left=0.015, right=0.985, top=0.98, bottom=0.02)
    return fig, ax


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=180, facecolor="white")
    fig.savefig(OUT / f"{name}.svg", facecolor="white")
    plt.close(fig)


def text_generation():
    fig, ax = canvas(14, 4.8)
    label(ax, 0.5, 0.94, "从实际输入到逐步生成的回答", size=19, weight="bold")
    specs = [
        (0.025, "1  实际输入", "任务指令与用户消息\n本轮实际送入的资料\n和工具结果"),
        (0.275, "2  数值表示", "分词与 Token 编号\n向量表示与顺序信息\n切分方式依分词器而定"),
        (0.525, "3  模型计算", "通过多层计算\n关联上下文信息\n得到后续 Token 的分数"),
        (0.775, "4  选择与输出", "按生成规则选择 Token\n继续生成或满足停止条件\n逐步组成一段回答"),
    ]
    for x, title, body in specs:
        box(ax, x, 0.37, 0.20, 0.43, title, body)
    for x in (0.235, 0.485, 0.735):
        arrow(ax, (x, 0.585), (x + 0.030, 0.585))
    ax.plot([0.875, 0.875, 0.375], [0.355, 0.205, 0.205], color=BLUE, lw=1.6)
    arrow(ax, (0.375, 0.205), (0.375, 0.355))
    label(ax, 0.64, 0.14, "未停止：把新 Token 加入已有内容，继续生成", size=13, color=BLUE)
    label(ax, 0.5, 0.035, "概念示意：生成合理文字与核验外部事实，是不同环节。", size=11, color=MUTED)
    save(fig, "text-generation")


def application_layers():
    fig, ax = canvas(15, 5.1)
    label(ax, 0.5, 0.94, "把模型放进能够检查结果的应用", size=19, weight="bold")
    specs = [
        (0.02, "用户目标", "要求与约束\n需要交付什么"),
        (0.22, "组织输入", "任务说明\n相关资料与历史"),
        (0.42, "模型处理", "生成候选\n提出工具请求"),
        (0.62, "实际执行", "查询真实记录\n执行获准操作"),
        (0.82, "核验与交付", "规则检查\n检查产物和状态"),
    ]
    for x, title, body in specs:
        box(ax, x, 0.50, 0.16, 0.31, title, body,
            fill="#EDF4FC" if x < 0.62 else "#EAF5EF")
    for x in (0.185, 0.385, 0.585, 0.785):
        arrow(ax, (x, 0.655), (x + 0.025, 0.655))
    box(ax, 0.21, 0.07, 0.22, 0.24, "资料与持久状态", "按需读取 · 标明来源", fill="#F7F3E8")
    arrow(ax, (0.30, 0.323), (0.30, 0.485))
    ax.plot([0.90, 0.90, 0.50], [0.485, 0.37, 0.37], color=BLUE, lw=1.6)
    arrow(ax, (0.50, 0.37), (0.50, 0.485))
    label(ax, 0.725, 0.285, "带回真实结果或校验反馈\n需要继续时再次处理；达到边界就停止", size=12, color=BLUE)
    label(ax, 0.71, 0.09, "模型提出请求 ≠ 工具已执行\n文件已生成 ≠ 所有业务条件已满足", size=12, color=MUTED)
    save(fig, "application-layers")


if __name__ == "__main__":
    text_generation()
    application_layers()
    print("Wrote two original diagrams in PNG and SVG formats.")
