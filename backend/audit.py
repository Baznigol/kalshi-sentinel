import json
from typing import Any, Dict, Optional

from db import get_db


def log(level: str, component: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    db = get_db()
    try:
        db.execute(
            "INSERT INTO audit_log(level, component, message, data_json) VALUES (?,?,?,?)",
            (level, component, message, json.dumps(data) if data is not None else None),
        )
        db.commit()
    finally:
        db.close()
