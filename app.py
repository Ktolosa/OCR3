import streamlit as st
import pandas as pd
from ollama import Client
from pdf2image import convert_from_path
import tempfile
import os
import json
import time
import io

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Nexus Extractor (Ollama Cloud)", layout="wide")
st.title("☁️ Nexus Extractor: Ollama Cloud Oficial")

# 1. Configurar Cliente para Ollama Cloud
if "OLLAMA_API_KEY" in st.secrets:
    # Conexión al endpoint oficial de la nube de Ollama
    client = Client(
        host='https://api.ollama.com',
        headers={'Authorization': f'Bearer {st.secrets["OLLAMA_API_KEY"]}'}
    )
else:
    st.error("❌ Falta la OLLAMA_API_KEY en secrets.toml")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Analiza esta imagen de factura.
        REGLAS:
        1. Si ves "Duplicado" o "Copia", JSON: {"tipo_documento": "Copia", "items": []}.
        2. Si es Original, extrae todo.
        JSON ESPERADO:
        {"tipo_documento": "Original/Copia", "numero_factura": "...", "fecha": "...", "orden_compra": "...", "proveedor": "...", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.00, "total_linea": 0.00}], "total_factura": 0.00}
    """,
    "Factura RadioShack": """
        Factura RadioShack. Extrae en JSON. Usa SKU como 'modelo'.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "RadioShack", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0}
    """,
    "Factura Mabe": """
        Factura Mabe. Extrae en JSON. Usa CODIGO MABE como 'modelo'.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0}
    """
}

# ==========================================
# 🧠 LÓGICA DE ANÁLISIS
# ==========================================
def analizar_pagina(image, prompt_sistema):
    try:
        # 1. Convertir imagen a bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        img_bytes = img_byte_arr.getvalue()

        # 2. Llamada a OLLAMA CLOUD
        # NOTA: Verifica que el modelo exista en la nube. Si llama3.2-vision no está, prueba 'llama3.2' (pero podría no ver imágenes).
        response = client.chat(
            model='llama3.2-vision', 
            messages=[{
                'role': 'user',
                'content': prompt_sistema + " Responde SOLO con JSON.",
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
        return {}, f"Error Ollama Cloud: {str(e)}"

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
    
    my_bar = st.progress(0, text=f"Procesando {filename} en la Nube de Ollama...")

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
    st.header("Configuración")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))
    st.info("☁️ Conectado a Ollama Official Cloud")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar"):
    gran_acumulado = []
    st.divider()
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"Analizando..."):
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
        st.download_button("📥 Descargar Todo (CSV)", csv, "extraccion_ollama_cloud.csv", "text/csv")
