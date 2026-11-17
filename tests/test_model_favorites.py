import json
from pathlib import Path

import pytest

from app.model_favorites import FavoritesError, favorites_first, favorites_path, load_favorites, set_favorite


def test_persistence_order_and_isolated_paths(tmp_path):
    db = tmp_path / 'one.db'
    other = tmp_path / 'two.db'
    assert load_favorites(db) == set()
    set_favorite(db, 'provider/b', True)
    set_favorite(db, 'missing/model', True)
    assert load_favorites(db) == {'provider/b', 'missing/model'}
    assert load_favorites(other) == set()
    assert favorites_first(['provider/a', 'provider/b', 'provider/c'], load_favorites(db)) == [
        'provider/b', 'provider/a', 'provider/c']
    assert favorites_first(['provider/c', 'provider/b', 'provider/a'], {'provider/a', 'provider/c'}) == [
        'provider/c', 'provider/a', 'provider/b']
    set_favorite(db, 'provider/b', False)
    assert load_favorites(db) == {'missing/model'}
    assert not db.exists()


@pytest.mark.parametrize('raw', ['bad JSON', '{}', '[null]', '[1]', '["bad"]',
                                  '["provider/"]', '["/model"]', '["p/a b"]', '["p/a\\n"]'])
def test_malformed_never_overwritten(tmp_path, raw):
    db = tmp_path / 'workspace.db'
    path = favorites_path(db)
    path.write_text(raw)
    with pytest.raises(FavoritesError):
        load_favorites(db)
    with pytest.raises(FavoritesError):
        set_favorite(db, 'p/valid', True)
    assert path.read_text() == raw


def test_io_failures_preserve_preferences(tmp_path, monkeypatch):
    db = tmp_path / 'workspace.db'
    set_favorite(db, 'p/original', True)
    before = favorites_path(db).read_bytes()
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_text', denied)
        with pytest.raises(FavoritesError, match='read'):
            load_favorites(db)
    with monkeypatch.context() as patch:
        patch.setattr('app.model_favorites.os.replace', denied)
        with pytest.raises(FavoritesError, match='save'):
            set_favorite(db, 'p/new', True)
    assert favorites_path(db).read_bytes() == before
    assert list(tmp_path.iterdir()) == [favorites_path(db)]
    with monkeypatch.context() as patch:
        patch.setattr('app.model_favorites.tempfile.NamedTemporaryFile', denied)
        with pytest.raises(FavoritesError, match='save'):
            set_favorite(db, 'p/new', True)
    assert favorites_path(db).read_bytes() == before


def test_invalid_action_and_latest_saved_preferences(tmp_path):
    db = tmp_path / 'workspace.db'
    with pytest.raises(FavoritesError):
        set_favorite(db, 'bad id', True)
    set_favorite(db, 'p/first', True)
    favorites_path(db).write_text(json.dumps(['p/first', 'p/other-tab']))
    set_favorite(db, 'p/last', True)
    assert load_favorites(db) == {'p/first', 'p/other-tab', 'p/last'}
