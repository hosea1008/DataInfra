"""Filesystem and API contract tests; independent of production mounts."""
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.services import dataset_path_service as service
from app.services.stats_service import clear_cache


def test_common_directory_matches_requirement():
    item = service.summarize_paths('example', ['/a/b/c/d/e', '/a/b/c/d/f', '/a/b/g/d/h'])
    assert item.dataset_path == '/a/b'


def test_real_directory_and_single_sample(tmp_path):
    item = service.summarize_paths('single', [str(tmp_path / 'lerobot')])
    assert item.dataset_path == str(tmp_path)
    assert item.resolved_path == str(tmp_path)
    assert not item.links
    assert item.warnings


def test_parent_and_multiple_relative_links(tmp_path):
    real = tmp_path / 'disk' / 'dataset'
    real.mkdir(parents=True)
    (tmp_path / 'middle').symlink_to('disk', target_is_directory=True)
    (tmp_path / 'entry').symlink_to('middle', target_is_directory=True)
    item = service.summarize_paths('linked', [str(tmp_path / 'entry/dataset/a'), str(tmp_path / 'entry/dataset/b')])
    assert item.resolved_path == str(real)
    assert [(link.path, link.target) for link in item.links] == [
        (str(tmp_path / 'entry'), 'middle'), (str(tmp_path / 'middle'), 'disk')]
    assert not item.warnings


def test_broken_and_cyclic_links(tmp_path):
    (tmp_path / 'broken').symlink_to('missing')
    resolved, links, warnings = service.trace_path(str(tmp_path / 'broken'))
    assert resolved is None and len(links) == 1 and warnings
    (tmp_path / 'one').symlink_to('two')
    (tmp_path / 'two').symlink_to('one')
    resolved, links, warnings = service.trace_path(str(tmp_path / 'one'))
    assert resolved is None and len(links) == 2
    assert '循环' in warnings[0]


def test_permission_error_and_invalid_samples(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError('denied')
    monkeypatch.setattr(service.os, 'lstat', denied)
    assert service.trace_path(str(tmp_path))[0] is None
    assert service.trace_path(str(tmp_path))[2]
    item = service.summarize_paths('invalid', [None, '', 'relative/path', '/bad\x00path'])
    assert item.dataset_path is None
    assert item.warnings


def test_dotdot_is_expanded_after_symlink(tmp_path):
    (tmp_path / 'disk' / 'nested').mkdir(parents=True)
    (tmp_path / 'alias').symlink_to('disk/nested')
    resolved, links, warnings = service.trace_path(str(tmp_path / 'alias' / '..'))
    assert resolved == str(tmp_path / 'disk')
    assert len(links) == 1 and not warnings


class FakePools:
    scans = 0

    @contextmanager
    def connection(self, env):
        assert env == 'production'
        yield self

    @contextmanager
    def cursor(self, name):
        assert name == 'dataset_path_samples'
        yield self

    def execute(self, query):
        assert query == 'SELECT dataset_name, lerobot_path FROM status'
        self.scans += 1

    def __iter__(self):
        return iter([('large', f'/not-mounted/dataset/robot-{i}') for i in range(100)] +
                    [('small', '/not-mounted/small/robot')])


def test_api_sampling_cache_and_refresh():
    clear_cache()
    app = create_app()
    pools = FakePools()
    app.state.pools = pools
    with TestClient(app) as client:
        # Lifespan installs real pools; replace after entering without touching DB.
        app.state.pools = pools
        first = client.get('/api/stats/dataset-paths')
        assert first.status_code == 200
        body = first.json()
        assert body['sample_size'] == 20
        assert [(row['dataset_name'], row['sample_count']) for row in body['items']] == [('large', 20), ('small', 1)]
        assert body['items'][0]['dataset_path'] == '/not-mounted/dataset'
        assert body['items'][0]['resolved_path'] is None
        assert body['items'][0]['warnings']
        assert client.get('/api/stats/dataset-paths').json()['cached_at'] == body['cached_at']
        assert pools.scans == 1
        assert client.get('/api/stats/dataset-paths?refresh=1').status_code == 200
        assert pools.scans == 2
        # Restore a closeable manager for lifespan shutdown.
        from app.db import PoolManager
        app.state.pools = PoolManager()
    clear_cache()


def test_api_with_mock_postgres(client):
    """Exercise the real named psycopg2 cursor and production reader connection."""
    clear_cache()
    response = client.get('/api/stats/dataset-paths?refresh=1')
    assert response.status_code == 200
    body = response.json()
    import json
    from pathlib import Path
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'scripts/seed_manifest.json').read_text())
    expected = {row['dataset_name']: min(row['lerobot_count'], 20) for row in manifest['datasets']}
    assert {row['dataset_name']: row['sample_count'] for row in body['items']} == expected
    assert all(0 < item['sample_count'] <= 20 for item in body['items'])
    assert all(item['dataset_path'] for item in body['items'])
    clear_cache()


def test_absolute_link_target(tmp_path):
    target = tmp_path / 'real'
    target.mkdir()
    (tmp_path / 'alias').symlink_to(target)
    resolved, links, warnings = service.trace_path(str(tmp_path / 'alias'))
    assert resolved == str(target)
    assert links[0].target == str(target)
    assert not warnings
