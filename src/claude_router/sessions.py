import json
import time
from pathlib import Path


class SessionStore:
    """記錄哪些 session 已被判成受限。一旦鎖定就整段留在本地，重啟後仍有效。"""

    def __init__(self, path: Path):
        self.path = path
        self.locked: dict[str, dict] = {}
        if path.exists():
            self.locked = json.loads(path.read_text())

    def is_local(self, session_id: str | None) -> bool:
        return bool(session_id) and session_id in self.locked

    def lock(self, session_id: str | None, reason: str) -> None:
        if not session_id or session_id in self.locked:
            return
        self.locked[session_id] = {"since": time.strftime("%Y-%m-%dT%H:%M:%S"), "reason": reason}
        self._save()

    def unlock(self, session_id: str) -> bool:
        if self.locked.pop(session_id, None) is None:
            return False
        self._save()
        return True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.locked, indent=2))
        tmp.replace(self.path)
