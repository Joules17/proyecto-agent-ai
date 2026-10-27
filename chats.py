import asyncio
import json
import math
from pathlib import Path

import streamlit as st

from agente import rag
from agente.agente_devoluciones import MCP_URL, listar_tools
from agente.orquestador import responder
from db import create_chat, create_message, delete_chat, get_messages, init_db, list_chats, read_chat

AUDITORIA_PATH = Path(__file__).resolve().parent / "logs" / "auditoria_mcp.jsonl"

ICONOS_RUTA = {"agente": "🛠️ Agente + tools MCP", "informacion": "📚 RAG", "conversacion": "💬 Respuesta directa"}

PROMPTS_DE_PRUEBA = [
    "¿Cuál es el plazo para devolver un producto?",
    "¿Cómo va mi pedido ECO-1004?",
    "Quiero devolver la botella térmica del pedido ECO-1001, me regalaron otra igual",
    "Quiero devolver el champú del pedido ECO-1003, no me gustó",
    "La lámpara solar del pedido ECO-1011 dejó de funcionar",
    "Quiero devolver la camiseta del pedido ECO-9999",
]


def chats_home():
    """
    Renders the main chats page where users can:
    - Create new chats with titles
    - View and manage previous chats
    - Navigate through paginated chat history
    """

    st.markdown("<h1 style='text-align: center;'>🌱 EcoMarket · Eco</h1>", unsafe_allow_html=True)
    st.markdown(
        "<p style='text-align: center;'>Asistente de servicio al cliente: estado de pedidos, "
        "devoluciones y dudas sobre productos y políticas.</p>",
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        col1, col2 = st.columns([0.8, 0.2])

        with col1:
            chat_title = st.text_input("Nueva conversación", placeholder="Ej.: Devolución botella", key="chat_title")

        with col2:
            st.markdown("<br>", unsafe_allow_html=True)  # Add vertical space
            if st.button("Crear", type="primary"):
                if chat_title:
                    chat_id = create_chat(chat_title)
                    st.query_params.from_dict({"chat_id": chat_id})
                    st.rerun()
                else:
                    st.warning("Escribe un título para la conversación")

    with st.container(border=True):
        st.subheader("Conversaciones anteriores")

        previous_chats = list_chats()

        # Pagination settings
        chats_per_page = 5
        total_pages = max(1, math.ceil(len(previous_chats) / chats_per_page))

        if "current_page" not in st.session_state:
            st.session_state.current_page = 1

        start_idx = (st.session_state.current_page - 1) * chats_per_page
        end_idx = start_idx + chats_per_page

        for chat in previous_chats[start_idx:end_idx]:
            chat_id, chat_title = chat[0], chat[1]
            with st.container(border=True):
                col1, col2, col3 = st.columns([0.6, 0.2, 0.2])
                with col1:
                    st.markdown(f"**{chat_title}**")
                with col2:
                    if st.button("📂 Abrir", key=f"open_{chat_id}"):
                        st.query_params.from_dict({"chat_id": chat_id})
                        st.rerun()
                with col3:
                    if st.button("🗑️ Borrar", key=f"delete_{chat_id}"):
                        delete_chat(chat_id)
                        st.rerun()

        # Pagination controls
        col1, col2, col3 = st.columns([1, 2, 1])
        with col1:
            if st.button("Anterior") and st.session_state.current_page > 1:
                st.session_state.current_page -= 1
                st.rerun()
        with col2:
            st.write(f"Página {st.session_state.current_page} de {total_pages}")
        with col3:
            if st.button("Siguiente") and st.session_state.current_page < total_pages:
                st.session_state.current_page += 1
                st.rerun()


def show_trace(trace: dict):
    """Muestra cómo se resolvió una respuesta: ruta del router, tools MCP llamadas y fuentes del RAG."""
    tools = trace.get("tools") or []
    resumen = ICONOS_RUTA.get(trace.get("ruta"), trace.get("ruta", "?"))
    if tools:
        resumen += f" · {len(tools)} tool(s)"
    if trace.get("error"):
        resumen += " · ⚠️ error"

    with st.expander(f"🔎 Cómo se resolvió: {resumen}"):
        st.markdown(
            f"**Router** ({trace.get('metodo_ruta')}): `{trace.get('ruta')}`: {trace.get('razon_ruta')}  \n"
            f"**Tiempo:** {trace.get('ms')} ms"
        )
        if trace.get("fuentes"):
            st.markdown("**Fuentes RAG:** " + ", ".join(f"`{f}`" for f in trace["fuentes"]))
        for i, llamada in enumerate(tools, 1):
            st.markdown(f"**{i}. `{llamada['tool']}`**" + (" ⚠️" if llamada.get("error") else ""))
            st.json({"argumentos": llamada["args"], "resultado": llamada["resultado"]}, expanded=False)
        if trace.get("error"):
            st.error(f"Error registrado: {trace['error']}")


@st.cache_data(ttl=20, show_spinner=False)
def mcp_status() -> tuple[bool, list[str] | str]:
    """Pregunta al servidor MCP qué tools publica (cacheado unos segundos)."""
    try:
        return True, asyncio.run(listar_tools())
    except BaseException as e:  # ExceptionGroup de anyio incluido
        return False, type(e).__name__


def read_audit_log(limit: int = 6) -> list[dict]:
    if not AUDITORIA_PATH.exists():
        return []
    lines = AUDITORIA_PATH.read_text("utf-8").strip().splitlines()[-limit:]
    return [json.loads(line) for line in reversed(lines)]


def chat_page(chat_id):
    """
    Display the chat page for a specific chat ID: chat history with the trace of each answer,
    the message input and a sidebar with the state of the MCP server and the action log.
    """

    chat = read_chat(chat_id)
    if not chat:
        st.error("Conversación no encontrada")
        return

    messages = get_messages(chat_id)

    st.subheader(f"💬 {chat[1]}")
    if not messages:
        st.info(
            "¡Hola! Soy **Eco** 🌱. Puedo revisar el estado de tu pedido, ayudarte con una devolución "
            "(verifico si aplica y genero la etiqueta) o resolver tus dudas sobre productos, envíos y políticas."
        )

    for sender, content, trace in messages:
        with st.chat_message("user" if sender == "user" else "assistant", avatar=None if sender == "user" else "🌱"):
            st.markdown(content)
            if trace:
                show_trace(trace)

    prompt = st.chat_input("Escribe tu mensaje...")
    prompt = prompt or st.session_state.pop("pending_prompt", None)
    if prompt:
        history = [(sender, content, (trace or {}).get("ruta")) for sender, content, trace in messages]
        create_message(chat_id, "user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant", avatar="🌱"):
            with st.spinner("Eco está revisando tu solicitud..."):
                answer = responder(prompt, history)
            st.markdown(answer.texto)

        create_message(chat_id, "ai", answer.texto, answer.traza())
        st.rerun()

    with st.sidebar:
        if st.button("⬅️ Volver a conversaciones"):
            st.query_params.clear()
            st.rerun()

        st.subheader("🔌 Servidor MCP")
        ok, detail = mcp_status()
        if ok:
            st.success(f"Conectado a `{MCP_URL}`")
            st.caption("Tools descubiertas: " + ", ".join(f"`{t}`" for t in detail))
        else:
            st.error(f"Sin conexión con `{MCP_URL}` ({detail})")
            st.caption("Inícialo con `uv run python -m mcp_server.server`")

        st.subheader("📚 Base de conocimiento")
        chunks = rag.knowledge_base_size()
        if chunks:
            st.caption(f"{chunks} fragmentos indexados de ./documentos")
        else:
            st.warning("Vacía: ejecuta `uv run python indexar_documentos.py`")

        st.subheader("💡 Prueba con")
        for i, example in enumerate(PROMPTS_DE_PRUEBA):
            if st.button(example, key=f"example_{i}", use_container_width=True):
                st.session_state.pending_prompt = example
                st.rerun()

        st.subheader("🧾 Registro de acciones")
        records = read_audit_log()
        if not records:
            st.caption("Aún no hay llamadas a tools.")
        for record in records:
            icon = "✅" if record.get("ok") else "⚠️"
            st.caption(f"{icon} `{record['ts'][11:]}` **{record['tool']}** → {record.get('codigo') or 'ok'}")


def main():
    """
    Main entry point for the chat application.

    Handles routing between the chats list page and individual chat pages using the
    `chat_id` URL parameter.
    """
    st.set_page_config(page_title="EcoMarket · Eco", page_icon="🌱")
    init_db()

    query_params = st.query_params
    if "chat_id" in query_params:
        chat_page(query_params["chat_id"])
    else:
        chats_home()


if __name__ == "__main__":
    main()
