"""動きの検品（check.animation）の数える所（Blender の外。numpy だけ）。

動きを付けた .blend から Blender で書き出した値（blender_adapter/animation_sample.py の npz と JSON）を読み、全コマを調べて、
破綻の候補を「いつ・どこが・どれだけ」の出来事にまとめる。**直さない。採点しない。関門にしない**（どの出来事も status: review・
evidence: measured。異常と読むかは、絵を見て人やエージェントが決める）。docs/model3d.md の「動きを検品する」。

見る物:
- 静的（1 コマだけ見て分かる）
  - penetration.arm_body: 腕（二の腕・ひじ・前腕・手首・こぶし）が胴・頭に入る
  - penetration.arm_leg: 前腕・手首・こぶしが太もも・すねに入る
  - penetration.arm_arm: 左右の腕が重なる（手首とこぶしは見ない）
  - penetration.leg_leg: 左右の脚が重なる
  - penetration.floor: 足のメッシュの低い点が床（z = 0）より下
  - penetration.body_cloth: 体が、上に重なる布（裾など。揺れものの骨で動くメッシュ）を突き抜ける
  - joint.wrist_bend・joint.wrist_twist・joint.neck・joint.knee_back: 関節の無理
- 動的（前後のコマと比べて分かる）
  - motion.pop: 骨の向きが 1 コマで飛ぶ（戻る／切り替わる）
  - motion.position_pop: 関節の位置が 1 コマだけ飛んで戻る
  - motion.jitter: 小刻みに震える（回りの向きが短い間に何度も逆になる）
  - motion.foot_slide: 床に着いた足が滑る
  - motion.cut: 全身が同じコマで切り替わる（動きのつなぎ目。知らせるだけ）

体の形は、そのモデルの初期姿勢のメッシュから作る（面の向きには頼らない）: 胴は高さ 2 cm ごとの輪切りの「向きごとの半径」、頭は面までの
距離、脚は骨のまわりの太さの中央値、腕は骨の上の 7 点と太さ。腕を自然に下ろした時の二の腕と胴の重なり（その体で避けられない重なり）は、
先に測って差し引く。飛びは「目に見える向き」（部位そのものの向き。腕や脚の筒は軸まわりを除く。親が同じコマで飛んだ子は挙げない）で測る。

リポジトリ外の試作（動きの検品の台本。Blender の中で測る所と数える所が 1 本に混ざっていた）を、測る所（Blender）と数える所（ここ）に
分けて移した。計算の順と式は試作と同じ。試作は Blender の単精度（mathutils）で計算していた所があり、ここは倍精度で計算する。
しきい値の既定（DEFAULTS）は仮の値で、コードで作った試しの動きと、2 作目・3 作目の VRM のダンスで決めた。
"""
import fnmatch
import math

import numpy as np

SCHEMA = "model3d-animation-check/0.1"
SAMPLE_SCHEMA = "model3d-animation-sample/0.1"
SIDES = ("left", "right")
SIDE = {"left": "左", "right": "右"}
REQUIRED_ROLES = ("hips", "spine", "head", *(f"{side}_{part}" for side in SIDES
                                             for part in ("upper_arm", "lower_arm", "hand", "upper_leg", "lower_leg", "foot")))
TORSO_ROLES = ("hips", "spine", "chest", "upper_chest", "neck")

# しきい値の既定（仮の値。コードで作った試しの動きと、2 作目・3 作目の VRM のダンスで決めた。長さは m、角度は度）
DEFAULTS = {
    "arm_upper": [[0.55, 0.042], [0.80, 0.042], [1.00, 0.040]],     # 二の腕の上の点（付け根からの割合 u, 太さの半分 r）。最後の点がひじ
    "arm_lower": [[0.33, 0.036], [0.66, 0.033], [1.00, 0.030]],     # 前腕の上の点。最後の点が手首
    "fist": [0.075, 0.032],                                         # 手首からこぶしの中心まで, こぶしの太さの半分
    "penetration_min_m": 0.030,       # これより浅い重なりは「接触」として件数だけ数え、出来事には挙げない（腕と頭・腕と脚・腕どうし）
    "contact_min_m": 0.010,           # これより浅いものは数えない
    # 腕と胴: 挙げ始める深さを腕の場所ごとに変える。二の腕とひじを体の脇に付けた姿勢は、袖に隠れて目には破綻に見えにくい
    "arm_body_min_m": {"upper_arm": 0.08, "elbow": 0.08, "forearm": 0.04, "wrist": 0.03, "fist": 0.03},
    "leg_overlap_min_m": 0.070,       # 脚どうし（太ももは柔らかく、閉じると重なるので、深い時だけ）
    "floor_min_m": 0.015,
    "torso_weight_min": 0.85,         # 胴の骨の重みがこの割合以上の頂点だけを胴の形にする（わきの下のふくらみを入れない）
    "wrist_bend_max_deg": 75, "wrist_twist_max_deg": 120, "neck_max_deg": 70, "knee_back_min_m": 0.025,
    "pop_min_deg": 30, "pop_ratio": 3.0, "pop_window": 6, "spring_pop_min_deg": 45, "spring_pop_ratio": 4.0,
    "cut_share": 0.5, "cut_bone_deg": 25, "swing_m_per_frame": 0.06,
    "pos_pop_min_m": 0.03,
    "jitter_window": 8, "jitter_reversals": 5, "jitter_min_deg": 2.0,
    "slide_min_m_per_frame": 0.008, "slide_max_m_per_frame": 0.04, "slide_frames": 3, "plant_height_m": 0.015, "plant_dz_m": 0.004,
    "cloth_depth_min_m": 0.008, "cloth_min_vertices": 22, "cloth_near_m": 0.06,   # 22 頂点より小さい所（結び目・垂れの先）は挙げない
    "merge_gap_frames": 2,
}
ARM_BODY_KEYS = ("upper_arm", "elbow", "forearm", "wrist", "fist")
INTEGER_KEYS = {"pop_window", "jitter_window", "jitter_reversals", "slide_frames", "merge_gap_frames", "cloth_min_vertices"}
FOOT_LOWEST = ("world", "object")   # 足のメッシュの低い点の選び方: 世界の z（既定）か、物体の中の z（試作と同じ。誤り）
LEG_RADIUS_GUESS_M = 0.05           # 脚の太さを測れないとき（その骨にいちばん重い頂点が無い）の推定の太さ（仮の値。試作と同じ）

# 決まった値（パラメーターにしない。試作と同じ）
SLICE_DZ, BINS = 0.02, 24                 # 胴の輪切りの間隔（m）と、向きの数
SLICE_MIN_POINTS = 12                     # 輪切りに使う点の数の下限（足りない高さは 1 つ下の輪切りを写す）
DEFORM_WEIGHT = 0.25                      # 見た目を動かす骨: どれかの頂点にこの重み以上がある骨
HEAD_CENTER_HEIGHT, HEAD_SPHERE = 0.42, 0.33     # 頭の中心の高さ（頭の高さの割合）と、頭の中の球の半径（幅と奥行きの小さい方の割合）
WAIST_MARGIN_M = 0.04                     # 腰の輪切りを使う高さの上の余裕（腰の点）
ARM_NAMES = ("二の腕", "二の腕", "ひじ", "前腕", "前腕", "手首", "こぶし")
ARM_KEYS = ("upper_arm", "upper_arm", "elbow", "forearm", "forearm", "wrist", "fist")
FINGERS = ("thumb", "index", "middle", "ring", "little")
LIMB_ROLES = ("upper_arm", "lower_arm", "upper_leg", "lower_leg")
JP_WORDS = (("left_", "左の"), ("right_", "右の"), ("upper_chest", "胸の上"), ("upper_arm", "二の腕"), ("lower_arm", "前腕"),
            ("upper_leg", "太もも"), ("lower_leg", "すね"), ("shoulder", "肩"), ("hand", "手"), ("foot", "足"), ("toes", "つま先"),
            ("hips", "腰"), ("spine", "腹"), ("chest", "胸"), ("neck", "首"), ("head", "頭"), ("thumb", "親指"), ("index", "人さし指"),
            ("middle", "中指"), ("ring", "薬指"), ("little", "小指"), ("_metacarpal", "の付け根"), ("_proximal", "の付け根"),
            ("_intermediate", "の中"), ("_distal", "の先"), ("eye", "目"))
