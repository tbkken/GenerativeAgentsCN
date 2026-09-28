"""Render original diagrams and a clearly labeled synthetic teaching plot."""
from pathlib import Path
import json
from statistics import median
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parent
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
INK, BLUE, GREEN, GRAY = "#253b50", "#3978b3", "#388770", "#657789"


def text(ax, x, y, value, size=13, color=INK):
    ax.text(x, y, value, fontproperties=FONT, fontsize=size, color=color,
            ha="center", va="center", linespacing=1.6)


def box(ax, x, y, w, h, title, body, color="#edf3f9"):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle="round,pad=0.008,rounding_size=0.018", fc=color, ec="#b9cbd8", lw=1.3))
    text(ax, x+w/2, y+h*.73, title, 16)
    text(ax, x+w/2, y+h*.32, body, 12)


def arrow(ax, x1, y1, x2, y2):
    ax.add_patch(FancyArrowPatch((x1,y1), (x2,y2), arrowstyle="-|>",
        color=GRAY, mutation_scale=18, lw=1.4))


def save(fig, name):
    for ext in ("png", "svg"):
        fig.savefig(ROOT/f"{name}.{ext}", dpi=160, facecolor="white")
    plt.close(fig)


def experiment_design():
    fig, ax = plt.subplots(figsize=(14, 7), dpi=160)
    fig.subplots_adjust(left=.015,right=.985,bottom=.02,top=.98)
    ax.set(xlim=(0,1), ylim=(0,1)); ax.axis("off")
    text(ax,.5,.94,"从一个应用问题，走到可解释的比较",22)
    box(ax,.04,.59,.23,.22,"共同条件","角色 · 事实 · 模型\n空间 · 时间 · 判分")
    box(ax,.39,.68,.23,.17,"基线 A","固定原有方法")
    box(ax,.39,.40,.23,.17,"对照 B","只改变预定因素", "#eaf4ef")
    box(ax,.74,.59,.21,.22,"各自独立运行","保留全部 Run\n恢复不增加样本量")
    arrow(ax,.28,.70,.375,.76); arrow(ax,.28,.66,.375,.49)
    arrow(ax,.635,.76,.725,.72); arrow(ax,.635,.49,.725,.63)
    box(ax,.20,.11,.25,.19,"证据与评分","已提交事实 → 逐项判断")
    box(ax,.57,.11,.30,.19,"分布、失败与成本","报告差异及适用条件", "#eaf4ef")
    arrow(ax,.84,.57,.84,.35); arrow(ax,.81,.35,.38,.35)
    arrow(ax,.38,.35,.38,.31); arrow(ax,.465,.205,.55,.205)
    text(ax,.5,.025,"研究流程示意，不表示本章案例已经运行或策略已经有效。",11,GRAY)
    save(fig,"study-design")


def synthetic_comparison():
    data = json.loads((ROOT.parent/"examples/comparison.json").read_text("utf-8"))
    rows = data["rows"]
    fig, axes = plt.subplots(1,2,figsize=(12,6),dpi=160)
    fig.subplots_adjust(left=.07,right=.97,top=.78,bottom=.24,wspace=.3)
    fig.suptitle("人工教学数据：更多闭环，与更长的已完成时延",fontproperties=FONT,fontsize=19,color=INK,y=.96)
    fig.text(.5,.86,"6行作者构造记录，不是系统Run或模型实测结果",ha="center",fontproperties=FONT,fontsize=12,color=GRAY)
    for i,(name,color) in enumerate((("A",BLUE),("B",GREEN))):
        members=[r for r in rows if r["group"]==name]
        rates=[len(r["completed_minutes"])/r["tasks"]*100 for r in members]
        times=[t for r in members for t in r["completed_minutes"]]
        total=sum(r["tasks"] for r in members)
        offsets=[-.12,0,.12]
        axes[0].scatter([i+x for x in offsets],rates,s=65,color=color)
        axes[0].plot([i-.22,i+.22],[len(times)/total*100]*2,color=color,lw=3)
        axes[1].scatter([i+(k-(len(times)-1)/2)*.035 for k in range(len(times))],times,s=38,color=color)
        axes[1].plot([i-.22,i+.22],[median(times)]*2,color=color,lw=3)
        fig.text(.715 if i==0 else .89,.145,f"{name} 未闭环：{total-len(times)} / {total}",ha="center",fontproperties=FONT,fontsize=11,color=color)
    axes[0].set(ylim=(0,100),yticks=range(0,101,25))
    axes[1].set(ylim=(0,22),yticks=range(0,21,5))
    for ax,title,ylabel in zip(axes,("每次记录的闭环率","仅已闭环任务的时延"),("闭环率（%）","虚拟分钟")):
        ax.set_title(title,fontproperties=FONT,fontsize=14,pad=12,color=INK)
        ax.set_ylabel(ylabel,fontproperties=FONT,fontsize=12,color=INK)
        ax.set(xlim=(-.5,1.5),xticks=(0,1))
        ax.set_xticklabels(("A 基线","B 澄清策略"),fontproperties=FONT,fontsize=12)
        ax.spines[["top","right"]].set_visible(False)
        ax.grid(axis="y",alpha=.2); ax.set_axisbelow(True)
    fig.text(.27,.145,"点：每次记录；短线：合计闭环率",ha="center",fontproperties=FONT,fontsize=11,color=GRAY)
    fig.text(.73,.09,"点：已完成任务；短线：中位数\n未闭环任务的完成时间未知，不填成60分钟。",ha="center",fontproperties=FONT,fontsize=10,color=GRAY)
    save(fig,"teaching-comparison")


if __name__ == "__main__":
    experiment_design()
    synthetic_comparison()
    print("Rendered two original figures as PNG and SVG; synthetic data only.")
