"""目の確かめの絵の一覧（render.eye_sheet の並べる側。Pillow だけ）: 姿勢（行）× 見る向き（列）に、描いた絵を並べる。

docs/model3d.md の「目を動かせる作りにする」。リポジトリ外の 3 作目の VRM のダンスのあとの試しと同じ並べ方（こまの縦横は絵と同じ比、
こまの上に「姿勢 / 向き」の札）。書体は既定で Pillow の内蔵の書体（ラテン文字だけ）。
"""
from pathlib import Path

from .blender_adapter.face_sheet import image_names
from .title import load_font

BACKGROUND = (24, 24, 26)
LABEL = (255, 255, 0)
STRIP = 22                       # こまの上の札の帯（画素）


def join(folder, poses, views, out, cell=480, quality=90, font=None, font_size=16):
    """folder の絵（face_sheet.image_names の名前）を、行 = poses・列 = views で並べた絵を out に書く（拡張子で形式。JPEG は quality）。
    こまは横 cell、縦は最初の絵と同じ比。絵が無いか、2 つのこまの絵の名前が重なれば ValueError。戻り: 絵の大きさ。"""
    from PIL import Image, ImageDraw
    folder = Path(folder)
    names = image_names(poses, views)
    missing = [names[key] for key in names if not (folder / names[key]).is_file()]
    if missing:
        raise ValueError(f"確かめの絵がありません: {missing[:3]}")
    with Image.open(folder / names[(poses[0], views[0])]) as first:
        ch = int(cell * first.height / first.width)
    if ch < 3 or cell < 3:                                             # こまの絵は枠の 2 画素を引いた大きさ
        raise ValueError(f"並べるこまの縦が {ch} 画素で、枠（2 画素）を引くと絵が入りません（cell {cell}、絵 {first.width}×{first.height}）")
    face = load_font(font, font_size)
    sheet = Image.new("RGB", (cell * len(views), (ch + STRIP) * len(poses)), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for r, pose in enumerate(poses):
        for c, view in enumerate(views):
            with Image.open(folder / names[(pose, view)]) as image:
                sheet.paste(image.convert("RGB").resize((cell - 2, ch - 2), Image.LANCZOS), (c * cell + 1, r * (ch + STRIP) + STRIP))
            draw.text((c * cell + 4, r * (ch + STRIP) + 2), f"{pose} / {view}", fill=LABEL, font=face)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if Path(out).suffix.lower() in (".jpg", ".jpeg"):
        sheet.save(out, quality=quality)
    else:
        sheet.save(out)
    return sheet.size
