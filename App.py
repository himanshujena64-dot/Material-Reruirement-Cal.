import pandas as pd

# =========================
# 1. LOAD FILES
# =========================
bom = pd.read_excel("bom as on 1503.XLSX", dtype=str)
req = pd.read_excel("Req and Stock.xlsx", sheet_name="Requirement", dtype=str)
stock = pd.read_excel("Req and Stock.xlsx", sheet_name="Stock", dtype=str)

# =========================
# 2. CLEAN COLUMN NAMES
# =========================
bom.columns = bom.columns.str.strip()
req.columns = req.columns.str.strip()
stock.columns = stock.columns.str.strip()

bom.rename(columns={"Alt.": "Alt"}, inplace=True)
req.rename(columns={"Alt.": "Alt"}, inplace=True)

# =========================
# 3. NORMALIZE MATERIAL (KEEP LEADING ZERO)
# =========================
def normalize(x):
    if pd.isna(x):
        return ""
    x = str(x).strip()
    if x.endswith(".0"):
        x = x[:-2]
    return x.zfill(10)

bom["Component"] = bom["Component"].apply(normalize)
bom["BOM Header"] = bom["BOM Header"].apply(normalize)
stock["Component"] = stock["Component"].apply(normalize)
req["BOM Header"] = req["BOM Header"].apply(normalize)

# =========================
# 4. NUMERIC FIX
# =========================
bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")

if "Required Qty" in bom.columns:
    bom["Quantity"] = bom["Required Qty"]

bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)

# =========================
# 5. FIX STOCK (COMMA ISSUE)
# =========================
stock = stock.rename(columns={"Quantity": "Stock"})
stock["Stock"] = stock["Stock"].astype(str).str.replace(",", "")
stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

# =========================
# 6. CREATE PARENT COMPONENT
# =========================
parents = []
stack = {}

for i in range(len(bom)):
    lvl = bom.loc[i, "Level"]
    comp = bom.loc[i, "Component"]

    if lvl == 1:
        parent = bom.loc[i, "BOM Header"]
    else:
        parent = stack.get(lvl - 1, bom.loc[i, "BOM Header"])

    parents.append(parent)
    stack[lvl] = comp

bom["Parent Component"] = parents

# =========================
# 7. REQUIREMENT PREP
# =========================
req_long = req.melt(
    id_vars=["BOM Header", "Alt"],
    var_name="Month",
    value_name="Demand"
)

req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
req_long = req_long[req_long["Demand"] > 0]
req_long = req_long.rename(columns={"BOM Header": "Component"})

# =========================
# 8. INITIAL DEMAND
# =========================
current = req_long.copy()

results = []

max_level = int(bom["Level"].max())

# =========================
# 9. MRP ENGINE (FINAL FIXED)
# =========================
for lvl in range(1, max_level + 1):

    level_bom = bom[bom["Level"] == lvl]

    merged = current.merge(
        level_bom,
        left_on=["Component", "Alt"],
        right_on=["Parent Component", "Alt"],
        how="inner"
    )

    if merged.empty:
        continue

    # Phantom logic
    merged["Required"] = merged.apply(
        lambda x: x["Demand"] if str(x["Special procurement"]) == "50"
        else x["Demand"] * x["Quantity"],
        axis=1
    )

    # Remove duplicate paths
    merged = merged.drop_duplicates(
        subset=["Parent Component", "Component_y", "Month", "Alt"]
    )

    # Aggregate
    grouped = merged.groupby(
        ["Component_y", "Month", "Alt"], as_index=False
    )["Required"].sum()

    grouped = grouped.rename(columns={"Component_y": "Component"})

    # Merge stock
    grouped = grouped.merge(stock, on="Component", how="left")
    grouped["Stock"] = grouped["Stock"].fillna(0)

    # Shortage
    grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

    results.append(grouped)

    # 🔥 CRITICAL FIX: CONSOLIDATE DEMAND BEFORE NEXT LEVEL
    grouped["Demand"] = grouped["Shortage"]

    current = grouped.groupby(
        ["Component", "Month", "Alt"], as_index=False
    )["Demand"].sum()

# =========================
# 10. COMBINE
# =========================
all_req = pd.concat(results, ignore_index=True)

demand = all_req.groupby(["Component", "Month"])["Required"].sum().reset_index()

# =========================
# 11. PIVOT
# =========================
pivot = demand.pivot(index="Component", columns="Month", values="Required").fillna(0)
pivot = pivot.reset_index()

# =========================
# 12. MERGE STOCK
# =========================
pivot = pivot.merge(stock, on="Component", how="left")
pivot["Stock"] = pivot["Stock"].fillna(0)

# =========================
# 13. ADD MASTER DATA
# =========================
extra = bom[[
    "Component",
    "Component descriptio",
    "Procurement type",
    "Special procurement"
]].drop_duplicates()

pivot = pivot.merge(extra, on="Component", how="left")

# =========================
# 14. MONTH ORDER
# =========================
month_order = ["Jan-26", "Feb-26", "Mar-26", "Apr-26", "May-26"]
month_cols = [m for m in month_order if m in pivot.columns]

# =========================
# 15. CUMULATIVE
# =========================
for i, m in enumerate(month_cols):
    if i == 0:
        pivot[m] = pivot["Stock"] - pivot[m]
    else:
        pivot[m] = pivot[month_cols[i-1]] - pivot[m]

# =========================
# 16. FINAL OUTPUT
# =========================
pivot = pivot.groupby("Component", as_index=False).agg({
    "Stock": "first",
    **{m: "sum" for m in month_cols},
    "Component descriptio": "first",
    "Procurement type": "first",
    "Special procurement": "first"
})

pivot = pivot[
    ["Component", "Component descriptio", "Procurement type", "Special procurement", "Stock"]
    + month_cols
]

print(pivot.head())

# =========================
# 17. EXPORT
# =========================
pivot.to_excel("MRP_Final_Output.xlsx", index=False)

from google.colab import files
files.download("MRP_Final_Output.xlsx")