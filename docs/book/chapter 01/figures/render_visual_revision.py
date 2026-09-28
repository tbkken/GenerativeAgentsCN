"""Section 1.2–1.6 explanatory figures; source dates remain unchanged."""
from pathlib import Path
import sys
import math
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'visuals'))
from drawing import *
from matplotlib.patches import Circle


def application():
    compare(ROOT,'s12-model-and-app','同一模型，应用给它的条件可以不同','只给任务','补齐应用能力',[
        ('可用材料','“安排开放日”','容量表＋预约记录'),
        ('取得事实','依赖已给输入','真实查询工具'),
        ('发现冲突','模型自行判断','独立约束校验'),
        ('交付依据','一份候选文本','文本＋执行与校验记录')],
        '评价对象要写清：裸模型，还是带资料、工具与控制的完整应用。')
    timeline(ROOT,'s12-time-check','同一份候选，沿时间轴查冲突',[
        ('已有预约',[(60,90,'10:00—10:30',2)]),
        ('冲突候选',[(60,90,'同一时段被占用',0)]),
        ('可行候选',[(90,120,'10:30—11:00',1)])],
        [(0,'09:00'),(60,'10:00'),(90,'10:30'),(120,'11:00'),(180,'12:00')],
        '10人 ≤ 容量12人；活动30分钟；区间首尾相接不算重叠。')


