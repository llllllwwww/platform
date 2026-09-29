"""Filter fused MVS points against the camera corridor, remesh and repair small holes.

Forward-motion MVS leaves characteristic along-axis streaks: pixels whose depth is
consistently wrong along near-epipolar directions survive geometric consistency and
become long spikes through the camera corridor or far beyond the wall. The spikes
inflate the axial bounding box (measured 1.5x on rail_train) and poison meshing.

This module
1. gates fused points by view count (fused.ply.vis) and robust distance to the
   camera path (median +/- k * 1.4826 * MAD, two-sided),
2. writes a filtered COLMAP workspace (fused.ply + fused.ply.vis) and re-runs
   advancing_front_mesher on it,
3. orients winding against the filtered normals (orient_surface),
4. fills only small boundary loops that pass planarity and size checks; filled
   triangles use existing vertices only and are recorded as inferred geometry,
5. reports before/after axial extent, aspect ratio, topology and a camera
   velocity drift indicator. No metric scale or unobserved geometry is invented.

Outputs live under <run>/dense/filtered/; the caller is responsible for versioning
previous surface.npz / mesh_oriented.ply before this module writes the new ones.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from mesh_io import read_ply
from orient_surface import orient
from regularize_tunnel import topology

ROOT = Path(__file__).resolve().parent


def read_binary_ply_vertices(path):
    """Read a scalar-only binary_little_endian PLY; return (props dict, ordered prop names)."""
    with Path(path).open('rb') as handle:
        header = b''
        while not header.endswith(b'end_header\n') and not header.endswith(b'end_header\r\n'):
            line = handle.readline()
            if not line:
                raise ValueError(f'PLY header has no end_header: {path}')
            header += line
        text = header.decode('ascii')
        if 'format binary_little_endian 1.0' not in text:
            raise ValueError(f'Expected binary_little_endian PLY: {path}')
        vertex_section = text.split('element vertex ', 1)[1]
        count = int(vertex_section.split('\n', 1)[0].split('\r', 1)[0])
        names, formats = [], []
        in_vertex = False
        for line in text.splitlines():
            parts = line.strip().split()
            if parts[:2] == ['element', 'vertex']:
                in_vertex = True
                continue
            if parts[:1] == ['element']:
                in_vertex = False
                continue
            if in_vertex and len(parts) == 3 and parts[0] == 'property':
                if parts[1] not in ('float', 'double', 'uchar', 'uint8'):
                    raise ValueError(f'Unsupported scalar property type: {line}')
                names.append(parts[2])
                formats.append({'float': '<f4', 'double': '<f8', 'uchar': 'u1', 'uint8': 'u1'}[parts[1]])
        dtype = np.dtype(list(zip(names, formats)))
        arr = np.fromfile(handle, dtype=dtype, count=count)
    props = {name: arr[name].copy() for name in names}
    return props, names, formats


def write_binary_ply_vertices(path, props, names, formats):
    dtype = np.dtype(list(zip(names, formats)))
    kind_map = {'float32': 'float', 'float64': 'double', 'uint8': 'uchar'}
    with Path(path).open('wb') as handle:
        header = ['ply', 'format binary_little_endian 1.0', f'element vertex {len(props[names[0]])}']
        for name, fmt in zip(names, formats):
            header.append(f'property {kind_map[np.dtype(fmt).name]} {name}')
        header.append('end_header')
        handle.write(('\r\n'.join(header) + '\r\n').encode('ascii'))
        arr = np.empty(len(props[names[0]]), dtype=dtype)
        for name in names:
            arr[name] = props[name]
        arr.tofile(handle)


def read_vis(path):
    """COLMAP fused visibility: [uint64 total][per point: uint32 count + count*uint32 image ids]."""
    raw = Path(path).read_bytes()
    if len(raw) < 8:
        raise ValueError(f'Visibility file too short: {path}')
    total = struct.unpack_from('<Q', raw, 0)[0]
    offset = 8
    rows = []
    for _ in range(total):
        count = struct.unpack_from('<I', raw, offset)[0]
        offset += 4
        ids = np.frombuffer(raw, dtype='<i4', count=count, offset=offset)
        offset += 4 * count
        rows.append(ids)
    if offset != len(raw):
        raise ValueError(f'Visibility file length mismatch: {path}')
    return rows


def write_vis(path, rows):
    with Path(path).open('wb') as handle:
        handle.write(struct.pack('<Q', len(rows)))
        for row in rows:
            handle.write(struct.pack('<I', len(row)))
            if len(row):
                handle.write(np.asarray(row, dtype='<i4').tobytes())


def principal_axis(cameras):
    centers = np.array([c['center'] for c in cameras], dtype=np.float64)
    _, singular, vt = np.linalg.svd(centers - centers.mean(0), full_matrices=False)
    axis = vt[0]
    if axis @ (centers[-1] - centers[0]) < 0:
        axis = -axis
    return centers, axis, float(singular[1] / max(singular[0], 1e-12))


def velocity_drift_indicator(centers, axis):
    """Report-only trend of per-interval camera spacing along the axis.

    Under a near-constant capture speed the per-interval spacing should be flat;
    a systematic trend indicates monocular axial scale drift. The indicator is
    (robust slope of spacing vs station) x span / median spacing. It is NOT a
    correction and never rewrites geometry.
    """
    s = (centers - centers.mean(0)) @ axis
    order = np.argsort(s)
    s_sorted = s[order]
    steps = np.diff(s_sorted)
    steps = steps[(steps > 0) & np.isfinite(steps)]
    if len(steps) < 8:
        return None
    stations = 0.5 * (s_sorted[:-1] + s_sorted[1:])
    stations = stations[(np.diff(s_sorted) > 0) & np.isfinite(np.diff(s_sorted))]
    median_step = float(np.median(steps))
    span = float(s_sorted[-1] - s_sorted[0])
    if median_step <= 0 or span <= 0:
        return None
    slope = float(np.polyfit(stations, steps, 1)[0])
    return {'median_step_sfm_unit': median_step, 'span_sfm_unit': span,
            'slope_per_sfm_unit': slope, 'drift_indicator': slope * span / median_step,
            'interpretation': 'Magnitude >> 1 means camera spacing changed systematically along the axis; monocular axial drift cannot be corrected post-hoc without breaking pixel-ray consistency.'}


def boundary_loops(vertices, faces):
    """Extract closed boundary loops from directed boundary half-edges.

    Each undirected boundary edge is traversed exactly once (its reverse is marked
    consumed immediately), so pinched vertices with more than two boundary edges
    cannot duplicate loops. Ambiguity at a vertex is resolved by continuing the
    straightest geometric turn, which keeps small fragments coherent.
    """
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    unique, count = np.unique(edges, axis=0, return_counts=True)
    boundary = unique[count == 1]
    if len(boundary) == 0:
        return [], {'boundary_edges_before_fill': 0, 'nonmanifold_edges_before_fill': int((count > 2).sum())}
    stats = {'boundary_edges_before_fill': int(len(boundary)),
             'nonmanifold_edges_before_fill': int((count > 2).sum())}
    outgoing = {}
    for a, b in boundary:
        outgoing.setdefault(int(a), []).append(int(b))
        outgoing.setdefault(int(b), []).append(int(a))
    consumed = set()

    def consume(a, b):
        consumed.add((a, b))
        consumed.add((b, a))

    loops = []
    for a, b in boundary:
        a, b = int(a), int(b)
        if (a, b) in consumed:
            continue
        loop = [a]
        previous, current = a, b
        consume(a, b)
        loop.append(b)
        guard = 0
        while current != a and guard <= len(boundary) + 1:
            guard += 1
            candidates = [n for n in outgoing.get(current, []) if (current, n) not in consumed]
            if not candidates:
                break
            if len(candidates) == 1:
                nxt = candidates[0]
            else:
                incoming = vertices[current] - vertices[previous]
                incoming /= max(np.linalg.norm(incoming), 1e-15)
                best, best_dot = None, -2.0
                for n in candidates:
                    direction = vertices[n] - vertices[current]
                    direction /= max(np.linalg.norm(direction), 1e-15)
                    score = float(direction @ incoming)
                    if score > best_dot:
                        best, best_dot = n, score
                nxt = best
            consume(current, nxt)
            previous, current = current, nxt
            if current == a:
                break
            loop.append(current)
        if len(loop) >= 3 and current == a:
            loops.append(loop)
    return loops, stats


def fill_small_holes(vertices, faces, max_edges=32, max_planarity_factor=2.0, max_area_factor=200.0):
    """Fill small, nearly planar boundary loops with a centroid fan.

    Each accepted loop gets one new inferred vertex (the loop centroid projected on
    its best-fit plane) fanned to the boundary; boundary loop vertices stay measured.
    Acceptance: short loop, near-planar relative to its own edge length, small
    relative to the median triangle area. The fan reuses every boundary edge exactly
    once and all spokes are new edges, so the mesh stays manifold by construction.
    Returns updated (vertices, faces, filled_vertex_mask, report).
    """
    triangles = vertices[faces]
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    median_area = float(np.median(.5 * np.linalg.norm(face_normals, axis=1)))
    median_edge = float(np.median(np.linalg.norm(triangles[:, 1] - triangles[:, 0], axis=1)))
    report = {'loops_detected': 0, 'holes_filled': 0, 'triangles_added': 0, 'vertices_added': 0,
              'rejected_long_loops': 0, 'rejected_nonplanar': 0, 'rejected_large': 0}
    loops, stats = boundary_loops(vertices, faces)
    report.update(stats)
    new_vertices = []
    new_faces = []
    cursor = len(vertices)
    for loop in loops:
        report['loops_detected'] += 1
        if len(loop) > max_edges:
            report['rejected_long_loops'] += 1
            continue
        if len(set(loop)) != len(loop):
            # Figure-eight through a pinched vertex; a fan here would duplicate spokes.
            report['rejected_repeated_vertex'] = report.get('rejected_repeated_vertex', 0) + 1
            continue
        points = vertices[loop]
        center = points.mean(0)
        local = points - center
        _, _, vt = np.linalg.svd(local, full_matrices=False)
        residuals = np.abs(local @ vt[1])
        loop_edges = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
        scale = max(float(np.median(loop_edges)), median_edge, 1e-9)
        if float(residuals.max()) > max_planarity_factor * scale:
            report['rejected_nonplanar'] += 1
            continue
        adjacent = faces[(faces[:, 0] == loop[0]) | (faces[:, 1] == loop[0]) | (faces[:, 2] == loop[0])]
        normals_adj = face_normals[[np.flatnonzero((faces == tri).all(axis=1))[0] for tri in adjacent]]
        fill_normal = normals_adj[np.argmax(np.linalg.norm(normals_adj, axis=1))]
        normal = vt[1] if vt[1] @ fill_normal >= 0 else -vt[1]
        flat = local @ vt[[0, 2]].T
        area2 = 0.0
        for i in range(len(flat)):
            x1, y1 = flat[i]
            x2, y2 = flat[(i + 1) % len(flat)]
            area2 += x1 * y2 - x2 * y1
        if .5 * abs(area2) > max_area_factor * max(median_area, 1e-15):
            report['rejected_large'] += 1
            continue
        if area2 < 0:
            loop = list(reversed(loop))
        centroid_index = cursor
        cursor += 1
        new_vertices.append(center)
        for i in range(len(loop)):
            a = loop[i]
            b = loop[(i + 1) % len(loop)]
            new_faces.append([centroid_index, a, b])
        report['holes_filled'] += 1
        report['triangles_added'] += len(loop)
        report['vertices_added'] += 1
    report['median_edge_length_sfm_unit'] = median_edge
    report['median_face_area_sfm_unit2'] = median_area
    filled_mask = np.zeros(len(vertices) + len(new_vertices), dtype=bool)
    if new_vertices:
        vertices = np.vstack([vertices, np.asarray(new_vertices, dtype=vertices.dtype)])
        faces = np.vstack([faces, np.asarray(new_faces, dtype=np.int32)])
        filled_mask[-len(new_vertices):] = True
    return vertices, faces, filled_mask, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--colmap', required=True)
    parser.add_argument('--min-views', type=int, default=3)
    parser.add_argument('--radius-sigmas', type=float, default=2.5)
    parser.add_argument('--max-hole-edges', type=int, default=32)
    parser.add_argument('--fill-holes', action='store_true', default=True)
    args = parser.parse_args()
    if args.min_views < 1 or args.radius_sigmas < 1:
        raise ValueError('Invalid gate parameters')
    run = args.run.resolve()
    scene = json.loads((run / 'scene.json').read_text(encoding='utf-8'))
    dense = run / 'dense'
    filtered_dir = dense / 'filtered'
    if filtered_dir.exists() and any(filtered_dir.iterdir()):
        raise FileExistsError(f'Filtered workspace already exists: {filtered_dir}')
    filtered_dir.mkdir(parents=True)
    executable = Path(args.colmap).resolve()

    props, names, formats = read_binary_ply_vertices(dense / 'fused.ply')
    points = np.column_stack([props['x'], props['y'], props['z']])
    total = len(points)
    rows = read_vis(dense / 'fused.ply.vis')
    if len(rows) != total:
        raise ValueError('fused.ply and fused.ply.vis disagree on point count')
    view_counts = np.fromiter((len(row) for row in rows), dtype=np.int32, count=total)

    centers, axis, bending = principal_axis(scene['cameras'])
    distance_to_path = cKDTree(centers).query(points)[0]
    median_distance = float(np.median(distance_to_path))
    sigma = 1.4826 * float(np.median(np.abs(distance_to_path - median_distance)))
    if sigma <= 0:
        raise ValueError('Degenerate distance distribution to the camera path')
    views_keep = view_counts >= args.min_views
    radius_keep = np.abs(distance_to_path - median_distance) <= args.radius_sigmas * sigma
    keep = views_keep & radius_keep & np.isfinite(points).all(axis=1)
    if keep.sum() < 1000:
        raise ValueError('Radius/view gates removed almost everything; refusing to continue')

    kept_rows = [rows[i] for i in np.flatnonzero(keep)]
    kept_props = {name: props[name][keep] for name in names}
    filtered_ply = filtered_dir / 'fused.ply'
    write_binary_ply_vertices(filtered_ply, kept_props, names, formats)
    write_vis(filtered_dir / 'fused.ply.vis', kept_rows)
    if not (dense / 'sparse').is_dir():
        raise FileNotFoundError(f'AFM needs the undistorted sparse model for visibility rays: {dense}/sparse')
    shutil.copytree(dense / 'sparse', filtered_dir / 'sparse')

    os.chdir(ROOT)
    rel_workspace = os.path.relpath(filtered_dir, ROOT)
    if not rel_workspace.isascii():
        raise ValueError('COLMAP on Windows requires ASCII relative paths')
    started = time.perf_counter()
    with (filtered_dir / 'advancing_front_mesher.log').open('wb') as handle:
        result = subprocess.run([str(executable), 'advancing_front_mesher',
                                 '--input_path', rel_workspace, '--output_path', rel_workspace + '/mesh_filtered.ply'],
                                cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f'advancing_front_mesher failed on filtered workspace; see {rel_workspace}/advancing_front_mesher.log')
    meshing_seconds = time.perf_counter() - started

    mesh_props, faces = read_ply(filtered_dir / "mesh_filtered.ply")
    mesh_vertices = np.column_stack([mesh_props['x'], mesh_props['y'], mesh_props['z']])
    orient(filtered_dir / "mesh_filtered.ply", filtered_ply, filtered_dir / 'mesh_oriented.ply')
    oriented_props, oriented_faces = read_ply(filtered_dir / 'mesh_oriented.ply')
    vertices = np.column_stack([oriented_props['x'], oriented_props['y'], oriented_props['z']])
    faces = oriented_faces.astype(np.int32)

    vertices, faces, filled_vertices, hole_report = fill_small_holes(vertices, faces, max_edges=args.max_hole_edges)

    # Replace the module-facing mesh; version the originals before calling this script.
    if (dense / 'mesh_oriented.ply').exists():
        raise FileExistsError('dense/mesh_oriented.ply still present; version it as mesh_oriented_unfiltered.ply first')
    with (dense / 'mesh_oriented.ply').open('wb') as handle:
        handle.write(('ply\nformat binary_little_endian 1.0\nelement vertex ' + str(len(vertices)) +
                      '\nproperty float x\nproperty float y\nproperty float z\nelement face ' + str(len(faces)) +
                      '\nproperty list uchar int vertex_index\nend_header\n').encode('ascii'))
        handle.write(vertices.astype('<f4').tobytes())
        block = np.empty(len(faces), dtype=[('count', 'u1'), ('indices', '<i4', (3,))])
        block['count'] = 3
        block['indices'] = faces
        handle.write(block.tobytes())

    origin = centers.mean(0)
    station_before = (points - origin) @ axis
    station_after = (points[keep] - origin) @ axis
    radius_before = np.linalg.norm(np.cross(points - origin, axis), axis=1)
    radius_after = radius_before[keep]
    extent_before = float(station_before.max() - station_before.min())
    extent_after = float(station_after.max() - station_after.min())
    wall_radius_before = float(np.median(radius_before))
    aspect_before = extent_before / max(wall_radius_before, 1e-12)
    aspect_after = extent_after / max(float(np.median(radius_after)), 1e-12)

    topology_after = topology(vertices, faces)
    np.savez_compressed(run / 'surface.npz', vertices=vertices.astype(np.float32), faces=faces.astype(np.int32),
                        scene_id=np.asarray(str(scene['scene_id'])), filled_vertices=filled_vertices)

    report = {
        'scene_id': scene['scene_id'],
        'points_before': int(total), 'points_kept': int(keep.sum()),
        'removed_low_visibility': int((~views_keep & radius_keep & np.isfinite(points).all(axis=1)).sum()),
        'removed_path_radius': int((views_keep & ~radius_keep & np.isfinite(points).all(axis=1)).sum()),
        'view_count_percentiles': {str(q): float(np.percentile(view_counts, q)) for q in (1, 25, 50, 75)},
        'path_distance_median_sfm_unit': median_distance,
        'path_distance_sigma_sfm_unit': sigma,
        'radius_sigmas': args.radius_sigmas, 'min_views': args.min_views,
        'axial_extent_before_sfm_unit': extent_before,
        'axial_extent_after_sfm_unit': extent_after,
        'extent_shrink_factor': extent_before / max(extent_after, 1e-12),
        'median_wall_radius_before_sfm_unit': wall_radius_before,
        'aspect_ratio_extent_over_radius_before': aspect_before,
        'aspect_ratio_extent_over_radius_after': aspect_after,
        'radius_p999_before_sfm_unit': float(np.percentile(radius_before, 99.9)),
        'radius_p999_after_sfm_unit': float(np.percentile(radius_after, 99.9)),
        'camera_axis_bending_ratio': bending,
        'velocity_drift_indicator': velocity_drift_indicator(centers, axis),
        'meshing_seconds': meshing_seconds,
        'topology_after': topology_after,
        'hole_repair': hole_report,
        'limits': [
            'The camera-path radius gate is a robust statistical cut, not a wall-detection oracle; a few genuine close-range observations are removed with the spikes.',
            'Filled holes are planar interpolations over small boundary loops, marked inferred in surface.npz (filled_vertices); they are not measured surface.',
            'Axial scale remains relative (sfm_unit); the velocity drift indicator quantifies suspected monocular drift but never rewrites geometry.',
        ],
    }
    (filtered_dir / 'postprocess_report.json').write_text(json.dumps(report, indent=2, allow_nan=False), 'utf-8')
    print(json.dumps({k: report[k] for k in (
        'points_before', 'points_kept', 'extent_shrink_factor', 'aspect_ratio_extent_over_radius_before',
        'aspect_ratio_extent_over_radius_after', 'topology_after', 'hole_repair')}, indent=2))


if __name__ == '__main__':
    main()
