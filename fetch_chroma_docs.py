import os
import sys
import chromadb
from config import Config
from core.rag_manager import RAGManager

def main():
    try:
        client = chromadb.HttpClient(host=Config.CHROMA_HOST, port=Config.CHROMA_PORT)
        print(f"Connected to Chroma DB at {Config.CHROMA_HOST}:{Config.CHROMA_PORT}")
    except Exception as e:
        print(f"Failed to connect to Chroma: {e}")
        return

    company_id = os.getenv("COMPANY_ID")
    if not company_id:
        print("Set COMPANY_ID in your environment / .env")
        return
    doc_ids = [d for d in os.getenv("DOC_IDS", "").split(",") if d]
    if not doc_ids:
        print("Set DOC_IDS (comma-separated) in your environment / .env")
        return
    
    rm = RAGManager()
    col_name = rm._collection_name(company_id)
    print(f"Collection Name: {col_name}")
    
    try:
        collection = client.get_collection(name=col_name)
    except Exception as e:
        print(f"Failed to get collection: {e}")
        return
        
    print(f"Collection found. Fetching documents with doc IDs: {doc_ids}")
    
    # Fetch from Chroma
    try:
        results = collection.get(
            where={"document_id": {"$in": doc_ids}}
        )
        
        docs = results.get('documents', [])
        metadatas = results.get('metadatas', [])
        
        print(f"Found {len(docs)} chunks.")
        
        grouped = {}
        for doc, meta in zip(docs, metadatas):
            d_id = meta.get('document_id')
            source = meta.get('source', 'Unknown')
            if d_id not in grouped:
                grouped[d_id] = {'source': source, 'chunks': []}
            grouped[d_id]['chunks'].append(doc)
            
        for d_id, data in grouped.items():
            print(f"\n{'='*50}")
            print(f"DOCUMENT ID: {d_id} | SOURCE: {data['source']}")
            print(f"{'='*50}")
            full_text = "\n".join(data['chunks'])
            print(full_text)
            
    except Exception as e:
        print(f"Error querying collection: {e}")

if __name__ == "__main__":
    main()
