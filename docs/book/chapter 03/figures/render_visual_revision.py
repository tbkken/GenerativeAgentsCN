"""Chapter 3 teaching diagrams. Offline authoring only; no Runtime/UI/model calls.

Run with Python + matplotlib + Pillow and Microsoft YaHei (or another CJK font).
All identities, paths and timelines drawn here are schematic, not Run evidence.
"""
from pathlib import Path
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, Rectangle, FancyBboxPatch, FancyArrowPatch, Polygon
from PIL import Image, ImageOps, ImageDraw

OUT = Path(__file__).resolve().parent
INK, BLUE, GREEN, ORANGE = "#20344B", "#276FBF", "#26816B", "#CB6B28"
MUTED, LINE, PALE = "#546779", "#BCCDDC", "#F4F7FB"
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
FIGURES = []
TEXT_BOUNDS = []


def txt(ax, x, y, value, size=18, color=INK, ha="center", weight="normal"):
    return ax.text(x, y, value, ha=ha, va="center", fontsize=size,
                   fontproperties=FONT, color=color, weight=weight, linespacing=1.45)


def panel(ax, x, y, w, h, fill=PALE, edge=LINE, dashed=False):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.008,rounding_size=0.015",
                      facecolor=fill, edgecolor=edge, linewidth=1.5,
                      linestyle="--" if dashed else "-")
    ax.add_patch(p)
    return p


def arrow(ax, a, b, color=BLUE, curve=0, dashed=False):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=20,
                 lw=2, color=color, connectionstyle=f"arc3,rad={curve}",
                 linestyle="--" if dashed else "-"))


def person(ax, x, y, name, color=BLUE, size=.024):
    ax.add_patch(Circle((x, y + .04), size, fc=color, ec="none"))
    ax.plot([x, x], [y + .005, y - .055], lw=7, color=color, solid_capstyle="round")
    ax.plot([x - .033, x, x + .033], [y - .014, y - .006, y - .014], lw=4, color=color)
    txt(ax, x, y - .103, name, 16, color)


def dot(ax, x, y, label, color=BLUE, radius=.033):
    ax.add_patch(Circle((x, y), radius, fc=color, ec="white", lw=2, zorder=3))
    txt(ax, x, y, label, 16, "white", weight="bold").set_zorder(4)


def canvas(title, note="教学示意，依据正文合同绘制；不是系统截图或已执行的青禾 Run。", height=7.8):
    fig, ax = plt.subplots(figsize=(14, height), dpi=150)
    fig.subplots_adjust(left=.025, right=.975, bottom=.025, top=.985)
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis("off")
    txt(ax, .5, .94, title, 25, weight="bold")
    txt(ax, .5, .042, note, 12, MUTED)
    return fig, ax


def save(fig, name, section, title):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for ax in fig.axes:
        for item in ax.texts:
            box = item.get_window_extent(renderer)
            if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
                TEXT_BOUNDS.append({"figure": name, "text": item.get_text()})
    for suffix in ("png", "svg"):
        fig.savefig(OUT / f"{name}.{suffix}", dpi=150, facecolor="white")
    plt.close(fig)
    FIGURES.append({"section": section, "name": name, "title": title})


def story_sources():
    fig, ax = canvas("预约事实从哪里来，谁真正获得了它？")
    panel(ax, .035, .48, .235, .31, "#EAF5EF")
    txt(ax, .152, .725, "服务台", 21, GREEN, weight="bold")
    txt(ax, .152, .61, "独有预约资料\n按真实请求回答", 18)
    person(ax, .40, .63, "林岚：核实与调整")
    panel(ax, .58, .48, .19, .31, "#FFF3E8")
    txt(ax, .675, .725, "公告栏", 21, ORANGE, weight="bold")
    txt(ax, .675, .605, "旧公告 → 新公告\n须由对象提交", 17)
    person(ax, .89, .63, "访客")
    arrow(ax, (.28, .66), (.36, .66)); arrow(ax, (.445, .66), (.565, .66))
    arrow(ax, (.78, .66), (.85, .66))
    txt(ax, .32, .805, "咨询 / 回复", 15, BLUE)
    txt(ax, .51, .805, "更新请求", 15, BLUE)
    txt(ax, .825, .805, "真实查询", 15, BLUE)
    panel(ax, .08, .16, .84, .20)
    txt(ax, .5, .285, "读者已知的答案，不进入所有人的共同 Brain", 21, ORANGE)
    txt(ax, .5, .205, "四名 Agent ＋ 两个对象；24 / 10 / 16 是活动计划人数", 17)
    save(fig, "01-information-sources", "3.1", "信息来源与角色边界")


