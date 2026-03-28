import streamlit as st
import pandas as pd

# --- CORE MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    """
    Recursively explodes the BOM.
    - If a component is a Phantom (SP 50), it passes demand 100%.
    - If it's a regular part, it subtracts stock before passing demand to children.
    """
    total_gross_for_target = 0
    
    # Filter children at the next level for this parent (Filtered by Alt 10)
    children = bom_df[(bom_df['BOM Header'] == parent_pn) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt.'] == 10)]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component'])
        req_qty = float(row['Required Qty'])
        spec_proc = str(row.get('Special procurement', ''))
        
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            
            if spec_proc == "50":
                pass_down_qty = child_gross_req # Phantom: Ignore stock
            else:
                pass_down_qty = max(0, child_gross_req - child_stock) # Regular: Subtract stock
            
            if pass_down_qty > 0:
                total_gross_for_target += calculate_recursive_demand(
                    child_pn, pass_down_qty, current_level + 1, target_pn, bom_df, stock_dict
                )
                
    return total_gross_for_target

# --- STREAMLIT UI ---
st.set_page_config(page_title="MRP Logic Corrector", layout="wide")
st.title("📦 App-2: MRP Requirement Calculator")

st.sidebar.header("Upload Data Files")
bom_file = st.sidebar.file_uploader("Upload BOM File (Excel/CSV)", type=['xlsx', 'csv'])
demand_file = st.sidebar.file_uploader("Upload Demand File (Excel/CSV)", type=['xlsx', 'csv'])
stock_file = st.sidebar.file_uploader("Upload Stock File (Excel/CSV)", type=['xlsx', 'csv'])

target_part = st.sidebar.text_input("Target Component PN", value="0010300601DEL")

if bom_file and demand_file and stock_file:
    # Load Data
    df_bom = pd.read_excel(bom_file) if bom_file.name.endswith('xlsx') else pd.read_csv(bom_file)
    df_demand = pd.read_excel(demand_file) if demand_file.name.endswith('xlsx') else pd.read_csv(demand_file)
    df_stock = pd.read_excel(stock_file) if stock_file.name.endswith('xlsx') else pd.read_csv(stock_file)

    # Convert Stock to a Dictionary for fast lookup
    # Assuming Stock file has columns: 'Component' and 'Avl. Stock'
    stock_dict = pd.Series(df_stock['Avl. Stock'].values, index=df_stock['Component'].astype(str)).to_dict()

    if st.button("Run MRP Calculation"):
        total_gross_req = 0
        
        # Process each Header in the Demand file
        # Assuming Demand file has 'BOM Header' and 'Demand' columns
        for _, row in df_demand.iterrows():
            header = str(row['BOM Header'])
            qty = float(row['Demand'])
            
            total_gross_req += calculate_recursive_demand(
                header, qty, 0, target_part, df_bom, stock_dict
            )
        
        # Final Calculation
        available_stock = float(stock_dict.get(target_part, 0))
        net_shortage = max(0, total_gross_req - available_stock)
        
        # Results Display
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Gross Requirement", f"{total_gross_req:,.3f}")
        c2.metric("Stock on Hand", f"{available_stock:,.3f}")
        c3.metric("Net Shortage", f"{net_shortage:,.3f}")

        if net_shortage > 0:
            st.error(f"⚠️ Shortage detected for {target_part}")
        else:
            st.success(f"✅ Sufficient stock for {target_part}")

else:
    st.info("Please upload all three files in the sidebar to begin.")