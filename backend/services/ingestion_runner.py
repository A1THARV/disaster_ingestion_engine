"""Background ingestion runner for the dashboard."""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


class IngestionRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "running": False,
            "last_started": None,
            "last_finished": None,
            "last_exit_code": None,
            "last_output": "",
        }

    def status(self) -> Dict:
        with self._lock:
            return dict(self._state)

    def trigger(self, workdir: str) -> Dict:
        with self._lock:
            if self._state["running"]:
                return dict(self._state)
            self._state["running"] = True
            self._state["last_started"] = datetime.now(timezone.utc).isoformat()
            self._state["last_output"] = ""

        thread = threading.Thread(target=self._run, args=(workdir,), daemon=True)
        thread.start()
        return self.status()

    def _run(self, workdir: str):
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "ingestion.pipeline"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            output = "\n".join(
                part for part in [completed.stdout.strip(), completed.stderr.strip()] if part
            )
            exit_code = completed.returncode
        except Exception as exc:
            output = str(exc)
            exit_code = -1

        with self._lock:
            self._state["running"] = False
            self._state["last_finished"] = datetime.now(timezone.utc).isoformat()
            self._state["last_exit_code"] = exit_code
            self._state["last_output"] = output[-4000:]
