import os
import json
import time
import anthropic
from datetime import datetime

from config import BRANCHES, BRANCH_HOURS
from tools.notion_tools import (
    create_task,
    get_tasks,
    update_task_status,
    save_meeting_notes,
    get_weekly_hours_by_branch,
    log_time,
)
from tools.calendar_tools import get_calendar_events, block_calendar_time, delete_calendar_event
from tools.gmail_tools import read_emails, get_email_body
from tools.memory_tools import get_memory, update_memory
from tools.contacts_tools import add_contact, get_contacts, update_contact
from tools.documents_tools import save_document, search_documents, get_document_content
from tools.sheets_tools import get_editorial_articles, mark_article, get_editorial_style, get_editorial_references
from tools.editor_agent import review_article
from tools.search_tools import web_search

client = anthropic.Anthropic()

# ─────────────────────────────────────────────
# Memory cache (avoid hitting Notion on every message)
# ─────────────────────────────────────────────

_memory_cache: str = ""
_memory_cache_ts: float = 0.0
_MEMORY_TTL = 300  # refresh every 5 minutes


def get_memory_cached() -> str:
    global _memory_cache, _memory_cache_ts
    if time.time() - _memory_cache_ts > _MEMORY_TTL:
        _memory_cache = get_memory()
        _memory_cache_ts = time.time()
    return _memory_cache


def invalidate_memory_cache():
    global _memory_cache_ts
    _memory_cache_ts = 0.0


# ─────────────────────────────────────────────
# Tool schemas (JSON Schema for Claude)
# ─────────────────────────────────────────────

BRANCH_ENUM = [b.name for b in BRANCHES]

