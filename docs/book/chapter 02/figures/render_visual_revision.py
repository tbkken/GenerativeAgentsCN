"""Render chapter 2 teaching diagrams (2026-09-26 visual revision).

No network, model, browser or business-state access. Run this file directly.
The figures describe the accompanying teaching code; they are not UI screenshots
or measurements. SVG text is converted to paths. PNG resolution is 160 dpi.
"""
from pathlib import Path
import argparse
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Circle

OUT = Path(__file__).resolve().parent
INK, MUTED = "#20344B", "#526779"
BLUE, GREEN, ORANGE = "#276FBF", "#25836B", "#C57720"
BF, GF, OF, NF, LINE = "#EDF4FC", "#ECF7F2", "#FFF4E6", "#F4F7FA", "#B5C7D8"

def fonts():
    path=Path("C:/Windows/Fonts/msyh.ttc")
    if path.exists():
        return font_manager.FontProperties(fname=path), font_manager.FontProperties(fname="C:/Windows/Fonts/msyhbd.ttc")
    for family in ("Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "SimHei"):
        try:
            p=font_manager.findfont(family, fallback_to_default=False)
            return font_manager.FontProperties(fname=p), font_manager.FontProperties(fname=p, weight="bold")
        except ValueError: pass
    raise RuntimeError("A Chinese font is required")
FONT, BOLD = fonts()
plt.rcParams.update({"svg.fonttype":"path", "axes.unicode_minus":False})
FIGURES=[]

def txt(ax,x,y,s,size=16,color=INK,bold=False,ha="center"):
    return ax.text(x,y,s,fontsize=size,color=color,fontproperties=BOLD if bold else FONT,
                   ha=ha,va="center",linespacing=1.45,zorder=6)

def box(ax,x,y,w,h,title,body="",color=BLUE,fill=BF):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0.004,rounding_size=0.012",facecolor=fill,edgecolor=color,lw=1.3,zorder=2))
    if body:
        txt(ax,x+w/2,y+h*.72,title,18,color,True)
        txt(ax,x+w/2,y+h*.30,body,15.5)
    else: txt(ax,x+w/2,y+h/2,title,18,color,True)

def arr(ax,a,b,color=BLUE,rad=0,lw=2):
    ax.add_patch(FancyArrowPatch(a,b,arrowstyle="-|>",mutation_scale=19,connectionstyle=f"arc3,rad={rad}",lw=lw,color=color,zorder=3))

def line(ax,a,b,color=LINE,lw=1.5,style="-"):
    ax.plot([a[0],b[0]],[a[1],b[1]],color=color,lw=lw,linestyle=style,zorder=1)

def canvas(title,subtitle="",height=7.4,basis="教学示意 · 依据本节与配套示例 · 非 UI 截图、非实测结果"):
    fig,ax=plt.subplots(figsize=(14.8,height),dpi=160)
    fig.subplots_adjust(left=.018,right=.982,top=.98,bottom=.025)
    fig.patch.set_facecolor("white");ax.set(xlim=(0,1),ylim=(0,1));ax.axis("off")
    txt(ax,.5,.952,title,24,INK,True)
    if subtitle: txt(ax,.5,.883,subtitle,16,MUTED)
    line(ax,(.025,.075),(.975,.075))
    txt(ax,.5,.035,basis,13,MUTED)
    return fig,ax

def note(ax,s,y=.13): txt(ax,.5,y,s,16,ORANGE,True)

def save(fig,stem):
    fig.canvas.draw();renderer=fig.canvas.get_renderer();W,H=fig.canvas.get_width_height()
    for ax in fig.axes:
        for t in ax.texts:
            b=t.get_window_extent(renderer)
            if b.x0<0 or b.y0<0 or b.x1>W or b.y1>H:
                raise ValueError(f"{stem}: text outside canvas: {t.get_text()!r}")
    fig.savefig(OUT/(stem+".png"),dpi=160,facecolor="white")
    fig.savefig(OUT/(stem+".svg"),facecolor="white",metadata={"Creator":"GenerativeAgentsCN original teaching diagram","Date":None})
    plt.close(fig);FIGURES.append(stem)

def chain(ax,items,y=.52,h=.19,left=.035,right=.965):
    n=len(items);gap=.035;w=(right-left-gap*(n-1))/n
    for i,item in enumerate(items):
        title,body,*style=item;x=left+i*(w+gap)
        color,fill=style if style else (BLUE,BF)
        box(ax,x,y,w,h,title,body,color,fill)
        if i<n-1:arr(ax,(x+w+.005,y+h/2),(x+w+gap-.005,y+h/2))

def request_lifecycle():
    f,a=canvas("一次请求走过哪些层？","先定位故障所在层，再决定是否重试")
    chain(a,[("本地程序","读取材料与环境变量"),("SDK / HTTP","构造并发送请求"),("模型服务","检查权限并生成"),("响应对象","状态、输出与用量")],.57)
    for x,t,b in [(.15,"本地错误","解释器 / 文件 / 依赖"),(.50,"服务错误","认证 / 权限 / 配额"),(.85,"任务错误","缺事实 / 违反约束")]:
        box(a,x-.125,.25,.25,.17,t,b,ORANGE,OF)
    arr(a,(.15,.56),(.15,.43),ORANGE);arr(a,(.62,.56),(.50,.43),ORANGE);arr(a,(.85,.56),(.85,.43),ORANGE)
    note(a,"output_text 是文本入口；不能替代状态、工具项和 usage")
    save(f,"01-request-lifecycle")

def routes_proof():
    f,a=canvas("两条实践路线，共用一套验收材料","共享规则不等于共享权限、账户或计费")
    box(a,.035,.40,.20,.22,"同一组材料","需求、房间、预约")
    box(a,.35,.63,.27,.16,"API 路线","自己管理请求与执行")
    box(a,.35,.24,.27,.16,"WorkBuddy 路线","使用工作空间与工具",GREEN,GF)
    box(a,.75,.40,.215,.22,"同一校验器","文件 + 报告 + 退出码",GREEN,GF)
    for yy,c in [(.71,BLUE),(.32,GREEN)]:arr(a,(.24,.51),(.34,yy),c);arr(a,(.63,yy),(.74,.51),c)
    txt(a,.485,.515,"各自记录实际步骤与产物",16,MUTED)
    note(a,"聊天里说“成功”，不能替代本次文件与真实校验结果")
    save(f,"01-routes-and-proof")

def task_contract():
    f,a=canvas("把愿望拆成一个可验收的任务合同","六个问题共同约束候选；角色名称不会额外授予权限")
    for x,t,b in [(.04,"目标","要交付什么"),(.36,"输入","依据哪些材料"),(.68,"约束","必须满足什么")]:box(a,x,.62,.28,.17,t,b)
    for x,t,b in [(.04,"输出","文件与格式"),(.36,"假设","未知项如何处理"),(.68,"验收","用什么判通过")]:box(a,x,.25,.28,.17,t,b,GREEN,GF)
    for x in [.18,.82]:arr(a,(x,.61),(x,.43))
    txt(a,.5,.52,"候选排期必须同时回答上下两排",17,ORANGE,True)
    note(a,"容量 12 人是硬约束；“尽量上午”是可权衡的偏好")
    save(f,"02-task-contract")

def prompt_comparison():
    f,a=canvas("比较 Prompt 时，只改变任务描述","对照设计示意；图中没有模型成绩")
    box(a,.055,.65,.89,.13,"固定：材料、模型、工具、校验器与评分表")
    for x,t,b in [(.065,"写法 A","一句愿望"),(.365,"写法 B","目标 + 约束"),(.665,"写法 C","完整合同 + 示例")]:
        box(a,x,.37,.27,.18,t,b);arr(a,(x+.135,.64),(x+.135,.56))
        arr(a,(x+.135,.36),(x+.135,.23),GREEN)
    box(a,.055,.11,.89,.12,"逐次记录全部结果：通过、失败、缺失项、耗时",color=GREEN,fill=GF)
    save(f,"02-prompt-comparison")

def context_packing():
    f,a=canvas("上下文是经过选择的信息包","示意面积没有表示真实 Token 比例")
    for y,t,b in [(.64,"任务要求","指令与硬约束"),(.40,"资料与工具结果","来源、日期、适用范围"),(.16,"当前工作状态","候选、错误与待确认项")]:box(a,.035,y,.27,.16,t,b)
    box(a,.40,.40,.21,.20,"筛选与压缩","先去无关，再保留证据",GREEN,GF)
    for y in [.72,.48,.24]:arr(a,(.315,y),(.39,.50))
    box(a,.72,.34,.245,.34,"本轮输入","事实 + 来源 + 状态\n完整保留关键约束")
    arr(a,(.62,.50),(.71,.50),GREEN)
    txt(a,.84,.25,"另为模型输出预留空间",15.5,ORANGE)
    save(f,"03-context-packing")

def conversation_state():
    f,a=canvas("延续对话：两种明确的状态路线","选用实际需要的状态机制，仍单独保存业务进度")
    txt(a,.12,.70,"手工历史",18,BLUE,True)
    chain(a,[("本轮输入","历史 + 新材料"),("完整输出项","response.output"),("下轮输入","保留输出项再追加")],.60,.18,.245,.97)
    txt(a,.12,.38,"响应关联",18,GREEN,True)
    chain(a,[("上一轮响应","真实 response.id",GREEN,GF),("关联请求","previous_response_id",GREEN,GF),("显式指令","每次重新提供",GREEN,GF)],.28,.18,.245,.97)
    note(a,"只拼接 output_text 会丢掉工具项；关联响应也不等于免费 Token")
    save(f,"03-conversation-state")

def fact_refresh():
    f,a=canvas("事实更新后，旧的“通过”必须重新审视","青禾材料变体：只在独立副本中改变需求")
    chain(a,[("原需求","手作 10 人"),("原房间","容量 12 人"),("旧候选","可能满足容量")],.62)
    chain(a,[("新需求","手作改为 13 人",ORANGE,OF),("同一房间","容量仍为 12 人"),("重新判定","原安排不可行",ORANGE,OF)],.28)
    arr(a,(.185,.61),(.185,.48),ORANGE)
    txt(a,.5,.53,"重新读取文件 → 撤回旧结论 → 再校验",17,GREEN,True)
    note(a,"保持人数要求；没有替代房间时，不能擅自把人数改回 10")
    save(f,"03-fact-refresh")

def validation_gates():
    f,a=canvas("结构合法，只是经过了第一组关卡","每一层都有独立的失败原因；前一层通过不保证后一层通过")
    chain(a,[("响应可用","状态 / 拒绝 / 中断"),("格式正确","JSON + Schema"),("业务可行","人数 / 时间 / 预约"),("来源有效","日期 / 文件 / 范围")],.56)
    for x in [.14,.38,.62,.86]:arr(a,(x,.55),(x,.36),ORANGE)
    box(a,.06,.20,.88,.15,"任一必要关卡失败：保留原因，修正输入或停止",color=ORANGE,fill=OF)
    note(a,"本章使用 Responses 的 text.format；校验器另外检查业务规则")
    save(f,"04-validation-gates")

def interval_collision():
    f,a=canvas("半开区间：端点相接可以，时间重叠不行","2026-10-17 · Asia/Shanghai · 手作室已有预约 [10:00, 10:30)",height=8.0)
    x0,x1=.27,.94
    def xx(v):return x0+(v-9)/3*(x1-x0)
    for h in [9,9.5,10,10.5,11,11.5,12]:
        x=xx(h);line(a,(x,.22),(x,.77),LINE,1,":");txt(a,x,.82,f"{int(h):02d}:{'30' if h%1 else '00'}",14,MUTED)
    for y,label,s,e,col,fill,end in [(.68,"已有预约",10,10.5,ORANGE,OF,"占用"),(.53,"候选 A",9,10,GREEN,GF,"允许"),(.38,"候选 B",10,11,ORANGE,OF,"冲突"),(.23,"候选 C",10.5,11.5,GREEN,GF,"允许")]:
        txt(a,.10,y,label,17,INK,True);box(a,xx(s),y-.045,xx(e)-xx(s),.09,end,color=col,fill=fill)
    note(a,"区间 [a,b) 与 [c,d) 相交，当且仅当 a < d 且 c < b")
    save(f,"04-interval-collision")

def function_sequence():
    f,a=canvas("Function Calling：请求与执行分属不同角色","模型选择调用；应用负责实际执行并回传",height=8.7)
    xs=[.15,.50,.85]
    for x,t in zip(xs,["应用 / 宿主","模型服务","本地工具"]):box(a,x-.12,.76,.24,.085,t);line(a,(x,.20),(x,.755),LINE,2,":")
    for y,src,dst,label in [(.68,0,1,"① 输入与工具定义"),(.57,1,0,"② function_call"),(.46,0,2,"③ 验参数、执行函数"),(.35,2,0,"④ 工具实际结果"),(.24,0,1,"⑤ function_call_output")]:
        arr(a,(xs[src],y),(xs[dst],y),GREEN if src==2 else BLUE)
        txt(a,(xs[src]+xs[dst])/2,y+.037,label,15.5)
    note(a,"继续生成与再次调用取决于返回结果；本章工具只有查询和校验")
    save(f,"05-function-sequence")

def function_correlation():
    f,a=canvas("工具结果必须回到原调用","call_id 是请求与结果的关联键；以下符号仅作教学示意")
    box(a,.06,.61,.32,.18,"工具请求","name + arguments + call_id")
    box(a,.62,.61,.32,.18,"工具输出","output + 同一 call_id",GREEN,GF)
    arr(a,(.39,.70),(.61,.70));txt(a,.50,.755,"实际执行",15.5)
    line(a,(.22,.60),(.22,.35),BLUE,2);line(a,(.78,.60),(.78,.35),GREEN,2)
    arr(a,(.22,.35),(.355,.35));arr(a,(.78,.35),(.645,.35),GREEN)
    box(a,.365,.25,.27,.19,"下一轮上下文","完整 response.output\n+ 每条工具结果")
    note(a,"一次响应可能有多个调用：逐条处理，不只取第一个")
    save(f,"05-function-correlation")

def function_failure():
    f,a=canvas("执行前检查；无进展时停止","本章 function_calling.py 的教学控制边界")
    chain(a,[("工具请求","名称与 JSON 参数"),("入口检查","注册表 / 类型 / 预算"),("允许执行","调用只读函数",GREEN,GF),("结果记录","返回数据或受控错误")],.61)
    arr(a,(.39,.60),(.39,.42),ORANGE)
    box(a,.15,.24,.48,.17,"执行前阻断","未知工具 / 非法参数 / 重复无进展",ORANGE,OF)
    box(a,.70,.24,.25,.17,"上限分开计","8 轮模型响应\n16 条工具请求")
    note(a,"同一函数和参数第 3 次出现：先拦截，记录 not_executed")
    save(f,"05-function-failure")

def mcp_boundaries():
    f,a=canvas("MCP 把宿主与能力服务分开","协议定义交互；宿主决定向模型暴露哪些能力")
    box(a,.035,.35,.29,.40,"","")
    txt(a,.18,.70,"Host：宿主",18,BLUE,True)
    txt(a,.18,.62,"WorkBuddy 或自建应用",15.5)
    box(a,.072,.40,.216,.13,"Client：客户端","连接一个 MCP 服务",GREEN,GF)
    box(a,.53,.43,.22,.20,"MCP Server","提供能力与合同")
    arr(a,(.335,.50),(.52,.50));txt(a,.42,.56,"协议通信",15.5)
    for y,t in [(.68,"tools：调用"),(.43,"resources：读取"),(.18,"prompts：模板")]:
        box(a,.80,y,.17,.13,t,color=GREEN,fill=GF);arr(a,(.76,.53),(.79,y+.065),GREEN)
    note(a,"本章只暴露 2 个只读工具；不同宿主对三类能力的支持不同")
    save(f,"06-mcp-boundaries")

def mcp_transports():
    f,a=canvas("本地 stdio 与远程 HTTP，连接位置不同","本机能访问，不代表云端模型服务能访问")
    box(a,.03,.53,.58,.27,"读者电脑","本地宿主  /  标准输入输出  /  Python MCP")
    txt(a,.32,.44,"stdio：command 与 args 启动本地进程",16,BLUE)
    box(a,.04,.19,.25,.16,"OpenAI 服务","远端请求发起方")
    box(a,.64,.19,.32,.16,"可访问的 MCP 服务","streamable HTTP",GREEN,GF)
    arr(a,(.30,.27),(.63,.27),GREEN);txt(a,.465,.34,"server_url",16,GREEN,True)
    box(a,.69,.56,.24,.18,"地址边界","E: 路径、127.0.0.1\n不自动对云端可达",ORANGE,OF)
    note(a,"MCP 隧道需另行具备可用条件；本节不假定已开通")
    save(f,"06-mcp-transports")

def mcp_approval():
    f,a=canvas("三道边界各管一件事","工具可见、数据传递审批、服务权限分别检查；此图不是协议时序")
    for x,title,body in [(.035,"允许哪些工具","allowed_tools"),(.355,"何时请求审批","require_approval"),(.675,"服务授权","身份与访问权限")]:
        box(a,x,.58,.29,.19,title,body)
    for x,t in [(.18,"缩小导入范围"),(.50,"判断信息是否可发送"),(.82,"校验调用者能做什么")]:txt(a,x,.43,t,16)
    box(a,.11,.20,.78,.14,"审批项必须按真实返回处理，再关联后续请求",color=GREEN,fill=GF)
    note(a,"把审批改成 never，不会修好认证、网络或工具合同错误")
    save(f,"06-mcp-approval")

def cli_contract():
    f,a=canvas("一次 CLI 执行的完整合同","命令文字只是入口；执行位置与结束状态同样重要")
    for y,t,b in [(.65,"程序 + argv","解释器、脚本、参数"),(.40,"cwd + 环境","工作目录、依赖、变量"),(.15,"时间边界","超时与终止处理")]:box(a,.035,y,.27,.17,t,b)
    box(a,.415,.40,.18,.20,"子进程","执行已指定程序",GREEN,GF)
    for y in [.735,.485,.235]:arr(a,(.315,y),(.405,.50))
    for y,t,b in [(.65,"stdout","结构化结果"),(.40,"stderr","诊断信息"),(.15,"退出码 / 超时","本例：0 通过 / 1 不合格 / 2 输入错")]:
        box(a,.68,y,.285,.17,t,b,GREEN,GF);arr(a,(.603,.50),(.67,y+.085),GREEN)
    save(f,"07-cli-contract")

def cli_execution():
    f,a=canvas("CLI、Shell 与执行环境分别选择","固定脚本与参数列表，使本地教学调用更容易核对")
    chain(a,[("应用校验","允许的输入文件"),("subprocess.run","argv + shell=False"),("本机 Python","校验脚本"),("返回记录","JSON / exit / timeout")],.60)
    box(a,.06,.23,.40,.19,"托管 Shell","远端容器执行；不会直接读 E: 盘",GREEN,GF)
    box(a,.54,.23,.40,.19,"本地 Shell 工具","应用执行并回传 shell_call 的结果",ORANGE,OF)
    txt(a,.5,.49,"原生工具的环境与本地受控函数，合同不同",16,MUTED)
    note(a,"shell=False 不是通用沙箱；“能执行命令”也不等于有公开 WorkBuddy CLI")
    save(f,"07-cli-execution")

def skill_package():
    f,a=canvas("Skill 逐层提供方法与必要材料","渐进读取减少无关内容；依赖必须跟随 Skill 一起交付")
    chain(a,[("先判断是否适用","name / description"),("再读操作方法","SKILL.md 的 SOP"),("按需展开","references / scripts / assets")],.59)
    box(a,.09,.23,.35,.17,"方法需要证据","输入、操作、验收与停止条件")
    box(a,.56,.23,.35,.17,"文件需要闭包","按自身路径发现资料和脚本",GREEN,GF)
    arr(a,(.5,.58),(.265,.41));arr(a,(.82,.58),(.735,.41),GREEN)
    note(a,"Skill 描述怎么做；连接、执行权限和预算仍由宿主负责")
    save(f,"08-skill-package")

def skill_hosting():
    f,a=canvas("原生 Skill 与应用自行装载：三条不同路线","选择路线后，按该环境的真实合同读取、执行和取回产物",height=8.2)
    for y,label,items in [(.64,"托管",[("上传 ZIP","POST /v1/skills"),("返回身份","skill_id + version"),("托管 Shell","skill_reference")]),(.39,"本地",[("本地 Skill 目录","真实存在的路径"),("本地执行环境","local 技能配置"),("应用执行","处理 shell_call")]),(.14,"应用 SOP",[("应用读文件","读取 SKILL.md"),("构造上下文","按需加入方法资料"),("自己的工具循环","调用受控脚本")])]:
        txt(a,.09,y+.08,label,18,BLUE,True);chain(a,items,y,.17,.20,.97)
    save(f,"08-skill-hosting")

def skill_proof():
    f,a=canvas("启用 Skill 之后，还需要哪些证据？","四个问题逐步检查，不把安装状态当成任务结果")
    chain(a,[("已安装","宿主可发现"),("已读取","任务采用了方法"),("已执行","实际脚本与输出"),("已通过","本次候选满足规则",GREEN,GF)],.57)
    for x,s in [(.145,"看到技能名称"),(.38,"确认引用的内容"),(.62,"核对真实命令"),(.855,"匹配文件与报告")]:txt(a,x,.43,s,16)
    box(a,.08,.20,.84,.14,"托管产物需按工具返回下载；聊天文字不会自动成为本机文件",color=ORANGE,fill=OF)
    save(f,"08-skill-proof")

def rag_pipeline():
    f,a=canvas("RAG：先把可追溯的证据找出来","小型练习无需先部署向量数据库")
    chain(a,[("授权与切片","读取范围 + chunk_id"),("检索排序","问题与片段相似度"),("选择依据","日期、身份与完整性"),("生成答案","引用片段并标未知")],.56)
    box(a,.05,.23,.40,.16,"精确条件先筛选","room_id=craft + 活动日期",GREEN,GF)
    box(a,.55,.23,.40,.16,"语义检索补充查找","手工体验 与 手作室说明")
    arr(a,(.25,.40),(.39,.55),GREEN);arr(a,(.75,.40),(.39,.55))
    note(a,"相似度是排序依据，不是答案正确的概率；检索不会修改模型权重")
    save(f,"09-rag-pipeline")

def rag_checks():
    f,a=canvas("检索、引用与最终排期，各自评分","资料不足应该被显式发现，而不是被流畅答案掩盖")
    chain(a,[("检索命中","需要的片段找到多少"),("引用支持","每条结论是否有依据"),("业务通过","最终排期是否满足规则")],.57)
    box(a,.055,.22,.27,.19,"Recall@k","前 k 命中的相关片段数\n÷ 预先标注的相关片段数")
    box(a,.365,.22,.27,.19,"支持关系","容量记录不证明\n10:30 的预约情况",ORANGE,OF)
    box(a,.675,.22,.27,.19,"完整性","人数、时长、房间、\n开放时间与预约",GREEN,GF)
    save(f,"09-rag-checks")

def loop_stop():
    f,a=canvas("Agent 什么时候应当停下来？","循环依赖真实工具结果；预算限制由应用执行")
    box(a,.06,.43,.24,.20,"本轮结果","观察、工具与候选")
    for y,t,b,c,fill in [(.66,"成功交付","候选通过 + 文件齐全",GREEN,GF),(.40,"继续修正","有新信息且仍有预算",BLUE,BF),(.14,"有原因地停止","无进展 / 上限 / 无有效候选",ORANGE,OF)]:
        box(a,.56,y,.38,.17,t,b,c,fill);arr(a,(.31,.53),(.55,y+.085),c)
    line(a,(.95,.485),(.975,.485),BLUE,2)
    line(a,(.975,.485),(.975,.835),BLUE,2)
    line(a,(.975,.835),(.18,.835),BLUE,2)
    arr(a,(.18,.835),(.18,.64),BLUE)
    txt(a,.25,.24,"模型轮数 ≠ 工具请求数\n单次超时 ≠ 任务总预算",16,MUTED)
    save(f,"10-loop-stop")

def memory_layers():
    f,a=canvas("三种“记住”，可信范围不同","本章示例没有实现跨进程断点续跑")
    for y,t,b,c,fill in [(.65,"会话上下文","实际传入的历史与工具结果",BLUE,BF),(.40,"应用任务状态","候选、输入摘要、校验与预算",GREEN,GF),(.15,"长期记忆","可跨任务读取的偏好与记录",BLUE,BF)]:
        box(a,.055,y,.39,.17,t,b,c,fill)
    box(a,.65,.37,.30,.23,"恢复前重新核对","事实是否过期\n输入是否变化",ORANGE,OF)
    for y in [.735,.485,.235]:arr(a,(.455,y),(.64,.485),GREEN)
    txt(a,.80,.74,"偏好：中文报告",17,GREEN,True)
    txt(a,.80,.20,"预约：必须带日期与来源",16,ORANGE,True)
    save(f,"10-memory-layers")

def multimodal_evidence():
    f,a=canvas("不同输入，只能证明各自范围内的事实","感知内容与当前业务事实分开核对")
    for y,t,b,detail in [(.65,"平面示意图","可见标注与相对位置","容量看 rooms.csv；预约看 availability.json"),(.40,"负责人录音","先转写，再核对关键字","人数、时间、否定词必须回听确认"),(.15,"网页截图","本次页面可见状态","显示“空闲”不证明后台预约已提交")]:
        box(a,.045,y,.28,.17,t,b);arr(a,(.335,y+.085),(.435,y+.085));box(a,.445,y,.51,.17,detail,color=GREEN,fill=GF)
    save(f,"11-multimodal-evidence")

def computer_loop():
    f,a=canvas("计算机交互：每次行动后，重新观察","动作坐标只是请求，宿主执行后才会得到新状态")
    chain(a,[("观察页面","真实截图"),("模型动作请求","点击 / 输入等"),("宿主执行","范围、坐标与权限"),("新截图","回传并核对状态")],.58)
    arr(a,(.86,.57),(.145,.57),GREEN,-.42)
    txt(a,.5,.40,"computer_call_output 关联真实 call_id",17,GREEN,True)
    box(a,.08,.14,.84,.12,"页面文字属于外部数据；它不能自行扩大操作授权",color=ORANGE,fill=OF)
    save(f,"11-computer-loop")

def multi_agent_forkjoin():
    f,a=canvas("两个独立审阅并行，汇总等待两者完成","本章 multi_agent.py：固定三次模型调用，不动态扩张团队")
    box(a,.03,.40,.23,.22,"固定输入","同一候选 + 材料\n+ 确定性校验报告")
    box(a,.385,.64,.27,.17,"资料核对员","约束、来源与缺口")
    box(a,.385,.23,.27,.17,"表达审查员","表述与证据是否相符",GREEN,GF)
    box(a,.78,.40,.19,.22,"汇总员","team-report.md",GREEN,GF)
    for y,c in [(.725,BLUE),(.315,GREEN)]:arr(a,(.27,.51),(.375,y),c);arr(a,(.665,y),(.77,.51),c)
    txt(a,.52,.515,"各自上下文、各自输出文件",15.5,MUTED)
    note(a,"任何审阅失败则中止本次交付；角色不能并写同一候选文件")
    save(f,"12-multi-agent-forkjoin")

def handoff_contract():
    f,a=canvas("交接时，传证据和边界","摘要会丢失范围；多数投票也不能改变原始事实")
    box(a,.04,.54,.28,.23,"原始事实","手作预约仅\n10:00—10:30")
    box(a,.38,.54,.24,.23,"交接包","结论 + 来源 + 时段\n未解决项 + 证据",GREEN,GF)
    box(a,.68,.54,.28,.23,"接收角色","回查来源后汇总")
    arr(a,(.33,.655),(.37,.655));arr(a,(.63,.655),(.67,.655),GREEN)
    box(a,.07,.20,.39,.18,"保留三种身份","事实 / 程序判定 / 角色建议",GREEN,GF)
    box(a,.54,.20,.39,.18,"避免范围被扩大","“上午不可用”不等于原预约",ORANGE,OF)
    save(f,"12-handoff-contract")

def eval_levels():
    f,a=canvas("测试了哪一层，结论就到哪一层","三个实验对象不能相互冒充")
    rows=[(.64,"人工参考排期","本地校验器","程序规则是否正确"),(.39,"模拟 API 响应","工具循环","关联、错误与停止是否正确"),(.14,"真实模型 + 实际工具","端到端任务","是否生成有效的交付物")]
    for y,t,b,result in rows:chain(a,[(t,"受控输入"),(b,"实际被测对象",GREEN,GF),(result,"证据范围",GREEN,GF)],y,.17)
    save(f,"13-eval-levels")

def diagnosis_chain():
    f,a=canvas("从错误产物倒查，定位责任层","示例：最终手作排期为 10:00—11:00，撞上已有预约")
    chain(a,[("交付控制","明明报错却保存通过？"),("工具数据","是否加载正确预约文件？"),("模型上下文","预约已返回却未采用？"),("请求链","调用是否真的发出？")],.57)
    for x in [.145,.38,.62,.855]:arr(a,(x,.56),(x,.36),GREEN)
    box(a,.06,.20,.88,.15,"对照本次文件、工具参数、真实输出与响应记录",color=GREEN,fill=GF)
    note(a,"模型总结只能提供线索；工具记录与文件才证明执行发生过什么")
    save(f,"13-diagnosis-chain")

def cost_units():
    f,a=canvas("费用与耗时：先把计数单位分开","本图为记账关系，无真实价格、吞吐或用量数据")
    box(a,.05,.58,.25,.20,"逻辑调用","应用发起的请求")
    box(a,.39,.58,.25,.20,"物理尝试","网络请求 + 有限重试")
    box(a,.73,.58,.22,.20,"实际用量","usage 与账单",GREEN,GF)
    arr(a,(.31,.68),(.38,.68));arr(a,(.65,.68),(.72,.68),GREEN)
    txt(a,.345,.76,"可能 1 对多",14,MUTED)
    box(a,.055,.22,.40,.20,"时间分别记","模型执行 / 本地命令 / 用户等待")
    box(a,.545,.22,.40,.20,"费用分别记","API 账单 / WorkBuddy 积分",GREEN,GF)
    note(a,"Token 子类按服务口径拆分；已含在总量内的部分不再重复相加")
    save(f,"13-cost-units")

def technology_choice():
    f,a=canvas("按任务缺口，选择最小技术组合","技术可以组合；每加一种都要说明解决了什么问题",height=8.3)
    box(a,.04,.40,.23,.23,"先找缺口","输入？事实？执行？\n方法？验证？")
    for y,t,b,c,fill in [(.68,"要求与格式","Prompt / 上下文 / Schema",BLUE,BF),(.48,"资料与外部事实","检索 / Function Calling / MCP",GREEN,GF),(.28,"可复用执行","CLI / Skill / Agent 循环",BLUE,BF),(.08,"额外感知与分工","多模态 / 计算机工具 / 多 Agent",GREEN,GF)]:
        box(a,.51,y,.44,.16,t,b,c,fill);arr(a,(.28,.515),(.50,y+.08),c)
    save(f,"14-technology-choice")

def integrated_delivery():
    f,a=canvas("交付筹备文件，再进入仿真世界","第二章交付物还没有 Run 身份、StepResult 或回放事实")
    for x,t,b in [(.045,"需求与来源","事实、规则、缺失项"),(.365,"排期与校验","schedule + 独立报告"),(.685,"说明与记录","plan + 请求与工具记录")]:
        box(a,x,.60,.27,.18,t,b,GREEN,GF)
    line(a,(.18,.51),(.82,.51),GREEN,2)
    for x in [.18,.50,.82]:line(a,(x,.59),(x,.51),GREEN,2)
    arr(a,(.50,.50),(.50,.37),GREEN)
    box(a,.16,.19,.68,.18,"第三章重新配置与执行","地图、角色、Skill、模型用途与实验装配")
    note(a,"本章校验通过只覆盖已编码约束，不证明真实预订或仿真已运行")
    save(f,"14-integrated-delivery")

RENDERERS=[request_lifecycle,routes_proof,task_contract,prompt_comparison,context_packing,conversation_state,fact_refresh,validation_gates,interval_collision,function_sequence,function_correlation,function_failure,mcp_boundaries,mcp_transports,mcp_approval,cli_contract,cli_execution,skill_package,skill_hosting,skill_proof,rag_pipeline,rag_checks,loop_stop,memory_layers,multimodal_evidence,computer_loop,multi_agent_forkjoin,handoff_contract,eval_levels,diagnosis_chain,cost_units,technology_choice,integrated_delivery]
if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--only",nargs="*",help="Renderer function names; omit to render all")
    args=parser.parse_args()
    for render in RENDERERS:
        if not args.only or render.__name__ in args.only:render()
    print(f"Rendered {len(FIGURES)} figures (PNG + SVG):")
    print("\n".join(FIGURES))
