import os
import re
import sqlite3
import smtplib
from typing import Optional
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGCHAIN_DISABLED", "true")
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
os.environ.setdefault("LANGSMITH_TRACING", "false")

GMAIL_REMITENTE = os.getenv("GMAIL_REMITENTE")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage, SystemMessage

from security import ValidadorEntrada, ValidadorSalida, FiltroEtico, LimitadorTasa

DB_FILE = os.path.join(os.path.dirname(__file__), "transportes_pardo.db")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12
}

def normalizar_fecha(texto: str) -> Optional[str]:
    texto = texto.lower().strip()
    hoy = datetime.now()
    if texto in ("hoy", "el dia de hoy"):
        return hoy.strftime("%d-%m-%Y")
    if texto in ("manana", "mañana", "el dia de manana", "el dia de mañana"):
        return (hoy + timedelta(days=1)).strftime("%d-%m-%Y")
    if texto in ("pasado manana", "pasado mañana", "el dia siguiente"):
        return (hoy + timedelta(days=2)).strftime("%d-%m-%Y")
    # DD-MM-YYYY o DD/MM/YYYY
    m = re.match(r"(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{4})", texto)
    if m:
        d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{d:02d}-{mes:02d}-{a}"
    # YYYY-MM-DD
    m = re.match(r"(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})", texto)
    if m:
        a, mes, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{d:02d}-{mes:02d}-{a}"
    # DD de MES (ej: "5 de enero 2026")
    m = re.match(r"(\d{1,2})\s*de\s*(\w+)\s*(?:de\s*)?(\d{4})?", texto)
    if m:
        d, mes_nombre = int(m.group(1)), m.group(2).lower()
        a = int(m.group(3)) if m.group(3) else hoy.year
        mes = MESES.get(mes_nombre)
        if mes:
            return f"{d:02d}-{mes:02d}-{a}"
    # MES DD (ej: "enero 15 2026")
    for nombre, num_mes in MESES.items():
        if nombre in texto:
            m = re.search(r"(\d{1,2})\s*(?:,?\s*(\d{4}))?", texto)
            if m:
                d = int(m.group(1))
                a = int(m.group(2)) if m.group(2) else hoy.year
                return f"{d:02d}-{num_mes:02d}-{a}"
    return None

def normalizar_hora(texto: str) -> Optional[str]:
    texto = texto.lower().strip()
    if not texto:
        return None
    # "medianoche", "media noche"
    if texto in ("medianoche", "media noche", "las 12 de la noche"):
        return "00:00"
    # "mediodia", "medio dia"
    if texto in ("mediodia", "medio dia", "las 12 del dia"):
        return "12:00"
    # "8am", "8:00am", "8:00a.m.", "8 am"
    m = re.match(r"(\d{1,2})\s*(?::(\d{2}))?\s*(?:a\.?\s*m\.?|am)\s*$", texto, re.I)
    if m:
        h, mins = int(m.group(1)), int(m.group(2) or 0)
        if h == 12:
            h = 0
        return f"{h:02d}:{mins:02d}"
    # "1pm", "1:00pm", "1:00p.m.", "1 pm"
    m = re.match(r"(\d{1,2})\s*(?::(\d{2}))?\s*(?:p\.?\s*m\.?|pm)\s*$", texto, re.I)
    if m:
        h, mins = int(m.group(1)), int(m.group(2) or 0)
        if h != 12:
            h += 12
        return f"{h:02d}:{mins:02d}"
    # "1 de la tarde", "8 de la manana", "8 de la noche"
    m = re.match(r"(\d{1,2})\s*de\s*la\s*(tarde|noche|manana|mañana|madrugada)", texto, re.I)
    if m:
        h = int(m.group(1))
        periodo = m.group(2).lower()
        if periodo in ("tarde", "noche"):
            if h < 12:
                h += 12
        else:
            if h >= 12:
                h = 0 if h == 12 else h - 12
        return f"{h:02d}:00"
    # "13:00" formato 24h
    m = re.match(r"(\d{1,2}):(\d{2})", texto)
    if m:
        h, mins = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return f"{h:02d}:{mins:02d}"
    # Solo numero "8" -> asumir hora
    m = re.match(r"^(\d{1,2})$", texto)
    if m:
        h = int(m.group(1))
        return f"{h:02d}:00"
    return None

