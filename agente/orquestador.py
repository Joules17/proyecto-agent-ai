"""
Punto de entrada único del asistente: router -> ruta -> respuesta + traza.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError

from agente import rag
from agente.agente_devoluciones import ejecutar_agente
from agente.llm import llm
from agente.router import decidir_ruta

logger = logging.getLogger("ecomarket.orquestador")
TRAZAS_PATH = Path(__file__).resolve().parent.parent / "logs" / "trazas_agente.jsonl"

MENSAJE_MCP_CAIDO = (
    "¡Lo siento! 😔 En este momento no puedo conectarme con el sistema de pedidos y devoluciones, "
    "así que no quiero darte información equivocada. Por favor intenta de nuevo en unos minutos; "
    "si es urgente, puedo pasarte con uno de nuestros agentes. ¿Te puedo ayudar con algo más?"
)
MENSAJE_BUCLE = (
    "Lo siento, tu solicitud se complicó más de lo esperado y no pude completarla. 🙏 "
    "¿Me la puedes contar de nuevo con el número de pedido y el producto? "
    "Si prefieres, te comunico con un agente humano."
)
MENSAJE_LIMITE = (
    "Estoy atendiendo muchas solicitudes en este momento. ⏳ Por favor escríbeme de nuevo en "
    "unos {segundos} segundos y continúo con tu solicitud justo donde la dejamos."
)
MENSAJE_CUOTA_AGOTADA = (
    "Lo siento, 😔 en este momento el asistente alcanzó su límite de uso y no puedo atender tu "
    "solicitud. Por favor intenta más tarde; si es urgente, uno de nuestros agentes humanos puede ayudarte."
)
# Si el proveedor pide esperar más que esto, no es un pico momentáneo sino la cuota agotada
MAX_ESPERA_RAZONABLE_SEGUNDOS = 120
MENSAJE_ERROR = (
    "¡Ups! Tuve un problema técnico al procesar tu mensaje. 🙏 Por favor intenta de nuevo; "
    "si el problema continúa, te comunico con uno de nuestros agentes."
)
MENSAJE_SIN_BASE = (
    "Aún no tengo cargada la información de EcoMarket para responder esta pregunta. "
    "(Administrador: ejecuta `uv run python indexar_documentos.py`.)"
)

PROMPT_CONVERSACION = """
Actúa como "Eco", el asistente de servicio al cliente de EcoMarket, una tienda en línea de
productos sostenibles. Tuteas al cliente y eres cálido y breve (máximo 60 palabras).
- Si saluda, agradece o se despide, responde con amabilidad.
- Si pregunta qué puedes hacer: consultar el estado de un pedido, gestionar devoluciones
  (verificar si aplica y generar la etiqueta) y responder dudas sobre productos, envíos y políticas.
- Si el tema no tiene relación con EcoMarket, dilo con amabilidad y vuelve a ofrecer tu ayuda.
  No respondas preguntas de cultura general, programación u otros temas.
"""


@dataclass
class Respuesta:
    texto: str
    ruta: str
    razon_ruta: str
    metodo_ruta: str
    tools: list[dict] = field(default_factory=list)
    fuentes: list[str] = field(default_factory=list)
    error: str | None = None
    ms: float = 0

    def traza(self) -> dict:
        """Lo que se guarda junto al mensaje para mostrar "Cómo se resolvió" en la interfaz."""
        return {k: v for k, v in asdict(self).items() if k != "texto"}


def responder(mensaje: str, historial: list[tuple[str, str, str | None]]) -> Respuesta:
    inicio = time.perf_counter()
    respuesta = Respuesta("", ruta="sin_ruta", razon_ruta="", metodo_ruta="")

    try:
        decision = decidir_ruta(mensaje, historial)
        respuesta.ruta, respuesta.razon_ruta, respuesta.metodo_ruta = decision.ruta, decision.razon, decision.metodo

        if decision.ruta == "agente":
            resultado = asyncio.run(ejecutar_agente(mensaje, historial))
            respuesta.texto, respuesta.tools = resultado.texto, resultado.tools
        elif decision.ruta == "informacion":
            if rag.knowledge_base_size() == 0:
                respuesta.texto, respuesta.error = MENSAJE_SIN_BASE, "BASE_DE_CONOCIMIENTO_VACIA"
            else:
                respuesta.texto, respuesta.fuentes = rag.generate_answer_from_context(mensaje)
        else:
            respuesta.texto = llm.invoke(
                [SystemMessage(PROMPT_CONVERSACION.strip()), HumanMessage(mensaje)]
            ).text
    except GraphRecursionError:
        logger.exception("El agente superó el límite de pasos")
        respuesta.texto, respuesta.error = MENSAJE_BUCLE, "LIMITE_DE_PASOS"
    except ModelRateLimitError as e:
        logger.warning("Límite de solicitudes de Gemini: %s", e)
        espera = re.search(r"'retryDelay': '(\d+)s'", str(e))
        segundos = int(espera.group(1)) if espera else 30
        if segundos > MAX_ESPERA_RAZONABLE_SEGUNDOS:
            respuesta.texto, respuesta.error = MENSAJE_CUOTA_AGOTADA, "CUOTA_LLM_AGOTADA"
        else:
            respuesta.texto = MENSAJE_LIMITE.format(segundos=segundos)
            respuesta.error = "LIMITE_DE_SOLICITUDES_LLM"
    except Exception as e:
        logger.exception("Error atendiendo la ruta %s", respuesta.ruta)
        if respuesta.ruta == "agente" and _es_error_de_conexion(e):
            respuesta.texto, respuesta.error = MENSAJE_MCP_CAIDO, "MCP_NO_DISPONIBLE"
        else:
            respuesta.texto, respuesta.error = MENSAJE_ERROR, f"{type(e).__name__}: {e}"[:300]

    if not respuesta.texto.strip():
        respuesta.texto, respuesta.error = MENSAJE_ERROR, "RESPUESTA_VACIA"

    respuesta.ms = round((time.perf_counter() - inicio) * 1000)
    _registrar(mensaje, respuesta)
    return respuesta


def _es_error_de_conexion(error: BaseException) -> bool:
    """El cliente MCP envuelve los fallos de red en ExceptionGroup (anyio); se revisan todos."""
    if isinstance(error, BaseExceptionGroup):
        return any(_es_error_de_conexion(e) for e in error.exceptions)
    nombres = {type(e).__name__ for e in (error, error.__cause__, error.__context__) if e}
    return bool(nombres & {"ConnectError", "ConnectTimeout", "ConnectionRefusedError", "McpError"})


def _registrar(mensaje: str, respuesta: Respuesta) -> None:
    """Una línea JSON por mensaje: base para monitoreo, alertas y auditoría."""
    TRAZAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    registro = {"ts": datetime.now().isoformat(timespec="seconds"), "mensaje": mensaje, **asdict(respuesta)}
    with TRAZAS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