def observation_window():
    fig, ax = canvas("48 步观察到开始，尚未观察完整结束", "教学设计；节点间距不按时间比例。不是青禾 Run 的实测进度。")
    xs = [.09, .25, .58, .70, .91]
    ax.plot([xs[0], xs[-1]], [.56, .56], color=LINE, lw=6)
    ax.plot([xs[0], xs[3]], [.56, .56], color=GREEN, lw=8)
    labels = [("09:50", "Step 1"), ("10:00", "Step 11"), ("10:30", "Step 41"), ("10:37", "Step 48"), ("11:30", "窗口之外")]
    for x, (t, s) in zip(xs, labels):
        dot(ax, x, .56, "", GREEN if x <= .70 else ORANGE, .017)
        txt(ax, x, .665, t, 21); txt(ax, x, .455, s, 16)
    txt(ax, .25, .79, "原消息开始", 17, BLUE)
    txt(ax, .58, .79, "候选调整开始", 17, GREEN)
    txt(ax, .90, .79, "完整活动结束", 17, ORANGE)
    txt(ax, .40, .29, "观察：获知更正、移动、到达、开始", 20, GREEN)
    txt(ax, .5, .17, "t(k) = 2026-10-17 09:50+08:00 ＋ (k−1) × 1 分钟", 19)
    save(fig, "01-observation-window", "3.1", "观察窗口与活动终点")


def resource_scope():
    fig, ax = canvas("同一个资源名称，要先辨认编辑范围")
    for x, title, sub, col in [( .05, "基础配置", "公共作者资源", BLUE), (.56, "当前实验", "实验独立副本", GREEN)]:
        panel(ax, x, .25, .39, .55, "#EDF4FC" if col == BLUE else "#EAF5EF")
        txt(ax, x + .195, .73, title, 23, col, weight="bold")
        txt(ax, x + .195, .64, sub, 18, col)
        for y, name in [( .52, "地图"), (.42, "智能体 → 智能体 / 人群"), (.32, "技能 → 技能 / 大脑")]:
            txt(ax, x + .195, y, name, 18)
    arrow(ax, (.455, .53), (.545, .53)); txt(ax, .5, .62, "选入时\n复制", 16)
    txt(ax, .5, .15, "先确认实验名称与范围提示，再编辑、保存、重开核对", 20)
    save(fig, "02-editor-scope", "3.2", "公共资源与当前实验范围")


def resource_identity():
    fig, ax = canvas("身份、内容、位置，分别回答不同问题")
    labels=[("稳定 ID", "是哪一份资源", BLUE), ("内容摘要", "保存了哪些内容", GREEN), ("实验装配", "人物从哪里开始", ORANGE)]
    for i, (a,b,c) in enumerate(labels):
        x=.17+i*.33
        ax.add_patch(Circle((x,.63),.10,fc=c,alpha=.13,ec=c,lw=2))
        txt(ax,x,.63,a,22,c,weight="bold");txt(ax,x,.43,b,19)
    panel(ax,.07,.15,.86,.16)
    txt(ax,.5,.245,"公共 Agent：人物资料与图片    实验副本：出生位置与已知空间",18)
    txt(ax,.5,.185,"直接编辑不创建业务 Revision；公共修改不自动传播到旧实验",17,ORANGE)
    save(fig,"02-identity-content-placement","3.2","资源身份、内容与实验位置")


def semantic_map():
    fig, ax = canvas("先对齐空间范围，再核对可走通路", height=9)
    m=fig.add_axes([.07,.16,.55,.67]);m.set_aspect("equal");m.set_xlim(0,32);m.set_ylim(24,0)
    m.set_xticks([0,5,11,16,21,26,32]);m.set_yticks([0,1,13,23,24]);m.tick_params(labelsize=12)
    for x,y,w,h,n,c in [(1,1,10,12,"阅读室",BLUE),(12,1,9,12,"手作室",GREEN),(22,1,9,12,"交流室",ORANGE),(1,13,30,10,"公共大厅",MUTED)]:
        m.add_patch(Rectangle((x,y),w,h,fc=c,alpha=.12,ec=c,lw=1.5));txt(m,x+w/2,y+h/2,n,21,c)
    for x in [0,31]:m.plot([x+.5,x+.5],[.5,23.5],color=INK,lw=5)
    for y in [0,23]:m.plot([.5,31.5],[y+.5,y+.5],color=INK,lw=5)
    for x in [11,21]:m.plot([x+.5,x+.5],[.5,13.5],color=INK,lw=4)
    for a,b in [(1,5),(7,16),(18,26),(28,31)]:m.plot([a,b],[13.5,13.5],color=INK,lw=4)
    for a,b in [(5,7),(16,18),(26,28)]:m.plot([a,b],[13.5,13.5],color=GREEN,lw=7)
    txt(ax,.78,.745,"World 青禾社区\nSector 学习中心",20)
    txt(ax,.78,.56,"32 × 24 格\n每格 32 像素\n背景 1024 × 768",21,BLUE)
    txt(ax,.78,.34,"绿色：设计门洞\n不是人物已走轨迹\n对象占用见地图表",17,GREEN)
    txt(ax,.40,.105,"示意省略物件碰撞；完整录入与验收以 case/map 为准。",15,MUTED)
    save(fig,"03-semantic-map","3.3","四个 Arena 与设计门洞")


