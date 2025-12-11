import streamlit as st
import pandas as pd
from ollama import Client # <--- Importamos Client para conectar remoto
from pdf2image import convert_from_path
import tempfile
import os
import json
import time
import io

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Nexus Extractor (Remoto)", layout="wide")
st.title("🌐 Nexus Extractor: Motor Centralizado (Ollama)")

# ==========================================
# 🔌 CONFIGURACIÓN DE CONEXIÓN AL SERVIDOR
# ==========================================
# CAMBIA ESTO POR LA IP DE TU PC POTENTE
IP_SERVIDOR_OLLAMA = "192.168.1.50"  # <--- ¡PON TU IP AQUÍ!
PUERTO_OLLAMA = "11434"

# Inicializamos el cliente apuntando al servidor
try:
    client = Client(host=f'http://{IP_SERVIDOR_OLLAMA}:{PUERTO_OLLAMA}')
    client.list() # Prueba de conexión
    st.sidebar.success(f"✅ Conectado al Servidor: {IP_SERVIDOR_OLLAMA}")
except Exception as e:
    st.sidebar.error(f"❌ Error conectando a {IP_SERVIDOR_OLLAMA}. ¿Ollama está corriendo con OLLAMA_HOST=0.0.0.0?")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Analiza esta imagen de factura.
        REGLAS:
        1. Si ves "Duplicado" o "Copia", el JSON debe tener "tipo_documento": "Copia" y "items": [].
        2. Si es Original, extrae todo.
        Responde SOLO con este JSON:
        {"tipo_documento": "Original/Copia", "numero_factura": "...", "fecha": "...", "orden_compra": "...", "proveedor": "...", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.00, "total_linea": 0.00}], "total_factura": 0.00}
    """,
    "Factura RadioShack": """
        Factura RadioShack. Extrae datos en JSON. Usa SKU como 'modelo'.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "RadioShack", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0}
    """,
    "Factura Mabe": """
        Factura Mabe. Extrae datos en JSON. Usa CODIGO MABE como 'modelo'.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0}
    """
}

# ==========================================
# 🧠 LÓGICA DE ANÁLISIS (REMOTO)
# ==========================================
def analizar_pagina(image, prompt_sistema):
    try:
        # 1. Convertir imagen a bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        img_bytes = img_byte_arr.getvalue()

        # 2. Llamada al SERVIDOR OLLAMA (Usando el objeto 'client' que configuramos arriba)
        response = client.chat(
            model='llama3.2-vision', 
            messages=[{
                'role': 'user',
                'content': prompt_sistema + " IMPORTANTE: Responde ÚNICAMENTE con el JSON válido.",
                'images': [img_bytes]
            }]
        )

        texto_respuesta = response['message']['content'].strip()
        
        # 3. Limpieza
        if "```json" in texto_respuesta: 
            texto_respuesta = texto_respuesta.replace("```json", "").replace("```", "")
        elif "```" in texto_respuesta:
            texto_respuesta = texto_respuesta.replace("```", "")
        
        return json.loads(texto_respuesta), None

    except Exception as e:
        return {}, f"Error Servidor: {str(e)}"

# ==========================================
# ⚙️ PROCESAMIENTO
# ==========================================
def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error leyendo PDF: {e}"

    items_locales = []
    resumen_local = []
    
    my_bar = st.progress(0, text=f"Enviando a Cerebro Central: {filename}...")

    for i, img in enumerate(images):
        data, error = analizar_pagina(img, prompt)
        
        if error:
            st.error(f"Error {filename} Pág {i+1}: {error}")
        elif not data or "copia" in str(data.get("tipo_documento", "")).lower():
            pass 
        else:
            factura_id = data.get("numero_factura", "S/N")
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Archivo_Origen"] = filename
                    item["Factura_Origen"] = factura_id
                    items_locales.append(item)
            resumen_local.append({
                "Archivo": filename,
                "Factura": factura_id,
                "Total": data.get("total_factura"),
                "Cliente": data.get("cliente")
            })
        
        my_bar.progress((i + 1) / len(images))
        
    my_bar.empty()
    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Configuración de Red")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))
    st.info(f"📡 Conectado a: {IP_SERVIDOR_OLLAMA}")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar en Servidor"):
    gran_acumulado = []
    st.divider()
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"El servidor está pensando..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = procesar_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    df = pd.DataFrame(items)
                    st.success(f"✅ {len(items)} items extraídos.")
                    st.dataframe(df, use_container_width=True)
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos.")

    if gran_acumulado:
        st.divider()
        csv = pd.DataFrame(gran_acumulado).to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Todo (CSV)", csv, "extraccion_remota.csv", "text/csv")
