"""
Servidor MCP de EcoMarket: expone el sistema de pedidos y devoluciones como tools.

Transporte: Streamable HTTP en http://127.0.0.1:8001/mcp (configurable con MCP_HOST,
MCP_PORT). Cualquier cliente MCP (el agente de este proyecto, Claude Code, Cursor...)
puede conectarse y descubrir las tools sin código adicional.

    uv run python -m mcp_server.server
"""

import functools
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_server import devoluciones

load_dotenv()

AUDITORIA_PATH = Path(os.getenv("ECOMARKET_AUDITORIA", devoluciones.RAIZ / "logs" / "auditoria_mcp.jsonl"))
logger = logging.getLogger("ecomarket.mcp")

mcp = FastMCP(
    name="EcoMarket",
    instructions=(
        "Sistema de pedidos y devoluciones de EcoMarket. Flujo de devolución: "
        "verificar_elegibilidad_devolucion -> (el cliente confirma) -> generar_etiqueta_devolucion. "
        "Los resultados con ok=false explican el problema y sugieren qué hacer."
    ),
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8001")),
)


def auditado(tool):
    """Registra cada invocación (tool, argumentos, resultado, duración) en un JSONL.
    Si la lógica falla de forma inesperada, devuelve un error legible en vez de romper la sesión."""

    @functools.wraps(tool)
    def envoltura(**kwargs):
        inicio = time.perf_counter()
        try:
            resultado = tool(**kwargs)
        except Exception as e:  # el agente debe recibir algo explicable, nunca un traceback
            logger.exception("Fallo en %s", tool.__name__)
            resultado = {
                "ok": False,
                "codigo": "ERROR_INTERNO",
                "mensaje": "El sistema de devoluciones tuvo un error inesperado.",
                "sugerencia": "Discúlpate con el cliente y ofrece escalar el caso a un agente humano.",
                "detalle": type(e).__name__,
            }
        registro = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "tool": tool.__name__,
            "args": kwargs,
            "ok": resultado.get("ok"),
            "codigo": resultado.get("codigo") or resultado.get("codigo_decision"),
            "ms": round((time.perf_counter() - inicio) * 1000, 1),
            "resultado": resultado,
        }
        AUDITORIA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDITORIA_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        return resultado

    return envoltura


NumeroPedido = Annotated[str, Field(description="Número de pedido con formato ECO-XXXX, por ejemplo ECO-1001.")]


@mcp.tool(
    annotations=ToolAnnotations(title="Consultar pedido", readOnlyHint=True, openWorldHint=False),
)
@auditado
def consultar_pedido(numero_pedido: NumeroPedido) -> dict:
    """Consulta un pedido de EcoMarket: estado, productos, fechas, enlace de rastreo y motivo de
    retraso si lo hay. Úsala cuando el cliente pregunte por el estado de su pedido o cuando
    necesites saber qué productos tiene un pedido. Es de solo lectura."""
    return devoluciones.consultar_pedido(numero_pedido)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Verificar elegibilidad de devolución", readOnlyHint=False, idempotentHint=True, openWorldHint=False
    ),
)
@auditado
def verificar_elegibilidad_devolucion(
    numero_pedido: NumeroPedido,
    producto: Annotated[
        str, Field(description="Nombre del producto a devolver tal como lo menciona el cliente, ej. 'botella térmica'.")
    ],
    motivo: Annotated[
        Literal["no_satisfecho", "cambio_de_opinion", "talla_o_modelo", "danado", "defectuoso", "vencido", "otro"],
        Field(description="Motivo de la devolución. Usa danado/defectuoso/vencido solo si el cliente reporta un problema con el producto."),
    ],
) -> dict:
    """Aplica la política de devoluciones de EcoMarket a un producto de un pedido y decide si la
    devolución es elegible (plazo de 30 días, categorías no devolvibles, excepción por daño en
    los primeros 5 días, garantía). Devuelve `elegible`, la explicación, alternativas y un
    `verificacion_id` que se necesita para generar la etiqueta. Llama esta tool SIEMPRE antes
    de decirle al cliente si puede devolver un producto; no decidas la elegibilidad por tu cuenta.
    Es idempotente: verificar lo mismo dos veces devuelve el mismo verificacion_id."""
    return devoluciones.verificar_elegibilidad(numero_pedido, producto, motivo)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Generar etiqueta de devolución", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
@auditado
def generar_etiqueta_devolucion(
    verificacion_id: Annotated[
        str, Field(description="El verificacion_id devuelto por verificar_elegibilidad_devolucion, ej. VER-1A2B3C4D.")
    ],
) -> dict:
    """Genera la guía de envío prepagada para una devolución YA verificada como elegible y
    devuelve el número de etiqueta, el enlace para descargarla, la fecha límite de envío y las
    instrucciones. Llámala SOLO después de que el cliente confirme explícitamente que quiere
    generar la etiqueta. El servidor rechaza verificaciones inexistentes o no elegibles, y si
    ya existe una etiqueta para ese producto devuelve la misma (no crea duplicados)."""
    return devoluciones.generar_etiqueta(verificacion_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Escalar a agente humano", readOnlyHint=False, openWorldHint=True),
)
@auditado
def escalar_a_agente_humano(
    motivo: Annotated[
        Literal["garantia", "queja", "caso_especial", "error_del_sistema", "solicitud_del_cliente"],
        Field(description="Por qué se escala el caso."),
    ],
    resumen: Annotated[str, Field(description="Resumen breve del caso para el agente humano (2-3 frases).")],
    numero_pedido: Annotated[str, Field(description="Número de pedido relacionado, si lo hay.")] = "",
) -> dict:
    """Crea un caso para que un agente humano contacte al cliente. Úsala para garantías,
    quejas, casos que la política no cubre, cuando el cliente pide hablar con una persona o
    cuando otra tool falla con ERROR_INTERNO. Pregunta al cliente antes de escalar."""
    return devoluciones.escalar_a_humano(motivo, resumen, numero_pedido)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="streamable-http")
