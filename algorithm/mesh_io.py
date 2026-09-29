"""把 COLMAP 导出的网格 PLY 转成 localize_defects.py 需要的 NPZ 接口。

这是补齐管线上的一环：reconstruct.py 出稀疏点云与位姿，COLMAP 出网格，
localize_defects.py 需要 (vertices, faces, scene_id) 三个键的 NPZ。
本模块只做「读 PLY -> 写 NPZ」，不做任何几何推断，也不修改坐标。

只支持 COLMAP 实际会写出的几种 PLY 变体：
  - format ascii / binary_little_endian
  - 标量属性类型 char/uchar/short/ushort/int/uint/float/double
  - 面索引用 property list <count_type> <index_type>
binary_big_endian 直接报错（COLMAP 不会写）。
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np

# PLY 标量类型 -> numpy dtype（little endian）
SCALAR = {
    'char': 'i1', 'int8': 'i1',
    'uchar': 'u1', 'uint8': 'u1',
    'short': 'i2', 'int16': 'i2',
    'ushort': 'u2', 'uint16': 'u2',
    'int': 'i4', 'int32': 'i4',
    'uint': 'u4', 'uint32': 'u4',
    'float': 'f4', 'float32': 'f4',
    'double': 'f8', 'float64': 'f8',
}


def read_ply(path):
    """返回 (properties: {name: np.ndarray}, faces: (M,3) int64)。只读第一个 element 的顶点与面。"""
    path = Path(path)
    with open(path, 'rb') as handle:
        header_lines = []
        while True:
            line = handle.readline()
            if not line:
                raise ValueError(f'PLY 头部未结束（缺少 end_header）: {path}')
            text = line.decode('ascii', errors='replace').strip()
            header_lines.append(text)
            if text == 'end_header':
                break
        if not header_lines or header_lines[0] != 'ply':
            raise ValueError(f'不是 PLY 文件: {path}')
        formats = [ln for ln in header_lines if ln.startswith('format ')]
        if len(formats) != 1:
            raise ValueError('PLY 必须恰好有一条 format 声明')
        fmt = formats[0].split()[1]
        if fmt not in ('ascii', 'binary_little_endian'):
            raise ValueError(f'不支持的 PLY 格式（COLMAP 不会写这个）: {fmt}')

        # 解析 element 段
        elements = []
        for text in header_lines:
            parts = text.split()
            if not parts:
                continue
            if parts[0] == 'element':
                elements.append({'name': parts[1], 'count': int(parts[2]), 'props': []})
            elif parts[0] == 'property' and elements:
                if parts[1] == 'list':
                    elements[-1]['props'].append(('list', parts[2], parts[3], parts[4]))
                else:
                    elements[-1]['props'].append(('scalar', parts[1], parts[2]))
        if not elements:
            raise ValueError('PLY 中没有任何 element')

        data = handle.read()

    if fmt == 'ascii':
        return _read_ascii(elements, data)
    return _read_binary(elements, data)


def _read_ascii(elements, data):
    tokens = data.split()
    cursor = 0
    properties, faces = {}, np.zeros((0, 3), dtype=np.int64)
    for element in elements:
        if element['name'] == 'vertex':
            columns = {i: [] for i, p in enumerate(element['props'])}
            for _ in range(element['count']):
                for i, prop in enumerate(element['props']):
                    if prop[0] != 'scalar':
                        raise ValueError('顶点属性不支持 list')
                    columns[i].append(float(tokens[cursor]))
                    cursor += 1
            for i, prop in enumerate(element['props']):
                properties[prop[2]] = np.asarray(columns[i], dtype=np.float64)
        elif element['name'] == 'face':
            rows = []
            for _ in range(element['count']):
                count = int(tokens[cursor]); cursor += 1
                indices = [int(tokens[cursor + k]) for k in range(count)]
                cursor += count
                if count != 3:
                    raise ValueError(f'只支持三角面，遇到 {count} 边面')
                rows.append(indices)
            faces = np.asarray(rows, dtype=np.int64).reshape(-1, 3)
        else:
            cursor += element['count'] * len(element['props'])
    return properties, faces


def _read_binary(elements, data):
    offset = 0
    properties, faces = {}, np.zeros((0, 3), dtype=np.int64)
    for element in elements:
        # 组装一个结构化 dtype；含 list 的 element 只能逐条读
        if any(p[0] == 'list' for p in element['props']):
            if element['name'] != 'face':
                raise ValueError(f'只有 face 允许含 list 属性，遇到 {element["name"]}')
            rows = []
            for _ in range(element['count']):
                for prop in element['props']:
                    if prop[0] == 'list':
                        count_dtype = np.dtype(SCALAR[prop[1]])
                        count = int(np.frombuffer(data, count_dtype, 1, offset)[0])
                        offset += count_dtype.itemsize
                        index_dtype = np.dtype(SCALAR[prop[2]])
                        indices = np.frombuffer(data, index_dtype, count, offset)
                        offset += index_dtype.itemsize * count
                        if count != 3:
                            raise ValueError(f'只支持三角面，遇到 {count} 边面')
                        rows.append(indices.astype(np.int64))
                    else:
                        raise ValueError(f'face 元素中出现标量属性 {prop[2]}，未预期')
            faces = np.asarray(rows, dtype=np.int64).reshape(-1, 3)
            continue
        dtype = np.dtype([(p[2], SCALAR[p[1]]) for p in element['props']])
        block = np.frombuffer(data, dtype, element['count'], offset)
        offset += dtype.itemsize * element['count']
        if element['name'] == 'vertex':
            for name in (p[2] for p in element['props']):
                properties[name] = block[name].astype(np.float64)
    return properties, faces


def export_npz(ply_path, out_npz, scene_id):
    """把 PLY 网格写成 localize_defects.py 期望的 NPZ（vertices/faces/scene_id）。"""
    properties, faces = read_ply(ply_path)
    missing = [k for k in ('x', 'y', 'z') if k not in properties]
    if missing:
        raise ValueError(f'网格顶点缺少坐标属性: {missing}')
    vertices = np.column_stack([properties['x'], properties['y'], properties['z']])
    if len(vertices) < 3 or len(faces) == 0:
        raise ValueError(f'网格为空（顶点 {len(vertices)} / 面 {len(faces)}），拒绝导出空表面')
    if not np.isfinite(vertices).all():
        raise ValueError('网格含非有限坐标')
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError('面索引越界')
    out_npz = Path(out_npz)
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_npz, vertices=vertices, faces=faces,
                        scene_id=np.asarray(str(scene_id)))
    return {'ply': str(ply_path), 'out': str(out_npz), 'scene_id': str(scene_id),
            'vertices': int(len(vertices)), 'faces': int(len(faces)),
            'bounds_min': vertices.min(axis=0).tolist(),
            'bounds_max': vertices.max(axis=0).tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ply', required=True, help='COLMAP 导出的网格 PLY')
    parser.add_argument('--scene', required=True, help='对应的 scene.json（取其 scene_id）')
    parser.add_argument('--out', required=True, help='输出 NPZ 路径')
    args = parser.parse_args()
    scene = json.loads(Path(args.scene).read_text('utf-8'))
    if scene.get('schema_version') != 1 or not scene.get('scene_id'):
        raise ValueError('scene.json 需要 schema_version=1 与 scene_id')
    # 未做尺度锚定时必须显式提示，避免下游误当米制
    scale = scene.get('scale', {})
    if scale.get('status') != 'metric':
        print(json.dumps({'warning': 'scene 未做尺度锚定（sfm_unit），几何量不能当米制使用',
                          'scale_status': scale.get('status')}, ensure_ascii=False))
    print(json.dumps(export_npz(args.ply, args.out, scene['scene_id']), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
