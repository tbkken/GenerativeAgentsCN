"""Original conceptual diagrams; PNG and SVG, no model/network calls.
Optional rendering dependency: matplotlib. Reader scripts don't require it.
"""
from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

HERE = Path(__file__).resolve().parent
INK, BLUE, MUTED = "#20344b", "#276fbf", "#607487"


def font():
    for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        try:
            return font_manager.FontProperties(fname=font_manager.findfont(family, fallback_to_default=False))
        except ValueError:
            pass
    raise RuntimeError("Install a Chinese font such as Microsoft YaHei or Noto Sans CJK SC.")


FONT = font()
plt.rcParams["svg.fonttype"] = "path"


def label(ax, x, y, text, size=13, color=INK, weight="normal"):
    ax.text(x, y, text, fontsize=size, fontproperties=FONT, fontweight=weight,
            color=color, ha="center", va="center", linespacing=1.65)


def box(ax, x, y, w, h, title, body, color="#edf4fc"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.008,rounding_size=0.014", fc=color, ec="#b9ccdf", lw=1.1))
    label(ax, x+w/2, y+h-0.055, title, 15, weight="bold")
    label(ax, x+w/2, y+h/2-0.04, body, 12)


def arrow(ax, start, end):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=17, lw=1.5, color=BLUE))


def canvas(width=15, height=6):
    fig, ax = plt.subplots(figsize=(width, height), dpi=160)
    ax.set(xlim=(0,1), ylim=(0,1)); ax.axis("off")
    fig.subplots_adjust(left=.015,right=.985,bottom=.02,top=.98)
    return fig, ax


def save(fig, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(destination.with_suffix('.'+ext), facecolor="white", dpi=170)
    plt.close(fig)


def technology_map():
    fig,ax=canvas()
    label(ax,.5,.94,"从任务要求到可验证交付",21,weight="bold")
    items=[(.035,"表达任务","Prompt · 上下文\n提供目标与相关证据"),(.28,"提出候选","模型 · 结构化输出\n生成方案或工具请求"),(.525,"实际执行","Function Calling · MCP\nCLI / Shell 执行操作"),(.77,"校验交付","业务规则 · 评估\n核对文件与最终状态")]
    for x,t,b in items:box(ax,x,.51,.195,.27,t,b)
    for x in (.238,.483,.728):arrow(ax,(x,.645),(x+.026,.645))
    box(ax,.06,.14,.25,.20,"补充信息","检索 / RAG · 图像与语音",color="#f7f3e8")
    box(ax,.375,.14,.25,.20,"组织方法","Skill · 规划 · Agent 循环",color="#eaf5ef")
    box(ax,.69,.14,.25,.20,"分工与控制","多角色协作 · 预算与记录",color="#eaf5ef")
    label(ax,.5,.065,"按任务需要组合：连接能力、做事方法与结果验证，分别承担不同职责。",12,MUTED)
    save(fig,HERE/'technology-map')


def agent_loop():
    fig,ax=canvas(14,6)
    label(ax,.5,.94,"模型决定下一步，应用执行并核验",21,weight="bold")
    for x,t,b in [(.035,"观察","用户任务与材料\n实际工具结果"),(.28,"决定","生成候选方案\n或选择下一项工具"),(.525,"执行","校验身份与参数\n运行受控函数 / 命令"),(.77,"核验","检查约束与进展\n决定继续或结束")]:box(ax,x,.53,.195,.27,t,b)
    for x in (.238,.483,.728):arrow(ax,(x,.665),(x+.026,.665))
    ax.plot([.866,.866,.13],[.513,.37,.37],color=BLUE,lw=1.5)
    arrow(ax,(.13,.37),(.13,.51))
    label(ax,.46,.30,"仍有问题且预算允许：带回真实结果，进入下一轮",13,BLUE)
    arrow(ax,(.866,.355),(.866,.24))
    box(ax,.72,.055,.25,.17,"结束并保留记录","通过 · 失败 · 无进展 · 预算耗尽",color="#eaf5ef")
    label(ax,.33,.12,"一次工具请求 ≠ 一次已执行操作\n文字总结 ≠ 业务验证结果",13,MUTED)
    save(fig,HERE/'agent-loop')


def layout():
    fig,ax=canvas(12,7)
    label(ax,.5,.94,"青禾社区学习中心 · 教学平面示意",20,weight="bold")
    for x,w,t,b,c in [(.06,.31,"阅读室","reading",'#edf4fc'),(.395,.22,"手作室","craft",'#f7f3e8'),(.64,.30,"交流室","discussion",'#eaf5ef')]:
        ax.add_patch(Rectangle((x,.41),w,.35,fc=c,ec='#526c83',lw=2))
        label(ax,x+w/2,.62,t,21,weight="bold");label(ax,x+w/2,.50,b,13,MUTED)
    ax.add_patch(Rectangle((.06,.24),.88,.13,fc='#f0f2f4',ec='#a5b3c0',lw=1.5))
    label(ax,.5,.305,"公共走廊（仅标示相对位置）",16)
    label(ax,.5,.13,"不按比例绘制；不包含门洞、碰撞或导航信息。",12,MUTED)
    label(ax,.5,.065,"容量以 rooms.csv 为准；预约以 availability.json 为准。",12,MUTED)
    save(fig,HERE.parent/'examples'/'data'/'reference-layout')


if __name__=='__main__':
    technology_map();agent_loop();layout()
    print('Wrote three original figures as PNG and SVG.')