llm = ChatOpenAI(
    base_url=os.getenv("GITHUB_BASE_URL", "https://models.github.ai/inference"),
    api_key=os.getenv("GITHUB_TOKEN"),
    model="gpt-4o-mini",
    temperature=0.2
)

validador_entrada = ValidadorEntrada(max_longitud=2000)
validador_salida = ValidadorSalida()
filtro_etico = FiltroEtico()
limitador = LimitadorTasa(max_peticiones=10, ventana_segundos=60.0)

viaje_actual = {
    "nombre": None, "origen": None, "destino": None,
    "fecha": None, "hora": None, "pasajeros": None
}
_ultimo_viaje = {}
_esperando_confirmacion = False
_esperando_correo = False

def obtener_conexion():
    return sqlite3.connect(DB_FILE)

def obtener_cliente_id(nombre: str, conn: Optional[sqlite3.Connection] = None) -> int:
    close = False
    if conn is None:
        conn = obtener_conexion()
        close = True
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO clientes (nombre) VALUES (?)", (nombre.strip(),))
    conn.commit()
    cursor.execute("SELECT id FROM clientes WHERE nombre = ?", (nombre.strip(),))
    cid = cursor.fetchone()[0]
    if close:
        conn.close()
    return cid

def obtener_lugar_id(nombre: str, conn: Optional[sqlite3.Connection] = None) -> int:
    close = False
    if conn is None:
        conn = obtener_conexion()
        close = True
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO lugares (nombre) VALUES (?)", (nombre.strip(),))
    conn.commit()
    cursor.execute("SELECT id FROM lugares WHERE nombre = ?", (nombre.strip(),))
    lid = cursor.fetchone()[0]
    if close:
        conn.close()
    return lid

