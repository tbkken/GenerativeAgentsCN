"""Draw editable vector authoring assets; never accesses Studio or Run data.

Requires matplotlib and a Chinese font. PNGs are direct renders of original
vector artwork, not edits of historical case images or screenshots.
"""
from pathlib import Path
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, Circle, Ellipse, FancyBboxPatch

ROOT = Path(__file__).resolve().parent
DESIGN = json.loads((ROOT / "map" / "design.json").read_text("utf-8"))
FONT = None
for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
    try:
        FONT = font_manager.FontProperties(fname=font_manager.findfont(family, fallback_to_default=False))
        break
    except ValueError:
        pass
if FONT is None:
    raise RuntimeError("A Chinese font is required for chapter artwork.")
plt.rcParams["svg.fonttype"] = "path"


def canvas(width, height, *, units=None, transparent=False):
    fig = plt.figure(figsize=(width/100, height/100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    w,h = units or (width,height)
    ax.set(xlim=(0,w), ylim=(h,0), aspect="equal")
    ax.axis("off")
    if transparent:
        fig.patch.set_alpha(0);ax.patch.set_alpha(0)
    return fig,ax


def save(fig, path, transparent=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(path.with_suffix('.'+ext), dpi=100, transparent=transparent)
    plt.close(fig)


def text(ax,x,y,value,size=12,color="#304456"):
    ax.text(x,y,value,fontproperties=FONT,fontsize=size,ha="center",va="center",color=color)


def blocked_cells():
    w,h=DESIGN["width"],DESIGN["height"]
    cells={(x,y) for x in (0,w-1) for y in range(h)}
    cells|={(x,y) for y in DESIGN["wall_rows"] for x in range(w)}
    cells|={(x,y) for x in (11,21) for y in range(1,13)}
    cells-={(x,DESIGN["door_row"]) for x in DESIGN["door_columns"]}
    return cells


def map_background():
    fig,ax=canvas(1024,768,units=(32,24))
    ax.add_patch(Rectangle((0,0),32,24,fc="#e6e4de",ec="none"))
    for arena in DESIGN["arenas"]:
        x,y,w,h=arena["rect"]
        ax.add_patch(Rectangle((x,y),w,h,fc=arena["color"],ec="none"))
        if arena['key']!='hall':text(ax,x+w/2,y+1.1,arena["name"],16)
    for x in range(33):ax.plot([x,x],[0,24],color="#849599",alpha=.1,lw=.4)
    for y in range(25):ax.plot([0,32],[y,y],color="#849599",alpha=.1,lw=.4)
    for x,y in sorted(blocked_cells()):
        ax.add_patch(Rectangle((x,y),1,1,fc="#647783",ec="#5d6e79",lw=.4))
    for obj in DESIGN["objects"]:
        if obj["key"]=="board":continue
        x,y,w,h=obj['rect']
        ax.add_patch(FancyBboxPatch((x+.08,y+.08),w-.16,h-.16,boxstyle="round,pad=0,rounding_size=.18",fc="#b9936b",ec="#795f49",lw=1.5))
        label = obj['name']
        text(ax,x+w/2,y+h/2,label,11,color="#fffaf2")
    # Visual detail stays inside the declared furniture footprint.
    ax.add_patch(Rectangle((3.4,5.3),.8,.5,fc="#f8eed6",ec="#805e44",lw=.5))
    ax.add_patch(Rectangle((14.4,5.3),.8,.5,fc="#bce0d6",ec="#805e44",lw=.5))
    text(ax,23,20.5,"公共大厅",17)
    text(ax,23,21.6,"青禾社区学习中心",10,color="#73858f")
    save(fig,ROOT/'map'/'assets'/'learning-center-background')


def board(revised=False):
    fig,ax=canvas(96,64,transparent=True)
    ax.add_patch(Rectangle((12,7),72,45,fc="#395565",ec="#223946",lw=2))
    ax.add_patch(Rectangle((17,12),62,34,fc="#e3eee4" if revised else "#f2e8d0",ec="none"))
    for x in (22,68):ax.add_patch(Rectangle((x,52),6,10,fc="#627986",ec="none"))
    text(ax,48,23,"公告栏",9)
    text(ax,48,37,"已调整" if revised else "原计划",8,color="#347459" if revised else "#8d6941")
    save(fig,ROOT/'map'/'assets'/('board-revised' if revised else 'board-original'),True)


PEOPLE=[('lin-lan','林岚','#357ba8'),('zhou-ning','周宁','#4b916f'),('chen-chen','陈晨','#846cb0'),('xu-an','许安','#ce8542')]


def person(ax,x,y,color,direction='down',phase=0):
    stride=(0,1,0,-1)[phase]
    ax.add_patch(Ellipse((x+16,y+29.5),16,3,fc="#233443",alpha=.13,ec="none"))
    ax.add_patch(Rectangle((x+11,y+20),4,8+stride,fc="#44586b",ec="none"))
    ax.add_patch(Rectangle((x+18,y+20),4,8-stride,fc="#44586b",ec="none"))
    ax.add_patch(Rectangle((x+10,y+27+stride),6,3,fc="#263b4b",ec="none"))
    ax.add_patch(Rectangle((x+18,y+27-stride),6,3,fc="#263b4b",ec="none"))
    ax.add_patch(FancyBboxPatch((x+10,y+13),13,11,boxstyle="round,pad=0,rounding_size=2",fc=color,ec="#395161",lw=.5))
    ax.add_patch(Circle((x+16.5,y+8.5),5.3,fc="#e4b692",ec="#755946",lw=.5))
    ax.add_patch(Ellipse((x+16.5,y+5),10.5,5,fc="#414951",ec="none"))
    if direction=='up':
        ax.add_patch(Circle((x+16.5,y+8.5),5.0,fc="#414951",ec="none"))
    elif direction in ('left','right'):
        eye=x+13.5 if direction=='left' else x+19.5
        ax.add_patch(Circle((eye,y+9),.6,fc="#233544",ec="none"))
    else:
        for eye in (14.2,18.8):ax.add_patch(Circle((x+eye,y+9),.6,fc="#233544",ec="none"))


def people():
    for folder,name,color in PEOPLE:
        fig,ax=canvas(128,128,transparent=True)
        for row,direction in enumerate(('down','left','right','up')):
            for col in range(4):person(ax,col*32,row*32,color,direction,col)
        save(fig,ROOT/'agents'/folder/'sprite-4x4',True)
        fig,ax=canvas(128,128,units=(32,32))
        ax.add_patch(Rectangle((0,0),32,32,fc="#f0f4f7",ec="none"))
        person(ax,0,1,color)
        save(fig,ROOT/'agents'/folder/'portrait')


if __name__=='__main__':
    map_background();board(False);board(True);people()
    print('Rendered 11 original authoring assets, each as SVG and PNG; no UI or runtime calls.')