def map_layers():
    fig,ax=canvas("画面、语义和碰撞是三种不同的地图信息")
    for i,(title,body,col) in enumerate([("显示图像","背景、人物与状态外观",BLUE),("空间语义","World → Sector → Arena → Object",GREEN),("碰撞 / 寻路","可走格、阻挡格、门洞",ORANGE)]):
        x=.08+i*.10;y=.60-i*.17
        points=[(x,y),(x+.54,y),(x+.68,y+.13),(x+.14,y+.13)]
        ax.add_patch(Polygon(points,closed=True,fc=col,alpha=.13,ec=col,lw=2))
        txt(ax,x+.34,y+.065,title+"："+body,16,col)
    txt(ax,.5,.80,"同一场所，逐层核对",21,weight="bold")
    txt(ax,.5,.14,"门画得像能走，需要通路证据；对象换图，需要状态提交。",20)
    save(fig,"03-map-layers","3.3","显示、语义与碰撞层")


def spawn_selection():
    fig,ax=canvas("选择空间后自动落点，出生不是手填剧本坐标")
    placements=[("林岚","公共大厅"),("周宁","手作室"),("陈晨","交流室"),("许安","阅读室")]
    for i,(name,space) in enumerate(placements):
        x=.13+i*.245;person(ax,x,.68,name,BLUE if i<2 else GREEN)
        txt(ax,x,.40,space,21);arrow(ax,(x,.49),(x,.445))
    panel(ax,.10,.14,.80,.17)
    txt(ax,.5,.25,"实验 → 智能体 → 初始位置与空间 → 选择空间",19)
    txt(ax,.5,.18,"X / Y 只读；保存后核对实际坐标、地址与可用空间",18,ORANGE)
    save(fig,"03-spawn-selection","3.3","人物出生的页面装配")


def skill_tree():
    fig,ax=canvas("五份 Skill：调用关系与对象绑定分别建立")
    panel(ax,.04,.38,.57,.43,"#EDF4FC");txt(ax,.325,.745,"4 名 Agent 共用 1 个 Brain",22,BLUE)
    dot(ax,.325,.59,"脑",BLUE,.055)
    for x,t in [(.18,"比较候选做法"),(.47,"整理记忆依据")]:
        arrow(ax,(.325,.535),(x,.45));txt(ax,x,.415,t,18,GREEN)
    panel(ax,.67,.38,.29,.43,"#FFF3E8");txt(ax,.815,.745,"2 个对象根 Skill",21,ORANGE)
    txt(ax,.815,.60,"公告栏 → 公告规则\n服务台 → 咨询规则",19)
    txt(ax,.325,.235,"子 Skill 按需调用\n返回建议，不能 world-act",20,BLUE)
    txt(ax,.815,.235,"对象身份独立\n只能改自身状态",20,ORANGE)
    save(fig,"04-skill-topology","3.4","Brain 依赖与对象 Skill 绑定")


def brain_turn():
    fig,ax=canvas("根 Brain 选择方法，一次成功动作结束本轮")
    points=[(.12,.58,"读上下文"),(.36,.58,"感知 / 检索"),(.63,.58,"选一个动作"),(.88,.58,"结束本轮")]
    for i,(x,y,t) in enumerate(points):dot(ax,x,y,str(i+1));txt(ax,x,.46,t,19)
    for a,b in zip(points,points[1:]):arrow(ax,(a[0]+.04,.58),(b[0]-.04,.58))
    panel(ax,.28,.73,.40,.11,"#EAF5EF");txt(ax,.48,.785,"冲突或困难 → 按需调用子 Skill",18,GREEN)
    arrow(ax,(.36,.62),(.39,.72),GREEN);arrow(ax,(.61,.73),(.63,.62),GREEN)
    txt(ax,.63,.31,"必要便签写在动作前",19,ORANGE)
    txt(ax,.5,.17,"accepted 仍需整步提交核验；调用上限由运行配置约束",19)
    save(fig,"04-brain-turn","3.4","条件式 Brain 与动作结束点")


def perception():
    fig,ax=canvas("视野约束范围，注意力约束返回候选")
    ax.add_patch(Circle((.25,.55),.235,fc="#EDF4FC",ec=BLUE,lw=2))
    ax.add_patch(Circle((.25,.55),.15,fc="#EAF5EF",ec=GREEN,lw=2,linestyle="--"))
    dot(ax,.25,.55,"我",GREEN)
    for x,y in [(.18,.63),(.35,.54),(.23,.43),(.42,.64),(.10,.49)]:ax.add_patch(Circle((x,y),.014,fc=BLUE))
    txt(ax,.25,.84,"硬上限：vision_radius",18,BLUE)
    txt(ax,.25,.21,"请求可缩小，不能扩大上限",17,GREEN)
    arrow(ax,(.51,.55),(.59,.55))
    for y,l in [(.75,"空间候选"),(.61,"人物 / 对象候选"),(.47,"事件候选")]:
        txt(ax,.76,y,l,19);ax.plot([.62,.91],[y-.055,y-.055],color=GREEN,lw=4)
    txt(ax,.76,.305,"各类分别限制候选\nCURRENT 层级锚点保留",19,GREEN)
    txt(ax,.5,.11,"核对 requested / effective radius、各类候选数与 attention 截断信息",16)
    save(fig,"05-perception-limits","3.5","视野与注意力的两个边界")


