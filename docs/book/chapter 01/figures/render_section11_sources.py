"""Render three original section 1.1 teaching diagrams; no network or model calls.

Run this file directly. It writes only the six PNG/SVG artifacts named below.
All numerical examples are illustrative facts from the chapter, not measurements.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


OUT = Path(__file__).resolve().parent
INK = "#20344B"
MUTED = "#526779"
BLUE = "#276FBF"
GREEN = "#25836B"
ORANGE = "#C57720"
BLUE_FILL = "#EDF4FC"
GREEN_FILL = "#ECF7F2"
ORANGE_FILL = "#FFF4E6"
NEUTRAL_FILL = "#F4F7FA"
LINE = "#B5C7D8"


def select_font():
    windows_regular = Path("C:/Windows/Fonts/msyh.ttc")
    windows_bold = Path("C:/Windows/Fonts/msyhbd.ttc")
    if windows_regular.exists() and windows_bold.exists():
        return (
            font_manager.FontProperties(fname=windows_regular),
            font_manager.FontProperties(fname=windows_bold),
        )
    for family in ("Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "SimHei"):
        try:
            regular = font_manager.findfont(family, fallback_to_default=False)
            bold = font_manager.findfont(
                font_manager.FontProperties(family=family, weight="bold"),
                fallback_to_default=False,
            )
            return (
                font_manager.FontProperties(fname=regular),
                font_manager.FontProperties(fname=bold),
            )
        except ValueError:
            pass
    raise RuntimeError("A Chinese font is required: Microsoft YaHei or Noto Sans CJK SC.")


FONT, BOLD = select_font()
plt.rcParams.update({"svg.fonttype": "path", "axes.unicode_minus": False})


def canvas(height):
    fig, ax = plt.subplots(figsize=(14.8, height), dpi=160)
    fig.patch.set_facecolor("white")
    fig.subplots_adjust(left=0.012, right=0.988, top=0.985, bottom=0.015)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    return fig, ax


def text(ax, x, y, value, size=16, color=INK, bold=False, align="center"):
    return ax.text(
        x, y, value, ha=align, va="center", color=color, fontsize=size,
        fontproperties=BOLD if bold else FONT, linespacing=1.55,
        zorder=5,
    )


def panel(ax, x, y, w, h, fill=NEUTRAL_FILL, edge=LINE, radius=0.014, lw=1.3):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.004,rounding_size={radius}",
        facecolor=fill, edgecolor=edge, linewidth=lw, zorder=1,
    ))


def card(ax, x, y, w, h, title, body, fill=BLUE_FILL, color=BLUE,
         title_size=18, body_size=16):
    panel(ax, x, y, w, h, fill, color)
    text(ax, x+w/2, y+h*0.75, title, title_size, color, True)
    text(ax, x+w/2, y+h*0.34, body, body_size)


def arrow(ax, start, end, color=BLUE, lw=2.0, style="arc3,rad=0"):
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle="-|>", mutation_scale=20,
        linewidth=lw, color=color, connectionstyle=style,
        shrinkA=0, shrinkB=0, zorder=3,
    ))


def check_canvas(fig):
    """Catch accidental canvas clipping without implying semantic validation."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width, height = fig.canvas.get_width_height()
    for ax in fig.axes:
        for artist in ax.texts:
            bbox = artist.get_window_extent(renderer)
            if bbox.x0 < 0 or bbox.y0 < 0 or bbox.x1 > width or bbox.y1 > height:
                raise ValueError(f"Text outside canvas: {artist.get_text()!r}")


def save(fig, stem):
    check_canvas(fig)
    fig.savefig(OUT/f"{stem}.png", dpi=160, facecolor="white")
    fig.savefig(
        OUT/f"{stem}.svg", facecolor="white",
        metadata={"Creator": "GenerativeAgentsCN book original teaching figure", "Date": None},
    )
    plt.close(fig)


def training_and_inference():
    fig, ax = canvas(8.6)
    text(ax, .5, .955, "训练与使用：参数在哪里改变？", 25, bold=True)
    text(ax, .5, .895, "训练调整模型；普通推理使用已经训练好的模型", 16, MUTED)

    rows = (
        (.660, "预训练", "大量资料", "文本等训练数据", "训练计算", "学习规律", "更新参数", "形成基础能力", BLUE, BLUE_FILL),
        (.435, "后训练", "示范与反馈", "任务答案、偏好等", "继续训练", "改善行为", "更新参数", "增强任务适配", GREEN, GREEN_FILL),
        (.210, "推理", "当前输入", "问题＋房间容量表", "已有模型", "使用已有参数", "生成回答", "本次活动方案", BLUE, BLUE_FILL),
    )
    for y, phase, source, source_body, process, process_body, result, result_body, color, fill in rows:
        panel(ax, .025, y, .125, .160, fill, color)
        text(ax, .0875, y+.080, phase, 20, color, True)
        card(ax, .19, y, .23, .16, source, source_body, fill, color)
        card(ax, .49, y, .18, .16, process, process_body, fill, color)
        card(ax, .745, y, .23, .16, result, result_body, fill, color)
        arrow(ax, (.433, y+.080), (.476, y+.080), color)
        arrow(ax, (.683, y+.080), (.731, y+.080), color)

    text(ax, .73, .166, "普通推理通常不修改模型参数", 16, ORANGE, True)
    panel(ax, .09, .045, .82, .080, ORANGE_FILL, ORANGE)
    text(ax, .5, .085, "今天给出一张房间表  →  改变本次可用的上下文", 18, ORANGE, True)
    save(fig, "training-and-inference")


