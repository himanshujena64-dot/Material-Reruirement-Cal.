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
    except Exception as e:
        st.error(f"❌ Excel read error: {e}")
        return None


def clean_cols(df):
    df.columns = df.columns.str.strip()

    # 🔥 FIX column names
    rename_map = {}
    for c in df.columns:
        if c.lower() == "alt.":
            rename_map[c] = "Alt"
        if c.lower() == "sp type" or c.lower() == "special procurement":
            rename_map[c] = "SP"

    df = df.rename(columns=rename_map)
    return df


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
                st.stop()

            # ---------- CLEAN ----------
            bom = clean_cols(bom)
            req = clean_cols(req)
            stock = clean_cols(stock)

            st.write("🔍 BOM Columns:", bom.columns.tolist())
            st.write("🔍 REQ Columns:", req.columns.tolist())

            # ---------- ENSURE COLUMNS ----------
            if "Alt" not in bom.columns:
                bom["Alt"] = ""

            if "Alt" not in req.columns:
                req["Alt"] = ""

            # 🔥 SP optional
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

            # ---------- PARENT ----------
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

                merged = merged.merge(stock, on="Component", how="left")
                merged["Stock"] = merged["Stock"].fillna(0)

                # ---------- SHORTAGE ----------
                merged["Shortage"] = merged.apply(
                    lambda x: x["Gross"] if str(x["SP"]) == "50"
                    else max(0, x["Gross"] - x["Stock"]),
                    axis=1
                )

                results.append(merged[["Component", "Month", "Gross"]])

                current = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                    columns={"Component": "Parent Component", "Shortage": "Demand"}
                )

            # ---------- FINAL ----------
            if results:
                final = pd.concat(results)

                final = (
                    final.groupby(["Component", "Month"])["Gross"]
                    .sum()
                    .unstack()
                    .fillna(0)
                    .reset_index()
                )

                final = final.merge(stock, on="Component", how="left").fillna(0)

                st.success("✅ Done")
                st.dataframe(final)

                output = BytesIO()
                final.to_excel(output, index=False)

                st.download_button("Download", output.getvalue(), "MRP.xlsx")

            else:
                st.warning("No result")

        except Exception as e:
            st.error(f"❌ Critical error: {e}")

else:
    st.info("Upload files")