def inicializar_bd():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lugares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS viajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            origen_id INTEGER NOT NULL,
            destino_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            hora TEXT NOT NULL,
            pasajeros INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(cliente_id) REFERENCES clientes(id),
            FOREIGN KEY(origen_id) REFERENCES lugares(id),
            FOREIGN KEY(destino_id) REFERENCES lugares(id)
        )""")
    conn.commit()
    conn.close()

def validar_formato_fecha_hora(fecha: str, hora: str) -> bool:
    try:
        datetime.strptime(f"{fecha} {hora}", "%d-%m-%Y %H:%M")
        return True
    except ValueError:
        return False

def obtener_conflictos(fecha: str, hora: str) -> list[tuple[str, str]]:
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT v.hora, c.nombre FROM viajes v "
        "JOIN clientes c ON v.cliente_id = c.id "
        "WHERE v.fecha = ? AND v.hora = ?", (fecha, hora)
    )
    viajes = cursor.fetchall()
    conn.close()
    return viajes

@tool
def guardar_datos_viaje(
    nombre: Optional[str] = None,
    origen: Optional[str] = None,
    destino: Optional[str] = None,
    fecha: Optional[str] = None,
    hora: Optional[str] = None,
    pasajeros: Optional[int] = None
) -> str:
    """Guarda o actualiza los datos del viaje. PASA SIEMPRE el texto exacto del cliente, la funcion normaliza automaticamente fechas como 'manana', 'pasado manana', '5 de enero', '1/1/2026', '8am', '1pm', '1 de la tarde', '13:00'."""
    global viaje_actual
    if nombre:
        viaje_actual["nombre"] = nombre.strip()
    if origen:
        viaje_actual["origen"] = origen.strip()
    if destino:
        viaje_actual["destino"] = destino.strip()
    if fecha:
        fecha_normalizada = normalizar_fecha(fecha)
        if fecha_normalizada:
            viaje_actual["fecha"] = fecha_normalizada
        else:
            viaje_actual["fecha"] = fecha.strip()
    if hora:
        hora_normalizada = normalizar_hora(hora)
        if hora_normalizada:
            viaje_actual["hora"] = hora_normalizada
        else:
            viaje_actual["hora"] = hora.strip()
    if pasajeros is not None:
        viaje_actual["pasajeros"] = pasajeros
    nf, nh = viaje_actual["fecha"], viaje_actual["hora"]
    if nf and nh:
        if not validar_formato_fecha_hora(nf, nh):
            return f"No se pudo interpretar la fecha u hora. Fecha recibida: '{nf}', Hora recibida: '{nh}'. Por favor pide al cliente que reformule."
        conflictos = obtener_conflictos(nf, nh)
        if conflictos:
            viaje_actual["hora"] = None
            hc, nc = conflictos[0]
            return (f"ERROR DE AGENDAMIENTO: Las {nh} del {nf} colisiona con un viaje de "
                    f"{nc} a las {hc}. Solicita al cliente otra hora.")
    return f"Progreso: {viaje_actual}"

@tool
def agendar_viaje_definitivo() -> str:
    """Finaliza y guarda el viaje en la base de datos cuando el cliente confirma."""
    global viaje_actual, _ultimo_viaje, _esperando_confirmacion, _esperando_correo
    faltantes = [k for k, v in viaje_actual.items() if v is None]
    if faltantes:
        return f"Faltan datos: {faltantes}"
    if not validar_formato_fecha_hora(viaje_actual["fecha"], viaje_actual["hora"]):
        return "ERROR: Formato de fecha u hora invalido"
    conn = obtener_conexion()
    cursor = conn.cursor()
    cid = obtener_cliente_id(viaje_actual["nombre"], conn)
    oid = obtener_lugar_id(viaje_actual["origen"], conn)
    did = obtener_lugar_id(viaje_actual["destino"], conn)
    cursor.execute(
        "INSERT INTO viajes (cliente_id, origen_id, destino_id, fecha, hora, pasajeros) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, oid, did, viaje_actual["fecha"], viaje_actual["hora"], int(viaje_actual["pasajeros"]))
    )
    conn.commit()
    conn.close()
    _ultimo_viaje = dict(viaje_actual)
    viaje_actual = {k: None for k in viaje_actual}
    _esperando_confirmacion = False
    _esperando_correo = True
    return "Viaje guardado permanentemente en la base de datos. Pide el correo del cliente para enviarle la confirmacion."

@tool
def enviar_correo_viaje(correo: str) -> str:
    """Envía al cliente un correo de confirmacion con los detalles del viaje. Llama SOLO despues de agendar_viaje_definitivo y cuando el cliente ya dio su correo."""
    global _esperando_correo
    if not _ultimo_viaje or not _ultimo_viaje.get("nombre"):
        return "ERROR: No hay un viaje confirmado para enviar."
    if not GMAIL_REMITENTE or not GMAIL_APP_PASSWORD:
        return "ERROR: Correo no configurado. Contacta al administrador."
    try:
        msg = MIMEMultipart()
        msg["From"] = GMAIL_REMITENTE
        msg["To"] = correo
        msg["Subject"] = "Viaje confirmado - Transportes Pardo"
        d = _ultimo_viaje
        body = (
            "Viaje confirmado!\n"
            "Gracias por escoger Transportes Pardo!\n\n"
            "Informacion de tu viaje:\n\n"
            f"Inicio del viaje: {d['origen']}\n"
            f"Destino: {d['destino']}\n"
            f"Fecha: {d['fecha']}\n"
            f"Hora: {d['hora']}\n"
            f"Cantidad de pasajeros: {d['pasajeros']}\n\n"
            "Buen Viaje!"
        )
        msg.attach(MIMEText(body, "plain"))
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_REMITENTE, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        _esperando_correo = False
        return "Correo enviado exitosamente."
    except Exception as e:
        _esperando_correo = False
        return f"ERROR al enviar correo: {str(e)}"

tools_map = {
    "guardar_datos_viaje": guardar_datos_viaje,
    "agendar_viaje_definitivo": agendar_viaje_definitivo,
    "enviar_correo_viaje": enviar_correo_viaje
}
llm_with_tools = llm.bind_tools([guardar_datos_viaje, agendar_viaje_definitivo, enviar_correo_viaje])

CAMPOS = {"nombre": "Nombre", "origen": "Origen", "destino": "Destino", "fecha": "Fecha", "hora": "Hora", "pasajeros": "Pasajeros"}

def _formatear_viaje(d: dict) -> str:
    partes = []
    for k, v in d.items():
        if v is not None:
            partes.append(f"{CAMPOS.get(k, k)}: {v}")
    return " | ".join(partes) if partes else "(vacio)"

def _campos_faltantes(d: dict) -> list[str]:
    etiq = {"nombre": "tu nombre", "origen": "el origen", "destino": "el destino", "fecha": "la fecha", "hora": "la hora", "pasajeros": "los pasajeros"}
    return [etiq[k] for k, v in d.items() if v is None]

def _todos_completos(d: dict) -> bool:
    return all(v is not None for v in d.values())

def _resumen_confirmar(d: dict) -> str:
    lineas = [f"  {CAMPOS[k]}: {v}" for k, v in d.items() if v is not None]
    return "\n".join(lineas)

SISTEMA = """Eres asistente de Transportes Pardo en Puerto Montt.

