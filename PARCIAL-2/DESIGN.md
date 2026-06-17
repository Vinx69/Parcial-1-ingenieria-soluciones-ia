---
colors:
  primary: "#1A73E8"
  secondary: "#34A853"
  surface: "#FFFFFF"
  background: "#F8F9FA"
  error: "#EA4335"
  text_primary: "#202124"
  text_secondary: "#5F6368"
typography:
  font_family: "'Segoe UI', system-ui, sans-serif"
  heading_1: { size: 28px, weight: 700 }
  heading_2: { size: 22px, weight: 600 }
  heading_3: { size: 18px, weight: 600 }
  body: { size: 16px, weight: 400 }
  caption: { size: 14px, weight: 400 }
  button: { size: 16px, weight: 500 }
spacing:
  base: 8px
  scale: [4, 8, 12, 16, 24, 32, 48]
border_radius:
  small: 8px
  medium: 12px
  large: 16px
  full: 9999px

---
# Transportes Pardo - Design System

## Brand Voice
- Tono: amable, profesional, confiable
- La empresa opera en Puerto Montt, Chile
- Transporte de pasajeros

## Screens

### 1. Pantalla de Chat (principal)
- Header con logo de Transportes Pardo y nombre
- Burbujas de chat: usuario (verde), asistente (azul claro)
- Input de texto con boton enviar
- Indicador de "escribiendo..." del asistente
- Area scrollable de mensajes

### 2. Sidebar / Panel lateral
- Seccion de estado de seguridad (metricas en vivo)
  - Peticiones restantes
  - Total validaciones
  - Bloqueos de seguridad
- Tabla de viajes registrados
- Boton "Limpiar conversacion"

### 3. Pantalla de bienvenida
- Mensaje de bienvenida del asistente
- Breve instruccion de uso

## Endpoints de API (backend en http://localhost:8000)
- `GET /api/health` - Health check
- `POST /api/chat` - Enviar mensaje (body: {"mensaje": "...", "session_id": "..."})
- `GET /api/seguridad` - Metricas de seguridad
- `GET /api/viajes` - Lista de viajes registrados
