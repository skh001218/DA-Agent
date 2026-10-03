import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    records_dsn: str = os.getenv("RECORDS_DSN", "postgresql://recorder:local-records@127.0.0.1:5432/records")
    learner_dsn: str = os.getenv("LEARNER_DSN", "postgresql://learner:local-learner@127.0.0.1:5432/training")
    packages_root: Path = Path(os.getenv("PACKAGES_ROOT", "packages"))
    execution_ttl: int = 600
    max_rows: int = 1000
    max_bytes: int = 1048576
    preview_rows: int = 200
    query_timeout_ms: int = 5000
