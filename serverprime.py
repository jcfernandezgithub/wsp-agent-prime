from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import requests, os, time
from collections import deque, defaultdict

app = Flask(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Falta la variable de entorno OPENAI_API_KEY. Cárgala en .env (local) o en el panel del proveedor (Render/Railway).")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_URL = os.getenv("OPENAI_URL", "https://api.openai.com/v1/chat/completions")

# Otros parámetros leídos desde env (con defaults)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.1"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "600"))
MAX_TURNS_PER_USER = int(os.getenv("MAX_TURNS_PER_USER", "8"))

# ──────────────────────────────────────────────────────────────────────────────
# Conocimiento (resumen curado) - SOLO de "Prime Seguros"
# Mantener conciso para reducir tokens y evitar alucinaciones
# ──────────────────────────────────────────────────────────────────────────────
KNOWLEDGE_BASE = """
Marca: PRIME / Corredores de Seguros (Prime Seguros)
Enfoque: Gestión de carteras y asesoría técnica personalizada para personas, empresas y pymes.

Fundación:
- Prime Seguros fue fundada en 2012 por especialistas con experiencia en el sector asegurador.

Líneas de negocio (qué ofrecen):
- Seguros Generales: vehículos, responsabilidad civil, incendio, sismo, robo, transporte (nacional/internacional), equipos móviles, todo riesgo construcción, empresas/pymes, agrícola, accidentes personales, casco marítimo/aviación, perjuicio por paralización (PxP), paramétricos, buses, asiento pasajero.
- Garantía y Crédito: garantías (anticipo, fiel cumplimiento, correcta ejecución, venta en verde) y seguro de crédito (doméstico y exportaciones).
- Vida & Salud:
  • Colectivos: complemento de salud y adicionales (dental, catastrófico), vida, accidentes personales.
  • Individuales: complemento de salud, catastrófico, escolar, vida, asistencia en viajes, accidentes personales.

Servicios y propuesta:
- Asesoría técnica y personalizada: comprensión del riesgo y diseño de soluciones a medida.
- Cotización: búsqueda de alternativas en el mercado según necesidad del cliente.
- Administración: soporte de back office y comunicación continua con clientes.
- Siniestros: acompañamiento y gestión del siniestro con la compañía; apoyo en la liquidación.

Horario y dirección publicados:
- Lunes a Viernes: 9:00 a 18:00 hrs.
- Dirección: Av. Alonso de Córdova 5151, Of. 2102.

Alcance del asistente:
- Responder dudas generales sobre líneas de negocio, coberturas típicas, proceso de cotización, pasos para denunciar un siniestro y datos de contacto/horario.
- No entregar montos comerciales, primas ni condiciones particulares que dependan de la póliza/compañía; derivar a un ejecutivo cuando se requiera información específica de contrato.

Mensajes clave/FAQ:
- “¿Cotizan X?” → Sí, Prime analiza tu necesidad y busca en el mercado opciones adecuadas; solicitar datos básicos (tipo de seguro, actividad/uso, sumas aproximadas, etc.) para derivar.
- “¿Cómo declaro un siniestro?” → Indicar que Prime cuenta con un equipo de siniestros que guía el proceso y coordina con la compañía; pedir datos del siniestro y derivar a un ejecutivo.
- “¿Tienen salud complementaria/empresarial?” → Sí, en Vida & Salud (colectivo e individual). Explicar a alto nivel y ofrecer derivación.
- “¿Trabajan con empresas/pymes?” → Sí, foco relevante en empresas y pymes; mencionar responsabilidad civil, incendio, transporte, crédito y garantías, etc.
- “¿Dónde están y horario?” → Av. Alonso de Córdova 5151, Of. 2102. L-V 9:00–18:00.

Límites:
- No citar ni prometer coberturas, exclusiones, primas o plazos específicos; estos dependen de cada póliza/aseguradora.
- Si la pregunta excede la información disponible, ofrecer derivación a un ejecutivo.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Prompt de sistema con guardrails (Prime Seguros)
# ──────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""
Eres el asistente oficial de WhatsApp de Prime Seguros (PRIME / Corredores de Seguros).
Tono: cercano, claro, amable y profesional. Responde en español de Chile.
Alcance: SOLO hablas de Prime Seguros y de sus líneas/servicios (Generales, Garantía y Crédito, Vida & Salud, asesoría, cotizaciones y siniestros).
No opines de otros temas ni entregues consejos fuera de este alcance.

Instrucciones de seguridad y estilo:
- No alucines: si no hay información en la base, di que no cuentas con ese dato y ofrece derivar a un ejecutivo.
- Sé breve y ordenado (frases cortas, bullets cuando ayuden).
- Incluye disclaimers cuando corresponda (coberturas, primas y condiciones dependen de la póliza/aseguradora).
- Nunca inventes cifras, exclusiones, ni plazos comerciales.
- Si el usuario pide “reset” o “reiniciar”, reconoce y reinicia el hilo.

Información verificada:
{KNOWLEDGE_BASE}

Respuestas tipo:
- “¿Qué ofrecen?” → Explica las 3 líneas (Generales, Garantía y Crédito, Vida & Salud) y servicios (asesoría, cotización, administración y siniestros).
- “Quiero cotizar” → Pide datos básicos (tipo de seguro, actividad/uso, antecedentes de riesgo, montos/sumas estimadas si las tiene) y ofrece derivar.
- “Tuve un siniestro” → Indica que Prime tiene un equipo de siniestros que acompaña y gestiona con la compañía; solicita información del siniestro para escalar.
- “Horario/dirección” → Entregar los datos publicados (L-V 9:00–18:00; Av. Alonso de Córdova 5151, Of. 2102).
- “¿Trabajan con empresas/pymes?” → Sí, foco en empresas y pymes; mencionar seguros típicos (RC, incendio/sismo, transporte, crédito y garantías, etc.).
"""

# ──────────────────────────────────────────────────────────────────────────────
# Memoria en RAM por usuario
# ──────────────────────────────────────────────────────────────────────────────
class ConversationMemory:
    def __init__(self, max_turns=8):
        self.max_turns = max_turns
        self.store = defaultdict(lambda: deque(maxlen=max_turns*2))

    def get_history(self, user_id):
        return list(self.store[user_id])

    def append(self, user_id, role, content):
        self.store[user_id].append({"role": role, "content": content})

    def reset(self, user_id):
        self.store[user_id].clear()

MEMORY = ConversationMemory(MAX_TURNS_PER_USER)

# ──────────────────────────────────────────────────────────────────────────────
# OpenAI
# ──────────────────────────────────────────────────────────────────────────────
def ask_openai(from_number: str, user_text: str) -> str:
    try:
        # Comando de reset
        if user_text.strip().lower() in {"reset", "reiniciar", "inicio"}:
            MEMORY.reset(from_number)
            return "Listo, reinicié la conversación. ¿Te cuento qué seguros y servicios ofrece Prime Seguros?"

        # Construir historial
        history = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Mensajes previos del usuario (memoria)
        previous = MEMORY.get_history(from_number)
        for msg in previous:
            if msg["role"] in ("user", "assistant"):
                history.append(msg)

        # Mensaje actual
        history.append({"role": "user", "content": user_text})

        payload = {
            "model": OPENAI_MODEL,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "messages": history,
        }

        r = requests.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if 200 <= r.status_code < 300:
            data = r.json()
            answer = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "Gracias por tu mensaje 🙌")
            ).strip()

            # Actualiza memoria (solo si éxito)
            MEMORY.append(from_number, "user", user_text)
            MEMORY.append(from_number, "assistant", answer)

            return answer

        print("OpenAI error:", r.status_code, r.text[:500])
        return "Ahora mismo no puedo responder. ¿Puedes intentar de nuevo en un momento?"
    except Exception as e:
        print("OpenAI exception:", e)
        return "Tuvimos un problema procesando tu mensaje."

# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return "ok"

@app.post("/webhook")
def webhook():
    from_number = (request.form.get("From") or "").strip()  # "whatsapp:+56..."
    text = (request.form.get("Body") or "").strip()

    if not text or not from_number:
        return ("", 200)

    answer = ask_openai(from_number, text)

    resp = MessagingResponse()
    resp.message(answer)
    return Response(str(resp), mimetype="application/xml")

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
