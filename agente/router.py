"""
Router: decide qué ruta atiende cada mensaje del cliente.

    informacion  -> cadena RAG del Taller 2 (políticas, catálogo, envíos, FAQ). Sin acciones.
    agente       -> agente con tools MCP (consultar pedidos, verificar, generar etiqueta, escalar).
    conversacion -> respuesta directa (saludos, agradecimientos, temas fuera de EcoMarket).

Primero se aplican reglas deterministas (baratas y predecibles) y solo si ninguna aplica se
pregunta al LLM con salida estructurada. Si el LLM falla se usa la ruta más segura: informacion,
que solo lee la base de conocimiento y no puede ejecutar acciones.
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agente.llm import llm

logger = logging.getLogger("ecomarket.router")

Ruta = Literal["informacion", "agente", "conversacion"]

NUMERO_PEDIDO = re.compile(r"\bECO[-\s]?\d{4}\b", re.IGNORECASE)
MAX_PALABRAS_RESPUESTA_CORTA = 6


@dataclass
class DecisionRuta:
    ruta: Ruta
    razon: str
    metodo: Literal["regla", "llm", "respaldo"]


class ClasificacionLLM(BaseModel):
    """Ruta elegida para el mensaje del cliente."""

    ruta: Ruta = Field(description="Ruta que debe atender el mensaje.")
    razon: str = Field(description="Justificación en una frase corta.")


PROMPT_ROUTER = """
Eres el router del asistente de atención al cliente de EcoMarket (tienda en línea de productos
sostenibles). Clasifica el ÚLTIMO mensaje del cliente en una ruta:

- "agente": el cliente quiere que hagamos algo con SU pedido: saber el estado de su pedido,
  devolver un producto que compró, generar una etiqueta/guía de devolución, reportar un
  producto dañado que recibió, o hablar con una persona. También cuando responde a una pregunta
  que el asistente le hizo dentro de ese proceso (número de pedido, producto, confirmación).
- "informacion": preguntas generales sobre políticas, productos, precios, envíos, pagos,
  garantías o sostenibilidad que se responden con documentación, sin mirar un pedido concreto.
  Ej.: "¿cuál es el plazo para devolver?", "¿el champú se puede devolver?", "¿cuánto cuesta el envío?".
- "conversacion": saludos, agradecimientos, despedidas, preguntas sobre qué puede hacer el
  asistente o temas que no tienen nada que ver con EcoMarket.

Ten en cuenta la conversación previa para entender respuestas cortas.
"""


def decidir_ruta(mensaje: str, historial: list[tuple[str, str, str | None]]) -> DecisionRuta:
    """
    Args:
        mensaje: último mensaje del cliente.
        historial: mensajes previos como (sender, contenido, ruta) con sender "user" o "ai".
    """
    if NUMERO_PEDIDO.search(mensaje):
        return DecisionRuta("agente", "El mensaje menciona un número de pedido.", "regla")

    ultima_ruta_ai = next((ruta for sender, _, ruta in reversed(historial) if sender == "ai"), None)
    if ultima_ruta_ai == "agente" and len(mensaje.split()) <= MAX_PALABRAS_RESPUESTA_CORTA:
        return DecisionRuta(
            "agente", "Respuesta corta dentro de un proceso que está atendiendo el agente.", "regla"
        )

    contexto = "\n".join(
        f"{'Cliente' if sender == 'user' else 'Asistente'}: {contenido[:300]}"
        for sender, contenido, _ in historial[-4:]
    )
    try:
        clasificador = llm.with_structured_output(ClasificacionLLM)
        resultado = clasificador.invoke(
            [
                SystemMessage(PROMPT_ROUTER.strip()),
                HumanMessage(
                    f"Conversación previa:\n{contexto or '(ninguna)'}\n\nÚltimo mensaje del cliente: {mensaje}"
                ),
            ]
        )
        return DecisionRuta(resultado.ruta, resultado.razon, "llm")
    except ModelRateLimitError:
        raise  # cualquier otra ruta también fallaría: lo maneja el orquestador
    except Exception:
        logger.exception("El clasificador del router falló; se usa la ruta de respaldo")
        return DecisionRuta(
            "informacion", "El clasificador no respondió; se usa la ruta de solo lectura.", "respaldo"
        )