Tienes 3 funciones disponibles. DEBES usarlas cuando corresponda:

- guardar_datos_viaje: cuando el cliente te da nombre, origen, destino, fecha, hora o pasajeros. USA SIEMPRE.
- agendar_viaje_definitivo: cuando el cliente confirma el viaje.
- enviar_correo_viaje: cuando el cliente da su correo para la confirmacion.

Reglas:
- Pregunta un dato a la vez en orden: nombre -> origen -> destino -> fecha -> hora -> pasajeros.
- Cuando el cliente responda con un dato, USA guardar_datos_viaje. No respondas sin haberla usado.
- No describas las funciones. Responde natural.
- Si dan datos fuera de orden, guardalos y pregunta el siguiente faltante.

Datos guardados: {formulario_actual}
Faltan: {faltan}

Al confirmar: usa agendar_viaje_definitivo, pide correo, usa enviar_correo_viaje."""

def coordinar_agente(query_input: dict) -> dict:
    global viaje_actual, _esperando_confirmacion, _esperando_correo
    session_id = query_input.get("config", {}).get("configurable", {}).get("session_id", "default")
    history = obtener_historial(session_id)

    # Build state display
    if _esperando_correo and _ultimo_viaje:
        estado = "Viaje CONFIRMADO:\n" + _resumen_confirmar(_ultimo_viaje) + "\n\nPendiente: correo del cliente para enviar confirmacion."
        faltan = "correo del cliente"
    elif _esperando_confirmacion:
        estado = _formatear_viaje(viaje_actual)
        faltan = "confirmacion del cliente"
    else:
        estado = _formatear_viaje(viaje_actual)
        faltan = ", ".join(_campos_faltantes(viaje_actual)) if not _todos_completos(viaje_actual) else "ninguno"

    prompt_msg = SystemMessage(content=SISTEMA.format(formulario_actual=estado, faltan=faltan))
    history_msgs = history.messages
    user_msg = HumanMessage(content=query_input["input"])
    messages = [prompt_msg] + history_msgs + [user_msg]

    # Add user message to history before LLM calls
    history.add_user_message(query_input["input"])

    # Call LLM with tools
    response = llm_with_tools.invoke(messages)
    if not response.tool_calls:
        history.add_ai_message(response.content)
        if _esperando_confirmacion and _todos_completos(viaje_actual):
            _esperando_confirmacion = False
        return {"output": response.content}

    # Execute tool calls
    tool_messages = [response]
    for tc in response.tool_calls:
        if tc["name"] in tools_map:
            resultado = tools_map[tc["name"]].invoke(tc["args"])
            tool_messages.append(ToolMessage(content=resultado, tool_call_id=tc["id"]))
            # Update state
            if _esperando_correo and _ultimo_viaje:
                estado = "Viaje CONFIRMADO:\n" + _resumen_confirmar(_ultimo_viaje) + "\n\nPendiente: correo del cliente para enviar confirmacion."
                faltan = "correo del cliente"
            elif _esperando_confirmacion:
                estado = _formatear_viaje(viaje_actual)
                faltan = "confirmacion del cliente"
            else:
                estado = _formatear_viaje(viaje_actual)
                faltan = ", ".join(_campos_faltantes(viaje_actual)) if not _todos_completos(viaje_actual) else "ninguno"

    # Call LLM again with tool results in context
    full_msgs = [prompt_msg] + history_msgs + tool_messages
    final = llm.invoke(full_msgs)
    history.add_ai_message(final.content)

    # After tool call, if data complete, not expecting correo, and LLM didn't ask, force confirmation
    if _todos_completos(viaje_actual) and not _esperando_correo:
        txt = final.content.lower()
        if not any(w in txt for w in ["confirm", "correcto", "todo bien"]):
            _esperando_confirmacion = True
            return {"output": _resumen_confirmar(viaje_actual) + "\n\n¿Todo esta correcto? Confirma para agendar el viaje."}
    return {"output": final.content}

historiales = {}

def obtener_historial(session_id: str) -> InMemoryChatMessageHistory:
    if session_id not in historiales:
        historiales[session_id] = InMemoryChatMessageHistory()
    return historiales[session_id]

def validar_entrada_segura(texto: str) -> dict:
    return validador_entrada.validar(texto)

def validar_salida_segura(texto: str) -> dict:
    return validador_salida.validar(texto)

def evaluar_etico(texto: str):
    return filtro_etico.evaluar(texto)

def procesar_mensaje(mensaje: str, session_id: str = "default") -> dict:
    reporte_entrada = validar_entrada_segura(mensaje)
    if not reporte_entrada["es_seguro"]:
        return {
            "output": "Lo siento, no puedo procesar ese mensaje por seguridad.",
            "seguridad": reporte_entrada,
            "bloqueado": True
        }
    etico = evaluar_etico(mensaje)
    if not etico.es_seguro:
        return {
            "output": "Lo siento, no puedo procesar solicitudes con ese contenido.",
            "seguridad": {"es_seguro": False, "razon": etico.mensaje},
            "bloqueado": True
        }
    if not limitador.permitir():
        return {
            "output": f"Limite de solicitudes excedido. Espera {limitador.ventana}s.",
            "seguridad": {"es_seguro": False, "riesgo_maximo": "rate_limit"},
            "bloqueado": True
        }
    try:
        resultado = coordinar_agente({
            "input": mensaje,
            "config": {"configurable": {"session_id": session_id}}
        })
        texto = resultado["output"]
    except Exception as e:
        error_str = str(e)
        if "Unauthorized" in error_str or "401" in error_str:
            texto = "Error de autenticación con el modelo. Verifica tu GITHUB_TOKEN en el archivo .env"
        else:
            texto = f"Error interno del asistente. Detalles: {error_str[:100]}"
        return {
            "output": texto,
            "seguridad": {"es_seguro": False, "riesgo_maximo": "error_llm"},
            "bloqueado": True
        }
    reporte_salida = validar_salida_segura(texto)
    return {
        "output": texto,
        "seguridad": reporte_salida,
        "bloqueado": False
    }

def listar_viajes() -> list[tuple]:
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT v.id, c.nombre, lo.nombre, ld.nombre, v.fecha, v.hora, v.pasajeros "
        "FROM viajes v JOIN clientes c ON v.cliente_id = c.id "
        "JOIN lugares lo ON v.origen_id = lo.id "
        "JOIN lugares ld ON v.destino_id = ld.id"
    )
    filas = cursor.fetchall()
    conn.close()
    return filas

if __name__ == "__main__":
    inicializar_bd()
    print("Agente de Transportes Pardo con ciberseguridad")
    print("Comandos: salir, ver sql")
    session = "default"
    while True:
        user = input("Cliente: ")
        if user.lower() == "salir":
            break
        if user.lower() == "ver sql":
            for f in listar_viajes():
                print(f"ID:{f[0]} Cliente:{f[1]} {f[2]}->{f[3]} {f[4]} {f[5]} Pax:{f[6]}")
            continue
        if not user.strip():
            continue
        res = procesar_mensaje(user, session)
        print(f"Agente: {res['output']}")
