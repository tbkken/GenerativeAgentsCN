"""Shared drawing primitives for original textbook diagrams (no runtime data)."""
from pathlib import Path
import textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle

INK, BLUE, GREEN, ORANGE, MUTED = '#20344b', '#276fbf', '#26816b', '#c97624', '#617386'
PALE = ['#edf4fc', '#eaf5ef', '#fff3e8', '#f3eef9']
FONT = None
for family in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC'):
    try:
        FONT=font_manager.FontProperties(fname=font_manager.findfont(family,fallback_to_default=False)); break
    except ValueError: pass
if FONT is None: raise RuntimeError('A Chinese font is required.')
plt.rcParams['svg.fonttype']='path'

def text(ax,x,y,s,size=16,color=INK,ha='center',weight='normal'):
    return ax.text(x,y,s,ha=ha,va='center',fontproperties=FONT,fontsize=size,color=color,
                   linespacing=1.45,fontweight=weight)

def box(ax,x,y,w,h,title='',body='',color=0):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.006,rounding_size=0.012',
                               facecolor=PALE[color%4],edgecolor='#b8c9d8',lw=1.2))
    if title: text(ax,x+w/2,y+h*.73,title,17,[BLUE,GREEN,ORANGE,INK][color%4],weight='bold')
    if body: text(ax,x+w/2,y+h*.32,body,14)

def arrow(ax,p,q,color=BLUE,curve=0,style='-|>'):
    ax.add_patch(FancyArrowPatch(p,q,arrowstyle=style,mutation_scale=18,lw=1.7,color=color,
                               connectionstyle=f'arc3,rad={curve}'))

def new(title,subtitle='作者教学示意；不表示真实运行结果。',height=6.8):
    fig,ax=plt.subplots(figsize=(14,height),dpi=160)
    fig.subplots_adjust(left=.025,right=.975,bottom=.03,top=.98)
    ax.set(xlim=(0,1),ylim=(0,1));ax.axis('off')
    text(ax,.5,.942,title,23,weight='bold')
    text(ax,.5,.865,subtitle,12,MUTED)
    return fig,ax

def save(fig,root,name):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    for ext in ('png','svg'): fig.savefig(root/f'{name}.{ext}',dpi=160,facecolor='white')
    plt.close(fig)

def flow(root,name,title,steps,note='',subtitle='作者教学示意；箭头表示工作关系，不是系统固定流水线。'):
    fig,ax=new(title,subtitle)
    n=len(steps);gap=.03;w=(.94-gap*(n-1))/n
    for i,(heading,body) in enumerate(steps):
        x=.03+i*(w+gap);box(ax,x,.43,w,.29,heading,body,i%3)
        if i<n-1: arrow(ax,(x+w+.007,.575),(x+w+gap-.007,.575))
    if note: box(ax,.12,.11,.76,.16,'读图结论',note,2)
    save(fig,root,name)

def compare(root,name,title,left,right,rows,note='',subtitle='作者教学对照；不是两组实测结果。'):
    fig,ax=new(title,subtitle,height=max(6.5,len(rows)*1.05+2.5))
    text(ax,.16,.755,'比较项',14,MUTED);text(ax,.46,.755,left,18,BLUE,weight='bold');text(ax,.79,.755,right,18,GREEN,weight='bold')
    step=.56/max(1,len(rows))
    for i,(label,a,b) in enumerate(rows):
        y=.69-i*step
        ax.add_patch(Rectangle((.03,y-step*.72),.94,step*.9,fc=PALE[i%2],ec='white',lw=2))
        text(ax,.16,y-step*.25,label,14)
        text(ax,.46,y-step*.25,a,14);text(ax,.79,y-step*.25,b,14)
    if note:text(ax,.5,.06,note,13,ORANGE)
    save(fig,root,name)

def sequence(root,name,title,actors,messages,note='',subtitle='消息顺序示意；不是实际Trace或系统截图。'):
    fig,ax=new(title,subtitle,height=7.2)
    xs=[.09+i*.82/(len(actors)-1) for i in range(len(actors))]
    for x,actor in zip(xs,actors):
        box(ax,x-.07,.725,.14,.07);text(ax,x,.76,actor,15,BLUE)
        ax.plot([x,x],[.17,.72],ls='--',lw=1,color='#b8c9d8')
    for i,(a,b,label) in enumerate(messages):
        y=.655-i*.44/max(1,len(messages)-1)
        arrow(ax,(xs[a],y),(xs[b],y),GREEN if a>b else BLUE)
        text(ax,(xs[a]+xs[b])/2,y+.025,label,12)
    if note:text(ax,.5,.075,note,13,ORANGE)
    save(fig,root,name)

def cards(root,name,title,items,note='',subtitle='概念分解示意；不表示各项已经验收。'):
    fig,ax=new(title,subtitle,height=7.2)
    cols=2;rows=(len(items)+1)//2;h=.60/rows-.025
    for i,(heading,body) in enumerate(items):
        col=i%cols;row=i//cols
        box(ax,.035+col*.49,.74-(row+1)*(h+.025),.445,h,heading,body,i%3)
    if note:text(ax,.5,.055,note,13,ORANGE)
    save(fig,root,name)

def timeline(root,name,title,rows,ticks,note='',subtitle='作者给定的教学时间安排；不是实际运行日志。'):
    fig,ax=new(title,subtitle,height=6.5)
    x0,x1=.22,.94;maximum=max(v for v,label in ticks)
    for v,label in ticks:
        x=x0+v/maximum*(x1-x0);text(ax,x,.745,label,12,MUTED)
        ax.plot([x,x],[.25,.705],ls='--',lw=.8,color='#cbd6df')
    for i,(label,segments) in enumerate(rows):
        y=.64-i*.31/max(1,len(rows)-1);text(ax,.19,y,label,13,ha='right')
        for start,end,caption,color in segments:
            a=x0+start/maximum*(x1-x0);w=(end-start)/maximum*(x1-x0)
            ax.add_patch(Rectangle((a,y-.025),w,.052,facecolor=[BLUE,GREEN,ORANGE][color],alpha=.82))
            text(ax,a+w/2,y-.067,caption,12)
    if note:text(ax,.5,.10,note,14,ORANGE)
    save(fig,root,name)
