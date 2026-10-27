"""
Construye la base de conocimiento del RAG con los documentos de ./documentos (Taller 2).
"""

import shutil
from pathlib import Path

from agente.rag import PERSIST_DIRECTORY, create_collection, load_document

DOCUMENTOS = Path(__file__).resolve().parent / "documentos"


def main():
    # Se reconstruye desde cero para no duplicar fragmentos al re-indexar
    shutil.rmtree(PERSIST_DIRECTORY, ignore_errors=True)

    documentos = []
    for archivo in sorted(DOCUMENTOS.iterdir()):
        docs = load_document(str(archivo))
        print(f"  {archivo.name}: {len(docs)} documento(s)")
        documentos.extend(docs)

    vectordb = create_collection(documentos)
    print(f"Base de conocimiento lista: {vectordb._collection.count()} fragmentos en {PERSIST_DIRECTORY}")


if __name__ == "__main__":
    main()
