"""
Editor-in-Chief Agent: autonomous fact-checking and style review for editorial articles.

This agent receives a draft article, then:
1. Extracts every factual claim, statistic, and source mentioned
2. Verifies each one via web search (Perplexity)
3. Checks style against the editorial guidelines and reference examples
4. Returns a structured verdict: APROBADO / REQUIERE CAMBIOS / RECHAZADO
"""

import os
import json
import anthropic
from tools.search_tools import web_search
from tools.sheets_tools import get_editorial_style, get_editorial_references, set_editor_verdict

client = anthropic.Anthropic()

EDITOR_TOOLS = [
    {
        "name": "verify_claim",
        "description": (
            "Busca en internet para verificar si una afirmación, dato o fuente "
            "del artículo es real y precisa. Úsalo para CADA dato, estadística, "
            "nombre de empresa, cifra o fuente mencionada en el artículo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "claim": {
                    "type": "string",
                    "description": "La afirmación o dato exacto a verificar",
                },
                "search_query": {
                    "type": "string",
                    "description": "Búsqueda web optimizada para verificar el dato",
                },
            },
            "required": ["claim", "search_query"],
        },
    },
]


def _execute_editor_tool(name: str, tool_input: dict) -> str:
    if name == "verify_claim":
        result = web_search(tool_input["search_query"])
        return f"Claim: {tool_input['claim']}\nResultado búsqueda:\n{result}"
    return f"Herramienta desconocida: {name}"


def _build_style_context(platform: str) -> tuple[str, str]:
    """Load style rules and references for a platform."""
    style_rules = get_editorial_style(platform=platform)
    references = get_editorial_references(platform=platform)

    style_text = "\n".join(
        f"- {r['regla']}: {r['descripcion']}" for r in style_rules
    ) if style_rules else "(sin reglas de estilo definidas)"

    refs_text = "\n".join(
        f"- {r['nombre']} ({r['plataforma']}): {r.get('notas_estilo', '')}"
        for r in references
    ) if references else "(sin referencias definidas)"

    return style_text, refs_text


def _run_agentic_loop(system_prompt: str, user_message: str, tools: list,
                      tool_executor, max_iterations: int = 15) -> str:
    """Generic agentic loop with tool use."""
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_parts)

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = tool_executor(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})

        else:
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_parts) if text_parts else "Error: proceso incompleto."

    return "Error: se excedió el límite de iteraciones."


def _fact_check(article_text: str, platform: str, style_text: str, refs_text: str) -> str:
    """Phase 1: Verify all claims in the article via web search."""
    system_prompt = f"""Eres el EDITOR JEFE de un medio de comunicación profesional. Tu trabajo es garantizar
la RIGUROSIDAD ABSOLUTA de cada artículo antes de publicación.

PLATAFORMA DE DESTINO: {platform}

REGLAS DE ESTILO EDITORIAL:
{style_text}

MEDIOS DE REFERENCIA (el tono y estilo debe ser similar):
{refs_text}

TU PROCESO DE REVISIÓN (sigue estos pasos en orden):

PASO 1 — EXTRACCIÓN DE CLAIMS:
Identifica TODAS las afirmaciones verificables del artículo:
- Estadísticas y cifras (porcentajes, cantidades, valoraciones)
- Nombres de empresas, productos, personas
- Hechos presentados como ciertos (fechas, eventos, tendencias)
- Fuentes citadas directa o indirectamente
- Predicciones o tendencias atribuidas a estudios/informes

PASO 2 — VERIFICACIÓN:
Para CADA claim identificado, usa la herramienta verify_claim para buscarlo en internet.
Sé exhaustivo: verifica TODOS los datos, no solo algunos.
Si un dato no se puede verificar, márcalo como NO VERIFICABLE.

PASO 3 — REVISIÓN DE ESTILO:
Compara el artículo con las reglas de estilo y los medios de referencia.

PASO 4 — VEREDICTO FINAL:
Emite tu veredicto con este formato exacto:

---
## VEREDICTO: [APROBADO ✅ / REQUIERE CAMBIOS ⚠️ / RECHAZADO ❌]

### Datos verificados correctamente
- [dato]: ✅ [fuente que lo confirma]

### Datos incorrectos o no verificables
- [dato]: ❌ [lo que realmente dice la evidencia / "no se encontró fuente"]

### Datos que necesitan fuente
- [dato]: ⚠️ [sugerencia de fuente o reformulación]

### Revisión de estilo
- [observaciones sobre tono, estructura, extensión]

### Recomendaciones concretas
1. [cambio específico con texto sugerido]
2. [...]
---

REGLAS INQUEBRANTABLES:
• NUNCA apruebes un artículo con datos no verificados
• Si hay UNA SOLA cifra inventada o incorrecta → REQUIERE CAMBIOS
• Si más del 30% de los datos son inventados → RECHAZADO
• Toda estadística necesita una fuente real verificable
• "Según estudios" sin citar cuál estudio = NO ACEPTABLE
• Mejor un artículo sin cifra que un artículo con cifra inventada"""

    return _run_agentic_loop(
        system_prompt=system_prompt,
        user_message=f"Revisa este artículo:\n\n{article_text}",
        tools=EDITOR_TOOLS,
        tool_executor=_execute_editor_tool,
    )


