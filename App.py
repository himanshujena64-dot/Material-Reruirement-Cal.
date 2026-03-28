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
    """Cloud-safe Excel reader with proper error handling"""
    try:
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine="openpyxl")
    except Exception as e:
        st.error(f"❌ Excel read failed ({sheet_name}): {e}")
        return None

def normalize(x):
    if pd.isna(x): 
        return ""
    x = str(x).strip()
    if x.endswith(".0"):
        x = x[:-2]
    return x.upper()

# --- 4. ENGINE ---
if bom_file and req_file:

    if st.sidebar.button("🚀 Run MRP Engine"):

        with st.spinner("Calculating Requirements..."):

            try:
                # --- A. LOAD DATA ---
                bom = read_excel_safe(bom_file)
                req = read_excel_safe(req_file, "Requirement")
                stock = read_excel_safe(req_file, "Stock")

                if bom is None or req is None or stock is None:
                    st.stop()

                # --- B. CLEAN COLUMN NAMES ---
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]

                bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Special procurement": "SP"}, inplace=True)
                req.rename(columns={"Alt.": "Alt"}, inplace=True)

                # --- C. VALIDATION ---
                required_bom_cols = ["Component", "BOM Header", "Level"]
                for col in required_bom_cols:
                    if col not in bom.columns:
                        st.error(f"❌ Missing column in BOM: {col}")
                        st.stop()

                if "Component" not in stock.columns:
                    st.error("❌ 'Component' missing in Stock sheet")
                    st.stop()

                # --- D. NORMALIZATION ---
                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # --- E. NUMERIC HANDLING ---
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0)

                if "Required Qty" in bom.columns:
                    bom["Quantity_Used"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)
                else:
                    bom["Quantity_Used"] = pd.to_numeric(bom.get("Quantity", 0), errors="coerce").fillna(0)

                stock = stock.rename(columns={"Quantity": "Stock"})
                stock["Stock"] = pd.to_numeric(
                    stock["Stock"].astype(str).str.replace(",", ""), errors="coerce"
                ).fillna(0)

                # --- F. PARENT TRACKING ---
                parents = []
                stack_tracker = {}

                for i in range(len(bom)):
                    lvl = bom.loc[i, "Level"]
                    parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack_tracker.get(lvl - 1)
                    parents.append(parent)
                    stack_tracker[lvl] = bom.loc[i, "Component"]

                bom["Parent Component"] = parents

                # --- G. REQUIREMENT MELT ---
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]

                if not month_cols:
                    st.error("❌ No month columns found in Requirement sheet")
                    st.stop()

                req_long = req.melt(
                    id_vars=id_cols,
                    value_vars=month_cols,
                    var_name="Month",
                    value_name="Demand"
                )

                req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
                req_long = req_long[req_long["Demand"] > 0]

                req_long = req_long.rename(columns={"BOM Header": "Parent Component"})

                # --- H. EXPLOSION ---
                current = req_long.copy()
                results = []

                max_lvl = int(bom["Level"].max())

                for lvl in range(1, max_lvl + 1):

                    level_bom = bom[bom["Level"] == lvl]

                    merged = current.merge(
                        level_bom,
                        on=["Parent Component", "Alt"],
                        how="inner"
                    )

                    if merged.empty:
                        continue

                    merged["Gross_Req"] = merged["Demand"] * merged["Quantity_Used"]

                    merged = merged.merge(stock, on="Component", how="left")
                    merged["Stock"] = merged["Stock"].fillna(0)

                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross_Req"]
                        if str(x.get("SP")) == "50"
                        else max(0, x["Gross_Req"] - x["Stock"]),
                        axis=1
                    )

                    results.append(merged[["Component", "Month", "Gross_Req"]])

                    current = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={
                            "Component": "Parent Component",
                            "Shortage": "Demand"
                        }
                    )

                # --- I. FINAL OUTPUT ---
                if results:
                    all_data = pd.concat(results, ignore_index=True)

                    final_pivot = (
                        all_data.groupby(["Component", "Month"])["Gross_Req"]
                        .sum()
                        .unstack()
                        .fillna(0)
                        .reset_index()
                    )

                    final_pivot = final_pivot.merge(stock, on="Component", how="left").fillna(0)

                    # optional info
                    info_cols = ["Component", "Component descriptio", "Procurement type", "SP"]
                    existing_cols = [c for c in info_cols if c in bom.columns]

                    if existing_cols:
                        info = bom[existing_cols].drop_duplicates("Component")
                        final_pivot = final_pivot.merge(info, on="Component", how="left")

                    st.success("✅ MRP Run Successful!")
                    st.dataframe(final_pivot, use_container_width=True)

                    # download
                    output = BytesIO()
                    final_pivot.to_excel(output, index=False)

                    st.download_button(
                        "📥 Download Final MRP Report",
                        output.getvalue(),
                        "MRP_Final_Report.xlsx"
                    )

                else:
                    st.warning("⚠️ No matching data found between Requirement & BOM")

            except Exception as e:
                st.error(f"❌ Critical error: {e}")

else:
    st.info("Please upload both BOM and Requirement/Stock files.")