def movement_budget():
    fig,ax=canvas("5 格路径：先走 4 格，再核对剩余 1 格")
    xs=[.12+i*.15 for i in range(6)]
    for i,x in enumerate(xs):
        ax.add_patch(Rectangle((x-.05,.48),.10,.15,fc="#EAF5EF" if i<=4 else "#FFF3E8",ec=GREEN if i<=4 else ORANGE,lw=2))
        txt(ax,x,.555,str(i),23)
        if i<5:arrow(ax,(x+.055,.555),(xs[i+1]-.055,.555),GREEN if i<4 else ORANGE,dashed=i==4)
    txt(ax,xs[0],.735,"起点",19);txt(ax,xs[4],.735,"首轮终点",19,GREEN);txt(ax,xs[5],.735,"目标",19,ORANGE)
    txt(ax,.37,.34,"4 格 / 分钟 × 1 分钟 = 最多 4 格",22,GREEN)
    txt(ax,.78,.34,"剩余 1 格",21,ORANGE)
    txt(ax,.5,.195,"next_coord 是路径首格；导航本身不移动，实际轨迹由 MOVE 提交。",18)
    save(fig,"05-movement-budget","3.5","路径预算与实际终点")


def action_permissions():
    fig,ax=canvas("六种动作的控制权：人物与固定对象不同")
    columns=["MOVE","ACT","WAIT","SPEAK","INTERACT","SET_OBJECT\n_STATE"]
    for i,t in enumerate(columns):txt(ax,.245+i*.13,.765,t,16)
    txt(ax,.095,.59,"Agent",21,BLUE);txt(ax,.095,.39,"固定对象",21,ORANGE)
    for y,allowed,col in [(.59,[1,1,1,1,1,0],BLUE),(.39,[0,1,1,0,0,1],ORANGE)]:
        for i,a in enumerate(allowed):
            x=.245+i*.13
            if a:
                ax.add_patch(Circle((x,y),.026,fc=col))
                ax.plot([x-.012,x-.003,x+.014],[y,y-.012,y+.014],color="white",lw=2,zorder=4)
            else:txt(ax,x,y,"—",22,MUTED)
    txt(ax,.5,.205,"对象可在同次动作中携带 responses；须引用真实请求 ID。",20)
    txt(ax,.5,.115,"每位参与者每 Step 最多成功接受一次动作；不能代改别人的状态。",17)
    save(fig,"05-action-ownership","3.5","动作原语与参与者控制权")


def memory_lifecycle():
    fig,ax=canvas("更正认识时保留历史，默认检索只用有效内容")
    panel(ax,.04,.46,.25,.28,"#EDF4FC");txt(ax,.165,.66,"旧认识",22,BLUE);txt(ax,.165,.55,"原消息：10:00 开始",18)
    arrow(ax,(.30,.60),(.435,.60),GREEN);txt(ax,.368,.74,"supersede",17,GREEN)
    panel(ax,.45,.46,.28,.28,"#EAF5EF");txt(ax,.59,.66,"新认识 + 来源",22,GREEN);txt(ax,.59,.55,"获正式更正后更新",18)
    arrow(ax,(.74,.60),(.87,.60),GREEN);txt(ax,.865,.76,"search",18,GREEN)
    txt(ax,.89,.53,"有效结果",19,GREEN)
    arrow(ax,(.165,.45),(.165,.30),ORANGE);txt(ax,.165,.235,"旧内容留历史\n不继续当有效事实",18,ORANGE)
    txt(ax,.65,.27,"append：追加独立事实\ninvalidate：失效但保留记录",20)
    txt(ax,.5,.115,"当前为 file_lexical；自然语言记忆不会自动新增导航知识。",18)
    save(fig,"06-memory-lifecycle","3.6","记忆追加、替代、失效与检索")


def pending_then_confirm():
    fig,ax=canvas("动作之前写待确认，下一轮按事实核实")
    ax.plot([.51,.51],[.18,.84],color=LINE,lw=2,linestyle="--")
    txt(ax,.26,.80,"本轮",23,BLUE);txt(ax,.75,.80,"下一轮",23,GREEN)
    for x,y,n,t,c in [(.13,.63,"1","保存待确认便签",BLUE),(.36,.63,"2","提交一次动作",BLUE),(.64,.63,"3","读真实坐标 / 回复",GREEN),(.87,.63,"4","再标记完成",GREEN)]:
        dot(ax,x,y,n,c);txt(ax,x,.50,t,17)
    for a,b in [(.17,.32),(.40,.60),(.68,.83)]:arrow(ax,(a,.63),(b,.63))
    txt(ax,.29,.33,"成功 world-act 后结束\n不能再补写“我已完成”",20,ORANGE)
    txt(ax,.755,.33,"未成功 / 未到达 / 未收到\n继续保留待确认",20,ORANGE)
    txt(ax,.5,.13,"认识、打算与世界事实分别保存；角色说过不等于世界做过。",19)
    save(fig,"06-pending-confirmation","3.6","跨步进度与动作结束边界")


