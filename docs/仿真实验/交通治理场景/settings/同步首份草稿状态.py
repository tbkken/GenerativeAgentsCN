from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
replacements={
 'agents/角色录入记录-2026-09-13.md': [('尚未组建人群、导入实验或运行。','四个单人人群已创建；李子健已导入首份实验草稿，未运行。实验导入后显示大小为空，已阻断，见 settings/Agent显示大小导入缺失-2026-09-13.md（相对案例根目录）。')],
 'settings/地图与设施录入记录-2026-09-13.md': [('尚未创建实验或 Run。','已创建首份草稿“交通治理·C1-L·李子健”（6092db14-4f63-4986-8dd8-b5c81b07ffca），没有 Run。因人物显示大小导入缺失，当前阻断，见 [问题记录](Agent显示大小导入缺失-2026-09-13.md)。'),('17:49 浏览器保存，编辑器近景确认叠加。','17:49 浏览器保存，编辑器近景确认叠加；随后地图校验通过，整页刷新后仍绑定该素材，坐标 56,27 和 22 节点保留。')],
 'settings/Brain与素材进展-2026-09-13.md': [('尚未绑定实验出生点或人群。','四个单人人群已保存；李子健已导入首份草稿，出生点尚未配置。'),('实验尚未创建，该变量尚未配置。','首份实验草稿已创建，该变量尚未配置。'),('未创建、封存交通实验，未启动 Run。','已创建首份草稿“交通治理·C1-L·李子健”（6092db14-4f63-4986-8dd8-b5c81b07ffca），未封存、未启动 Run。图片已在实验内加载成功，但人物显示大小从公共资源的 2 格变成空白，已停止配置，见 [阻断记录](Agent显示大小导入缺失-2026-09-13.md)。')],
 '首轮实验方案-v1.md': [('尚未创建或封存实验、未启动 Run；','已创建首份草稿“交通治理·C1-L·李子健”，未封存、未启动 Run；人物显示大小在导入后为空，当前按约定阻断，见 [问题记录](settings/Agent显示大小导入缺失-2026-09-13.md)；'),('尚未创建实验或启动运行。','首份实验草稿已创建，尚未封存或启动运行，当前因显示大小导入问题阻断。')],
 '实验目标与地图设计-v3.md': [('2026-09-13 已保存公共地图、4 名 Agent 的文字定义和共用 Brain；人物图片暂未达到上传规格。尚未创建实验或启动运行，保存状态与素材问题以 [后续进展](settings/Brain与素材进展-2026-09-13.md) 为准。','2026-09-13 已保存公共地图、4 名 Agent 的完整文字和图片、四个单人人群与共用 Brain，摄像头独立图片已绑定。首份实验草稿已创建，人物图片复制成功，但显示大小从 2 格变为空，当前阻断，未封存或启动运行。见 [后续进展](settings/Brain与素材进展-2026-09-13.md) 与 [阻断记录](settings/Agent显示大小导入缺失-2026-09-13.md)。')]
}
for rel,pairs in replacements.items():
    p=ROOT/rel; text=p.read_text(encoding='utf-8')
    for old,new in pairs:
        if old not in text: raise ValueError(f'Missing expected status: {rel}: {old}')
        text=text.replace(old,new)
    p.write_text(text,encoding='utf-8')
for key,status in [('traffic-rider-brain','已保存公共作者资源；首份草稿概览显示已复制，实验内正文尚未再核验'),('traffic-camera-observe','已保存公共作者资源并绑定地图；已使用该地图创建首份草稿，实验内正文尚未核验')]:
    p=ROOT/'skill'/key/'作者资源.json'; data=json.loads(p.read_text(encoding='utf-8')); data['status']=status; p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Draft and blocking issue status synchronized across current records.')
