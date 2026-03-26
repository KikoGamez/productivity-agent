/**
 * ═══════════════════════════════════════════════════════════════
 * EDITOR JEFE — Fact-checking y revisión editorial automatizada
 * ═══════════════════════════════════════════════════════════════
 *
 * Añadir estas funciones al Apps Script del motor editorial.
 * Requisito: añadir PERPLEXITY_KEY en Propiedades del script
 *   (Configuración del proyecto → Propiedades del script)
 *
 * Uso: llamar REVISAR_CON_EDITOR_JEFE(articulo, plataforma)
 *      desde APROBAR_TEMA, justo después de GENERAR_ARTICULO_CLAUDE.
 */


// ─────────────────────────────────────────────
// Perplexity web search
// ─────────────────────────────────────────────

function PERPLEXITY_SEARCH_(query) {
  var props = PropertiesService.getScriptProperties();
  var apiKey = props.getProperty("PERPLEXITY_KEY");
  if (!apiKey) return "Error: PERPLEXITY_KEY no configurada en propiedades del script.";

  try {
    var response = UrlFetchApp.fetch("https://api.perplexity.ai/chat/completions", {
      method: "post",
      contentType: "application/json",
      headers: { "Authorization": "Bearer " + apiKey },
      payload: JSON.stringify({
        model: "sonar",
        messages: [{ role: "user", content: query }]
      }),
      muteHttpExceptions: true
    });

    var data = JSON.parse(response.getContentText());
    if (data.error) return "Error Perplexity: " + (data.error.message || JSON.stringify(data.error));

    var content = data.choices[0].message.content;

    // Append citations if present
    var citations = data.citations || [];
    if (citations.length > 0) {
      content += "\n\nFuentes:\n";
      for (var i = 0; i < Math.min(citations.length, 5); i++) {
        content += "[" + (i + 1) + "] " + citations[i] + "\n";
      }
    }
    return content;
  } catch (e) {
    return "Error en búsqueda: " + e.toString();
  }
}


// ─────────────────────────────────────────────
// Claude API with tool use
// ─────────────────────────────────────────────

function CALL_CLAUDE_WITH_TOOLS_(systemPrompt, messages, tools) {
  var props = PropertiesService.getScriptProperties();
  var apiKey = props.getProperty("ANTHROPIC_KEY");

  var payload = {
    model: "claude-sonnet-4-6",
    max_tokens: 4096,
    system: systemPrompt,
    messages: messages
  };
  if (tools && tools.length > 0) {
    payload.tools = tools;
  }

  var response = UrlFetchApp.fetch("https://api.anthropic.com/v1/messages", {
    method: "post",
    contentType: "application/json",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01"
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  return JSON.parse(response.getContentText());
}


// ─────────────────────────────────────────────
// Load editorial context from Sheet tabs
// ─────────────────────────────────────────────

function LOAD_STYLE_RULES_(plataforma) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName("Estilo");
  if (!ws) return "(sin reglas de estilo definidas)";

  var data = ws.getDataRange().getValues();
  var rules = [];
  for (var i = 1; i < data.length; i++) {
    var regla = (data[i][0] || "").toString().trim();
    var desc = (data[i][1] || "").toString().trim();
    if (!desc) continue;
    var reglaLower = regla.toLowerCase();
    if (plataforma && reglaLower !== "todo" && reglaLower !== "ambos" &&
        regla !== "" && reglaLower !== plataforma.toLowerCase()) continue;
    rules.push("- " + regla + ": " + desc);
  }
  return rules.length > 0 ? rules.join("\n") : "(sin reglas de estilo definidas)";
}

function LOAD_REFERENCES_(plataforma) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName("Referencias");
  if (!ws) return "(sin referencias definidas)";

  var data = ws.getDataRange().getValues();
  var refs = [];
  for (var i = 1; i < data.length; i++) {
    var plat = (data[i][0] || "").toString().trim();
    var nombre = (data[i][1] || "").toString().trim();
    var notas = (data[i][3] || "").toString().trim();
    if (!nombre) continue;
    var platLower = plat.toLowerCase();
    if (plataforma && platLower !== "ambos" && platLower !== "todo" &&
        plat !== "" && platLower !== plataforma.toLowerCase()) continue;
    refs.push("- " + nombre + " (" + plat + "): " + notas);
  }
  return refs.length > 0 ? refs.join("\n") : "(sin referencias definidas)";
}


// ─────────────────────────────────────────────
// Agentic loop — runs Claude with tools
// ─────────────────────────────────────────────

