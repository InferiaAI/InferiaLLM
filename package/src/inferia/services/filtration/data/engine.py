"""
Data Engine Service.
Handles RAG, vector lookups, context assembly, and logging.
"""

from typing import List, Dict, Any, Optional
import chromadb
from config import settings
import logging

logger = logging.getLogger(__name__)

class DataEngine:
    def __init__(self):
        self.client = None
        # Lazy initialization will occur when needed.

    def initialize_client(self):
        """Initialize or refresh the ChromaDB client."""
        import traceback
        logger.info(f"initialize_client called from:\n{''.join(traceback.format_stack())}")
        try:
            # Check if we should use local/HttpClient or CloudClient
            if settings.chroma_is_local:
                # Local or Self-Hosted via URL
                from chromadb.config import Settings as ChromaSettings
                
                url = settings.chroma_url or "http://localhost:8000"
                # Strip trailing slash for consistency
                url = url.rstrip('/')
                
                logger.info(f"Connecting to Local ChromaDB at {url}...")
                self.client = chromadb.HttpClient(
                    host=url.split('://')[-1].split(':')[0],
                    port=int(url.split(':')[-1]) if ':' in url.split('://')[-1] else 8000,
                    ssl='https' in url,
                    tenant=settings.chroma_tenant or "default_tenant",
                    database=settings.chroma_database or "default_database",
                    # If we need Auth for local, we add it here
                )
            else:
                # Chroma Cloud
                logger.info("Connecting to ChromaDB Cloud...")
                self.client = chromadb.CloudClient(
                    api_key=settings.chroma_api_key,
                    tenant=settings.chroma_tenant,
                    database=settings.chroma_database
                )
            logger.info("ChromaDB client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to connect to ChromaDB: {e}")
            self.client = None

    def _get_scoped_name(self, collection_name: str, org_id: Optional[str]) -> str:
        """Scope collection name by Org ID. If no org_id, assumes global/admin or legacy."""
        if not org_id:
            return collection_name # Default or Legacy
        return f"org_{org_id}_{collection_name}"

    def _get_unscoped_name(self, scoped_name: str) -> str:
        """Remove org prefix from collection name."""
        # Format: org_{uuid}_{name}
        parts = scoped_name.split("_", 2)
        if len(parts) >= 3 and parts[0] == "org":
            return parts[2]
        return scoped_name

    async def list_collections(self, org_id: str) -> List[str]:
        """
        List all available collections for the given organization.
        """
        if not self.client:
            self.initialize_client()
            
        if not self.client:
            return []
        try:
            collections = self.client.list_collections()
            # Filter by org prefix
            prefix = f"org_{org_id}_"
            return [
                self._get_unscoped_name(c.name) 
                for c in collections 
                if c.name.startswith(prefix)
            ]
        except Exception as e:
            logger.error(f"Error listing collections: {e}")
            return []

    async def list_files(self, collection_name: str, org_id: str) -> List[Dict[str, Any]]:
        """
        List unique files ingested in the collection.
        Returns metadata for each unique source.
        """
        scoped_name = self._get_scoped_name(collection_name, org_id)
        collection = self._get_collection(scoped_name)
        if not collection:
            return []
            
        try:
            # Fetch all metadata to aggregaget unique files
            # Note: For large collections, this is inefficient. optimize later (e.g. SQL index).
            result = collection.get(include=["metadatas"])
            metadatas = result.get("metadatas", []) or []
            
            files_map = {}
            for m in metadatas:
                if not m: continue
                source = m.get("source")
                if not source: continue
                
                if source not in files_map:
                    files_map[source] = {
                        "filename": source,
                        "doc_id": "n/a", # we don't track single doc_id for whole file easily here
                        "uploaded_by": m.get("uploaded_by"),
                        "doc_count": 0
                    }
                files_map[source]["doc_count"] += 1
                
            return list(files_map.values())
        except Exception as e:
            logger.error(f"Error listing files in {scoped_name}: {e}")
            return []

    def _get_collection(self, collection_name: str):
        if not self.client:
            logger.info("ChromaDB client not initialized. Attempting initialization...")
            self.initialize_client()
            
        if not self.client:
            logger.error("ChromaDB client is STILL NOT initialized.")
            return None
        try:
            return self.client.get_or_create_collection(name=collection_name)
        except Exception as e:
            logger.error(f"Error getting collection {collection_name}: {e}")
            return None

    async def log_event(self, event_type: str, data: Dict[str, Any]):
        """
        Log a telemetry event (async).
        Placeholder for Phase 3.4
        """
        # TODO: Implement logging (e.g., to Kafka/ClickHouse)
        logger.info(f"[TELEMETRY] {event_type}: {data}")

    def add_documents(self, collection_name: str, documents: List[str], metadatas: List[Dict], ids: List[str], org_id: str = None):
        """
        Add documents to a ChromaDB collection.
        Automatically chunks documents if they exceed limits.
        """
        try:
            from .chunker import chunker
        except ImportError:
            chunker = None
            logger.warning("Chunker not available, proceeding with whole documents")

        scoped_name = self._get_scoped_name(collection_name, org_id)
        
        logger.info(f"Attempting to ingest {len(documents)} source documents into {scoped_name}")
        collection = self._get_collection(scoped_name)
        
        if not collection:
            logger.error("Failed to get collection (collection is None)")
            return False

        try:
            final_docs = []
            final_ids = []
            final_metadatas = []

            for i, doc_text in enumerate(documents):
                source_id = ids[i]
                source_meta = metadatas[i] or {}

                if chunker:
                    chunks = chunker.split_text(doc_text)
                    logger.info(f"Split document {source_id} into {len(chunks)} chunks")
                else:
                    chunks = [doc_text]

                for chunk_idx, chunk in enumerate(chunks):
                    # Create chunk ID and metadata
                    chunk_id = f"{source_id}_{chunk_idx}"
                    chunk_meta = source_meta.copy()
                    chunk_meta.update({
                        "chunk_index": chunk_idx,
                        "parent_id": source_id,
                        "total_chunks": len(chunks)
                    })
                    
                    final_docs.append(chunk)
                    final_ids.append(chunk_id)
                    final_metadatas.append(chunk_meta)

            if not final_docs:
                logger.warning("No content to ingest after processing")
                return True

            # Process in batches to avoid hitting max batch size limits (Chroma default ~40k chars or 5k items usually safe)
            batch_size = 100
            total_batches = (len(final_docs) + batch_size - 1) // batch_size
            
            for b in range(total_batches):
                start = b * batch_size
                end = start + batch_size
                
                collection.add(
                    documents=final_docs[start:end],
                    metadatas=final_metadatas[start:end],
                    ids=final_ids[start:end]
                )
                
            logger.info(f"Successfully added {len(final_docs)} chunks to collection")
            return True

        except Exception as e:
            logger.error(f"Error adding documents to collection: {e}", exc_info=True)
            return False

    def retrieve_context(self, collection_name: str, query: str, org_id: str, n_results: int = 3) -> List[str]:
        """
        Retrieve context for RAG from ChromaDB.
        """
        scoped_name = self._get_scoped_name(collection_name, org_id)
        collection = self._get_collection(scoped_name)
        if not collection:
            return []
        
        results = collection.query(
            query_texts=[query],
            n_results=n_results
        )
        
        # Flatten results (list of lists)
        if results and results.get("documents") and results.get("distances"):
            documents = results["documents"][0]
            distances = results["distances"][0]
            
            # Filter by relevance threshold
            # ChromaDB default (L2): Lower is better. 0 = exact match.
            # 1.5 is a loose threshold, 0.5 is strict.
            # If distance > threshold, drop it.
            # Filter by relevance threshold
            # ChromaDB results are sorted by distance (ASC).
            # Always include the best match (index 0) even if it exceeds threshold.
            threshold = 1.5
            
            valid_docs = []
            
            if documents:
                # Always add the best match
                valid_docs.append(documents[0])
                logger.info(f"Added best match (distance {distances[0]:.4f})")
                
                # Check others
                for doc, dist in zip(documents[1:], distances[1:]):
                    if dist < threshold:
                        valid_docs.append(doc)
                    else:
                        logger.info(f"Dropped RAG result (distance {dist:.4f} > {threshold}): {doc[:50]}...")
            
            return valid_docs
        return []

data_engine = DataEngine()
