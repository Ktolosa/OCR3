import streamlit as st
import pandas as pd
from ollama import Client
from pdf2image import convert_from_path
import tempfile
import os
import json
import io

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Nexus Híbrido", layout="wide")
st.title("⚡ Nexus Extractor: Nube + Ollama Local")

# 1. CONEXIÓN AL TÚNEL (NGROK)
# Buscamos la URL en los secretos de Streamlit
ngrok_url = st.secrets.get("OLLAMA_HOST")

if not ngrok_url:
    st.error("❌ No se encontró la dirección de tu Ollama Local.")
    st.info("Configura el secreto 'OLLAMA_HOST' en Streamlit Cloud con tu URL de ngrok.")
    st.stop()

# --- CORRECCIÓN CLAVE: HEADER PARA EVITAR ERROR DE NGROK ---
try:
    client = Client(
        host=ngrok_url,
        headers={'ngrok-skip-browser-warning': 'true'} # <--- ESTO SOLUCIONA EL BLOQUEO
    )
except Exception as e:
    st.error(f"Error inicializando cliente: {e}")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS
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
        Analiza esta factura de Mabe. Extrae datos en JSON. Usa CODIGO MABE como modelo.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "origen": ""}], "total_factura": 0.0}
    """,
    "Factura Goodyear": """
        Analiza esta factura de Goodyear.
        INSTRUCCIONES:
        1. NÚMERO DE FACTURA: Busca "INVOICE NUMBER". Si no aparece, usa "CONTINUACION".
        2. TABLA DE ITEMS: 
           - Code -> modelo
           - Description -> descripcion
           - Qty -> cantidad
           - Unit Value -> precio_unitario
           - Origin -> origen (Busca columnas "Origin", "Orig", "Ctry". Ej: Brazil, China. Si no hay, déjalo vacío "").
        
        Responde SOLAMENTE JSON:
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
# 🛠️ FUNCIONES AUXILIARES
# ==========================================
def imagen_a_bytes(image):
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return buffered.getvalue()

def analizar_pagina(image, prompt_sistema):
    try:
        img_bytes = imagen_a_bytes(image)
        
        # Llamada Remota a tu PC
        response = client.chat(
            model='llama3.2-vision', # Asegúrate de tener este modelo instalado en tu PC
            format='json',
            messages=[{
                'role': 'user',
                'content': prompt_sistema,
                'images': [img_bytes]
            }],
            options={'temperature': 0}
        )
        return json.loads(response['message']['content']), None

    except Exception as e:
        return {}, f"Error de Conexión (Ngrok/Ollama): {str(e)}"

def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error leyendo PDF (Poppler): {e}"

    items_locales = []
    resumen_local = []
    ultimo_factura = "S/N"
    
    my_bar = st.progress(0, text=f"Enviando a tu casa: {filename}...")

    for i, img in enumerate(images):
        data, error = analizar_pagina(img, prompt)
        
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
                    
                    # Rellenar campos faltantes
                    for k in ["origen", "modelo", "descripcion"]:
                        if k not in item: item[k] = ""
                    for k in ["cantidad", "precio_unitario"]:
                        if k not in item: item[k] = 0
                        
                    items_locales.append(item)
            
            # Resumen visual
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
    st.info("🟢 Conectado a Ollama Remoto")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar Remotamente"):
    gran_acumulado = []
    st.divider()
    
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 Procesando: {uploaded_file.name}", expanded=True):
            with st.spinner(f"Tu PC está analizando el documento..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = procesar_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    st.success(f"✅ {len(items)} items recibidos de tu PC.")
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos extraíbles.")

    # --- GENERAR EXCEL FINAL ---
    if gran_acumulado:
        st.divider()
        st.subheader("📥 Zona de Descargas")
        
        df_final = pd.DataFrame(gran_acumulado)
        
        # Selección de columnas
        cols_deseadas = ['modelo', 'descripcion', 'cantidad', 'precio_unitario', 'origen', 'Factura_Origen']
        cols_existentes = [c for c in cols_deseadas if c in df_final.columns]
        df_export = df_final[cols_existentes]
        
        st.dataframe(df_export, use_container_width=True)
        
        # Crear Excel en memoria
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Items')
            workbook = writer.book
            worksheet = writer.sheets['Items']
            worksheet.set_column('B:B', 50) 
            
        st.download_button(
            label="📊 Descargar Excel Normal (.xlsx)",
            data=buffer.getvalue(),
            file_name="Reporte_Remoto_Ollama.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
