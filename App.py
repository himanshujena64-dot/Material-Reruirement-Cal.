import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="MRP Debug Tool", layout="wide")

# --- LOGIN ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]:
        return True

    st.markdown("### 🔐 Login")
    user = st.text_input("User")
    pas = st.text_input("Password", type="password")
    
    if st.button("Login"):
        if user in st.secrets["passwords"] and pas == st.secrets["passwords"][user]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("Wrong credentials")
    return False

# --- APP ---
if check_password():
    st.title("⚙️ MRP Shortage + Debug Tool")

    with st.sidebar:
        bom_file = st.file_uploader("BOM File")
        req_file = st.file_uploader("Req + Stock File")
        debug_mode = st.checkbox("Enable Debug Mode 🔍")

    def read_excel_safe(file, sheet=None):
        file.seek(0)
        for engine in ["openpyxl", "pyxlsb", "xlrd"]:
            try:
                return pd.read_excel(file, sheet_name=sheet, dtype=str, engine=engine)
            except:
                continue
        return pd.read_excel(file, sheet_name=sheet, dtype=str)

    if bom_file and req_file:
        if st.button("Run MRP"):

            bom = read_excel_safe(bom_file, 0)
            req = read_excel_safe(req_file, "Requirement")
            stock = read_excel_safe(req_file, "Stock")

            # --- CLEAN ---
            bom.columns = bom.columns.str.strip()
            req.columns = req.columns.str.strip()
            stock.columns = stock.columns.str.strip()

            bom.rename(columns={"Alt.": "Alt", "Special procurement": "SP"}, inplace=True)

            def norm(x):
                if pd.isna(x): return ""
                x = str(x).strip()
                if x.endswith(".0"): x = x[:-2]
                return x.zfill(10)

            bom["Component"] = bom["Component"].apply(norm)
            bom["BOM Header"] = bom["BOM Header"].apply(norm)
            req["BOM Header"] = req["BOM Header"].apply(norm)
            stock["Component"] = stock["Component"].apply(norm)

            bom["Required Qty"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
            stock["Stock"] = pd.to_numeric(stock["Quantity"], errors="coerce").fillna(0)

            stock_dict = stock.set_index("Component")["Stock"].to_dict()

            # Phantom
            bom["Is_Phantom"] = bom["SP"].astype(str) == "50"

            # Parent build
            parents, stack = [], {}
            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                comp = bom.loc[i, "Component"]
                p = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1)
                parents.append(p)
                stack[lvl] = comp
            bom["Parent Component"] = parents

            # Demand reshape
            req_long = req.melt(id_vars=["BOM Header", "Alt"], var_name="Month", value_name="Demand")
            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Component"})

            current = req_long.copy()
            results = []
            debug_rows = []

            for lvl in range(1, int(bom["Level"].max()) + 1):

                level_bom = bom[bom["Level"] == lvl]

                merged = current.merge(
                    level_bom,
                    left_on=["Component", "Alt"],
                    right_on=["Parent Component", "Alt"],
                    how="inner"
                )

                if merged.empty:
                    continue

                # Calculation
                merged["Gross_Req"] = merged.apply(
                    lambda r: r["Demand"] if r["Is_Phantom"] else r["Demand"] * r["Required Qty"],
                    axis=1
                )

                # DEBUG TRACE
                if debug_mode:
                    temp = merged.copy()
                    temp["Trace"] = (
                        temp["Component_x"] + " → " + temp["Component_y"] +
                        " | Demand=" + temp["Demand"].astype(str) +
                        " | Qty=" + temp["Required Qty"].astype(str) +
                        " | Gross=" + temp["Gross_Req"].astype(str)
                    )
                    debug_rows.append(temp[["Trace", "Month"]])

                grouped = merged.groupby(["Component_y", "Month", "Alt"], as_index=False)["Gross_Req"].sum()
                grouped.rename(columns={"Component_y": "Component", "Gross_Req": "Required"}, inplace=True)

                grouped["Stock"] = grouped["Component"].map(stock_dict).fillna(0)
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                # Save non-phantom
                phantom_map = bom[["Component", "Is_Phantom"]].drop_duplicates()
                grouped = grouped.merge(phantom_map, on="Component", how="left")

                results.append(grouped[~grouped["Is_Phantom"]])

                current = grouped[["Component", "Month", "Alt", "Shortage"]].rename(columns={"Shortage": "Demand"})

            # FINAL OUTPUT
            all_data = pd.concat(results, ignore_index=True)

            pivot = all_data.groupby(["Component", "Month"])["Shortage"].sum().unstack().fillna(0).reset_index()

            st.subheader("📊 Final Shortage")
            st.dataframe(pivot, use_container_width=True)

            # DEBUG OUTPUT
            if debug_mode:
                st.subheader("🔍 DEBUG TRACE")
                debug_df = pd.concat(debug_rows, ignore_index=True)
                st.dataframe(debug_df)

            # FILTER RM
            st.subheader("🎯 Check Specific Component")
            part = st.text_input("Enter Component (e.g. 0010300601DEL)")
            if part:
                st.dataframe(all_data[all_data["Component"] == part])

            # DOWNLOAD
            output = BytesIO()
            pivot.to_excel(output, index=False)
            st.download_button("Download", output.getvalue(), "MRP.xlsx")