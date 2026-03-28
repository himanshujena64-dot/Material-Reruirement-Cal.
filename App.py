import streamlit as st
import pandas as pd
from io import BytesIO

# ================= PAGE CONFIG =================
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")
st.title("📊 MRP Shortage Analysis Dashboard")

# ================= SIDEBAR =================
with st.sidebar:
    st.header("📂 Data Upload")
    bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
    req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

# ================= UTILITIES =================
def read_excel_safe(uploaded_file, sheet_name=None):
    """Robust Excel reader (handles dict + engine fallback)"""
    try:
        uploaded_file.seek(0)

        # Try openpyxl first (best for cloud)
        try:
            data = pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine="openpyxl")
        except:
            uploaded_file.seek(0)
            data = pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

        # Handle dict return (CRITICAL FIX)
        if isinstance(data, dict):
            if sheet_name and sheet_name in data:
                return data[sheet_name]
            return list(data.values())[0]

        return data

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


# ================= MAIN ENGINE =================
if bom_file and req_file:

    if st.sidebar.button("🚀 Run MRP Engine"):

        with st.spinner("Calculating Requirements..."):

            try:
                # ---------- LOAD FILES ----------
                bom = read_excel_safe(bom_file)
                req = read_excel_safe(req_file, "Requirement")
                stock = read_excel_safe(req_file, "Stock")

                # ---------- VALIDATION ----------
                if not isinstance(bom, pd.DataFrame):
                    st.error("❌ BOM not read correctly")
                    st.stop()

                if not isinstance(req, pd.DataFrame):
                    st.error("❌ 'Requirement' sheet missing")
                    st.stop()

                if not isinstance(stock, pd.DataFrame):
                    st.error("❌ 'Stock' sheet missing")
                    st.stop()

                # ---------- CLEAN COLUMN NAMES ----------
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]

                bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Special procurement": "SP"}, inplace=True)
                req.rename(columns={"Alt.": "Alt"}, inplace=True)

                # ---------- CHECK REQUIRED COLUMNS ----------
                for col in ["Component", "BOM Header", "Level"]:
                    if col not in bom.columns:
                        st.error(f"❌ Missing column in BOM: {col}")
                        st.stop()

                if "Component" not in stock.columns:
                    st.error("❌ 'Component' missing in Stock sheet")
                    st.stop()

                # ---------- NORMALIZATION ----------
                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # ---------- NUMERIC CONVERSION ----------
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0)

                if "Required Qty" in bom.columns:
                    bom["Quantity_Used"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)
                else:
                    bom["Quantity_Used"] = pd.to_numeric(bom.get("Quantity", 0), errors="coerce").fillna(0)

                stock = stock.rename(columns={"Quantity": "Stock"})
                stock["Stock"] = pd.to_numeric(
                    stock["Stock"].astype(str).str.replace(",", ""),
                    errors="coerce"
                ).fillna(0)

                # ---------- PARENT MAPPING ----------
                parents = []
                stack = {}

                for i in range(len(bom)):
                    lvl = bom.loc[i, "Level"]
                    parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1)
                    parents.append(parent)
                    stack[lvl] = bom.loc[i, "Component"]

                bom["Parent Component"] = parents

                # ---------- DEMAND MELT ----------
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]

                if not month_cols:
                    st.error("❌ No demand/month columns found")
                    st.stop()

                req_long = req.melt(
                    id_vars=id_cols,
                    value_vars=month_cols,
                    var_name="Month",
                    value_name="Demand"
                )

                req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
                req_long = req_long[req_long["Demand"] > 0]
                req_long.rename(columns={"BOM Header": "Parent Component"}, inplace=True)

                # ---------- MRP EXPLOSION ----------
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

                    # ---------- SHORTAGE LOGIC ----------
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

                # ---------- FINAL OUTPUT ----------
                if results:
                    all_data = pd.concat(results, ignore_index=True)

                    final = (
                        all_data.groupby(["Component", "Month"])["Gross_Req"]
                        .sum()
                        .unstack()
                        .fillna(0)
                        .reset_index()
                    )

                    final = final.merge(stock, on="Component", how="left").fillna(0)

                    st.success("✅ MRP Run Successful!")
                    st.dataframe(final, use_container_width=True)

                    # Download
                    output = BytesIO()
                    final.to_excel(output, index=False)

                    st.download_button(
                        "📥 Download Final Report",
                        output.getvalue(),
                        "MRP_Output.xlsx"
                    )

                else:
                    st.warning("⚠️ No matching data found")

            except Exception as e:
                st.error(f"❌ Critical error: {e}")

else:
    st.info("Please upload BOM and Requirement/Stock files to proceed.")
