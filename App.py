import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")
st.title("📊 MRP Shortage Analysis Dashboard")

# --- 2. SIDEBAR ---
with st.sidebar:
    st.header("📂 Data Upload")
    bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
    req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

# --- 3. UTILITIES ---
def read_excel_safe(uploaded_file, sheet_name=None):
    """Handles pointer resets and multiple engines for robust file reading."""
    uploaded_file.seek(0)
    for engine in ["openpyxl", "pyxlsb", "xlrd"]:
        try:
            return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
        except:
            continue
    return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

def normalize(x):
    """Cleans part numbers: removes .0, strips spaces, and converts to uppercase."""
    if pd.isna(x): return ""
    x = str(x).strip()
    if x.endswith(".0"): x = x[:-2]
    return x.upper()

# --- 4. ENGINE ---
if bom_file and req_file:
    if st.sidebar.button("🚀 Run MRP Engine"):
        with st.spinner("Calculating Requirements..."):
            try:
                # A. Load Data
                bom = read_excel_safe(bom_file, sheet_name=0)
                req = read_excel_safe(req_file, sheet_name="Requirement")
                stock = read_excel_safe(req_file, sheet_name="Stock")

                # B. Clean Column Names
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]
                
                bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Special procurement": "SP"}, inplace=True)
                req.rename(columns={"Alt.": "Alt"}, inplace=True)

                # C. Normalize Part Numbers
                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # D. Process Quantities and Levels
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
                
                # Logic: Prioritize 'Required Qty', fallback to 'Quantity'
                if "Required Qty" in bom.columns:
                    bom["Quantity_Used"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)
                else:
                    bom["Quantity_Used"] = pd.to_numeric(bom.get("Quantity", 0), errors="coerce").fillna(0)
                
                stock = stock.rename(columns={"Quantity": "Stock"})
                stock["Stock"] = pd.to_numeric(stock["Stock"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

                # E. Path Tracking (Parent-Child mapping)
                parents, stack_tracker = [], {}
                for i in range(len(bom)):
                    lvl = bom.loc[i, "Level"]
                    p_id = bom.loc[i, "BOM Header"] if lvl == 1 else stack_tracker.get(lvl - 1)
                    parents.append(p_id)
                    stack_tracker[lvl] = bom.loc[i, "Component"]
                bom["Parent Component"] = parents

                # F. Melt Requirements (Convert Month columns to rows)
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]
                req_long = req.melt(id_vars=id_cols, value_vars=month_cols, var_name="Month", value_name="Demand")
                req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
                req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Parent Component"})

                # G. Explosion Loop
                current = req_long.copy()
                results = []
                max_lvl = int(bom["Level"].max()) if not bom["Level"].isna().all() else 0

                for lvl in range(1, max_lvl + 1):
                    level_bom = bom[bom["Level"] == lvl]
                    merged = current.merge(level_bom, on=["Parent Component", "Alt"], how="inner")

                    if merged.empty:
                        continue

                    # Calculation: Demand * Required Qty
                    merged["Gross_Req"] = merged["Demand"] * merged["Quantity_Used"]
                    merged = merged.merge(stock, on="Component", how="left")
                    merged["Stock"] = merged["Stock"].fillna(0)
                    
                    # Shortage Logic: Phantom (50) passes through full demand, others subtract stock
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross_Req"] if str(x["SP"]) == "50" else max(0, x["Gross_Req"] - x["Stock"]),
                        axis=1
                    )

                    # Store results for this level
                    if "Gross_Req" in merged.columns:
                        results.append(merged[["Component", "Month", "Gross_Req"]])
                    
                    # Prepare input for next level
                    current = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent Component", "Shortage": "Demand"}
                    )

                # H. Final Aggregation
                if results:
                    all_data = pd.concat(results, ignore_index=True)
                    final_pivot = all_data.groupby(["Component", "Month"])["Gross_Req"].sum().unstack().fillna(0).reset_index()
                    final_pivot = final_pivot.merge(stock, on="Component", how="left").fillna(0)
                    
                    # Attach descriptive info
                    info = bom[["Component", "Component descriptio", "Procurement type", "SP"]].drop_duplicates(subset=["Component"])
                    final_pivot = final_pivot.merge(info, on="Component", how="left")

                    st.success("✅ MRP Run Successful!")
                    st.dataframe(final_pivot, use_container_width=True)

                    # Export to Excel
                    output = BytesIO()
                    final_pivot.to_excel(output, index=False)
                    st.download_button("📥 Download Final MRP Report", output.getvalue(), "MRP_Final_Report.xlsx")
                else:
                    st.warning("No matches found between Requirements and BOM Headers.")

            except Exception as e:
                st.error(f"Unexpected error during calculation: {e}")
else:
    st.info("Please upload both the BOM Master and Requirement/Stock files to begin.")
