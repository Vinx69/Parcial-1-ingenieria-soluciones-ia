import ast
import re
import time
import json
from dataclasses import dataclass, field
from typing import Optional


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
]

PATRONES_PII = {
    "correo_electronico": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "telefono_chile": re.compile(r"(?:\+?56)?\s*(?:9\s*\d{4}\s*\d{4}|[2-9]\d{7,8})"),
    "rut_chileno": re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK]\b"),
    "tarjeta_credito": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
}

CATEGORIAS_ETICAS = {
    "violencia": ["hackear", "atacar", "explotar vulnerabilidad", "destruir", "arma", "bomba", "dano fisico"],
    "contenido_ilegal": ["robar datos", "suplantar identidad", "falsificar", "evadir impuestos", "lavado de dinero"],
    "manipulacion": ["manipular personas", "engano masivo", "desinformacion", "propaganda", "deepfake danino"],
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


class ValidadorEntrada:
    def __init__(self, max_longitud: int = 2000):
        self.max_longitud = max_longitud
        self.historial = []

    def detectar_inyeccion(self, texto: str) -> ResultadoValidacion:
        for patron in PATRONES_INYECCION:
            if re.search(patron, texto):
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
        return reporte

    def sanitizar(self, texto: str) -> str:
        texto = texto[:self.max_longitud]
        for patron in PATRONES_INYECCION:
            texto = re.sub(patron, "[BLOQUEADO]", texto)
        for tipo, patron in PATRONES_PII.items():
            texto = patron.sub(f"[{tipo.upper()}_REDACTADO]", texto)
        return texto.strip()


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

    def validar(self, respuesta: str) -> dict:
        resultados = {"pii": self.verificar_pii(respuesta)}
        es_seguro = all(r.es_valido for r in resultados.values())
        return {"es_seguro": es_seguro, "validaciones": resultados}


class FiltroEtico:
    def evaluar(self, texto: str) -> ResultadoFiltroEtico:
        texto_lower = texto.lower()
        categorias = []
        terminos = []
        for categoria, palabras in CATEGORIAS_ETICAS.items():
            for termino in palabras:
                if termino in texto_lower:
                    categorias.append(categoria)
                    terminos.append(termino)
        categorias_unicas = list(set(categorias))
        if categorias_unicas:
            return ResultadoFiltroEtico(
                es_seguro=False,
                categorias_detectadas=categorias_unicas,
                terminos_detectados=terminos,
                mensaje=f"Contenido bloqueado: categorias {categorias_unicas}",
            )
        return ResultadoFiltroEtico(es_seguro=True, mensaje="Contenido aprobado")


class LimitadorTasa:
    def __init__(self, max_peticiones: int = 10, ventana_segundos: float = 60.0):
        self.max_peticiones = max_peticiones
        self.ventana = ventana_segundos
        self.peticiones: list[float] = []

    def permitir(self) -> bool:
        ahora = time.time()
        self.peticiones = [t for t in self.peticiones if ahora - t < self.ventana]
        if len(self.peticiones) >= self.max_peticiones:
            return False
        self.peticiones.append(ahora)
        return True

    def restantes(self) -> int:
        ahora = time.time()
        self.peticiones = [t for t in self.peticiones if ahora - t < self.ventana]
        return max(0, self.max_peticiones - len(self.peticiones))
