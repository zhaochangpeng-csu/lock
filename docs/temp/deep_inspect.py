# -*- coding: utf-8 -*-
"""深度体检：背景色、形状密度、图片数、动画分布（直接读 XML，避免枚举遗漏）。"""
import re
from collections import Counter
from pptx import Presentation
from pptx.util import Emu

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"

FILES = [
    ("PPT模板.pptx", r"C:\Users\hoyo\Desktop\lock\PPT模板.pptx"),
    ("14 (1).pptx", r"C:\Users\hoyo\Desktop\lock\14 (1).pptx"),
    ("16 (6).pptx", r"C:\Users\hoyo\Desktop\lock\16 (6).pptx"),
]


def child_count(el, tag_ns, tag):
    return len(el.findall(f".//{{{tag_ns}}}{tag}"))


def background_of(part_el):
    """返回该 part 的背景 solidFill 颜色，找不到返回 None。"""
    bg = part_el.find(f"{{{NS_P}}}bg")
    if bg is None:
        return None
    sf = bg.find(f".//{{{NS_A}}}solidFill/{{{NS_A}}}srgbClr")
    if sf is not None:
        return sf.get("val")
    sf2 = bg.find(f".//{{{NS_A}}}solidFill/{{{NS_A}}}schemeClr")
    if sf2 is not None:
        return "scheme:" + (sf2.get("val") or "?")
    return "<non-solid bg>"


def analyze(name, path):
    prs = Presentation(path)
    master = prs.slide_masters[0]
    print("=" * 76)
    print(f"### {name}")
    print(f"  size {round(Emu(prs.slide_width).inches,2)} x "
          f"{round(Emu(prs.slide_height).inches,2)} in")

    # 主题色板
    theme_el = master.element.find(f".//{{{NS_A}}}clrScheme")
    if theme_el is not None:
        pal = {}
        for ch in theme_el:
            tag = ch.tag.split("}")[-1]
            if ch.find(f"{{{NS_A}}}srgbClr") is not None:
                pal[tag] = ch.find(f"{{{NS_A}}}srgbClr").get("val")
            elif ch.find(f"{{{NS_A}}}sysClr") is not None:
                pal[tag] = "sys:" + (ch.find(f"{{{NS_A}}}sysClr").get("lastClr") or "?")
        key = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3",
               "accent4", "accent5", "accent6"]
        print("  palette: " + "  ".join(f"{k}=#{pal[k]}" for k in key if k in pal))

    m_bg = background_of(master.element)
    print(f"  master bg: {m_bg}")

    # 自定义版式（含内页）
    print("  -- custom layouts (raw spTree) --")
    for i, lay in enumerate(prs.slide_layouts):
        sp = lay.element.find(f".//{{{NS_P}}}spTree")
        n_sp = len(sp.findall(f"{{{NS_P}}}sp")) if sp is not None else 0
        n_pic = len(sp.findall(f"{{{NS_P}}}pic")) if sp is not None else 0
        n_grp = len(sp.findall(f"{{{NS_P}}}grpSp")) if sp is not None else 0
        if n_sp or n_pic or n_grp:
            l_bg = background_of(lay.element)
            print(f"   [{i:>2}] {lay.name:<20} sp={n_sp:<3} pic={n_pic:<3} "
                  f"grp={n_grp:<3} bg={l_bg}")

    # 每页幻灯片
    print("  -- slides --")
    anim_total = 0
    dark_cnt = 0
    for i, s in enumerate(prs.slides):
        sp = s.element.find(f".//{{{NS_P}}}spTree")
        n_sp = len(sp.findall(f"{{{NS_P}}}sp")) if sp is not None else 0
        n_pic = len(sp.findall(f"{{{NS_P}}}pic")) if sp is not None else 0
        n_grp = len(sp.findall(f"{{{NS_P}}}grpSp")) if sp is not None else 0
        timing = s.element.find(f"{{{NS_P}}}timing")
        n_anim = 0
        if timing is not None:
            tn = timing.find(f".//{{{NS_P}}}tnLst")
            n_anim = len(tn.findall(f"{{{NS_P}}}par")) if tn is not None else 0
        anim_total += n_anim
        s_bg = background_of(s.element) or ""
        if s_bg.startswith(("1", "2", "3")) and len(s_bg) == 6:
            r, g, b = int(s_bg[0:2], 16), int(s_bg[2:4], 16), int(s_bg[4:6], 16)
            if (r + g + b) / 3 < 90:
                dark_cnt += 1
        if i < 8 or n_anim:
            print(f"   s{i:<2} sp={n_sp:<3} pic={n_pic:<3} grp={n_grp:<3} "
                  f"anim={n_anim:<2} bg={s_bg}")
    print(f"  => anim nodes total={anim_total}   dark-bg slides(first8+)={dark_cnt}")
    print()


for n, p in FILES:
    try:
        analyze(n, p)
    except Exception as e:
        print(f"### {n} FAILED: {e}")
