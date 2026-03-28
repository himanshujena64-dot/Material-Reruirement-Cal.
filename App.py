import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="MRP Tool", layout="wide")
st.title("📊 MRP Shortage Dashboard")

# ---------------- FILE UPLOAD ----------------
with st.sidebar:
    bom_file = st.file_uploader("1. BOM File", type=["xlsx"])
    req_file = st.file_uploader("2. Requirement File", type=["xlsx"])

# ---------------- UTILITIES ----------------
def read_excel(file, sheet=None):
    try:
        file.seek(0)
        df = pd.read_excel(file, sheet_name=sheet, engine="openpyxl")
        if isinstance(df, dict):
            return list(df.values())[0]
        return df
    except:
        return None

def clean_cols(df):
    df.columns = df.columns.str.strip()
    return df.rename(columns={"Alt.": "Alt", "Special procurement": "SP", "SP type": "SP"})

def normalize(x):
    if pd.isna(x): return ""
    return str(x).strip().replace(".0","").upper()

# ---------------- MAIN ----------------
if bom_file and req_file:

    if st.sidebar.button("🚀 Run MRP"):

        try:
            # ---------- LOAD ----------
            bom = read_excel(bom_file)
            req = read_excel(req_file, "Requirement")
            stock = read_excel(req_file, "Stock")

            if any(x is None for x in [bom, req, stock]):
                st.error("❌ File read failed. Ensure sheets 'Requirement' and 'Stock' exist.")
                st.stop()

            # ---------- CLEAN ----------
            bom = clean_cols(bom)
            req = clean_cols(req)
            stock = clean_cols(stock)

            # ---------- FIX COLUMNS ----------
            bom["Alt"] = bom.get("Alt", "")
            req["Alt"] = req.get("Alt", "")
            bom["SP"] = bom.get("SP", "")

            # ---------- NORMALIZE ----------
            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock["Component"].apply(normalize)
            req["BOM Header"] = req["BOM Header"].apply(normalize)

            # ---------- NUMERIC ----------
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0)
            qty_col = "Required Qty" if "Required Qty" in bom.columns else "Quantity"
            bom["Qty"] = pd.to_numeric(bom[qty_col], errors="coerce").fillna(0)

            stock = stock.rename(columns={"Quantity": "Stock"})
            stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

            # ---------- DEMAND ----------
            id_cols = ["BOM Header", "Alt"]
            month_cols = [c for c in req.columns if c not in id_cols]

            req_long = req.melt(id_vars=id_cols, value_vars=month_cols,
                                var_name="Month", value_name="Demand")

            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0]
            req_long = req_long.rename(columns={"BOM Header": "Parent Component"})

            # ---------- EXPLOSION ----------
            merged = req_long.merge(
                bom[["BOM Header", "Component", "Alt", "Qty"]],
                left_on=["Parent Component", "Alt"],
                right_on=["BOM Header", "Alt"],
                how="inner"
            )

            merged["Gross"] = merged["Demand"] * merged["Qty"]
            result_df = merged[["Component", "Month", "Gross"]]

            # ---------- PIVOT ----------
            pivot = (
                result_df.groupby(["Component", "Month"])["Gross"]
                .sum()
                .unstack()
                .fillna(0)
            )
            
            # Ensure all month columns are present and ordered
            for col in month_cols:
                if col not in pivot.columns:
                    pivot[col] = 0
            pivot = pivot[month_cols] 

            # ---------- STOCK MERGE ----------
            pivot = pivot.merge(stock.set_index("Component"), left_index=True, right_index=True, how="left").fillna(0)

            # ---------- 🔥 CORRECTED SHORTAGE LOGIC ----------
            demand_matrix = pivot[month_cols]
            stock_values = pivot["Stock"].values.reshape(-1, 1)
            
            # 1. Cumulative Demand across months
            cum_demand = demand_matrix.cumsum(axis=1)
            
            # 2. Total Shortage needed (Cumulative Demand - Stock, capped at 0)
            # This represents the total gap to be filled from month 1 to current month
            total_shortage_needed = (cum_demand - stock_values).clip(lower=0)
            
            # 3. Monthly Incremental Shortage
            # Subtract previous month's total shortage to see what is specifically needed THIS month
            monthly_shortage = total_shortage_needed.diff(axis=1).fillna(total_shortage_needed)
            
            # Update values in the pivot table
            pivot[month_cols] = monthly_shortage

            pivot = pivot.reset_index()

            # Final Polish: Merge descriptive info if available
            info_cols = ["Component", "Component descriptio", "Procurement type", "SP"]
            existing_info = [c for c in info_cols if c in bom.columns]
            if existing_info:
                info_df = bom[existing_info].drop_duplicates("Component")
                pivot = pivot.merge(info_df, on="Component", how="left")

            st.success("✅ MRP Run Successful - Shortage Logic Applied")
            st.dataframe(pivot, use_container_width=True)

            # ---------- DOWNLOAD ----------
            output = BytesIO()
            pivot.to_excel(output, index=False)
            st.download_button("📥 Download Shortage Report", output.getvalue(), "MRP_Shortage_Results.xlsx")

        except Exception as e:
            st.error(f"❌ An error occurred: {e}")

else:
    st.info("Please upload your BOM and Requirement files to begin.")
