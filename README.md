# SAP MRP Engine — Streamlit App

Full L1–L4 MRP explosion with phantom handling, Alt-aware joins, NET propagation, and dynamic month detection.

## Files
```
app.py            ← Streamlit application (all logic here)
requirements.txt  ← Python dependencies
README.md         ← This file
```

## Deploy to Streamlit Cloud (step by step)

1. **Push to GitHub**
   ```
   git init
   git add app.py requirements.txt README.md
   git commit -m "Initial MRP engine"
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

2. **Go to** [share.streamlit.io](https://share.streamlit.io)

3. Click **New app** → connect your GitHub repo → set:
   - Branch: `main`
   - Main file: `app.py`

4. Click **Deploy** — done. No extra config needed.

## How to use

| Step | Action |
|------|--------|
| 1 | Upload **BOM file** (.xlsx) in the sidebar |
| 2 | Upload **Req and Stock file** (.xlsx) in the sidebar |
| 3 | Upload **Production Orders** (.xlsx) — optional |
| 4 | Adjust verify component codes if needed |
| 5 | Click **▶ Run MRP** |
| 6 | Review verification tabs, then download `mrp_final.xlsx` |

## MRP rules applied
- **Phantom** (Sp. Proc = 50): qty = 1 (transparent), no stock netting, no order
- **4I / 4P**: treated as normal components (netted against stock)
- **NET propagation**: each level uses parent net shortage as demand basis
- **Stock depletion**: chronological, shared across all BOM headers
- **Join key**: BOM Header + Alt (Alt normalised to integer string to prevent dtype mismatch)
- **Cumulative shortage**: carry-forward via cumsum — if Jan has shortage and Feb has no demand, Jan shortage persists in Feb column
