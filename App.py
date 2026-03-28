import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool - App-2", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM (Using your old logic) ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]:
        return True

    st.markdown("### 🔐 Production Planning Login")
    user = st.text_input("User ID", key="username")
    pas = st.text_input("Passcode", type="password", key="password")
    
    if st.button("Login"):
        if user in st.secrets["passwords"] and pas == st.secrets["passwords"][user]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("😕 Access Denied")
    return False

# --- 3. THE MRP ENGINE ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    """
    Recursive logic to handle Phantom pass-through and Regular stock consumption.
    """
    total_gross_for_target = 0
    parent_pn_str = str(parent_pn).strip()
    
    # Filter for children at Level + 1
    # Note: Using Alt 10 to avoid double counting
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
            
            # PHANTOM LOGIC: Pass 100% demand down
            if is_phantom:
                pass_down_qty = child_gross_req
            else:
                # REGULAR LOGIC: Subtract stock before exploding
                pass_down_qty = max(0, child_gross_req - child_stock)
            
            if pass_down_qty > 0:
                total_gross_for_target += calculate_recursive_demand(
                    child_pn, pass_down_qty, current_level + 1, target_pn, bom_df, stock_dict
                )
    return total_gross_for_target

# --- 4. THE APP ---
if check_password():
    st.title("⚙️ MRP Shortage Analysis (App-2)")
    
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

    # Helper function from your old code to handle various Excel engines
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
                # 1. LOAD DATA
                # BOM usually first sheet, Req/Stock in specific sheets
                bom = read_excel_safe(bom_file, sheet_name=0)
                
                xls_data = pd.ExcelFile(req_file)
                req_sheet = [s for s in xls_data.sheet_names if 'Req' in s][0]
                stock_sheet = [s for s in xls_data.sheet_names if 'Stock' in s][0]
                
                req = read_excel_safe(req_file, sheet_name=req_sheet)
                stock = read_excel_safe(req_file, sheet_name=stock_sheet)

                # 2. CLEANING (Robust stripping of column names)
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]
                
                # Normalize column names to match the logic
                bom.rename(columns={"Alt.": "Alt", "Special procurement": "SP", "SP type": "SP"}, inplace=True)
                
                # Identify Month Column (looking for Jan-26)
                month_cols = [c for c in req.columns if 'Jan-26' in c]
                if not month_cols:
                    st.error("Could not find 'Jan-26' column in Requirement sheet.")
                    st.stop()
                target_month = month_cols[0]

                # 3. PREP STOCK DICTIONARY
                # Using 'Quantity' or 'Avl. Stock' based on your file
                q_col = [c for c in stock.columns if 'Quantity' in c or 'Stock' in c][0]
                stock_dict = pd.Series(stock[q_col].values, 
                                       index=stock['Component'].astype(str).str.strip()).to_dict()

                # 4. EXECUTION
                total_gross = 0
                progress_text = "Exploding BOM Levels..."
                my_bar = st.progress(0, text=progress_text)
                
                rows = list(req.iterrows())
                for i, (_, row) in enumerate(rows):
                    h_pn = str(row['BOM Header']).strip()
                    h_demand = float(row[target_month]) if pd.notnull(row[target_month]) else 0
                    
                    if h_demand > 0:
                        total_gross += calculate_recursive_demand(
                            h_pn, h_demand, 0, target_pn_input, bom, stock_dict
                        )
                    my_bar.progress((i + 1) / len(rows))
                
                # 5. FINAL CALCULATION
                on_hand = float(stock_dict.get(target_pn_input, 0))
                shortage = max(0, total_gross - on_hand)

                # 6. RESULTS DISPLAY
                st.subheader(f"📊 Result for Component: {target_pn_input}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Gross Requirement", f"{total_gross:,.3f}")
                c2.metric("Available Stock", f"{on_hand:,.3f}")
                c3.metric("Net Shortage", f"{shortage:,.3f}", delta_color="inverse")

                if shortage > 0:
                    st.error(f"🚨 Action Required: Procure {shortage:,.3f} units.")
                else:
                    st.success("✅ Stock is sufficient to cover demand.")

            except Exception as e:
                st.error(f"Critical Error: {e}")
