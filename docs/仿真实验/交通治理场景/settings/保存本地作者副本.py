"""从人工录入记录生成本地资料副本；不读写系统数据库或实验目录。"""
from pathlib import Path
import re,json,hashlib
ROOT=Path(__file__).resolve().parents[1]
def save(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
text=(ROOT/'agents'/'角色录入记录-2026-09-13.md').read_text(encoding='utf-8')
people=[('李子健',28,'agent-mtzlbf8j-5yfte2',(8,26),'东端办公报到入口','c6600f54-bb96-42f6-bcd5-ce1907fb33c8','79cc589c-1fd7-4c0f-9eb9-604a2c6845cb'),('周浩然',26,'agent-mtzlgmoe-pc5i1i',(10,26),'东端早餐交付点','f2e83af8-0fd0-4b66-9ac9-6dd783ff8de5','9bd4b927-c973-4b8a-aeb4-8ea6b5d0a816'),('小王',29,'agent-mtzlgonc-cvsohq',(12,26),'东端文件接收点','2ff19446-9f3d-4517-86ff-70587951a77e','06333395-d79a-4338-8b2a-53909e022b7d'),('刘桂芳',48,'agent-mtzlgqk5-bh5848',(14,26),'东端住宅预约入口','b4296e2f-be0f-4451-8888-2bd4a6fbabbd','170f8550-76d2-4b13-adc3-5de0f1afbeaf')]
for name,age,key,start,dest,portrait_id,sprite_id in people:
    section=text.split('### '+name+'\n',1)[1].split('\n### ',1)[0].split('\n## 待完成',1)[0]
    fields={key:re.search(r'^- '+label+r'：(.*)$',section,re.M).group(1) for key,label in [('currently','当前目标'),('innate','天生特质'),('learned','背景经历'),('lifestyle','生活方式'),('daily_plan','日常计划')]}
    fields['goals']=re.search(r'```text\n(.*?)\n```',section,re.S).group(1).splitlines()
    assets={}
    for kind,suffix,asset_id in [('portrait','头像',portrait_id),('sprite','骑行行走图',sprite_id)]:
        path=ROOT/'map'/f'{name}-{suffix}-128.png'
        assets[kind]={'file':path.relative_to(ROOT).as_posix(),'public_asset_id':asset_id,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size_px':[128,128]}
    save(ROOT/'agents'/f'{name}.json',{'format':'中文录入资料副本（非系统导入协议）','recorded_date':'2026-09-13','public_template_key':key,'name':name,'age':age,**fields,'sprite_display_tiles':2,'vision_radius':20,'attention_bandwidth':8,'assets':assets,'ui_verification':'保存后整页刷新并重开，两个预览均加载为128×128，行走图4x4','pending_experiment':{'spawn':list(start),'destination':dest,'spawn_status':'仅拟定，未导入实验','display_scale_status':'2格为初值，待地图叠加验收'}})
manifest=json.loads((ROOT/'settings'/'人物图片素材清单.json').read_text(encoding='utf-8'))
for entry in manifest: entry['uploaded']=True; entry['ui_verification']='整页刷新并重开，两张预览均128×128'
save(ROOT/'settings'/'人物图片素材清单.json',manifest)
for key,kind,public_id,ui_hash in [('traffic-rider-brain','BRAIN','65214d64-20c3-4329-b57f-0e2aa9ee1843','3db4f32c9b18'),('traffic-camera-observe','ATOMIC',None,'525b1006cb52')]:
    source=ROOT/'skill'/key/'SKILL.md'
    save(source.parent/'作者资源.json',{'format':'作者资源录入资料副本（非系统导入协议）','skill_key':key,'kind':kind,'public_id':public_id,'ui_content_hash_prefix':ui_hash,'source':'SKILL.md','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'public_id_note':'未记录的公共ID保留null，不编造','status':'已保存公共作者资源，未导入实验'})
print('Saved 4 Agent definitions, 2 Skill/Brain metadata files and image manifest.')
