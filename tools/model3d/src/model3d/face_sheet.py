"""表情の一覧の絵（render.face_sheet の並べる側。Pillow だけ）: 表情（行）× 向き（列）に、描いた絵を並べる。

docs/model3d.md の「表情を形で作る」。リポジトリ外の 3 作目の VRM のダンスの台本と同じ並べ方（同じ絵と同じ書体なら同じ絵になる）。
"""
from pathlib import Path

from .blender_adapter.face_sheet import image_names
from .title import load_font

BACKGROUND = (20, 20, 20)
LABEL = (255, 255, 0)


def join(folder, combos, views, out, cell=320, font=None, font_size=16, quality=90):
    """folder の <札>_<向き>.png を、行 = combos の札・列 = views の向きで並べた絵を out に書く（拡張子で形式。JPEG は quality）。
    こまの大きさは cell × int(cell × 0.9)。左の列の絵の左上に札を書く（黒い地に黄色）。絵が無いか、2 つのこまの絵の名前が重なれば
    ValueError（描く側と同じ image_names）。戻り: 絵の大きさ。"""
    from PIL import Image, ImageDraw
    folder = Path(folder)
    names = image_names(combos, views)
    ch = int(cell * 0.9)
    face = load_font(font, font_size)
    sheet = Image.new("RGB", (cell * len(views), ch * len(combos)), BACKGROUND)
    for r, label in enumerate(combos):
        for c, view in enumerate(views):
            path = folder / names[(label, view)]
            if not path.is_file():
                raise ValueError(f"表情の絵がありません: {path.name}")
            im = Image.open(path).convert("RGB").resize((cell, ch), Image.LANCZOS)
            if c == 0:
                draw = ImageDraw.Draw(im)
                draw.rectangle((0, 0, draw.textlength(label, font=face) + 8, 22), fill=(0, 0, 0))
                draw.text((4, 1), label, fill=LABEL, font=face)
            sheet.paste(im, (c * cell, r * ch))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if Path(out).suffix.lower() in (".jpg", ".jpeg"):
        sheet.save(out, quality=quality)
    else:
        sheet.save(out)
    return sheet.size
