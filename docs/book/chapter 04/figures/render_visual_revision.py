"""Original chapter 4 teaching diagrams, 2026-09-26.

All layouts are author proposals; all scoring values are the chapter's artificial
examples. No simulation, model request, examples data or system files are read.
Run with: python "docs/book/chapter 04/figures/render_visual_revision.py"
"""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, Circle, FancyBboxPatch, FancyArrowPatch
from PIL import Image, ImageOps, ImageDraw

OUT = Path(__file__).resolve().parent
INK, BLUE, GREEN, ORANGE = "#20344B", "#276FBF", "#25836B", "#C57720"
MUTED, LINE = "#526779", "#BCCDDC"
BF, GF, OF, NF = "#EDF4FC", "#ECF7F2", "#FFF4E6", "#F4F7FA"
FONT = font_manager.FontProperties(fname="C:/Windows/Fonts/msyh.ttc")
BOLD = font_manager.FontProperties(fname="C:/Windows/Fonts/msyhbd.ttc")
plt.rcParams.update({"svg.fonttype": "path", "axes.unicode_minus": False})
CREATED = []


def txt(ax, x, y, s, size=15, color=INK, bold=False, ha="center"):
    return ax.text(x, y, s, ha=ha, va="center", fontsize=size, color=color,
                   fontproperties=BOLD if bold else FONT, linespacing=1.5, zorder=5)


def panel(ax, x, y, w, h, fill=BF, edge=LINE):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0.005,rounding_size=0.012",
                              facecolor=fill,edgecolor=edge,lw=1.2,zorder=1))


def card(ax,x,y,w,h,title,body,fill=BF,color=BLUE,size=15):
    panel(ax,x,y,w,h,fill,color)
    txt(ax,x+w/2,y+h*.76,title,18,color,True)
    txt(ax,x+w/2,y+h*.34,body,size)


def arrow(ax,a,b,color=BLUE,curve=0):
    ax.add_patch(FancyArrowPatch(a,b,arrowstyle="-|>",mutation_scale=18,
                               color=color,lw=1.8,connectionstyle=f"arc3,rad={curve}",zorder=3))


def canvas(title, subtitle="概念示意 · 非系统截图、非 Run 结果", height=7.5):
    fig,ax=plt.subplots(figsize=(14,height),dpi=160)
    fig.subplots_adjust(left=.025,right=.975,bottom=.025,top=.975)
    ax.set(xlim=(0,1),ylim=(0,1));ax.axis("off")
    txt(ax,.5,.954,title,24,bold=True)
    txt(ax,.5,.89,subtitle,13,MUTED)
    return fig,ax


def footer(ax,s):
    txt(ax,.5,.035,s,13,MUTED)


def save(fig,stem):
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer();w,h=fig.canvas.get_width_height()
    for ax in fig.axes:
        for t in ax.texts:
            bb=t.get_window_extent(renderer)
            if bb.x0<0 or bb.y0<0 or bb.x1>w or bb.y1>h:
                raise ValueError(f"Canvas clipping in {stem}: {t.get_text()}")
    fig.savefig(OUT/f"{stem}.png",dpi=160,facecolor="white")
    fig.savefig(OUT/f"{stem}.svg",facecolor="white",metadata={"Date":None})
    plt.close(fig);CREATED.append(stem)


def matrix(ax, cols, rows, cells, x=.06,y=.17,w=.88,h=.59):
    """A colored evidence matrix; each cell is (label, type)."""
    cw=w/(len(cols)+1);rh=h/(len(rows)+1)
    for j,c in enumerate([""]+cols):
        ax.add_patch(Rectangle((x+j*cw,y+h-rh),cw,rh,facecolor=BF,edgecolor="white",lw=2))
        txt(ax,x+(j+.5)*cw,y+h-rh/2,c,14,BLUE,True)
    for i,r in enumerate(rows):
        cy=y+h-(i+2)*rh
        ax.add_patch(Rectangle((x,cy),cw,rh,facecolor=NF,edgecolor="white",lw=2))
        txt(ax,x+cw/2,cy+rh/2,r,15,bold=True)
        for j,(v,kind) in enumerate(cells[i]):
            fill,color={"ok":(GF,GREEN),"bad":(OF,ORANGE),"note":(BF,BLUE),"na":(NF,MUTED)}[kind]
            ax.add_patch(Rectangle((x+(j+1)*cw,cy),cw,rh,facecolor=fill,edgecolor="white",lw=2))
            txt(ax,x+(j+1.5)*cw,cy+rh/2,v,14,color)


