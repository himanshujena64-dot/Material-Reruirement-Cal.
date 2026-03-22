import streamlit as st
import pandas as pd
from io import BytesIO

# Set page config
st.set_page_config(page_title="MRP Shortage Tool", layout="wide")

st.title("📊 MRP Shortage Tool")

# =========================
# SAFE EXCEL READER
# =========================
def read_excel_safe(uploaded_file, sheet_name=None):
    """
    Robustly reads Excel files by resetting the file pointer 
    and trying multiple engines.
    """
    uploaded_file.seek(0)
    engines = ["openpyxl", "pyxlsb", "xlrd"]
    
    for engine in engines:
        try:
            df = pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
            return df
        except Exception:
            uploaded_file.seek(0)
            continue
            
    try:
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)
    except Exception as e:
        # If a specific sheet failed, let's see what sheets actually exist
        uploaded_file.seek(0)
        all_sheets = pd.ExcelFile(uploaded_file).sheet_names
        st.error(f"Could not find sheet: '{sheet_name}'")
        st.info(f"Available sheets in your file are: {all_sheets}")
        st.stop()

# =========================
# SIDEBAR / FILE UPLOAD
# =========================
st.sidebar.header("Upload Data")
bom_file = st.sidebar.file_uploader("1. Upload BOM file", type=["xlsx", "xlsb", "xls"])
req_file = st.sidebar.file_uploader("2. Upload Requirement + Stock file", type=["xlsx", "xlsb", "xls"])

if st.button("Run MRP Analysis"):

    if bom_file and req_file:
        with st.spinner("Processing MRP..."):
            # 1. Read BOM
            bom = read_excel_safe(bom_file)

            # 2. Read Requirement and Stock
            # These names MUST match your Excel tabs exactly
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")

            # =========================
            # DATA CLEANING
            # =========================
            # Ensure we are dealing with DataFrames and strip headers
            for df, name in zip([bom, req, stock], ["BOM", "Requirement", "Stock"]):
                if isinstance(df, dict):
                    st.error(f"Critical Error: {name} file returned a dictionary instead of a table.")
                    st.stop()
                df.columns = df.columns.str.strip()

            # Normalize Column Names
            bom.rename(columns={"Alt.": "Alt"}, inplace=True)
            req.rename(columns={"Alt.": "Alt"}, inplace=True)

            def normalize(x):
                if pd.isna(x): return ""
                x = str(x).strip()
                if x.endswith(".0"): x = x[:-2]
                return x.zfill(10)

            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock["Component"].apply(normalize)
            req["BOM Header"] = req["BOM Header"].apply(normalize)

            # Numeric Conversions
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
            if "Required Qty" in bom.columns:
                bom["Quantity"] = bom["Required Qty"]
            bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)

            stock = stock.rename(columns={"Quantity": "Stock"})
            stock["Stock"] = pd.to_numeric(stock["Stock"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

            # =========================
            # BUILD BOM PARENTS
            # =========================
            parents = []
            stack = {}
            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                comp = bom.loc[i, "Component"]
                parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl-1)
                parents.append(parent)
                stack[lvl] = comp
            bom["Parent Component"] = parents

            # =========================
            # MRP LOGIC
            # =========================
            req_long = req.melt(id_vars=["BOM Header", "Alt"], var_name="Month", value_name="Demand")
            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0]
            req_long = req_long.rename(columns={"BOM Header": "Component"})

            current = req_long.copy()
            results = []
            max_level = int(bom["Level"].max())

            for lvl in range(1, max_level + 1):
                level_bom = bom[bom["Level"] == lvl]
                merged = current.merge(level_bom, left_on=["Component", "Alt"], right_on=["Parent Component", "Alt"], how="inner")

                if merged.empty: continue

                # Logic for Phantom Assemblies (Procurement 50)
                merged["Required"] = merged.apply(
                    lambda x: x["Demand"] if str(x["Special procurement"]) == "50"
                    else x["Demand"] * x["Quantity"], axis=1
                )

                grouped = merged.groupby(["Component_y", "Month", "Alt"], as_index=False)["Required"].sum()
                grouped = grouped.rename(columns={"Component_y": "Component"})
                grouped = grouped.merge(stock, on="Component", how="left")
                grouped["Stock"] = grouped["Stock"].fillna(0)
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                results.append(grouped)
                grouped["Demand"] = grouped["Shortage"]
                current = grouped.groupby(["Component", "Month", "Alt"], as_index=False)["Demand"].sum()

            # =========================
            # OUTPUT
            # =========================
            if results:
                final = pd.concat(results)
                pivot = final.pivot_table(index="Component", columns="Month", values="Required", aggfunc="sum").fillna(0).reset_index()
                pivot = pivot.merge(stock, on="Component", how="left").fillna(0)

                st.success("MRP Analysis Successful!")
                st.dataframe(pivot, use_container_width=True)

                output = BytesIO()
                with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                    pivot.to_excel(writer, index=False)
                
                st.download_button("📥 Download Results", output.getvalue(), "MRP_Report.xlsx")
            else:
                st.warning("No requirements found to process.")
    else:
        st.info("Upload both files in the sidebar to start.")