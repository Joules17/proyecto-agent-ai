# Fase 4: Despliegue de la aplicación

## 1. Herramienta para interfaz: StreamLit

Se elige **Streamlit** porque reutiliza lo del taller 2 y porque en este proyecto importa mostrar
*por qué* respondió así el agente, no solo la respuesta.

## 2. Cómo se despliega

La app corre en dos procesos: la interfaz en Streamlit (puerto 8501), que llama a Gemini, y el
servidor MCP (puerto 8001) con las tools. Así el sistema de devoluciones queda como un servicio
aparte, como sería en un despliegue real.

```bash
uv venv
uv pip install -r requirements.txt
copy .env.example .env                    
uv run python indexar_documentos.py 

# Terminal 1: servidor MCP
uv run python -m mcp_server.server

# Terminal 2: interfaz
uv run streamlit run chats.py
```

## 3. La interfaz

- **Chat:** igual que en el taller 2, con la diferencia de que cada respuesta trae un desplegable
  "Cómo se resolvió" con la ruta, las tools usadas, las fuentes y el tiempo.
- **Barra lateral:** estado del servidor MCP (en rojo si está apagado), tamaño de la base de
  conocimiento, prompts de ejemplo y las últimas llamadas a tools.