def bilateral_speech():
    fig,ax=canvas("一个会话连接两个人，回复必须真实发生")
    person(ax,.20,.77,"周宁",BLUE);person(ax,.78,.77,"许安",GREEN)
    for x in [.20,.78]:ax.plot([x,x],[.15,.62],color=LINE,lw=2,linestyle="--")
    arrow(ax,(.205,.56),(.773,.56));txt(ax,.49,.62,"真实 SPEAK：提出问题",19,BLUE)
    arrow(ax,(.775,.32),(.207,.32),GREEN);txt(ax,.49,.39,"许安后续真实 SPEAK：回应",19,GREEN)
    txt(ax,.5,.205,"沿用同一个 conversation_id",20)
    txt(ax,.5,.11,"第三人不会自动加入；自己写两句引号，不能生成对方消息。",18,ORANGE)
    save(fig,"07-bilateral-speech","3.7","双人会话与真实回复")


def object_sequence():
    fig,ax=canvas("对象在人物之后运行，回复到目标的下一轮",height=8.7)
    xs=[.13,.39,.64,.87];heads=["人物阶段","对象阶段","整步提交","目标下一轮"]
    for x,h in zip(xs,heads):txt(ax,x,.82,h,20);ax.plot([x,x],[.21,.755],color=LINE,lw=2,linestyle="--")
    arrow(ax,(xs[0],.69),(xs[1],.69));txt(ax,.26,.74,"INTERACT",18,BLUE)
    txt(ax,.13,.58,"request_id",16,BLUE)
    arrow(ax,(xs[1],.51),(xs[2],.51),ORANGE);txt(ax,.515,.575,"动作 + responses",17,ORANGE)
    arrow(ax,(xs[2],.33),(xs[3],.33),GREEN);txt(ax,.765,.40,"按真实接收者投递",16,GREEN)
    txt(ax,.39,.30,"只能改自身状态",15,ORANGE)
    txt(ax,.5,.135,"同一 Step 形成事实；下一轮恰好一次投递，不广播、不补造请求。",19)
    save(fig,"07-object-sequence","3.7","对象交互与下一轮投递")


def notice_states():
    fig,ax=canvas("候选建议还不是公告更正")
    panel(ax,.035,.43,.26,.36,"#EDF4FC");txt(ax,.165,.72,"服务台反馈",22,BLUE)
    txt(ax,.165,.60,"已有预约至 10:30\n提出可行候选",19)
    arrow(ax,(.31,.60),(.43,.60));txt(ax,.37,.76,"林岚核实\n请求更新",17)
    panel(ax,.45,.43,.25,.36,"#FFF3E8");txt(ax,.575,.72,"公告栏自身提交",21,ORANGE)
    txt(ax,.575,.59,"original → revised\nSET_OBJECT_STATE",17)
    arrow(ax,(.715,.60),(.81,.60),GREEN)
    person(ax,.89,.62,"访客真实查询",GREEN)
    txt(ax,.5,.28,"两张公告图：状态键与 state_images 匹配",21)
    txt(ax,.5,.17,"服务台 ready 使用默认背景外观；并不需要另配一张 ready 图。",18,GREEN)
    save(fig,"07-notice-state-change","3.7","候选安排、公告提交与外观")


def experiment_fork():
    fig,ax=canvas("草稿可改，封存后的调整进入新的独立实验")
    for x,y,t,col in [(.16,.65,"公共资源",BLUE),(.46,.65,"实验 A\nDRAFT",BLUE),(.81,.65,"实验 A\nSEALED",GREEN),(.81,.27,"实验 B\nDRAFT",ORANGE)]:
        panel(ax,x-.115,y-.09,.23,.18,"#EAF5EF" if col==GREEN else PALE);txt(ax,x,y,t,21,col)
    arrow(ax,(.28,.65),(.335,.65));txt(ax,.31,.80,"完整复制",17)
    arrow(ax,(.58,.65),(.685,.65));txt(ax,.635,.80,"封存",18,GREEN)
    arrow(ax,(.81,.55),(.81,.375),ORANGE);txt(ax,.59,.42,"复制为新独立草稿\n保留旧实验与旧 Run",19,ORANGE)
    txt(ax,.30,.255,"公共修改不会自动更新\n已经导入的副本",20,BLUE)
    txt(ax,.5,.12,"不是业务 Revision；实验只在 DRAFT → SEALED 之间推进。",19)
    save(fig,"08-experiment-copy-seal","3.8","物理副本与封存边界")


def preflight_execution():
    fig,ax=canvas("当前执行入口：先保存与预检，再确认执行")
    labels=["执行实验","保存草稿\n与预检","查看结果\n与预计消耗","确认执行\n封存并创建 Run"]
    for i,l in enumerate(labels):
        x=.12+i*.25;dot(ax,x,.62,str(i+1),GREEN if i==3 else BLUE);txt(ax,x,.47,l,19)
        if i<3:arrow(ax,(x+.045,.62),(x+.205,.62))
    ax.plot([.715,.715],[.33,.80],color=ORANGE,lw=2,linestyle="--")
    txt(ax,.44,.27,"预检：材料结构与依赖\n不能代替穿门、交互与恢复实测",20,ORANGE)
    txt(ax,.5,.12,"图示当前入口含义，不虚构一个已验收的独立“只封存”按钮。",18)
    save(fig,"08-preflight-execution","3.8","保存预检与确认执行")