def knowledge_sources():
    fig, ax = canvas(8.6)
    text(ax, .5, .955, "四种“知道”：信息必须走过一条明确的路径", 24, bold=True)

    card(ax, .025, .665, .255, .18, "③ 外部资料与工具", "文件、数据库\n预约查询等服务", BLUE_FILL, BLUE, 18, 16)
    card(ax, .025, .380, .255, .18, "④ 应用持久记忆", "保存的偏好\n任务进度与历史记录", GREEN_FILL, GREEN, 18, 16)

    card(ax, .335, .515, .185, .185, "应用按需读取", "选择、检索\n带回真实内容", ORANGE_FILL, ORANGE, 17, 16)
    arrow(ax, (.289, .755), (.325, .665), BLUE)
    arrow(ax, (.289, .470), (.325, .550), GREEN)
    arrow(ax, (.530, .608), (.572, .608), ORANGE)

    panel(ax, .585, .430, .180, .285, BLUE_FILL, BLUE)
    text(ax, .675, .671, "② 当前上下文", 18, BLUE, True)
    text(ax, .675, .550, "本轮指令与历史\n资料和工具结果\n被读出的记忆", 15.5)

    panel(ax, .575, .805, .195, .082, NEUTRAL_FILL, LINE)
    text(ax, .6725, .846, "用户消息与指令", 16, MUTED)
    arrow(ax, (.675, .793), (.675, .728), BLUE)

    card(ax, .805, .430, .170, .285, "已有模型", "处理当前输入\n生成回答", BLUE_FILL, BLUE, 19, 16)
    arrow(ax, (.775, .573), (.795, .573), BLUE)

    card(ax, .800, .805, .180, .110, "① 参数知识", "训练学到的规律", GREEN_FILL, GREEN, 17, 14)
    arrow(ax, (.890, .793), (.890, .728), GREEN)
    text(ax, .960, .765, "影响\n计算", 13.5, GREEN)

    panel(ax, .11, .240, .78, .090, ORANGE_FILL, ORANGE)
    text(ax, .5, .285, "文件在电脑里，不等于已经进入模型的上下文", 18, ORANGE, True)
    text(ax, .5, .169, "外部资料与持久记忆，都要由应用读取后再提供", 16, MUTED)

    panel(ax, .10, .035, .80, .085, GREEN_FILL, GREEN)
    text(ax, .5, .0775, "仿真角色此刻在哪里？以系统提交的位置事实为准。", 17, GREEN, True)
    save(fig, "knowledge-sources")


def variability_and_errors():
    fig, ax = canvas(9.5)
    text(ax, .5, .956, "回答会变化，也可能出错：两件事分开看", 24, bold=True)
    text(ax, .5, .900, "人工示例，不是模型实测；两次回答不能用来估计稳定性或错误率", 15, MUTED)

    panel(ax, .085, .819, .83, .058, NEUTRAL_FILL, LINE)
    text(ax, .5, .848, "核验依据：手作室容量为 12 人；本次没有执行预约。", 16)

    card(ax, .040, .593, .435, .185, "稳定，且正确", "回答1：容量12人，预约未执行。\n回答2：容量12人，预约未执行。", GREEN_FILL, GREEN, 19, 16)
    card(ax, .525, .593, .435, .185, "稳定，但错误", "回答1：容量15人，预约已完成。\n回答2：容量15人，预约已完成。", ORANGE_FILL, ORANGE, 19, 16)
    card(ax, .040, .365, .435, .185, "表述变化，事实一致", "回答1：容量12人，预约未执行。\n回答2：最多12人；尚未预约。", BLUE_FILL, BLUE, 19, 16)
    card(ax, .525, .365, .435, .185, "表述变化，含有错误", "回答1：容量12人，预约未执行。\n回答2：容量12人，预约已完成。", ORANGE_FILL, ORANGE, 19, 16)

    text(ax, .5, .310, "发现问题后，先定位资料、执行与约束", 18, bold=True)
    card(ax, .030, .095, .290, .155, "容量数字错  →  查资料", "正确容量表是否\n实际送入上下文？", BLUE_FILL, BLUE, 16.5, 15.5)
    card(ax, .355, .095, .290, .155, "声称已预约  →  查执行", "是否存在真实请求\n以及成功执行记录？", GREEN_FILL, GREEN, 16.5, 15.5)
    card(ax, .680, .095, .290, .155, "忽略约束  →  查校验", "人数、房间与时间\n是否通过规则检查？", ORANGE_FILL, ORANGE, 16.5, 15.5)
    text(ax, .5, .035, "减少输出差异不保证正确；不能用“随机性太大”解释全部错误。", 15, MUTED)
    save(fig, "variability-and-errors")


if __name__ == "__main__":
    training_and_inference()
    knowledge_sources()
    variability_and_errors()
    print("Rendered 3 original diagrams: 160 dpi PNG and SVG with outlined text.")