LABEL = {"penetration.arm_body": ("貫通", "腕が体に入る", "cm"), "penetration.arm_arm": ("貫通", "腕どうしが重なる", "cm"),
         "penetration.arm_leg": ("貫通", "腕が脚に入る", "cm"), "penetration.leg_leg": ("貫通", "脚どうしが重なる", "cm"),
         "penetration.body_cloth": ("貫通", "体が布を突き抜ける", "cm"), "penetration.floor": ("貫通", "足が床より下", "cm"),
         "joint.wrist_bend": ("関節", "手首の曲げすぎ", "度"), "joint.wrist_twist": ("関節", "手首のねじれすぎ", "度"),
         "joint.neck": ("関節", "首の曲げすぎ", "度"), "joint.knee_back": ("関節", "ひざが逆に曲がる", "cm"),
         "motion.pop": ("飛び", "向きが 1 コマで飛ぶ", "度"), "motion.position_pop": ("飛び", "位置が 1 コマだけ飛ぶ", "cm"),
         "motion.jitter": ("震え", "小刻みに震える", "回"), "motion.foot_slide": ("足", "着いた足が滑る", "cm"),
         "motion.cut": ("切り替え", "全身が同じコマで切り替わる", "%")}
SEVERITY_SCALE = {"cm": 1.0, "度": 30.0, "回": 3.0, "%": 1e9}


class SettingsError(ValueError):
    """パラメーターの誤り（工程は ToolError にして止める）。"""


def settings(parameters):
    """しきい値（DEFAULTS に parameters の同じ鍵を重ねたもの）。腕の点の表は形を確かめる（二の腕 3 点・前腕 3 点で、最後の点が u = 1）。"""
    out = {key: (dict(value) if isinstance(value, dict) else value) for key, value in DEFAULTS.items()}
    for key, value in parameters.items():
        if key not in DEFAULTS:
            continue
        if key == "arm_body_min_m":
            if not isinstance(value, dict) or set(value) - set(ARM_BODY_KEYS):
                raise SettingsError(f"arm_body_min_m は {list(ARM_BODY_KEYS)} を鍵にした深さ（m）の表にしてください: {value!r}")
            for name, depth in value.items():
                _number(depth, f"arm_body_min_m.{name}")
            out[key] = {**out[key], **value}
        elif key in ("arm_upper", "arm_lower"):
            if (not isinstance(value, list) or len(value) != 3
                    or not all(isinstance(p, list) and len(p) == 2 for p in value)):
                raise SettingsError(f"{key} は [付け根からの割合 u, 太さの半分 r] の 3 点の並びにしてください: {value!r}")
            us = [_number(u, f"{key} の u", positive=True) for u, _ in value]
            for _, r in value:
                _number(r, f"{key} の r", positive=True)
            if us != sorted(us) or len(set(us)) != 3 or us[-1] != 1.0:
                raise SettingsError(f"{key} の u は 0 より大きく、小さい順に重ならず、最後の点を 1（{'ひじ' if key == 'arm_upper' else '手首'}）"
                                    f"にしてください: {us}")
            out[key] = [list(p) for p in value]
        elif key == "fist":
            if not isinstance(value, list) or len(value) != 2:
                raise SettingsError(f"fist は [手首からこぶしの中心まで, こぶしの太さの半分] にしてください: {value!r}")
            out[key] = [_number(v, "fist", positive=True) for v in value]
        else:
            out[key] = _number(value, key, integer=key in INTEGER_KEYS)
    if out["torso_weight_min"] > 1:
        raise SettingsError(f"torso_weight_min は 0〜1 にしてください: {out['torso_weight_min']}")
    if out["jitter_window"] < 2 or out["pop_window"] < 1 or out["slide_frames"] < 1 or out["jitter_reversals"] < 1:
        raise SettingsError("jitter_window は 2 以上、pop_window・slide_frames・jitter_reversals は 1 以上にしてください")
    if not 0 < out["cut_share"] <= 1:
        raise SettingsError(f"cut_share は 0 より大きく 1 以下にしてください: {out['cut_share']}")
    if out["slide_min_m_per_frame"] > out["slide_max_m_per_frame"]:
        raise SettingsError("slide_min_m_per_frame は slide_max_m_per_frame 以下にしてください")
    return out


