import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("MRP Debug Mode")

bom_file = st.file_uploader("Upload BOM")
req_file = st.file_uploader("Upload Requirement File")

if bom_file and req_file:

    try:
        bom = pd.read_excel(bom_file, engine="openpyxl")
        req = pd.read_excel(req_file, sheet_name="Requirement", engine="openpyxl")
        stock = pd.read_excel(req_file, sheet_name="Stock", engine="openpyxl")

        st.success("✅ Files loaded successfully")

        st.write("BOM Columns:", bom.columns.tolist())
        st.write("REQ Columns:", req.columns.tolist())
        st.write("STOCK Columns:", stock.columns.tolist())

        st.dataframe(bom.head())
        st.dataframe(req.head())
        st.dataframe(stock.head())

    except Exception as e:
        st.error(f"❌ ERROR: {e}")
