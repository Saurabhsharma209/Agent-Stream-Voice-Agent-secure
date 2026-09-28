import os
import asyncio
import hashlib
import logging
from typing import List, Dict, Any
from pathlib import Path

import boto3
import chromadb
from google import genai
from config import Config

logger = logging.getLogger(__name__)

# Optional: use langchain's text splitter if available
try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ImportError:
    RecursiveCharacterTextSplitter = None

class RAGManager:
    """Enterprise per‑company RAG manager using Chroma DB and AWS S3."""

    def __init__(self):
        self.chroma_client = None
        self.embed_cache = {}
        # 1. Connect to Centralized Chroma DB Server (with error safety)
        try:
            logger.info(f"Connecting to Chroma DB at {Config.CHROMA_HOST}:{Config.CHROMA_PORT}...")
            self.chroma_client = chromadb.HttpClient(
                host=Config.CHROMA_HOST,
                port=Config.CHROMA_PORT
            )
            # O4: Keep client connection pool warm
            try:
                self.chroma_client.heartbeat()
            except Exception as hb_err:
                logger.warning(f"Chroma DB heartbeat failed during warmup: {hb_err}")
            logger.info("✅ Connected to Chroma DB successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Chroma DB: {e}. RAG functions will be offline.")
        
        # 2. Initialize Gemini Client for embeddings (text-embedding-004)
        gcp_key_path = "project-gcp-key.json" if os.path.exists("project-gcp-key.json") else "/app/project-gcp-key.json"
        if os.path.exists(gcp_key_path):
            logger.info("🔑 project-gcp-key.json found. Initializing Gemini Client in Vertex AI mode...")
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = gcp_key_path
            # Pop GEMINI_API_KEY to prevent SDK credential conflicts (API key vs Service Account)
            os.environ.pop("GEMINI_API_KEY", None)
            self.gemini_client = genai.Client(vertexai=True)
            logger.info("✅ Gemini Client initialized via Vertex AI successfully.")
        else:
            logger.info("ℹ️ project-gcp-key.json not found. Initializing Gemini Client in Developer API key mode...")
            api_key = Config.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning("⚠️ GEMINI_API_KEY not set in configurations. Using fallback dummy key to prevent startup crash.")
                api_key = "DUMMY_GEMINI_KEY"
            self.gemini_client = genai.Client(api_key=api_key)
        
        # 3. Connect to S3 for raw document storage
        self.bucket_name = Config.AWS_S3_BUCKET_NAME
        self.s3_client = None
        if not self.bucket_name:
            logger.warning("⚠️ AWS_S3_BUCKET_NAME not set. Document uploads will skip S3 persistent raw file backing.")
        else:
            try:
                aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
                aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
                region_name = os.getenv("AWS_DEFAULT_REGION", "ap-south-1")
                
                if aws_access_key and aws_secret_key:
                    self.s3_client = boto3.client(
                        's3',
                        aws_access_key_id=aws_access_key,
                        aws_secret_access_key=aws_secret_key,
                        region_name=region_name
                    )
                else:
                    self.s3_client = boto3.client('s3', region_name=region_name)
                logger.info("✅ S3 Client initialized successfully using explicit credentials/region.")
            except Exception as s3_err:
                logger.error(f"❌ Failed to initialize S3 client: {s3_err}. Document uploads will skip S3 backing.")
                self.bucket_name = None

    # ---------------------------------------------------------------------
    # Tenant Index helpers
    # ---------------------------------------------------------------------
    def _collection_name(self, company_id: str) -> str:
        clean_cid = company_id.replace("_", "-").lower()
        clean_cid = "".join(c for c in clean_cid if c.isalnum() or c in ['-', '_'])
        name = f"tenant-{clean_cid}"
        if len(name) > 63:
            name = name[:63]
        return name

    def init_index(self, company_id: str):
        """Pre-initialize tenant collection in Chroma"""
        if not self.chroma_client:
            logger.error("Chroma DB client is offline. Skipping collection initialization.")
            return
        name = self._collection_name(company_id)
        logger.info(f"Initializing collection: {name} in Chroma DB")
        self.chroma_client.get_or_create_collection(name=name)

    def delete_index(self, company_id: str):
        """Delete tenant collection from Chroma"""
        if not self.chroma_client:
            logger.error("Chroma DB client is offline. Skipping collection deletion.")
            return
        name = self._collection_name(company_id)
        logger.info(f"Deleting collection: {name} from Chroma DB")
        try:
            self.chroma_client.delete_collection(name=name)
        except Exception as e:
            logger.warning(f"Failed to delete collection {name} (may not exist): {e}")

    def delete_document(self, company_id: str, doc_id: str, filename: str):
        """Delete specific document vectors from Chroma and its raw file from S3 using doc_id"""
        if not self.chroma_client:
            logger.error("Chroma DB client is offline. Skipping document deletion.")
            return
            
        col_name = self._collection_name(company_id)
        logger.info(f"Deleting document ID {doc_id} ('{filename}') from collection '{col_name}'...")
        
        try:
            collection = self.chroma_client.get_collection(name=col_name)
            # Delete chunks matching unique document_id metadata filter
            collection.delete(where={"document_id": str(doc_id)})
            logger.info(f"✅ Deleted vectors for document ID {doc_id} from Chroma.")
        except Exception as e:
            logger.error(f"❌ Failed to delete document vectors from Chroma: {e}")
            
        # Delete raw file from S3
        if self.bucket_name:
            s3_key = f"documents/{company_id}/{doc_id}_{filename}"
            logger.info(f"Deleting s3://{self.bucket_name}/{s3_key}...")
            try:
                self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
                logger.info(f"✅ Deleted S3 file for document ID {doc_id}.")
            except Exception as e:
                logger.error(f"❌ Failed to delete S3 file for document ID {doc_id}: {e}")

    # ---------------------------------------------------------------------
    # Embeddings & Document processing
    # ---------------------------------------------------------------------
    async def _embed_text(self, text: str) -> List[float]:
        """Generate a 768-dimension embedding via Gemini API"""
        text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if text_hash in getattr(self, 'embed_cache', {}):
            # Move to end for true LRU behavior
            emb = self.embed_cache.pop(text_hash)
            self.embed_cache[text_hash] = emb
            return emb
            
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.gemini_client.models.embed_content(
                model="text-embedding-004",
                contents=text,
            )
        )
        emb = response.embeddings[0].values
        
        if hasattr(self, 'embed_cache'):
            self.embed_cache[text_hash] = emb
            if len(self.embed_cache) > getattr(Config, 'EMBED_CACHE_SIZE', 1000):
                # Simple FIFO eviction
                first_key = next(iter(self.embed_cache))
                del self.embed_cache[first_key]
                
        return emb

    def _split_text(self, text: str) -> List[str]:
        """Split text into manageable chunks for vector search"""
        if RecursiveCharacterTextSplitter:
            splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
            return splitter.split_text(text)
        # Fallback simple split
        return [text[i : i + 500] for i in range(0, len(text), 500)]

    async def upload_documents(self, company_id: str, filename: str, file_body: bytes, text_content: str, doc_id: str, on_progress=None):
        """Upload raw file to S3, chunk and generate embeddings using Gemini, and index in Chroma DB"""
        if not self.chroma_client:
            raise RuntimeError("Chroma DB is offline. Cannot upload documents.")
            
        # A. Save raw file to S3
        if self.bucket_name:
            s3_key = f"documents/{company_id}/{doc_id}_{filename}"
            logger.info(f"Uploading document to S3: s3://{self.bucket_name}/{s3_key}")
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.put_object(
                        Bucket=self.bucket_name,
                        Key=s3_key,
                        Body=file_body
                    )
                )
            except Exception as e:
                logger.error(f"❌ Failed to upload document to S3: {e}")
                raise

        # B. Load Chroma Collection
        col_name = self._collection_name(company_id)
        collection = self.chroma_client.get_or_create_collection(name=col_name)

        # C. Chunk text
        chunks = self._split_text(text_content)
        total = len(chunks)
        logger.info(f"Chunked document into {total} fragments for tenant {company_id}")

        # D. Batch embed and insert into Chroma
        for idx, chunk in enumerate(chunks):
            embedding = await self._embed_text(chunk)
            chunk_id = f"{col_name}_doc_{doc_id}_{hashlib.sha256(chunk.encode('utf-8')).hexdigest()[:16]}"
            
            # Run sync chroma call in executor to prevent event loop blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: collection.add(
                    embeddings=[embedding],
                    documents=[chunk],
                    ids=[chunk_id],
                    metadatas=[{"source": filename, "company_id": company_id, "document_id": str(doc_id)}]
                )
            )
            # Fire progress callback after each chunk
            if on_progress:
                on_progress(idx + 1, total)

        logger.info(f"Successfully indexed document chunks in Chroma DB collection: {col_name}")

    # ---------------------------------------------------------------------
    # Vector Search
    # ---------------------------------------------------------------------
    async def search(self, company_id: str, query: str, top_k: int = 3, document_ids: List[Any] = None) -> List[Dict[str, Any]]:
        """Perform similarity search against the tenant's collection using Gemini embedded query"""
        if not self.chroma_client:
            logger.error("Chroma DB is offline. Cannot perform search.")
            return []
            
        col_name = self._collection_name(company_id)
        
        try:
            # Query chroma in executor
            loop = asyncio.get_event_loop()
            collection = await loop.run_in_executor(
                None,
                lambda: self.chroma_client.get_collection(name=col_name)
            )
        except Exception:
            logger.warning(f"Chroma collection not found: {col_name}. Returning empty search results.")
            return []

        # Generate query embedding
        query_emb = await self._embed_text(query)

        # Build filter dict if document_ids are provided
        where_filter = None
        if document_ids:
            # Convert all to strings cleanly
            normalized_ids = [str(x) for x in document_ids]
            if len(normalized_ids) == 1:
                where_filter = {"document_id": normalized_ids[0]}
            elif len(normalized_ids) > 1:
                where_filter = {"document_id": {"$in": normalized_ids}}

        # Perform ANN search
        loop = asyncio.get_event_loop()
        query_kwargs = {
            "query_embeddings": [query_emb],
            "n_results": top_k
        }
        if where_filter:
            query_kwargs["where"] = where_filter

        results = await loop.run_in_executor(
            None,
            lambda: collection.query(**query_kwargs)
        )

        # Convert results to standard dictionary format
        outputs = []
        if results and 'documents' in results and results['documents']:
            docs = results['documents'][0]
            metadatas = results['metadatas'][0] if 'metadatas' in results and results['metadatas'] else [{}] * len(docs)
            for doc, meta in zip(docs, metadatas):
                outputs.append({
                    "chunk_text": doc,
                    "metadata": meta
                })
                
        return outputs