def measurement():
    flow(ROOT,'s13-evaluation-chain','先选对任务，再计算指标',[
        ('目标','减少报名遗漏'),('样本','固定材料与答案'),('判据','人员—活动匹配'),('指标','计数与分母'),('边界','能说明什么')],
        '任务选错时，计算再精确，也回答不了原来的问题。')
    fig,ax=new('同一份抽取结果，两个分母','人工教学计数：真实12项，输出10项；不是真实模型测评。')
    box(ax,.06,.39,.38,.37,'真实报名：12项','',1)
    box(ax,.56,.39,.38,.37,'模型输出：10项','',0)
    for k in range(12):
        x=.115+(k%6)*.052;y=.575-(k//6)*.085
        ax.add_patch(Circle((x,y),.018,fc=GREEN if k<8 else ORANGE))
    for k in range(10):
        x=.62+(k%5)*.06;y=.575-(k//5)*.085
        ax.add_patch(Circle((x,y),.018,fc=GREEN if k<8 else BLUE))
    text(ax,.25,.435,'8项找到  ＋  4项遗漏',14)
    text(ax,.75,.435,'8项正确  ＋  2项误报',14)
    arrow(ax,(.44,.565),(.56,.565),GREEN)
    text(ax,.25,.285,'Recall = 8 / 12 ≈ 66.67%',17,GREEN)
    text(ax,.75,.285,'Precision = 8 / 10 = 80%',17,BLUE)
    text(ax,.5,.14,'F1 = 2×8 / (2×8 + 2 + 4) ≈ 72.73%',19)
    text(ax,.5,.065,'绿色为正确项，橙色为遗漏，蓝色为误报；错误代价仍需单独判断。',12,MUTED)
    save(fig,ROOT,'s13-extraction-counts')
    fig,ax=plt.subplots(figsize=(13.5,6.8),dpi=160)
    ks=list(range(1,7));p=.8
    ax.plot(ks,[100*(1-(1-p)**k) for k in ks],'-o',lw=2.5,color=BLUE,label='至少一次成功：1 − (1 − p)^k')
    ax.plot(ks,[100*p**k for k in ks],'-o',lw=2.5,color=GREEN,label='每次都成功：p^k')
    ax.set(xlim=(.8,6.2),ylim=(0,105),xticks=ks)
    ax.set_title('机会增加，对两种成功标准的影响相反',fontproperties=FONT,fontsize=22,pad=35)
    ax.set_xlabel('独立重复次数 k',fontproperties=FONT,fontsize=15)
    ax.set_ylabel('假设条件下的概率（%）',fontproperties=FONT,fontsize=15)
    ax.legend(prop=FONT,loc='lower left',frameon=False)
    ax.grid(alpha=.18);ax.spines[['top','right']].set_visible(False)
    ax.annotate('96%',(2,96),(2.3,90),fontsize=16,color=BLUE)
    ax.annotate('64%',(2,64),(2.3,66),fontsize=16,color=GREEN)
    fig.subplots_adjust(bottom=.23,top=.8,left=.09,right=.95)
    fig.text(.5,.93,'人工概率演算：某一道固定任务 p=0.8；初态与配置相同，各次独立。',ha='center',fontproperties=FONT,fontsize=13)
    fig.text(.5,.06,'不把题集平均准确率直接代入；带错误反馈的自适应重试另作评价。',ha='center',fontproperties=FONT,fontsize=13,color=MUTED)
    save(fig,ROOT,'s13-repeat-success')
    fig,ax=plt.subplots(figsize=(13.5,5.9),dpi=160)
    def wilson(s,n):
        z=1.95996398454;phat=s/n;den=1+z*z/n;c=(phat+z*z/(2*n))/den
        d=z*math.sqrt(phat*(1-phat)/n+z*z/(4*n*n))/den
        return c-d,c+d
    for y,(s,n) in enumerate([(8,10),(80,100)]):
        lo,hi=wilson(s,n)
        ax.plot([lo*100,hi*100],[y,y],lw=6,color=[BLUE,GREEN][y],solid_capstyle='round')
        ax.scatter([80],[y],s=140,color=INK,zorder=3)
        ax.text((lo+hi)*50,y+.12,f'{lo*100:.1f}%—{hi*100:.1f}%',ha='center',fontsize=16)
    ax.set(yticks=[0,1],yticklabels=['8 / 10','80 / 100'],xlim=(40,100),ylim=(-.5,1.55))
    ax.set_title('同为80%，样本量改变不确定范围',fontproperties=FONT,fontsize=22,pad=30)
    ax.set_xlabel('95% Wilson区间（%）',fontproperties=FONT,fontsize=15)
    ax.grid(axis='x',alpha=.2);ax.spines[['top','right','left']].set_visible(False)
    fig.subplots_adjust(left=.13,right=.96,top=.8,bottom=.26)
    fig.text(.5,.09,'人工计数；需满足独立、同一目标总体等抽样前提。',ha='center',fontproperties=FONT,fontsize=13,color=MUTED)
    fig.text(.5,.03,'区间不是下一题答对的概率；材料高度相关时不能直接沿用该解释。',ha='center',fontproperties=FONT,fontsize=13,color=MUTED)
    save(fig,ROOT,'s13-uncertainty')
    flow(ROOT,'s13-evidence-check','检索、引用、答案，分别检查',[
        ('找全','所需证据命中率'),('用对','引用支持陈述'),('答对','回答符合问题'),('交付','全部必要条件')],
        '链接存在，不等于支持陈述；检索命中，不等于最终任务成功。')


def capabilities():
    compare(ROOT,'s14-instruction-levels','逐条通过与整份通过，看的是不同单位','通知甲','通知乙',[
        ('约束1','通过','通过'),('约束2','通过','通过'),('约束3','通过','通过'),
        ('约束4','通过','未通过'),('完整交付','全部通过','尚未全部通过')],
        '人工演算：逐条7/8=87.5%；整份1/2=50%。形式检查之外还要核对事实。')
    flow(ROOT,'s14-code-evidence','代码能力：从局部功能，到完整工程',[
        ('函数题','接口与说明'),('执行测试','边界与行为'),('仓库问题','定位与修改'),('回归结果','规定测试通过')],
        'HumanEval与SWE-bench回答不同问题；工具、框架和预算也是条件。')
    sequence(ROOT,'s14-tool-state','工具调用与任务完成之间，还有执行证据',[
        '用户','模型','宿主／工具','环境'],[
        (0,1,'提出预约目标'),(1,2,'提出函数与参数'),(2,3,'校验后实际执行'),
        (3,2,'返回真实状态'),(2,1,'带回结果或错误'),(1,0,'依据结果回答')],
        '参数正确、工具成功、目标状态满足、过程守规，需要分别核查。')
    fig,ax=new('视觉任务：位置、文档、图表不能合成一种“识图分”','作者自编小图，用于区分任务；不是基准原题或实测。',height=7.2)
    for x,title in [(.03,'场地图：关系'),(.36,'文档：内容与版面'),(.69,'图表：编码与计算')]:
        box(ax,x,.30,.28,.46,title,'')
    for x,y,w,h,label in [(.055,.39,.10,.22,'阅读区'),(.18,.39,.10,.22,'手作区')]:
        ax.add_patch(Rectangle((x,y),w,h,ec=BLUE,fc='white',lw=2));text(ax,x+w/2,y+h/2,label,12)
    arrow(ax,(.16,.355),(.16,.43),GREEN);text(ax,.16,.325,'入口在哪里？',13)
    for y,label in [(.59,'姓名：周澈'),(.52,'活动：阅读分享'),(.45,'人数：10人')]:text(ax,.5,y,label,14)
    ax.plot([.4,.62],[.41,.41],color='#b8c9d8')
    text(ax,.5,.345,'字段属于哪一行？',13)
    for x,height,label in [(.745,.12,'上午'),(.865,.19,'下午')]:
        ax.add_patch(Rectangle((x,.415),.052,height,fc=BLUE));text(ax,x+.026,.383,label,12)
    text(ax,.84,.345,'比较前先看单位与刻度',12)
    text(ax,.5,.185,'MMMU-Pro：图文求解   ·   DocVQA：文档问答   ·   ChartQA：图表问答',15,BLUE)
    text(ax,.5,.08,'视觉成绩不能证明系统碰撞、语义地址或实际导航已经通过。',14,ORANGE)
    save(fig,ROOT,'s14-visual-tasks')
    timeline(ROOT,'s14-temporal-input','视频多一个时间维度：顺序不能靠单帧证明',[
        ('过程甲',[(0,1,'搬椅子',0),(2,3,'贴指示牌',1)]),
        ('过程乙',[(0,1,'贴指示牌',1),(2,3,'搬椅子',0)])],[(0,'先'),(1,'过程'),(2,'后'),(3,'结束')],
        '还须记录抽帧、字幕、音轨和时长；从字幕得到的答案不全是视觉能力。',
        '作者事件顺序示意；横轴仅示顺序，无实际秒数。')


def snapshots():
    cards(ROOT,'s15-score-card','一个分数，需要带着这些条件',[
        ('模型身份','完整标识／显示名\n记录可得精度'),('题目范围','基准版本与子集\n样本量与修订'),
        ('运行条件','工具、预算、配置\n采样与框架'),('来源与时间','厂商自报／维护方\n日期、未披露项')],
        '先判断条件是否可比，再比较分数。')
    fig,ax=plt.subplots(figsize=(14,7),dpi=160)
    names=['Fable 5.1','Fable 5','Opus 5'];partial=[77.9,72.9,75.4];strict=[41.7,36.1,39.6]
    xs=[0,1,2]
    ax.bar([x-.17 for x in xs],partial,width=.32,color=BLUE,label='Partial score')
    ax.bar([x+.17 for x in xs],strict,width=.32,color=GREEN,label='Strict pass rate')
    for x,a,b in zip(xs,partial,strict):
        ax.text(x-.17,a+1,f'{a}%',ha='center',fontsize=15)
        ax.text(x+.17,b+1,f'{b}%',ha='center',fontsize=15)
    ax.set(xticks=xs,xticklabels=names,ylim=(0,100));ax.set_ylabel('原报告数值（%）',fontproperties=FONT,fontsize=14)
    ax.set_title('完成了一部分，不等于全部完成',fontproperties=FONT,fontsize=22,pad=42)
    ax.legend(frameon=False,fontsize=13,loc='upper right');ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.subplots_adjust(left=.08,right=.96,top=.77,bottom=.26)
    fig.text(.5,.91,'OSWorld 2.0：既有正文的2026-09-25资料快照；厂商自报，未由本书复测。',ha='center',fontproperties=FONT,fontsize=13)
    fig.text(.5,.115,'108项任务；同一修订任务版；5次独立运行平均；1080p；最多500动作。',ha='center',fontproperties=FONT,fontsize=12)
    fig.text(.5,.058,'部分得分与严格通过率分母含义不同；图中不补造置信区间，不推断普遍显著差异。',ha='center',fontproperties=FONT,fontsize=12,color=MUTED)
    save(fig,ROOT,'s15-partial-strict')
    compare(ROOT,'s15-comparison-boundary','这些变化会改变分数的含义','表面相似','还要核对',[
        ('同一名称','都是同一个榜单','任务版本／修订'),('同一百分比','都写80%','题目、测试还是任务'),
        ('同一档位','都叫high','不等于同计算预算'),('同样用了工具','都有检索或执行','工具集合与使用上限')],
        '条件未知就写未披露，不把不同设置排成一条进步曲线。')


def selection():
    fig,ax=new('小评价集：六类任务，每类四项','作者建议的入门规模；24项不支撑广泛统计结论。')
    names=['材料理解','事实与缺失','结构与抽取','时间与数量','工具结果','跨轮信息修正']
    for i,name in enumerate(names):
        y=.72-i*.095;text(ax,.29,y,name,15,ha='right')
        for j in range(4):
            box(ax,.37+j*.12,y-.026,.08,.054,color=i%3);text(ax,.41+j*.12,y,str(j+1),13)
    text(ax,.5,.07,'8项练习可调提示 → 冻结24项评价；评价集不能边看答案边改。',15,ORANGE)
    save(fig,ROOT,'s16-task-grid')
    compare(ROOT,'s16-choice-tradeoff','同看成功、错误类型、时间与成本','候选甲','候选乙',[
        ('成功任务','20 / 24','22 / 24'),('无依据完成声明','2项','0项'),
        ('全部尝试费用','1.20元','3.30元'),('每项成功费用','0.06元','0.15元'),('中位耗时','8秒','15秒')],
        '人工数据。先确定不可违反的条件，再比较成本；零次观察不证明永不出错。')
    flow(ROOT,'s16-selection-process','选择说明要能让别人复查',[
        ('任务','输入与边界'),('条件','关键验收项'),('比较','固定设置'),('证据','失败与成本'),('复查','变化后重测')],
        '活动助手与仿真角色的任务不同，需要分别评价。')

if __name__=='__main__':
    application();measurement();capabilities();snapshots();selection()