def run_states():
    fig,ax=canvas("状态描述执行进度，业务结果要另查证据")
    xlist=[.11,.30,.49,.70,.90];labs=["CREATED","QUEUED","RUNNING","FINALIZING","COMPLETED"]
    for i,(x,l) in enumerate(zip(xlist,labs)):
        dot(ax,x,.66,str(i+1),GREEN if i==4 else BLUE);txt(ax,x,.55,l,15)
        if i<4:arrow(ax,(x+.04,.66),(xlist[i+1]-.04,.66))
    for x,l,desc in [(.25,"PAUSED","暂停"),(.5,"CANCELLED","取消"),(.75,"FAILED","失败")]:
        panel(ax,x-.115,.265,.23,.14,"#FFF3E8");txt(ax,x,.355,l,17,ORANGE);txt(ax,x,.29,desc,16)
    txt(ax,.5,.455,"还需区分这些执行结局（图中未穷举状态转换）",17,MUTED)
    txt(ax,.5,.15,"排队不等于持久队列；完成不等于业务通过；收尾不等于已导出 .garun。",17)
    save(fig,"09-run-states","3.9","执行状态的不同含义")


def calls_and_time():
    fig,ax=canvas("轮次、调用、尝试和三种时间，单位各不相同")
    txt(ax,.25,.79,"(4 人 + 2 对象) × 48 步 = 288 轮",20,BLUE)
    panel(ax,.06,.40,.39,.24,"#EDF4FC");txt(ax,.255,.56,"1 次逻辑调用",22,BLUE)
    arrow(ax,(.25,.395),(.145,.27));arrow(ax,(.28,.395),(.365,.27),ORANGE)
    txt(ax,.14,.22,"物理尝试 1",18);txt(ax,.38,.22,"可能的重试",18,ORANGE)
    for y,title,sub,c in [(.75,"虚拟时间","故事时刻；暂停不自动推进",GREEN),(.51,"模型处理时间","真实请求与生成耗时",BLUE),(.27,"墙钟时间","还包含暂停、工具和人工处理",ORANGE)]:
        ax.plot([.54,.60],[y,y],color=c,lw=6);txt(ax,.62,y+.035,title,21,c,ha="left");txt(ax,.62,y-.045,sub,16,ha="left")
    txt(ax,.5,.11,"288 是粗估公式基数，不是已发生的模型调用；未知 Token 不填免费。",17)
    save(fig,"09-calls-time-units","3.9","轮次、模型尝试与时间单位")


def run_attempt_identity():
    fig,ax=canvas("续跑保留 Run，重跑创建另一个 Run")
    panel(ax,.06,.45,.88,.33,"#EDF4FC");txt(ax,.15,.71,"Run A",22,BLUE)
    panel(ax,.24,.51,.25,.18,"#EAF5EF");txt(ax,.365,.60,"Attempt 1\nStep 1 … k",19,GREEN)
    panel(ax,.61,.51,.26,.18,"#EAF5EF");txt(ax,.74,.60,"Attempt 2\nStep k+1 …",19,GREEN)
    arrow(ax,(.505,.60),(.592,.60));txt(ax,.55,.735,"续跑",18)
    panel(ax,.06,.16,.88,.20,"#FFF3E8");txt(ax,.15,.26,"Run B",22,ORANGE)
    txt(ax,.57,.26,"重跑：原 Run 内嵌实验 → 新 Attempt，从 Step 1 开始",18)
    save(fig,"10-run-attempt-identity","3.10","Run 与 Attempt 身份")


def recovery_boundary():
    fig,ax=canvas("只从最新已提交边界的完整快照继续")
    for i,t in enumerate(["k−2","k−1","k","k+1"]):
        x=.13+i*.235;panel(ax,x-.085,.49,.17,.20,"#EAF5EF" if i<3 else "#FFF3E8",dashed=i==3)
        txt(ax,x,.59,"Step "+t,21,GREEN if i<3 else ORANGE)
    txt(ax,.365,.78,"完整已提交事实",22,GREEN);txt(ax,.84,.78,"计算中 / 未提交",19,ORANGE)
    ax.plot([.715,.715],[.32,.83],color=ORANGE,lw=2,linestyle="--")
    arrow(ax,(.60,.48),(.60,.30),GREEN);txt(ax,.57,.23,"恢复快照在 k\n新 Attempt 下一提交为 k+1",20,GREEN)
    txt(ax,.19,.235,"缺少最新完整快照\n诊断，不静默回退",18,ORANGE)
    txt(ax,.5,.115,"已提交到 12 而只有 10 的完整快照，不能直接从 11 再做。",19)
    save(fig,"10-recovery-boundary","3.10","最新提交与恢复快照边界")