function EXTRACT_TEXT_(content) {
  var texts = [];
  if (!content) return "";
  for (var i = 0; i < content.length; i++) {
    if (content[i].type === "text") {
      texts.push(content[i].text);
    }
  }
  return texts.join("\n");
}

function RUN_AGENTIC_LOOP_(systemPrompt, userMessage, tools, maxIterations) {
  maxIterations = maxIterations || 15;
  var messages = [{ role: "user", content: userMessage }];

  for (var iter = 0; iter < maxIterations; iter++) {
    var response = CALL_CLAUDE_WITH_TOOLS_(systemPrompt, messages, tools);

    // Handle API errors
    if (response.error) {
      Logger.log("Error Claude API: " + JSON.stringify(response.error));
      return "Error API: " + (response.error.message || JSON.stringify(response.error));
    }

    if (response.stop_reason === "end_turn") {
      return EXTRACT_TEXT_(response.content);
    }

    if (response.stop_reason === "tool_use") {
      messages.push({ role: "assistant", content: response.content });
      var toolResults = [];

      for (var j = 0; j < response.content.length; j++) {
        var block = response.content[j];
        if (block.type === "tool_use") {
          // Execute the tool (Perplexity search)
          var searchQuery = block.input.search_query || block.input.query || "";
          var claim = block.input.claim || "";
          var searchResult = PERPLEXITY_SEARCH_(searchQuery);

          var resultText = claim
            ? ("Claim: " + claim + "\nResultado búsqueda:\n" + searchResult)
            : searchResult;

          toolResults.push({
            type: "tool_result",
            tool_use_id: block.id,
            content: resultText
          });
        }
      }
      messages.push({ role: "user", content: toolResults });
    } else {
      // Unexpected stop reason — return whatever text we have
      return EXTRACT_TEXT_(response.content || []);
    }
  }
  return "Error: se excedió el límite de iteraciones del editor.";
}


// ─────────────────────────────────────────────
// EDITOR JEFE — Main entry point
// ─────────────────────────────────────────────

/**
 * Reviews an article with fact-checking and style review.
 * If the article fails review, it gets rewritten with verified data.
 *
 * @param {string} articulo - The full article text
 * @param {string} plataforma - "Economía Digital" or "LinkedIn"
 * @returns {Object} {verdict, review, article}
 *   - verdict: "APROBADO" | "CORREGIDO → Reescrito" | "RECHAZADO → Reescrito"
 *   - review: Full editor review text
 *   - article: The final article (original if approved, rewritten if not)
 */
