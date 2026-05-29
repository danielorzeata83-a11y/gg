"""
Shared signal log — bot.py writes here, api_server.py reads from here.
Keeps the last MAX_SIGNALS entries in a JSON-lines file.
"""
import json
import time
from pathlib import Path

SIGNALS_PATH = "signals.jsonl"
MAX_SIGNALS = 500


def write_signal(signal: dict, decision: dict, path: str = SIGNALS_PATH) -> None:
    """Append a signal + its decision to the log, trimming old entries."""
    row = {
        "ts": time.time(),
        "signal": signal,
        "decision": decision,
    }
    p = Path(path)
    lines = []
    if p.exists():
        with open(p) as f:
            lines = [l for l in f if l.strip()]
    lines.append(json.dumps(row) + "\n")
    # keep bounded
    if len(lines) > MAX_SIGNALS:
        lines = lines[-MAX_SIGNALS:]
    with open(p, "w") as f:
        f.writelines(lines)


def read_signals(path: str = SIGNALS_PATH, limit: int = 50) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows[-limit:]
