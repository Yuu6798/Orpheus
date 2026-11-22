"""出来事の光: そのコマに効いている出来事の光の候補（強さ・位置・色）を集め、強い順に、決まった数の点の光へ割り当てる。
計算だけ（render.show のフックが Blender の中で `import fx_lights` で読む。Blender の側は fx_blender.apply_lights が光に書く）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、出来事が舞台と踊り手を照らす光の割り当て方を移した。光の数を増やすと、影の
領域があふれる（影つきの点の光）ので、数を決めて強い物から割り当てる。光の台本の値で打つだけの光（窓の外・拍で打つ光）は、
render.show の lights（光の物体の名前 → 値の名前と W）で足りる。

    assigned = assign(glows, count, floor)      count 個の (強さ, 位置, 色) か None（その光を消す）
"""


def assign(glows, count, floor):
    """glows: [(強さ W, 位置 (3,), 色 (3,)), …]。強い順（同じ強さなら並びの順）に count 個まで。floor 以下の物は割り当てない。"""
    ranked = sorted((g for g in glows if g is not None), key=lambda g: -g[0])
    out = []
    for k in range(int(count)):
        if k < len(ranked) and ranked[k][0] > floor:
            energy, position, colour = ranked[k]
            out.append((float(energy), [float(v) for v in position], [float(v) for v in colour]))
        else:
            out.append(None)
    return out
