"""目の動きの区間の説明の字（render.eye_captions の計算。Pillow だけ）: 描いたコマに、下の帯と区間の説明、左上の題を入れる。

docs/model3d.md の「目を動かせる作りにする」。リポジトリ外の 3 作目の VRM のダンスのあとの試しと同じ入れ方（基準の幅 1920 で、下に高さ 84・
濃さ 150/255 の黒い帯、説明は 40 の白、題は 26 の白に黒い縁取り 3）。大きさは基準の幅（reference_width）での画素で書き、コマの幅に合わせる。
"""
from pathlib import Path

STYLE = {"reference_width": 1920, "bar_height": 84, "bar_alpha": 150, "caption_size": 40, "caption_at": [36, 68], "title_size": 26,
         "title_at": [34, 24], "title_stroke": 3}   # 仮の値（試しの入れ方）


def fonts(style, font=None, title_font=None, width=None):
    """説明と題の書体（font が無ければ Pillow の内蔵の書体。ラテン文字だけ）。width はコマの幅（大きさを合わせる）。"""
    from .title import load_font
    scale = (width or style["reference_width"]) / style["reference_width"]
    return (load_font(font, max(1, round(style["caption_size"] * scale))),
            load_font(title_font or font, max(1, round(style["title_size"] * scale))))


def layout(style, width, height):
    """コマ（width × height）での (倍率, 帯の高さ, 説明の y, 題の y)。大きさはコマの幅に合わせるので、とても横長のコマでは縦が足りず、
    帯がコマより高くなったり、字がコマの外に出たりする。そのときは ValueError（黙って切れた絵を書かない）。"""
    scale = width / style["reference_width"]
    bar_h = round(style["bar_height"] * scale)
    caption_y = height - round(style["caption_at"][1] * scale)
    title_y = round(style["title_at"][1] * scale)
    if bar_h >= height or caption_y < 0 or title_y + round(style["title_size"] * scale) > height - bar_h:
        raise ValueError(f"コマ {width}×{height} には、帯（高さ {bar_h}）と字が入りません（大きさはコマの幅に合わせる）。reference_width を"
                         "大きくするか、縦の大きいコマにしてください")
    return scale, bar_h, caption_y, title_y


def draw(image, caption, title, style, caption_font, title_font):
    """1 枚のコマ（PIL の RGB）に、帯・説明・題を入れた新しい絵を返す。"""
    from PIL import Image, ImageDraw
    out = image.convert("RGB")
    w, h = out.size
    scale, bar_h, caption_y, title_y = layout(style, w, h)
    bar = Image.new("RGBA", (w, bar_h), (0, 0, 0, style["bar_alpha"]))
    out.paste(bar, (0, h - bar_h), bar)
    d = ImageDraw.Draw(out)
    if caption:
        d.text((round(style["caption_at"][0] * scale), caption_y), caption, fill=(255, 255, 255), font=caption_font)
    if title:
        d.text((round(style["title_at"][0] * scale), title_y), title, fill=(255, 255, 255), font=title_font,
               stroke_width=max(1, round(style["title_stroke"] * scale)), stroke_fill=(0, 0, 0))
    return out


def caption_frames(timeline, frames_dir, out_dir, name, style, title=None, font=None, title_font=None):
    """timeline（motion.eye_plan の出力）のコマごとに、その区間の説明を入れて out_dir へ書く。戻り: 書いたコマの名前の並び。"""
    from PIL import Image
    frames_dir, out_dir = Path(frames_dir), Path(out_dir)
    captions = {s["section"]: s["caption"] for s in timeline["sections"]}
    paths = [(row, frames_dir / name.format(frame=row["frame"])) for row in timeline["frames"]]
    missing = [path.name for _, path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"コマがありません: {missing[:3]}（ほか {max(0, len(missing) - 3)}）")
    sizes = set()
    for _, path in paths:                                            # 書く前に、どの大きさのコマにも帯と字が入るか確かめる
        with Image.open(path) as image:
            sizes.add(image.size)
    for width, height in sorted(sizes):
        layout(style, width, height)
    sources = {path.resolve() for _, path in paths}                  # 書く所が元のコマと同じファイルなら書かない（元のコマを書き換えない）
    same = [path.name for _, path in paths if (out_dir / path.name).resolve() in sources]
    if same:
        raise ValueError(f"書く所が元のコマと同じファイルになります: {same[:3]}（out_dir を frames_dir と別のフォルダにしてください）")
    out_dir.mkdir(parents=True, exist_ok=True)
    written, cache = [], {}
    for row, path in paths:
        with Image.open(path) as image:
            if image.width not in cache:
                cache[image.width] = fonts(style, font, title_font, image.width)
            draw(image, captions[row["section"]], title, style, *cache[image.width]).save(out_dir / path.name, compress_level=6)
        written.append(path.name)
    return written