function REVISAR_CON_EDITOR_JEFE(articulo, plataforma) {
  var styleText = LOAD_STYLE_RULES_(plataforma);
  var refsText = LOAD_REFERENCES_(plataforma);

  // ─── Phase 1: Fact-check and style review ───

  var reviewTools = [{
    name: "verify_claim",
    description:
      "Busca en internet para verificar si una afirmación, dato o fuente " +
      "del artículo es real y precisa. Úsalo para CADA dato, estadística, " +
      "nombre de empresa, cifra o fuente mencionada en el artículo.",
    input_schema: {
      type: "object",
      properties: {
        claim: {
          type: "string",
          description: "La afirmación o dato exacto a verificar"
        },
        search_query: {
          type: "string",
          description: "Búsqueda web optimizada para verificar el dato"
        }
      },
      required: ["claim", "search_query"]
    }
  }];

  var factCheckPrompt =
    "Eres el EDITOR JEFE de un medio de comunicación profesional. Tu trabajo es garantizar " +
    "la RIGUROSIDAD ABSOLUTA de cada artículo antes de publicación.\n\n" +
    "PLATAFORMA DE DESTINO: " + plataforma + "\n\n" +
    "REGLAS DE ESTILO EDITORIAL:\n" + styleText + "\n\n" +
    "MEDIOS DE REFERENCIA (el tono y estilo debe ser similar):\n" + refsText + "\n\n" +
    "TU PROCESO DE REVISIÓN (sigue estos pasos en orden):\n\n" +
    "PASO 1 — EXTRACCIÓN DE CLAIMS:\n" +
    "Identifica TODAS las afirmaciones verificables del artículo:\n" +
    "- Estadísticas y cifras (porcentajes, cantidades, valoraciones)\n" +
    "- Nombres de empresas, productos, personas\n" +
    "- Hechos presentados como ciertos (fechas, eventos, tendencias)\n" +
    "- Fuentes citadas directa o indirectamente\n" +
    "- Predicciones o tendencias atribuidas a estudios/informes\n\n" +
    "PASO 2 — VERIFICACIÓN:\n" +
    "Para CADA claim identificado, usa la herramienta verify_claim para buscarlo en internet.\n" +
    "Sé exhaustivo: verifica TODOS los datos, no solo algunos.\n" +
    "Si un dato no se puede verificar, márcalo como NO VERIFICABLE.\n\n" +
    "PASO 3 — REVISIÓN DE ESTILO:\n" +
    "Compara el artículo con las reglas de estilo y los medios de referencia.\n\n" +
    "PASO 4 — VEREDICTO FINAL:\n" +
    "Emite tu veredicto con este formato exacto:\n\n" +
    "---\n" +
    "## VEREDICTO: [APROBADO ✅ / REQUIERE CAMBIOS ⚠️ / RECHAZADO ❌]\n\n" +
    "### Datos verificados correctamente\n" +
    "- [dato]: ✅ [fuente que lo confirma]\n\n" +
    "### Datos incorrectos o no verificables\n" +
    "- [dato]: ❌ [lo que realmente dice la evidencia / \"no se encontró fuente\"]\n\n" +
    "### Revisión de estilo\n" +
    "- [observaciones sobre tono, estructura, extensión]\n\n" +
    "### Recomendaciones concretas\n" +
    "1. [cambio específico]\n" +
    "---\n\n" +
    "REGLAS INQUEBRANTABLES:\n" +
    "• NUNCA apruebes un artículo con datos no verificados\n" +
    "• Si hay UNA SOLA cifra inventada o incorrecta → REQUIERE CAMBIOS\n" +
    "• Si más del 30% de los datos son inventados → RECHAZADO\n" +
    "• Toda estadística necesita una fuente real verificable\n" +
    "• \"Según estudios\" sin citar cuál estudio = NO ACEPTABLE\n" +
    "• Mejor un artículo sin cifra que un artículo con cifra inventada";

  Logger.log("🔍 Editor Jefe: iniciando fact-checking...");
  var review = RUN_AGENTIC_LOOP_(factCheckPrompt, "Revisa este artículo:\n\n" + articulo, reviewTools);
  Logger.log("🔍 Editor Jefe: fact-checking completado.");

  // Check if approved
  if (review.indexOf("APROBADO ✅") > -1 &&
      review.indexOf("REQUIERE") === -1 &&
      review.indexOf("RECHAZADO") === -1) {
    Logger.log("✅ Editor Jefe: artículo APROBADO");
    return { verdict: "✅ Aprobado", review: review, article: articulo };
  }

  // ─── Phase 2: Rewrite with verified data ───

  Logger.log("📝 Editor Jefe: reescribiendo con datos verificados...");

  var rewriteTools = [{
    name: "search_data",
    description:
      "Busca en internet datos reales, estadísticas verificadas y fuentes " +
      "fiables para incluir en el artículo reescrito. Úsalo para encontrar " +
      "los datos correctos que reemplacen los incorrectos del borrador.",
    input_schema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Búsqueda para encontrar datos reales y fuentes verificadas"
        }
      },
      required: ["query"]
    }
  }];

  var rewritePrompt =
    "Eres un REDACTOR EDITORIAL de primer nivel. Tu tarea es REESCRIBIR un artículo " +
    "que no ha pasado el control de calidad del editor jefe.\n\n" +
    "PLATAFORMA DE DESTINO: " + plataforma + "\n\n" +
    "REGLAS DE ESTILO EDITORIAL:\n" + styleText + "\n\n" +
    "MEDIOS DE REFERENCIA (imita su tono y estilo):\n" + refsText + "\n\n" +
    "INSTRUCCIONES DE REESCRITURA:\n\n" +
    "1. DATOS Y FUENTES:\n" +
    "   - Usa la herramienta search_data para buscar datos REALES que reemplacen los incorrectos\n" +
    "   - CADA estadística, cifra y dato debe tener una fuente real verificable\n" +
    "   - Cita las fuentes explícitamente: \"según [informe/estudio] de [organización] ([año])\"\n" +
    "   - Si no encuentras un dato fiable para una afirmación, ELIMINA esa afirmación\n" +
    "   - NUNCA inventes datos, cifras ni fuentes\n\n" +
    "2. ESTILO:\n" +
    "   - Mantén el tono y la voz del autor original — es un ejecutivo con 25 años de experiencia\n" +
    "     en multinacionales, experto en tecnología, IA y growth\n" +
    "   - El estilo debe ser: analítico, con opinión fundamentada, directo, sin rodeos\n" +
    "   - No uses frases genéricas ni vacías. Cada párrafo debe aportar información concreta\n" +
    "   - Adapta al estilo de los medios de referencia indicados arriba\n\n" +
    "3. ESTRUCTURA:\n" +
    "   - Mantén la estructura temática del artículo original\n" +
    "   - Puedes reorganizar párrafos para mejorar el flujo argumentativo\n" +
    "   - El artículo debe tener una tesis clara y datos que la respalden\n\n" +
    "4. OUTPUT:\n" +
    "   - Devuelve SOLO el artículo reescrito completo, listo para publicar\n" +
    "   - No incluyas comentarios, explicaciones ni notas al editor\n" +
    "   - Al final del artículo, incluye una sección \"---\\nFuentes:\" con las URLs/referencias usadas";

  var rewritten = RUN_AGENTIC_LOOP_(
    rewritePrompt,
    "ARTÍCULO ORIGINAL:\n" + articulo + "\n\n" +
    "REVISIÓN DEL EDITOR JEFE:\n" + review + "\n\n" +
    "Reescribe el artículo corrigiendo TODOS los problemas detectados. " +
    "Busca datos reales para cada afirmación que lo necesite.",
    rewriteTools,
    15
  );

  var verdictLabel = review.indexOf("RECHAZADO") > -1
    ? "❌ Rechazado → Reescrito"
    : "⚠️ Corregido → Reescrito";

  Logger.log("📝 Editor Jefe: reescritura completada. Veredicto: " + verdictLabel);
  return { verdict: verdictLabel, review: review, article: rewritten };
}


