"""本次视频的 CPU SfM、圆柱规则化观察面与候选定位。

原始观测面仍完全来自本次视频的 COLMAP 多帧几何；另外根据观测点和相机
轨迹拟合一个圆柱断面，作为与历史案例一致的隧道观察模型。圆柱面是显示
与复核用的几何先验，不是把缺失观测伪装成实测工程表面。未注册帧及没有
原始局部支撑的像素仍保留空坐标。独立子进程隔离原生库故障。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import Delaunay, QhullError, cKDTree

ROOT = Path(__file__).resolve().parent
METHOD = "sparse_observed_surface_ray_intersection"
LIMITS = [
    "本次视频采用 CPU 多帧几何重建；仅导出最大连通分量，未注册帧不会被伪造位姿。",
    "灰色地标与原始三角面来自稀疏多帧观测；原始面是插值支撑，不是完整或稠密的隧道表面。",
    "观察模型默认显示由本次观测点拟合的纯灰色圆柱断面；现场图像纹理仅用于原始观测面，缺失墙面属于显示先验，原始观测面可随时切换对照。",
    "候选只在对应帧有局部几何支撑时计算射线交点；缺少支撑的像素与帧保持未定位。",
    "单目尺度未标定，坐标使用相对重建单位；不能换算为实际里程、裂缝尺寸或工程精度。",
    "补抽帧仅用于几何重建，没有再次检测；红色仍是原检测结果中的待复核候选。",
]


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")


def emit(phase, percent, **values):
    print("SLZJ_RECONSTRUCTION " + json.dumps({"phase": phase, "percent": percent, **values}, ensure_ascii=False), flush=True)


def validate_source(review, detections):
    if detections.get("coordinate_space") != "original_image_pixels":
        raise ValueError("候选必须使用原图像素坐标。")
    original_id = detections.get("scene_id")
    if original_id and review.get("jobId") and original_id != review["jobId"]:
        raise ValueError("原检测与复核成果不属于同一视频任务。")
    names = {f["name"] for f in review.get("frames", [])}
    if any(d.get("image_name") not in names for d in detections.get("detections", [])):
        raise ValueError("候选帧与本次检测抽帧不一致，不能串用其他视频结果。")


def prepare_frames(job_dir, out, max_frames):
    review = json.loads((job_dir / "review.json").read_text("utf-8"))
    known = review["frames"]
    validate_source(review, json.loads((job_dir / "detect" / "detections.json").read_text("utf-8")))
    if len(known) < 3:
        raise ValueError("检测帧少于 3 帧，无法恢复多帧几何。")
    source = job_dir / "input" / Path(review["source"]["filename"]).name
    if not source.is_file():
        raise ValueError("原始视频不在本机运行目录，无法补抽相邻帧。请重新导入并完成检测。")
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise ValueError("无法解码本次检测视频。")
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        if total < 3 or not math.isfinite(fps) or fps <= 0:
            raise ValueError("视频帧数或帧率无效。")
        # 以实际源帧编号排序，保留原检测图片名称和像素几何。
        extra = set(np.linspace(0, total - 1, min(max_frames, total), dtype=int).tolist())
        known_by_index = {int(f["sourceFrame"]): f for f in known}
        indices = sorted(extra | set(known_by_index))
        images = out / "images"
        images.mkdir(parents=True)
        rows = []
        sizes = set()
        for n, index in enumerate(indices):
            row = known_by_index.get(index)
            if row:
                name = Path(row["name"]).name
                frame_path = job_dir / "frames" / "images" / name
                if not frame_path.is_file():
                    raise ValueError(f"原检测图片缺失：{name}")
                image = cv2.imdecode(np.frombuffer(frame_path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError(f"无法读取原检测图片：{name}")
                shutil.copy2(frame_path, images / name)
            else:
                name = f"geom_{index:08d}.png"
                cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, image = cap.read()
                if not ok:
                    continue
                ok, encoded = cv2.imencode(".png", image)
                if not ok:
                    continue
                (images / name).write_bytes(encoded.tobytes())
            h, w = image.shape[:2]
            sizes.add((w, h))
            rows.append({"image_name": name, "sourceFrame": index, "timeSec": float(row["timeSec"]) if row else index / fps,
                         "hasDetection": row is not None})
            emit("补抽相邻帧，保留原检测像素", 5 + round((n + 1) / len(indices) * 7), frames=len(rows), totalFrames=len(indices))
        if len(rows) < 3 or len(sizes) != 1:
            raise ValueError("需要至少 3 帧且分辨率一致的视频；请勿混剪、裁剪或变焦。")
        write_json(out / "frames.json", {"frames": rows, "source": review["source"], "limits": LIMITS})
        return review, images, rows
    finally:
        cap.release()


def recover_geometry(images, rows, out):
    import pycolmap as pc
    from reconstruct import export_scene, native_path
    pc.set_random_seed(0)
    names = [r["image_name"] for r in rows]  # 原视频时间顺序，不按两种文件名前缀排序。
    database = native_path(out / "database.db")
    reader = pc.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    feature = pc.FeatureExtractionOptions()
    feature.max_image_size = 960
    feature.num_threads = 2
    feature.use_gpu = False
    feature.sift.max_num_features = 4096
    emit("CPU SIFT 特征提取", 15, totalFrames=len(rows))
    kwargs = dict(database_path=database, image_path=native_path(images), image_names=names,
                  camera_mode=pc.CameraMode.SINGLE, reader_options=reader,
                  extraction_options=feature, device=pc.Device.cpu)
    # 3.13 与 4.x 的绑定签名不同，本机以实际绑定为准。
    if "camera_model:" in (pc.extract_features.__doc__ or ""):
        kwargs["camera_model"] = reader.camera_model
    pc.extract_features(**kwargs)
    emit("相邻帧匹配与几何验证", 33)
    matching = pc.FeatureMatchingOptions()
    matching.num_threads = 2
    matching.use_gpu = False
    matching.guided_matching = True
    # 总帧数受限，显式时间图避免文件名前缀影响 SequentialPairing 的排序。
    pairs = out / "pairs.txt"
    pairs.write_text("\n".join(f"{a} {b}" for i, a in enumerate(names) for b in names[i+1:i+9]), "ascii")
    pairing = pc.ImportedPairingOptions()
    pairing.match_list_path = native_path(pairs)
    pc.match_image_pairs(database_path=database, pairing_options=pairing, matching_options=matching, device=pc.Device.cpu)
    mapper = pc.IncrementalPipelineOptions()
    mapper.num_threads = 2
    mapper.min_model_size = 3
    mapper.max_num_models = 4
    mapper.init_num_trials = 60
    mapper.mapper.init_min_tri_angle = 4.0
    mapper.ba_global_max_num_iterations = 30
    mapper.ba_local_max_num_iterations = 20
    if hasattr(mapper, "max_runtime_seconds"):
        mapper.max_runtime_seconds = 480
    if hasattr(mapper, "random_seed"):
        mapper.random_seed = 0
    emit("恢复相机位姿与稀疏地标", 50)
    registered = [0]
    def on_image():
        registered[0] += 1
        emit("恢复相机位姿与稀疏地标", min(73, 51 + round(registered[0] / len(rows) * 22)), registeredFrames=registered[0])
    models = pc.incremental_mapping(database_path=database, image_path=native_path(images),
        output_path=native_path(out / "components"), options=mapper, next_image_callback=on_image)
    useful = {k: m for k, m in models.items() if m.num_reg_images() >= 3 and m.num_points3D() >= 30}
    backend = "incremental SfM"
    weak = not useful or max(m.num_reg_images() for m in useful.values()) < 0.75 * len(rows)
    if weak and hasattr(pc, "global_mapping"):
        # 前向行驶的视频可能没有适合增量初始化的首对；改用真实匹配图的全局优化。
        # 不合并无关联分量，不以规则隧道替代失败几何。
        emit("增量初始化不足，使用全局多帧几何优化", 58)
        global_db = out / "global.db"
        shutil.copy2(out / "database.db", global_db)
        if hasattr(pc, "calibrate_view_graph"):
            pc.calibrate_view_graph(native_path(global_db))
        global_options = pc.GlobalPipelineOptions()
        global_options.num_threads = 2
        global_options.random_seed = 0
        global_options.mapper.num_threads = 2
        global_options.mapper.random_seed = 0
        global_options.mapper.global_positioning.use_gpu = False
        ba = global_options.mapper.bundle_adjustment
        ba.ceres.use_gpu = False
        ba.ceres.solver_options.num_threads = 2
        ba.ceres.solver_options.max_num_iterations = 80
        ba.ceres.solver_options.max_solver_time_in_seconds = 180
        ba.refine_principal_point = False
        global_maps = pc.global_mapping(native_path(global_db), native_path(images), native_path(out / "global_components"), global_options)
        global_useful = {k: m for k, m in global_maps.items() if m.num_reg_images() >= 3 and m.num_points3D() >= 30}
        score = lambda models: max(((m.num_reg_images(), m.num_points3D()) for m in models.values()), default=(0, 0))
        if score(global_useful) > score(useful):
            maps, useful = global_maps, global_useful
            backend = "global SfM"
    if not useful:
        raise ValueError("未恢复足够的多帧几何（至少 3 个注册帧和 30 个地标）。请使用有纹理、连续移动、重叠充分的录像；静止、纯旋转、弱纹理或混剪视频不能可靠重建。")
    selected = max(useful, key=lambda k: (useful[k].num_reg_images(), useful[k].num_points3D()))
    rec = useful[selected]
    sfm = out / "sfm"
    quality = export_scene(rec, sfm, "real_camera_video", len(rows), max_points=30000)
    quality.update({"backend": f"pycolmap {pc.__version__} / CPU / {backend}", "selectedComponent": int(selected),
        "components": [{"id": int(k), "registeredFrames": m.num_reg_images(), "points": m.num_points3D()} for k, m in models.items()],
        "configuration": {"max_image_size": 960, "max_features": 4096, "threads": 2, "temporal_overlap": 8, "init_min_tri_angle": 4.0},
        "unregistered_images": sorted(set(names) - {im.name for im in rec.images.values() if im.has_pose})})
    if not math.isfinite(quality["point_median_reprojection_error_px"]) or quality["point_median_reprojection_error_px"] > 2.5:
        raise ValueError("几何重投影误差过高，本次结果不能用于候选定位。请换用更清晰且重叠充分的视频。")
    write_json(sfm / "quality.json", quality)
    scene = json.loads((sfm / "scene.json").read_text("utf-8"))
    return rec, scene, quality


def supported_triangles(xyz, uv, errors, tracks, camera):
    """只在小范围、连续深度、足够多帧支撑的观测点之间插值。"""
    xyz, uv = np.asarray(xyz, float).reshape(-1, 3), np.asarray(uv, float).reshape(-1, 2)
    if len(xyz) < 3:
        return np.empty((0, 3), dtype=int)
    matrix = np.asarray(camera["world_to_camera"], float)
    depth = (xyz @ matrix[:3, :3].T + matrix[:3, 3])[:, 2]
    good = (depth > 0) & (np.asarray(errors) <= 2.0) & (np.asarray(tracks) >= 3)
    good &= np.isfinite(xyz).all(axis=1) & np.isfinite(uv).all(axis=1)
    good &= (uv[:, 0] >= 0) & (uv[:, 0] < camera["width"]) & (uv[:, 1] >= 0) & (uv[:, 1] < camera["height"])
    ids = np.flatnonzero(good)
    if len(ids) < 3:
        return np.empty((0, 3), dtype=int)
    # 去重，避免退化观测扰乱 Delaunay。
    _, keep = np.unique(np.round(uv[ids], 3), axis=0, return_index=True)
    ids = ids[keep]
    if len(ids) < 3:
        return np.empty((0, 3), dtype=int)
    try:
        faces = ids[Delaunay(uv[ids]).simplices]
    except QhullError:
        return np.empty((0, 3), dtype=int)
    edges = uv[faces] - uv[faces[:, [1, 2, 0]]]
    max_edge = max(12.0, min(camera["width"], camera["height"]) * 0.10)
    small = np.linalg.norm(edges, axis=2).max(axis=1) <= max_edge
    depths = depth[faces]
    continuous = depths.max(axis=1) / depths.min(axis=1) <= 1.15
    tri = xyz[faces]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    valid_area = np.linalg.norm(normal, axis=1) > 1e-9
    return faces[small & continuous & valid_area]


def build_surfaces(rec, scene):
    surfaces = {}
    cameras = {c["image_name"]: c for c in scene["cameras"]}
    for im in rec.images.values():
        if not im.has_pose:
            continue
        observations = [p for p in im.points2D if p.has_point3D()]
        ids = [p.point3D_id for p in observations]
        xyz = np.asarray([rec.points3D[i].xyz for i in ids], float).reshape(-1, 3)
        uv = np.asarray([p.xy for p in observations], float).reshape(-1, 2)
        faces = supported_triangles(xyz, uv, [rec.points3D[i].error for i in ids],
                                   [rec.points3D[i].track.length() for i in ids], cameras[im.name])
        surfaces[im.name] = {"vertices": xyz, "uv": uv, "faces": faces,
                             "point_ids": np.asarray(ids, dtype=np.int64)}
    return surfaces


def build_global_surface(rec, surfaces):
    """把各帧的局部三角面合并成规则化器需要的观测点面接口。"""
    point_index = {}
    vertices = []
    faces = []
    for support in surfaces.values():
        ids = support.get("point_ids", [])
        for face in np.asarray(support.get("faces", []), dtype=np.int64).reshape(-1, 3):
            mapped = []
            for local_id in face:
                point_id = int(ids[int(local_id)])
                if point_id not in point_index:
                    point_index[point_id] = len(vertices)
                    vertices.append(np.asarray(rec.points3D[point_id].xyz, dtype=float))
                mapped.append(point_index[point_id])
            if len(set(mapped)) == 3:
                faces.append(mapped)
    vertices = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    faces = np.asarray(faces, dtype=np.int32).reshape(-1, 3)
    if len(vertices) < 3 or len(faces) == 0:
        raise ValueError("原始局部面不足，无法拟合圆柱观察模型。")
    # 同一组三角面可能被多个相机重复观察，去掉重复项避免拓扑统计膨胀。
    unique = np.unique(np.sort(faces, axis=1), axis=0)
    return vertices, unique.astype(np.int32)


def _cylinder_mesh(origin, basis, center_local, radius, station_range, rings=120, angles=96):
    lo, hi = map(float, station_range)
    if not hi > lo or radius <= 1e-8:
        raise ValueError("圆柱观察模型的轴向范围或半径无效。")
    stations = np.linspace(lo, hi, rings)
    theta = np.linspace(-np.pi, np.pi, angles, endpoint=False)
    ss, tt = np.meshgrid(stations, theta, indexing="ij")
    local = np.stack([center_local[0] + radius * np.cos(tt),
                      center_local[1] + radius * np.sin(tt), ss], axis=-1).reshape(-1, 3)
    vertices = local @ np.asarray(basis, float) + np.asarray(origin, float)
    faces = []
    for i in range(rings - 1):
        for j in range(angles):
            a = i * angles + j
            b = (i + 1) * angles + j
            c = i * angles + (j + 1) % angles
            d = (i + 1) * angles + (j + 1) % angles
            faces.extend(((a, b, c), (b, d, c)))
    return local, vertices, np.asarray(faces, dtype=np.int32)


def _fallback_cylinder(points, scene, run_dir):
    """极少数点云覆盖不足时的保守圆柱先验，仍由相机轨迹和点云估计。"""
    from regularize_tunnel import fit_circle_kasa, topology, write_ply
    cameras = scene["cameras"]
    centers = np.asarray([c["center"] for c in cameras], dtype=float)
    origin = np.median(centers, axis=0)
    _, _, vt = np.linalg.svd(centers - centers.mean(0), full_matrices=False)
    axis = vt[0]
    if np.dot(axis, centers[-1] - centers[0]) < 0:
        axis = -axis
    up = -np.mean([np.asarray(c["world_to_camera"], float)[1, :3] for c in cameras], axis=0)
    up = up - axis * float(up @ axis)
    if np.linalg.norm(up) < 1e-8:
        up = vt[1]
    up /= max(np.linalg.norm(up), 1e-12)
    u = np.cross(up, axis)
    u /= max(np.linalg.norm(u), 1e-12)
    basis = np.asarray([u, np.cross(axis, u), axis])
    local_points = (np.asarray(points, float) - origin) @ basis.T
    lo, hi = np.quantile(local_points[:, 2], [0.03, 0.97])
    if hi - lo < 1e-4:
        lo, hi = float(local_points[:, 2].min()), float(local_points[:, 2].max())
    observed = local_points[(local_points[:, 2] >= lo) & (local_points[:, 2] <= hi)]
    if len(observed) < 20:
        observed = local_points
    try:
        cx, cy, radius = fit_circle_kasa(observed[:, 0], observed[:, 1])
    except Exception:
        cx, cy = np.median(observed[:, :2], axis=0)
        radius = float(np.median(np.linalg.norm(observed[:, :2] - [cx, cy], axis=1)))
    radius = max(float(radius), 1e-3)
    local, vertices, faces = _cylinder_mesh(origin, basis, (cx, cy), radius, (lo, hi))
    regular_dir = Path(run_dir) / "regularization"
    regular_dir.mkdir(parents=True, exist_ok=True)
    near = cKDTree(observed).query(local)[0]
    supported = near <= max(0.18 * radius, 1e-4)
    end_centers = np.asarray([[0, 0, lo], [0, 0, hi]])
    # End-cap vertices follow the same local frame and are kept only for display.
    cap_vertices = np.vstack([local, end_centers])
    caps = []
    angles = 96
    for j in range(angles):
        k = (j + 1) % angles
        last = (120 - 1) * angles
        caps.extend(((len(local), j, k), (len(local) + 1, last + k, last + j)))
    caps = np.asarray(caps, dtype=np.int32)
    triangles = vertices[faces]
    normals = np.zeros_like(vertices)
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    for col in range(3):
        np.add.at(normals, faces[:, col], face_normals)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-15)
    np.savez_compressed(regular_dir / "surface_regularized.npz", vertices=vertices.astype(np.float32), faces=faces,
                        scene_id=scene["scene_id"], surface_id="fallback-cylinder", support=supported.astype(np.uint8),
                        distance_to_observation=near.astype(np.float32))
    np.savez_compressed(regular_dir / "display_attributes.npz", normals=normals.astype(np.float32),
                        supported=supported.astype(np.uint8), caps_positions=cap_vertices[caps].astype(np.float32))
    report = {"scene_id": scene["scene_id"], "surface_id": "fallback-cylinder", "surface_kind": "primitive_cylinder",
              "method": "观测点覆盖不足时的相机轨迹 / 点云圆柱显示先验", "coordinate_origin": origin.tolist(),
              "coordinate_basis_rows": basis.tolist(), "axis_bending_ratio": None, "station_range": [float(lo), float(hi)],
              "fit": {"shape": "cylinder", "center_local": [float(cx), float(cy)], "radius": radius},
              "raw_topology": {"vertices": int(len(points)), "triangles": 0},
              "regularized_topology": topology(vertices, faces), "closed_topology": None,
              "local_support_threshold_sfm_unit": float(max(0.18 * radius, 1e-4)),
              "supported_vertices": int(supported.sum()), "inferred_vertices": int((~supported).sum()),
              "radial_range": [radius, radius],
              "limits": ["观测覆盖不足，圆柱面是本次视频点云的显示先验，不代表完整实测隧道表面。",
                         "单目尺度仍为相对 SfM 单位；原始观测面可切换对照。"]}
    (regular_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return regular_dir, report


def localize_regularized_display(localized, points, report):
    """把已经通过原始面射线检查的点映射到圆柱面，避免伪造未命中的候选。"""
    origin = np.asarray(report["coordinate_origin"], float)
    basis = np.asarray(report["coordinate_basis_rows"], float)
    cx, cy = map(float, report["fit"]["center_local"])
    radius = float(report["fit"]["radius"])
    lo, hi = map(float, report["station_range"])
    tree = cKDTree(np.asarray(points, float))
    tolerance = float(report.get("local_support_threshold_sfm_unit", radius * .18)) * 2
    mapped = []
    for item in localized["defects"]:
        fitted_points = []
        displacements = []
        supported_count = inferred_count = 0
        for point in item.get("points", []):
            local = (np.asarray(point, float) - origin) @ basis.T
            radial = local[:2] - [cx, cy]
            length = float(np.linalg.norm(radial))
            if length < 1e-9:
                continue
            local_fit = np.asarray([cx + radius * radial[0] / length,
                                    cy + radius * radial[1] / length,
                                    np.clip(local[2], lo, hi)])
            fitted = local_fit @ basis + origin
            distance = float(np.linalg.norm(fitted - point))
            nearest = float(tree.query(fitted)[0])
            if distance <= tolerance:
                fitted_points.append(fitted.tolist())
                displacements.append(distance)
                if nearest <= report.get("local_support_threshold_sfm_unit", radius * .18):
                    supported_count += 1
                else:
                    inferred_count += 1
        status = "localized" if fitted_points and len(fitted_points) == item.get("hit_count", 0) else "partial" if fitted_points else "no_accepted_fitted_hit"
        mapped.append({"id": item["id"], "points": fitted_points, "hit_count": len(fitted_points),
                       "status": status, "supported_hit_count": supported_count, "inferred_hit_count": inferred_count,
                       "displacement_median": float(np.median(displacements)) if displacements else None})
    return mapped


def gray_texture_data_url():
    """返回历史多场景案例使用的中性灰规则化表面材质。"""
    gray = Image.new("RGB", (64, 64), (200, 210, 219))
    gray_bytes = io.BytesIO()
    gray.save(gray_bytes, format="JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(gray_bytes.getvalue()).decode("ascii")


def build_regularized_surface(rec, scene, surfaces, localized, out):
    """生成历史案例同款的圆柱规则化表面，并把原始候选关联到该显示面。"""
    vertices, faces = build_global_surface(rec, surfaces)
    np.savez_compressed(out / "surface.npz", vertices=vertices, faces=faces, scene_id=scene["scene_id"])
    # 规则化器沿用历史案例的 run/scene.json 接口；这是本次 SfM 场景的副本。
    write_json(out / "scene.json", scene)
    try:
        from regularize_tunnel import regularize
        regular_dir = out / "regularization"
        if regular_dir.exists():
            shutil.rmtree(regular_dir)
        report = regularize(out, rings=120, angles=96, domain_coverage=.25, domain_points=30,
                            max_unsupported=.75, shape="cylinder", support_tol_fraction=.18)
    except Exception as error:
        # 仍输出由本次视频观测估计的圆柱先验；失败原因写入 report，便于审计。
        regular_dir, report = _fallback_cylinder(scene["points"], scene, out)
        report["regularization_warning"] = str(error)
        (regular_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    with np.load(out / "regularization" / "surface_regularized.npz") as surface:
        rv, rf = surface["vertices"].astype(float), surface["faces"].astype(np.int32)
    attrs = np.load(out / "regularization" / "display_attributes.npz")
    origin = np.asarray(report["coordinate_origin"], float)
    basis = np.asarray(report["coordinate_basis_rows"], float)
    local = (rv - origin) @ basis.T
    theta = np.arctan2(local[:, 1] - report["fit"]["center_local"][1], local[:, 0] - report["fit"]["center_local"][0])
    lo, hi = report["station_range"]
    uv = np.column_stack([(theta + np.pi) / (2 * np.pi), 1 - np.clip((local[:, 2] - lo) / max(hi - lo, 1e-9), 0, 1)])
    reg_localized = localize_regularized_display(localized, scene["points"], report)
    bounds = {"center": ((rv.min(0) + rv.max(0)) / 2).tolist(), "radius": float(np.linalg.norm(rv.max(0) - rv.min(0)) / 2)}
    mesh = {"positions": packed(rv[rf]), "uv": packed(uv[rf]), "vertices": len(rv), "triangles": len(rf),
            "normals": packed(attrs["normals"][rf]), "support": packed(attrs["supported"][rf]),
            "caps": packed(attrs["caps_positions"])}
    # 历史 GitHub 案例的规则化面使用中性灰材质；场景图集只用于原始观测面，
    # 避免把现场照片投影到理想圆柱上，导致观察模型混入洞内画面。
    gray_texture = gray_texture_data_url()
    return {"mesh": mesh, "bounds": bounds, "report": report, "texture_url": gray_texture,
            "localization": {"candidates": len(reg_localized), "mapped": sum(bool(d["points"]) for d in reg_localized)},
            "mapped_defects": {d["id"]: d for d in reg_localized}}


def localize_candidates(scene, detections, surfaces):
    from localize_defects import camera_rays, intersect_rays
    cameras = {c["image_name"]: c for c in scene["cameras"]}
    result = []
    for d in detections["detections"]:
        item = {k: d[k] for k in ("id", "image_name", "label", "confidence")}
        item.update(points=[], observations=[], hit_count=0, requested_count=len(d["pixels"]), usable_count=0)
        c = cameras.get(d["image_name"])
        support = surfaces.get(d["image_name"])
        if not c:
            item["status"] = "unregistered_frame"
        elif not support or not len(support["faces"]):
            item["status"] = "pending_surface"
        else:
            pixels = np.asarray(d["pixels"], float)
            origin, directions, valid, reasons = camera_rays(c, pixels)
            item["usable_count"] = int(valid.sum())
            if origin is None:
                item["status"] = "no_ray"
            else:
                # 限制为该帧观测的三角面，不以其他帧的面穿插来填补缺口。
                distances, triangles = intersect_rays(origin, directions, support["vertices"], support["faces"])
                for pixel, direction, distance, triangle, usable, reason in zip(pixels, directions, distances, triangles, valid, reasons):
                    hit = bool(usable and triangle >= 0)
                    point = (origin + distance * direction).tolist() if hit else None
                    item["observations"].append({"pixel": pixel.tolist(), "point": point,
                        "status": "hit" if hit else str(reason) if not usable else "no_surface_hit", "triangle_id": int(triangle) if hit else None})
                    if hit:
                        item["points"].append(point)
                item["hit_count"] = len(item["points"])
                item["status"] = "localized" if item["hit_count"] and item["hit_count"] == item["usable_count"] else "partial" if item["hit_count"] else "no_surface_hit"
        result.append(item)
    summary = {"detections": len(result), "localizedCandidates": sum(bool(d["points"]) for d in result),
        "fullyLocalizedCandidates": sum(d["status"] == "localized" for d in result),
        "unregisteredCandidates": sum(d["status"] == "unregistered_frame" for d in result),
        "hit_count": sum(d["hit_count"] for d in result), "requested_count": sum(d["requested_count"] for d in result),
        "usable_count": sum(d["usable_count"] for d in result)}
    return {"schema_version": 1, "scene_id": scene["scene_id"], "scale": scene["scale"],
        "method": METHOD, "defects": result, "summary": summary, "limits": LIMITS}


def packed(array):
    return base64.b64encode(np.asarray(array, dtype="<f4").tobytes()).decode("ascii")


def export_viewer(out, job_dir, review, rows, scene, quality, surfaces, localized, detections, regularized=None):
    web = out / "web"
    assets = web / "assets"
    assets.mkdir(parents=True)
    times = {r["image_name"]: r for r in rows}
    cameras = sorted(scene["cameras"], key=lambda c: times[c["image_name"]]["timeSec"])
    cols = math.ceil(math.sqrt(len(cameras)))
    tile = 192
    atlas = Image.new("RGB", (cols * tile, cols * tile), (35, 45, 59))
    positions, texture_uv = [], []
    for index, c in enumerate(cameras):
        name = c["image_name"]
        c["time_s"] = times[name]["timeSec"]
        c["has_detection"] = times[name]["hasDetection"]
        image = Image.open(out / "images" / name).convert("RGB")
        image.thumbnail((1280, 1280))
        image.save(assets / (Path(name).stem + ".jpg"), quality=87)
        c["image_url"] = "assets/" + Path(name).stem + ".jpg"
        mask = job_dir / "detect" / "masks" / (Path(name).stem + ".png")
        if mask.is_file():
            imask = np.asarray(Image.open(mask).convert("L").resize(image.size, Image.Resampling.NEAREST))
            rgba = np.zeros((image.height, image.width, 4), np.uint8)
            rgba[:, :, :3] = (255, 60, 65)
            rgba[:, :, 3] = (imask > 0).astype(np.uint8) * 155
            mask_name = Path(name).stem + "_mask.png"
            Image.fromarray(rgba).save(assets / mask_name)
            c["mask_url"] = "assets/" + mask_name
        image.thumbnail((tile - 2, tile - 2))
        x, y = index % cols * tile + 1, index // cols * tile + 1
        atlas.paste(image, (x, y))
        s = surfaces[name]
        positions.extend(s["vertices"][s["faces"]].tolist())
        uv = s["uv"][s["faces"]].copy()
        uv[:, :, 0] = (x + uv[:, :, 0] / c["width"] * image.width) / atlas.width
        uv[:, :, 1] = 1 - (y + uv[:, :, 1] / c["height"] * image.height) / atlas.height
        texture_uv.extend(uv.tolist())
    atlas.save(assets / "texture.jpg", quality=88)
    by_id = {d["id"]: d for d in detections["detections"]}
    candidates = []
    for d in localized["defects"]:
        source = by_id[d["id"]]
        candidates.append({**{k: d[k] for k in ("id", "image_name", "confidence", "status", "points", "hit_count", "requested_count")},
            "label": "疑似裂缝 / 待复核", "pixels": source["pixels"], "mask_area_px": source.get("mask_area_px"),
            "center": np.median(d["points"], axis=0).tolist() if d["points"] else None,
            "pixel_hits": [o["status"] == "hit" for o in d["observations"]]})
    points = np.asarray(scene["points"], float)
    extents = np.quantile(points, [0.02, 0.98], axis=0)
    center = extents.mean(axis=0)
    radius = max(float(np.linalg.norm(extents[1] - extents[0]) / 2), 0.001)
    data = {"title": "本次视频 · " + review["source"]["filename"], "scene_id": scene["scene_id"],
        "geometry_kind": "sparse_observed_surface", "scale": scene["scale"], "quality": quality,
        "cameras": cameras, "defects": candidates, "localization": localized["summary"],
        "detector": review.get("detector", {}), "video_url": None, "texture_url": "assets/texture.jpg",
        "mesh": {"positions": packed(positions), "uv": packed(texture_uv), "triangles": len(positions)},
        "sparse_points": packed(points), "bounds": {"center": center.tolist(), "radius": radius},
        "source": {"url": "", "author": "本机导入视频", "license": "由导入者确认使用权限", "camera_description": "单目视频估计相机内参"},
        "limits": LIMITS}
    if regularized:
        data["regularized"] = {k: regularized[k] for k in ("mesh", "bounds", "report", "localization", "texture_url")}
        mapped = regularized.get("mapped_defects", {})
        for candidate in data["defects"]:
            if candidate["id"] in mapped:
                candidate["fitted"] = mapped[candidate["id"]]
        data["limits"] = LIMITS + regularized["report"].get("limits", [])
    write_json(web / "scene_data.json", data)
    (web / "scene_data.js").write_text("window.TUNNEL_DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + ";", "utf-8")
    for name in ("index.html", "viewer.js", "viewer.css"):
        shutil.copy2(ROOT / "web_template" / name, web / name)
    shutil.copy2(ROOT / "web" / "multiscene" / "platform-theme.css", web / "platform-theme.css")
    page = (web / "index.html").read_text("utf-8")
    page = page.replace('<link rel="stylesheet" href="viewer.css">', '<link rel="stylesheet" href="viewer.css"><link rel="stylesheet" href="platform-theme.css">')
    page = page.replace('<header><div>', '<header><div class="scene-header-copy">', 1)
    page = page.replace('<div class="headline">', '<nav class="platform-nav" aria-label="返回导航"><a class="platform-back" target="_top" href="/index.html#defects">← 返回候选复核</a><a class="platform-back" target="_top" href="/index.html#processing">返回智能处理工作流</a></nav><div class="headline">', 1)
    (web / "index.html").write_text(page, "utf-8")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-frames", type=int, default=24)
    args = parser.parse_args()
    if not 12 <= args.max_frames <= 96:
        raise ValueError("建模补抽帧数必须在 12–96 之间。")
    job_dir, out = Path(args.job_dir).resolve(), Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("本次建模输出目录必须为空，以免混用其他任务结果。")
    out.mkdir(parents=True, exist_ok=True)
    # C++ 文件访问使用 ASCII 相对路径；Python 的资源访问保留完整 Unicode 路径。
    os.chdir(out)
    start = time.perf_counter()
    review, images, rows = prepare_frames(job_dir, out, args.max_frames)
    rec, scene, quality = recover_geometry(images, rows, out)
    emit("构建有观测支撑的局部表面", 78, registeredFrames=len(scene["cameras"]), sparsePoints=len(scene["points"]))
    surfaces = build_surfaces(rec, scene)
    detections = json.loads((job_dir / "detect" / "detections.json").read_text("utf-8"))
    if detections.get("coordinate_space") != "original_image_pixels":
        raise ValueError("候选必须使用原图像素坐标。")
    # 本次重建重新分配场景 ID，只写本次结果副本，不改原始检测成果。
    detections = {**detections, "scene_id": scene["scene_id"]}
    write_json(out / "detections.json", detections)
    emit("原候选像素与对应帧表面射线求交", 86)
    localized = localize_candidates(scene, detections, surfaces)
    write_json(out / "localized_defects.json", localized)
    emit("拟合圆柱隧道观察表面", 90)
    regularized = build_regularized_surface(rec, scene, surfaces, localized, out)
    emit("导出三维模型、帧证据与定位结果", 96, localizedCandidates=localized["summary"]["localizedCandidates"])
    data = export_viewer(out, job_dir, review, rows, scene, quality, surfaces, localized, detections, regularized)
    result = {"sourceJobId": review["jobId"], "sourceSha256": review.get("sourceSha256") or (json.loads((job_dir / "source.json").read_text("utf-8")).get("sha256") if (job_dir / "source.json").is_file() else None), "sceneId": scene["scene_id"], "geometryKind": data["geometry_kind"], "method": METHOD,
        "registeredFrames": len(scene["cameras"]), "totalFrames": len(rows), "sparsePoints": len(scene["points"]),
        "triangles": data["mesh"]["triangles"], "candidateCount": len(localized["defects"]),
        "localizedCandidates": localized["summary"]["localizedCandidates"],
        "fullyLocalizedCandidates": localized["summary"]["fullyLocalizedCandidates"],
        "hitCount": localized["summary"]["hit_count"], "requestedCount": localized["summary"]["requested_count"],
        "unregisteredCandidates": localized["summary"]["unregisteredCandidates"],
        "regularizedSurfaceKind": data.get("regularized", {}).get("report", {}).get("surface_kind"),
        "regularizedTriangles": data.get("regularized", {}).get("mesh", {}).get("triangles", 0),
        "elapsedSec": round(time.perf_counter() - start, 2), "scale": scene["scale"], "quality": quality, "limits": data["limits"]}
    write_json(out / "result.json", result)
    emit("三维重建与定位流程完成", 100, **{k: result[k] for k in ("registeredFrames", "sparsePoints", "localizedCandidates")})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("SLZJ_RECONSTRUCTION_ERROR " + str(exc), flush=True)
        raise