def cancellation():
    fig,ax=canvas("提交取消请求，不等于远端推理已经退出")
    ax.plot([.09,.92],[.57,.57],color=LINE,lw=5)
    for x,n,t in [(.12,"1","发出模型请求"),(.40,"2","点击取消"),(.70,"?","在途请求结束"),(.90,"3","确认最终状态")]:
        dot(ax,x,.57,n,ORANGE if n=="?" else BLUE);txt(ax,x,.72,t,18)
    txt(ax,.52,.40,"文件控制标志、步骤边界和可中断退避\n各自有检查位置",20)
    txt(ax,.5,.245,"同步 requests.post 在途等待能否立即中断，仍需专项验收。",19,ORANGE)
    txt(ax,.5,.13,"记录请求时刻、最终 Step 和状态；本图没有测量或承诺取消延迟。",17)
    save(fig,"10-cancel-request-versus-stop","3.10","取消请求与实际停止")


def commit_chain():
    fig,ax=canvas("动作被接受后，还要跨过世界提交与持久边界")
    steps=[("world-act","接受动作选择"),("World Commit","形成实际变化"),("StepResult","统一整步事实"),("持久提交","帧 + 校验 + 快照"),("Replay","读已提交边界")]
    for i,(a,b) in enumerate(steps):
        x=.09+i*.205;dot(ax,x,.61,str(i+1),GREEN if i>=3 else BLUE)
        txt(ax,x,.76,a,18);txt(ax,x,.45,b,16)
        if i<4:arrow(ax,(x+.04,.61),(x+.165,.61))
    txt(ax,.5,.275,"Event + 非空 structured_payload 支撑重建",22,GREEN)
    txt(ax,.5,.155,"计划、Thought 与临时文本用于审计；不能直接驱动画面或宣布完成。",18,ORANGE)
    save(fig,"11-commit-chain","3.11","从动作选择到已提交事实")


def replay_reduction():
    fig,ax=canvas("回放是沿事实重建状态，不再调用 Brain")
    for i in range(4):
        x=.08+i*.025;y=.43+i*.06;panel(ax,x,y,.23,.23,"#EDF4FC")
    txt(ax,.24,.655,"已提交帧",23,BLUE)
    arrow(ax,(.375,.59),(.46,.59))
    ax.add_patch(Circle((.56,.59),.10,fc="#EAF5EF",ec=GREEN,lw=2));txt(ax,.56,.59,"校验\n归约",23,GREEN)
    arrow(ax,(.675,.59),(.75,.59))
    panel(ax,.77,.43,.18,.30);txt(ax,.86,.59,"位置 / 状态\n会话 / 外观",19)
    txt(ax,.24,.295,"身份、哈希、连续性",19,BLUE)
    txt(ax,.76,.295,"同一批事实 → 一致状态",19,GREEN)
    txt(ax,.5,.16,"无需重新执行模型；可丢弃的投影缓存不是事实源。",21)
    save(fig,"11-replay-reduction","3.11","事实归约与回放隔离")


def evidence_diagnosis():
    fig,ax=canvas("画面或叙述可疑时，沿证据向下定位")
    tiers=[(.77,"声称：已到达 / 已更正 / 已通知",.83,BLUE),(.57,"核对：坐标轨迹 / 状态事件 / 请求与消息",.70,GREEN),(.37,"定位：Run → Attempt → Step → 参与者",.57,ORANGE)]
    for y,t,w,c in tiers:
        ax.add_patch(Polygon([(.5-w/2,y+.07),(.5+w/2,y+.07),(.5+w/2-.05,y-.07),(.5-w/2+.05,y-.07)],fc=c,alpha=.12,ec=c,lw=2))
        txt(ax,.5,y,t,20,c)
    arrow(ax,(.5,.695),(.5,.65));arrow(ax,(.5,.495),(.5,.45))
    txt(ax,.5,.19,"区分配置问题、行为偏差、正确拒绝与系统异常，再记录复现材料。",18)
    save(fig,"11-diagnosis-evidence","3.11","从观察到最小可定位证据")


def three_evaluation_axes():
    fig,ax=canvas("执行状态、行为质量与业务成效，分别作答")
    for x,n,title,desc,c in [(.17,"1","执行状态","是否执行结束\n在哪里中断",BLUE),(.50,"2","行为质量","回退、重复、空记忆\n规则与调用证据",ORANGE),(.83,"3","业务成效","消息是否更正\n访客是否获知并行动",GREEN)]:
        dot(ax,x,.69,n,c,.06);txt(ax,x,.52,title,24,c);txt(ax,x,.34,desc,19)
    txt(ax,.5,.16,"COMPLETED 不自动推出业务通过；NOT_EVALUATED 不是通过。",20)
    save(fig,"12-evaluation-dimensions","3.12","三层评价各自的证据")


def metric_population():
    fig,ax=canvas("到达率先定分母：本案例只有两名目标访客")
    panel(ax,.04,.30,.47,.49,"#EAF5EF");txt(ax,.275,.72,"分母：陈晨与许安，共 2 人",21,GREEN)
    person(ax,.16,.56,"陈晨",GREEN);person(ax,.39,.56,"许安",GREEN)
    txt(ax,.275,.355,"分子：窗口内实际到达目标站位的人数",17)
    panel(ax,.57,.30,.39,.49,"#FFF3E8");txt(ax,.765,.72,"不混入这个分母",21,ORANGE)
    txt(ax,.765,.54,"组织者与志愿者\n两个对象\n活动计划中的 24 / 10 / 16 人",19)
    txt(ax,.5,.19,"传播延迟另设起止事实；未获知记未观察到，不能补成零延迟。",19)
    txt(ax,.5,.105,"图中不填写到达结果；没有新 Run 分数。",17,MUTED)
    save(fig,"12-metric-denominators","3.12","目标人群与指标分母")