// ─────────────────────────────────────────────
// Archivar fila completada al final del Sheet
// ─────────────────────────────────────────────

/**
 * Moves a completed article row to the bottom of the sheet
 * and clears the original row for reuse.
 *
 * @param {Sheet} ws - The Editorial sheet
 * @param {number} row - Row number to archive
 */
function ARCHIVAR_FILA_AL_FINAL_(ws, row) {
  var numCols = ws.getLastColumn();
  var lastRow = ws.getLastRow();

  // Copy all data from the row
  var rowData = ws.getRange(row, 1, 1, numCols).getValues();
  // Append at the bottom with a "Generado" status
  ws.getRange(lastRow + 1, 1, 1, numCols).setValues(rowData);
  ws.getRange(lastRow + 1, 5).setValue("Generado");  // Column E = Estado

  // Clear the original row (content only, keep formatting/checkboxes)
  ws.getRange(row, 1, 1, numCols).clearContent();

  // Re-insert checkboxes in F, G, H if they were checkboxes
  try {
    ws.getRange(row, 6).insertCheckboxes();  // F = Rechazar
    ws.getRange(row, 7).insertCheckboxes();  // G = Aprobar
    ws.getRange(row, 8).insertCheckboxes();  // H = Modificar
  } catch (e) {
    // Columns might not use checkboxes — ignore
  }

  Logger.log("📦 Fila " + row + " archivada al final (fila " + (lastRow + 1) + ")");
}


// ─────────────────────────────────────────────
// Generar nueva idea editorial
// ─────────────────────────────────────────────

/**
 * Generates a fresh content idea for a platform using Claude + Perplexity
 * and writes it in the specified row.
 *
 * @param {Sheet} ws - The Editorial sheet
 * @param {number} row - Row number where to write the new idea
 * @param {string} plataforma - "Economía Digital" or "LinkedIn"
 */
