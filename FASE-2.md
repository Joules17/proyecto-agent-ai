# Fase 2: Implementación y conexión de componentes

## 1. Extensión del código del taller 2

Se partió del código del taller 2 y se le agregó la lógica del agente en dos pasos:

1. **Definición de tools:** cada tool es una función de Python en
   `mcp_server/server.py`, publicada con el decorator `@mcp.tool`. Su docstring y sus parámetros
   son lo que el LLM lee para saber cuándo y cómo usarla. Las reglas de la política quedan aparte,
   en `mcp_server/devoluciones.py`.
2. **Inicialización del agente:** en `agente/agente_devoluciones.py` el agente se conecta al servidor
   MCP, descubre las 4 tools y se crea con `create_agent(llm, tools, system_prompt)`, usando el
   mismo Gemini de los talleres anteriores.

El mensaje del cliente pasa primero por el router (`agente/router.py`), que decide si va al RAG, al
agente o a una respuesta directa. Si va al agente, este decide qué tools llamar y el servidor MCP
aplica la política.

## 2. Manejo de respuestas

Con ejecuciones exitosas:

- **Devolución elegible:** Eco explica por qué aplica, recuerda las condiciones (sin usar, en su
  empaque) y pregunta si genera la etiqueta.
- **Etiqueta generada:** Eco entrega el número de etiqueta, el enlace, la fecha límite y los pasos.
- **No elegible:** Eco explica el motivo y ofrece las alternativas que trae la tool. Si
  aplica garantía, ofrece escalar el caso y entrega un número de caso.

Cuando algo falla:

- **Pedido inexistente o mal escrito:** pide verificar el número, sin inventar datos.
- **Producto que no está en el pedido o que es ambiguo:** pregunta cuál, mostrando los productos
  del pedido.
- **Faltan datos:** hace una sola pregunta con lo que falta.
- **Intento de saltarse la verificación:** el servidor responde `NO_ELEGIBLE` o
  `VERIFICACION_NO_ENCONTRADA` y no se genera la etiqueta.
- **Reintento o doble confirmación:** el servidor devuelve la misma etiqueta (`ya_existia: true`).
- **Error inesperado en una tool:** Eco se disculpa y ofrece escalar el caso.
- **Servidor MCP caído:** Eco avisa que no puede conectarse con el sistema de pedidos.
- **El agente entra en un bucle de tools:** se corta a los 12 pasos y Eco pide reformular.
- **Límite de solicitudes de Gemini:** Eco pide escribir de nuevo en los segundos que indique
  Gemini. Si lo que se agotó es la cuota del día, avisa que el servicio no está disponible y
  ofrece escalar a un asesor humano.
- **Base de conocimiento sin indexar:** indica el comando para construirla.

## 3. Evaluación del comportamiento

`evaluar_agente.py` corre 11 conversaciones (12 turnos) y revisa en cada turno la ruta elegida y
qué tools se deben y no se deben llamar. Así se comprueba que el agente distingue cuándo usar
herramientas y cuándo responder directamente. El detalle, con las respuestas de Eco, está en
[evaluacion/resultados.md](evaluacion/resultados.md).
