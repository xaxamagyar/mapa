import streamlit as st
import pandas as pd
import requests
import io
import json
import os
import time
import base64
from datetime import datetime

st.set_page_config(page_title="Zúčtovací středisko", layout="wide", page_icon="🧾")

# ==============================================================================
# --- DATABÁZOVÁ VRSTVA S GITHUB SYNCHRONIZACÍ (Zabrání ztrátě dat) ---
# ==============================================================================
ORDERS_DB_FILE = "finance_orders_db.json"
INVOICES_DB_FILE = "finance_invoices_db.json"

try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
    GITHUB_REPO = st.secrets["GITHUB_REPO"]  
except:
    GITHUB_TOKEN = ""
    GITHUB_REPO = ""

def get_github_headers(): 
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_db(file_path):
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        resp = requests.get(url, headers=get_github_headers())
        if resp.status_code == 200:
            try: return json.loads(base64.b64decode(resp.json()['content']).decode('utf-8'))
            except: return {}
        return {}
    else:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                try: return json.load(f)
                except: return {}
        return {}

def save_db(file_path, data_obj):
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        headers = get_github_headers()
        resp = requests.get(url, headers=headers)
        sha = resp.json().get('sha') if resp.status_code == 200 else None
        
        # Převedeme slovník na JSON text a následně na base64 (což vyžaduje GitHub)
        content_b64 = base64.b64encode(json.dumps(data_obj, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
        
        commit_msg = f"Update financí {datetime.now().strftime('%H:%M:%S')}"
        payload = {"message": commit_msg, "content": content_b64}
        if sha: payload["sha"] = sha
        
        # Odeslání aktualizovaného souboru přímo na GitHub
        requests.put(url, headers=headers, json=payload)
    else:
        with open(file_path, "w", encoding="utf-8") as f: 
            json.dump(data_obj, f, ensure_ascii=False, indent=2)

# ==============================================================================
# --- CHYTRÉ STAHOVÁNÍ DAT Z E-SHOPŮ ---
# ==============================================================================
SHOP_MAXI_URL = "https://www.max-i.cz/export/orders.xls?patternId=141&partnerId=13&hash=bb270e1c6e0e097e42ab367330f8f0dbe0a42762719ce23873e8c8fc57a8c4ba"
SHOP_VOMAKS_URL = "https://www.vomaks.cz/export/orders.xls?patternId=116&partnerId=8&hash=d2d000ca1be65f892cb7b0d6cfad9d44c37fddf30ee8566727cde352cd36b05f"

def fetch_finance_data(url, prefix, eshop_name):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try: 
        response = requests.get(url, headers=headers, timeout=90)
        response.raise_for_status()
        content = response.content
    except Exception as e: 
        return {}
        
    df = None
    for sep, enc in [(';', 'utf-8'), (';', 'cp1250'), (',', 'utf-8')]:
        if df is None:
            try: 
                df_temp = pd.read_csv(io.BytesIO(content), sep=sep, dtype=str, encoding=enc)
                if len(df_temp.columns) > 2: df = df_temp
            except: pass
            
    if df is None:
        try: df = pd.read_excel(io.BytesIO(content), dtype=str)
        except: pass
            
    if df is None or df.empty: return {}
    df = df.loc[:, ~df.columns.duplicated()]
    
    prod_col, amount_col, status_col, price_col, itemcode_col, variant_col = None, None, None, None, None, None
    unit_price_novat_col, unit_price_vat_col = None, None
    
    for col in ['itemName', 'Název položky', 'productName']:
        if col in df.columns: prod_col = col; break
    for col in ['itemAmount', 'amount', 'Množství']:
        if col in df.columns: amount_col = col; break
    for col in ['statusName', 'Stav', 'Stav objednávky', 'orderStatus']:
        if col in df.columns: status_col = col; break
    for col in ['priceToPay', 'geisDeliveryPriceToPay', 'Celková cena s DPH', 'priceWithVat']:
        if col in df.columns: price_col = col; break
    for col in ['itemCode', 'Kód položky', 'Kód', 'code']:
        if col in df.columns: itemcode_col = col; break
    for col in df.columns:
        if str(col).strip().lower() in ['itemvariantname', 'variantname', 'varianta']:
            variant_col = col; break
    for col in ['itemUnitPriceWithoutVat', 'Jednotková cena bez DPH']:
        if col in df.columns: unit_price_novat_col = col; break
    for col in ['itemUnitPriceWithVat', 'Jednotková cena s DPH', 'itemPriceWithVat']:
        if col in df.columns: unit_price_vat_col = col; break

    skip_keywords = ['doprava', 'platba', 'dobírka', 'ppl', 'dpd', 'zásilkovna', 'gls', 'česká pošta', 'osobní odběr', 'kurýr', 'balíkovna', 'dobirka']
    orders_dict = {}
    
    if 'code' in df.columns and prod_col:
        for code, group in df.groupby('code'):
            order_id = prefix + str(code)
            products = []
            shipping_revenue_czk = 0.0 
            
            o_status = str(group[status_col].iloc[0]) if status_col and pd.notna(group[status_col].iloc[0]) else "Neznámý"
            o_price = str(group[price_col].iloc[0]) if price_col and pd.notna(group[price_col].iloc[0]) else "0"
            
            try: 
                raw_sp = float(str(o_price).replace(' ', '').replace('\xa0', '').replace('Kč', '').replace(',', '.'))
                sp_clean = raw_sp / 1.21
            except: sp_clean = 0.0
            
            # --- DEFINITIVNÍ LIKVIDÁTOR SHOPTET DUCHŮ ---
            seen_final_names = set() 
            
            for _, r in group.iterrows():
                p_name_raw = str(r[prod_col]).strip()
                if not p_name_raw or p_name_raw.lower() in ['nan', 'none']:
                    continue
                    
                is_shipping_or_billing = False
                if itemcode_col and pd.notna(r[itemcode_col]):
                    icode = str(r[itemcode_col]).lower()
                    if 'shipping' in icode or 'billing' in icode: 
                        is_shipping_or_billing = True
                        
                if not is_shipping_or_billing:
                    if any(kw in p_name_raw.lower() for kw in skip_keywords):
                        is_shipping_or_billing = True
                        
                unit_sell_price = 0.0
                if unit_price_novat_col and pd.notna(r[unit_price_novat_col]):
                    try: unit_sell_price = float(str(r[unit_price_novat_col]).replace(' ', '').replace('\xa0', '').replace('Kč', '').replace(',', '.'))
                    except: pass
                elif unit_price_vat_col and pd.notna(r[unit_price_vat_col]):
                    try: unit_sell_price = float(str(r[unit_price_vat_col]).replace(' ', '').replace('\xa0', '').replace('Kč', '').replace(',', '.')) / 1.21
                    except: pass
                    
                try: amt = int(float(r[amount_col])) if amount_col and pd.notna(r[amount_col]) else 1
                except: amt = 1
                
                if is_shipping_or_billing:
                    shipping_revenue_czk += (unit_sell_price * amt)
                    continue
                    
                v_name = ""
                if variant_col and pd.notna(r[variant_col]):
                    v_str = str(r[variant_col]).strip()
                    if v_str and v_str.lower() not in ['nan', 'none', '']:
                        v_name = v_str
                
                p_name_final = p_name_raw
                if v_name: 
                    p_name_final = f"{p_name_final} [Varianta: {v_name}]"
                    
                # Pokud jsme tento produkt včetně varianty už načetli, ten další řádek okamžitě zahodíme (nebudeme ani sčítat kusy)
                if p_name_final in seen_final_names:
                    continue 
                
                seen_final_names.add(p_name_final)
                products.append({"name": p_name_final, "qty": amt, "unit_sell_price_czk": unit_sell_price})
                        
            orders_dict[order_id] = {
                "eshop": eshop_name,
                "status": o_status,
                "sell_price_czk": sp_clean,
                "shipping_revenue_czk": shipping_revenue_czk, 
                "products": products,
                "finance": [] 
            }
    return orders_dict

def sync_shoptet():
    dict_maxi = fetch_finance_data(SHOP_MAXI_URL, "MAX-", "Max-i.cz")
    dict_vomaks = fetch_finance_data(SHOP_VOMAKS_URL, "VOM-", "Vomaks.cz")
    all_fetched = {**dict_maxi, **dict_vomaks}
    
    db = load_db(ORDERS_DB_FILE)
    added, updated = 0, 0
    
    for oid, data in all_fetched.items():
        if oid not in db:
            db[oid] = data
            added += 1
        else:
            db[oid]['status'] = data['status']
            db[oid]['sell_price_czk'] = data['sell_price_czk']
            db[oid]['shipping_revenue_czk'] = data.get('shipping_revenue_czk', 0.0) # --- NOVINKA: Uloženo ---
            db[oid]['products'] = data['products']
            if 'finance' not in db[oid]: db[oid]['finance'] = []
            updated += 1
            
    save_db(ORDERS_DB_FILE, db)
    return added, updated

# --- HLAVNÍ APLIKACE ---
col_title, col_btn = st.columns([4, 1])
col_title.title("🧾 Účetní a Zúčtovací středisko")
if col_btn.button("🔄 Stáhnout nové obj. ze Shoptetu", type="primary", use_container_width=True):
    with st.spinner("Inkrementální synchronizace databáze..."):
        a, u = sync_shoptet()
    st.success(f"Nové objednávky: {a} | Aktualizované: {u}")
    time.sleep(1.5)
    st.rerun()

st.markdown("---")

tab_invoice, tab_history, tab_profit = st.tabs(["✍️ Zpracování přijaté faktury", "📦 Historie faktur a Slepý sklad", "💰 Ziskovost objednávek"])

db_orders = load_db(ORDERS_DB_FILE)
db_invoices = load_db(INVOICES_DB_FILE)

# =======================================================
# TAB 1: ZPRACOVÁNÍ FAKTURY
# =======================================================
with tab_invoice:
    if 'active_invoice' not in st.session_state:
        st.markdown("### 1. Založení nové faktury")
        st.info("Přepište údaje z přijaté faktury. Následně se Vám otevře prostor pro přiřazení objednávek.")
        
        # --- OPRAVA: Měna je vytažená MIMO formulář, aby okamžitě reagovala ---
        col_curr_space, col_curr = st.columns([4, 1])
        inv_curr = col_curr.selectbox("Měna faktury:", ["CZK", "EUR", "PLN"])
        
        with st.form("new_invoice_form"):
            c1, c2 = st.columns(2)
            inv_id = c1.text_input("🧾 Číslo faktury:")
            inv_supp = c2.text_input("🏭 Dodavatel:")
            
            c4, c5, c6 = st.columns([1, 2, 2])
            # min_value jsem snížil na 0.01 pro jistotu, kdyby byl kurz někdy velmi nízký
            inv_rate = c4.number_input("Kurz do CZK:", min_value=0.01, value=25.0 if inv_curr=="EUR" else (5.8 if inv_curr=="PLN" else 1.0), step=0.1, disabled=(inv_curr=="CZK"))
            inv_goods = c5.number_input(f"📦 Celková cena zboží (v {inv_curr}):", min_value=0.0, step=100.0)
            inv_transp = c6.number_input(f"🚚 Celková doprava/vedlejší náklady (v {inv_curr}):", min_value=0.0, step=10.0)
            
            is_incl_vat = st.checkbox("⚠️ Zadané částky výše a nákupní ceny zboží JSOU VČETNĚ DPH (Systém je očistí o 21 %)", value=True if inv_curr == "CZK" else False)
            
            if st.form_submit_button("Založit fakturu a začít rozúčtovávat", type="primary"):
                if not inv_id or not inv_supp or inv_goods <= 0:
                    st.error("Číslo, dodavatel a cena zboží musí být vyplněny!")
                elif inv_id in db_invoices:
                    st.error("Faktura s tímto číslem již v systému existuje!")
                else:
                    st.session_state['active_invoice'] = {
                        "inv_id": inv_id, "supplier": inv_supp, "currency": inv_curr, "rate": inv_rate,
                        "total_goods_fc": inv_goods, "total_transp_fc": inv_transp,
                        "is_incl_vat": is_incl_vat, "items": []
                    }
                    st.rerun()
    else:
        inv = st.session_state['active_invoice']
        
        # Očištění o DPH pro interní tachometry
        actual_total_goods = inv['total_goods_fc'] / 1.21 if inv['is_incl_vat'] else inv['total_goods_fc']
        actual_total_transp = inv['total_transp_fc'] / 1.21 if inv['is_incl_vat'] else inv['total_transp_fc']
        
        assigned_goods = sum(item['price_fc_clean'] * item['qty'] for item in inv['items'])
        remaining_goods = actual_total_goods - assigned_goods
        
        st.markdown(f"### 🧾 Zpracování faktury: {inv['inv_id']} ({inv['supplier']})")
        
        # TACHOMETR
        c_tot, c_ass, c_rem = st.columns(3)
        c_tot.metric("Hodnota zboží (Bez DPH)", f"{int(actual_total_goods)} {inv['currency']}")
        c_ass.metric("Přiřazeno k objednávkám", f"{int(assigned_goods)} {inv['currency']}")
        c_rem.metric("Zbývá rozúčtovat (Na Sklad)", f"{int(remaining_goods)} {inv['currency']}", delta=f"{int(remaining_goods)} {inv['currency']}", delta_color="off" if remaining_goods == 0 else "inverse")
        
        st.markdown("---")
        
        # ROZÚČTOVÁNÍ
        c_search, c_work = st.columns([1, 2])
        with c_search:
            st.markdown("#### 🔍 Hledat objednávku")
            search_list = [f"{oid} | {d['status']} | {d.get('eshop','')}" for oid, d in db_orders.items()]
            
            # --- POJISTKA PRO BEZPEČNÝ RESET ROLETKY ---
            if 'sel_counter' not in st.session_state: 
                st.session_state['sel_counter'] = 0
                
            # Roletka má nyní klíč s počítadlem
            selected_order_str = st.selectbox("Najděte objednávku k přiřazení:", ["-- Vyberte --"] + list(reversed(search_list)), key=f"order_selector_box_{st.session_state['sel_counter']}")
            
            if selected_order_str != "-- Vyberte --":
                sel_oid = selected_order_str.split(" | ")[0]
                sel_data = db_orders[sel_oid]
                
                with c_work:
                    st.markdown(f"#### 🛒 Zboží v objednávce: {sel_oid}")
                    if not sel_data['products']:
                        st.warning("Shoptet k této objednávce neposlal žádné položky.")
                    else:
                        # --- POJISTKA PRO BEZPEČNÝ RESET WIDGETŮ ---
                        if 'item_counter' not in st.session_state: 
                            st.session_state['item_counter'] = 0
                            
                        # OPRAVA: Použijeme seznam místo slovníku, aby se nám nepřepsaly produkty se stejným jménem
                        selected_prods_for_inv = []
                        
                        # OPRAVA: enumerate(sel_data['products']) přidá unikátní číslo (idx) ke každému řádku
                        for idx, p in enumerate(sel_data['products']):
                            # Do klíče přidáme `idx` pro absolutní unikátnost
                            chk_key = f"chk_add_{sel_oid}_{idx}_{st.session_state['item_counter']}"
                            
                            c_chk, c_n, c_q, c_p = st.columns([0.8, 2.2, 1, 1])
                            is_checked = c_chk.checkbox("Na faktuře", key=chk_key)
                            
                            c_n.markdown(f"**{p['name']}**<br><span style='font-size:0.85em; color:#7f8c8d;'>Zákazník koupil: {p['qty']} ks</span>", unsafe_allow_html=True)
                            
                            in_qty = c_q.number_input("Ks z faktury:", min_value=1, max_value=p['qty'], value=p['qty'], step=1, disabled=not is_checked, key=f"q_{sel_oid}_{idx}_{st.session_state['item_counter']}")
                            in_price = c_p.number_input(f"Cena/ks ({inv['currency']}):", min_value=0.0, step=10.0, disabled=not is_checked, key=f"p_{sel_oid}_{idx}_{st.session_state['item_counter']}")
                            
                            if is_checked:
                                selected_prods_for_inv.append({
                                    "name": p['name'], 
                                    "qty": in_qty, 
                                    "price": in_price
                                })
                                
                        st.write("")
                        if st.button("💾 Přidat zaškrtnuté položky na fakturu", type="primary", use_container_width=True):
                            if not selected_prods_for_inv:
                                st.error("Musíte zaškrtnout alespoň jeden produkt!")
                            else:
                                for p_data in selected_prods_for_inv:
                                    p_clean = p_data['price'] / 1.21 if inv['is_incl_vat'] else p_data['price']
                                    st.session_state['active_invoice']['items'].append({
                                        "oid": sel_oid,
                                        "product_name": p_data['name'],
                                        "qty": p_data['qty'],
                                        "price_fc_input": p_data['price'],
                                        "price_fc_clean": p_clean
                                    })
                                    
                                # --- RESET ROLETKY ---
                                # Zvednutím počítadla se stará roletka smaže a načte se nová (na výchozí '-- Vyberte --')
                                st.session_state['sel_counter'] += 1
                                
                                # Reset zaškrtávátek
                                st.session_state['item_counter'] += 1
                                st.rerun()

        st.markdown("---")
        st.markdown("#### 📋 Položky aktuálně zařazené na faktuře")
        if not inv['items']:
            st.info("Zatím jste na tuto fakturu nepřidali žádné položky z objednávek.")
        else:
            for idx, it in enumerate(inv['items']):
                c_del, c_inf = st.columns([0.5, 5])
                if c_del.button("❌", key=f"del_{idx}"):
                    st.session_state['active_invoice']['items'].pop(idx)
                    st.rerun()
                c_inf.markdown(f"**{it['oid']}** - {it['qty']}x {it['product_name']} | Cena: {it['price_fc_clean']:.2f} {inv['currency']} (Bez DPH)")
                
        st.markdown("<br>", unsafe_allow_html=True)
        c_cancel, c_save = st.columns(2)
        if c_cancel.button("🗑️ Zrušit rozpracovanou fakturu", use_container_width=True):
            del st.session_state['active_invoice']
            st.rerun()
            
        if c_save.button("💾 ULOŽIT FAKTURU (Zbývající hodnota půjde do Skladu)", type="primary", use_container_width=True):
            if remaining_goods < -0.1:
                st.error("⚠️ Rozdělili jste na objednávky více peněz, než je celková hodnota faktury! Zkontrolujte zadané ceny.")
            else:
                # 1. Výpočet dopravy na základě poměru
                final_inv = {
                    "supplier": inv['supplier'],
                    "currency": inv['currency'],
                    "rate": inv['rate'],
                    "total_goods_czk": actual_total_goods * inv['rate'],
                    "total_transp_czk": actual_total_transp * inv['rate'],
                    "assigned_goods_czk": assigned_goods * inv['rate'],
                    "stock_remainder_czk": remaining_goods * inv['rate'],
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    # --- NOVINKA: DATA PRO ZPĚTNÉ OTEVŘENÍ ---
                    "raw_total_goods_fc": inv['total_goods_fc'],
                    "raw_total_transp_fc": inv['total_transp_fc'],
                    "raw_is_incl_vat": inv['is_incl_vat'],
                    "items": inv['items']
                }
                
                db_invoices[inv['inv_id']] = final_inv
                
                # 2. Rozpad do objednávek v orders_db
                for it in inv['items']:
                    oid = it['oid']
                    cost_czk = it['price_fc_clean'] * inv['rate']
                    
                    # Poměrový výpočet dopravy pro tuto konkrétní položku
                    ratio = it['price_fc_clean'] / actual_total_goods if actual_total_goods > 0 else 0
                    transp_czk = (actual_total_transp * inv['rate']) * ratio * it['qty']
                    
                    finance_entry = {
                        "inv_id": inv['inv_id'],
                        "product": it['product_name'],
                        "qty": it['qty'],
                        "buy_czk": cost_czk * it['qty'],
                        "transp_czk": transp_czk
                    }
                    db_orders[oid]['finance'].append(finance_entry)
                    
                save_db(INVOICES_DB_FILE, db_invoices)
                save_db(ORDERS_DB_FILE, db_orders)
                del st.session_state['active_invoice']
                st.success(f"Faktura {inv['inv_id']} úspěšně uložena a zbytek přesunut na Sklad!")
                time.sleep(2)
                st.rerun()

# =======================================================
# TAB 2: HISTORIE FAKTUR A SKLAD
# =======================================================
with tab_history:
    if st.session_state.get('reopen_msg'):
        st.success(f"✅ Faktura {st.session_state['reopen_msg']} byla vyjmuta ze skladu! Přepněte se do 1. záložky (Zpracování přijaté faktury) a můžete pokračovat v přidávání.")
        st.session_state['reopen_msg'] = ""

    st.markdown("### 📦 Zpracované faktury a Zůstatky (Sklad)")
    if not db_invoices:
        st.info("Zatím nebyly zpracovány žádné faktury.")
    else:
        for i_id, i_data in reversed(list(db_invoices.items())):
            
            # --- VÝPOČET MARŽE A ZŮSTATKŮ PRO TUTO KONKRÉTNÍ FAKTURU ---
            inv_revenue = 0.0
            inv_cost_goods = 0.0
            inv_cost_transp = 0.0
            
            for oid, o_data in db_orders.items():
                if 'finance' in o_data:
                    for f in o_data['finance']:
                        if f['inv_id'] == i_id:
                            inv_cost_goods += f['buy_czk']
                            inv_cost_transp += f['transp_czk']
                            # Dohledáme prodejní cenu přiřazených kusů
                            for p in o_data.get('products', []):
                                if p['name'] == f['product']:
                                    inv_revenue += p.get('unit_sell_price_czk', 0.0) * f['qty']
                                    break
            
            inv_cost_total = inv_cost_goods + inv_cost_transp
            inv_margin = inv_revenue - inv_cost_total
            inv_margin_pct = (inv_margin / inv_revenue * 100) if inv_revenue > 0 else 0
            
            celkem_faktura_czk = i_data['total_goods_czk'] + i_data['total_transp_czk']
            sklad_zbozi_czk = i_data['total_goods_czk'] - inv_cost_goods
            sklad_doprava_czk = i_data['total_transp_czk'] - inv_cost_transp
            
            barva_marze = "#c0392b" if inv_margin < 0 else "#27ae60"

            # --- OPRAVA: Formátování bokem pro 100% čisté HTML ---
            str_celkem = f"{int(celkem_faktura_czk):,} Kč".replace(',', ' ')
            str_sklad_zbozi = f"{int(sklad_zbozi_czk):,} Kč".replace(',', ' ')
            str_sklad_doprava = f"{int(sklad_doprava_czk):,} Kč".replace(',', ' ')
            str_trzba = f"{int(inv_revenue):,} Kč".replace(',', ' ')
            str_naklad = f"{int(inv_cost_total):,} Kč".replace(',', ' ')
            str_marze = f"{int(inv_margin):,} Kč ({inv_margin_pct:.1f} %)".replace(',', ' ')

            # HTML kód musí být přiražený k levému okraji
            html_obsah = f"""<div style='border: 1px solid #3498db; border-radius: 8px; padding: 15px; margin-bottom: 5px; background-color: #ebf5fb;'>
<h4 style='margin-top: 0; color: #2980b9;'>🧾 Faktura: {i_id} | 🏭 {i_data['supplier']}</h4>
<div style='display:flex; justify-content:space-between; border-top: 1px solid #bdc3c7; padding-top: 10px; margin-top: 10px;'>
    <div style='width: 30%;'>
        <span style='font-size: 0.85em; color: #7f8c8d;'>CELKOVÁ FAKTURA</span><br>
        <b style='font-size: 1.1em;'>{str_celkem}</b>
    </div>
    <div style='width: 35%;'>
        <span style='font-size: 0.85em; color: #7f8c8d;'>📦 ZBOŽÍ NA SKLADĚ</span><br>
        <b style='font-size: 1.1em; color:#8e44ad;'>{str_sklad_zbozi}</b>
    </div>
    <div style='width: 35%;'>
        <span style='font-size: 0.85em; color: #7f8c8d;'>🚚 NEZAŘAZENÁ DOPRAVA</span><br>
        <b style='font-size: 1.1em; color:#e67e22;'>{str_sklad_doprava}</b>
    </div>
</div>
<div style='margin-top: 15px; background-color: #ffffff; padding: 10px; border-radius: 5px; border: 1px solid #d5d8dc;'>
    <b style='color:#2c3e50;'>📊 Ziskovost z přiřazených položek:</b><br>
    <div style='display:flex; justify-content:space-between; margin-top: 5px;'>
        <div>Tržba z e-shopu:<br><b>{str_trzba}</b></div>
        <div>Náklady z faktury:<br><b>{str_naklad}</b></div>
        <div style='text-align: right;'>Hrubá marže:<br><b style='color:{barva_marze}; font-size: 1.1em;'>{str_marze}</b></div>
    </div>
</div>
</div>"""
            
            # Samotné vykreslení
            st.markdown(html_obsah, unsafe_allow_html=True)
            
            with st.expander("⚙️ Nástroje a správa faktury"):
                c_reopen, c_storno = st.columns(2)
                
                if c_reopen.button(f"✏️ Otevřít a doplnit fakturu", key=f"reopen_{i_id}", use_container_width=True):
                    for oid, o_data in db_orders.items():
                        if 'finance' in o_data:
                            o_data['finance'] = [f for f in o_data['finance'] if f['inv_id'] != i_id]
                            
                    st.session_state['active_invoice'] = {
                        "inv_id": i_id,
                        "supplier": i_data['supplier'],
                        "currency": i_data['currency'],
                        "rate": i_data['rate'],
                        "total_goods_fc": i_data.get('raw_total_goods_fc', i_data['total_goods_czk'] / i_data['rate']),
                        "total_transp_fc": i_data.get('raw_total_transp_fc', i_data['total_transp_czk'] / i_data['rate']),
                        "is_incl_vat": i_data.get('raw_is_incl_vat', False),
                        "items": i_data.get('items', [])
                    }
                    
                    del db_invoices[i_id]
                    save_db(INVOICES_DB_FILE, db_invoices)
                    save_db(ORDERS_DB_FILE, db_orders)
                    st.session_state['reopen_msg'] = i_id
                    st.rerun()
                    
                if c_storno.button(f"🗑️ Stornovat fakturu", key=f"storno_{i_id}", type="primary", use_container_width=True):
                    del db_invoices[i_id]
                    for oid, o_data in db_orders.items():
                        if 'finance' in o_data:
                            o_data['finance'] = [f for f in o_data['finance'] if f['inv_id'] != i_id]
                    save_db(INVOICES_DB_FILE, db_invoices)
                    save_db(ORDERS_DB_FILE, db_orders)
                    st.success("Faktura stornována a vymazána ze všech objednávek!")
                    time.sleep(1.5)
                    st.rerun()
            st.write("")

# =======================================================
# TAB 3: ZISKOVOST OBJEDNÁVEK
# =======================================================
with tab_profit:
    st.markdown("### 💰 Analýza zisku a kontrola rozúčtování")
    st.info("Zde vidíte všechny objednávky. Pokud objednávce chybí faktura k některým produktům (svítí červeně nebo oranžově), zisk záměrně skrýváme, dokud nebude účetnictví kompletní.")
    
    col_s1, col_s2 = st.columns(2)
    q_ord = col_s1.text_input("🔍 Hledat číslo objednávky:").strip().lower()
    q_stat = col_s2.selectbox("Filtrovat podle stavu (E-shop):", ["Všechny"] + list(set([d['status'] for d in db_orders.values()])))
    
    table_data = []
    
    for oid, d in db_orders.items():
        if q_ord and q_ord not in oid.lower(): continue
        if q_stat != "Všechny" and d['status'] != q_stat: continue
        
        # --- KONTROLA KOMPLETNOSTI (E-shop vs. Faktury) ---
        is_canceled = any(x in d['status'].lower() for x in ['zrušen', 'stornov', 'vrácen'])
        
        req_qty = {}
        for p in d.get('products', []):
            req_qty[p['name']] = req_qty.get(p['name'], 0) + p['qty']
            
        ass_qty = {}
        for f in d.get('finance', []):
            ass_qty[f['product']] = ass_qty.get(f['product'], 0) + f['qty']
            
        tot_req = sum(req_qty.values())
        tot_ass = sum(min(ass_qty.get(p, 0), req_qty[p]) for p in req_qty)
        
        if is_canceled:
            stav_uctovani = "⚪ Zrušeno"
        elif tot_req == 0:
            stav_uctovani = "✅ Kompletní"
        elif tot_ass == 0:
            stav_uctovani = "❌ Chybí vše"
        elif tot_ass < tot_req:
            stav_uctovani = "⚠️ Chybí část"
        else:
            stav_uctovani = "✅ Kompletní"
            
        # --- VÝPOČTY ZISKU ---
        prodej = d.get('sell_price_czk', 0)
        naklad_zbozi = sum(f['buy_czk'] for f in d.get('finance', []))
        naklad_doprava = sum(f['transp_czk'] for f in d.get('finance', []))
        celk_naklad = naklad_zbozi + naklad_doprava
        
        marze = prodej - celk_naklad
        marze_pct = (marze / prodej * 100) if prodej > 0 else 0
        
        inv_list = list(set([f['inv_id'] for f in d.get('finance', [])]))
        inv_str = ", ".join(inv_list) if inv_list else "-"
        
        if is_canceled and celk_naklad == 0 and prodej == 0:
            continue
            
        # --- DETEKTOR ZOBRAZENÍ MARŽE ---
        if stav_uctovani in ["✅ Kompletní", "⚪ Zrušeno"]:
            marze_str = f"{int(marze):,} Kč".replace(',', ' ')
            marze_pct_str = f"{marze_pct:.1f} %"
        else:
            marze_str = "Čeká se na faktury..."
            marze_pct_str = "---"
            
        table_data.append({
            "Objednávka": oid,
            "Stav (E-shop)": d['status'],
            "Stav účtování": stav_uctovani,
            "Tržba (CZK)": f"{int(prodej):,} Kč".replace(',', ' '),
            "Náklad zboží": f"{int(naklad_zbozi):,} Kč".replace(',', ' ') if naklad_zbozi > 0 else "0 Kč",
            "Náklad doprava": f"{int(naklad_doprava):,} Kč".replace(',', ' ') if naklad_doprava > 0 else "0 Kč",
            "Hrubá marže": marze_str,
            "Marže (%)": marze_pct_str,
            "Přiřazené faktury": inv_str
        })
        
    if not table_data:
        st.warning("Žádná objednávka nevyhovuje filtrům.")
    else:
        df_profit = pd.DataFrame(table_data)
        
        # --- ROZDĚLENÍ DAT DO KATEGORIÍ ---
        df_comp = df_profit[df_profit['Stav účtování'] == '✅ Kompletní']
        df_part = df_profit[df_profit['Stav účtování'] == '⚠️ Chybí část']
        df_miss = df_profit[df_profit['Stav účtování'] == '❌ Chybí vše']
        df_canc = df_profit[df_profit['Stav účtování'] == '⚪ Zrušeno']
        
        # --- PODZÁLOŽKY S POČÍTADLEM ---
        t_comp, t_part, t_miss, t_canc = st.tabs([
            f"✅ Kompletní marže ({len(df_comp)})", 
            f"⚠️ Chybí část faktur ({len(df_part)})", 
            f"❌ Chybí všechny faktury ({len(df_miss)})", 
            f"⚪ Zrušené ({len(df_canc)})"
        ])
        
        # --- FORMÁTOVÁNÍ A PODBARVENÍ TABULKY ---
        def highlight_row(row):
            if row['Stav účtování'] == '⚠️ Chybí část':
                return ['background-color: #fff3cd; color: #d35400; font-weight: bold'] * len(row)
            elif row['Stav účtování'] == '❌ Chybí vše':
                return ['background-color: #fadbd8; color: #c0392b; font-weight: bold'] * len(row)
            elif row['Stav účtování'] == '⚪ Zrušeno':
                return ['color: #95a5a6; font-style: italic'] * len(row)
            return [''] * len(row)
            
        def render_table(df_to_render):
            if df_to_render.empty:
                st.info("Žádné objednávky v této kategorii.")
            else:
                styled_df = df_to_render.style.apply(highlight_row, axis=1)
                st.dataframe(styled_df, use_container_width=True, hide_index=True)

        # Vykreslení do jednotlivých podzáložek
        with t_comp: render_table(df_comp)
        with t_part: render_table(df_part)
        with t_miss: render_table(df_miss)
        with t_canc: render_table(df_canc)

        # --- DETAILNÍ RENTGEN OBJEDNÁVKY ---
        st.markdown("---")
        st.markdown("### 🔎 Rentgen: Ziskovost jednotlivých produktů v objednávce")
        
        filtered_oids = [r["Objednávka"] for r in table_data]
        detail_oid = st.selectbox("Vyberte objednávku pro zobrazení detailní marže na úroveň produktů:", ["-- Vyberte --"] + filtered_oids)
        
        if detail_oid != "-- Vyberte --":
            d = db_orders[detail_oid]
            
            # Celkové shrnutí za objednávku
            req_qty_d = {}
            for p in d.get('products', []): req_qty_d[p['name']] = req_qty_d.get(p['name'], 0) + p['qty']
            ass_qty_d = {}
            for f in d.get('finance', []): ass_qty_d[f['product']] = ass_qty_d.get(f['product'], 0) + f['qty']
            
            t_req = sum(req_qty_d.values())
            t_ass = sum(min(ass_qty_d.get(p, 0), req_qty_d[p]) for p in req_qty_d)
            is_order_complete = (t_req > 0 and t_ass >= t_req) or (t_req == 0)
            
            celkovy_prodej = d.get('sell_price_czk', 0)
            prijem_doprava = d.get('shipping_revenue_czk', 0)
            celkovy_prodej_zbozi = celkovy_prodej - prijem_doprava
            
            celkovy_naklad_z = sum(f['buy_czk'] for f in d.get('finance', []))
            celkovy_naklad_d = sum(f['transp_czk'] for f in d.get('finance', []))
            celkovy_naklad = celkovy_naklad_z + celkovy_naklad_d
            celk_marze = celkovy_prodej - celkovy_naklad
            celk_marze_pct = (celk_marze / celkovy_prodej * 100) if celkovy_prodej > 0 else 0
            
            if is_order_complete:
                barva_top = "#c0392b" if celk_marze < 0 else "#27ae60"
                marze_top_html = f"<span style='color:{barva_top}; font-size:1.1em;'><b>{int(celk_marze)} Kč ({celk_marze_pct:.1f} %)</b></span>"
            else:
                marze_top_html = "<span style='color:#e67e22; font-size:1.1em;'><b>Zatím neznámá (Čeká se na přiřazení všech faktur)</b></span>"
            
            st.markdown(f"""
            <div style='background-color: #f8f9f9; padding: 15px; border-radius: 8px; border-left: 5px solid #2980b9; margin-bottom: 20px;'>
                <h4 style='margin:0; color:#2c3e50;'>Detail objednávky: {detail_oid} | Stav: {d['status']}</h4>
                <b>Celková čistá tržba:</b> {int(celkovy_prodej)} Kč <span style='font-size:0.9em; color:#7f8c8d;'>(Z toho za zboží: {int(celkovy_prodej_zbozi)} Kč, Za dopravu od zák.: {int(prijem_doprava)} Kč)</span><br>
                <b>Zatím zadané náklady:</b> {int(celkovy_naklad)} Kč <span style='font-size:0.9em; color:#7f8c8d;'>(Z toho nákup: {int(celkovy_naklad_z)} Kč, Doprava dodavatele: {int(celkovy_naklad_d)} Kč)</span><br>
                <b>Celková hrubá marže:</b> {marze_top_html}
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown("#### Rozpad na jednotlivé sekce:")
            
            st.markdown(f"""
            <div style='border: 1px solid #3498db; background-color: #ebf5fb; padding: 15px; border-radius: 8px; margin-bottom: 15px;'>
                <div style='display:flex; justify-content:space-between; margin-bottom: 10px;'>
                    <div><b style='font-size: 1.1em;'>🚚 Příjem za dopravu a platbu</b> <span style='color:#7f8c8d;'>(Vybráno od zákazníka na e-shopu)</span></div>
                    <div><span style='background-color:#3498db; color:white; padding:3px 8px; border-radius:12px; font-size:0.8em;'>Příprava pro logistiku</span></div>
                </div>
                <div style='display:flex; justify-content:space-between; border-top: 1px solid #bdc3c7; padding-top: 10px;'>
                    <div style='width: 30%;'>
                        <span style='font-size: 0.85em; color: #7f8c8d;'>PŘÍJEM (bez DPH)</span><br>
                        <b>{int(prijem_doprava)} Kč</b>
                    </div>
                    <div style='width: 40%;'>
                        <span style='font-size: 0.85em; color: #7f8c8d;'>INTERNÍ NÁKLADY (Rozvoz/Toptrans)</span><br>
                        <span style='font-style: italic; color: #7f8c8d;'>Brzy bude napojeno na 1_Planovac_Tras</span>
                    </div>
                    <div style='width: 30%; text-align: right;'>
                        <span style='font-size: 0.85em; color: #7f8c8d;'>ČISTÁ MARŽE Z DOPRAVY</span><br>
                        <span style='font-size: 1.2em; color: #7f8c8d;'><b>Zatím neznámá</b></span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            if not d.get('products'):
                st.info("K této objednávce nejsou evidovány žádné další produkty.")
            else:
                for p in d['products']:
                    p_name = p['name']
                    p_qty = p['qty']
                    p_unit_sell = p.get('unit_sell_price_czk', 0)
                    p_total_sell = p_unit_sell * p_qty
                    
                    f_entries = [f for f in d.get('finance', []) if f['product'] == p_name]
                    f_qty = sum(f['qty'] for f in f_entries)
                    f_buy_czk = sum(f['buy_czk'] for f in f_entries)
                    f_transp_czk = sum(f['transp_czk'] for f in f_entries)
                    f_total_cost = f_buy_czk + f_transp_czk
                    
                    p_margin = p_total_sell - f_total_cost
                    p_margin_pct = (p_margin / p_total_sell * 100) if p_total_sell > 0 else 0
                    
                    inv_names = ", ".join(list(set([f['inv_id'] for f in f_entries])))
                    
                    if f_qty == 0:
                        status_badge = "<span style='background-color:#e74c3c; color:white; padding:3px 8px; border-radius:12px; font-size:0.8em;'>Chybí faktura (0 ks)</span>"
                        border_color = "#e74c3c"
                        bg_color = "#fdf2e9"
                        item_marze_html = "<span style='font-size: 1.1em; color:#e74c3c;'><b>Čeká se...</b></span>"
                    elif f_qty < p_qty:
                        status_badge = f"<span style='background-color:#f39c12; color:white; padding:3px 8px; border-radius:12px; font-size:0.8em;'>Částečné náklady ({f_qty}/{p_qty} ks)</span>"
                        border_color = "#f39c12"
                        bg_color = "#fef9e7"
                        item_marze_html = "<span style='font-size: 1.1em; color:#f39c12;'><b>Čeká se...</b></span>"
                    else:
                        status_badge = "<span style='background-color:#2ecc71; color:white; padding:3px 8px; border-radius:12px; font-size:0.8em;'>Náklady kompletní</span>"
                        border_color = "#2ecc71"
                        bg_color = "#ffffff"
                        
                        barva_item = "#c0392b" if p_margin < 0 else "#27ae60"
                        item_marze_html = f"<span style='font-size: 1.2em; color:{barva_item};'><b>{int(p_margin)} Kč ({p_margin_pct:.1f} %)</b></span>"
                        
                    st.markdown(f"""
                    <div style='border: 1px solid {border_color}; background-color: {bg_color}; padding: 15px; border-radius: 8px; margin-bottom: 10px;'>
                        <div style='display:flex; justify-content:space-between; margin-bottom: 10px;'>
                            <div><b style='font-size: 1.1em;'>{p_name}</b> <span style='color:#7f8c8d;'>({p_qty} ks)</span></div>
                            <div>{status_badge}</div>
                        </div>
                        <div style='display:flex; justify-content:space-between; border-top: 1px solid #eee; padding-top: 10px;'>
                            <div style='width: 30%;'>
                                <span style='font-size: 0.85em; color: #7f8c8d;'>PRODEJ (bez DPH)</span><br>
                                <b>{int(p_total_sell)} Kč</b> <span style='font-size: 0.85em; color: #7f8c8d;'>(á {int(p_unit_sell)} Kč)</span>
                            </div>
                            <div style='width: 40%;'>
                                <span style='font-size: 0.85em; color: #7f8c8d;'>NÁKLADY (Zboží + Doprava z faktur)</span><br>
                                <b>{int(f_buy_czk)} Kč + {int(f_transp_czk)} Kč</b> 
                                <span style='font-size: 0.85em; color: #7f8c8d;'><br>Pochází z faktur: {inv_names if inv_names else "-"}</span>
                            </div>
                            <div style='width: 30%; text-align: right;'>
                                <span style='font-size: 0.85em; color: #7f8c8d;'>HRUBÁ MARŽE POLOŽKY</span><br>
                                {item_marze_html}
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)