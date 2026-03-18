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
from tools.sheets_tools import get_editorial_style, get_editorial_references

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


def review_article(article_text: str, platform: str) -> str:
    """Run the editor-in-chief agent on an article draft.

    Returns a structured review with verdict and detailed findings.
    """
    # Gather style rules and references for context
    style_rules = get_editorial_style(platform=platform)
    references = get_editorial_references(platform=platform)

    style_text = "\n".join(
        f"- {r['regla']}: {r['descripcion']}" for r in style_rules
    ) if style_rules else "(sin reglas de estilo definidas)"

    refs_text = "\n".join(
        f"- {r['nombre']} ({r['plataforma']}): {r.get('notas_estilo', '')}"
        for r in references
    ) if references else "(sin referencias definidas)"

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

    messages = [{"role": "user", "content": f"Revisa este artículo:\n\n{article_text}"}]

    # Agentic loop — let the editor verify claims autonomously
    max_iterations = 15  # safety limit
    for _ in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            tools=EDITOR_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            # Extract final text
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_parts)

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _execute_editor_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})

        else:
            # Unexpected stop reason
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_parts) if text_parts else "Error: revisión incompleta."

    return "Error: la revisión excedió el límite de iteraciones."
