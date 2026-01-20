from typing import List, Optional, Dict, Any
from pydantic import BaseModel

class IngestRequest(BaseModel):
    collection_name: str
    documents: List[str]
    metadatas: Optional[List[Dict[str, Any]]] = None
    ids: Optional[List[str]] = None

class RetrieveRequest(BaseModel):
    collection_name: str
    query: str
    n_results: int = 3

class RetrieveResponse(BaseModel):
    context: List[str]
