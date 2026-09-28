"""Original chapter-3 explanatory diagrams; no runtime or model invocation."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT=Path(__file__).resolve().parent
FONT=None
for name in ('Microsoft YaHei','SimHei','Noto Sans CJK SC'):
    try:
        FONT=font_manager.FontProperties(fname=font_manager.findfont(name,fallback_to_default=False));break
    except ValueError:pass
if FONT is None:raise RuntimeError('Install a Chinese font for rendering.')
plt.rcParams['svg.fonttype']='path'
INK='#20344b';BLUE='#276fbf';GRAY='#607487'


def label(ax,x,y,text,size=13,color=INK):
    ax.text(x,y,text,ha='center',va='center',fontsize=size,fontproperties=FONT,color=color,linespacing=1.6)


def box(ax,x,y,w,h,title,body,fill='#edf4fc'):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.006,rounding_size=0.018',fc=fill,ec='#b2c8dd',lw=1.3))
    label(ax,x+w/2,y+h-.06,title,16)
    label(ax,x+w/2,y+h/2-.04,body,12)


def arrow(ax,x1,y1,x2,y2):
    ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle='-|>',mutation_scale=18,lw=1.6,color=BLUE))


def canvas(w=15,h=5.5):
    fig,ax=plt.subplots(figsize=(w,h),dpi=160)
    ax.set(xlim=(0,1),ylim=(0,1));ax.axis('off')
    fig.subplots_adjust(left=.015,right=.985,bottom=.02,top=.98)
    return fig,ax


def save(fig,name):
    for suffix in ('png','svg'):fig.savefig(ROOT/f'{name}.{suffix}',dpi=160,facecolor='white')
    plt.close(fig)


def packages():
    fig,ax=canvas()
    label(ax,.5,.94,'从作者内容到已提交事实',21)
    specs=[(.02,'公共资源','直接编辑作者内容\n地图 · 人物 · Skill · 模型'),(.225,'实验草稿','完整复制资源与依赖\n只编辑包内副本'),(.43,'封存实验','DRAFT → SEALED\n调整时建立独立副本'),(.635,'Run','完整内嵌实验\nAttempt · 帧 · 检查点'),(.84,'Replay','只读已提交事实\n重建状态与画面')]
    for x,t,b in specs:box(ax,x,.44,.14,.34,t,b,fill='#eaf5ef' if x>.6 else '#edf4fc')
    for x in (.172,.377,.582,.787):arrow(ax,x,.61,x+.038,.61)
    label(ax,.315,.29,'内容不随公共资源后续修改自动变化',13,BLUE)
    label(ax,.795,.29,'回放不再次执行模型或 Skill',13,BLUE)
    label(ax,.5,.12,'目录是工作形态；config ZIP / .gaexp / .garun 是相应用途的交换形态。',13,GRAY)
    save(fig,'package-flow')


def feedback():
    fig,ax=canvas(14,6.2)
    label(ax,.5,.94,'同一步提交请求与对象结果，下一轮投递反馈',20)
    box(ax,.03,.50,.21,.28,'Step k：人物阶段','INTERACT 提出真实请求\n内核记录 request_id')
    box(ax,.29,.50,.21,.28,'Step k：对象阶段','读取真实待处理请求\n选择状态变化与回复')
    box(ax,.55,.50,.18,.28,'整步提交','StepResult 与提交边界\n保留对象状态和回复','#eaf5ef')
    box(ax,.79,.50,.18,.28,'Step k+1','目标人物收到反馈\n依据事实更新记忆','#eaf5ef')
    for x in (.252,.512,.742):arrow(ax,x,.64,x+.025,.64)
    label(ax,.37,.32,'请求已接受 ≠ 状态已提交\n对象已回复 ≠ 所有人都已获知',14,BLUE)
    label(ax,.81,.32,'仅向真实目标投递\n恢复后仍需核对不重不漏',14,BLUE)
    label(ax,.5,.12,'概念图：具体先后依据调度与提交事实核对，不预设故事发生在哪一个 Step。',12,GRAY)
    save(fig,'step-and-feedback')


if __name__=='__main__':
    packages();feedback();print('Wrote two chapter diagrams as PNG and SVG.')