def package_contents():
    fig,ax=canvas("用内容和清单区分包，不靠修改后缀")
    for x,title,parts,c in [(.035,"config  .zip",["资源内容","自身附件","可显式未绑定依赖"],BLUE),(.365,"exp  .gaexp",["完整资源闭包","实验装配","输入与参数"],GREEN),(.695,"run  .garun",["完整内嵌实验","执行事实 / Attempt","帧、快照与质量"],ORANGE)]:
        panel(ax,x,.35,.27,.45,"#EDF4FC" if c==BLUE else "#EAF5EF" if c==GREEN else "#FFF3E8")
        txt(ax,x+.135,.73,title,22,c)
        for j,p in enumerate(parts):txt(ax,x+.135,.60-j*.085,p,18)
    txt(ax,.5,.23,"ga-package v2；package_kind 与清单 UUID 确认类型和身份",19)
    txt(ax,.5,.115,"“下载全部”的结果 ZIP ≠ 正式 .garun；检查点 ZIP ≠ 完整 Run。",19,ORANGE)
    save(fig,"13-package-contents","3.13","三种正式包与结果 ZIP")


def handoff_levels():
    fig,ax=canvas("跨机器交付：字节核对、事实回放、执行能力分别验收")
    for i,(title,desc,c) in enumerate([("原包与完整性","文件大小、SHA256\n清单与成员校验",BLUE),("离线回放","读取已有事实与素材\n无需再次调用模型",GREEN),("恢复 / 重跑","兼容环境与模型服务\n目标机器提供凭据",ORANGE)]):
        x=.17+i*.33;dot(ax,x,.68,str(i+1),c,.058);txt(ax,x,.515,title,23,c);txt(ax,x,.355,desc,19)
        if i<2:arrow(ax,(x+.08,.68),(x+.245,.68))
    txt(ax,.5,.18,"密钥不在包中；任何一层通过，都不能替代下一层的真实验证。",20)
    save(fig,"13-handoff-levels","3.13","迁移与复现的三个验收层次")


def architecture_boundaries():
    fig,ax=canvas("行为方法可以变，事实与权限边界必须保留")
    panel(ax,.05,.34,.42,.44,"#EDF4FC");txt(ax,.26,.705,"Brain / Skill 决定",24,BLUE)
    txt(ax,.26,.54,"何时感知、咨询与比较\n如何使用证据和选择动作\n何时等待或结束本轮",20)
    panel(ax,.54,.34,.41,.44,"#EAF5EF");txt(ax,.745,.705,"内核保证",24,GREEN)
    txt(ax,.745,.54,"身份与能力校验\n世界提交、时间与恢复\n已提交事实的可回放性",20)
    txt(ax,.5,.23,"ReAct / 反思 / 排程是可选方法；不固化成所有角色的内核流水线。",18)
    txt(ax,.5,.12,"交付时分别保留：精确输入、真实执行记录、评价规则与未验收边界。",18)
    save(fig,"14-behavior-kernel-boundary","3.14","行为自由与内核边界")


def contact_sheets():
    names=[x["name"] for x in FIGURES]+["package-flow","step-and-feedback"]
    for start in range(0,len(names),6):
        batch=names[start:start+6]
        sheet=Image.new("RGB",(1800,1620),"#e6ecf2")
        draw=ImageDraw.Draw(sheet)
        for k,name in enumerate(batch):
            im=Image.open(OUT/f"{name}.png").convert("RGB")
            im.thumbnail((890,505))
            x=(k%2)*900+(900-im.width)//2;y=(k//2)*540+28
            sheet.paste(im,(x,y));draw.text(((k%2)*900+18,(k//2)*540+8),name,fill=INK)
        sheet.save(OUT/f"visual-review-{start//6+1:02d}.png")


if __name__ == "__main__":
    for f in [story_sources,observation_window,resource_scope,resource_identity,semantic_map,map_layers,spawn_selection,
              skill_tree,brain_turn,perception,movement_budget,action_permissions,memory_lifecycle,pending_then_confirm,
              bilateral_speech,object_sequence,notice_states,experiment_fork,preflight_execution,run_states,calls_and_time,
              run_attempt_identity,recovery_boundary,cancellation,commit_chain,replay_reduction,evidence_diagnosis,
              three_evaluation_axes,metric_population,package_contents,handoff_levels,architecture_boundaries]:
        f()
    contact_sheets()
    (OUT/"visual-manifest.json").write_text(json.dumps({"figures":FIGURES,"text_outside_canvas":TEXT_BOUNDS},ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Generated {len(FIGURES)} diagrams, PNG + SVG. Text outside canvas: {len(TEXT_BOUNDS)}")