function GENERAR_NUEVA_IDEA_(ws, row, plataforma) {
  // Collect existing ideas to avoid duplicates
  var data = ws.getDataRange().getValues();
  var existingIdeas = [];
  var maxId = 0;
  for (var i = 1; i < data.length; i++) {
    var id = parseInt(data[i][0]) || 0;
    if (id > maxId) maxId = id;
    var titulo = (data[i][2] || "").toString().trim();
    if (titulo) existingIdeas.push(titulo);
  }
  var newId = maxId + 1;

  // Load editorial context
  var styleText = LOAD_STYLE_RULES_(plataforma);
  var refsText = LOAD_REFERENCES_(plataforma);

  // Search current trends via Perplexity
  var trendQuery = plataforma.toLowerCase().indexOf("linkedin") > -1
    ? "trending topics thought leadership LinkedIn technology AI business 2026"
    : "tendencias economía digital tecnología IA empresas España 2026 últimas noticias";

  var trends = PERPLEXITY_SEARCH_(trendQuery);

  // Generate idea with Claude
  var props = PropertiesService.getScriptProperties();
  var apiKey = props.getProperty("ANTHROPIC_KEY");

  var systemPrompt =
    "Eres el director editorial de un medio especializado. " +
    "Generas ideas de artículos para un ejecutivo con 25 años de experiencia " +
    "en multinacionales, experto en tecnología, IA, robótica y growth.\n\n" +
    "PLATAFORMA: " + plataforma + "\n\n" +
    "REGLAS DE ESTILO:\n" + styleText + "\n\n" +
    "MEDIOS DE REFERENCIA:\n" + refsText;

  var userMessage =
    "TENDENCIAS ACTUALES:\n" + trends + "\n\n" +
    "IDEAS YA EXISTENTES (NO repetir ni temas muy similares):\n" +
    existingIdeas.join("\n") + "\n\n" +
    "Genera UNA idea de artículo nueva, actual y relevante para " + plataforma + ".\n\n" +
    "FORMATO DE RESPUESTA (solo esto, nada más):\n" +
    "TÍTULO: [título del artículo]\n" +
    "ESQUEMA: [esquema breve en 3-5 puntos de lo que cubriría el artículo]";

  var response = UrlFetchApp.fetch("https://api.anthropic.com/v1/messages", {
    method: "post",
    contentType: "application/json",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01"
    },
    payload: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 1024,
      system: systemPrompt,
      messages: [{ role: "user", content: userMessage }]
    }),
    muteHttpExceptions: true
  });

  var result = JSON.parse(response.getContentText());
  var text = result.content[0].text;

  // Parse title and schema
  var titulo = "";
  var esquema = "";
  var lines = text.split("\n");
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (line.toUpperCase().indexOf("TÍTULO:") === 0 || line.toUpperCase().indexOf("TITULO:") === 0) {
      titulo = line.substring(line.indexOf(":") + 1).trim();
    } else if (line.toUpperCase().indexOf("ESQUEMA:") === 0) {
      // Esquema might span multiple lines
      esquema = line.substring(line.indexOf(":") + 1).trim();
      for (var j = i + 1; j < lines.length; j++) {
        var nextLine = lines[j].trim();
        if (nextLine && nextLine.toUpperCase().indexOf("TÍTULO") === -1) {
          esquema += "\n" + nextLine;
        }
      }
      break;
    }
  }

  // If parsing failed, use full text as title
  if (!titulo) titulo = text.substring(0, 200);

  // Write new idea to the row
  ws.getRange(row, 1).setValue(newId);            // A = ID
  ws.getRange(row, 2).setValue(plataforma);        // B = Plataforma
  ws.getRange(row, 3).setValue(titulo);             // C = Título/Temática
  ws.getRange(row, 4).setValue(esquema);             // D = Esquema
  ws.getRange(row, 5).setValue("Pendiente");       // E = Estado
  // F, G, H already cleared/reset by ARCHIVAR_FILA_AL_FINAL_
  ws.getRange(row, 9).clearContent();              // I = Editor Jefe (vacío)

  Logger.log("💡 Nueva idea generada para " + plataforma + ": " + titulo);
  return titulo;
}


// ═══════════════════════════════════════════════════════════════
// INTEGRACIÓN EN APROBAR_TEMA
// ═══════════════════════════════════════════════════════════════
//
// En tu función APROBAR_TEMA, después de generar el artículo y
// subirlo al destino (Google Doc / Notion), añade estas líneas
// AL FINAL de la función:
//
//   // ─── Editor Jefe: fact-check y corrección automática ───
//   var editorResult = REVISAR_CON_EDITOR_JEFE(articulo, plataforma);
//   articulo = editorResult.article;  // Usa el artículo revisado/reescrito
//
//   // Escribir veredicto del editor en columna I
//   var timestamp = Utilities.formatDate(new Date(), "Europe/Madrid", "dd/MM/yyyy HH:mm");
//   ws.getRange(row, 9).setValue(editorResult.verdict + " — " + timestamp);
//
//   // ─── Archivar y generar nueva idea ───
//   var plataforma = ws.getRange(row, 2).getValue();
//   ARCHIVAR_FILA_AL_FINAL_(ws, row);
//   GENERAR_NUEVA_IDEA_(ws, row, plataforma);
//
// IMPORTANTE: La línea de ARCHIVAR va DESPUÉS de subir al destino,
// porque al archivar se limpia la fila original.
// ═══════════════════════════════════════════════════════════════
