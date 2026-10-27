"""Modelo Gemini compartido por el router, el RAG, el agente y la ruta de conversación."""
import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
FECHA_HOY = os.getenv("ECOMARKET_FECHA_HOY", "2026-09-15")

llm = ChatGoogleGenerativeAI(
    model=GEMINI_MODEL,
    google_api_key=os.getenv("GEMINI_API_KEY"),
    max_retries=2,
    timeout=60,
)