def grid(fig,box,nx,ny,regions,walls=None):
    ax=fig.add_axes(box)
    ax.set(xlim=(0,nx),ylim=(ny,0),aspect="equal")
    ax.set_xticks(range(0,nx+1,2));ax.set_yticks(range(0,ny+1,2))
    ax.tick_params(labelsize=10,colors=MUTED)
    ax.set_xticks(range(nx+1),minor=True);ax.set_yticks(range(ny+1),minor=True)
    ax.grid(which="minor",color=LINE,alpha=.45,lw=.5)
    for (x,y,w,h,name,fill) in regions:
        ax.add_patch(Rectangle((x,y),w,h,facecolor=fill,edgecolor=LINE,lw=1))
        txt(ax,x+w/2,y+h/2,name,14,bold=True)
    if walls is not None:
        for x,y in walls:
            ax.add_patch(Rectangle((x,y),1,1,facecolor=INK,edgecolor="none"))
    for sp in ax.spines.values():sp.set_color(LINE)
    return ax


def study_boundaries():
    fig,ax=canvas("同一份完整教案，分三条资料路径")
    card(ax,.06,.60,.24,.21,"作者与评分者","全题、答案、判据\n只用于制作与判分",OF,ORANGE)
    card(ax,.38,.60,.24,.21,"角色 / 对象","各自事实与目标\n私有进度、真实来源",GF,GREEN)
    card(ax,.70,.60,.24,.21,"共同材料","场所、公开规则\n任务方法与工具边界")
    for i,(title,body,color) in enumerate([
        ("评分记录","证据 → 判据 → 结论",ORANGE),
        ("实际交流","真实请求、消息、感知",GREEN),
        ("本轮上下文","明确读取后才可使用",BLUE)]):
        x=.06+i*.32;arrow(ax,(x+.12,.58),(x+.12,.39),color)
        panel(ax,x,.22,.24,.16,NF,color);txt(ax,x+.12,.33,title,17,color,True);txt(ax,x+.12,.265,body,13)
    txt(ax,.5,.13,"完整答案不能借共同 Brain 或总结模板流入角色",18,ORANGE,True)
    footer(ax,"角色姓名不同 ≠ 信息已经隔离；实际装配和输入才决定谁知道什么。")
    save(fig,"study-information-boundaries")


def study_time():
    fig,ax=canvas("时间点、任务数与独立重复，分别计数")
    ax.plot([.10,.90],[.69,.69],color=BLUE,lw=3)
    for x,step,time in [(.10,"Step 1","09:00"),(.50,"…","…"),(.90,"Step 61","10:00")]:
        ax.scatter([x],[.69],s=110,color=BLUE);txt(ax,x,.77,step,17,BLUE,True);txt(ax,x,.61,time,16)
    txt(ax,.5,.51,"61 个时间点覆盖 60 分钟：T末 = T始 + (N−1) × 步长",18,bold=True)
    for i,label in enumerate(["Run A01","Run A02","Run A03"]):
        x=.06+i*.32;panel(ax,x,.18,.25,.22,GF,GREEN);txt(ax,x+.125,.35,label,18,GREEN,True)
        txt(ax,x+.125,.255,"若干任务 / 消息\n可有多个 Attempt",14)
    footer(ax,"独立新 Run 才增加重复；恢复后的 Attempt、同一世界中的角色不增加独立样本量。")
    save(fig,"study-time-and-repeats")


