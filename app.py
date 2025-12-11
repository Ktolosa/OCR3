import streamlit as st
import pandas as pd
from ollama import Client  # <--- CAMBIO: Usamos Cliente Ollama
from pdf2image import convert_from_path
import tempfile
import os
import json
import time
import base64
import io

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Nexus Extractor (Local Remoto)", layout="wide")
st.title("⚡ Nexus Extractor: Motor Local (Ollama Remoto)")

# 1. CONEXIÓN AL TÚNEL (NGROK)
# En lugar de API Key, buscamos la URL de tu túnel ngrok en los secretos
ngrok_url = st.secrets.get("OLLAMA_HOST")

if not ngrok_url:
    st.error("❌ No se encontró la dirección de tu Ollama Local.")
    st.info("Configura el secreto 'OLLAMA_HOST' en Streamlit Cloud con tu URL de ngrok (ej: https://xxxx.ngrok-free.app).")
    st.stop()

# Inicializamos el cliente apuntando a tu computadora
client = Client(host=ngrok_url)

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS (TUS MISMOS PROMPTS)
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Analiza la imagen de la factura.
        REGLA DE FILTRADO:
        1. Si el documento dice explícitamente "Duplicado" o "Copia", marca "tipo_documento" como "Copia" y deja "items" vacío.
        2. Si dice "Original" o no especifica, extrae todo.
        Responde SOLAMENTE con un JSON válido:
        {"tipo_documento": "Original/Copia", "numero_factura": "Invoice #", "fecha": "YYYY-MM-DD", "orden_compra": "PO #", "proveedor": "Vendor Name", "cliente": "Sold To", "items": [{"modelo": "Model No", "descripcion": "Description", "cantidad": 0, "precio_unitario": 0.00, "origen": ""}], "total_factura": 0.00}
    """,
    "Factura RadioShack": """
        Analiza esta factura de RadioShack. Extrae datos en JSON. Usa SKU como modelo.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "RadioShack", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "origen": ""}], "total_factura": 0.0}
    """,
    "Factura Mabe": """
        Analiza esta factura de Mabe. Extrae datos en JSON. Usa CODIGO MABE como modelo. Ignora impuestos.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "origen": ""}], "total_factura": 0.0}
    """,
    "Factura Goodyear": """
        Analiza esta factura de Goodyear.
        INSTRUCCIONES CRÍTICAS DE LECTURA:
        1. NÚMERO DE FACTURA:
           - Busca "INVOICE NUMBER" (ej: 300098911).
           - IMPORTANTE: Si en esta página NO aparece el texto "INVOICE NUMBER", devuelve null o "CONTINUACION".
        2. TABLA DE ITEMS:
           - Mapeo de columnas:
             'Code' -> modelo
             'Origin' -> origen (IMPORTANTE: Extraer el país, ej: Brazil, China, US). Si no hay dato, dejalo vacio "".
             'Description' -> descripcion
             'Qty' -> cantidad
             'Unit Value' -> precio_unitario
           - MANEJO DE SALTOS DE LÍNEA: Si la info está en dos líneas, únelas.

        Responde SOLAMENTE con este JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "...",
            "fecha": "...",
            "orden_compra": "...",
            "proveedor": "Goodyear International Corporation",
            "cliente": "...",
            "items": [
                {
                    "modelo": "...",
                    "origen": "País de Origen", 
                    "descripcion": "...",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """
}

# ==========================================
# 🛠️ FUNCIONES AUXILIARES
# ==========================================
def imagen_a_bytes(image):
    """Convierte imagen a bytes para Ollama"""
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return buffered.getvalue()

# ==========================================
# 🧠 LÓGICA DE ANÁLISIS (MODIFICADA PARA OLLAMA REMOTO)
# ==========================================
def analizar_pagina(image, prompt_sistema):
    try:
        # 1. Convertir imagen a bytes (Ollama usa bytes, no base64 string complejo)
        img_bytes = imagen_a_bytes(image)
        
        # 2. Enviar a tu PC a través del Túnel
        # Usamos el modelo Vision
        response = client.chat(
            model='llama3.2-vision', 
            format='json',  # Forzamos JSON
            messages=[
                {
                    'role': 'user',
                    'content': prompt_sistema,
                    'images': [img_bytes]
                }
            ],
            options={'temperature': 0}
        )
        
        # 3. Obtener respuesta
        texto_respuesta = response['message']['content']
        return json.loads(texto_respuesta), None

    except Exception as e:
        # Capturamos errores de conexión
        return {}, f"Error conectando a tu PC (Revisa ngrok): {str(e)}"

# ==========================================
# ⚙️ PROCESAMIENTO (TU LÓGICA ORIGINAL)
# ==========================================
def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error leyendo PDF: {e}"

    items_locales = []
    resumen_local = []
    
    ultimo_numero_factura = "S/N"
    
    my_bar = st.progress(0, text=f"Enviando a tu casa: {filename}...")

    for i, img in enumerate(images):
        data, error = analizar_pagina(img, prompt)
        
        if error:
            st.error(f"Error {filename} Pág {i+1}: {error}")
        
        elif not data or "copia" in str(data.get("tipo_documento", "")).lower():
            pass 
        else:
            factura_actual = str(data.get("numero_factura", "")).strip()
            
            if not factura_actual or factura_actual.lower() in ["none", "null", "continuacion", "pendiente"] or len(factura_actual) < 3:
                factura_id = ultimo_numero_factura
            else:
                factura_id = factura_actual
                ultimo_numero_factura = factura_actual

            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Factura_Origen"] = factura_id
                    item["Archivo_Origen"] = filename
                    
                    # Asegurar campos vacíos si faltan
                    if "origen" not in item: item["origen"] = "" 
                    if "modelo" not in item: item["modelo"] = ""
                    if "descripcion" not in item: item["descripcion"] = ""
                    if "cantidad" not in item: item["cantidad"] = 0
                    if "precio_unitario" not in item: item["precio_unitario"] = 0.0
                    
                    items_locales.append(item)
            
            ya_existe = any(d['Factura'] == factura_id and d['Archivo'] == filename for d in resumen_local)
            if not ya_existe and factura_id != "S/N":
                resumen_local.append({
                    "Archivo": filename,
                    "Factura": factura_id,
                    "Total": data.get("total_factura"),
                    "Cliente": data.get("cliente")
                })
        
        my_bar.progress((i + 1) / len(images))
        # time.sleep(0.5) # No necesario en remoto, ya hay latencia de red

    my_bar.empty()
    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ (TU DISEÑO ORIGINAL)
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))
    st.info("☁️ Streamlit Cloud --> 🏠 Tu PC")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar Remotamente"):
    gran_acumulado = []
    st.divider()
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"Conectando con tu Ollama Local..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = procesar_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    st.success(f"✅ {len(items)} items extraídos.")
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos.")

    if gran_acumulado:
        st.divider()
        st.subheader("📥 Descargas")
        
        df_final = pd.DataFrame(gran_acumulado)
        
        # Columnas específicas que pediste
        cols_deseadas = ['modelo', 'descripcion', 'cantidad', 'precio_unitario', 'origen', 'Factura_Origen']
        cols_finales = [c for c in cols_deseadas if c in df_final.columns]
        
        st.dataframe(df_final[cols_finales], use_container_width=True)
        
        # Generar Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_final[cols_finales].to_excel(writer, index=False, sheet_name='Items')
            workbook = writer.book
            worksheet = writer.sheets['Items']
            worksheet.set_column('B:B', 50) # Ancho descripción
            
        st.download_button(
            label="📊 Descargar Excel Normal (.xlsx)",
            data=buffer.getvalue(),
            file_name="Reporte_Remoto.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
