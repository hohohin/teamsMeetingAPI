# models.py 或直接放在主文件
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Dict, Any, Optional

class SubsMeta(BaseModel):
    id: str
    object_key: str
    region: str = "guangzhou"
    size: int = 0
    task_id: str = ""
    status: str = "NONE" or "COMPLETED" or "ONGOING"
    query_res: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_modified: str = ""

class DoubaoSubsMeta(SubsMeta):
    x_id: str = ""