def hall():
    fig,ax=canvas("4.1 服务大厅：把往返成本放进空间", "20 × 16 格作者草图 · 设施位置仅示意 · 未做系统寻路")
    regs=[(1,1,18,3,"入口 · 咨询台 / 四位来访者",BF),(1,4,18,4,"等候区 · 引导员余真",NF),
          (1,8,9,7,"A 服务区\n未来场地使用",GF),(10,8,9,7,"B 服务区\n现有设施问题",OF)]
    walls={(x,y) for x in range(20) for y in range(16) if x in (0,19) or y in (0,15)}
    grid(fig,[.06,.16,.48,.64],20,16,regs,walls)
    card(ax,.60,.56,.33,.23,"五名 Agent / 三个对象","H1—H4 ＋ 引导员\n咨询台 ＋ A窗口 ＋ B窗口")
    card(ax,.60,.25,.33,.23,"两组相同","四区真实地址已知\n窗口分工不预先给来访者",GF,GREEN)
    footer(ax,"2026-10-18 09:00—10:15（+08:00）· 1 分钟 / 步 · 76 步；实际出生坐标由 UI 保存核对。")
    save(fig,"hall-map")
    fig,ax=canvas("4.1 只改一处：缺目的时，猜测还是先问？")
    card(ax,.04,.36,.23,.27,"相同首次话语","H1 = H3 的首句\nH2 = H4 的首句\n私人目的分别持有")
    card(ax,.37,.60,.26,.20,"基线","给最可能的窗口建议\n标明推测；以后可修正",OF,ORANGE)
    card(ax,.37,.24,.26,.25,"对照","缺字段 → 先问目的\n收到补充 → 再建议\n已有明确目的 → 直接建议",GF,GREEN)
    card(ax,.74,.37,.23,.27,"相同后续条件","实际移动 → 窗口回复\n→ 真实确认下一步\n错误访问仍保留计数")
    arrow(ax,(.28,.58),(.36,.68),ORANGE);arrow(ax,(.28,.43),(.36,.37),GREEN)
    arrow(ax,(.64,.69),(.73,.58),ORANGE);arrow(ax,(.64,.37),(.73,.43),GREEN)
    footer(ax,"对照新增的信息只能来自真实追问与回复；地图、模型、窗口规则、观察窗口不变。")
    save(fig,"hall-intervention")
    fig,ax=canvas("4.1 有效建议与咨询闭环：不要合并计分", "人工判分示例 · 非 Run 结果")
    matrix(ax,["首次有效回应","匹配窗口回复","正确下一步确认","主要闭环"],["H1","H2","H3","H4"],
           [[("有","ok"),("有","ok"),("有","ok"),("计入","ok")],
            [("有，7分钟","ok"),("尚无","bad"),("尚无","bad"),("不计入","bad")],
            [("有","ok"),("有","ok"),("有","ok"),("计入","ok")],
            [("有","ok"),("有","ok"),("有","ok"),("计入","ok")]],y=.28,h=.49)
    txt(ax,.5,.18,"闭环 3/4 = 75%     错误窗口请求 2/7 ≈ 28.6%     重复说明 3/4 = 0.75 次/任务",17,bold=True)
    footer(ax,"四项任务始终在主指标分母；入口有效建议可以先发生，目标窗口任务仍未闭环。")
    save(fig,"hall-scoring")


def handover():
    fig,ax=canvas("4.2 五项事实，必须通过交接渠道真正到达")
    for x,title,body,color,fill in [(.03,"设备间 · T-17","K1—K5 私有事实\nstate = attention",BLUE,BF),
        (.37,"资料区 · 记录台","只保存实际提交正文\nempty → recorded",GREEN,GF),
        (.71,"值班室 · 接班员","读取交接、复述与核对\n复核员不读私有记忆",ORANGE,OF)]:
        card(ax,x,.52,.26,.25,title,body,fill,color)
    arrow(ax,(.30,.65),(.36,.65));arrow(ax,(.64,.65),(.70,.65),GREEN)
    txt(ax,.33,.81,"交班员取得真实回复后整理",14,BLUE)
    txt(ax,.68,.43,"保存确认 → 通知 → 真实读取",15,GREEN)
    ax.plot([.16,.16,.84,.84],[.51,.25,.25,.51],color=ORANGE,lw=1.8,linestyle="--")
    txt(ax,.5,.20,"接班员独立咨询可以补救，但不补记交接主指标",17,ORANGE,True)
    footer(ax,"三人初始知道值班室、设备间、资料区地址；陌生对象由感知取得。")
    save(fig,"handover-sources")
    fig,ax=canvas("4.2 五项整理框架，不是五条事实的机械对应")
    for i,(s,c) in enumerate([("K1 现象",BLUE),("K2 数值7",BLUE),("K3 前史E-17",BLUE),("K4 缺内部结果",BLUE),("K5 未确认恢复",BLUE)]):
        y=.76-i*.115;panel(ax,.045,y-.04,.23,.08,BF,c);txt(ax,.16,y,s,16,c)
    categories=["现象","证据来源","已做（区分前史）","待做","不能确认"]
    for i,s in enumerate(categories):
        y=.76-i*.115;panel(ax,.69,y-.04,.25,.08,GF,GREEN);txt(ax,.815,y,s,17,GREEN)
    panel(ax,.35,.38,.24,.26,NF);txt(ax,.47,.56,"同一原始资料",18,bold=True);txt(ax,.47,.46,"自由说明 / 五项整理\n没有内容就标未知",15)
    for a,b in [(0,0),(0,1),(1,0),(2,1),(2,2),(3,4),(4,3),(4,4)]:
        ax.plot([.285,.68],[.76-a*.115,.76-b*.115],color=LINE,lw=1,alpha=.65,zorder=0)
    footer(ax,"只有对照调用整理子 Skill；两组均含同一依赖。子 Skill 不新增诊断、不写记忆、不提交世界动作。")
    save(fig,"handover-checklist")
    fig,ax=canvas("4.2 收到 AND 正确复述或使用，才算到达", "人工判分示例 · 非 Run 结果")
    matrix(ax,["交接内容","接班员实际表现","主指标"],["K1","K2","K3","K4","K5"],
        [[("正确传达","ok"),("正确复述","ok"),("1","ok")],[("正确传达","ok"),("正确复述","ok"),("1","ok")],
         [("前史写成刚完成","bad"),("错误含义","bad"),("0","bad")],[("遗漏","bad"),("后来独立查询","note"),("0","bad")],
         [("保留未确认","ok"),("正确复述","ok"),("1","ok")]],y=.25,h=.55)
    txt(ax,.5,.145,"交接到达 3/5 = 60%     缺依据声明 1/4 = 25%     不必要重复检查 1/3",17,bold=True)
    footer(ax,"先独立获知、后听交接：标“交接前已知”；来源无法区分：标“来源混合”，不倒推交接成功。")
    save(fig,"handover-scoring")