TOOLS = [
    {
        "name": "create_task",
        "description": (
            "Crea una tarea en Notion. Úsalo cuando el usuario quiera añadir "
            "una tarea, to-do o acción a realizar. También úsalo automáticamente "
            "cuando detectes acciones en notas de reuniones o en correos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título descriptivo de la tarea"},
                "branch": {
                    "type": "string",
                    "enum": BRANCH_ENUM,
                    "description": "Rama de trabajo a la que pertenece la tarea",
                },
                "priority": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                    "description": "Prioridad de la tarea",
                },
                "estimated_hours": {
                    "type": "number",
                    "description": "Horas estimadas para completar la tarea",
                },
                "due_date": {
                    "type": "string",
                    "description": "Fecha límite en formato YYYY-MM-DD (opcional)",
                },
                "notes": {
                    "type": "string",
                    "description": "Notas adicionales (opcional)",
                },
            },
            "required": ["title", "branch", "priority", "estimated_hours"],
        },
    },
    {
        "name": "get_tasks",
        "description": "Obtiene las tareas de Notion, opcionalmente filtradas por rama y/o estado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "enum": BRANCH_ENUM,
                    "description": "Filtrar por rama (opcional, omitir para todas)",
                },
                "status": {
                    "type": "string",
                    "enum": ["Pending", "In Progress", "Done"],
                    "description": "Filtrar por estado (opcional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "update_task_status",
        "description": (
            "Actualiza el estado de una tarea en Notion. Úsalo cuando el usuario "
            "diga que ha completado, empezado o quiera cambiar el estado de una tarea."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID de la tarea en Notion (obtenido de get_tasks)",
                },
                "status": {
                    "type": "string",
                    "enum": ["Pending", "In Progress", "Done"],
                    "description": "Nuevo estado de la tarea",
                },
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "save_meeting_notes",
        "description": (
            "Guarda las notas de una reunión en Notion. Después de guardarlas, "
            "extrae automáticamente las acciones y crea las tareas correspondientes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título o nombre de la reunión"},
                "attendees": {
                    "type": "string",
                    "description": "Participantes separados por comas",
                },
                "notes": {"type": "string", "description": "Contenido completo de las notas"},
                "action_items": {
                    "type": "string",
                    "description": "Acciones concretas derivadas de la reunión (opcional)",
                },
            },
            "required": ["title", "attendees", "notes"],
        },
    },
    {
        "name": "get_calendar_events",
        "description": "Obtiene los eventos de Google Calendar para una fecha específica.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Fecha en formato YYYY-MM-DD. Omitir para usar hoy.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "block_calendar_time",
        "description": "Crea un bloque de trabajo enfocado en Google Calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Descripción del bloque"},
                "start_time": {
                    "type": "string",
                    "description": "Inicio en ISO 8601, ej: 2024-01-15T09:00:00",
                },
                "end_time": {
                    "type": "string",
                    "description": "Fin en ISO 8601, ej: 2024-01-15T11:00:00",
                },
                "branch": {
                    "type": "string",
                    "enum": BRANCH_ENUM,
                    "description": "Rama de trabajo para este bloque",
                },
                "notes": {"type": "string", "description": "Notas adicionales (opcional)"},
            },
            "required": ["title", "start_time", "end_time", "branch"],
        },
    },
    {
        "name": "read_emails",
        "description": "Lee correos de Gmail. Por defecto devuelve los no leídos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_emails": {
                    "type": "integer",
                    "description": "Número máximo de correos (por defecto 10)",
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "Solo correos no leídos (por defecto true)",
                },
                "yesterday_only": {
                    "type": "boolean",
                    "description": "Solo correos recibidos ayer (por defecto false)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_email_body",
        "description": "Obtiene el cuerpo completo de un correo por su ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {
                    "type": "string",
                    "description": "ID del correo obtenido con read_emails",
                }
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "log_time",
        "description": "Registra horas trabajadas en una rama para el seguimiento semanal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "enum": BRANCH_ENUM,
                    "description": "Rama de trabajo",
                },
                "hours": {"type": "number", "description": "Horas trabajadas"},
                "task_description": {
                    "type": "string",
                    "description": "Descripción de lo realizado (opcional)",
                },
            },
            "required": ["branch", "hours"],
        },
    },
    {
        "name": "save_document",
        "description": (
            "Guarda un documento, resumen o nota larga en la base de datos de Notion. "
            "Úsalo para guardar información extensa sobre proyectos, investigaciones, "
            "transcripciones de audio, resúmenes de emails o cualquier contexto importante."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título del documento"},
                "content": {"type": "string", "description": "Contenido completo del documento"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Etiquetas para categorizar (ej: ['inversores', 'AION', 'strategy'])",
                },
                "source": {
                    "type": "string",
                    "enum": ["Manual", "Email", "Reunión", "Audio", "Investigación"],
                    "description": "Origen del documento",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "search_documents",
        "description": "Busca documentos guardados por título o etiquetas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto a buscar en el título"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filtrar por etiquetas",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_document_content",
        "description": "Obtiene el contenido completo de un documento por su ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "ID del documento obtenido con search_documents"},
            },
            "required": ["doc_id"],
        },
    },
    {
        "name": "get_editorial_articles",
        "description": "Lee las propuestas de contenido del Google Sheet Editorial. Devuelve los artículos pendientes de revisión (o todos si only_pending=false).",
        "input_schema": {
            "type": "object",
            "properties": {
                "only_pending": {
                    "type": "boolean",
                    "description": "Si true (por defecto), devuelve solo los pendientes de revisar.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_editorial_style",
        "description": "Lee las reglas de estilo editorial del Google Sheet (pestaña Estilo). Úsalo antes de generar o revisar cualquier artículo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "Filtra por plataforma: 'Economía Digital' o 'LinkedIn'. Si no se indica, devuelve todas.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_editorial_references",
        "description": "Lee los medios de referencia del Google Sheet (pestaña Referencias) para inspirarse al generar artículos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "Filtra por plataforma: 'Economía Digital' o 'LinkedIn'. Si no se indica, devuelve todos.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "mark_article",
        "description": "Marca una fila del Sheet Editorial como aprobada, rechazada o aprobada con modificaciones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "row": {
                    "type": "integer",
                    "description": "Número de fila en el Sheet (obtenido de get_editorial_articles).",
                },
                "action": {
                    "type": "string",
                    "enum": ["aprobar", "rechazar", "modificar"],
                    "description": "'aprobar' = columna G verde, 'rechazar' = columna F rojo, 'modificar' = columna G lápiz.",
                },
            },
            "required": ["row", "action"],
        },
    },
    {
        "name": "review_article",
        "description": (
            "OBLIGATORIO antes de aprobar cualquier artículo editorial. "
            "Lanza al editor jefe: un agente autónomo que verifica la rigurosidad "
            "del artículo buscando en internet CADA dato, cifra, fuente y afirmación. "
            "También revisa que el estilo sea coherente con las guías editoriales. "
            "Devuelve un veredicto detallado (APROBADO / REQUIERE CAMBIOS / RECHAZADO). "
            "NUNCA apruebes un artículo sin pasar esta revisión primero."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "article_text": {
                    "type": "string",
                    "description": "Texto completo del artículo a revisar",
                },
                "platform": {
                    "type": "string",
                    "enum": ["Economía Digital", "LinkedIn"],
                    "description": "Plataforma destino del artículo",
                },
            },
            "required": ["article_text", "platform"],
        },
    },
    {
        "name": "add_contact",
        "description": "Añade un contacto de LinkedIn al registro de networking en Notion.",
        "input_schema": {
            "type": "object",
            "properties": {
                "persona": {"type": "string", "description": "Nombre completo del contacto"},
                "empresa": {"type": "string", "description": "Empresa donde trabaja"},
                "tipo_contacto": {
                    "type": "string",
                    "enum": ["Conexión", "Mensaje", "Comentario", "Reunión", "Café virtual", "Seguimiento"],
                    "description": "Tipo de contacto realizado",
                },
                "ultimo_contacto": {"type": "string", "description": "Fecha del último contacto YYYY-MM-DD (por defecto hoy)"},
                "proximo_contacto": {"type": "string", "description": "Qué hacer en el próximo contacto"},
                "fecha_proximo_contacto": {"type": "string", "description": "Cuándo hacer el próximo contacto YYYY-MM-DD"},
            },
            "required": ["persona"],
        },
    },
    {
        "name": "get_contacts",
        "description": (
            "Obtiene contactos de LinkedIn del registro de networking. "
            "Úsalo para ver quién necesita seguimiento o listar contactos activos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "estado": {
                    "type": "string",
                    "enum": ["Activo", "Frío", "Convertido"],
                    "description": "Filtrar por estado (opcional)",
                },
                "dias_sin_contacto": {
                    "type": "integer",
                    "description": "Devuelve contactos sin actividad en los últimos N días (opcional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "update_contact",
        "description": (
            "Actualiza un contacto de LinkedIn: registra un nuevo contacto, "
            "cambia el próximo seguimiento o su estado. "
            "Usa get_contacts primero para obtener el ID del contacto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "ID del contacto obtenido con get_contacts"},
                "tipo_contacto": {
                    "type": "string",
                    "enum": ["Conexión", "Mensaje", "Comentario", "Reunión", "Café virtual", "Seguimiento"],
                    "description": "Tipo del contacto realizado",
                },
                "ultimo_contacto": {"type": "string", "description": "Fecha del contacto YYYY-MM-DD (por defecto hoy)"},
                "proximo_contacto": {"type": "string", "description": "Qué hacer en el próximo contacto"},
                "fecha_proximo_contacto": {"type": "string", "description": "Cuándo hacer el próximo contacto YYYY-MM-DD"},
                "estado": {
                    "type": "string",
                    "enum": ["Activo", "Frío", "Convertido"],
                    "description": "Nuevo estado del contacto",
                },
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "delete_calendar_event",
        "description": (
            "Elimina un evento de Google Calendar por su ID. "
            "⚠️ REGLA OBLIGATORIA: NUNCA ejecutes esta herramienta sin haber pedido "
            "confirmación EXPLÍCITA al usuario DOS VECES. Primero di qué evento vas a borrar "
            "y pide confirmación. Si el usuario confirma, vuelve a preguntar '¿Seguro? Esta "
            "acción no se puede deshacer.' Solo ejecuta tras el segundo OK. "
            "Usa primero get_calendar_events para obtener el ID del evento a borrar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "ID del evento obtenido con get_calendar_events",
                }
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "get_memory",
        "description": (
            "Lee la memoria de largo plazo del agente: contexto sobre el usuario, "
            "proyectos activos, contactos clave y compromisos importantes. "
            "Úsalo cuando necesites recordar información de conversaciones anteriores."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_memory",
        "description": (
            "Actualiza la memoria de largo plazo con información relevante nueva. "
            "Úsalo cuando el usuario comparta información importante sobre proyectos, "
            "contactos, compromisos o contexto personal que deba recordarse. "
            "Escribe el contenido COMPLETO de la memoria, no solo lo nuevo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "Contenido completo de la memoria en formato markdown. "
                        "Usa secciones con ## para organizar: "
                        "## Contexto Personal, ## Proyectos Activos, "
                        "## Contactos Clave, ## Compromisos Pendientes, ## Notas"
                    ),
                }
            },
            "required": ["content"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Busca información actualizada en internet usando Perplexity. "
            "Úsalo para noticias del día, precios, eventos recientes, investigación "
            "o cualquier consulta que requiera datos en tiempo real. "
            "Escribe la query en español o inglés según convenga."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Pregunta o búsqueda a realizar",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "generate_agenda_data",
        "description": (
            "Recopila todos los datos para generar la agenda del día: tareas pendientes, "
            "eventos del calendario, horas trabajadas esta semana y déficit por rama. "
            "Úsalo SIEMPRE antes de proponer una agenda diaria."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Fecha para la agenda en YYYY-MM-DD. Omitir para hoy.",
                }
            },
            "required": [],
        },
    },
]


