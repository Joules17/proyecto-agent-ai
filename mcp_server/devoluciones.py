"""
Reglas de negocio y datos del "sistema de devoluciones" simulado de EcoMarket.

Este módulo no sabe nada de MCP ni de LLMs: recibe datos, aplica la política de
devoluciones de forma determinista y devuelve diccionarios. El servidor MCP
(server.py) solo lo expone como tools. Así la política no depende de que el LLM
"razone bien": la decisión de elegibilidad siempre la toma este código.
"""

import csv
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PEDIDOS_PATH = RAIZ / "data" / "pedidos.json"
# El catálogo es el mismo CSV que alimenta la base de conocimiento del RAG:
# una sola fuente de verdad para "¿es devolvible?" y "¿tiene garantía?".
CATALOGO_PATH = RAIZ / "documentos" / "catalogo-productos.csv"

PLAZO_DEVOLUCION_DIAS = 30
PLAZO_REPORTE_DANO_DIAS = 5
DIAS_PARA_ENVIAR_PAQUETE = 7
CATEGORIAS_NO_DEVOLVIBLES = {"Alimentos", "Higiene personal", "Higiene íntima", "Digital"}

MOTIVOS_DANO = {"danado", "defectuoso", "vencido"}
MOTIVOS = MOTIVOS_DANO | {"no_satisfecho", "cambio_de_opinion", "talla_o_modelo", "otro"}

PALABRAS_VACIAS = {"las", "los", "del", "una", "con", "para", "por", "que", "mis", "pedido", "producto"}

FORMATO_PEDIDO = re.compile(r"^ECO-?\s?(\d{4})$", re.IGNORECASE)


def fecha_hoy() -> date:
    """Fecha "actual" del sistema. Se fija por variable de entorno para que los pedidos
    de prueba (octubre de 2026, los del Taller 1 corridos 44 días) den siempre el mismo resultado."""
    return date.fromisoformat(os.getenv("ECOMARKET_FECHA_HOY", "2026-10-29"))


def db_path() -> Path:
    return Path(os.getenv("ECOMARKET_DB", RAIZ / "data" / "devoluciones.sqlite"))


# ---------------------------------------------------------------------------
# Respuestas estándar: los errores son resultados legibles, no excepciones,
# para que el agente pueda explicarle al cliente qué pasó y qué hacer.
# ---------------------------------------------------------------------------


def _error(codigo: str, mensaje: str, sugerencia: str, **extra) -> dict:
    return {"ok": False, "codigo": codigo, "mensaje": mensaje, "sugerencia": sugerencia, **extra}


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", sin_tildes.lower())


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------


def cargar_pedidos() -> dict[str, dict]:
    pedidos = json.loads(PEDIDOS_PATH.read_text("utf-8"))
    return {p["numero"]: p for p in pedidos}


def cargar_catalogo() -> dict[str, dict]:
    with CATALOGO_PATH.open(encoding="utf-8") as f:
        return {fila["producto"]: fila for fila in csv.DictReader(f)}


