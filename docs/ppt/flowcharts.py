#!/usr/bin/env python3
"""Generate 4 flowcharts in the 14 (1).pptx light-blue template style.

Design rules (from user feedback on 图1):
  - embed template background JPG for exact color match
  - large fonts only (title 48+, box 28-32, content 24-26)
  - drop tiny technical details; keep only the main flow labels
"""
import base64
import os
import pymupdf

def esc(s):
    """Escape characters that have special meaning in XML/SVG text."""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BG_PATH = os.path.join(ROOT, "docs", "ppt", "_tpl_bg", "p01_0b91503e.jpg")
with open(BG_PATH, "rb") as f:
    BG_B64 = base64.b64encode(f.read()).decode()

# Palette
C_DK = "#44546A"
C_A1 = "#ADC7DD"
C_A2 = "#E1F1FE"
C_A4 = "#6A9AC4"
C_A5 = "#A3C9E9"
# Semantic colors
C_RED = "#E0625C"   # failure / reject
C_GREEN = "#4FAE7A" # success
C_GRAY = "#A6A6A6"  # alternative
C_AMBER = "#E5A64F" # warning / dry-run


def header(num, total, title, subtitle):
    """Top title bar + page badge."""
    return f'''
  <g>
    <circle cx="90" cy="120" r="26" fill="{C_A4}"/>
    <text x="90" y="131" font-size="28" font-weight="bold" fill="white" text-anchor="middle">{esc(num)}</text>
    <text x="140" y="135" font-size="46" font-weight="bold" fill="{C_DK}">{esc(title)}</text>
    <text x="140" y="185" font-size="24" fill="{C_A4}">{esc(subtitle)}</text>
    <line x1="140" y1="210" x2="600" y2="210" stroke="{C_A4}" stroke-width="4"/>
  </g>
  <rect x="1720" y="90" width="160" height="56" rx="28" fill="{C_A4}"/>
  <text x="1800" y="127" font-size="22" fill="white" text-anchor="middle">图 {esc(num)} / {esc(total)}</text>'''


def decor_top_right():
    return f'''
  <g opacity="0.30">
    <circle cx="1820" cy="110" r="130" fill="{C_A1}"/>
    <circle cx="1680" cy="200" r="60" fill="{C_A5}"/>
  </g>'''


def footer(label):
    return f'''
  <text x="80" y="1055" font-size="22" fill="{C_A4}">{esc(label)}</text>
  <text x="1840" y="1055" font-size="22" fill="{C_A4}" text-anchor="end">→ 下一张</text>'''