def gallery():
    fig,ax=canvas("4.3 同一非阻挡导览牌，只有位置不同", "24 × 18 格作者草图 · 无真实出生点、视野或路径验收",8.5)
    regs=[(1,1,22,6,"入口",BF),(1,7,10,6,"甲厅",GF),(11,7,12,6,"乙厅",OF),(1,13,22,4,"休息区",NF)]
    walls={(x,y) for x in range(24) for y in range(18) if x in (0,23) or y in (0,17)}
    for box,point,title in [([.055,.27,.405,.49],(2,2),"基线：[2,2,1,1]"),([.54,.27,.405,.49],(12,5),"对照：[12,5,1,1]")]:
        ga=grid(fig,box,24,18,regs,walls);x,y=point
        ga.add_patch(Rectangle((x,y),1,1,facecolor=ORANGE,edgecolor=INK,lw=1.5));txt(ax,box[0]+.19,.80,title,18,bold=True)
    txt(ax,.5,.20,"同时移动语义范围与显示素材；不修改任何碰撞格",19,ORANGE,True)
    txt(ax,.5,.13,"访客只预知入口 / 休息区；甲乙主题答案由真实查询获得",16)
    footer(ax,"视野5格、注意力8为候选值；两处均立即可见时，应在正式比较前重新审查干预设计。")
    save(fig,"gallery-layouts")
    fig,ax=canvas("4.3 发现、理解、抵达、使用，是四个证据环节")
    labels=[("发现","真实感知或交互"),("主题匹配","按真实介绍选择"),("抵达","实际坐标与地址"),("使用","符合兴趣的 ACT")]
    for i,(title,body) in enumerate(labels):
        x=.035+i*.245;card(ax,x,.50,.20,.25,title,body,GF if i==3 else BF,GREEN if i==3 else BLUE,14)
        if i<3:arrow(ax,(x+.209,.62),(x+.239,.62))
    card(ax,.08,.18,.35,.18,"可以补救","讲解员帮助未见牌者成功",GF,GREEN)
    card(ax,.57,.18,.35,.18,"不能偷换","看见牌 ≠ 用过主题资料",OF,ORANGE)
    footer(ax,"当前模型读取语义、state、地址，不读取牌子的图片像素；纯美术修改不等于模型视觉刺激。")
    save(fig,"gallery-evidence-chain")
    fig,ax=canvas("4.3 预算内的真实使用，才进入主要分子", "人工判分示例 · 非 Run 结果")
    matrix(ax,["匹配活动发生时路程","对应活动","主要成功"],["G1","G2","G3","G4"],
      [[("20格","ok"),("甲厅阅读图册","ok"),("是","ok")],[("32格","ok"),("甲厅阅读记录","ok"),("是","ok")],
       [("未给定","na"),("只有寻找展品","bad"),("否：活动缺失","bad")],[("43格 > 40格","bad"),("乙厅查看步骤","ok"),("否：超预算","bad")]],y=.28,h=.51)
    txt(ax,.5,.18,"主要成功 2/4 = 50%     全体路程 120格 / 2 = 60格/成功访问",18,bold=True)
    footer(ax,"累计已执行路径的相邻移动边；若数组含起点，N个点只有N−1条边。40格不是内核自动额度。")
    save(fig,"gallery-scoring")


