from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, BinaryIO


class Storage:
    """Where uploads go. Subclass it to use S3 or anything else. Files are always served through the panel."""

    def save(self, file: Any, name: str) -> None:
        raise NotImplementedError

    def open(self, name: str) -> BinaryIO:
        """Raises FileNotFoundError when there's no such file."""
        raise NotImplementedError


class LocalStorage(Storage):
    """Keeps files in a folder. Put it outside anything your web server serves, or uploads become public."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _path(self, name: str) -> Path:
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError(f"{name!r} is outside the storage folder")
        return path

    def save(self, file: Any, name: str) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as target:
            shutil.copyfileobj(file, target)

    def open(self, name: str) -> BinaryIO:
        return self._path(name).open("rb")
