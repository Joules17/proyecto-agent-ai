"""
Ruta "informacion" del router: el sistema RAG del Taller 2.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import (
    CSVLoader,
    Docx2txtLoader,
    PyPDFLoader,
    TextLoader,
    UnstructuredHTMLLoader,
    UnstructuredMarkdownLoader,
)
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from agente.llm import llm

load_dotenv()

PERSIST_DIRECTORY = str(Path(__file__).resolve().parent.parent / "persist")
COLLECTION_NAME = "ecomarket_kb"

# Changing the embedding model requires deleting ./persist and re-indexing:
# vectors from different models are not comparable.
embeddings = GoogleGenerativeAIEmbeddings(
    model=os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001"),
    google_api_key=os.getenv("GEMINI_API_KEY"),
)

# Tries to cut by paragraph, then line, then sentence, so each rule or section stays whole.
text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)


def load_document(file_path: str) -> list[Document]:
    """
    Load a document from a file path.
    Supports .txt, .pdf, .docx, .csv, .html, .md and .json files.

    Raises:
    ValueError: If the file type is not supported.
    """
    _, file_extension = os.path.splitext(file_path)

    if file_extension == ".txt":
        loader = TextLoader(file_path, encoding="utf-8")
    elif file_extension == ".pdf":
        loader = PyPDFLoader(file_path)
    elif file_extension == ".docx":
        loader = Docx2txtLoader(file_path)
    elif file_extension == ".csv":
        loader = CSVLoader(file_path, encoding="utf-8")
    elif file_extension == ".html":
        loader = UnstructuredHTMLLoader(file_path)
    elif file_extension == ".md":
        loader = UnstructuredMarkdownLoader(file_path)
    elif file_extension == ".json":
        return load_faq_json(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_extension}")

    return loader.load()


def load_faq_json(file_path: str) -> list[Document]:
    """
    Load a FAQ file: a JSON list of objects with "pregunta" and "respuesta".
    Each question-answer pair becomes its own Document, so the question always
    stays next to its answer.
    """
    with open(file_path, encoding="utf-8") as f:
        faqs = json.load(f)

    return [
        Document(
            page_content=f"Pregunta: {faq['pregunta']}\nRespuesta: {faq['respuesta']}",
            metadata={"source": file_path, "id": faq.get("id"), "tema": faq.get("tema")},
        )
        for faq in faqs
    ]


def create_collection(documents: list[Document]) -> Chroma:
    """Split the documents and build the knowledge base collection from scratch."""
    texts = text_splitter.split_documents(documents)
    return Chroma.from_documents(
        documents=texts,
        embedding=embeddings,
        persist_directory=PERSIST_DIRECTORY,
        collection_name=COLLECTION_NAME,
    )


def load_collection() -> Chroma:
    """Load the knowledge base collection from disk."""
    return Chroma(
        persist_directory=PERSIST_DIRECTORY,
        embedding_function=embeddings,
        collection_name=COLLECTION_NAME,
    )


def knowledge_base_size() -> int:
    """Number of chunks indexed (0 if `indexar_documentos.py` has not been run)."""
    if not os.path.exists(PERSIST_DIRECTORY):
        return 0
    return load_collection()._collection.count()


def load_retriever(score_threshold: float = 0.6):
    """
    Create a retriever that only returns chunks with a similarity score above the threshold,
    so an unrelated question gets an empty context instead of noise.
    """
    return load_collection().as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"score_threshold": score_threshold},
    )


ROLE_PROMPT = """
Actúa como "Eco", un agente de servicio al cliente amable y preciso de EcoMarket,
una tienda en línea de productos sostenibles. Tuteas al cliente.
Respondes ÚNICAMENTE con la información de EcoMarket que aparece entre >>>>> y <<<<<.
Nunca inventas productos, precios, plazos, políticas ni estados de pedidos.
"""

INSTRUCTION_PROMPT = """
Información de EcoMarket:
>>>>>
{context}
<<<<<

Consulta del cliente: {question}

Sigue estos pasos:
1. Busca en la información de EcoMarket lo que responde la consulta.
2. Si la información no alcanza para responder, dilo con amabilidad, explica que no
   tienes esa información y ofrece comunicar al cliente con un agente humano.
   No completes la respuesta con conocimiento general.
3. Si el cliente quiere saber el estado de un pedido o devolver un producto de un pedido
   suyo, explica la regla general si aplica y pídele el número de pedido (formato ECO-XXXX)
   para revisarlo. No supongas el estado de ningún pedido.
4. Si la información sí alcanza, responde de forma clara y directa. Si es una regla con
   excepciones (por ejemplo, productos dañados), menciona la excepción.

Responde en español, en máximo 120 palabras, y cierra preguntando si puedes ayudar en
algo más.
"""

# Few-shot: a normal case and a case where the context does not have the answer
POSITIVE_EXAMPLE = """
Información de EcoMarket:
>>>>>
El kit de jabones artesanales tiene garantía de 3 meses por defectos de fabricación.
<<<<<

Consulta del cliente: ¿El kit de jabones tiene garantía?
"""

POSITIVE_OUTPUT = """
¡Hola! 🌱 Sí, el kit de jabones artesanales tiene una garantía de **3 meses** por
defectos de fabricación. Si notas algún defecto dentro de ese tiempo, escríbenos y lo
revisamos.

¿Te puedo ayudar con algo más?
"""

NEGATIVE_EXAMPLE = """
Información de EcoMarket:
>>>>>
El envío es gratis en compras desde $150.000.
<<<<<

Consulta del cliente: ¿Venden paneles solares para techo?
"""

NEGATIVE_OUTPUT = """
¡Hola! Gracias por escribirnos. 🌱 No tengo información sobre paneles solares para
techo en nuestro catálogo, así que no quiero darte una respuesta equivocada.

Si quieres, puedo comunicarte con uno de nuestros agentes para que lo revise contigo.
¿Te puedo ayudar con algo más?
"""


def format_docs(docs: list[Document]) -> str:
    """Join the retrieved chunks into a single text block for the prompt."""
    if not docs:
        return "(No se encontró información relacionada.)"
    return "\n\n---\n\n".join(doc.page_content for doc in docs)


def generate_answer_from_context(question: str) -> tuple[str, list[str]]:
    """
    Answer a question with the knowledge base (same chain as Taller 2).

    Returns:
        The answer and the sources of the retrieved chunks (for the UI trace).
    """
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", ROLE_PROMPT.strip()),
            ("human", POSITIVE_EXAMPLE.strip()),
            ("ai", POSITIVE_OUTPUT.strip()),
            ("human", NEGATIVE_EXAMPLE.strip()),
            ("ai", NEGATIVE_OUTPUT.strip()),
            ("human", INSTRUCTION_PROMPT.strip()),
        ]
    )

    docs = load_retriever().invoke(question)
    rag_chain = {"context": lambda _: format_docs(docs), "question": RunnablePassthrough()} | prompt | llm
    sources = sorted({os.path.basename(doc.metadata.get("source", "?")) for doc in docs})
    return rag_chain.invoke(question).text, sources
