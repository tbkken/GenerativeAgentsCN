"""Original visual explanations for section 1.1; all examples are schematic."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle


OUT = Path(__file__).resolve().parent
INK, BLUE, GREEN, ORANGE = "#20344B", "#276FBF", "#26816B", "#CB6B28"
MUTED, LINE = "#546779", "#BCCDDC"
FONT = None
for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
    try:
        FONT = font_manager.FontProperties(fname=font_manager.findfont(family, fallback_to_default=False))
        break
    except ValueError:
        pass
if FONT is None:
    raise RuntimeError("A Chinese font is required.")
plt.rcParams["svg.fonttype"] = "path"


def text(ax, x, y, value, size=14, color=INK, ha="center", weight="normal"):
    return ax.text(x, y, value, ha=ha, va="center", fontproperties=FONT,
                   fontsize=size, color=color, fontweight=weight, linespacing=1.55)


def panel(ax, x, y, w, h, fill="#EDF4FC", edge=LINE):
    shape = FancyBboxPatch((x, y), w, h,
                          boxstyle="round,pad=0.008,rounding_size=0.015",
                          facecolor=fill, edgecolor=edge, linewidth=1.25)
    ax.add_patch(shape)
    return shape


def arrow(ax, start, end, color=BLUE, curve=0):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>",
                                mutation_scale=18, linewidth=1.65, color=color,
                                connectionstyle=f"arc3,rad={curve}"))


def canvas(title, height=7):
    fig, ax = plt.subplots(figsize=(14, height), dpi=160)
    fig.subplots_adjust(left=.025, right=.975, bottom=.025, top=.98)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    text(ax, .5, .94, title, 23, weight="bold")
    return fig, ax


def save(fig, name):
    for extension in ("png", "svg"):
        fig.savefig(OUT / f"{name}.{extension}", dpi=160, facecolor="white")
    plt.close(fig)


def answer_check():
    fig, ax = canvas("方案写得完整，两个硬条件仍然没过", 7.6)
    panel(ax, .035, .735, .37, .12)
    text(ax, .22, .795, "用户：请安排一个上午的开放日", 15)
    arrow(ax, (.42, .795), (.505, .795))
    panel(ax, .52, .735, .44, .12, "#EAF5EF")
    text(ax, .74, .795, "方案片段：10:00，手作室，15人", 16, GREEN)
    panel(ax, .035, .185, .435, .48, "#F8FAFC")
    panel(ax, .525, .185, .435, .48, "#F8FAFC")
    text(ax, .25, .607, "① 容量冲突", 18, weight="bold")
    text(ax, .745, .607, "② 预约冲突", 18, weight="bold")
    for index in range(15):
        x = .085 + (index % 5) * .076
        y = .505 - (index // 5) * .083
        color = GREEN if index < 12 else ORANGE
        ax.add_patch(Circle((x, y), .012, facecolor=color, edgecolor="none"))
        ax.plot([x, x], [y - .018, y - .044], color=color, linewidth=4, solid_capstyle="round")
    text(ax, .25, .254, "12 个名额  ＋  3 人超出", 16, ORANGE)
    # Aligned intervals express overlap, without claiming a real booking record.
    xs = [.655, .795, .935]
    for x, hour in zip(xs, ["…", "10:00", "…"]):
        text(ax, x, .52, hour, 12, MUTED)
        ax.plot([x, x], [.32, .495], color=LINE, linewidth=1, linestyle="--")
    text(ax, .62, .442, "已有预约", 12, ha="right")
    text(ax, .62, .352, "拟安排", 12, ha="right")
    ax.add_patch(Rectangle((.775, .415), .16, .053, facecolor=ORANGE, alpha=.82))
    ax.add_patch(Rectangle((.775, .325), .16, .053, facecolor=BLUE, alpha=.82))
    text(ax, .745, .254, "同一房间，10:00时段冲突", 16, ORANGE)
    text(ax, .5, .115, "模型可以先起草；容量表、预约记录和校验决定方案能否成立。", 16)
    text(ax, .5, .043, "教学虚构：人数与时间用于说明问题，不是模型实测或真实预约记录。", 12, MUTED)
    save(fig, "llm-answer-check")


def model_family():
    fig, ax = canvas("先看技术关系，再看模型的不同标签", 7.7)
    panel(ax, .035, .285, .57, .555, "#F4F7FB")
    text(ax, .065, .795, "人工智能 AI", 18, ha="left", weight="bold")
    text(ax, .40, .795, "识别、预测、规划、行动等", 12, MUTED)
    panel(ax, .075, .315, .49, .405, "#E6EFF9")
    text(ax, .10, .675, "机器学习 ML", 17, ha="left", weight="bold")
    text(ax, .395, .675, "从数据中调整参数", 12)
    panel(ax, .12, .35, .405, .245, "#CEE1F3")
    text(ax, .145, .552, "深度学习 DL", 16, ha="left", weight="bold")
    text(ax, .40, .552, "多层神经网络", 12)
    panel(ax, .16, .377, .323, .10, "#276FBF", BLUE)
    text(ax, .32, .427, "大语言模型 LLM", 18, "white", weight="bold")
    text(ax, .32, .243, "主流大语言模型的技术归属示意", 12, MUTED)
    panel(ax, .65, .605, .305, .225, "#EAF5EF")
    text(ax, .802, .775, "基础模型：看用途", 17, GREEN, weight="bold")
    text(ax, .802, .683, "经过广泛训练\n可适配多种下游任务", 14)
    panel(ax, .65, .315, .305, .225, "#FFF3E8")
    text(ax, .802, .485, "多模态：看信息形式", 17, ORANGE, weight="bold")
    text(ax, .802, .393, "可处理多类信息\n例如文字、图像、语音", 14)
    text(ax, .803, .251, "标签可重叠，并非互斥类别", 12, MUTED)
    for x, title, body in [(.04, "参数规模", "训练中调整的数值有多少"),
                           (.365, "训练数据量", "训练时使用了多少资料"),
                           (.69, "训练计算量", "训练投入了多少计算")]:
        panel(ax, x, .075, .27, .115, "#F7F9FB")
        text(ax, x + .135, .152, title, 14, BLUE, weight="bold")
        text(ax, x + .135, .104, body, 12)
    text(ax, .5, .024, "参数规模里的 1B = 十亿个参数；它不是十亿个汉字，也不是上下文长度。", 12, MUTED)
    save(fig, "model-family-map")


def tokens_context():
    fig, ax = canvas("文字先变成可计算的片段，再进入有限的上下文", 7.3)
    panel(ax, .03, .68, .215, .145)
    text(ax, .137, .786, "原始文字", 13, MUTED)
    text(ax, .137, .728, "请安排阅读分享", 18)
    arrow(ax, (.26, .75), (.325, .75))
    for i, token in enumerate(["请", "安排", "阅读", "分享"]):
        x = .35 + i * .092
        panel(ax, x, .705, .072, .095, "#EAF5EF")
        text(ax, x + .036, .752, token, 16, GREEN)
    text(ax, .52, .655, "仅示意切分，不是真实分词结果", 12, MUTED)
    arrow(ax, (.726, .75), (.784, .75))
    panel(ax, .802, .68, .163, .145, "#FFF3E8")
    text(ax, .883, .783, "Token 编号", 13, MUTED)
    text(ax, .883, .729, "a · b · c · d", 18, ORANGE)
    text(ax, .883, .654, "字母代指编号", 12, MUTED)
    arrow(ax, (.883, .66), (.883, .535))
    text(ax, .635, .525, "嵌入：每个编号映射为一组数值", 15, ha="right")
    values = [[.3, .8, .45, .65], [.6, .4, .75, .2], [.45, .7, .3, .85], [.8, .2, .6, .4]]
    for row, cells in enumerate(values):
        text(ax, .74, .553 - row * .046, "abcd"[row], 12, MUTED)
        for col, value in enumerate(cells):
            ax.add_patch(Rectangle((.768 + col * .043, .535 - row * .046), .031, .032,
                                   facecolor=BLUE, alpha=value))
    text(ax, .853, .348, "色块是数值向量示意", 12, MUTED)
    text(ax, .23, .503, "Token ≠ 汉字数", 21, BLUE, weight="bold")
    text(ax, .23, .415, "不同分词器，可能切得不同\n精确用量看对应分词器或接口统计", 13)
    text(ax, .5, .285, "上下文窗口：本次计算能容纳的信息范围", 16, weight="bold")
    spans = [(.055, .18, "指令", "#BCD4ED"), (.235, .19, "历史消息", "#D4E7DF"),
             (.425, .28, "资料／工具结果", "#F0D8BD"), (.705, .24, "生成内容所需空间", "#E6ECF1")]
    for x, width, title, fill in spans:
        ax.add_patch(Rectangle((x, .145), width, .075, facecolor=fill, edgecolor="white", linewidth=2))
        text(ax, x + width / 2, .182, title, 13)
    text(ax, .5, .069, "窗口有限，应用可能筛选或截断输入；具体容量与输入／输出限制因模型而异。", 12, MUTED)
    text(ax, .5, .025, "图中片段、编号、向量和各段宽度均为概念示意，无真实 Token 数或嵌入值。", 11, MUTED)
    save(fig, "tokens-and-context")


def attention_order():
    fig, ax = canvas("理解一句话，既要关联信息，也要保留顺序", 6.4)
    panel(ax, .03, .20, .55, .61, "#F5F8FC")
    panel(ax, .63, .20, .34, .61, "#F5F8FC")
    text(ax, .30, .747, "关联：把对象与数量联系起来", 17, weight="bold")
    for x, value, fill in [(.065, "手作室", "#CEE1F3"), (.22, "容量", "#EDF4FC"), (.36, "12人", "#D4E7DF")]:
        panel(ax, x, .57, .12, .075, fill)
        text(ax, x + .06, .607, value, 16)
    for x, value, fill in [(.065, "手作活动", "#CEE1F3"), (.22, "拟安排", "#EDF4FC"), (.36, "15人", "#F0D8BD")]:
        panel(ax, x, .385, .12, .075, fill)
        text(ax, x + .06, .422, value, 16)
    arrow(ax, (.12, .56), (.12, .47), BLUE)
    arrow(ax, (.42, .56), (.42, .47), GREEN)
    text(ax, .275, .51, "联系同一场地的约束", 12, MUTED)
    text(ax, .3, .27, "需检查：15 > 12，超出容量", 17, ORANGE)
    text(ax, .80, .747, "顺序：谁把钥匙给了谁？", 16, weight="bold")
    for y, sentence, reverse in [(.572, "甲把钥匙交给乙", False), (.35, "乙把钥匙交给甲", True)]:
        for x, person in [(.707, "甲"), (.895, "乙")]:
            ax.add_patch(Circle((x, y), .028, facecolor="#CEE1F3", edgecolor=LINE))
            text(ax, x, y, person, 17)
        start, end = ((.859, y), (.746, y)) if reverse else ((.746, y), (.859, y))
        arrow(ax, start, end)
        text(ax, .80, y + .08, sentence, 14)
    text(ax, .5, .112, "注意力帮助不同位置的信息发生关联；位置与顺序信息帮助区分含义。", 15)
    text(ax, .5, .042, "关系示意，不是真实注意力权重图；画出需要检查的关系，不代表模型必然检查正确。", 12, MUTED)
    save(fig, "attention-and-order")


if __name__ == "__main__":
    answer_check()
    model_family()
    tokens_context()
    attention_order()
