from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'首轮实验方案-v1.md'; text=p.read_text(encoding='utf-8').replace('骑行图片的尺寸和透明通道尚不合格，未上传。','四位 Agent 的头像和透明四方向骑行图已上传保存，整页刷新并逐个重开确认；摄像头独立图片也已绑定保存。人物地图显示比例仍待实验内核验。本地精确副本见 settings/本地副本索引.md。'); p.write_text(text,encoding='utf-8')
p=ROOT/'settings'/'地图与设施录入记录-2026-09-13.md'; text=p.read_text(encoding='utf-8').replace('摄像头与提示牌独立图片、人物图片、真实模型感知、移动预算和抓拍行为仍待完成。','摄像头独立图片与四名人物图片已完成上传保存；提示牌、实验内人物比例、真实模型感知、移动预算和抓拍行为仍待完成。').replace('摄像头独立图片尚未绑定；初始状态不是已发生抓拍事实。','摄像头独立图片已绑定 `交通治理·道路摄像头`（slice-mtzmu1kj-95j95i），源文件 map/道路摄像头-32.png，整图 1×1 格，旋转 0°；17:49 浏览器保存，编辑器近景确认叠加。初始状态不是已发生抓拍事实。').replace('人物文字参数现已录入，头像和行走图尚未绑定，','人物文字参数、头像和行走图均已录入保存，'); p.write_text(text,encoding='utf-8')
print('Current material status synchronized.')
