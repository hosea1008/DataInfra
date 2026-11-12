"""Sample status once with bounded per-dataset reservoirs; inspect local paths.

No data-table scan or database/filesystem writes. Symlinks in *any* path
component are expanded individually, retaining every link and its raw target.
"""

import os
import random
import stat
from collections import deque
from datetime import UTC, datetime

from app.schemas import DatasetPathItem, DatasetPathLink, DatasetPathsResponse
from app.services.stats_service import _CACHE, _singleflight

SAMPLE_SIZE = 20
MAX_LINKS = 128


def trace_path(path: str) -> tuple[str | None, list[DatasetPathLink], list[str]]:
    """Resolve components without hiding intermediate links or broken targets."""
    links = []
    pending = deque(path.split('/'))
    current = '/'
    seen = set()
    try:
        while pending:
            part = pending.popleft()
            if part in ('', '.'):
                continue
            if part == '..':
                current = os.path.dirname(current)
                continue
            candidate = os.path.join(current, part)
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                state = (candidate, tuple(pending))
                if state in seen or len(links) >= MAX_LINKS:
                    return None, links, ['软链接循环或超过 128 层，无法确认真实路径']
                seen.add(state)
                target = os.readlink(candidate)
                links.append(DatasetPathLink(path=candidate, target=target))
                if os.path.isabs(target):
                    current = '/'
                pending.extendleft(reversed(target.split('/')))
            else:
                if not stat.S_ISDIR(info.st_mode):
                    return None, links, [f'路径不是目录：{candidate}']
                current = candidate
    except (OSError, ValueError) as exc:
        return None, links, [f'无法核实路径（不存在、断链或无访问权限）：{exc}']
    return current, links, []


def summarize_paths(name: str | None, paths: list[str | None]) -> DatasetPathItem:
    item = DatasetPathItem(dataset_name=name, sample_count=len(paths))
    # Never turn relative/empty database paths into server working-directory paths.
    valid = [p for p in paths if isinstance(p, str) and p.startswith('/') and '\x00' not in p]
    if len(valid) != len(paths):
        item.warnings.append('样本包含空值、相对路径或无效路径，已忽略；目录推断可能不完整')
    if not valid:
        item.warnings.append('没有可用的绝对路径')
        return item
    # lerobot_path names the lerobot directory itself; use its parent even
    # for a dataset with only one lerobot, rather than report the lerobot as root.
    parents = [os.path.dirname(p.rstrip('/')) or '/' for p in valid]
    item.dataset_path = os.path.commonpath(parents)
    if item.dataset_path == '/':
        item.warnings.append('样本仅有根目录作为公共目录，可能分散存储于多个位置')
    if len(paths) < 2:
        item.warnings.append('仅有一个样本，使用其父目录；请核实数据集边界')
    item.resolved_path, item.links, warnings = trace_path(item.dataset_path)
    item.warnings.extend(warnings)
    return item


def get_dataset_paths(pools, refresh: bool = False) -> DatasetPathsResponse:
    key = ('dataset_paths',)
    if not refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
    with _singleflight(key):
        if not refresh:
            cached = _CACHE.get(key)
            if cached is not None:
                return cached
        # A named server-side cursor streams status without buffering the whole
        # result. Reservoir sampling is uniform per dataset, O(20 * datasets)
        # memory, and avoids ORDER BY random() sorts or one scan per dataset.
        reservoirs = {}
        counts = {}
        rng = random.Random()
        with pools.connection('production') as conn:
            with conn.cursor(name='dataset_path_samples') as cur:
                cur.itersize = 2000
                cur.execute('SELECT dataset_name, lerobot_path FROM status')
                for name, path in cur:
                    counts[name] = counts.get(name, 0) + 1
                    sample = reservoirs.setdefault(name, [])
                    if len(sample) < SAMPLE_SIZE:
                        sample.append(path)
                    else:
                        index = rng.randrange(counts[name])
                        if index < SAMPLE_SIZE:
                            sample[index] = path
        items = [summarize_paths(name, reservoirs[name])
                 for name in sorted(reservoirs, key=lambda n: (n is not None, n or ''))]
        response = DatasetPathsResponse(cached_at=datetime.now(UTC), items=items)
        _CACHE.set(key, response)
        return response
