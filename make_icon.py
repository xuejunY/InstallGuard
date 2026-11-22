"""生成安装守卫的多尺寸盾牌图标 assets/guard.ico。

设计：蓝色渐变盾牌 + 白色对勾 + 浅蓝高光边框，透明背景。
16px 下仍能辨认轮廓与对勾。运行：.venv\\Scripts\\python.exe make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512
OUT = Path(__file__).parent / "assets" / "guard.ico"


def bezier(p0, p1, p2, steps=26):
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append((
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ))
    return out


def shield_points(scale=SIZE):
    pts = [
        (0.20, 0.13), (0.80, 0.13),
        (0.80, 0.28), (0.80, 0.45),
    ]
    pts += bezier((0.80, 0.45), (0.79, 0.71), (0.50, 0.89))
    pts += bezier((0.50, 0.89), (0.21, 0.71), (0.20, 0.45))
    pts += [(0.20, 0.28)]
    return [(int(x * scale), int(y * scale)) for x, y in pts]


def gradient(size, top, bottom):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,)
        d.line((0, y, size, y), fill=color)
    return img


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # 柔和投影
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon(
        [(x, y + 14) for x, y in shield_points()], fill=(15, 42, 90, 90)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    img.alpha_composite(shadow)

    # 盾牌主体渐变，用遮罩裁剪
    fill = gradient(SIZE, (59, 130, 246), (29, 78, 216))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).polygon(shield_points(), fill=255)
    img.paste(fill, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # 外描边：深蓝，增强小尺寸辨识度
    d.line(shield_points() + [shield_points()[0]], fill=(30, 64, 175, 255), width=14, joint="curve")
    # 左上柔和光泽，让盾牌有立体感（不会在小尺寸形成杂线）
    gloss = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse((70, -80, 310, 190), fill=(190, 220, 255, 85))
    gloss = gloss.filter(ImageFilter.GaussianBlur(26))
    gloss.putalpha(Image.composite(gloss.getchannel("A"), Image.new("L", (SIZE, SIZE), 0), mask))
    img.alpha_composite(gloss)
    d = ImageDraw.Draw(img)

    # 对勾：白色，带轻微阴影
    check = [(0.32, 0.47), (0.455, 0.615), (0.70, 0.335)]
    check_px = [(int(x * SIZE), int(y * SIZE)) for x, y in check]
    d.line(check_px, fill=(12, 45, 110, 150), width=54, joint="curve")
    d.line([(x, y - 8) for x, y in check_px], fill=(255, 255, 255, 255), width=46, joint="curve")

    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    master = build()
    master.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                           (64, 64), (128, 128), (256, 256)])
    master.resize((256, 256), Image.LANCZOS).save(OUT.parent / "guard.png")
    print("icon:", OUT, OUT.stat().st_size, "bytes")
    print("preview:", OUT.parent / "guard.png")
