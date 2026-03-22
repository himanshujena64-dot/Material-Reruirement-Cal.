import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="MRP Shortage Tool", layout="wide")
st.title("📊 MRP Shortage Tool")

def read_excel_safe(uploaded_file, sheet_name=None):
    uploaded_file.seek(0)
    engines = ["openpyxl", "pyxlsb", "xlrd"]
    
    for engine in engines:
        try:
            # We explicitly pass the sheet_name here
            df = pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
            return df
        except Exception:
            uploaded_file.seek(0)
            continue
            
    try:
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)
    except Exception as e:
        uploaded_file.seek(0)
        all_sheets = pd.ExcelFile(uploaded_file).sheet_names
        st.error(f"Could not read sheet: '{sheet_name if sheet_name is not None else 'First Sheet'}'")
        st.info(f"Available sheets: {all_sheets}")
        st.stop()

st.sidebar.header("Upload Data")
bom_file = st.sidebar.file_uploader("1. Upload BOM file", type=["xlsx", "xlsb", "xls"])
req_file = st.sidebar.file_uploader("2. Upload Requirement + Stock file", type=["xlsx", "xlsb", "xls"])

if st.button("Run MRP Analysis"):
    if bom_file and req_file:
        with st.spinner("Processing..."):
            # FIX: Force BOM to read the first sheet (index 0)
            bom = read_excel_safe(bom_file, sheet_name=0)
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")

            # Validate they are DataFrames
            dfs = {"BOM": bom, "Requirement": req, "Stock": stock}
            for name, df in dfs.items():
                if isinstance(df, dict):
                    st.error(f"Critical Error: {name} file returned multiple sheets. Please ensure it is a simple table.")
                    st.stop()
                df.columns = df.columns.str.strip()

            # Normalization logic
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

            # MRP Calculation logic...
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
            if "Required Qty" in bom.columns:
                bom["Quantity"] = bom["Required Qty"]
            bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)

            stock = stock.rename(columns={"Quantity": "Stock"})
            stock["Stock"] = pd.to_numeric(stock["Stock"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

            parents = []
            stack = {}
            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl-1)
                parents.append(parent)
                stack[lvl] = bom.loc[i, "Component"]
            bom["Parent Component"] = parents

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
                st.warning("No shortages found.")
    else:
        st.info("Upload both files in the sidebar.")