def _rewrite_article(original_text: str, review: str, platform: str,
                     style_text: str, refs_text: str) -> str:
    """Phase 2: Rewrite the article with verified data, real sources, and correct style."""

    rewrite_tools = [
        {
            "name": "search_data",
            "description": (
                "Busca en internet datos reales, estadísticas verificadas y fuentes "
                "fiables para incluir en el artículo reescrito. Úsalo para encontrar "
                "los datos correctos que reemplacen los incorrectos del borrador."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Búsqueda para encontrar datos reales y fuentes verificadas",
                    },
                },
                "required": ["query"],
            },
        },
    ]

    def _execute_rewrite_tool(name: str, tool_input: dict) -> str:
        if name == "search_data":
            return web_search(tool_input["query"])
        return f"Herramienta desconocida: {name}"

    system_prompt = f"""Eres un REDACTOR EDITORIAL de primer nivel. Tu tarea es REESCRIBIR un artículo
que no ha pasado el control de calidad del editor jefe.

PLATAFORMA DE DESTINO: {platform}

REGLAS DE ESTILO EDITORIAL:
{style_text}

MEDIOS DE REFERENCIA (imita su tono y estilo):
{refs_text}

INSTRUCCIONES DE REESCRITURA:

1. DATOS Y FUENTES:
   - Usa la herramienta search_data para buscar datos REALES que reemplacen los incorrectos
   - CADA estadística, cifra y dato debe tener una fuente real verificable
   - Cita las fuentes explícitamente: "según [informe/estudio] de [organización] ([año])"
   - Si no encuentras un dato fiable para una afirmación, ELIMINA esa afirmación
   - NUNCA inventes datos, cifras ni fuentes

2. ESTILO:
   - Mantén el tono y la voz del autor original — es un ejecutivo con 25 años de experiencia
     en multinacionales, experto en tecnología, IA y growth
   - El estilo debe ser: analítico, con opinión fundamentada, directo, sin rodeos
   - No uses frases genéricas ni vacías. Cada párrafo debe aportar información concreta
   - Adapta al estilo de los medios de referencia indicados arriba

3. ESTRUCTURA:
   - Mantén la estructura temática del artículo original
   - Puedes reorganizar párrafos para mejorar el flujo argumentativo
   - El artículo debe tener una tesis clara y datos que la respalden

4. OUTPUT:
   - Devuelve SOLO el artículo reescrito completo, listo para publicar
   - No incluyas comentarios, explicaciones ni notas al editor
   - Al final del artículo, incluye una sección "---\\nFuentes:" con las URLs/referencias usadas"""

    return _run_agentic_loop(
        system_prompt=system_prompt,
        user_message=(
            f"ARTÍCULO ORIGINAL:\n{original_text}\n\n"
            f"REVISIÓN DEL EDITOR JEFE:\n{review}\n\n"
            "Reescribe el artículo corrigiendo TODOS los problemas detectados. "
            "Busca datos reales para cada afirmación que lo necesite."
        ),
        tools=rewrite_tools,
        tool_executor=_execute_rewrite_tool,
        max_iterations=15,
    )


def review_article(article_text: str, platform: str, sheet_row: int = None) -> str:
    """Run the editor-in-chief pipeline on an article draft.

    Phase 1: Fact-check and style review
    Phase 2: If not approved, rewrite with verified data and sources
    Writes the verdict to column H of the Sheet if sheet_row is provided.
    Returns the review + rewritten article (if applicable).
    """
    style_text, refs_text = _build_style_context(platform)

    # Phase 1: Fact-check
    review = _fact_check(article_text, platform, style_text, refs_text)

    # Determine verdict for Sheet
    if "APROBADO ✅" in review and "REQUIERE" not in review and "RECHAZADO" not in review:
        if sheet_row:
            try:
                set_editor_verdict(sheet_row, "✅ Aprobado")
            except Exception as e:
                print(f"⚠️ Error escribiendo veredicto en Sheet: {e}")
        return review

    # Phase 2: Rewrite with real data
    verdict_label = "❌ Rechazado → Reescrito" if "RECHAZADO" in review else "⚠️ Corregido → Reescrito"
    if sheet_row:
        try:
            set_editor_verdict(sheet_row, verdict_label)
        except Exception as e:
            print(f"⚠️ Error escribiendo veredicto en Sheet: {e}")

    rewritten = _rewrite_article(article_text, review, platform, style_text, refs_text)

    return (
        f"{review}\n\n"
        f"{'═' * 50}\n"
        f"📝 ARTÍCULO REESCRITO CON DATOS VERIFICADOS:\n"
        f"{'═' * 50}\n\n"
        f"{rewritten}"
    )
