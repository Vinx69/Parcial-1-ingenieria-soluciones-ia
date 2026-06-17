import os
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from agent import (
    procesar_mensaje, inicializar_bd, listar_viajes,
    validador_entrada, limitador
)

app = FastAPI(title="Transportes Pardo API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

inicializar_bd()

class MensajeRequest(BaseModel):
    mensaje: str
    session_id: str = "default"

class MensajeResponse(BaseModel):
    respuesta: str
    bloqueado: bool = False
    razon_bloqueo: str = ""

@app.get("/api/health")
def health():
    return {"status": "ok", "agente": "Transportes Pardo"}

@app.post("/api/chat", response_model=MensajeResponse)
def chat(req: MensajeRequest):
    if not req.mensaje.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacio")
    resultado = procesar_mensaje(req.mensaje, req.session_id)
    return MensajeResponse(
        respuesta=resultado["output"],
        bloqueado=resultado.get("bloqueado", False),
        razon_bloqueo=resultado.get("seguridad", {}).get("riesgo_maximo", "")
    )

@app.get("/api/seguridad")
def seguridad():
    return {
        "peticiones_restantes": limitador.restantes(),
        "total_validaciones": len(validador_entrada.historial),
        "bloqueos": sum(1 for r in validador_entrada.historial if not r.get("es_seguro", True))
    }

@app.get("/api/viajes")
def viajes():
    return {"viajes": listar_viajes()}

FRONTEND_DIR = Path(__file__).parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("Transportes Pardo - Backend iniciado")
    print("Abre http://localhost:8000 en tu navegador")
    print("=" * 50)
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)
