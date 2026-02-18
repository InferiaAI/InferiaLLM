from pydantic import BaseModel
from typing import Optional

class KBFileResponse(BaseModel):
    filename: str
    doc_id: str
    uploaded_by: Optional[str] = None
    doc_count: int = 1 # Number of chunks