def correction():
    fig,ax=canvas("4.4 更正必须先正式提交，再分别到达", "条件时序示意 · 各箭头不对应固定 Step",8)
    names=["三名参与者","沈禾","公告对象"]
    for x,n in zip([.15,.5,.85],names):
        txt(ax,x,.81,n,18,bold=True);ax.plot([x,x],[.20,.76],color=LINE,lw=1.5,linestyle="--")
    events=[(.71,.15,.85,"① 查询原公告：杏厅",BLUE),(.62,.15,.5,"② 三人各自真实确认",BLUE),
            (.53,.5,.85,"③ 收齐确认后申请更正",ORANGE),(.44,.85,.5,"④ 对象提交 revised，回复",GREEN),
            (.33,.5,.15,"⑤ 逐人发送正式更正：杉厅",GREEN)]
    for y,a,b,s,c in events:arrow(ax,(a,y),(b,y),c);txt(ax,(a+b)/2,y+.035,s,14,c)
    txt(ax,.5,.14,"任一前置条件未满足 → 保留缺口，不虚构下一环节",18,ORANGE,True)
    footer(ax,"revised 只公开外观标签；正式地点仍须真实查询。三名接收者需要三次双人 SPEAK。")
    save(fig,"correction-sequence")
    fig,ax=canvas("4.4 只替代“当前信念”，保留“过去发生过”")
    card(ax,.055,.58,.37,.22,"旧信念","本次当前正式会址：杏厅\n需使用实际存储的记忆 ID")
    card(ax,.565,.58,.37,.22,"历史观察","09:30 曾读到杏厅的旧公告\n世界改变不使这段经历变假",GF,GREEN)
    card(ax,.055,.20,.37,.23,"基线：append","追加“正式改为杉厅”\n旧当前信念仍有效",OF,ORANGE)
    card(ax,.565,.20,.37,.23,"对照：supersede","同一新内容替代旧当前信念\n旧节点留历史，退出有效态",GF,GREEN)
    arrow(ax,(.24,.56),(.24,.45),ORANGE);arrow(ax,(.32,.56),(.56,.39),GREEN)
    footer(ax,"两组检索问题与 limit=8 相同；找不到旧节点时同样追加，登记干预未实施，不编造 ID。")
    save(fig,"correction-belief-states")
    fig,ax=canvas("4.4 从个人收到正式更正后的决策开始计数", "人工判分示例 · 非 Run 结果")
    matrix(ax,["动作1","动作2","过期 / 相关"],["甲","乙","丙"],
      [[("说明现已改杉厅","ok"),("移动去杉厅","ok"),("0 / 2","ok")],
       [("仍移向杏厅","bad"),("断言仍在杏厅","bad"),("2 / 2","bad")],
       [("问是否正式决定","note"),("无其他相关动作","na"),("0 / 0，不适用","na")]],y=.33,h=.44)
    txt(ax,.5,.20,"汇总过期行动率 = 2 / 4 = 50%",21,ORANGE,True)
    footer(ax,"更正前行动、引用旧消息、普通问句与途经旧厅不算过期；同 Step 不代表消息已进入更早决策。")
    save(fig,"correction-scoring")


def learning():
    fig,ax=canvas("4.5 四条条件分散持有，主持人必须真实取得")
    for x,title,body in [(.04,"温乔","F1 阅读早于手作"),(.37,"任新","F2 拼图11:00"),(.70,"白露","F3 手作→操作室\nF4 阅读→阅览室")]:
        card(ax,x,.59,.26,.22,title,body,GF,GREEN)
        arrow(ax,(x+.13,.575),(.50,.39),GREEN)
    card(ax,.31,.16,.38,.23,"主持人陶明","无私有条件 / 无教师答案\n保存真实来源，定稿后逐人传达")
    txt(ax,.50,.105,"每条箭头代表真实双人交流；不表示自动广播",14,MUTED)
    footer(ax,"题面公开：3活动×50分钟，3时段各一次，3教室各一次；教室是纸上候选值，不是实际导航目标。")
    save(fig,"learning-information")
    fig,ax=canvas("4.5 教师解答：36个排列组合，被条件缩成1个", "教师专用解答 · 不得放入共同 Brain、对象或角色资料",8)
    times=["09:00—09:50","10:00—10:50","11:00—11:50"]
    matrix(ax,times,["阅览室","操作室","讨论室"],
       [[("读图阅读","ok"),("—","na"),("—","na")],[("—","na"),("纸艺手作","ok"),("—","na")],
        [("—","na"),("—","na"),("合作拼图","ok")]],y=.32,h=.46)
    txt(ax,.5,.22,"F2 固定拼图11点 → F1 固定阅读先、手作后",18,BLUE,True)
    txt(ax,.5,.145,"F3、F4 固定两间教室 → 剩余讨论室分给拼图",18,GREEN,True)
    footer(ax,"纸上活动日：2026-11-14；讨论 Run：2026-11-12 14:00—15:00，61步。")
    save(fig,"learning-teacher-schedule")
    fig,ax=canvas("4.5 部分高分，仍可能是不可执行的方案", "人工错误方案 · 阅读10点、手作9点、拼图11点；教室均正确")
    for i,(name,ok) in enumerate([(f"C{j}",j!=4) for j in range(1,8)]):
        x=.075+i*.13;panel(ax,x,.48,.105,.23,GF if ok else OF,GREEN if ok else ORANGE)
        txt(ax,x+.0525,.65,name,20,bold=True);txt(ax,x+.0525,.55,"满足" if ok else "冲突",17,GREEN if ok else ORANGE)
    txt(ax,.5,.34,"满足率 6/7 ≈ 85.7%     全部正确方案数 = 0",22,ORANGE,True)
    txt(ax,.5,.20,"C4：阅读必须早于手作；其他六项成立不能抵消这一冲突",17)
    footer(ax,"若真实发言只覆盖F1—F3：信息共享3/4；恰好猜中F4不补记共享。无定稿不能拿最佳候选代替。")
    save(fig,"learning-scoring")


