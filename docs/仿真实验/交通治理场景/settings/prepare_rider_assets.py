"""仅处理案例图片素材，不连接或修改系统配置。"""
from pathlib import Path
from collections import deque
import hashlib, json, shutil
import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / 'map'
GENERATED = Path(r'C:\Users\lenovo\.codex\generated_images\01a09539-552c-7f93-b5ab-823448580769')
SOURCES = {
    '李子健': MAP / '李子健-骑行图-v2-透明修正未通过.png',
    '周浩然': GENERATED / 'exec-b6e3d934-1cc4-4cc7-a860-7cfe8ca7710b.png',
    '小王': GENERATED / 'exec-78e727cb-6853-4e10-9966-97028a0d0a01.png',
    '刘桂芳': GENERATED / 'exec-9e1957ee-be7d-48d8-821f-af5873e3887e.png',
}

def remove_checker(rgb):
    a = np.asarray(rgb).astype(np.int16)
    candidate = (a.max(2)-a.min(2) <= 3) & (a.mean(2) > 130)
    h,w = candidate.shape
    seen = np.zeros((h,w), bool)
    remove = np.zeros((h,w), bool)
    for yy,xx in zip(*np.nonzero(candidate)):
        if seen[yy,xx]: continue
        queue = deque([(int(yy),int(xx))]); seen[yy,xx] = True; component = []
        while queue:
            y,x = queue.popleft(); component.append((y,x))
            for ny,nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if 0 <= ny < h and 0 <= nx < w and candidate[ny,nx] and not seen[ny,nx]:
                    seen[ny,nx] = True; queue.append((ny,nx))
        if len(component) > 300:
            ys,xs = zip(*component); remove[ys,xs] = True
    alpha = Image.fromarray((~remove).astype(np.uint8)*255).filter(ImageFilter.MinFilter(3))
    rgba = rgb.convert('RGBA'); rgba.putalpha(alpha)
    return rgba

def remove_magenta(rgb):
    a = np.asarray(rgb).astype(np.float32)
    excess = np.minimum(a[:,:,0],a[:,:,2])-a[:,:,1]
    alpha = np.clip(1-(excess-30)/100,0,1)
    a[:,:,0] = np.minimum(a[:,:,0], a[:,:,1]+70)
    a[:,:,2] = np.minimum(a[:,:,2], a[:,:,1]+70)
    out = np.dstack([np.clip(a,0,255).astype(np.uint8),(alpha*255).astype(np.uint8)])
    return Image.fromarray(out)

manifest = []
board = Image.new('RGB',(1200,450),'#e8efe4')
font = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',22)
draw = ImageDraw.Draw(board)
for person_index,(name,src) in enumerate(SOURCES.items()):
    if src.parent != MAP:
        local = MAP / f'{name}-骑行图-v1-生成原稿.png'
        shutil.copy2(src,local); src = local
    rgb = Image.open(src).convert('RGB')
    cutout = remove_checker(rgb) if name == '李子健' else remove_magenta(rgb)
    cutout.save(MAP / f'{name}-骑行图-透明高清.png')
    cells = []
    for row in range(4):
        for col in range(4):
            cell = cutout.crop((round(col*rgb.width/4),round(row*rgb.height/4),round((col+1)*rgb.width/4),round((row+1)*rgb.height/4)))
            box = cell.getbbox()
            if box is None: raise ValueError(f'{name} empty cell {row},{col}')
            cells.append(cell.crop(box))
    scale = min(28/max(c.width for c in cells),28/max(c.height for c in cells))
    sheet = Image.new('RGBA',(128,128))
    for n,cell in enumerate(cells):
        small = cell.resize((round(cell.width*scale),round(cell.height*scale)),Image.Resampling.LANCZOS)
        sheet.alpha_composite(small,(n%4*32+(32-small.width)//2,n//4*32+30-small.height))
    sprite = MAP / f'{name}-骑行行走图-128.png'; sheet.save(sprite)
    portrait = Image.new('RGBA',(134,134),'#e7eee8')
    portrait.alpha_composite(cutout.crop((92,24,226,158)))
    portrait = portrait.resize((128,128),Image.Resampling.LANCZOS).convert('RGB')
    portrait_file = MAP / f'{name}-头像-128.png'; portrait.save(portrait_file)
    x = person_index*300
    draw.text((x+18,8),name,font=font,fill='#243635')
    board.paste(portrait,(x+85,45))
    enlarged = sheet.resize((256,256),Image.Resampling.NEAREST)
    board.paste(enlarged,(x+22,185),enlarged)
    assets = []
    for path in (src,sprite,portrait_file):
        im = Image.open(path)
        assets.append({'path':path.relative_to(ROOT).as_posix(),'size':list(im.size),'mode':im.mode,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest.append({'name':name,'assets':assets,'sprite_layout':{'columns':4,'rows':['down','left','right','up'],'frame_px':[32,32]},'portrait_source':'正面第一帧头肩部裁切','uploaded':False})
board.save(MAP / '四位骑行者-素材检查.png')
(ROOT/'settings'/'人物图片素材清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps([{'name':x['name'],'assets':x['assets'][1:]} for x in manifest],ensure_ascii=False))
