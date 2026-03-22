import streamlit as st
import pandas as pd
from io import BytesIO

# Set page config for a better look
st.set_page_config(page_title="MRP Shortage Tool", layout="wide")

st.title("📊 MRP Shortage Tool")

# =========================
# SAFE EXCEL READER (FIXED)
# =========================
def read_excel_safe(uploaded_file, sheet_name=None):
    """
    Robustly reads Excel files by resetting the file pointer 
    and trying multiple engines.
    """
    # Reset pointer to start of file for every fresh read attempt
    uploaded_file.seek(0)
    
    # List of engines to try in order of preference
    engines = ["openpyxl", "pyxlsb", "xlrd"]
    
    for engine in engines:
        try:
            return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
        except Exception:
            uploaded_file.seek(0)
            continue
            
    # Final fallback: try without specifying engine
    try:
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)
    except Exception as e:
        st.error(f"Excel read failed on sheet '{sheet_name if sheet_name else 'Default'}': {e}")
        st.stop()

# =========================
# SIDEBAR / FILE UPLOAD
# =========================
st.sidebar.header("Upload Data")
bom_file = st.sidebar.file_uploader("Upload BOM file", type=["xlsx", "xlsb", "xls"])
req_file = st.sidebar.file_uploader("Upload Requirement + Stock file", type=["xlsx", "xlsb", "xls"])

if st.button("Run MRP Analysis"):

    if bom_file and req_file:
        with st.spinner("Processing MRP..."):
            # =========================
            # READ FILES
            # =========================
            # Reading BOM
            bom = read_excel_safe(bom_file)

            # Reading Requirement and Stock from the same file but different sheets
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")

            # =========================
            # DATA CLEANING & NORMALIZATION
            # =========================
            # Strip whitespace from headers
            for df in [bom, req, stock]:
                df.columns = df.columns.str.strip()

            # Handle common naming variations
            bom.rename(columns={"Alt.": "Alt"}, inplace=True)
            req.rename(columns={"Alt.": "Alt"}, inplace=True)

            def normalize(x):
                if pd.isna(x):
                    return ""
                x = str(x).strip()
                if x.endswith(".0"):
                    x = x[:-2]
                return x.zfill(10)

            # Apply normalization to match IDs across files
            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock["Component"].apply(normalize)
            req["BOM Header"] = req["BOM Header"].apply(normalize)

            # Convert numeric columns safely
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
            
            # Map "Required Qty" if it exists, otherwise use "Quantity"
            if "Required Qty" in bom.columns:
                bom["Quantity"] = bom["Required Qty"]
            bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)

            # Prepare Stock Data
            stock = stock.rename(columns={"Quantity": "Stock"})
            stock["Stock"] = stock["Stock"].astype(str).str.replace(",", "")
            stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

            # =========================
            # BOM PARENT HIERARCHY BUILD
            # =========================
            parents = []
            stack = {}

            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                comp = bom.loc[i, "Component"]

                # If Level 1, parent is the BOM Header. Otherwise, look up stack.
                parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl-1)
                parents.append(parent)
                stack[lvl] = comp

            bom["Parent Component"] = parents

            # =========================
            # MRP CALCULATIONS
            # =========================
            # Melt requirements into long format (Component, Alt, Month, Demand)
            req_long = req.melt(
                id_vars=["BOM Header", "Alt"],
                var_name="Month",
                value_name="Demand"
            )

            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0]
            req_long = req_long.rename(columns={"BOM Header": "Component"})

            current = req_long.copy()
            results = []
            max_level = int(bom["Level"].max())

            # Explode requirements down through the BOM levels
            for lvl in range(1, max_level + 1):
                level_bom = bom[bom["Level"] == lvl]

                merged = current.merge(
                    level_bom,
                    left_on=["Component", "Alt"],
                    right_on=["Parent Component", "Alt"],
                    how="inner"
                )

                if merged.empty:
                    continue

                # Logical check for special procurement
                merged["Required"] = merged.apply(
                    lambda x: x["Demand"] if str(x["Special procurement"]) == "50"
                    else x["Demand"] * x["Quantity"],
                    axis=1
                )

                # Group by Component and Month
                grouped = merged.groupby(
                    ["Component_y", "Month", "Alt"], as_index=False
                )["Required"].sum()

                grouped = grouped.rename(columns={"Component_y": "Component"})

                # Subtract Stock
                grouped = grouped.merge(stock, on="Component", how="left")
                grouped["Stock"] = grouped["Stock"].fillna(0)
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                results.append(grouped)

                # Pass shortages down as demand for the next level
                grouped["Demand"] = grouped["Shortage"]
                current = grouped.groupby(
                    ["Component", "Month", "Alt"], as_index=False
                )["Demand"].sum()

            # =========================
            # FINAL OUTPUT GENERATION
            # =========================
            if results:
                final = pd.concat(results)
                
                # Pivot for a readable Month-by-Month view
                pivot = final.pivot_table(
                    index="Component", 
                    columns="Month", 
                    values="Required", 
                    aggfunc="sum"
                ).fillna(0).reset_index()

                # Add stock info back to final view
                pivot = pivot.merge(stock, on="Component", how="left")
                pivot["Stock"] = pivot["Stock"].fillna(0)

                st.success("MRP Completed ✅")
                st.dataframe(pivot, use_container_width=True)

                # Download Logic
                output = BytesIO()
                with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                    pivot.to_excel(writer, index=False)
                
                st.download_button(
                    label="📥 Download MRP Report",
                    data=output.getvalue(),
                    file_name="MRP_Shortage_Report.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("No shortages or requirements found based on the provided data.")

    else:
        st.info("Please upload both the BOM and Requirement files in the sidebar to begin.")