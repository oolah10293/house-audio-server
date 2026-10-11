"""Atomic JSON replacement for server-owned settings and counters."""
import json
import os
import tempfile
from pathlib import Path


def save_json(path, value, committed=lambda: None):
    if path is None:
        committed()
        return
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.' + path.name,
                                         encoding='utf-8', delete=False) as target:
            temporary = target.name
            json.dump(value, target, ensure_ascii=False, allow_nan=False)
            target.write('\n')
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        temporary = None
        committed()  # Memory must match the replaced file even if fsync fails.
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
