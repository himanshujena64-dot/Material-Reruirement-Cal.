import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="App-2: MRP Calculation", layout="wide")
st.title("📊 App-2: MRP Requirement Calculation")

# --- 2. SIDEBAR ---
with st.sidebar:
    st.header("📂 Data Upload")
    bom_file = st.file_uploader("1. BOM Master File", type=["xlsx"])
    req_file = st.file_uploader("2. Req & Stock File", type=["xlsx"])

# --- 3. UTILITIES ---
def read_excel_safe(uploaded_file, sheet_name=None):
    uploaded_file.seek(0)
    try:
        # Using openpyxl to ensure decimal precision
        df = pd.read_excel(uploaded_file, sheet_name=sheet_name, engine="openpyxl")
        return df
    except:
        return None

def normalize(x):
    if pd.isna(x): return ""
    x = str(x).strip()
    if x.endswith(".0"): x = x[:-2]
    return x.upper()

# --- 4. ENGINE ---
if bom_file and req_file:
    if st.sidebar.button("Calculate Requirement"):
        with st.spinner("Executing Multi-Level Netting..."):
            try:
                # A. LOAD DATA
                bom_raw = read_excel_safe(bom_file)
                req_raw = read_excel_safe(req_file, "Requirement")
                stock_raw = read_excel_safe(req_file, "Stock")

                if any(df is None for df in [bom_raw, req_raw, stock_raw]):
                    st.error("❌ Critical Error: Missing sheets or incorrect file format.")
                    st.stop()

                # B. CLEAN & NORMALIZE
                bom = bom_raw.rename(columns=lambda x: str(x).strip())
                req = req_raw.rename(columns=lambda x: str(x).strip())
                stock = stock_raw.rename(columns=lambda x: str(x).strip())

                for df in [bom, stock]: 
                    df["Component"] = df["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # C. NUMERIC PREP
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0)
                qty_col = "Required Qty" if "Required Qty" in bom.columns else "Quantity"
                bom["Qty"] = pd.to_numeric(bom[qty_col], errors="coerce").fillna(0.0)
                
                stock = stock.rename(columns={"Quantity": "Stock_Qty"})
                stock["Stock_Qty"] = pd.to_numeric(stock["Stock_Qty"], errors="coerce").fillna(0.0)

                # D. INITIAL DEMAND SETUP
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]
                
                current_demand = req.melt(id_vars=id_cols, value_vars=month_cols, 
                                         var_name="Month", value_name="Demand")
                current_demand["Demand"] = pd.to_numeric(current_demand["Demand"], errors="coerce").fillna(0.0)
                current_demand = current_demand.rename(columns={"BOM Header": "Parent"})

                # E. MULTI-LEVEL EXPLOSION WITH STOCK DEDUCTION
                results = []
                max_depth = int(bom["Level"].max())

                for lvl in range(1, max_depth + 1):
                    bom_lvl = bom[bom["Level"] == lvl][["BOM Header", "Component", "Alt", "Qty", "SP"]]
                    
                    # Explode Parent Demand to Components
                    merged = current_demand.merge(bom_lvl, left_on=["Parent", "Alt"], right_on=["BOM Header", "Alt"], how="inner")
                    if merged.empty: break
                    
                    # Calculate Gross Requirement
                    merged["Gross"] = merged["Demand"] * merged["Qty"]
                    
                    # Merge with Stock
                    merged = merged.merge(stock[["Component", "Stock_Qty"]], on="Component", how="left")
                    merged["Stock_Qty"] = merged["Stock_Qty"].fillna(0.0)
                    
                    # Calculate Shortage (Net Requirement)
                    # Note: Using .clip(0) ensures we only pass POSITIVE shortages down to next level
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross"] if str(x.get("SP")) == "50" else max(0.0, x["Gross"] - x["Stock_Qty"]), 
                        axis=1
                    )
                    
                    results.append(merged[["Component", "Month", "Gross", "Shortage"]])
                    
                    # Net Requirement becomes Demand for next level
                    current_demand = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent", "Shortage": "Demand"}
                    )

                # F. FINAL PIVOT & FORMATTING (Matching your screenshot)
                if results:
                    final_data = pd.concat(results, ignore_index=True)
                    
                    # Pivot to get Month columns
                    pivot_df = final_data.groupby(["Component", "Month"])["Gross"].sum().unstack().fillna(0.0)
                    pivot_df = pivot_df[month_cols] # Ensure original month order
                    
                    # Re-attach Master Data (Description, Stock, Procurement Type)
                    info = bom[["Component", "Component descriptio", "Procurement type", "Special procurement"]].drop_duplicates("Component")
                    final_report = pivot_df.merge(info, on="Component", how="left")
                    final_report = final_report.merge(stock[["Component", "Stock_Qty"]], on="Component", how="left")
                    
                    # Reorder columns to match your screenshot
                    cols = ["Component", "Component descriptio", "Procurement type", "Special procurement", "Stock_Qty"] + month_cols
                    final_report = final_report[cols].rename(columns={"Stock_Qty": "Stock"})

                    st.success("✅ Calculation Complete")
                    st.dataframe(final_report, use_container_width=True)

                    # Export to Excel
                    output = BytesIO()
                    final_report.to_excel(output, index=False)
                    st.download_button("📥 Download Report", output.getvalue(), "MRP_Analysis.xlsx")
                else:
                    st.warning("⚠️ No matches found between BOM and Requirements.")

            except Exception as e:
                st.error(f"Error: {e}")
else:
    st.info("Please upload BOM and Requirement files to start.")
