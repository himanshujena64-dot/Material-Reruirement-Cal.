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

        # Fix dict issue
        if isinstance(df, dict):
            return list(df.values())[0]

        return df

    except Exception as e:
        st.error(f"❌ Excel read error ({sheet}): {e}")
        return None


def clean_cols(df):
    df.columns = df.columns.str.strip()
    return df


def normalize(x):
    if pd.isna(x): return ""
    return str(x).strip().replace(".0","").upper()


# ================= MAIN =================
if bom_file and req_file:

    if st.sidebar.button("🚀 Run MRP"):

        with st.spinner("Processing..."):

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

                # ---------- DEBUG (IMPORTANT) ----------
                st.write("🔍 Requirement Columns:", req.columns.tolist())

                # ---------- AUTO DETECT COLUMNS ----------
                req_cols = {c.lower(): c for c in req.columns}

                if "bom header" not in req_cols:
                    st.error("❌ 'BOM Header' not found in Requirement sheet")
                    st.stop()

                bom_col = req_cols["bom header"]
                alt_col = req_cols.get("alt", None)

                # ---------- NORMALIZE ----------
                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)
                req[bom_col] = req[bom_col].apply(normalize)

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
                id_cols = [bom_col]
                if alt_col:
                    id_cols.append(alt_col)

                month_cols = [c for c in req.columns if c not in id_cols]

                if not month_cols:
                    st.error("❌ No demand columns found")
                    st.stop()

                req_long = req.melt(
                    id_vars=id_cols,
                    value_vars=month_cols,
                    var_name="Month",
                    value_name="Demand"
                )

                req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
                req_long = req_long[req_long["Demand"] > 0]

                req_long.rename(columns={bom_col: "Parent Component"}, inplace=True)

                if alt_col:
                    req_long.rename(columns={alt_col: "Alt"}, inplace=True)
                else:
                    req_long["Alt"] = ""

                # ---------- EXPLOSION (OPTIMIZED) ----------
                current = req_long.copy()
                results = []
                max_lvl = int(bom["Level"].max())

                progress = st.progress(0)

                for lvl in range(1, max_lvl + 1):

                    progress.progress(lvl / max_lvl)

                    level_bom = bom[bom["Level"] == lvl][
                        ["Parent Component", "Component", "Alt", "Qty", "SP"]
                    ]

                    merged = current.merge(level_bom, on=["Parent Component", "Alt"], how="inner")

                    if merged.empty:
                        break

                    merged["Gross"] = merged["Demand"] * merged["Qty"]

                    merged = merged.merge(stock, on="Component", how="left")
                    merged["Stock"] = merged["Stock"].fillna(0)

                    # ---------- SHORTAGE LOGIC ----------
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross"]
                        if str(x.get("SP")) == "50"
                        else max(0, x["Gross"] - x["Stock"]),
                        axis=1
                    )

                    results.append(merged[["Component", "Month", "Gross"]])

                    current = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent Component", "Shortage": "Demand"}
                    )

                    # Prevent crash (cloud safe)
                    if len(current) > 200000:
                        st.warning("⚠️ Data too large — stopping early")
                        break

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

                    st.success("✅ MRP Completed")
                    st.dataframe(final, use_container_width=True)

                    output = BytesIO()
                    final.to_excel(output, index=False)

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
    st.info("Upload both BOM and Requirement files")
