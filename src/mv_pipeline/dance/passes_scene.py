"""mv dance passes の場面: カメラの控えの空間（Space）、手前の板・柱・つなぎの相手（Scene）、要素の一覧（model3d の render.passes。
Plan）の読み込みと、要素の画の読み方。mv_pipeline.dance.passes から以前の名前で読める。"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import numpy as np

from ..io import PipelineError, read_json, sha256
from . import space
from .passes_spec import KINDS, PHANTOM_KIND, SPRITES_SCHEMA, level_at


@dataclass
class Space:
    """3D の位置を使う部品の入力: コマ T ごとのカメラ（cameras）と小さな絵の位置（items）、小さな絵（sprites）。"""
    cameras: dict
    items: dict
    sprites: list


def load_space(spec, plan, cameras_path=None):
    """spec の cameras（model3d-cameras/0.1）・sprites の位置（dance-sprites/0.1）と絵を読む。どちらも無ければ None。
    cameras_path を渡すと、その控えを読む（つなぎの相手の要素のカメラ）。"""
    data = spec.data
    name = cameras_path or data["cameras"]
    if name is None:
        return None
    record = read_json(spec.resolve(name))
    if not isinstance(record, dict) or record.get("schema_version") != "model3d-cameras/0.1":
        raise PipelineError(f"cameras は model3d-cameras/0.1 にしてください: {name}")
    rw, rh = record["resolution"]
    width, height = plan.size
    if rw * height != rh * width:
        raise PipelineError(f"cameras の画面（{rw}×{rh}）と要素（{width}×{height}）の縦横比が違います")
    k = height / rh
    cameras = {}
    for fr in record["frames"]:
        if "distance_m" not in fr:
            raise PipelineError(f"cameras の T {fr['T']} に distance_m（踊り手までの距離）がありません")
        cameras[int(fr["T"])] = {"matrix": fr["matrix"], "focal": fr["focal_px"] * k,
                                 "principal": (fr["principal_px"][0] * k, fr["principal_px"][1] * k), "focus": fr["distance_m"],
                                 "dancer": fr.get("dancer"), "focal_px": fr["focal_px"]}
    absent = [T for T in plan.frames if T not in cameras]
    if data["sprites"] is not None and data["sprites"]["focus_metric"] == "depth":
        lacking = [T for T in plan.frames if T in cameras and cameras[T]["dancer"] is None]
        if lacking:
            raise PipelineError(f"sprites.focus_metric depth には、cameras の dancer（踊り手の点）が要ります（無い T: {lacking[:10]}）")
    if absent:
        raise PipelineError(f"cameras に、重ねるコマの T がありません: {absent[:10]}")
    items, sprites = {}, []
    if data["sprites"] is not None:
        placed = read_json(spec.resolve(data["sprites"]["items"]))
        try:
            jsonschema.Draft202012Validator(json.loads(SPRITES_SCHEMA.read_text(encoding="utf-8"))).validate(placed)
        except jsonschema.ValidationError as error:
            raise PipelineError(f"小さな絵の位置（{data['sprites']['items']}）の形が違います: {error.message[:200]}") from error
        items = {int(fr["T"]): np.asarray(fr["items"], np.float64).reshape(-1, 6) for fr in placed["frames"]}
        try:
            sprites = [space.sprite_levels(spec.resolve(name)) for name in data["sprites"]["images"]]
        except ValueError as error:
            raise PipelineError(str(error)) from error
        if not sprites:
            raise PipelineError("sprites.images に小さな絵がありません")
    return Space(cameras, items, sprites)


@dataclass
class Scene:
    """手前の板とつなぎの入力: 板（色・不透明度・4 隅 m・設定）と、つなぎごとの相手の要素の一覧・カメラと柱の絵。"""
    cards: list
    wipes: list


def load_scene(spec, plan):
    """spec の cards・wipes の絵と、つなぎの相手の要素の一覧（とびとびでよい）とカメラを読む。どちらも無ければ None。"""
    data = spec.data
    if data["cards"] is None and data["wipes"] is None:
        return None
    references = None
    if data["references"] is not None:
        report = read_json(spec.resolve(data["references"]))
        references = (report or {}).get("references")
        if not isinstance(references, dict):
            raise PipelineError(f"references は plan.shots の report（references のあるもの）にしてください: {data['references']}")
    cards = []
    for n, card in enumerate(data["cards"] or []):
        try:
            rgb, alpha = space.read_rgba(spec.resolve(card["image"]))
        except ValueError as error:
            raise PipelineError(f"cards[{n}]: {error}") from error
        if card["corners"] is not None:
            corners = np.asarray(card["corners"], np.float64)
        else:
            place = card["place"]
            if references is None or place["reference"] not in references:
                raise PipelineError(f"cards[{n}] の place.reference {place['reference']!r} が、references（plan.shots の report）にありません")
            reference = references[place["reference"]]
            corners = space.card_corners(reference, place["at"], float(place["depth"]), float(place["width_px"]), place.get("anchor", [0.5, 0.5]),
                                         (alpha.shape[1], alpha.shape[0]))
        cards.append({**card, "rgb": rgb, "alpha": alpha, "corners": corners})
    wipes = []
    for n, wipe in enumerate(data["wipes"] or []):
        if (data["cards"] or data["sprites"] is not None) and wipe["cameras"] is None:     # 相手の画にも、板と小さな絵を置く
            raise PipelineError(f"wipes[{n}] に cameras（相手のカメラの控え）が要ります（手前の板と小さな絵を相手の画にも置くため。"
                                "省くと、つなぎの間に相手の画から消える）")
        other = load_passes(spec.resolve(wipe["passes"]), continuous=False)
        if other.size != plan.size:
            raise PipelineError(f"wipes[{n}] の相手の要素の大きさ {other.size} が、要素の大きさ {plan.size} と違います")
        if abs(other.fps - plan.fps) > 1e-9:                     # 同じ T を同じ時刻として重ねるので、fps もそろえる
            raise PipelineError(f"wipes[{n}] の相手の要素の fps（{other.fps:g}）が、要素の fps（{plan.fps:g}）と違います")
        start, end = wipe["at"] - wipe["seconds"] / 2, wipe["at"] + wipe["seconds"] / 2
        needed = [T for T in plan.frames if abs((T - 1) / plan.fps - wipe["at"]) < wipe["seconds"] / 2]
        absent = [T for T in needed if T not in other.frames]
        if absent:
            raise PipelineError(f"wipes[{n}] の相手の要素に、つなぎの間（{start:g}〜{end:g} 秒）のコマがありません: {absent[:10]}")
        where = load_space(spec, other, wipe["cameras"]) if wipe["cameras"] is not None else None
        try:
            rgb, alpha = space.read_rgba(spec.resolve(wipe["pillar"]["image"]))
        except ValueError as error:
            raise PipelineError(f"wipes[{n}].pillar: {error}") from error
        wipes.append({**wipe, "plan": other, "where": where, "rgb": rgb, "alpha": alpha, "image": None})
    return Scene(cards, wipes)


def card_layer(data, scene, plan, T, where, frame, select):
    """手前の板を重ねる（select はどの板を出すかを決める時刻。つなぎの相手の画では、相手のショットの側の時刻）。"""
    if scene is None or not scene.cards:
        return frame
    t = (T - 1) / plan.fps
    unit = plan.size[0] / 1080
    camera = where.cameras[T]
    view = {"matrix": camera["matrix"], "focal_px": camera["focal"], "principal_px": camera["principal"], "resolution": list(plan.size)}
    for card in scene.cards:
        if card["during"] is not None and not any(a - 1e-6 <= select < b - 1e-6 for a, b in card["during"]):
            continue
        rgb, alpha = card["rgb"], card["alpha"]
        if card["wave"] is not None:
            w = card["wave"]
            rgb, alpha = space.wave(rgb, alpha, level_at(w["amount"], t), w["speed"] * (t - w["start"]), w["wavelength"])
        sigma = card["blur"] * camera["focal_px"] / card["focal_ref"] * unit if card["blur"] > 0 else 0.0
        frame = space.card(frame, rgb, alpha, card["corners"], view, sigma, level_at(card["gain"], t), plan.size, unit)
    return frame


@dataclass
class Plan:
    """重ねるコマの並び。files[要素][T] は要素のファイルの絶対パス（分身は描いた時間のコマだけ）。near[要素][T] は分身が手前か。"""
    manifest: dict
    directory: Path
    fps: float
    size: tuple
    frames: list
    files: dict
    near: dict = field(default_factory=dict)

    def phantoms(self):
        """分身の番号（ph<i> のある i の小さい順）。"""
        return sorted(int(PHANTOM_KIND.match(kind)[1]) for kind in self.files if kind.startswith("ph"))


def load_passes(path, verify=True, continuous=True):
    """要素の一覧（model3d-passes/0.1）を読み、重ねるコマの並びにする。char は要る。どの要素も同じコマがそろい、T が 1 ずつ続くこと
    （continuous が False なら続かなくてよい。つなぎの相手の要素のように、とびとびのコマだけを描いた一覧）。
    verify なら、一覧の sha256 と今のファイルを照合する（描いた後に変わったコマは止める）。"""
    manifest = read_json(path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "model3d-passes/0.1":
        raise PipelineError(f"要素の一覧は model3d-passes/0.1 にしてください: {path}")
    passes = manifest.get("passes") or {}
    if "char" not in passes:
        raise PipelineError("要素の一覧に char（人物）がありません")
    unknown = sorted(kind for kind in passes if kind not in KINDS and not PHANTOM_KIND.match(kind))
    if unknown:
        raise PipelineError(f"要素の一覧に知らない要素があります: {unknown}")
    directory = Path(manifest["directory"])
    frames = [fr["T"] for fr in passes["char"]["frames"]]
    if not frames:
        raise PipelineError("char のコマがありません")
    if continuous and any(b != a + 1 for a, b in zip(frames, frames[1:])):
        raise PipelineError("char のコマの T が 1 ずつ続いていません（とびがあると 1 本の動画にできません）")
    files, near = {}, {}
    for kind, entry in passes.items():
        if entry.get("missing"):
            raise PipelineError(f"{kind} に描いていないコマがあります: {entry['missing'][:10]}")
        by_T = {fr["T"]: fr for fr in entry["frames"]}
        outside = set(entry.get("outside", []))
        if outside & set(by_T) or sorted(set(by_T) | outside) != frames:
            raise PipelineError(f"{kind} のコマが char とそろっていません（描いたコマと描く時間の外のコマ outside を合わせて）")
        files[kind] = {}
        if PHANTOM_KIND.match(kind):
            near[kind] = {T: bool(fr.get("near", False)) for T, fr in by_T.items()}
        for T in sorted(by_T):
            file = directory / by_T[T]["file"]
            if not file.is_file():
                raise PipelineError(f"{kind} のコマ T {T} のファイルがありません: {file}")
            if verify and sha256(file) != by_T[T]["sha256"]:
                raise PipelineError(f"{kind} のコマ T {T} が、一覧を書いた後に変わっています: {file}")
            files[kind][T] = file
    width, height = manifest["resolution"]
    return Plan(manifest, directory, float(manifest["fps"]), (int(width), int(height)), frames, files, near)


def read_image(path, alpha=False):
    """画像を RGB（alpha なら RGBA）の 0〜1 の float32 で読む（日本語の入ったパスでも読めるよう、バイト列から）。"""
    import cv2
    data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED if alpha else cv2.IMREAD_COLOR)
    if image is None:
        raise PipelineError(f"画像を読めません: {path}")
    if alpha:
        if image.ndim != 3 or image.shape[2] != 4:
            raise PipelineError(f"char は透けのある RGBA の画像にしてください: {path}")
        return image[..., [2, 1, 0, 3]].astype(np.float32) / 255
    return image[..., ::-1].astype(np.float32) / 255
