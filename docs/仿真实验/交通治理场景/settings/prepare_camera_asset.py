from pathlib import Path
import shutil
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
src=Path(r'C:\Users\lenovo\.codex\generated_images\01a09539-552c-7f93-b5ab-823448580769\exec-62798740-972a-440e-aa3f-a3772ea55788.png')
local=ROOT/'map'/'道路摄像头-生成原稿.png'; shutil.copy2(src,local)
a=np.asarray(Image.open(local).convert('RGB')).astype(np.float32)
excess=np.minimum(a[:,:,0],a[:,:,2])-a[:,:,1]
alpha=np.clip(1-(excess-30)/100,0,1)
a[:,:,0]=np.minimum(a[:,:,0],a[:,:,1]+70); a[:,:,2]=np.minimum(a[:,:,2],a[:,:,1]+70)
cut=Image.fromarray(np.dstack([a.astype(np.uint8),(alpha*255).astype(np.uint8)]))
cut=cut.crop(cut.getbbox()); cut.save(ROOT/'map'/'道路摄像头-透明高清.png')
cut.thumbnail((28,28),Image.Resampling.LANCZOS)
out=Image.new('RGBA',(32,32)); out.alpha_composite(cut,((32-cut.width)//2,30-cut.height))
out.save(ROOT/'map'/'道路摄像头-32.png')
preview=Image.new('RGBA',(256,256),'#e8eee5'); preview.alpha_composite(out.resize((256,256),Image.Resampling.NEAREST)); preview.save(ROOT/'map'/'道路摄像头-预览.png')
print('Camera PNG saved: 32x32 RGBA.')
