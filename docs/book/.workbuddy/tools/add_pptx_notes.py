"""把 .slide 源文件里的 notes 备注写入 pptx 的备注页（notesSlide）。

背景：当前生成工具不把 <Slide notes={`...`}> 输出到 pptx（只在形状 descr 里存了源码），
因此这里直接向 pptx 包内注入标准的 notesSlide 部件，保证 PowerPoint 备注窗格可见。

用法：
    python add_pptx_notes.py <slides_dir> <pptx_path>
"""

import glob
import os
import re
import shutil
import sys
import zipfile
from xml.sax.saxutils import escape

NOTES_RE = re.compile(r"notes=\{`(.*?)`\}", re.S)

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

RT_NOTES_SLIDE = NS_R + "/notesSlide"
RT_NOTES_MASTER = NS_R + "/notesMaster"
CT_NOTES_SLIDE = (
    "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"
)


def collect_notes(slides_dir):
    out = []
    for path in sorted(glob.glob(os.path.join(slides_dir, "*.slide"))):
        src = open(path, encoding="utf-8").read()
        m = NOTES_RE.search(src)
        out.append((os.path.basename(path), m.group(1).strip() if m else ""))
    return out


def build_notes_xml(text):
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    body = "".join(
        '<a:p><a:r><a:rPr lang="zh-CN" dirty="0"/><a:t>{}</a:t></a:r></a:p>'.format(
            escape(p)
        )
        for p in paras
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:notes xmlns:a="{a}" xmlns:r="{r}" xmlns:p="{p}">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes Placeholder 2"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
        "<p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>"
        "</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>"
    ).format(a=NS_A, r=NS_R, p=NS_P, body=body)


def notes_rels_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="{}">'
        '<Relationship Id="rId1" Type="{}" Target="../notesMasters/notesMaster1.xml"/>'
        "</Relationships>"
    ).format(NS_REL, RT_NOTES_MASTER)


def slide_order(z):
    """按 presentation.xml 的 sldIdLst 返回页面顺序对应的 slides/slideN.xml 列表。"""
    pres = z.read("ppt/presentation.xml").decode("utf-8", "ignore")
    rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8", "ignore")
    rid2target = dict(
        re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels)
    )
    order = []
    for rid in re.findall(r'<p:sldId[^>]*r:id="([^"]+)"', pres):
        target = rid2target.get(rid, "")
        if target:
            order.append("ppt/" + target.lstrip("/").replace("../", ""))
    return order


def main():
    slides_dir, pptx_path = sys.argv[1], sys.argv[2]
    notes = collect_notes(slides_dir)

    src = zipfile.ZipFile(pptx_path)
    names = src.namelist()
    order = slide_order(src)

    if len(order) != len(notes):
        print("警告：pptx 页数 {} 与源文件页数 {} 不一致".format(len(order), len(notes)))

    replace = {}
    add = {}

    # 1. 逐页生成 notesSlide
    for idx, slide_part in enumerate(order):
        if idx >= len(notes):
            break
        label, text = notes[idx]
        if not text:
            print("  {}: 无备注，跳过".format(label))
            continue

        stem = os.path.splitext(os.path.basename(slide_part))[0]  # slide12
        notes_part = "ppt/notesSlides/notesSlide{}.xml".format(stem[5:])
        notes_rels_part = "ppt/notesSlides/_rels/notesSlide{}.xml.rels".format(stem[5:])

        add[notes_part] = build_notes_xml(text).encode("utf-8")
        add[notes_rels_part] = notes_rels_xml().encode("utf-8")

        # 2. slide 的 rels 里补一条 notesSlide 关系
        rels_part = "ppt/slides/_rels/{}.xml.rels".format(stem)
        rels = src.read(rels_part).decode("utf-8", "ignore")
        used = set(re.findall(r'Id="rId(\d+)"', rels))
        n = 900
        while str(n) in used:
            n += 1
        rel = '<Relationship Id="rId{n}" Type="{t}" Target="../notesSlides/notesSlide{stem}.xml"/>'.format(
            n=n, t=RT_NOTES_SLIDE, stem=stem[5:]
        )
        # 幂等：先移除已有的 notesSlide 关系，避免重复
        rels = re.sub(
            r'<Relationship Id="[^"]*" Type="{}" Target="[^"]*"/>'.format(
                re.escape(RT_NOTES_SLIDE)
            ),
            "",
            rels,
        )
        replace[rels_part] = rels.replace("</Relationships>", rel + "</Relationships>").encode("utf-8")

        print("  {}: 写入备注 {} 字".format(label, len(text)))

    # 3. Content_Types 补 notesSlide 声明
    ct = src.read("[Content_Types].xml").decode("utf-8", "ignore")
    ct = re.sub(r'<Override PartName="/ppt/notesSlides/[^"]*"[^>]*/>', "", ct)
    extras = "".join(
        '<Override PartName="/{}" ContentType="{}"/>'.format(p, CT_NOTES_SLIDE)
        for p in add
        if p.endswith(".xml") and "notesSlide" in p
    )
    replace["[Content_Types].xml"] = ct.replace("</Types>", extras + "</Types>").encode("utf-8")

    # 4. 重写包
    tmp = pptx_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name in names:
            if name in replace:
                out.writestr(name, replace[name])
            elif name in add:
                continue
            else:
                out.writestr(name, src.read(name))
        for name, data in add.items():
            out.writestr(name, data)
    src.close()
    shutil.move(tmp, pptx_path)
    print("完成：{} 页备注写入 {}".format(len([1 for _, t in notes if t]), os.path.basename(pptx_path)))


if __name__ == "__main__":
    main()