# ─────────────────────────────────────────────
# Tool execution
# ─────────────────────────────────────────────


def execute_tool(name: str, tool_input: dict) -> str:
    """Dispatch a tool call and return its string result."""
    try:
        if name == "create_task":
            return create_task(
                title=tool_input["title"],
                branch=tool_input["branch"],
                priority=tool_input["priority"],
                estimated_hours=tool_input["estimated_hours"],
                due_date=tool_input.get("due_date") or None,
                notes=tool_input.get("notes", ""),
            )

        elif name == "get_tasks":
            tasks = get_tasks(
                branch=tool_input.get("branch") or None,
                status=tool_input.get("status") or None,
            )
            return (
                json.dumps(tasks, ensure_ascii=False, indent=2)
                if tasks
                else "No se encontraron tareas con esos filtros."
            )

        elif name == "update_task_status":
            return update_task_status(
                task_id=tool_input["task_id"],
                status=tool_input["status"],
            )

        elif name == "save_meeting_notes":
            return save_meeting_notes(
                title=tool_input["title"],
                attendees=tool_input["attendees"],
                notes=tool_input["notes"],
                action_items=tool_input.get("action_items", ""),
            )

        elif name == "get_calendar_events":
            events = get_calendar_events(tool_input.get("date") or None)
            return (
                json.dumps(events, ensure_ascii=False, indent=2)
                if events
                else "No hay eventos en el calendario para esa fecha."
            )

        elif name == "block_calendar_time":
            return block_calendar_time(
                title=tool_input["title"],
                start_time=tool_input["start_time"],
                end_time=tool_input["end_time"],
                branch=tool_input["branch"],
                notes=tool_input.get("notes", ""),
            )

        elif name == "read_emails":
            emails = read_emails(
                max_emails=tool_input.get("max_emails", 10),
                unread_only=tool_input.get("unread_only", True),
                yesterday_only=tool_input.get("yesterday_only", False),
            )
            return (
                json.dumps(emails, ensure_ascii=False, indent=2)
                if emails
                else "No hay correos nuevos."
            )

        elif name == "get_email_body":
            return get_email_body(tool_input["email_id"])

        elif name == "save_document":
            return save_document(
                title=tool_input["title"],
                content=tool_input["content"],
                tags=tool_input.get("tags", []),
                source=tool_input.get("source", "Manual"),
            )

        elif name == "search_documents":
            docs = search_documents(
                query=tool_input.get("query", ""),
                tags=tool_input.get("tags"),
            )
            return json.dumps(docs, ensure_ascii=False, indent=2) if docs else "No se encontraron documentos."

        elif name == "get_document_content":
            return get_document_content(tool_input["doc_id"])

        elif name == "get_editorial_articles":
            articles = get_editorial_articles(
                only_pending=tool_input.get("only_pending", True),
            )
            return json.dumps(articles, ensure_ascii=False, indent=2) if articles else "No hay artículos pendientes de revisar."

        elif name == "get_editorial_style":
            rules = get_editorial_style(platform=tool_input.get("platform"))
            return json.dumps(rules, ensure_ascii=False, indent=2) if rules else "No hay reglas de estilo definidas."

        elif name == "get_editorial_references":
            refs = get_editorial_references(platform=tool_input.get("platform"))
            return json.dumps(refs, ensure_ascii=False, indent=2) if refs else "No hay referencias definidas."

        elif name == "mark_article":
            return mark_article(
                row=tool_input["row"],
                action=tool_input["action"],
            )

        elif name == "review_article":
            return review_article(
                article_text=tool_input["article_text"],
                platform=tool_input["platform"],
            )

        elif name == "add_contact":
            return add_contact(
                persona=tool_input["persona"],
                empresa=tool_input.get("empresa", ""),
                tipo_contacto=tool_input.get("tipo_contacto", "Conexión"),
                ultimo_contacto=tool_input.get("ultimo_contacto"),
                proximo_contacto=tool_input.get("proximo_contacto", ""),
                fecha_proximo_contacto=tool_input.get("fecha_proximo_contacto"),
            )

        elif name == "get_contacts":
            contacts = get_contacts(
                estado=tool_input.get("estado"),
                dias_sin_contacto=tool_input.get("dias_sin_contacto"),
            )
            return json.dumps(contacts, ensure_ascii=False, indent=2) if contacts else "No hay contactos con esos filtros."

        elif name == "update_contact":
            return update_contact(
                contact_id=tool_input["contact_id"],
                tipo_contacto=tool_input.get("tipo_contacto"),
                ultimo_contacto=tool_input.get("ultimo_contacto"),
                proximo_contacto=tool_input.get("proximo_contacto"),
                fecha_proximo_contacto=tool_input.get("fecha_proximo_contacto"),
                estado=tool_input.get("estado"),
            )

        elif name == "delete_calendar_event":
            return delete_calendar_event(tool_input["event_id"])

        elif name == "web_search":
            return web_search(tool_input["query"])

        elif name == "get_memory":
            memory = get_memory_cached()
            return memory if memory else "La memoria está vacía todavía."

        elif name == "update_memory":
            result = update_memory(tool_input["content"])
            invalidate_memory_cache()
            return result

        elif name == "log_time":
            return log_time(
                branch=tool_input["branch"],
                hours=tool_input["hours"],
                task_description=tool_input.get("task_description", ""),
            )

        elif name == "generate_agenda_data":
            date = tool_input.get("date") or datetime.now().strftime("%Y-%m-%d")
            tasks = get_tasks(status="Pending")
            calendar_events = get_calendar_events(date)
            weekly_hours = get_weekly_hours_by_branch()

            deficits = {
                branch: round(target - weekly_hours.get(branch, 0), 1)
                for branch, target in BRANCH_HOURS.items()
            }

            return json.dumps(
                {
                    "date": date,
                    "weekday": datetime.strptime(date, "%Y-%m-%d").strftime("%A"),
                    "pending_tasks": tasks,
                    "calendar_events": calendar_events,
                    "weekly_hours_logged": weekly_hours,
                    "branch_deficits": deficits,
                    "branch_targets": BRANCH_HOURS,
                },
                ensure_ascii=False,
                indent=2,
            )

        else:
            return f"Error: herramienta '{name}' no reconocida."

    except Exception as exc:
        return f"Error ejecutando {name}: {exc}"