def deliberation():
    fig,ax=canvas("4.6 所有候选合乎场地规则，仍没有全员可接受项", "教师分析矩阵 · 个人理由分别私有 · 非真实态度调查",8)
    matrix(ax,["A 18—19交谈","B 19—20交谈","C 19:50—20:50交谈","D 19—20静默"],
       ["何澄：20点后安静","秦川：19点后+交流","宋遥：19:30离开\n完整参与60分钟"],
       [[("符合","ok"),("符合","ok"),("不符合","bad"),("符合","ok")],
        [("无法到场","bad"),("符合","ok"),("符合","ok"),("交流需求未满足","note")],
        [("符合","ok"),("不能完整参加","bad"),("不能完整参加","bad"),("不能完整参加","bad")]],y=.27,h=.50)
    txt(ax,.5,.16,"场地：18:00—21:00，12人；计划9人，60分钟，20:50前结束留10分钟收整",16)
    footer(ax,"矩阵判断约束匹配，不替人物发言或投票；三名代表不是九名现实参加者的统计样本。")
    save(fig,"deliberation-constraints")
    fig,ax=canvas("4.6 理由说对了，与赞同候选，是两次不同确认")
    card(ax,.04,.47,.26,.30,"先收集理由","真实询问 R1—R5\n保存来源与限制强度\n两组共同执行")
    card(ax,.37,.47,.26,.30,"只对照必做","逐人复述其理由\n等待真实确认 / 修正\n基线自行归纳",GF,GREEN)
    card(ax,.70,.47,.26,.30,"再征询同一候选","同意 / 保留 / 反对\n未回应 / 待澄清\n两组共同执行")
    arrow(ax,(.31,.62),(.36,.62));arrow(ax,(.64,.62),(.69,.62),GREEN)
    txt(ax,.5,.32,"“理由没有补充”不能自动当作“同意这个候选”",21,ORANGE,True)
    txt(ax,.5,.20,"候选修改 → 重新逐人征询；不能沿用旧候选的同意",18)
    footer(ax,"只有三位代表对同一候选均明确同意，才记录一致意见；未形成一致是合法结果。")
    save(fig,"deliberation-confirmations")
    fig,ax=canvas("4.6 回应覆盖100%，也可能漏掉重要理由", "人工判分示例 · 最后候选D · 非 Run 结果")
    matrix(ax,["实际态度","最终记录","遗漏"],["何澄","秦川","宋遥"],
      [[("同意","ok"),("R1准确","ok"),("无","ok")],[("保留","note"),("R2、R3准确","ok"),("无","ok")],
       [("反对","bad"),("R4准确，仅写反对","note"),("R5完整60分钟","bad")]],y=.34,h=.45)
    txt(ax,.5,.23,"理由准确纳入 4/5 = 80%   明确回应 3/3 = 100%   保留事项记录 1/2 = 50%",17,bold=True)
    txt(ax,.5,.13,"应写“未形成一致意见”，不能总结成“大家基本同意D”",19,ORANGE,True)
    footer(ax,"主指标分母只含真实公开的重要理由；同时报告公开理由数/5，避免少听少记反而得高分。")
    save(fig,"deliberation-scoring")


