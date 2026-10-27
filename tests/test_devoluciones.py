"""Pruebas de las reglas de devolución (sin LLM ni red): uv run pytest"""

import pytest

from mcp_server import devoluciones as d


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOMARKET_DB", str(tmp_path / "estado.sqlite"))
    monkeypatch.setenv("ECOMARKET_FECHA_HOY", "2026-09-15")


@pytest.mark.parametrize(
    "pedido, producto, motivo, elegible, codigo",
    [
        ("ECO-1001", "botella térmica", "cambio_de_opinion", True, "DENTRO_DE_PLAZO"),
        ("ECO-1003", "cepillo de dientes", "no_satisfecho", False, "CATEGORIA_NO_DEVOLVIBLE"),
        ("ECO-1006", "camiseta", "talla_o_modelo", False, "FUERA_DE_PLAZO"),
        ("ECO-1011", "lámpara", "defectuoso", False, "APLICA_GARANTIA"),
        ("ECO-1012", "toallas", "no_satisfecho", False, "CATEGORIA_NO_DEVOLVIBLE"),
        ("ECO-1008", "café", "danado", True, "EXCEPCION_DANO"),
        ("ECO-1008", "miel", "no_satisfecho", False, "CATEGORIA_NO_DEVOLVIBLE"),
        ("ECO-1004", "compostera", "no_satisfecho", False, "NO_ENTREGADO"),
        ("ECO-1009", "tarjeta de regalo", "otro", False, "NO_ENTREGADO"),
    ],
)
def test_politica(pedido, producto, motivo, elegible, codigo):
    r = d.verificar_elegibilidad(pedido, producto, motivo)
    assert r["ok"] is True
    assert r["elegible"] is elegible
    assert r["codigo_decision"] == codigo


@pytest.mark.parametrize(
    "pedido, producto, codigo",
    [
        ("ECO-9999", "botella", "PEDIDO_NO_ENCONTRADO"),
        ("pedido 12", "botella", "FORMATO_INVALIDO"),
        ("ECO-1001", "miel", "PRODUCTO_NO_EN_PEDIDO"),
        ("ECO-1007", "camiseta", "PRODUCTO_NO_EN_PEDIDO"),
    ],
)
def test_errores_legibles(pedido, producto, codigo):
    r = d.verificar_elegibilidad(pedido, producto, "no_satisfecho")
    assert r["ok"] is False
    assert r["codigo"] == codigo
    assert r["mensaje"] and r["sugerencia"]


def test_producto_ambiguo():
    r = d.verificar_elegibilidad("ECO-1002", "bambú", "no_satisfecho")
    assert r["ok"] is True  # solo un producto de ECO-1002 es de bambú
    r = d.verificar_elegibilidad("ECO-1010", "acero", "no_satisfecho")
    assert r["producto"].startswith("Botella")


def test_numero_pedido_flexible():
    assert d.consultar_pedido("eco 1002")["numero"] == "ECO-1002"
    assert d.consultar_pedido("ECO1002")["estado"] == "En tránsito"


def test_etiqueta_requiere_verificacion_elegible():
    assert d.generar_etiqueta("VER-INVENTADO")["codigo"] == "VERIFICACION_NO_ENCONTRADA"

    no_elegible = d.verificar_elegibilidad("ECO-1003", "champú", "no_satisfecho")
    assert d.generar_etiqueta(no_elegible["verificacion_id"])["codigo"] == "NO_ELEGIBLE"


def test_etiqueta_idempotente():
    v = d.verificar_elegibilidad("ECO-1001", "botella", "cambio_de_opinion")
    assert d.verificar_elegibilidad("ECO-1001", "botella", "cambio_de_opinion")["verificacion_id"] == v["verificacion_id"]

    primera = d.generar_etiqueta(v["verificacion_id"])
    assert primera["ok"] and primera["ya_existia"] is False
    assert primera["enviar_antes_de"] == "2026-09-22"

    # Otro motivo para el mismo producto no genera una segunda etiqueta
    otra = d.verificar_elegibilidad("ECO-1001", "botella", "no_satisfecho")
    segunda = d.generar_etiqueta(otra["verificacion_id"])
    assert segunda["ya_existia"] is True
    assert segunda["numero_etiqueta"] == primera["numero_etiqueta"]


def test_excepcion_dano_pide_foto():
    v = d.verificar_elegibilidad("ECO-1008", "café", "danado")
    etiqueta = d.generar_etiqueta(v["verificacion_id"])
    assert "foto" in etiqueta["instrucciones"][0]


def test_escalar():
    caso = d.escalar_a_humano("garantia", "Lámpara dejó de funcionar", "eco-1011")
    assert caso["numero_caso"].startswith("CASO-")
