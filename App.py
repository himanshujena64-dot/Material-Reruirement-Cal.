import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP App-2", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]:
        return True

    st.markdown("### 🔐 Production Planning Login")
    user = st.text_input("User ID", key="username")
    pas = st.text_input("Passcode", type="password", key="password")
    
    if st.button("Login"):
        # Accessing secrets for passwords
        if user in st.secrets["passwords"] and pas == st.secrets["passwords"][user]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("😕 Access Denied")
    return False

# --- 3. MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    total_gross_for_target = 0
    parent_pn_str = str(parent_pn).strip()
    
    # Filter for Alt 10 children
    children = bom_df[(bom_df['BOM Header'].astype(str).str.strip() == parent_pn_str) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt'].astype(str).str.contains("10"))]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component']).strip()
        req_qty = float(row['Required Qty'])
        is_phantom = str(row.get('SP', '')).strip() == "50"
        
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            # Phantom passes demand, Regular subtracts stock
            pass_down_qty = child_gross_req if is_phantom else max(0, child_gross_req - child_stock)
            
            if pass_down_qty > 0:
                total_gross_for_target += calculate_recursive_demand(
                    child_pn, pass_down_qty, current_level + 1, target_pn, bom_df, stock_dict
                )
    return total_gross_for_target

# --- 4. THE APP ---
if check_password():
    st.title("⚙️ MRP Shortage Analysis Dashboard (App-2)")
    
    with st.sidebar:
        st.success("✅ Access Granted")
        if st.button("Logout"):
            st.session_state["password_correct"] = False
            st.rerun()
        st.markdown("---")
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])
        target_pn_input = st.text_input("Target Component PN", value="0010300601DEL")

    def read_excel_safe(uploaded_file, sheet_name=None):
        uploaded_file.seek(0)
        for engine in ["openpyxl", "pyxlsb", "xlrd"]:
            try:
                return pd.read_excel(uploaded_file, sheet_name=sheet_name, engine=engine)
            except:
                continue
        return pd.read_excel(uploaded_file, sheet_name=sheet_name)

    if bom_file and req_file:
        if st.sidebar.button("🚀 Run MRP Engine"):
            try:
                # 1. Load Data
                bom = read_excel_safe(bom_file, sheet_name=0)
                
                # Auto-detect sheets
                xls_data = pd.ExcelFile(req_file)
                req_sheet = [s for s in xls_data.sheet_names if 'Req' in s][0]
                stock_sheet = [s for s in xls_data.sheet_names if 'Stock' in s][0]
                
                req = read_excel_safe(req_file, sheet_name=req_sheet)
                stock = read_excel_safe(req_file, sheet_name=stock_sheet)

                # 2. CLEANING & DATE FIXING
                # Convert all column names to string and strip spaces
                req.columns = [str(c).strip() for c in req.columns]
                bom.columns = [str(c).strip() for c in bom.columns]
                stock.columns = [str(c).strip() for c in stock.columns]
                
                # Normalize BOM columns
                bom.rename(columns={"Alt.": "Alt", "Special procurement": "SP", "SP type": "SP"}, inplace=True)

                # 3. LOCATE 'Jan-26' COLUMN (The most robust way)
                # This finds the column even if it's a date or has extra text
                month_cols = [c for c in req.columns if 'Jan-26' in c or '2026-01' in c]
                if not month_cols:
                    st.error(f"Could not find 'Jan-26' column. Available: {list(req.columns)}")
                    st.stop()
                target_month = month_cols[0]

                # 4. PREP STOCK
                stock_q_col = [c for c in stock.columns if 'Quantity' in c or 'Stock' in c][0]
                stock_dict = pd.Series(stock[stock_q_col].values, 
                                       index=stock['Component'].astype(str).str.strip()).to_dict()

                # 5. EXPLOSION
                total_gross = 0
                rows = list(req.iterrows())
                progress_bar = st.progress(0)
                
                for i, (_, row) in enumerate(rows):
                    h_pn = str(row['BOM Header']).strip()
                    # Ensure demand is treated as a number
                    h_demand = pd.to_numeric(row[target_month], errors='coerce') or 0
                    
                    if h_demand > 0:
                        total_gross += calculate_recursive_demand(
                            h_pn, h_demand, 0, target_pn_input, bom, stock_dict
                        )
                    progress_bar.progress((i + 1) / len(rows))
                
                # 6. FINAL RESULTS
                on_hand = float(stock_dict.get(target_pn_input, 0))
                shortage = max(0, total_gross - on_hand)

                st.divider()
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Gross Req", f"{total_gross:,.2f}")
                c2.metric("Stock on Hand", f"{on_hand:,.2f}")
                c3.metric("Net Shortage", f"{shortage:,.2f}")

                if shortage > 0:
                    st.error(f"Need to procure: {shortage:,.2f} units")
                else:
                    st.success("Sufficient stock available.")

            except Exception as e:
                st.error(f"Application Error: {e}")
