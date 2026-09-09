import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunLogger:
    def __init__(
        self,
        phase: str,
        log_dir: str = "evidence/logs",
    ):
        self.run_id = str(uuid.uuid4())
        self.phase = phase

        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)

        self.path = directory / f"{phase}_{self.run_id}.jsonl"

    def log(
        self,
        event: str,
        *,
        step_number: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "phase": self.phase,
            "event": event,
            "step_number": step_number,
            "data": data or {},
        }

        with self.path.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )