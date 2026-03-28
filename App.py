import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")

st.title("📊 MRP Shortage Analysis Dashboard")

# --- 2. SIDEBAR SETUP ---
with st.sidebar:
    st.header("📂 Data Upload")
    bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
    req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

# --- 3. UTILITY FUNCTIONS ---
def read_excel_safe(uploaded_file, sheet_name=None):
    uploaded_file.seek(0)
    for engine in ["openpyxl", "pyxlsb", "xlrd"]:
        try:
            return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
        except:
            continue
    return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

def normalize(x):
    if pd.isna(x): return ""
    x = str(x).strip()
    if x.endswith(".0"): x = x[:-2]
    return x.upper()

# --- 4. MAIN LOGIC ---
if bom_file and req_file:
    if st.sidebar.button("🚀 Run MRP Engine"):
        with st.spinner("Processing BOM Explosion..."):
            try:
                # 1. LOAD DATA
                bom = read_excel_safe(bom_file, sheet_name=0)
                req = read_excel_safe(req_file, sheet_name="Requirement")
                stock = read_excel_safe(req_file, sheet_name="Stock")

                # 2. CLEAN COLUMNS
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]
                
                bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Special procurement": "SP"}, inplace=True)
                req.rename(columns={"Alt.": "Alt"}, inplace=True)

                # 3. NORMALIZE DATA
                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # Numeric Prep
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
                bom["Quantity"] = pd.to_numeric(bom.get("Required Qty", bom.get("Quantity", 0)), errors="coerce").fillna(0)
                stock = stock.rename(columns={"Quantity": "Stock"})
                stock["Stock"] = pd.to_numeric(stock["Stock"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

                # 4. PATH TRACKING
                parents = []
                stack_tracker = {}
                for i in range(len(bom)):
                    lvl = bom.loc[i, "Level"]
                    comp = bom.loc[i, "Component"]
                    p_id = bom.loc[i, "BOM Header"] if lvl == 1 else stack_tracker.get(lvl - 1)
                    parents.append(p_id)
                    stack_tracker[lvl] = comp
                bom["Parent Component"] = parents

                # 5. MELT REQUIREMENTS
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]
                
                req_long = req.melt(id_vars=id_cols, value_vars=month_cols, var_name="Month", value_name="Demand")
                req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
                req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Parent Component"})

                # 6. RECURSIVE ENGINE
                current = req_long.copy()
                results = []
                max_lvl = int(bom["Level"].max()) if not bom["Level"].isna().all() else 0

                for lvl in range(1, max_lvl + 1):
                    level_bom = bom[bom["Level"] == lvl]
                    merged = current.merge(level_bom, on=["Parent Component", "Alt"], how="inner")

                    if merged.empty: continue

                    merged["Gross_Req"] = merged["Demand"] * merged["Quantity"]
                    merged = merged.merge(stock, on="Component", how="left")
                    merged["Stock"] = merged["Stock"].fillna(0)
                    
                    # Shortage Logic: Phantom (50) passes through, others consume stock
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross_Req"] if str(x["SP"]) == "50" else max(0, x["Gross_Req"] - x["Stock"]),
                        axis=1
                    )

                    # Store Gross Req for report
                    results.append(merged[["Component", "Month", "Gross_Req"]])

                    # Prepare for next level (Demand = Shortage)
                    current = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent Component", "Shortage": "Demand"}
                    )

                # 7. FINAL TABULATION
                if results:
                    all_data = pd.concat(results, ignore_index=True)
                    if not all_data.empty:
                        # Pivot Month columns back
                        summary = all_data.groupby(["Component", "Month"])["Gross_Req"].sum().unstack().fillna(0).reset_index()
                        
                        # Merge with stock and master info
                        final_df = summary.merge(stock, on="Component", how="left").fillna(0)
                        info = bom[["Component", "Component descriptio", "Procurement type", "SP"]].drop_duplicates(subset=["Component"])
                        final_df = final_df.merge(info, on="Component", how="left")

                        st.success("✅ Analysis Complete!")
                        st.dataframe(final_df, use_container_width=True)

                        # Export
                        output = BytesIO()
                        final_df.to_excel(output, index=False)
                        st.download_button("📥 Download Excel Report", output.getvalue(), "MRP_Full_Report.xlsx")
                    else:
                        st.warning("No data found during explosion.")
                else:
                    st.error("No valid BOM matches found for the requirements provided.")

            except Exception as e:
                st.error(f"Logic Error: {e}")

else:
    st.info("Please upload both the BOM Master and Requirement files to start.")
