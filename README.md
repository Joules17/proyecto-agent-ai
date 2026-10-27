# Proyecto final: agente de devoluciones de EcoMarket

Continuación de los talleres [1 (prompt engineering)](https://github.com/Joules17/prompt-engineering-taller-1)
y [2 (RAG)](https://github.com/Joules17/rag-taller-2). En este proyecto el asistente de
**EcoMarket** ya no solo responde preguntas: consulta pedidos, verifica si una devolución cumple la
política, genera la etiqueta de devolución y escala casos a un humano.

- **Fase 1:** [Diseño de la arquitectura del agente](FASE-1.md)
- **Fase 2:** [Implementación y conexión de componentes](FASE-2.md)
- **Fase 3:** [Análisis crítico y propuestas de mejora](FASE-3.md)
- **Fase 4:** [Despliegue de la aplicación](FASE-4.md)

## Arquitectura

![Arquitectura del proyecto](docs/arquitectura.png)

Un router decide a dónde va cada mensaje: las preguntas generales van al RAG del taller 2, los
saludos se responden directo y todo lo que tenga que ver con un pedido va al agente, que usa las
tools del servidor MCP. El detalle está en la [Fase 1](FASE-1.md).

| Pieza | Tecnología |
|---|---|
| LLM | Google Gemini (`gemini-3.6-flash`) |
| Embeddings y base vectorial | `gemini-embedding-001` + Chroma (taller 2) |
| Agente | LangChain 1.x (`create_agent`) + `langchain-mcp-adapters` |
| Servidor de tools | SDK oficial de MCP para Python (`FastMCP`), transporte Streamable HTTP |
| Interfaz | Streamlit |

## Estructura

```
├── chats.py                  # Interfaz Streamlit (basada en la app de chats del taller 2)
├── db.py                     # Chats y mensajes en SQLite
├── agente/
│   ├── orquestador.py        # Router -> ruta -> respuesta, manejo de errores y trazas
│   ├── router.py             # Reglas + clasificador con Gemini
│   ├── agente_devoluciones.py# Agente que descubre sus tools en el servidor MCP
│   ├── rag.py                # RAG del taller 2
│   └── llm.py                # Modelo Gemini compartido
├── mcp_server/
│   ├── server.py             # Servidor MCP con las 4 tools y la auditoría
│   └── devoluciones.py       # Reglas de la política de devoluciones
├── data/pedidos.json         # Los 12 pedidos del taller 1
├── documentos/               # Base de conocimiento del taller 2
├── docs/                     # Diagrama de arquitectura (Excalidraw)
├── evaluacion/               # Resultados de la evaluación del agente
├── indexar_documentos.py     # Construye la base vectorial
├── evaluar_agente.py         # Evaluación del comportamiento del agente
└── tests/                    # Pruebas de las reglas de devolución (sin LLM)
```

## Instalación

```bash
uv venv
uv pip install -r requirements.txt
copy .env.example .env               
uv run python indexar_documentos.py 
```

## Uso

```bash
# Terminal 1: servidor MCP (http://127.0.0.1:8001/mcp)
uv run python -m mcp_server.server

# Terminal 2: interfaz (http://localhost:8501)
uv run streamlit run chats.py
```

Pruebas y evaluación:

```bash
uv run pytest                     # reglas de devolución
uv run python evaluar_agente.py   # comportamiento del agente (necesita el servidor MCP)
```