def arrow(x1, y1, x2, y2, color=C_A4, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="3"{d} marker-end="url(#arr)"/>'


def box(x, y, w, h, title, lines, *, fill="white", border=C_A1, title_color=C_DK,
        text_color=C_DK, accent=None, title_size=30, body_size=24):
    """A card with optional left accent bar."""
    acc = f'<rect x="{x}" y="{y+10}" width="10" height="{h-20}" fill="{accent}"/>' if accent else ''
    body = ""
    if isinstance(lines, str):
        lines = [lines]
    line_h = body_size * 1.4
    total_h = title_size * 1.4 + len(lines) * line_h + 30
    cur_y = y + title_size * 1.1
    body += f'<text x="{x+30}" y="{cur_y}" font-size="{title_size}" font-weight="bold" fill="{title_color}">{esc(title)}</text>'
    cur_y += title_size * 1.3
    for i, ln in enumerate(lines):
        body += f'<text x="{x+30}" y="{cur_y + i*line_h}" font-size="{body_size}" fill="{text_color}">{esc(ln)}</text>'
    return f'''<g>
    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{fill}" stroke="{border}" stroke-width="3"/>
    {acc}
    {body}
  </g>'''


def numbered(num, x, y, w, h, title, lines, *, title_size=30, body_size=24):
    """A card with a numbered blue circle (top-left)."""
    body = ""
    line_h = body_size * 1.4
    cy = y + 45
    body += f'<circle cx="{x+50}" cy="{cy}" r="28" fill="{C_A4}"/>'
    body += f'<text x="{x+50}" y="{cy+10}" font-size="28" font-weight="bold" fill="white" text-anchor="middle">{esc(num)}</text>'
    body += f'<text x="{x+100}" y="{cy+10}" font-size="{title_size}" font-weight="bold" fill="{C_DK}">{esc(title)}</text>'
    cur_y = cy + 55
    if isinstance(lines, str):
        lines = [lines]
    for i, ln in enumerate(lines):
        body += f'<text x="{x+30}" y="{cur_y + i*line_h}" font-size="{body_size}" fill="{C_DK}">{esc(ln)}</text>'
    return f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="white" stroke="{C_A1}" stroke-width="3"/>{body}</g>'


def diamond(cx, cy, w, h, title, lines, *, title_size=28, body_size=22,
            fill="white", border=C_A4):
    """A decision diamond. lines are placed inside (centered)."""
    half_w, half_h = w/2, h/2
    pts = f"{cx},{cy-half_h} {cx+half_w},{cy} {cx},{cy+half_h} {cx-half_w},{cy}"
    txt = f'<text x="{cx}" y="{cy-10}" font-size="{title_size}" font-weight="bold" fill="{C_DK}" text-anchor="middle">{esc(title)}</text>'
    line_h = body_size * 1.4
    if isinstance(lines, str):
        lines = [lines]
    for i, ln in enumerate(lines):
        txt += f'<text x="{cx}" y="{cy+20 + i*line_h}" font-size="{body_size}" fill="{C_DK}" text-anchor="middle">{esc(ln)}</text>'
    return f'<polygon points="{pts}" fill="{fill}" stroke="{border}" stroke-width="3"/>{txt}'


def wrap(title, body, *, total=5, footer_text=""):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" width="1920" height="1080" font-family="DengXian, 'Microsoft YaHei', sans-serif">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#D8E8F4"/><stop offset="1" stop-color="#F5F9FC"/></linearGradient>
    <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{C_A4}"/>
    </marker>
  </defs>
  <image x="0" y="0" width="1920" height="1080" href="data:image/jpeg;base64,{BG_B64}" preserveAspectRatio="none"/>
  {decor_top_right()}
  {header(title['num'], total, title['zh'], title['sub'])}
  {body}
  {footer(footer_text)}
</svg>'''


# ════════════════════════════════════════════════════════════════
# 图2: 待机与触发
# ════════════════════════════════════════════════════════════════
def chart_2():
    body = f'''
  {box(120, 340, 360, 200, "① 红外传感器", ["串口常驻轮询", "9600 波特"], accent=C_A4)}
  {arrow(480, 440, 560, 440)}
  {box(560, 340, 360, 200, "② 打开摄像头", ["持续人脸识别", "空闲自动关电源"], accent=C_A4)}
  {arrow(920, 440, 1000, 440)}
  {diamond(1180, 440, 360, 260, "红外 / 人脸", ["冷却期过？"])}
  {arrow(1000, 600, 1000, 720, color=C_RED, dash="10,6")}
  {box(800, 720, 400, 180, "③ 长时间无法识别", ["停留超 3s → 写异常", "离开后重新布防"], border=C_RED)}

  {arrow(1360, 440, 1520, 440, color=C_GREEN)}
  {box(1520, 340, 320, 200, "④ 进入五步认证", ["见 图 3"], fill=C_A2, border=C_GREEN, accent=C_GREEN, title_size=30)}

  {arrow(1000, 570, 800, 570, color=C_GRAY)}
  <text x="900" y="560" font-size="22" fill="{C_GRAY}" text-anchor="middle">否</text>
  {arrow(800, 280, 300, 280, color=C_GRAY)}
  <text x="540" y="270" font-size="22" fill="{C_GRAY}">否 · 继续观察</text>
  {arrow(1180, 310, 1180, 310)}

  {box(1450, 720, 390, 180, "⑤ 其他入口", ["GUI「手动认证」", "main.py --hardware"], border=C_GRAY)}
  {arrow(1450, 780, 1450, 780)}'''
    svg = wrap({"num": 2, "zh": "待机与触发 · 从人到认证的入口", "sub": "红外常驻轮询 · 摄像头随人开 · 自动认证条件"},
               body, total=5, footer_text="承接图1 · 启动完成即进入待机轮询")
    out = os.path.join(ROOT, "docs", "流程图_2_待机与触发_模板风格.svg")
    open(out, "w", encoding="utf-8").write(svg)
    return out


# ════════════════════════════════════════════════════════════════
# 图3: 五步认证 · 多模态融合判定
# ════════════════════════════════════════════════════════════════
def chart_3():
    steps = []
    for i, (num, title) in enumerate([(1, "红外检测"), (2, "人脸识别"),
                                     (3, "活体检测"), (4, "声纹识别"),
                                     (5, "融合判定")]):
        x = 100 + i * 350
        steps.append(numbered(num, x, 330, 320, 180, title, []))

    body = "\n  ".join(steps)
    body += "\n  " + arrow(170, 510, 200, 510)
    body += "\n  " + arrow(520, 510, 550, 510)
    body += "\n  " + arrow(870, 510, 900, 510)
    body += "\n  " + arrow(1220, 510, 1250, 510)

    body += f'''
  {arrow(1500, 600, 1500, 640)}
  {box(580, 600, 760, 110, "融合得分 = 0.45×人脸 + 0.25×活体 + 0.20×声纹 + 0.10×红外", [], title_size=28, body_size=24, fill=C_A2, border=C_A4)}
  {arrow(960, 710, 960, 760)}
  {diamond(960, 850, 420, 200, "融合通过？", ["score ≥ 0.78"])}
  {arrow(1170, 850, 1320, 850, color=C_GREEN)}
  <text x="1245" y="842" font-size="22" fill="{C_GREEN}">是</text>
  {box(1320, 750, 540, 200, "签发一次性认证书", ["credential_id · 300 秒有效"], fill=C_A2, border=C_GREEN, accent=C_GREEN, title_size=32)}
  {arrow(800, 920, 480, 970, color=C_RED, dash="10,6")}
  <text x="700" y="945" font-size="22" fill="{C_RED}">否</text>
  {box(100, 960, 380, 80, "认证失败 X", ["写事件 · 上报"], border=C_RED)}'''
    svg = wrap({"num": 3, "zh": "五步认证 · 多模态融合判定", "sub": "人脸 45% + 活体 25% + 声纹 20% + 红外 10% · 阈值 0.78"},
               body, total=5, footer_text="任一环节超时即中止本轮")
    out = os.path.join(ROOT, "docs", "流程图_3_五步认证_模板风格.svg")
    open(out, "w", encoding="utf-8").write(svg)
    return out


# ════════════════════════════════════════════════════════════════
# 图4: 语音 Agent · 对话式开锁请求
# ════════════════════════════════════════════════════════════════
def chart_4():
    steps = ""
    for i, (num, title) in enumerate([(1, "VAD 分段"), (2, "ASR 转写"),
                                     (3, "LLM 回复"), (4, "TTS 合成")]):
        x = 100 + i * 440
        steps += "\n  " + numbered(num, x, 280, 400, 160, title, [])
        if i < 3:
            steps += "\n  " + arrow(x + 400, 360, x + 440, 360)

    body = f'''
  {box(680, 70, 560, 130, "一次性凭证就绪", ["credential_id · 300 秒有效"], fill=C_A2, border=C_GREEN, accent=C_GREEN, title_size=34)}
  {arrow(960, 200, 960, 280)}

  {steps}

  <polyline points="1740,440 1740,470 960,470 960,510" fill="none" stroke="{C_A4}" stroke-width="3" marker-end="url(#arr)"/>
  {diamond(960, 610, 360, 200, "用户意图？", ["说「开门」？"])}
  <polyline points="780,710 780,750 270,750" fill="none" stroke="{C_RED}" stroke-width="3" stroke-dasharray="10,6" marker-end="url(#arr)"/>
  <text x="800" y="740" font-size="22" fill="{C_RED}">凭证无效</text>
  <polyline points="960,710 960,750 740,750" fill="none" stroke="{C_A4}" stroke-width="3" marker-end="url(#arr)"/>
  <text x="860" y="740" font-size="22" fill="{C_A4}">说「开门」</text>

  {box(80, 750, 380, 140, "凭证无效 · 拒绝开门", ["引导重认证 · 通知业主"], border=C_RED, title_size=28)}
  {box(540, 750, 400, 140, "查询 current_auth_context", ["available · fresh · authorized"], border=C_A4, title_size=28)}
  {box(1020, 750, 380, 140, "request_unlock", ["→ 见 图 5"], border=C_AMBER, title_size=32)}
  {arrow(940, 750, 1020, 750)}

  <polyline points="1400,820 1400,860 100,860 100,420 100,360" fill="none" stroke="{C_GRAY}" stroke-width="3" marker-end="url(#arr)"/>
  <text x="700" y="900" font-size="22" fill="{C_GRAY}">回到对话（闲噪 / 询问也走这条）</text>'''
    svg = wrap({"num": 4, "zh": "语音 Agent · 对话式开锁请求", "sub": "凭证就绪后开麦 · VAD → ASR → LLM → TTS 连续对话"},
               body, total=5, footer_text="对话循环直到用户离开或凭证过期失效")
    out = os.path.join(ROOT, "docs", "流程图_4_语音Agent_模板风格.svg")
    open(out, "w", encoding="utf-8").write(svg)
    return out


# ════════════════════════════════════════════════════════════════
# 图5: 安全闸门 · 开锁的最终裁决
# ════════════════════════════════════════════════════════════════
def chart_5():
    body = f'''
  {box(680, 230, 560, 110, "Agent 发起 request_unlock", ["credential_id + reason"], fill=C_A2, border=C_RED, accent=C_RED, title_size=32)}
  {arrow(960, 340, 960, 380)}

  {diamond(960, 600, 520, 440, "安全闸门五项校验", ["① flow = agent_confirm", "② 凭证未过期 (<300s)", "③ fusion_passed = true", "④ 凭证未被消费", "⑤ 非 dry-run (NO_UNLOCK=0)"], body_size=22)}

  {arrow(1240, 600, 1420, 600, color=C_GREEN)}
  {arrow(680, 600, 500, 600, color=C_RED, dash="10,6")}
  <text x="1380" y="592" font-size="24" fill="{C_GREEN}">是</text>
  <text x="585" y="592" font-size="24" fill="{C_RED}">否</text>

  {box(1420, 545, 420, 110, "消费凭证（幂等）", ["consumed = true · 一次性"], fill=C_A2, border=C_GREEN, accent=C_GREEN, title_size=28)}
  {arrow(1630, 655, 1630, 700)}
  {box(1420, 700, 420, 110, "驱动继电器 · 门开", [], fill="#E1F1FE", border=C_GREEN, accent=C_GREEN, title_size=32)}
  {arrow(1630, 810, 1630, 855)}
  {box(1420, 855, 420, 90, "开锁完成", ["凭证立即失效 · 留痕"], fill=C_A2, border=C_GREEN, accent=C_GREEN, title_size=30)}

  {box(80, 545, 420, 110, "拒绝请求 X", ["返回具体拒绝原因"], border=C_RED, title_size=30)}
  {arrow(290, 655, 290, 700)}
  {box(80, 700, 420, 110, "Agent 告知用户", ["引导重认证 · 通知业主"], border=C_RED, title_size=30)}
  <polyline points="290,810 290,900 1000,900" fill="none" stroke="{C_GRAY}" stroke-width="3" marker-end="url(#arr)"/>
  <text x="700" y="888" font-size="22" fill="{C_GRAY}">回到语音对话（图4）</text>

  {box(80, 970, 860, 80, "调试保护 · 闸门只模拟开锁 · 绝不驱动继电器", [], border=C_AMBER, title_size=24, body_size=22, fill="#FDF5E6")}
  {box(960, 970, 880, 80, "权限边界 · Agent 不评分 / 凭证过期或已消费一律拒绝", [], border=C_AMBER, title_size=24, body_size=22, fill="#FDF5E6")}'''
    svg = wrap({"num": 5, "zh": "安全闸门 · 开锁的最终裁决", "sub": "Agent 只有请求权 · 闸门拥有一票否决权"},
               body, total=5, footer_text="全流程：硬件认证 → 凭证 → 请求 → 闸门 四层防线")
    out = os.path.join(ROOT, "docs", "流程图_5_安全闸门_模板风格.svg")
    open(out, "w", encoding="utf-8").write(svg)
    return out


if __name__ == "__main__":
    paths = [chart_2(), chart_3(), chart_4(), chart_5()]
    for p in paths:
        # Render PNG via PyMuPDF
        doc = pymupdf.open(p)
        png = p.replace(".svg", ".png")
        doc[0].get_pixmap(dpi=150, alpha=False).save(png)
        print("rendered:", os.path.basename(png))
