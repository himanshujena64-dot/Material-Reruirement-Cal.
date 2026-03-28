import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="MRP Tool", layout="wide")
st.title("📊 MRP Shortage Dashboard")

# ---------------- FILE UPLOAD ----------------
with st.sidebar:
    bom_file = st.file_uploader("BOM File", type=["xlsx"])
    req_file = st.file_uploader("Requirement File", type=["xlsx"])

# ---------------- SAFE READ ----------------
def read_excel(file, sheet=None):
    try:
        file.seek(0)
        df = pd.read_excel(file, sheet_name=sheet, engine="openpyxl")
        if isinstance(df, dict):
            return list(df.values())[0]
        return df
    except Exception as e:
        st.error(f"Read error: {e}")
        return None

def normalize(x):
    if pd.isna(x): return ""
    return str(x).strip().replace(".0","").upper()

# ---------------- MAIN ----------------
if bom_file and req_file:

    if st.sidebar.button("Run"):

        with st.spinner("Processing..."):

            bom = read_excel(bom_file)
            req = read_excel(req_file, "Requirement")
            stock = read_excel(req_file, "Stock")

            if any(x is None for x in [bom, req, stock]):
                st.stop()

            # ---------- CLEAN ----------
            bom.columns = bom.columns.str.strip()
            req.columns = req.columns.str.strip()
            stock.columns = stock.columns.str.strip()

            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock["Component"].apply(normalize)
            req["BOM Header"] = req["BOM Header"].apply(normalize)

            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce").fillna(0)

            qty_col = "Required Qty" if "Required Qty" in bom.columns else "Quantity"
            bom["Qty"] = pd.to_numeric(bom[qty_col], errors="coerce").fillna(0)

            stock = stock.rename(columns={"Quantity": "Stock"})
            stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

            # ---------- DEMAND ----------
            req_long = req.melt(
                id_vars=["BOM Header","Alt"],
                var_name="Month",
                value_name="Demand"
            )

            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0]

            current = req_long.rename(columns={"BOM Header":"Parent Component"})

            # ---------- OPTIMIZED EXPLOSION ----------
            results = []
            max_lvl = int(bom["Level"].max())

            progress = st.progress(0)

            for lvl in range(1, max_lvl + 1):

                progress.progress(lvl / max_lvl)

                level_bom = bom[bom["Level"] == lvl][
                    ["Parent Component","Component","Alt","Qty","SP"]
                ]

                merged = current.merge(level_bom, on=["Parent Component","Alt"], how="inner")

                if merged.empty:
                    break  # 🔥 early stop (important)

                merged["Gross"] = merged["Demand"] * merged["Qty"]

                # attach stock only once
                merged = merged.merge(stock, on="Component", how="left")
                merged["Stock"] = merged["Stock"].fillna(0)

                # shortage
                merged["Shortage"] = merged.apply(
                    lambda x: x["Gross"] if str(x.get("SP"))=="50"
                    else max(0, x["Gross"] - x["Stock"]),
                    axis=1
                )

                # store minimal columns (🔥 reduce memory)
                results.append(merged[["Component","Month","Gross"]])

                # next loop input (🔥 keep small)
                current = merged[["Component","Month","Alt","Shortage"]].rename(
                    columns={"Component":"Parent Component","Shortage":"Demand"}
                )

                # 🔥 LIMIT growth (critical for cloud)
                if len(current) > 200000:
                    st.warning("⚠️ Data too large, stopping to avoid crash")
                    break

            # ---------- FINAL ----------
            if results:
                final = pd.concat(results)

                final = (
                    final.groupby(["Component","Month"])["Gross"]
                    .sum()
                    .unstack()
                    .fillna(0)
                    .reset_index()
                )

                final = final.merge(stock, on="Component", how="left").fillna(0)

                st.success("✅ Completed")
                st.dataframe(final, use_container_width=True)

                # download
                output = BytesIO()
                final.to_excel(output, index=False)

                st.download_button("Download", output.getvalue(), "MRP.xlsx")

            else:
                st.warning("No result")

else:
    st.info("Upload files")
