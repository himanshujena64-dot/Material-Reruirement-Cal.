import streamlit as st
import pandas as pd
from io import BytesIO

# ================= PAGE =================
st.set_page_config(page_title="MRP Tool", layout="wide")
st.title("📊 MRP Shortage Dashboard")

# ================= SIDEBAR =================
with st.sidebar:
    bom_file = st.file_uploader("1. BOM File", type=["xlsx"])
    req_file = st.file_uploader("2. Requirement File", type=["xlsx"])

# ================= UTILITIES =================
def read_excel(file, sheet=None):
    try:
        file.seek(0)
        df = pd.read_excel(file, sheet_name=sheet, engine="openpyxl")

        if isinstance(df, dict):
            return list(df.values())[0]

        return df

    except Exception as e:
        st.error(f"❌ Excel read error: {e}")
        return None


def clean_cols(df):
    df.columns = df.columns.str.strip()

    rename_map = {}
    for c in df.columns:
        if c.lower() == "alt.":
            rename_map[c] = "Alt"
        if c.lower() in ["sp type", "special procurement"]:
            rename_map[c] = "SP"

    return df.rename(columns=rename_map)


def normalize(x):
    if pd.isna(x):
        return ""
    return str(x).strip().replace(".0", "").upper()


# ================= MAIN =================
if bom_file and req_file:

    if st.sidebar.button("🚀 Run MRP"):

        try:
            # ---------- LOAD ----------
            bom = read_excel(bom_file)
            req = read_excel(req_file, "Requirement")
            stock = read_excel(req_file, "Stock")

            if any(x is None for x in [bom, req, stock]):
                st.stop()

            # ---------- CLEAN ----------
            bom = clean_cols(bom)
            req = clean_cols(req)
            stock = clean_cols(stock)

            # ---------- ENSURE COLUMNS ----------
            if "Alt" not in bom.columns:
                bom["Alt"] = ""
            if "Alt" not in req.columns:
                req["Alt"] = ""
            if "SP" not in bom.columns:
                bom["SP"] = ""

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

            # ---------- PARENT MAPPING ----------
            parents = []
            stack = {}

            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1)
                parents.append(parent)
                stack[lvl] = bom.loc[i, "Component"]

            bom["Parent Component"] = parents

            # ---------- DEMAND ----------
            id_cols = ["BOM Header", "Alt"]
            month_cols = [c for c in req.columns if c not in id_cols]

            req_long = req.melt(
                id_vars=id_cols,
                value_vars=month_cols,
                var_name="Month",
                value_name="Demand"
            )

            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0]
            req_long.rename(columns={"BOM Header": "Parent Component"}, inplace=True)

            # ---------- EXPLOSION ----------
            current = req_long.copy()
            results = []
            max_lvl = int(bom["Level"].max())

            for lvl in range(1, max_lvl + 1):

                level_bom = bom[bom["Level"] == lvl][
                    ["Parent Component", "Component", "Alt", "Qty", "SP"]
                ]

                merged = current.merge(level_bom, on=["Parent Component", "Alt"], how="inner")

                if merged.empty:
                    break

                merged["Gross"] = merged["Demand"] * merged["Qty"]

                # 🔥 DO NOT APPLY STOCK HERE
                results.append(merged[["Component", "Month", "Gross"]])

                current = merged[["Component", "Month", "Alt", "Gross"]].rename(
                    columns={"Component": "Parent Component", "Gross": "Demand"}
                )

            # ---------- FINAL WITH CORRECT STOCK LOGIC ----------
            if results:
                result_df = pd.concat(results, ignore_index=True)

                # Pivot month-wise
                pivot = (
                    result_df.groupby(["Component", "Month"])["Gross"]
                    .sum()
                    .unstack()
                    .fillna(0)
                    .reset_index()
                )

                # Merge stock
                pivot = pivot.merge(stock, on="Component", how="left").fillna(0)

                # 🔥 CUMULATIVE STOCK CONSUMPTION
                month_cols = [c for c in pivot.columns if c not in ["Component", "Stock"]]

                for i in range(len(pivot)):
                    available = pivot.loc[i, "Stock"]

                    for m in month_cols:
                        demand = pivot.loc[i, m]
                        balance = available - demand

                        pivot.loc[i, m] = balance
                        available = balance

                st.success("✅ MRP Completed (Correct Shortage Logic)")
                st.dataframe(pivot, use_container_width=True)

                # Download
                output = BytesIO()
                pivot.to_excel(output, index=False)

                st.download_button(
                    "📥 Download Report",
                    output.getvalue(),
                    "MRP_Output.xlsx"
                )

            else:
                st.warning("⚠️ No result generated")

        except Exception as e:
            st.error(f"❌ Critical error: {e}")

else:
    st.info("Upload BOM and Requirement files to proceed.")