@contextmanager
def conectar() -> Iterator[sqlite3.Connection]:
    """Abre la base de estado (verificaciones, etiquetas, casos), hace commit y la cierra."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        _crear_tablas(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _crear_tablas(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS verificaciones (
            id TEXT PRIMARY KEY,
            pedido TEXT NOT NULL,
            producto TEXT NOT NULL,
            motivo TEXT NOT NULL,
            elegible INTEGER NOT NULL,
            resultado TEXT NOT NULL,
            creado TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS etiquetas (
            numero TEXT PRIMARY KEY,
            verificacion_id TEXT NOT NULL REFERENCES verificaciones(id),
            pedido TEXT NOT NULL,
            producto TEXT NOT NULL,
            resultado TEXT NOT NULL,
            creado TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (pedido, producto)
        );
        CREATE TABLE IF NOT EXISTS casos_humanos (
            numero TEXT PRIMARY KEY,
            pedido TEXT,
            motivo TEXT NOT NULL,
            resumen TEXT NOT NULL,
            creado TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def normalizar_numero_pedido(numero: str) -> str | None:
    """'eco 1004', 'ECO1004' o 'ECO-1004' -> 'ECO-1004'. None si no tiene el formato."""
    coincidencia = FORMATO_PEDIDO.match(numero.strip())
    return f"ECO-{coincidencia.group(1)}" if coincidencia else None


def _buscar_pedido(numero_pedido: str) -> tuple[dict | None, dict | None]:
    """Devuelve (pedido, None) o (None, error)."""
    numero = normalizar_numero_pedido(numero_pedido)
    if not numero:
        return None, _error(
            "FORMATO_INVALIDO",
            f"'{numero_pedido}' no tiene el formato de un número de pedido de EcoMarket.",
            "Pide al cliente el número de pedido con formato ECO-XXXX (está en el correo de confirmación).",
        )
    pedido = cargar_pedidos().get(numero)
    if not pedido:
        return None, _error(
            "PEDIDO_NO_ENCONTRADO",
            f"No existe ningún pedido con el número {numero}.",
            "Pide al cliente verificar el número. No inventes datos del pedido.",
        )
    return pedido, None


def _buscar_producto(pedido: dict, producto: str) -> tuple[str | None, dict | None]:
    """Empareja lo que escribió el cliente ('la botella') con un producto del pedido."""
    palabras = [p for p in _normalizar(producto).split() if len(p) > 2 and p not in PALABRAS_VACIAS]
    candidatos = [
        nombre
        for nombre in pedido["productos"]
        if palabras and all(p in _normalizar(nombre) for p in palabras)
    ]
    if not candidatos:
        # Segundo intento más permisivo: basta con una palabra en común
        candidatos = [
            nombre for nombre in pedido["productos"] if any(p in _normalizar(nombre) for p in palabras)
        ]
    if len(candidatos) == 1:
        return candidatos[0], None
    if not candidatos:
        return None, _error(
            "PRODUCTO_NO_EN_PEDIDO",
            f"El pedido {pedido['numero']} no contiene ningún producto que coincida con '{producto}'.",
            "Confirma con el cliente cuál de los productos del pedido quiere devolver.",
            productos_del_pedido=pedido["productos"],
        )
    return None, _error(
        "PRODUCTO_AMBIGUO",
        f"'{producto}' coincide con varios productos del pedido {pedido['numero']}.",
        "Pregunta al cliente cuál de estos productos quiere devolver.",
        productos_del_pedido=candidatos,
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def consultar_pedido(numero_pedido: str) -> dict:
    pedido, error = _buscar_pedido(numero_pedido)
    if error:
        return error
    return {"ok": True, **pedido}


def verificar_elegibilidad(numero_pedido: str, producto: str, motivo: str) -> dict:
    """Aplica la política de devoluciones y registra el resultado con un verificacion_id."""
    if motivo not in MOTIVOS:
        return _error(
            "MOTIVO_INVALIDO",
            f"'{motivo}' no es un motivo válido.",
            f"Usa uno de: {', '.join(sorted(MOTIVOS))}.",
        )
    pedido, error = _buscar_pedido(numero_pedido)
    if error:
        return error
    nombre, error = _buscar_producto(pedido, producto)
    if error:
        return error

    ficha = cargar_catalogo().get(nombre, {})
    categoria = ficha.get("categoria", "Desconocida")
    garantia = ficha.get("garantia", "-")
    base = {
        "ok": True,
        "pedido": pedido["numero"],
        "cliente": pedido["cliente"],
        "producto": nombre,
        "categoria": categoria,
        "motivo": motivo,
    }

    if pedido["estado"] != "Entregado":
        decision = _decision(
            False,
            "NO_ENTREGADO",
            f"El pedido está en estado '{pedido['estado']}': la devolución solo aplica a pedidos entregados (política, punto 6).",
            ["Si el pedido está retrasado, ofrece el enlace de rastreo.", "Si es una queja, escalar a un agente humano."],
        )
        return _registrar_verificacion(base | decision)

    dias = (fecha_hoy() - date.fromisoformat(pedido["fecha_entrega"])).days
    base["dias_desde_entrega"] = dias
    devolvible = ficha.get("devolvible") == "Sí" and categoria not in CATEGORIAS_NO_DEVOLVIBLES

    if motivo in MOTIVOS_DANO and dias <= PLAZO_REPORTE_DANO_DIAS:
        decision = _decision(
            True,
            "EXCEPCION_DANO",
            f"El producto llegó {motivo} y se reporta a los {dias} días de la entrega (máximo {PLAZO_REPORTE_DANO_DIAS}): aplica la excepción por daño (punto 4), aunque la categoría no sea devolvible.",
            [],
            requiere_foto=True,
        )
    elif not devolvible:
        alternativas = ["Recomendar otros productos del catálogo."]
        if motivo in MOTIVOS_DANO:
            alternativas.insert(0, "Escalar a un agente humano si el cliente considera que es un caso especial.")
        decision = _decision(
            False,
            "CATEGORIA_NO_DEVOLVIBLE",
            f"Los productos de la categoría '{categoria}' no se pueden devolver (punto 3). La excepción por daño solo aplica si se reporta en los primeros {PLAZO_REPORTE_DANO_DIAS} días y han pasado {dias}.",
            alternativas,
        )
    elif dias <= PLAZO_DEVOLUCION_DIAS:
        decision = _decision(
            True,
            "DENTRO_DE_PLAZO",
            f"Producto devolvible y han pasado {dias} de {PLAZO_DEVOLUCION_DIAS} días desde la entrega. Debe estar sin usar, en su empaque original y con etiquetas (punto 2).",
            [],
        )
    elif motivo in MOTIVOS_DANO and _garantia_vigente(garantia, pedido["fecha_entrega"]):
        decision = _decision(
            False,
            "APLICA_GARANTIA",
            f"Pasaron {dias} días (más de {PLAZO_DEVOLUCION_DIAS}), así que no aplica devolución, pero el producto tiene garantía de {garantia} y sigue vigente.",
            ["Escalar a un agente humano para tramitar la garantía (reparación o reemplazo)."],
        )
    else:
        decision = _decision(
            False,
            "FUERA_DE_PLAZO",
            f"Pasaron {dias} días desde la entrega y el plazo de devolución es de {PLAZO_DEVOLUCION_DIAS} días (punto 1).",
            ["Escalar a un agente humano si el cliente tiene una queja."],
        )

    return _registrar_verificacion(base | decision)


def generar_etiqueta(verificacion_id: str) -> dict:
    """Genera la guía prepagada. Solo funciona sobre una verificación elegible (guarda del servidor)."""
    with conectar() as conn:
        verificacion = conn.execute(
            "SELECT * FROM verificaciones WHERE id = ?", (verificacion_id.strip().upper(),)
        ).fetchone()
        if not verificacion:
            return _error(
                "VERIFICACION_NO_ENCONTRADA",
                f"No existe la verificación '{verificacion_id}'.",
                "Llama primero a verificar_elegibilidad_devolucion y usa el verificacion_id que devuelve.",
            )
        if not verificacion["elegible"]:
            return _error(
                "NO_ELEGIBLE",
                "Esta devolución no es elegible según la política, así que no se puede generar la etiqueta.",
                "Explica al cliente el motivo y ofrece las alternativas de la verificación.",
                verificacion=json.loads(verificacion["resultado"]),
            )

        existente = conn.execute(
            "SELECT resultado FROM etiquetas WHERE pedido = ? AND producto = ?",
            (verificacion["pedido"], verificacion["producto"]),
        ).fetchone()
        if existente:
            # Idempotente: un reintento del agente no crea una segunda etiqueta
            return json.loads(existente["resultado"]) | {"ya_existia": True}

        pedido = cargar_pedidos()[verificacion["pedido"]]
        consecutivo = conn.execute("SELECT COUNT(*) FROM etiquetas").fetchone()[0] + 1
        numero = f"ETQ-{fecha_hoy():%Y%m%d}-{consecutivo:04d}"
        verificado = json.loads(verificacion["resultado"])
        etiqueta = {
            "ok": True,
            "ya_existia": False,
            "numero_etiqueta": numero,
            "pedido": verificacion["pedido"],
            "producto": verificacion["producto"],
            "transportadora": "Servientrega",
            "ciudad_origen": pedido["ciudad"],
            "url_etiqueta": f"https://ecomarket.example/etiquetas/{numero}.pdf",
            "enviar_antes_de": (fecha_hoy() + timedelta(days=DIAS_PARA_ENVIAR_PAQUETE)).isoformat(),
            "reembolso": "Al mismo medio de pago en 5 a 10 días hábiles después de recibir el producto.",
            "instrucciones": [
                "Empaca el producto en su empaque original, con sus etiquetas.",
                "Imprime la etiqueta y pégala en el paquete.",
                "Entrégalo en cualquier punto Servientrega antes de la fecha límite.",
            ],
        }
        if verificado.get("requiere_foto"):
            etiqueta["instrucciones"].insert(
                0, "Responde al correo de confirmación con una foto del daño o defecto."
            )
        conn.execute(
            "INSERT INTO etiquetas (numero, verificacion_id, pedido, producto, resultado) VALUES (?, ?, ?, ?, ?)",
            (numero, verificacion["id"], verificacion["pedido"], verificacion["producto"], json.dumps(etiqueta, ensure_ascii=False)),
        )
    return etiqueta


def escalar_a_humano(motivo: str, resumen: str, numero_pedido: str = "") -> dict:
    numero_caso = "CASO-" + hashlib.sha1(f"{datetime.now().isoformat()}{resumen}".encode()).hexdigest()[:6].upper()
    with conectar() as conn:
        conn.execute(
            "INSERT INTO casos_humanos (numero, pedido, motivo, resumen) VALUES (?, ?, ?, ?)",
            (numero_caso, normalizar_numero_pedido(numero_pedido) if numero_pedido else None, motivo, resumen),
        )
    return {
        "ok": True,
        "numero_caso": numero_caso,
        "tiempo_respuesta": "Un agente humano escribirá al cliente en máximo 24 horas hábiles.",
    }


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _decision(elegible: bool, codigo: str, explicacion: str, alternativas: list[str], **extra) -> dict:
    return {
        "elegible": elegible,
        "codigo_decision": codigo,
        "explicacion": explicacion,
        "alternativas": alternativas,
        **extra,
    }


def _garantia_vigente(garantia: str, fecha_entrega: str) -> bool:
    coincidencia = re.match(r"(\d+) meses", garantia)
    if not coincidencia:
        return False
    vence = date.fromisoformat(fecha_entrega) + timedelta(days=30 * int(coincidencia.group(1)))
    return fecha_hoy() <= vence


def _registrar_verificacion(resultado: dict) -> dict:
    """Guarda la verificación. El id es determinista (pedido + producto + motivo + fecha), así
    que verificar dos veces lo mismo devuelve el mismo id: el agente puede re-verificar en un
    turno posterior sin crear duplicados."""
    clave = f"{resultado['pedido']}|{resultado['producto']}|{resultado['motivo']}|{fecha_hoy()}"
    verificacion_id = "VER-" + hashlib.sha1(clave.encode()).hexdigest()[:8].upper()
    resultado = {"verificacion_id": verificacion_id, **resultado}
    with conectar() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO verificaciones (id, pedido, producto, motivo, elegible, resultado) VALUES (?, ?, ?, ?, ?, ?)",
            (verificacion_id, resultado["pedido"], resultado["producto"], resultado["motivo"], int(resultado["elegible"]), json.dumps(resultado, ensure_ascii=False)),
        )
    return resultado
