import ast
import re
import time
import json
import uuid
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seguridad")

PATRONES_INYECCION = [
    r"(?i)ignora\s+(todas\s+)?las\s+instrucciones\s+(anteriores|previas)",
    r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"(?i)system\s*prompt",
    r"(?i)prompt\s+del\s+sistema",
    r"(?i)olvida\s+(todo|tus\s+instrucciones)",
    r"(?i)forget\s+(your|all)\s+instructions",
    r"(?i)act\s+as\s+(if\s+you\s+are|a)\s+",
    r"(?i)nuevo\s+modo|new\s+mode",
    r"(?i)jailbreak",
    r"(?i)DAN\s+mode",
    r"(?i)developer\s+mode",
    r"(?i)modo\s+(desarrollador|admin)",
    r"(?i)\]\}\]>?",
]

PATRONES_PII = {
    "telefono_chile": re.compile(r"(?:\+?56)?\s*(?:9\s*\d{4}\s*\d{4}|[2-9]\d{7,8})"),
    "rut_chileno": re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK]\b"),
    "tarjeta_credito": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    "pasaporte": re.compile(r"\b[A-Z]{2}\d{6,7}\b"),
}

CATEGORIAS_ETICAS = {
    "violencia": ["hackear", "atacar", "explotar vulnerabilidad", "destruir", "arma", "bomba", "dano fisico"],
    "contenido_ilegal": ["robar datos", "suplantar identidad", "falsificar", "evadir impuestos", "lavado de dinero"],
    "manipulacion": ["manipular personas", "engano masivo", "desinformacion", "propaganda", "deepfake danino"],
}

PRECIOS_MODELOS = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
}

@dataclass
class ResultadoValidacion:
    es_valido: bool
    mensaje: str
    riesgo: str = "bajo"
    detalles: dict = field(default_factory=dict)

@dataclass
class ResultadoFiltroEtico:
    es_seguro: bool
    categorias_detectadas: list = field(default_factory=list)
    terminos_detectados: list = field(default_factory=list)
    mensaje: str = ""

# === TRAZABILIDAD ===