def closure():
    fig,ax=canvas("4.7 只关闭上缺口，下缺口仍可通行", "17 × 11 格作者设计 · 不是系统截图 · 路线为纸面示意",8)
    regs=[(1,1,4,3,"北",BF),(1,4,4,3,"中",BF),(1,7,4,3,"南",BF),(5,1,4,9,"",NF),(9,1,7,9,"集合区",GF)]
    base={(x,y) for x in range(17) for y in range(11) if x in (0,16) or y in (0,10) or (x==8 and y not in (3,8))};base.add((6,5))
    for box,closed,title in [([.05,.28,.425,.49],False,"基线：上、下缺口都可走"),([.525,.28,.425,.49],True,"对照：仅(8,3)改为阻挡")]:
        walls=base|({(8,3)} if closed else set());ga=grid(fig,box,17,11,regs,walls)
        txt(ax,box[0]+.21,.80,title,17,bold=True)
        if closed:
            ga.plot([7.5,7.5,9.5,9.5],[3.5,8.5,8.5,3.5],color=ORANGE,lw=2.4)
        else:ga.plot([7.5,9.5],[3.5,3.5],color=GREEN,lw=2.4)
        txt(ga,6.5,5.5,"B",11,"white")
    txt(ax,.5,.17,"碰撞在封存前确定，Run 中不动态改变；公告和背景提示保持相同",18,ORANGE,True)
    footer(ax,"B为阻挡公告对象；纸面连通不替代 UI 路径检查与真实已提交 MOVE。")
    save(fig,"closure-layouts")
    fig,ax=canvas("4.7 计划路径不算路程，实际移动边才算")
    pts=[(.12,.63),(.28,.63),(.44,.63),(.60,.63),(.76,.63),(.90,.63)]
    for i,(x,y) in enumerate(pts):
        ax.scatter([x],[y],s=130,color=GREEN if i<4 else LINE);txt(ax,x,y+.09,f"点{i}",15)
        if i<len(pts)-1:ax.plot([x,pts[i+1][0]],[y,y],color=GREEN if i<3 else LINE,lw=4,linestyle="-" if i<3 else "--")
    txt(ax,.36,.49,"已执行：点0→1→2→3，共3条边",19,GREEN,True)
    txt(ax,.79,.39,"剩余计划不计入",17,MUTED)
    card(ax,.08,.15,.36,.18,"路线可达","可以同一步开始 MOVE\n无需先制造失败或等待")
    card(ax,.56,.15,.36,.18,"真实到达","提交坐标进入集合区\n不以“已集合”的文字代替",GF,GREEN)
    footer(ax,"图中步数不是系统默认速度；真实预算按步长核对。accepted 是动作选择，StepResult 才是提交事实。")
    save(fig,"closure-executed-path")
    fig,ax=canvas("4.7 把未到达者走过的路，也放进分子", "人工判分示例 · 非 Run 结果")
    for i,(name,n,ok) in enumerate([("参与者1",12,True),("参与者2",18,True),("参与者3",9,False)]):
        y=.71-i*.17;txt(ax,.15,y,name,17,bold=True)
        ax.add_patch(Rectangle((.29,y-.035),n/25*.47,.07,facecolor=GREEN if ok else ORANGE))
        txt(ax,.78,y,f"{n}格 · {'到达' if ok else '未到达'}",16,GREEN if ok else ORANGE)
    txt(ax,.5,.20,"到达率 2/3 ≈ 66.7%     人均移动 (12+18+9)/3 = 13格",20,bold=True)
    footer(ax,"20次请求中2次参数非法、1次合法目标不可达：无效2/20=10%，不可达另列。引导员成本仍计入。")
    save(fig,"closure-scoring")


def memory():
    fig,ax=canvas("4.8 跨午夜：完整日期决定窗口与承诺分母", "候选窗口与人工承诺示例 · 每步10分钟 · 时间轴间距不按时长比例",8)
    ax.plot([.08,.92],[.62,.62],lw=3,color=BLUE)
    marks=[(.08,"10月19日08:00","Step1"),(.36,"10月19日16:00","顾澄承诺截止"),(.64,"10月20日00:00","首次跨日整理条件"),(.86,"10月20日08:00","Step145 / 窗口末"),(.92,"08:30","罗予截止（练习）")]
    for i,(x,t,label) in enumerate(marks):
        ax.scatter([x],[.62],s=110,color=ORANGE if i==4 else BLUE)
        txt(ax,x,.74 if i%2==0 else .49,t,14,ORANGE if i==4 else INK)
        txt(ax,x,.81 if i%2==0 else .42,label,13,ORANGE if i==4 else MUTED)
    panel(ax,.10,.15,.80,.14,GF,GREEN)
    txt(ax,.50,.25,"24小时：145步；48小时扩展：289步",19,GREEN,True)
    txt(ax,.50,.19,"144步只到次日07:50；窗口外承诺仍待履行，不算失败",15)
    footer(ax,"图中事件用于解释条件与边界，不保证人物在指定时刻完成行为；窗口末未投递回复不倒填。")
    save(fig,"memory-timeline")
    fig,ax=canvas("4.8 同样再做一次，三类事项含义不同")
    card(ax,.035,.48,.285,.31,"一次性任务","报名已确认后\n无新需求再提交\n→ 待计数的重复",OF,ORANGE)
    card(ax,.358,.48,.285,.31,"周期活动","每日09:00晨读\n次日是新实例\n→ 不算昨天任务重复",GF,GREEN)
    card(ax,.68,.48,.285,.31,"自愿承诺","邀请 → 明确接受\n承诺者+内容+截止时间\n→ 才产生一个承诺")
    txt(ax,.5,.34,"共同规则：动作前待确认，下轮据真实事实更新；一次成功世界动作后结束",16,bold=True)
    txt(ax,.5,.22,"唯一差异：对照首次跨日时，额外调用一次事实整理子 Skill",19,GREEN,True)
    footer(ax,"整理不是训练参数，也不是调度器；当前 file_lexical 检索中，有总结不代表后来检索或使用过。")
    save(fig,"memory-instance-types")
    fig,ax=canvas("4.8 零重复要与闭环一起看，成本覆盖全部参与者", "人工判分与算术示例 · 非 Run 结果",8)
    for i,(title,body,fill,color) in enumerate([
      ("任务实例","A一次 + C一次 + B三次\nB后两次为重复\n重复率2/5 = 40%",OF,ORANGE),
      ("完整任务","A、B、C首次均闭环\n3/3 = 100%\n无行动≠低重复的好结果",GF,GREEN),
      ("整理声明","8项可核验声明\n6有证据 + 2有问题\n严格保真6/8 = 75%",BF,BLUE)]):
        card(ax,.035+i*.325,.46,.28,.33,title,body,fill,color,15)
    txt(ax,.5,.31,"L = 1,440 次逻辑调用（含对象360次）",19,bold=True)
    txt(ax,.5,.22,"单位成本 = L / (3人 × 24小时) = 20 次 / 人·虚拟小时",20,BLUE,True)
    footer(ax,"成本含所有 Attempt；监控阈值不是 UI 自动硬限额，在途与收尾可能继续消耗。")
    save(fig,"memory-scoring-cost")