# ─────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────


def _build_system_prompt(extra_context: str = "") -> str:
    today = datetime.now().strftime("%A, %d de %B de %Y")
    branches_text = "\n".join(
        f"  {b.emoji} {b.name}: {b.weekly_hours}h/semana" for b in BRANCHES
    )
    memory = get_memory_cached()
    memory_section = f"\nMEMORIA (contexto de conversaciones anteriores):\n{memory}\n" if memory else ""
    rag_section = f"\n{extra_context}\n" if extra_context else ""
    return f"""Eres un asistente de productividad personal autónomo. Hoy es {today}.
{memory_section}{rag_section}

RAMAS DE TRABAJO Y OBJETIVOS SEMANALES:
{branches_text}
  Total: 54h/semana

CAPACIDADES:
• Crear y consultar tareas en Notion
• Guardar notas de reuniones y crear tareas derivadas automáticamente
• Leer y analizar correos de Gmail
• Ver y bloquear bloques de trabajo en Google Calendar
• Registrar horas trabajadas por rama
• Generar la agenda del día optimizada por déficit de horas
• Buscar información en internet en tiempo real (noticias, precios, eventos, investigación) usando web_search

ACCESO A INTERNET — MUY IMPORTANTE:
Tienes acceso a internet en tiempo real mediante la herramienta web_search (Perplexity).
NUNCA digas "no tengo acceso a internet" ni "no puedo ver noticias en tiempo real".
Ante cualquier pregunta sobre noticias, precios, eventos recientes o información actual → llama SIEMPRE a web_search.

🔴 REGLA CRÍTICA — PROTECCIÓN DE AGENDA:
NUNCA borres, elimines o modifiques eventos del calendario sin DOBLE CONFIRMACIÓN explícita del usuario.
Paso 1: Indica exactamente qué evento vas a borrar (nombre, fecha, hora) y pide confirmación.
Paso 2: Tras el primer OK, vuelve a preguntar "¿Seguro? No se puede deshacer."
Solo ejecuta delete_calendar_event tras recibir el segundo OK.
Esta regla NO tiene excepciones. Aplica incluso si el usuario dice "borra todo".

COMPORTAMIENTO AUTÓNOMO:
• Encadena herramientas sin pedir permiso para cada paso intermedio
• Al recibir notas de reunión → guárdalas Y crea todas las tareas detectadas
• Al revisar emails → identifica acciones y propone crear tareas
• Al generar o revisar artículos editoriales → SIEMPRE pasa el artículo por review_article
  ANTES de marcarlo como aprobado. NUNCA apruebes un artículo sin la revisión del editor jefe.
  Si el editor jefe dice REQUIERE CAMBIOS o RECHAZADO, muestra las correcciones al usuario.
• Al generar la agenda:
  1. Llama a generate_agenda_data para obtener todos los datos
  2. Propone bloques concretos priorizando ramas con más déficit
  3. Si el usuario confirma, bloquéalos TODOS en Google Calendar
• Propón siempre entre 6 y 9 horas de trabajo diario (lunes-viernes)

MEMORIA A LARGO PLAZO — MUY IMPORTANTE:
Eres el asistente personal de este usuario. Tu memoria es tu herramienta más valiosa.
GUARDA EN MEMORIA PROACTIVAMENTE, sin que el usuario te lo pida, cualquier información estructural:
• Trabajo actual, empresa, rol, proyectos en curso
• Situaciones personales relevantes (búsqueda de trabajo, inversores, negociaciones...)
• Preferencias y hábitos (horarios, forma de trabajar, herramientas preferidas)
• Contactos clave y su relación con el usuario
• Decisiones importantes tomadas
• Contexto de proyectos (estado actual, próximos pasos, bloqueos)
• Cualquier dato que cambie cómo debes ayudarle en el futuro

CUÁNDO actualizar la memoria — HAZLO CON FRECUENCIA:
• DURANTE la conversación, no solo al final. Si el usuario te cuenta algo importante, guárdalo YA.
• Cuando mencione personas, empresas, proyectos, reuniones, decisiones
• Cuando te diga qué ha hecho, qué va a hacer, o qué ha cambiado
• Cuando exprese preferencias, frustraciones o correcciones
• Cuando tome decisiones o cambie de planes
• REGLA: si dudas entre guardar o no guardar, GUARDA.

CÓMO actualizar la memoria:
• Llama a get_memory PRIMERO para leer la memoria actual
• Llama a update_memory con el contenido COMPLETO (memoria anterior + lo nuevo integrado)
• Organiza por secciones: ## Trayectoria profesional / ## Trabajo y proyectos activos / ## Contactos clave / ## Situación personal / ## Preferencias y hábitos / ## Decisiones recientes / ## Contexto y notas
• NUNCA borres información anterior a menos que el usuario la corrija explícitamente
• Sé detallado. Incluye fechas, nombres, y contexto. La memoria es tu recurso más valioso.

FORMATO DE AGENDA:
09:00–11:00 | 🚀 AION Growth Studio | Preparar deck inversores (2h)
11:00–12:00 | 📅 Reunión: Call con cliente (ya en calendar)
12:00–13:00 | 🤖 Intervia.ai | Revisar PRs feature branch (1h)
15:00–17:00 | 💼 Buscar trabajo | Preparar entrevista Google (2h)
17:00–18:00 | 🤝 Networking | Responder LinkedIn + emails (1h)

ROL DE COACH PROFESIONAL:
Además de asistente de productividad, eres un coach ejecutivo personal.
Actúa como coach cuando el usuario pida orientación, ánimo, reflexión sobre su carrera,
o cuando detectes que necesita perspectiva estratégica sobre su situación.

Principios de coaching:
• REALISTA y PRAGMÁTICO. Sin paños calientes ni frases motivacionales vacías.
  Hablas con un ejecutivo de 25+ años de experiencia en multinacionales. Trata al usuario
  como un igual intelectual — nunca condescendiente, nunca simplista.
• Haz preguntas incisivas antes de opinar. Un buen coach pregunta más de lo que afirma.
  Pregunta lo que necesites saber para dar orientación con fundamento.
• Enfoque dual: el usuario necesita equilibrar búsqueda activa de oportunidades en el mercado
  con emprendimiento propio (Intervia AI y AION Growth Studio con su socio Rames Sarwat).
  Ayúdale a no depender emocionalmente de un solo camino.
• Marca personal: tiene un libro escrito pendiente de editorial y necesita visibilidad
  en el ecosistema de tecnología, IA y growth. Empújale a ejecutar en este frente.
• Contexto profesional: 10 años en Telefónica (salió en agosto por cambio organizativo),
  breve paso por family office de Proeduca SUMMA (sept-feb), ahora en búsqueda de proyecto.
• Su CV está en la base de datos de documentos — consúltalo con search_documents cuando
  necesites datos concretos de su trayectoria.
• Cuando detectes desánimo o dispersión, reconduce con datos: revisa sus tareas,
  horas invertidas, progreso real. Pon espejo con hechos, no con opiniones.
• Propón acciones concretas, con fechas y métricas. Nada de "deberías pensar en..."
  sino "esta semana dedica 3h a X, el entregable es Y para el viernes".

Comunícate siempre en español. Sé directo y eficiente."""


# ─────────────────────────────────────────────
# Main agent loop
# ─────────────────────────────────────────────


def run_agent():
    """Run the interactive CLI productivity agent."""
    print("\n" + "═" * 56)
    print("   🤖  AGENTE DE PRODUCTIVIDAD PERSONAL")
    print("═" * 56)
    print("   Escribe tu mensaje. 'salir' para terminar.\n")

    messages = []
    system_prompt = _build_system_prompt()

    while True:
        try:
            user_input = input("Tú → ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n¡Hasta luego!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            print("¡Hasta luego!")
            break

        messages.append({"role": "user", "content": user_input})

        # Agentic loop: keeps going while Claude calls tools
        while True:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
            )

            if response.stop_reason == "end_turn":
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text":
                        print(f"\nAgente → {block.text}\n")
                break

            elif response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        print(f"  🔧 {block.name}...", flush=True)
                        result = execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason — still show any text
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text":
                        print(f"\nAgente → {block.text}\n")
                break
