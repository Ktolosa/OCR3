import streamlit as st
import pandas as pd
from pdf2image import convert_from_path
import tempfile
import os
import json
import io
import requests
import base64

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Nexus Híbrido", layout="wide")
st.title("⚡ Nexus Extractor: Nube + Ollama Local (Modo CPU)")

# 1. RECUPERAR URL DE NGROK
ngrok_url = st.secrets.get("OLLAMA_HOST")

if not ngrok_url:
    st.error("❌ Falta el secreto 'OLLAMA_HOST'.")
    st.stop()

ngrok_url = ngrok_url.rstrip('/')

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Analiza la imagen. Responde SOLO JSON:
        {"tipo_documento": "Original", "numero_factura": "Invoice #", "fecha": "YYYY-MM-DD", "orden_compra": "PO #", "proveedor": "Vendor Name", "cliente": "Sold To", "items": [{"modelo": "Model No", "descripcion": "Description", "cantidad": 0, "precio_unitario": 0.00, "origen": ""}], "total_factura": 0.00}
    """,
    "Factura RadioShack": """
        Analiza esta factura. JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "RadioShack", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "origen": ""}], "total_factura": 0.0}
    """,
    "Factura Mabe": """
        Analiza esta factura. JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "origen": ""}], "total_factura": 0.0}
    """,
    "Factura Goodyear": """
        Analiza esta factura de Goodyear.
        INSTRUCCIONES:
        1. Factura: Busca INVOICE NUMBER.
        2. Items: Code->modelo, Description->descripcion, Qty->cantidad, Unit Value->precio_unitario.
        3. Origen: Busca columna Origin/Orig/Ctry.
        Responde SOLO JSON válido:
        {
            "tipo_documento": "Original",
            "numero_factura": "...",
            "proveedor": "Goodyear",
            "items": [
                {
                    "modelo": "...",
                    "descripcion": "...",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "origen": "..."
                }
            ],
            "total_factura": 0.00
        }
    """
}

# ==========================================
# 🛠️ FUNCIONES MANUALES
# ==========================================
def codificar_imagen_base64(image):
    buffered = io.BytesIO()
    # Optimizamos un poco la compresión JPG para que viaje más rápido
    image.save(buffered, format="JPEG", quality=85)
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return img_str

def analizar_pagina_raw(image, prompt_sistema):
    try:
        b64_image = codificar_imagen_base64(image)
        
        payload = {
            "model": "llama3.2-vision",
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "user",
                    "content": prompt_sistema,
                    "images": [b64_image]
                }
            ]
        }
        
        # --- AQUÍ ESTÁ EL CAMBIO CLAVE: TIMEOUT 600 ---
        response = requests.post(
            f"{ngrok_url}/api/chat",
            json=payload,
            headers={'ngrok-skip-browser-warning': 'true'},
            verify=False,
            timeout=600  # <--- 10 MINUTOS DE ESPERA (Antes eran 2)
        )
        
        if response.status_code == 200:
            respuesta_json = response.json()
            contenido = respuesta_json['message']['content']
            return json.loads(contenido), None
        else:
            return {}, f"Error del Servidor ({response.status_code}): {response.text}"

    except Exception as e:
        return {}, f"Error de Conexión: {str(e)}"

# ==========================================
# ⚙️ PROCESAMIENTO
# ==========================================
def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        # --- CAMBIO CLAVE: BAJAMOS DPI A 150 ---
        # Esto hace la imagen más pequeña y rápida de procesar para tu CPU
        images = convert_from_path(pdf_path, dpi=150) 
    except Exception as e:
        return [], [], f"Error Poppler: {e}"

    items_locales = []
    resumen_local = []
    ultimo_factura = "S/N"
    
    my_bar = st.progress(0, text=f"Tu PC está pensando (Paciencia)... {filename}")

    for i, img in enumerate(images):
        data, error = analizar_pagina_raw(img, prompt)
        
        if error:
            st.error(f"Error {filename} Pág {i+1}: {error}")
        
        elif not data or "copia" in str(data.get("tipo_documento", "")).lower():
            pass 
        else:
            factura_id = str(data.get("numero_factura", "")).strip()
            if not factura_id or len(factura_id) < 3 or "null" in factura_id.lower():
                factura_id = ultimo_factura
            else:
                ultimo_factura = factura_id

            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Factura_Origen"] = factura_id
                    item["Archivo_Origen"] = filename
                    
                    for k in ["origen", "modelo", "descripcion"]:
                        if k not in item: item[k] = ""
                    for k in ["cantidad", "precio_unitario"]:
                        if k not in item: item[k] = 0
                        
                    items_locales.append(item)
            
            # Resumen
            ya_existe = any(d['Factura'] == factura_id and d['Archivo'] == filename for d in resumen_local)
            if not ya_existe and factura_id != "S/N":
                resumen_local.append({
                    "Archivo": filename,
                    "Factura": factura_id,
                    "Total": data.get("total_factura")
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
    st.info(f"Conectado a: {ngrok_url}")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar Remotamente"):
    gran_acumulado = []
    st.divider()
    
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"Tu PC está analizando (Puede tardar varios minutos)..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = procesar_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    st.success(f"✅ {len(items)} items recibidos.")
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos.")

    if gran_acumulado:
        st.divider()
        st.subheader("📥 Descargas")
        
        df_final = pd.DataFrame(gran_acumulado)
        cols_deseadas = ['modelo', 'descripcion', 'cantidad', 'precio_unitario', 'origen', 'Factura_Origen']
        cols_existentes = [c for c in cols_deseadas if c in df_final.columns]
        df_export = df_final[cols_existentes]
        
        st.dataframe(df_export, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Items')
            worksheet = writer.sheets['Items']
            worksheet.set_column('B:B', 50)
            
        st.download_button(
            label="📊 Descargar Excel",
            data=buffer.getvalue(),
            file_name="Reporte_Final.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