def comparison():
    fig,ax=canvas("4.9 同样两个 Run，分母加权会改变汇总值", "人工反例 · 非 Run 结果")
    for x,title,n,k in [(.06,"Run 1",1,1),(.55,"Run 2",9,1)]:
        txt(ax,x+.19,.78,title,21,bold=True)
        for j in range(n):
            cx=x+.04+(j%5)*.075;cy=.64-(j//5)*.13
            ax.add_patch(Circle((cx,cy),.025,facecolor=GREEN if j<k else OF,edgecolor=GREEN if j<k else ORANGE,lw=1.4))
        txt(ax,x+.19,.40,f"{k}/{n} = {'100%' if n==1 else '11.1%'}",20,GREEN,True)
    card(ax,.055,.14,.40,.18,"每个 Run 同权","(100% + 11.1%)/2 ≈ 55.6%")
    card(ax,.545,.14,.40,.18,"每项任务同权","(1 + 1)/(1 + 9) = 20%",GF,GREEN)
    footer(ax,"回答的问题不同，没有一个比率自动更正确；权重与观察单位应在看结果前固定。")
    save(fig,"comparison-denominators")
    fig,ax=canvas("4.9 总投入增加，单位成功代价仍可能下降", "沿用六行人工数据 · 非 Run 结果；本图不是货币费用",8)
    matrix(ax,["逻辑调用总数","物理尝试总数","闭环任务","物理尝试/闭环"],["基线A","对照B"],
      [[("370","note"),("411","note"),("6","ok"),("68.5","note")],[("480","note"),("516","note"),("8","ok"),("64.5","note")]],y=.43,h=.31)
    txt(ax,.5,.32,"全部 Attempt 的失败、重试、未闭环任务成本都进入分子",18,ORANGE,True)
    txt(ax,.5,.21,"单位成功逻辑调用：A 370/6≈61.67；B 480/8=60",18)
    footer(ax,"业务事实限已提交边界，真实发生的未提交失败尝试仍计成本；零成功时单位值不可计算。")
    save(fig,"comparison-cost")


def contact_sheets():
    for part in range((len(CREATED)+7)//8):
        stems=CREATED[part*8:(part+1)*8]
        sheet=Image.new("RGB",(1800,4*530),"#e8edf2")
        draw=ImageDraw.Draw(sheet)
        for i,stem in enumerate(stems):
            im=Image.open(OUT/f"{stem}.png").convert("RGB")
            im=ImageOps.contain(im,(888,493))
            x=(i%2)*900+(900-im.width)//2;y=(i//2)*530+23
            sheet.paste(im,(x,y));draw.text(((i%2)*900+12,(i//2)*530+5),stem,fill=INK)
        sheet.save(OUT/f"visual-contact-{part+1}.png")


if __name__=="__main__":
    study_boundaries();study_time();hall();handover();gallery();correction()
    learning();deliberation();closure();memory();comparison();contact_sheets()
    print(f"Rendered {len(CREATED)} original PNG/SVG teaching figures and 4 contact sheets.")
