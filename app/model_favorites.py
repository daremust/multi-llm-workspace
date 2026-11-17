"""Database-scoped model favorites; no database or provider access."""

import json
import os
from pathlib import Path
import tempfile


class FavoritesError(ValueError):
    """Preferences could not be safely read or saved."""


def favorites_path(database_path: Path) -> Path:
    return database_path.with_name(database_path.name + '.favorites.json')


def _valid_id(value) -> bool:
    return (isinstance(value, str) and '/' in value
            and all(value.split('/'))
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127
                        or 0xD800 <= ord(char) <= 0xDFFF for char in value))


def load_favorites(database_path: Path) -> set[str]:
    try:
        data = json.loads(favorites_path(database_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return set()
    except (OSError, UnicodeError, ValueError) as exc:
        raise FavoritesError('Could not read model favorites. Check the sidecar file; it has not been overwritten.') from exc
    if not isinstance(data, list) or any(not _valid_id(item) for item in data):
        raise FavoritesError('Invalid model favorites file. Expected a JSON list of OpenRouter model IDs; it has not been overwritten.')
    return set(data)


def set_favorite(database_path: Path, model_id: str, starred: bool) -> None:
    if not _valid_id(model_id):
        raise FavoritesError('Choose a valid OpenRouter model ID.')
    # Re-read on each action: preserve other tabs' already-saved changes and
    # never replace malformed preferences using a stale UI snapshot.
    favorites = load_favorites(database_path)
    if starred:
        favorites.add(model_id)
    else:
        favorites.discard(model_id)
    path = favorites_path(database_path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(sorted(favorites), handle, ensure_ascii=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise FavoritesError('Could not save model favorites. Check directory permissions and available disk space.') from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                raise FavoritesError('Could not remove a temporary favorites file. Check directory permissions.') from exc


def favorites_first(model_ids, favorites: set[str]) -> list[str]:
    ids = list(model_ids)
    return [mid for mid in ids if mid in favorites] + [mid for mid in ids if mid not in favorites]
