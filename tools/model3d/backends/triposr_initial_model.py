"""設定画（前処理済み RGB PNG）→ 初期モデル。固定した公開 TripoSR のネットワークと重みを使う（別の仮想環境で動かす）。

自作: ジョブの入出力、密度場からメッシュへの処理の再構成、元の抽出との比較、記録。
再利用（呼ぶだけ。コードは写さない）: TripoSR のネットワークと公開の重み（MIT）、密度と色の照会、torchmcubes の
marching cubes（MPL-2.0）、PyTorch、trimesh。ニューラルモデルの独立再実装や再学習はしない。

    python triposr_initial_model.py PREPARED.png OUT_DIR --source TripoSR_DIR --weights WEIGHTS_DIR
           [--resolution 128] [--threshold 25.0] [--seed 20261103] [--threads 4] [--chunk-size 4096] [--verify-original]

OUT_DIR に initial_model.obj / initial_model.glb / result.json（--verify-original なら original_same_scene.glb も）を書く。
前処理（前景の切り出し・余白・灰色への合成）は model3d 側で済ませた PNG を渡す（この script は再び前処理しない）。
2026-11-03 の検証（ユーザーの環境、CPU）では、元の CLI の出力と座標・向き付き三角形・RGBA が許容差 0 で一致した。
**この script は Orpheus の CI では動かない**（torch も重みも無い）。依存の入れ方は backends/README.md。
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

PINNED = {"TripoSR": "107cefdc244c39106fa830359024f6a2f1c78871", "TripoSR_HF": "5b521936b01fbe1890f6f9baed0254ab6351c04a",
          "weight_sha256": "429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee",
          "torchmcubes": "a9ac95aa329957ba646e7bb057f825f5dfa77f38"}


def canonical(vertices, faces, colors):
    """頂点番号と面の列挙順だけが違う出力を同じにする（座標で並べ替え、三角形は巡回の最小の並び。向きは保つ）。"""
    positions = np.asarray(vertices)
    if len(np.unique(positions, axis=0)) != len(positions):
        raise ValueError("座標の重なる頂点があるので、この比較は使えない（位相を見る比較が要る）")
    permutation = np.lexsort(positions.T[::-1])
    inverse = np.argsort(permutation)
    reindexed = inverse[np.asarray(faces)]
    triangles = np.array(sorted(min(tuple(row), tuple(np.roll(row, 1)), tuple(np.roll(row, 2))) for row in reindexed), dtype=np.int64)
    return positions[permutation], triangles, (np.asarray(colors)[permutation] if colors is not None else None)


def compare_geometry(left, right):
    lv, lf, lc = canonical(left.vertices, left.faces, left.visual.vertex_colors)
    rv, rf, rc = canonical(right.vertices, right.faces, right.visual.vertex_colors)
    checks = {"vertex_count_equal": len(lv) == len(rv), "face_count_equal": len(lf) == len(rf),
              "canonical_vertices_exact": bool(np.array_equal(lv, rv)), "canonical_oriented_faces_exact": bool(np.array_equal(lf, rf)),
              "canonical_vertex_rgba_exact": bool(np.array_equal(lc, rc))}
    return {"method": "exact canonical vertices, oriented face multiset, aligned RGBA", "tolerance": 0,
            "vertices": [len(lv), len(rv)], "triangles": [len(lf), len(rf)], "checks": checks, "passed": all(checks.values())}


def reconstruct_extraction(model, scene, resolution, threshold, marching_cubes, trimesh, torch):
    """TSR.extract_mesh を呼ばずに、密度の照会 → marching cubes → 頂点色の照会 を自作の順で行う（公開の MC カーネルは使う）。"""
    positions = torch.linspace(0.0, 1.0, resolution)
    grid = torch.stack(torch.meshgrid(positions, positions, positions, indexing="ij"), dim=-1).reshape(-1, 3)
    radius = model.renderer.cfg.radius
    grid = grid * (2 * radius) - radius
    density = model.renderer.query_triplane(model.decoder, grid, scene)["density_act"]
    field = (density - threshold).reshape(resolution, resolution, resolution)
    vertices, faces = marching_cubes(field, 0.0)
    vertices = vertices[:, [2, 1, 0]] / (resolution - 1.0)
    vertices = vertices * (2 * radius) - radius
    colors = model.renderer.query_triplane(model.decoder, vertices, scene)["color"]
    return trimesh.Trimesh(vertices=vertices.numpy(), faces=faces.numpy(), vertex_colors=colors.numpy(), process=False)


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def verify_source(source):
    """固定 commit の未変更の Git checkout だけを実行する。宣言文字列を実測の版として記録しない。"""
    source = Path(source).resolve()

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True,
                                  text=True, encoding="utf-8", timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("TripoSR source の Git checkout を検証できません（固定 commit の checkout が必要です）") from error

    if Path(git("rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("--source は TripoSR の Git checkout のルートを指定してください")
    commit = git("rev-parse", "HEAD")
    if commit != PINNED["TripoSR"]:
        raise ValueError(f"TripoSR source commit が固定した値と違います: {commit}")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("TripoSR source に未コミットの変更か未追跡ファイルがあります")
    files = {str(p.relative_to(source).as_posix()): digest(p) for p in sorted((source / "tsr").rglob("*.py"))}
    if "tsr/system.py" not in files:
        raise ValueError("TripoSR source に tsr/system.py がありません")
    return {"path": str(source), "commit": commit, "clean": True, "python_files_sha256": files}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("prepared", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--source", type=Path, required=True, help="固定 commit の TripoSR のディレクトリ")
    parser.add_argument("--weights", type=Path, required=True, help="config.yaml と model.ckpt のあるディレクトリ")
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument("--threshold", type=float, default=25.0)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=4096)
    parser.add_argument("--verify-original", action="store_true", help="元の extract_mesh とも比べる（時間が増える）")
    args = parser.parse_args(argv)
    # 重みと source の照合が済むまで、外部のコードやニューラルの依存を import / load しない。
    try:
        weight_sha = digest(args.weights / "model.ckpt")
        if weight_sha != PINNED["weight_sha256"]:
            raise ValueError(f"model.ckpt の sha256 が固定した値と違います: {weight_sha}")
        source = verify_source(args.source)
        config_sha = digest(args.weights / "config.yaml")
    except (OSError, ValueError) as error:
        print(f"backend input verification failed: {error}", file=sys.stderr)
        return 2
    import torch
    import trimesh
    from PIL import Image
    from torchmcubes import marching_cubes
    sys.path.insert(0, str(args.source.resolve()))
    from tsr.system import TSR   # noqa: E402 - 固定 commit の TripoSR

    started = time.perf_counter()
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    image = Image.open(args.prepared).convert("RGB")
    print("loading the pinned public model and weights", flush=True)
    model = TSR.from_pretrained(str(args.weights), "config.yaml", "model.ckpt")
    model.renderer.set_chunk_size(args.chunk_size)
    loaded = time.perf_counter()
    print("running image-conditioned inference", flush=True)
    with torch.no_grad():
        scene = model([image], device="cpu")
        inferred = time.perf_counter()
        print("reconstructed extraction", flush=True)
        mesh = reconstruct_extraction(model, scene[0], args.resolution, args.threshold, marching_cubes, trimesh, torch)
        extracted = time.perf_counter()
        comparison = None
        if args.verify_original:
            print("comparing with the original extraction", flush=True)
            original = model.extract_mesh(scene, True, resolution=args.resolution, threshold=args.threshold)[0]
            original.export(args.out_dir / "original_same_scene.glb")
            try:
                comparison = {"same_scene_code": True, **compare_geometry(mesh, original)}
            except ValueError as error:
                comparison = {"same_scene_code": True, "passed": False, "error": str(error)}
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        print("the extracted mesh is empty or not finite", file=sys.stderr)
        return 3
    mesh.export(args.out_dir / "initial_model.glb")
    mesh.export(args.out_dir / "initial_model.obj")
    comparison_failed = comparison is not None and not comparison["passed"]
    result = {"backend": "TripoSR original network + public weights, CPU float32", "expected_pins": PINNED,
              "source": source, "config_sha256": config_sha,
              "pin_verification": {"source_commit_and_clean_checkout": True, "weight_sha256": True,
                                   "config_expected_hash": False, "torchmcubes_commit": False},
              "status": "failed" if comparison_failed else "succeeded",
              "weight_sha256": weight_sha, "single_view_only": True, "input_sha256": digest(args.prepared),
              "resolution": args.resolution, "density_threshold": args.threshold, "seed": args.seed,
              "scene_shape": list(scene.shape), "scene_sha256": hashlib.sha256(scene.numpy().tobytes()).hexdigest(),
              "vertices": int(len(mesh.vertices)), "triangles": int(len(mesh.faces)),
              "bounds": mesh.bounds.tolist(), "extent": mesh.extents.tolist(), "watertight": bool(mesh.is_watertight),
              "winding_consistent": bool(mesh.is_winding_consistent), "components": len(mesh.split(only_watertight=False)),
              "original_extraction_comparison": comparison,
              "seconds": {"load": loaded - started, "inference": inferred - loaded, "extraction": extracted - inferred,
                          "total": time.perf_counter() - started},
              "output_files": {}}
    for path in sorted(args.out_dir.glob("*.glb")) + sorted(args.out_dir.glob("*.obj")):
        result["output_files"][path.name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    (args.out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("vertices", "triangles", "components", "watertight", "winding_consistent")}), flush=True)
    if comparison_failed:
        print("original extraction comparison failed; see result.json", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
