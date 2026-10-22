"""Small per-user history of device addresses; no credentials or URLs."""
import json
import os
from pathlib import Path


def history_path():
    base = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else Path.home() / ".config"
    return base / "nggpsdo-adev" / "hosts.json"


def load_hosts(path):
    try:
        if path.stat().st_size > 65536:
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        result, seen = [], set()
        for item in data:
            if not isinstance(item, dict):
                continue
            host, port = item.get("host"), item.get("port")
            if (not isinstance(host, str) or not host or len(host) > 253
                    or any(c.isspace() or c in "/?#@" for c in host)
                    or not isinstance(port, int) or not 1 <= port <= 65535
                    or host.casefold() in seen):
                continue
            seen.add(host.casefold())
            result.append({"host": host, "port": port})
            if len(result) == 10:
                break
        return result
    except (OSError, ValueError, UnicodeError):
        return []


def save_hosts(path, hosts):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(hosts[:10], indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass  # History is optional; connection/measurement must still work.
