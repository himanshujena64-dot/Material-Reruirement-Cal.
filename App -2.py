import streamlit as st
import pandas as pd
from io import BytesIO
import time

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM (THE GATEKEEPER) ---
def check_password():
    """Returns True if the user had the correct password."""
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        # This looks for the [passwords] section you created in Streamlit Settings
        if (
            st.session_state["username"] in st.secrets["passwords"]
            and st.session_state["password"]
            == st.secrets["passwords"][st.session_state["username"]]
        ):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # Initial Login Screen
        st.markdown("### 🔐 Production Planning Login")
        st.text_input("User ID", on_change=password_entered, key="username")
        st.text_input("Passcode", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        # Login Failed Screen
        st.markdown("### 🔐 Production Planning Login")
        st.text_input("User ID", on_change=password_entered, key="username")
        st.text_input("Passcode", type="password", on_change=password_entered, key="password")
        st.error("😕 Access Denied: User ID or Passcode incorrect")
        return False
    else:
        # Login Success
        return True

# --- 3. THE APP (ONLY RUNS IF LOGGED IN) ---
if check_password():
    
    # Custom CSS for a professional look
    st.markdown("""
        <style>
        .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border: 1px solid #e1e4e8; }
        </style>
        """, unsafe_content_type=True)

    st.title("⚙️ MRP Shortage Analysis Dashboard")
    
    # Sidebar logout and info
    with st.sidebar:
        st.success("✅ Access Granted")
        if st.button("Logout"):
            st.session_state["password_correct"] = False
            st.rerun()
        st.markdown("---")
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

    # =========================
    # UTILITY: SAFE EXCEL READER
    # =========================
    def read_excel_safe(uploaded_file, sheet_name=None):
        uploaded_file.seek(0)
        engines = ["openpyxl", "pyxlsb", "xlrd"]
        for engine in engines:
            try:
                return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
            except:
                uploaded_file.seek(0)
                continue
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

    # =========================
    # MAIN APP LOGIC
    # =========================
    if bom_file and req_file:
        # Pre-run Preview
        col1, col2 = st.columns(2)
        with col1:
            with st.expander("🔍 Preview BOM Master"):
                st.write(read_excel_safe(bom_file, sheet_name=0).head(5))
        with col2:
            with st.expander("🔍 Preview Stock Sheet"):
                st.write(read_excel_safe(req_file, sheet_name="Stock").head(5))

        if st.sidebar.button("🚀 Run MRP Engine"):
            progress_bar = st.progress(0)
            status_text = st.empty()

            # 1. Load Files
            status_text.text("Loading files...")
            bom = read_excel_safe(bom_file, sheet_name=0)
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")
            progress_bar.progress(20)

            # 2. Clean and Normalize
            status_text.text("Cleaning data...")
            bom.columns = bom.columns.str.strip()
            req.columns = req.columns.str.strip()
            stock.columns = stock.columns.str.strip()
            bom.rename(columns={"Alt.": "Alt"}, inplace=True)
            req.rename(columns={"Alt.": "Alt"}, inplace=True)

            def normalize(x):
                if pd.isna(x): return ""
                x = str(x).strip()
                if x.endswith(".0"): x = x[:-2]
                return x.zfill(10)

            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock["Component"].apply(normalize)
            req["BOM Header"] = req["BOM Header"].apply(normalize)
            progress_bar.progress(40)

            # 3. Numeric & Parent Hierarchy
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
            if "Required Qty" in bom.columns:
                bom["Quantity"] = bom["Required Qty"]
            bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)
            
            stock = stock.rename(columns={"Quantity": "Stock"})
            stock["Stock"] = pd.to_numeric(stock["Stock"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

            parents = []
            stack = {}
            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                parent = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1, bom.loc[i, "BOM Header"])
                parents.append(parent)
                stack[lvl] = bom.loc[i, "Component"]
            bom["Parent Component"] = parents
            progress_bar.progress(60)

            # 4. MRP Engine Explosion
            status_text.text("Running MRP Explosion...")
            req_long = req.melt(id_vars=["BOM Header", "Alt"], var_name="Month", value_name="Demand")
            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Component"})

            current = req_long.copy()
            results = []
            max_level = int(bom["Level"].max())

            for lvl in range(1, max_level + 1):
                level_bom = bom[bom["Level"] == lvl]
                merged = current.merge(level_bom, left_on=["Component", "Alt"], right_on=["Parent Component", "Alt"], how="inner")
                if merged.empty: continue

                merged["Required"] = merged.apply(
                    lambda x: x["Demand"] if str(x["Special procurement"]) == "50" else x["Demand"] * x["Quantity"], axis=1
                )
                merged = merged.drop_duplicates(subset=["Parent Component", "Component_y", "Month", "Alt"])
                grouped = merged.groupby(["Component_y", "Month", "Alt"], as_index=False)["Required"].sum()
                grouped = grouped.rename(columns={"Component_y": "Component"})
                grouped = grouped.merge(stock, on="Component", how="left").fillna(0)
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                results.append(grouped)
                grouped["Demand"] = grouped["Shortage"]
                current = grouped.groupby(["Component", "Month", "Alt"], as_index=False)["Demand"].sum()
            
            progress_bar.progress(90)

            # 5. Result Display
            if results:
                all_req = pd.concat(results, ignore_index=True)
                demand = all_req.groupby(["Component", "Month"])["Required"].sum().reset_index()
                pivot = demand.pivot(index="Component", columns="Month", values="Required").fillna(0).reset_index()
                pivot = pivot.merge(stock, on="Component", how="left").fillna(0)

                extra = bom[["Component", "Component descriptio", "Procurement type", "Special procurement"]].drop_duplicates()
                pivot = pivot.merge(extra, on="Component", how="left")

                month_order = ["Jan-26", "Feb-26", "Mar-26", "Apr-26", "May-26"]
                month_cols = [m for m in month_order if m in pivot.columns]

                # Cumulative logic
                for i, m in enumerate(month_cols):
                    if i == 0: pivot[m] = pivot["Stock"] - pivot[m]
                    else: pivot[m] = pivot[month_cols[i-1]] - pivot[m]

                pivot = pivot.groupby("Component", as_index=False).agg({
                    "Stock": "first", **{m: "sum" for m in month_cols},
                    "Component descriptio": "first", "Procurement type": "first", "Special procurement": "first"
                })
                pivot = pivot[["Component", "Component descriptio", "Procurement type", "Special procurement", "Stock"] + month_cols]

                progress_bar.progress(100)
                status_text.text("Calculation Complete!")
                st.balloons()
                
                # Metric Cards
                m1, m2, m3 = st.columns(3)
                m1.metric("Items Processed", len(pivot))
                m2.metric("Procurement 'F'", len(pivot[pivot["Procurement type"] == "F"]))
                m3.metric("Critical Shortages", len(pivot[pivot[month_cols].min(axis=1) < 0]) if month_cols else 0)

                st.subheader("📋 MRP Shortage Report")
                
                # Search Box Feature
                search_query = st.text_input("🔍 Search by Component ID or Description")
                if search_query:
                    pivot = pivot[pivot["Component"].str.contains(search_query, case=False) | 
                                  pivot["Component descriptio"].str.contains(search_query, case=False)]

                st.dataframe(pivot.style.applymap(lambda x: 'color: red; font-weight: bold' if isinstance(x, (int, float)) and x < 0 else None), use_container_width=True)

                output = BytesIO()
                with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                    pivot.to_excel(writer, index=False)
                
                st.download_button("📥 Download Excel Report", output.getvalue(), "MRP_Final_Report.xlsx")
            else:
                st.error("No requirements were generated. Please check your data.")
    else:
        st.info("👋 Upload your files in the sidebar to start the MRP analysis.")