class SistemaTrazas:
    def __init__(self):
        self.trazas: list = []

    def crear_traza(self, session_id: str = "") -> str:
        trace_id = uuid.uuid4().hex[:12]
        self.trazas.append({
            "trace_id": trace_id,
            "session_id": session_id,
            "inicio": datetime.now(timezone.utc).isoformat(),
            "eventos": [],
            "errores": 0,
        })
        logger.info(f"Traza creada: {trace_id}")
        return trace_id

    def agregar_evento(self, trace_id: str, etapa: str, detalle: str = "", estado: str = "ok"):
        for t in self.trazas:
            if t["trace_id"] == trace_id:
                t["eventos"].append({
                    "etapa": etapa,
                    "detalle": detalle,
                    "estado": estado,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                if estado == "error":
                    t["errores"] += 1
                logger.info(f"[{trace_id}] {etapa}: {detalle} ({estado})")
                return
        logger.warning(f"Traza no encontrada: {trace_id}")

    def totalizar(self, trace_id: str) -> Optional[dict]:
        for t in self.trazas:
            if t["trace_id"] == trace_id:
                eventos = t["eventos"]
                total = len(eventos)
                errores = t["errores"]
                duracion = (datetime.now(timezone.utc) - datetime.fromisoformat(t["inicio"])).total_seconds()
                return {"trace_id": trace_id, "total_eventos": total, "errores": errores, "duracion_s": round(duracion, 2)}
        return None

    def detectar_anomalias(self, trace_id: str) -> list:
        for t in self.trazas:
            if t["trace_id"] == trace_id:
                anomalias = []
                tiempos = [e["timestamp"] for e in t["eventos"]]
                for i in range(1, len(tiempos)):
                    t1 = datetime.fromisoformat(tiempos[i-1])
                    t2 = datetime.fromisoformat(tiempos[i])
                    delta = (t2 - t1).total_seconds()
                    if delta > 5.0:
                        anomalias.append(f"Alta latencia en evento {i}: {delta:.1f}s")
                    if delta < 0.01:
                        anomalias.append(f"Evento {i} muy rapido ({delta:.3f}s), posible error")
                return anomalias
        return []

sistema_trazas = SistemaTrazas()

# === GESTOR DE PRESUPUESTO ===

class GestorPresupuesto:
    def __init__(self, presupuesto_diario: float = 1.0):
        self.presupuesto = presupuesto_diario
        self.gastos: dict = {}

    def _dia(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def estimar_costo(self, texto: str, modelo: str = "gpt-4o-mini", tipo: str = "input") -> float:
        tokens = max(1, len(texto) // 4)
        precio = PRECIOS_MODELOS.get(modelo, PRECIOS_MODELOS["gpt-4o-mini"]).get(tipo, 0.00015)
        return (tokens / 1000) * precio

    def registrar_uso(self, modelo: str, input_tokens: int, output_tokens: int) -> bool:
        dia = self._dia()
        if dia not in self.gastos:
            self.gastos[dia] = 0.0
        precio_input = PRECIOS_MODELOS.get(modelo, PRECIOS_MODELOS["gpt-4o-mini"])["input"]
        precio_output = PRECIOS_MODELOS.get(modelo, PRECIOS_MODELOS["gpt-4o-mini"])["output"]
        costo = (input_tokens / 1000) * precio_input + (output_tokens / 1000) * precio_output
        self.gastos[dia] += costo
        return self.gastos[dia] <= self.presupuesto

    def permitir_solicitud(self, texto: str, modelo: str = "gpt-4o-mini") -> bool:
        costo = self.estimar_costo(texto, modelo, "input")
        dia = self._dia()
        if dia not in self.gastos:
            self.gastos[dia] = 0.0
        return (self.gastos[dia] + costo) <= self.presupuesto

# === VALIDADOR DE ENTRADA ===

class ValidadorEntrada:
    def __init__(self, max_longitud: int = 2000):
        self.max_longitud = max_longitud
        self.historial = []

    def detectar_inyeccion(self, texto: str) -> ResultadoValidacion:
        for patron in PATRONES_INYECCION:
            if re.search(patron, texto):
                logger.warning(f"Inyeccion detectada: {patron[:50]}")
                return ResultadoValidacion(
                    es_valido=False,
                    mensaje="Intento de inyeccion de prompt detectado",
                    riesgo="critico",
                    detalles={"patron": patron}
                )
        return ResultadoValidacion(es_valido=True, mensaje="Sin inyeccion detectada")

    def detectar_pii(self, texto: str) -> ResultadoValidacion:
        encontrado = {}
        for tipo, patron in PATRONES_PII.items():
            matches = patron.findall(texto)
            if matches:
                encontrado[tipo] = [m[:3] + "***" + m[-2:] if len(m) > 5 else "***" for m in matches]
        if encontrado:
            return ResultadoValidacion(
                es_valido=False,
                mensaje=f"PII detectado: {', '.join(encontrado.keys())}",
                riesgo="alto",
                detalles={"pii": encontrado}
            )
        return ResultadoValidacion(es_valido=True, mensaje="Sin PII detectado")

    def validar_longitud(self, texto: str) -> ResultadoValidacion:
        if len(texto) > self.max_longitud:
            return ResultadoValidacion(
                es_valido=False,
                mensaje=f"Texto excede longitud maxima ({len(texto)}/{self.max_longitud})",
                riesgo="medio"
            )
        return ResultadoValidacion(es_valido=True, mensaje=f"Longitud valida ({len(texto)})")

    def validar_contenido(self, texto: str) -> ResultadoValidacion:
        problemas = []
        patrones_peligrosos = [r"<script[^>]*>", r"eval\(", r"exec\(", r"__import__", r"subprocess", r"os\.system"]
        for p in patrones_peligrosos:
            if re.search(p, texto, re.IGNORECASE):
                problemas.append(f"Codigo sospechoso: {p}")
        if problemas:
            return ResultadoValidacion(es_valido=False, mensaje="; ".join(problemas), riesgo="alto")
        return ResultadoValidacion(es_valido=True, mensaje="Contenido valido")

    def validar(self, texto: str) -> dict:
        resultados = {
            "inyeccion": self.detectar_inyeccion(texto),
            "pii": self.detectar_pii(texto),
            "longitud": self.validar_longitud(texto),
            "contenido": self.validar_contenido(texto),
        }
        es_seguro = all(r.es_valido for r in resultados.values())
        riesgo_maximo = max(resultados.values(), key=lambda r: ["bajo", "medio", "alto", "critico"].index(r.riesgo)).riesgo
        reporte = {"es_seguro": es_seguro, "riesgo_maximo": riesgo_maximo, "validaciones": resultados}
        self.historial.append(reporte)
        logger.info(f"Validacion entrada: seguro={es_seguro}, riesgo={riesgo_maximo}")
        return reporte

    def sanitizar(self, texto: str) -> str:
        texto = texto[:self.max_longitud]
        for patron in PATRONES_INYECCION:
            texto = re.sub(patron, "[BLOQUEADO]", texto)
        for tipo, patron in PATRONES_PII.items():
            texto = patron.sub(f"[{tipo.upper()}_REDACTADO]", texto)
        return texto.strip()

# === VALIDADOR DE SALIDA ===

class ValidadorSalida:
    def verificar_pii(self, respuesta: str) -> ResultadoValidacion:
        encontrado = {}
        for tipo, patron in PATRONES_PII.items():
            matches = patron.findall(respuesta)
            if matches:
                encontrado[tipo] = len(matches)
        if encontrado:
            return ResultadoValidacion(es_valido=False, mensaje=f"PII en salida: {encontrado}", riesgo="alto")
        return ResultadoValidacion(es_valido=True, mensaje="Sin PII en salida")

    def sanitizar_salida(self, respuesta: str) -> str:
        for tipo, patron in PATRONES_PII.items():
            respuesta = patron.sub(f"[{tipo.upper()}_REDACTADO]", respuesta)
        return respuesta

    def validar(self, respuesta: str) -> dict:
        resultados = {"pii": self.verificar_pii(respuesta)}
        es_seguro = all(r.es_valido for r in resultados.values())
        return {"es_seguro": es_seguro, "validaciones": resultados}

# === FILTRO ETICO ===

class FiltroEtico:
    def evaluar(self, texto: str) -> ResultadoFiltroEtico:
        texto_lower = texto.lower()
        categorias = []
        terminos = []
        for categoria, palabras in CATEGORIAS_ETICAS.items():
            for termino in palabras:
                if re.search(r"\b" + re.escape(termino) + r"\b", texto_lower):
                    categorias.append(categoria)
                    terminos.append(termino)
        categorias_unicas = list(set(categorias))
        if categorias_unicas:
            logger.warning(f"Filtro etico activado: {categorias_unicas}")
            return ResultadoFiltroEtico(
                es_seguro=False,
                categorias_detectadas=categorias_unicas,
                terminos_detectados=terminos,
                mensaje=f"Contenido bloqueado: categorias {categorias_unicas}",
            )
        return ResultadoFiltroEtico(es_seguro=True, mensaje="Contenido aprobado")

# === LIMITADOR DE TASA ===

class LimitadorTasa:
    def __init__(self, max_peticiones: int = 10, ventana_segundos: float = 60.0):
        self.max_peticiones = max_peticiones
        self.ventana = ventana_segundos
        self.peticiones: list[float] = []

    def permitir(self) -> bool:
        ahora = time.time()
        self.peticiones = [t for t in self.peticiones if ahora - t < self.ventana]
        if len(self.peticiones) >= self.max_peticiones:
            logger.warning(f"Rate limit excedido ({len(self.peticiones)}/{self.max_peticiones})")
            return False
        self.peticiones.append(ahora)
        return True

    def restantes(self) -> int:
        ahora = time.time()
        self.peticiones = [t for t in self.peticiones if ahora - t < self.ventana]
        return max(0, self.max_peticiones - len(self.peticiones))

# === EVALUACION MATEMATICA SEGURA ===

def evaluar_matematica_segura(expresion: str) -> str:
    try:
        arbol = ast.parse(expresion, mode="eval")
    except SyntaxError:
        return "Error: expresion invalida"
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, (ast.Expression, ast.BinOp, ast.UnaryOp,
                                ast.Constant, ast.Add, ast.Sub, ast.Mult,
                                ast.Div, ast.Pow, ast.Mod, ast.USub)):
            return f"Error: operacion no permitida ({type(nodo).__name__})"
    try:
        resultado = eval(compile(arbol, "<entrada>", "eval"))
        return str(resultado)
    except Exception as e:
        return f"Error: {str(e)}"

# === SISTEMA DE CONFIANZA (HUMAN-IN-THE-LOOP) ===

@dataclass
class DecisionConfianza:
    decision: str
    confianza: float
    mensaje: str = ""

class SistemaConfianza:
    def __init__(self, umbral_auto: float = 0.8, umbral_revision: float = 0.5):
        self.umbral_auto = umbral_auto
        self.umbral_revision = umbral_revision

    def evaluar(self, confianza: float) -> DecisionConfianza:
        if confianza >= self.umbral_auto:
            return DecisionConfianza(decision="AUTO_RESPONDER", confianza=confianza, mensaje="Confianza suficiente, respondiendo automaticamente")
        elif confianza >= self.umbral_revision:
            return DecisionConfianza(decision="ESCALAR_HUMANO", confianza=confianza, mensaje="Confianza moderada, escalando a humano")
        else:
            return DecisionConfianza(decision="RECHAZAR", confianza=confianza, mensaje="Confianza baja, solicitando mas informacion")

# === DETECTOR DE SESGOS ===

class DetectorSesgos:
    def detectar(self, respuestas_por_grupo: dict) -> dict:
        diferencias = {}
        grupos = list(respuestas_por_grupo.keys())
        for i in range(len(grupos)):
            for j in range(i + 1, len(grupos)):
                g1, g2 = grupos[i], grupos[j]
                r1, r2 = respuestas_por_grupo[g1], respuestas_por_grupo[g2]
                vec1 = self._vectorizar(r1)
                vec2 = self._vectorizar(r2)
                sim = self._coseno(vec1, vec2)
                if sim < 0.5:
                    diferencias[f"{g1}_vs_{g2}"] = {"similitud": round(sim, 3), "posible_sesgo": True}
                    logger.warning(f"Posible sesgo detectado: {g1} vs {g2} (sim={sim:.3f})")
        return diferencias

    def _vectorizar(self, texto: str) -> dict:
        palabras = texto.lower().split()
        vec = {}
        for p in palabras:
            vec[p] = vec.get(p, 0) + 1
        return vec

    def _coseno(self, v1: dict, v2: dict) -> float:
        interseccion = set(v1) & set(v2)
        num = sum(v1[k] * v2[k] for k in interseccion)
        d1 = sum(v ** 2 for v in v1.values()) ** 0.5
        d2 = sum(v ** 2 for v in v2.values()) ** 0.5
        if not d1 or not d2:
            return 0.0
        return num / (d1 * d2)

# === CACHE SEGURO ===

class CacheLLM:
    def __init__(self, max_size: int = 100):
        self.cache: dict = {}
        self.max_size = max_size
        self.orden: list = []

    def _generar_clave(self, prompt: str, modelo: str) -> str:
        contenido = f"{modelo}:{prompt}"
        return hashlib.sha256(contenido.encode()).hexdigest()[:16]

    def obtener(self, prompt: str, modelo: str) -> Optional[str]:
        clave = self._generar_clave(prompt, modelo)
        return self.cache.get(clave)

    def guardar(self, prompt: str, modelo: str, respuesta: str):
        clave = self._generar_clave(prompt, modelo)
        if clave not in self.cache:
            if len(self.orden) >= self.max_size:
                antiguo = self.orden.pop(0)
                self.cache.pop(antiguo, None)
            self.orden.append(clave)
        self.cache[clave] = respuesta