def _number(value, name, integer=False, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SettingsError(f"{name} は数にしてください: {value!r}")
    if integer and not isinstance(value, int):
        raise SettingsError(f"{name} は整数にしてください: {value!r}")
    if value < 0 or (positive and value == 0):
        raise SettingsError(f"{name} は {'0 より大きく' if positive else '0 以上に'}してください: {value!r}")
    return value


# ---------------------------------------------------------------- 書き出した値
class Sample:
    """Blender で書き出した値（animation_sample.py）。長さは m、座標は人型のアーマチュアの中（rest・pose・body の co）と世界
    （heads・foot_*）。テストでは合成の値で直接作る。

    - humanoid: {役割: 骨の名前}、bones: 骨の名前（姿勢の骨の順）、parents: 親の名前（無ければ None）、rest: (B, 4, 4) 初期姿勢の行列
    - frames: コマの番号、fps、world: コマごとのアーマチュアの世界の行列 (T, 4, 4)（4×4 を 1 つ渡せば全部のコマで同じ。省くと単位の行列）
    - pose: (T, B, 4, 4) 姿勢の行列、quat: (T, B, 4) その向き（w, x, y, z）、heads: (T, B, 3) 骨の付け根の世界の位置
    - foot_object・foot_world: (T, 2, 3) 左右の足のメッシュの低い点（下の foot_lowest。無いコマは nan）
    - deform: {頂点グループの名前: どれかの頂点の重みの最大}（描くメッシュ全部）
    - body: 体のメッシュの並び {name, co (V, 3), dominant (V,) 頂点ごとのいちばん重い頂点グループの名前（無ければ None）,
      torso (V, 2) 胴の骨の重みの和と全部の重みの和, faces: 面ごとの頂点の番号の並び}
    - cloth: 布の物体の名前の並び。cloth_hits: 体の頂点から骨の軸へ向けた光線が布に当たった所の、コマと (骨, 布) ごとのまとめ
      {frame (N,) コマの番号の位置, bone (N,) 骨の番号, object (N,) 布の番号, count (N,) 頂点の数, depth (N,) いちばん深い所（m）,
      location (N, 3) 位置の平均（世界）, normal (N, 3) 外向き（無ければ nan）}。並びは、コマの中では見つけた順
    """

    def __init__(self, humanoid, bones, parents, rest, frames, fps, pose, quat, heads, foot_object, foot_world, deform, body,
                 world=None, cloth=(), cloth_hits=None, meta=None):
        self.humanoid, self.bones, self.parents = dict(humanoid), list(bones), list(parents)
        self.rest = np.asarray(rest, dtype=np.float64)
        self.frames, self.fps = [int(f) for f in frames], float(fps)
        self.pose = np.asarray(pose, dtype=np.float64)
        self.quat = np.asarray(quat, dtype=np.float64)
        self.heads = np.asarray(heads, dtype=np.float64)
        self.foot_object = np.asarray(foot_object, dtype=np.float64)
        self.foot_world = np.asarray(foot_world, dtype=np.float64)
        self.deform = dict(deform)
        self.body = list(body)
        world = np.eye(4) if world is None else np.asarray(world, dtype=np.float64)
        self.world = np.broadcast_to(world, (len(self.frames), 4, 4)).copy() if world.shape == (4, 4) else world
        self.cloth = list(cloth)
        empty = {"frame": np.zeros(0, int), "bone": np.zeros(0, int), "object": np.zeros(0, int), "count": np.zeros(0, int),
                 "depth": np.zeros(0), "location": np.zeros((0, 3)), "normal": np.zeros((0, 3))}
        self.cloth_hits = {key: np.asarray((cloth_hits or empty)[key]) for key in empty}
        self.meta = dict(meta or {})
        T, B = len(self.frames), len(self.bones)
        shapes = {"rest": (self.rest, (B, 4, 4)), "pose": (self.pose, (T, B, 4, 4)), "quat": (self.quat, (T, B, 4)),
                  "heads": (self.heads, (T, B, 3)), "foot_object": (self.foot_object, (T, 2, 3)),
                  "foot_world": (self.foot_world, (T, 2, 3)), "world": (self.world, (T, 4, 4))}
        for name, (array, shape) in shapes.items():
            if array.shape != shape:
                raise SettingsError(f"書き出した値 {name} の形が {array.shape} です（{shape} のはず）")
        if len(self.parents) != B or len(set(self.bones)) != B:
            raise SettingsError("書き出した骨の名前と親の数が合いません（または名前が重なっています）")
        for mesh in self.body:
            count = len(mesh["dominant"])
            if (np.shape(mesh["co"]) != (count, 3) or np.shape(mesh["torso"]) != (count, 2)
                    or any(len(face) < 3 or min(face) < 0 or max(face) >= count for face in mesh["faces"])):
                raise SettingsError(f"体のメッシュ {mesh.get('name')} の書き出しの形が合いません（頂点 {count} 個）")
        hits = self.cloth_hits
        rows = len(hits["frame"])
        if (any(len(hits[key]) != rows for key in hits) or (rows and (hits["frame"].min() < 0 or hits["frame"].max() >= T
                or hits["bone"].min() < 0 or hits["bone"].max() >= B or hits["object"].min() < 0
                or hits["object"].max() >= len(self.cloth)))):
            raise SettingsError("書き出した布の当たりの形が合いません")


def load_sample(npz_path, meta):
    """animation_sample.py の npz と JSON（meta。読んだ dict）から Sample を作る。"""
    if meta.get("schema_version") != SAMPLE_SCHEMA:
        raise SettingsError(f"書き出した値の形が違います: {meta.get('schema_version')!r}（{SAMPLE_SCHEMA} のはず）")
    with np.load(npz_path, allow_pickle=False) as data:
        arrays = {name: data[name] for name in data.files}
    groups = list(meta["groups"])
    body = []
    for k, entry in enumerate(meta["body"]):
        dominant = [groups[i] if i >= 0 else None for i in arrays[f"body{k}_dominant"].tolist()]
        start, verts = arrays[f"body{k}_face_start"].tolist(), arrays[f"body{k}_face_verts"]
        ends = start[1:] + [len(verts)]
        body.append({"name": entry["name"], "co": arrays[f"body{k}_co"], "dominant": dominant, "torso": arrays[f"body{k}_torso"],
                     "faces": [verts[a:b] for a, b in zip(start, ends)]})
    return Sample(meta["humanoid"], meta["bones"], meta["parents"], arrays["rest"], meta["frames"], meta["fps"], arrays["pose"],
                  arrays["quat"], arrays["heads"], arrays["foot_object"], arrays["foot_world"], meta["deform"], body,
                  world=arrays.get("world", meta.get("armature_world")), cloth=meta.get("cloth_objects", []),
                  cloth_hits={key: arrays[f"cloth_{key}"] for key in ("frame", "bone", "object", "count", "depth", "location", "normal")}
                  if "cloth_frame" in arrays else None, meta=meta)


# ---------------------------------------------------------------- 小さな計算（倍精度）
def _apply(matrix, point):
    return matrix[:3, :3] @ point + matrix[:3, 3]


def _rotate(matrix, normal):
    """向き（長さ 1）をアーマチュアの中から世界へ（回すだけ。アーマチュアの大きさは 1 に限っている）。無ければ None。"""
    return None if normal is None else matrix[:3, :3] @ normal


def _frame_spans(frames):
    """コマの並びを、続いたコマの区間 [[始め, 終わり], …] にする。"""
    spans = []
    for frame in frames:
        if spans and frame == spans[-1][1] + 1:
            spans[-1][1] = frame
        else:
            spans.append([frame, frame])
    return spans


def _normalized(v):
    length = float(np.linalg.norm(v))
    return v / length if length > 0 else v


def seg_closest(p, a, b):
    """点 p から線分 a–b のいちばん近い点までの距離と、その点。"""
    ab = b - a
    u = max(0.0, min(1.0, float(np.dot(p - a, ab)) / max(float(np.dot(ab, ab)), 1e-12)))
    c = a + ab * u
    return float(np.linalg.norm(p - c)), c


def quaternion(m):
    """3×3 の回転の行列（行が出力）→ クォータニオン（w, x, y, z。w ≥ 0）。"""
    m = np.asarray(m, dtype=np.float64)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2.0 * math.sqrt(1.0 + trace)
        q = np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = np.array([(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s])
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = np.array([(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s])
    else:
        s = 2.0 * math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = np.array([(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s])
    q = q / np.linalg.norm(q)
    return -q if q[0] < 0 else q


def rotation_between(a, b):
    """向き a を向き b へ回す、いちばん小さい回転の 3×3 の行列（逆向きなら a に垂直な軸のまわりに 180°）。"""
    a, b = _normalized(np.asarray(a, dtype=np.float64)), _normalized(np.asarray(b, dtype=np.float64))
    axis = np.cross(a, b)
    length = float(np.linalg.norm(axis))
    cosine = max(-1.0, min(1.0, float(np.dot(a, b))))
    if length < 1e-12:
        if cosine > 0:
            return np.eye(3)
        other = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        axis, angle = _normalized(np.cross(a, other)), math.pi
    else:
        axis, angle = axis / length, math.atan2(length, cosine)
    x, y, z = axis
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * (k @ k)


def closest_on_triangles(point, tris):
    """点から三角形の集まり（(N, 3, 3)）のいちばん近い点までの距離（Ericson の方法を並べて計算）。"""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    p = np.asarray(point, dtype=np.float64)
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.einsum("ij,ij->i", ab, ap), np.einsum("ij,ij->i", ac, ap)
    bp = p - b
    d3, d4 = np.einsum("ij,ij->i", ab, bp), np.einsum("ij,ij->i", ac, bp)
    cp = p - c
    d5, d6 = np.einsum("ij,ij->i", ab, cp), np.einsum("ij,ij->i", ac, cp)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = 1.0 / (va + vb + vc)
        v_in, w_in = vb * denom, vc * denom
        out = a + ab * v_in[:, None] + ac * w_in[:, None]                                       # 面の中
        t_ab = d1 / (d1 - d3)
        t_ac = d2 / (d2 - d6)
        t_bc = (d4 - d3) / ((d4 - d3) + (d5 - d6))
    choices = [
        ((d1 <= 0) & (d2 <= 0), a),
        ((d3 >= 0) & (d4 <= d3), b),
        ((d6 >= 0) & (d5 <= d6), c),
        ((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + ab * np.nan_to_num(t_ab)[:, None]),
        ((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + ac * np.nan_to_num(t_ac)[:, None]),
        ((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + (c - b) * np.nan_to_num(t_bc)[:, None]),
    ]
    done = np.zeros(len(tris), dtype=bool)
    for mask, value in choices:
        take = mask & ~done
        out[take] = value[take]
        done |= take
    bad = ~np.isfinite(out).all(axis=1)
    if bad.any():                                                                                 # つぶれた三角形は頂点で
        corners = np.stack([a[bad], b[bad], c[bad]], axis=1)
        out[bad] = corners[np.arange(bad.sum()), np.argmin(np.linalg.norm(corners - p, axis=2), axis=1)]
    return float(np.min(np.linalg.norm(out - p, axis=1)))


# ---------------------------------------------------------------- 骨の役割と体の形（検-2）
class Body:
    """骨の役割と、初期姿勢のメッシュから作った体の形。"""

    def __init__(self, sample, cfg, skip_bones=()):
        self.sample, self.cfg = sample, cfg
        H = sample.humanoid
        missing = [role for role in REQUIRED_ROLES if role not in H]
        if missing:
            raise SettingsError(f"人型の骨の割り当てが足りません: {missing}")
        absent = sorted({bone for bone in H.values() if bone not in sample.bones})
        if absent:
            raise SettingsError(f"人型の骨の割り当てに、アーマチュアに無い骨があります: {absent}")
        self.H = H
        self.index = {name: i for i, name in enumerate(sample.bones)}
        self.parent = dict(zip(sample.bones, sample.parents))
        self.role = {bone: role for role, bone in H.items()}
        self.human = set(H.values())
        self.chest = H.get("upper_chest") or H.get("chest") or H["spine"]
        self.rest = {name: sample.rest[i] for i, name in enumerate(sample.bones)}
        self.rest3 = {name: m[:3, :3] for name, m in self.rest.items()}
        self.deform = {name for name, weight in sample.deform.items() if weight >= DEFORM_WEIGHT}
        self.skip = list(skip_bones)
        self._torso(cfg)
        self._head()
        self._legs()
        self._baseline(cfg)
        self._bones()

    # 胴: 胴の骨の重みが torso_weight_min 以上の頂点を、高さ 2 cm ごとの輪切りの向きごとの半径にする
    def _torso(self, cfg):
        torso_bones = {self.H[role] for role in TORSO_ROLES if role in self.H}
        points = []
        for mesh in self.sample.body:
            inside, total = mesh["torso"][:, 0], mesh["torso"][:, 1]
            for i, name in enumerate(mesh["dominant"]):
                # わきの下のように腕の骨と混ざった所は、T の字の初期姿勢では外へふくらんでいて、腕を下ろすと引っこむ。ここを胴に数えると、
                # 腕を下ろしただけで「胸に入っている」と出る
                if name in torso_bones and total[i] > 0 and inside[i] / total[i] >= cfg["torso_weight_min"]:
                    points.append(mesh["co"][i])
        if not points:
            raise SettingsError("胴の形を作れません（胴の骨に重みのある頂点がありません）")
        points = np.asarray(points, dtype=np.float64)
        self.slices = torso_slices(points)
        if len(self.slices) < 2:
            raise SettingsError(f"胴の輪切りが {len(self.slices)} つしか作れません（2 つ以上が要ります）")
        self.waist_z = (self.rest[self.H["spine"]][2, 3] + self.rest[self.chest][2, 3]) / 2
        self.torso_points = len(points)

    def _head(self):
        head = self.H["head"]
        vertices, triangles = [], []
        for mesh in self.sample.body:
            dominant, co = mesh["dominant"], mesh["co"]
            remap = {}
            for face in mesh["faces"]:
                if all(dominant[i] == head for i in face):
                    ids = []
                    for i in face:
                        j = remap.get(int(i))
                        if j is None:
                            j = remap[int(i)] = len(vertices)
                            vertices.append(co[i])
                        ids.append(j)
                    triangles.extend([ids[0], ids[k], ids[k + 1]] for k in range(1, len(ids) - 1))
        self.head_tris = np.asarray(vertices, dtype=np.float64)[np.asarray(triangles, dtype=np.int64)] if triangles else None
        if self.head_tris is None:
            self.head_center = self.head_radius = self.head_box = None
            return
        v = np.asarray(vertices, dtype=np.float64)
        lo, hi = v.min(axis=0), v.max(axis=0)
        # 試作は mathutils の Vector（単精度）に入れていた
        self.head_center = np.array([np.float32((lo[0] + hi[0]) / 2), np.float32((lo[1] + hi[1]) / 2),
                                     np.float32(lo[2] + (hi[2] - lo[2]) * HEAD_CENTER_HEIGHT)], dtype=np.float64)
        self.head_radius = HEAD_SPHERE * min(hi[0] - lo[0], hi[1] - lo[1])
        self.head_box = (lo, hi)

    def _legs(self):
        self.leg_radius, self.leg_estimated = {}, set()
        for side in SIDES:
            for part, child in (("upper_leg", "lower_leg"), ("lower_leg", "foot")):
                bone = self.H[f"{side}_{part}"]
                a, b = self.rest[bone][:3, 3], self.rest[self.H[f"{side}_{child}"]][:3, 3]
                distances = []
                for mesh in self.sample.body:
                    picked = [i for i, name in enumerate(mesh["dominant"]) if name == bone]
                    if picked:
                        distances.extend(_segment_distances(mesh["co"][picked], a, b).tolist())
                distances.sort()
                if distances:
                    self.leg_radius[(side, part)] = distances[len(distances) // 2]
                else:                                                         # 測れない脚（重みが補助の骨にあるなど）: 推定の太さで見る
                    self.leg_radius[(side, part)] = LEG_RADIUS_GUESS_M
                    self.leg_estimated.add((side, part))

    # その体で避けられない重なり: 腕を自然に下ろした時の、二の腕と胴の重なり。肩幅より胸まわりが広い体では、下ろしただけで重なる。
    # 動きの破綻ではないので、二の腕の点の深さから差し引く
    def _baseline(self, cfg):
        self.baseline = {}
        for side, sign in (("left", 1.0), ("right", -1.0)):
            shoulder = self.rest[self.H[f"{side}_upper_arm"]][:3, 3]
            length = float(np.linalg.norm(self.rest[self.H[f"{side}_lower_arm"]][:3, 3] - shoulder))
            if shoulder[0] * sign < 0:
                sign = -sign
            down = _normalized(np.array([sign * 0.2, -0.05, -1.0]))
            values = []
            for u, r in cfg["arm_upper"]:
                q = shoulder + down * (length * u)
                sd = min(self.torso_sd(q, self.waist_z, self.slices[-1][0])[0],
                         self.torso_sd(q, self.slices[0][0], self.waist_z + WAIST_MARGIN_M)[0])
                values.append(max(0.0, r - sd))
            self.baseline[side] = values

    # 飛びと震えを見る骨: 見た目を動かす骨（どれかの頂点に 0.25 以上の重み）で、親のあるもの。skip_bones の名前は見ない
    def _bones(self):
        def visual_parent(bone):
            b = self.parent[bone]
            while b is not None and b not in self.deform:
                b = self.parent[b]
            return b

        self.pop_bones = [b for b in self.sample.bones if b in self.deform and self.parent[b] is not None
                          and not any(fnmatch.fnmatchcase(b, pattern) for pattern in self.skip)]
        position = {b: i for i, b in enumerate(self.pop_bones)}
        self.pop_parent = [position.get(visual_parent(b), -1) for b in self.pop_bones]
        self.pop_limb = np.array([any(k in self.role.get(b, "") for k in LIMB_ROLES) for b in self.pop_bones], dtype=bool)
        self.pop_human = [i for i, b in enumerate(self.pop_bones)
                          if b in self.human and not any(k in self.role[b] for k in (*FINGERS, "eye"))]

    def torso_sd(self, q, z_lo, z_hi):
        """点 q（初期姿勢の胴から見た位置）の、胴の面からの符号つきの距離（中が負）と、外向きの向き。"""
        slices = self.slices
        zc = max(z_lo, min(z_hi, float(q[2])))
        f = (zc - slices[0][0]) / SLICE_DZ
        i = max(0, min(len(slices) - 2, int(f)))
        u = max(0.0, min(1.0, f - i))
        sd0, n0 = slice_sd(slices[i], q)
        sd1, n1 = slice_sd(slices[i + 1], q)
        sd = sd0 * (1 - u) + sd1 * u
        dz = abs(float(q[2]) - zc)
        if dz > 0:
            sd = math.hypot(max(sd, 0.0), dz) if sd > 0 else dz
        return sd, (n0 if u < 0.5 else n1)

    def jp(self, bone):
        """骨の名前を、読む用の言葉にする（人型の骨だけ。ほかの骨は名前のまま）。"""
        name = self.role.get(bone)
        if name is None:
            return bone
        for a, b in JP_WORDS:
            name = name.replace(a, b)
        return name


def _segment_distances(points, a, b):
    ab = b - a
    u = np.clip((points - a) @ ab / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return np.linalg.norm(points - (a + u[:, None] * ab), axis=1)


def torso_slices(points):
    """胴の点 (N, 3) から、高さ 2 cm ごとの輪切り [(z, 中心 x, 中心 y, 向きごとの半径 24)]。点の少ない高さは 1 つ下の輪切りを写す。"""
    slices = []
    zs = points[:, 2]
    z = float(zs.min()) + SLICE_DZ
    top = float(zs.max())
    while z <= top:
        sel = points[np.abs(zs - z) < SLICE_DZ * 0.75]
        if len(sel) >= SLICE_MIN_POINTS:
            slices.append(slice_of(z, sel))
        elif slices:
            slices.append((z,) + slices[-1][1:])
        z += SLICE_DZ
    return slices


def slice_of(z, sel):
    x0, x1 = float(sel[:, 0].min()), float(sel[:, 0].max())
    y0, y1 = float(sel[:, 1].min()), float(sel[:, 1].max())
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radii = [0.0] * BINS
    for x, y in zip(sel[:, 0].tolist(), sel[:, 1].tolist()):
        dx, dy = x - cx, y - cy
        b = int((math.atan2(dy, dx) + math.pi) / (2 * math.pi) * BINS) % BINS
        radii[b] = max(radii[b], math.hypot(dx, dy))
    for _ in range(BINS):                                          # 点の無い向きは、隣の向きから埋める
        for b in range(BINS):
            if radii[b] <= 0:
                radii[b] = max(radii[b - 1], radii[(b + 1) % BINS]) * 0.98
    radii = [max(radii[b], 0.5 * (radii[b - 1] + radii[(b + 1) % BINS])) for b in range(BINS)]
    return (z, cx, cy, radii)


def slice_sd(sl, q):
    _z, cx, cy, radii = sl
    dx, dy = float(q[0]) - cx, float(q[1]) - cy
    r = math.hypot(dx, dy)
    if r < 1e-6:
        return -min(radii), np.array([0.0, -1.0, 0.0])
    fb = (math.atan2(dy, dx) + math.pi) / (2 * math.pi) * BINS - 0.5
    b0 = int(math.floor(fb)) % BINS
    w = fb - math.floor(fb)
    return r - (radii[b0] * (1 - w) + radii[(b0 + 1) % BINS] * w), np.array([dx / r, dy / r, 0.0])


# ---------------------------------------------------------------- 静的な検査（検-3。1 コマごと）
def static_hits(body, cfg, foot_lowest, hits, contact):
    """1 コマで分かる物を hits に足す（試作と同じ順: コマごとに、左右の腕と体・腕と脚・手首・ひざ・床、腕どうし、脚どうし、首）。"""
    sample, H = body.sample, body.H
    body.estimated_labels = set()                                         # 推定の脚の太さを使った出来事（信号, 場所）
    feet = sample.foot_world if foot_lowest == "world" else sample.foot_object
    regions = (("chest", body.chest), ("hips", H["hips"]), ("head", H["head"]))
    torso_ranges = (("chest", "胸", (body.waist_z, body.slices[-1][0])), ("hips", "腰", (body.slices[0][0], body.waist_z + WAIST_MARGIN_M)))
    arm_mins = [cfg["arm_body_min_m"][key] for key in ARM_KEYS]
    cloth_rows = {}
    for row, fi in enumerate(sample.cloth_hits["frame"].tolist()):
        cloth_rows.setdefault(fi, []).append(row)
    for fi, frame in enumerate(sample.frames):
        P, aw = sample.pose[fi], sample.world[fi]
        M = {role: P[body.index[bone]] for role, bone in H.items()}
        to_rest, to_pose = {}, {}
        for key, bone in regions:
            current = P[body.index[bone]]
            to_rest[key] = body.rest[bone] @ np.linalg.inv(current)
            to_pose[key] = (current @ np.linalg.inv(body.rest[bone]))[:3, :3]

        def body_sd(pt, r):
            best = (9.0, None, "")
            for key, name, (z_lo, z_hi) in torso_ranges:
                sd, n = body.torso_sd(_apply(to_rest[key], pt), z_lo, z_hi)
                if sd < best[0]:
                    best = (sd, to_pose[key] @ n, name)
            if body.head_tris is not None:
                q = _apply(to_rest["head"], pt)
                d = q - body.head_center
                sphere = float(np.linalg.norm(d)) - body.head_radius
                # 面までの距離は、腕の点が頭のそばにあるときだけ測る（遠い点は、接触の深さに届かないので、どちらでも同じ結果）
                lo, hi = body.head_box
                box = float(np.linalg.norm(np.maximum(np.maximum(lo - q, q - hi), 0.0)))
                cutoff = r - cfg["contact_min_m"] + 1e-6
                if min(sphere, box) <= cutoff:
                    sd = min(closest_on_triangles(q, body.head_tris) if box <= cutoff else math.inf, sphere)
                    if sd < best[0]:
                        best = (sd, to_pose["head"] @ (_normalized(d) if np.linalg.norm(d) > 1e-6 else np.array([0.0, 0.0, 1.0])), "頭")
            return best

        points = {}
        for side in SIDES:
            S, E, W = M[f"{side}_upper_arm"][:3, 3], M[f"{side}_lower_arm"][:3, 3], M[f"{side}_hand"][:3, 3]
            hand = H[f"{side}_hand"]
            D = M[f"{side}_hand"][:3, :3] @ np.linalg.inv(body.rest3[hand])
            p = ([(S + (E - S) * u, r) for u, r in cfg["arm_upper"]] + [(E + (W - E) * u, r) for u, r in cfg["arm_lower"]])
            p.append((W + D @ body.rest3[hand][:, 1] * cfg["fist"][0], cfg["fist"][1]))
            points[side] = p
            worst = None
            for k, (pt, r) in enumerate(p):                                   # 腕 → 胴・頭
                sd, n, region = body_sd(pt, r)
                depth = r - sd - (body.baseline[side][k] if k < 3 and region != "頭" else 0.0)
                if depth < cfg["contact_min_m"]:
                    continue
                over = depth - (cfg["penetration_min_m"] if region == "頭" else arm_mins[k])     # 挙げ始める深さを超えた分
                if worst is None or over > worst[4]:
                    worst = (depth, f"{SIDE[side]}の{ARM_NAMES[k]} → {region}", pt + n * max(0.0, -sd) if n is not None else pt, n, over)
            if worst and worst[4] >= 0:
                hits.append((frame, "penetration.arm_body", worst[1], worst[0], _apply(aw, worst[2]), _rotate(aw, worst[3])))
            elif worst:
                contact["腕と体"] = contact.get("腕と体", 0) + 1
            worst = None
            for k in range(3, 7):                                             # 前腕・手首・こぶし → 脚
                pt, r = p[k]
                for side2 in SIDES:
                    for part, child, word in (("upper_leg", "lower_leg", "太もも"), ("lower_leg", "foot", "すね")):
                        dist, c = seg_closest(pt, M[f"{side2}_{part}"][:3, 3], M[f"{side2}_{child}"][:3, 3])
                        depth = r + body.leg_radius[(side2, part)] - dist
                        if depth >= cfg["contact_min_m"] and (worst is None or depth > worst[0]):
                            worst = (depth, f"{SIDE[side]}の{ARM_NAMES[k]} → {SIDE[side2]}の{word}", pt,
                                     _normalized(pt - c) if dist > 1e-6 else None, (side2, part) in body.leg_estimated)
            if worst and worst[0] >= cfg["penetration_min_m"]:
                hits.append((frame, "penetration.arm_leg", worst[1], worst[0], _apply(aw, worst[2]), _rotate(aw, worst[3])))
                if worst[4]:
                    body.estimated_labels.add(("penetration.arm_leg", worst[1]))
            elif worst:
                contact["腕と脚"] = contact.get("腕と脚", 0) + 1
            # 手首: 二の腕から見て、軸まわりに回っていない前腕の向き（前腕の骨そのものの軸まわりの向きには頼らない。前腕の骨が軸まわりに
            # 裏返っても、見た目はねじれを分ける補助の骨が決めていて変わらないことがある）から見た手の回り
            lower, upper = H[f"{side}_lower_arm"], H[f"{side}_upper_arm"]
            axis = _normalized(body.rest3[lower][:, 1])
            D_up = M[f"{side}_upper_arm"][:3, :3] @ np.linalg.inv(body.rest3[upper])
            D_lo = rotation_between(D_up @ axis, _normalized(W - E)) @ D_up
            q = quaternion(D_lo.T @ D)
            proj = float(np.dot(q[1:], axis))
            twist = (2 * math.atan2(proj, q[0]) + math.pi) % (2 * math.pi) - math.pi
            swing = 2 * math.acos(min(1.0, math.sqrt(q[0] * q[0] + proj * proj)))
            if math.degrees(swing) > cfg["wrist_bend_max_deg"]:
                hits.append((frame, "joint.wrist_bend", f"{SIDE[side]}の手首", math.degrees(swing), _apply(aw, W), None))
            if abs(math.degrees(twist)) > cfg["wrist_twist_max_deg"]:
                hits.append((frame, "joint.wrist_twist", f"{SIDE[side]}の手首", abs(math.degrees(twist)), _apply(aw, W), None))
            # ひざが逆に曲がる（ひざが、股と足首を結ぶ線より後ろ）
            thigh = H[f"{side}_upper_leg"]
            hip, knee, ankle = M[f"{side}_upper_leg"][:3, 3], M[f"{side}_lower_leg"][:3, 3], M[f"{side}_foot"][:3, 3]
            forward = (M[f"{side}_upper_leg"][:3, :3] @ np.linalg.inv(body.rest3[thigh])) @ np.array([0.0, -1.0, 0.0])
            _d, c = seg_closest(knee, hip, ankle)
            back = -float(np.dot(knee - c, forward))
            if back > cfg["knee_back_min_m"]:
                hits.append((frame, "joint.knee_back", f"{SIDE[side]}のひざ", back, _apply(aw, knee), None))
            # 床（足のメッシュの低い点が、床 z = 0 より下）
            lowest = feet[fi, 0 if side == "left" else 1]
            if np.isfinite(lowest).all() and -lowest[2] > cfg["floor_min_m"]:
                hits.append((frame, "penetration.floor", f"{SIDE[side]}の足", -float(lowest[2]), lowest.copy(), None))
        worst = None
        for k, (pt, r) in enumerate(points["left"][1:5]):                    # 腕 ↔ 腕（手首とこぶしは見ない。道具の上で隣り合う）
            for k2, (q2, r2) in enumerate(points["right"][1:5]):
                depth = r + r2 - float(np.linalg.norm(pt - q2))
                if depth >= cfg["contact_min_m"] and (worst is None or depth > worst[0]):
                    worst = (depth, f"左の{ARM_NAMES[k + 1]} ↔ 右の{ARM_NAMES[k2 + 1]}", (pt + q2) / 2)
        if worst and worst[0] >= cfg["penetration_min_m"]:
            hits.append((frame, "penetration.arm_arm", worst[1], worst[0], _apply(aw, worst[2]), None))
        elif worst:
            contact["腕どうし"] = contact.get("腕どうし", 0) + 1
        worst = None
        legs = (("upper_leg", "lower_leg", "太もも"), ("lower_leg", "foot", "すね"))
        for part, child, word in legs:                                         # 脚 ↔ 脚
            a, b = M[f"left_{part}"][:3, 3], M[f"left_{child}"][:3, 3]
            for u in (0.4, 0.6, 0.8, 1.0):
                pt = a + (b - a) * u
                for part2, child2, word2 in legs:
                    dist, c = seg_closest(pt, M[f"right_{part2}"][:3, 3], M[f"right_{child2}"][:3, 3])
                    r1, r2 = body.leg_radius[("left", part)], body.leg_radius[("right", part2)]
                    depth = r1 + r2 - dist
                    limit = cfg["leg_overlap_min_m"]
                    if part == "upper_leg" and part2 == "upper_leg":     # 太ももは、片方の軸が相手の中ほどまで入った時だけ
                        limit = max(limit, r1 + r2 - 0.5 * min(r1, r2))
                    if depth >= limit and (worst is None or depth > worst[0]):
                        worst = (depth, f"左の{word} ↔ 右の{word2}", (pt + c) / 2,
                                 ("left", part) in body.leg_estimated or ("right", part2) in body.leg_estimated)
        if worst:
            hits.append((frame, "penetration.leg_leg", worst[1], worst[0], _apply(aw, worst[2]), None))
            if worst[3]:
                body.estimated_labels.add(("penetration.leg_leg", worst[1]))
        head, chest = H["head"], body.chest
        rel = (M["head"][:3, :3] @ np.linalg.inv(body.rest3[head])).T @ (P[body.index[chest]][:3, :3] @ np.linalg.inv(body.rest3[chest]))
        neck = math.degrees(2 * math.acos(min(1.0, abs(quaternion(rel)[0]))))
        if neck > cfg["neck_max_deg"]:
            hits.append((frame, "joint.neck", "首", neck, _apply(aw, M["head"][:3, 3]), None))
        for row in cloth_rows.get(fi, ()):                                     # 体 → 布（22 頂点より小さい所は挙げない）
            count = int(sample.cloth_hits["count"][row])
            if count >= cfg["cloth_min_vertices"]:
                bone = sample.bones[int(sample.cloth_hits["bone"][row])]
                normal = sample.cloth_hits["normal"][row]
                hits.append((frame, "penetration.body_cloth", f"{body.jp(bone)} → {sample.cloth[int(sample.cloth_hits['object'][row])]}",
                             float(sample.cloth_hits["depth"][row]), sample.cloth_hits["location"][row].copy(),
                             normal.copy() if np.isfinite(normal).all() else None, count))


# ---------------------------------------------------------------- 動的な検査（検-5。前後のコマと比べる）
def dynamic_hits(body, cfg, foot_lowest_unused, hits):
    """前後のコマと比べて分かる物を hits に足す（試作と同じ順: 全身の切り替え、向きの飛び、位置の飛び、震え、足の滑り）。
    向きは骨ごとの部位そのものの向き（姿勢の行列のクォータニオン。アーマチュアの中）で測る。"""
    sample, H = body.sample, body.H
    frames, T = sample.frames, len(sample.frames)
    if T < 2:
        return []
    columns = [body.index[b] for b in body.pop_bones]
    Q = sample.quat[:, columns]
    HP = sample.pose[:, columns][:, :, :3, 3]          # 骨の付け根の位置（アーマチュアの中。速く振っているコマを見分ける用）
    world_heads = sample.heads[:, columns]
    # コマ t-1 → t の回り（その部位の中から見た回り d = q0 の逆 × q1）。腕・脚の筒は、軸（Y）まわりの回りを除いた「向きの振れ」だけを
    # 見る（筒は軸まわりに回っても見た目がほぼ変わらず、骨の軸まわりの向きだけが 180° 裏返ることがある）。手・足・頭・胴は回り全体
    w0, v0, w1, v1 = Q[:-1, :, 0], Q[:-1, :, 1:], Q[1:, :, 0], Q[1:, :, 1:]
    dw = w0 * w1 + np.sum(v0 * v1, axis=2)
    dv = w0[..., None] * v1 - w1[..., None] * v0 - np.cross(v0, v1)
    full = np.degrees(2 * np.arccos(np.abs(dw).clip(0, 1)))
    swing_only = np.degrees(2 * np.arccos(np.sqrt(dw * dw + dv[..., 1] ** 2).clip(0, 1)))
    ANG = np.vstack([np.zeros((1, len(columns))), np.where(body.pop_limb[None, :], swing_only, full)])
    human = body.pop_human
    share = (ANG[:, human] >= cfg["cut_bone_deg"]).mean(axis=1) if human else np.zeros(T)
    cuts = [t for t in range(1, T) if share[t] >= cfg["cut_share"]]
    near_cut = set()
    hips_column = body.index[H["hips"]]
    for t in cuts:
        near_cut.update((t - 1, t, t + 1))
        hits.append((frames[t], "motion.cut", "全身", float(share[t] * 100), sample.heads[t, hips_column].copy(), None))

    w = cfg["pop_window"]
    for bi, bone in enumerate(body.pop_bones):
        is_human = bone in body.human
        smallest, ratio = ((cfg["pop_min_deg"], cfg["pop_ratio"]) if is_human else (cfg["spring_pop_min_deg"], cfg["spring_pop_ratio"]))
        col = ANG[:, bi]
        for t in np.nonzero(col >= smallest)[0]:
            t = int(t)
            if t in near_cut or t < 1:
                continue
            around = [col[k] for k in range(max(1, t - w), min(T, t + w + 1)) if k not in (t - 1, t, t + 1)]
            med = float(np.median(around)) if around else 0.0
            if col[t] < ratio * max(med, 3.0):
                continue
            parent = body.pop_parent[bi]
            if parent >= 0 and ANG[t, parent] >= 0.5 * col[t]:              # 親が同じコマで飛んでいる（元は親。子は巻き添え）
                continue
            back = t + 1 < T and _quat_angle(Q[t - 1, bi], Q[t + 1, bi]) <= 0.5 * col[t]
            swing = float(np.linalg.norm(HP[t, bi] - HP[t - 1, bi])) >= cfg["swing_m_per_frame"]   # 腕ごと速く振っているコマは、戻る時だけ
            if back or (col[t] >= 1.5 * smallest and not swing):
                role = body.role.get(bone, "")
                if any(k in role for k in FINGERS):
                    part = "左手の指" if role.startswith("left") else "右手の指"          # 指は 1 本ずつ挙げず、手ごとにまとめる
                else:
                    part = ("%s（1 コマで戻る）" if back else "%s（切り替わる）") % body.jp(bone)
                hits.append((frames[t], "motion.pop", part, float(col[t]), world_heads[t, bi].copy(), None))
    joints = {"左手": "left_hand", "右手": "right_hand", "左ひじ": "left_lower_arm", "右ひじ": "right_lower_arm", "頭": "head",
              "左足": "left_foot", "右足": "right_foot", "左ひざ": "left_lower_leg", "右ひざ": "right_lower_leg", "腰": "hips"}
    for name, role in joints.items():
        p = sample.heads[:, body.index[H[role]]]
        for t in range(1, T - 1):
            if t in near_cut:
                continue
            dev = float(np.linalg.norm(p[t] - (p[t - 1] + p[t + 1]) / 2))
            if dev >= cfg["pos_pop_min_m"] and np.linalg.norm(p[t + 1] - p[t - 1]) <= 0.6 * np.linalg.norm(p[t] - p[t - 1]):
                hits.append((frames[t], "motion.position_pop", name, dev, p[t].copy(), None))
    # 震え: 骨の回りの向きが、短い間に何度も逆になる
    RV = np.zeros((T, len(columns), 3))
    for t in range(1, T):
        for bi in human:
            a, b = Q[t - 1, bi], Q[t, bi]
            if np.dot(a, b) < 0:
                b = -b
            w0_, v0_ = a[0], -a[1:]                                  # a の逆 × b のベクトルの部分（小さい回りなら、回りの軸 × 角度の半分）
            RV[t, bi] = w0_ * b[1:] + b[0] * v0_ + np.cross(v0_, b[1:])
    jw, jmin = cfg["jitter_window"], math.sin(math.radians(cfg["jitter_min_deg"]) / 2)
    for bi in human:
        rev = np.zeros(T, dtype=bool)
        for t in range(2, T):
            a, b = RV[t - 1, bi], RV[t, bi]
            rev[t] = np.linalg.norm(a) >= jmin and np.linalg.norm(b) >= jmin and np.dot(a, b) < 0
        for t in range(jw, T):
            if t in near_cut:
                continue
            reversals = int(rev[t - jw + 1:t + 1].sum())
            if reversals >= cfg["jitter_reversals"]:
                hits.append((frames[t - jw // 2], "motion.jitter", body.jp(body.pop_bones[bi]), float(reversals),
                             world_heads[t - jw // 2, bi].copy(), None))
    # 着いた足が滑る（足と、つま先の骨の付け根の低い方。床の近くで、横へ 1 コマに slide_min〜slide_max 動くコマが slide_frames 以上続く）
    for side in SIDES:
        low_bones = [H[f"{side}_foot"]] + ([H[f"{side}_toes"]] if f"{side}_toes" in H else [])
        candidates = sample.heads[:, [body.index[b] for b in low_bones]]
        p = candidates[np.arange(T), np.argmin(candidates[:, :, 2], axis=1)]
        # 床に着いている: 足の低い骨の付け根が、初期姿勢の高さ（そのコマのアーマチュアの置き場所で）+ plant_height_m 以下
        rest_low = np.array([min(_apply(sample.world[t], body.rest[b][:3, 3])[2] for b in low_bones) for t in range(T)])
        planted = p[:, 2] <= rest_low + cfg["plant_height_m"]
        run = []
        for t in range(1, T):
            step = float(np.linalg.norm(p[t, :2] - p[t - 1, :2]))
            ok = (planted[t] and planted[t - 1] and abs(p[t, 2] - p[t - 1, 2]) <= cfg["plant_dz_m"] and t not in near_cut
                  and cfg["slide_min_m_per_frame"] <= step <= cfg["slide_max_m_per_frame"])
            if ok:
                run.append(t)
            if (not ok or t == T - 1) and run:
                if len(run) >= cfg["slide_frames"]:
                    total = float(np.linalg.norm(p[run[-1], :2] - p[run[0] - 1, :2]))
                    for k in run:
                        hits.append((frames[k], "motion.foot_slide", f"{SIDE[side]}の足", total, p[k].copy(), None))
                run = []
    return [frames[t] for t in cuts]


def _quat_angle(a, b):
    return math.degrees(2 * math.acos(min(1.0, abs(float(np.dot(a, b))))))


# ---------------------------------------------------------------- 出来事にまとめる（検-1）
def events_from(hits, frames, fps, merge_gap):
    """同じ信号・同じ場所の続いたコマ（merge_gap コマ以下のすき間）を 1 件にまとめる。いちばん大きいコマの値と場所を出し、
    静的な物が 2 コマ以下なら「一瞬」と印を付ける。並びは最初のコマ・信号の順で、番号を付ける。"""
    groups = {}
    for hit in hits:
        groups.setdefault((hit[1], hit[2]), []).append(hit)
    events = []
    for (signal, part), group in groups.items():
        group.sort(key=lambda h: h[0])
        run = [group[0]]
        for hit in group[1:] + [None]:
            if hit is not None and hit[0] - run[-1][0] <= merge_gap + 1:
                run.append(hit)
                continue
            peak = max(run, key=lambda r: r[3])
            category, title, unit = LABEL[signal]
            value = peak[3] * 100 if unit == "cm" else peak[3]
            static = signal.startswith("penetration") or signal.startswith("joint")
            event = {"category": category, "signal": signal, "title": title, "part": part, "frames": [run[0][0], run[-1][0]],
                     "seconds": [round((run[0][0] - frames[0]) / fps, 2), round((run[-1][0] - frames[0]) / fps, 2)],
                     "peak_frame": peak[0], "peak_value": round(value, 1), "unit": unit, "kind": "静的" if static else "動的",
                     "momentary": bool(static and run[-1][0] - run[0][0] + 1 <= 2), "location": [round(float(v), 4) for v in peak[4]],
                     "normal": [round(float(v), 3) for v in peak[5]] if peak[5] is not None else None,
                     "evidence": "measured", "status": "review"}
            if signal == "penetration.body_cloth":
                event["vertices"] = max(r[6] for r in run)
            events.append(event)
            run = [hit] if hit is not None else []
    for event in events:
        weight = 0.4 if event["category"] == "足" else (0.02 if "の指" in event["part"] else 1.0)   # 指の飛びは多いので、絵の順番では後ろへ
        event["severity"] = round(event["peak_value"] / SEVERITY_SCALE[event["unit"]] * weight, 2)
    events.sort(key=lambda e: (e["frames"][0], e["signal"]))
    for i, event in enumerate(events):
        event["id"] = i + 1
    return events


def check(sample, cfg, skip_bones=(), foot_lowest="world"):
    """全コマを調べて報告（model3d-animation-check/0.1 の中身）を返す。"""
    if foot_lowest not in FOOT_LOWEST:
        raise SettingsError(f"foot_lowest は {' か '.join(FOOT_LOWEST)} にしてください: {foot_lowest!r}")
    body = Body(sample, cfg, skip_bones)
    hits, contact = [], {}
    static_hits(body, cfg, foot_lowest, hits, contact)
    cuts = dynamic_hits(body, cfg, foot_lowest, hits)
    events = events_from(hits, sample.frames, sample.fps, cfg["merge_gap_frames"])
    counts = {}
    for event in events:
        counts[event["signal"]] = counts.get(event["signal"], 0) + 1
        if (event["signal"], event["part"]) in body.estimated_labels:       # 推定の脚の太さで測った重なりは、実測と分ける
            event["evidence"] = "estimated"
    skipped = []
    if body.head_tris is None:
        skipped.append({"check": "head", "reason": "頭の骨だけに重みのある面がありません（腕と頭の重なりは、頭の中の球だけで見ない）"})
    feet = sample.foot_world if foot_lowest == "world" else sample.foot_object
    for k, side in enumerate(SIDES):
        missing = ~np.isfinite(feet[:, k]).all(axis=1)
        if missing.all():
            skipped.append({"check": f"floor_{side}", "reason": f"{SIDE[side]}の足のメッシュの点がありません（足が床より下は見ない）"})
        elif missing.any():                                               # 頂点を足し引きする変形のコマなど
            skipped.append({"check": f"floor_{side}", "reason": f"{SIDE[side]}の足のメッシュの点が無いコマがあります（そのコマは足が床より下を見ない）",
                            "frames": _frame_spans([sample.frames[i] for i in np.where(missing)[0]])})
    measured = sample.meta.get("cloth_measured")
    if not sample.cloth:
        skipped.append({"check": "cloth", "reason": "布（揺れものの骨で動くメッシュ）がありません（体が布を突き抜けるは見ない）"})
    elif measured is False:
        skipped.append({"check": "cloth", "reason": "cloth: false（体が布を突き抜けるは見ない）"})
    elif sample.meta.get("cloth_watched") == 0:
        skipped.append({"check": "cloth", "reason": "初期姿勢で布の近くにある体の頂点がありません（体が布を突き抜けるは見ない）"})
    return {"schema_version": SCHEMA, "frames": [sample.frames[0], sample.frames[-1]] if sample.frames else [], "fps": sample.fps,
            "overall": "review" if events else ("clean_with_skips" if skipped else "clean"),
            "thresholds": cfg, "skip_bones": list(skip_bones), "foot_lowest": foot_lowest,
            "bones": len(body.pop_bones), "body_objects": [mesh["name"] for mesh in sample.body], "cloth_objects": list(sample.cloth),
            "cloth": {"measured": measured, "watched_vertices": sample.meta.get("cloth_watched"),
                      "tucked_vertices": sample.meta.get("cloth_tucked")},
            "torso": {"points": body.torso_points, "slices": len(body.slices)},
            "head_triangles": 0 if body.head_tris is None else int(len(body.head_tris)),
            "leg_radius_m": {f"{side}_{part}": round(value, 4) for (side, part), value in body.leg_radius.items()},
            "leg_radius_estimated": sorted(f"{side}_{part}" for side, part in body.leg_estimated),
            "cuts": cuts, "contact_frames": contact,
            "arm_torso_baseline_m": {side: [round(v, 4) for v in values] for side, values in body.baseline.items()},
            "counts": counts, "skipped": skipped, "events": events}
