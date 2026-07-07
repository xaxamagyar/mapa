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

def load_routes_db():
    file_path = "saved_routes.json"
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        resp = requests.get(url, headers=get_github_headers())
        if resp.status_code == 200:
            try: return json.loads(base64.b64decode(resp.json()['content']).decode('utf-8'))
            except: return []
        return []
    else:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                try: return json.load(f)
                except: return []
        return []
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

    # --- NOVINKA: Sloupec se stavem konkrétního produktu ---
    item_status_col = None
    for col in ['itemStatusName', 'Stav položky', 'itemStatus']:
        if col in df.columns: item_status_col = col; break

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
                # --- NOVINKA: Ignorování stornovaných produktů ---
                if item_status_col and pd.notna(r[item_status_col]):
                    i_stat = str(r[item_status_col]).strip().lower()
                    if 'stornov' in i_stat or 'zrušen' in i_stat or 'zrusen' in i_stat:
                        continue
                # ------------------------------------------------

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
    
    # Nejdříve všechny dosavadní objednávky označíme jako "ztracené"
    for oid in db.keys():
        db[oid]['in_last_import'] = False
    
    for oid, data in all_fetched.items():
        if oid not in db:
            db[oid] = data
            db[oid]['in_last_import'] = True
            added += 1
        else:
            db[oid]['status'] = data['status']
            db[oid]['sell_price_czk'] = data['sell_price_czk']
            db[oid]['shipping_revenue_czk'] = data.get('shipping_revenue_czk', 0.0)
            db[oid]['products'] = data['products']
            db[oid]['in_last_import'] = True
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

tab_invoice, tab_history, tab_profit, tab_transport = st.tabs(["✍️ Zpracování přijaté faktury", "📦 Historie faktur a Slepý sklad", "💰 Ziskovost objednávek", "🚚 Import dopravy"])

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
            
            c4, c5, c6, c7 = st.columns([1.2, 2, 2, 1.2])
            inv_rate = c4.number_input("Kurz do CZK:", min_value=0.01, value=25.0 if inv_curr=="EUR" else (5.8 if inv_curr=="PLN" else 1.0), step=0.1, disabled=(inv_curr=="CZK"))
            inv_goods = c5.number_input(f"📦 Zboží (v {inv_curr}):", min_value=0.0, step=100.0)
            inv_transp = c6.number_input(f"🚚 Doprava/vedl. náklady:", min_value=0.0, step=10.0)
            inv_transp_curr = c7.selectbox("Měna dopravy:", [inv_curr, "CZK"] if inv_curr != "CZK" else ["CZK"])
            
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
                        "transp_currency": inv_transp_curr, # Uložení měny dopravy
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
                        
                        for idx, p in enumerate(sel_data['products']):
                            # Do klíče přidáme `idx` pro absolutní unikátnost
                            chk_key = f"chk_add_{sel_oid}_{idx}_{st.session_state['item_counter']}"
                            
                            c_chk, c_n, c_q, c_p = st.columns([0.8, 2.2, 1, 1])
                            is_checked = c_chk.checkbox("Na faktuře", key=chk_key)
                            
                            c_n.markdown(f"**{p['name']}**<br><span style='font-size:0.85em; color:#7f8c8d;'>Zákazník koupil: {p['qty']} ks</span>", unsafe_allow_html=True)
                            
                            in_qty = c_q.number_input("Ks z faktury:", min_value=1, max_value=p['qty'], value=p['qty'], step=1, disabled=not is_checked, key=f"q_{sel_oid}_{idx}_{st.session_state['item_counter']}")
                            in_price = c_p.number_input(f"Cena/ks ({inv['currency']}):", min_value=0.0, step=10.0, disabled=not is_checked, key=f"p_{sel_oid}_{idx}_{st.session_state['item_counter']}")
                            
                            # --- NOVINKA: DETEKTOR CHYBĚJÍCÍCH ROŠTŮ ---
                            p_name_low = p['name'].lower()
                            if any(klic in p_name_low for klic in ['vmk', 'nevada', 'beskyd', 'boston']):
                                st.markdown("<div style='background-color: #fff3cd; color: #d35400; padding: 6px 12px; border-left: 4px solid #f39c12; margin-top: -10px; margin-bottom: 10px; border-radius: 3px; font-size: 0.85em;'>⚠️ <b>Pozor na rošt!</b> K tomuto typu postele se kupuje rošt zvlášť (často z jiné faktury). Nezapomeňte k posteli tyto náklady dodatečně přidat.</div>", unsafe_allow_html=True)
                            # ---------------------------------------------------
                            
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
        
        # --- INICIALIZACE DODATEČNÝCH NÁKLADŮ (ROŠTY) V SESSION_STATE ---
        if 'extra_invoice_fc' not in st.session_state:
            st.session_state['extra_invoice_fc'] = 0.0

        # Přepočet celkové hodnoty faktury včetně dodatečně uznané částky za rošty
        adjusted_total_goods = actual_total_goods + st.session_state['extra_invoice_fc']
        adjusted_remaining_goods = adjusted_total_goods - assigned_goods

        c_cancel, c_save = st.columns(2)
        if c_cancel.button("🗑️ Zrušit rozpracovanou fakturu", use_container_width=True):
            st.session_state['extra_invoice_fc'] = 0.0
            del st.session_state['active_invoice']
            st.rerun()
            
        if c_save.button("💾 ULOŽIT FAKTURU (Zbývající hodnota půjde do Skladu)", type="primary", use_container_width=True):
            if adjusted_remaining_goods < -0.1:
                st.error(f"⚠️ **Rozdělili jste na objednávky více peněz, než je celková hodnota zadané faktury!** (Přebitek: {abs(int(adjusted_remaining_goods))} {inv['currency']})")
                st.info("💡 Pokud k tomuto zboží přiřazujete rošty nebo příslušenství z jiné faktury, klikněte na tlačítko níže a připojte její poměrnou hodnotu.")
                st.session_state['show_extra_invoice_modal'] = True
            else:
                # --- 🚀 OSTRÉ ULOŽENÍ S ROZPOČÍTÁNÍM DOPRAVY A ROŠTŮ (ÚČETNÍ JÁDRO) ---
                t_curr = inv.get('transp_currency', inv['currency'])
                transp_rate = 1.0 if t_curr == 'CZK' else inv['rate']
                
                final_inv = {
                    "supplier": inv['supplier'],
                    "currency": inv['currency'],
                    "rate": inv['rate'],
                    "total_goods_czk": adjusted_total_goods * inv['rate'],
                    "total_transp_czk": actual_total_transp * transp_rate,
                    "assigned_goods_czk": assigned_goods * inv['rate'],
                    "stock_remainder_czk": adjusted_remaining_goods * inv['rate'],
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "raw_total_goods_fc": inv['total_goods_fc'] + st.session_state['extra_invoice_fc'],
                    "raw_total_transp_fc": inv['total_transp_fc'],
                    "transp_currency": t_curr,
                    "raw_is_incl_vat": inv['is_incl_vat'],
                    "extra_invoice_note": f"Připojena druhá faktura za příslušenství v hodnotě {st.session_state['extra_invoice_fc']} {inv['currency']}" if st.session_state['extra_invoice_fc'] > 0 else "",
                    "items": inv['items']
                }
                
                # Zápis do databáze faktur přes unikátní ID faktury
                db_invoices[inv['inv_id']] = final_inv
                
                # PŮVODNÍ POMĚROVÝ ROZPAD DOPRAVY A NÁKLADŮ DO JEDNOTLIVÝCH OBJEDNÁVEK
                for it in inv['items']:
                    oid = it['oid']
                    cost_czk = it['price_fc_clean'] * inv['rate']
                    
                    # Výpočet podílu na faktuře (zohledňuje i cenu přidaných roštů)
                    ratio = it['price_fc_clean'] / adjusted_total_goods if adjusted_total_goods > 0 else 0
                    transp_czk_final = (actual_total_transp * transp_rate) * ratio * it['qty']
                    
                    finance_entry = {
                        "inv_id": inv['inv_id'],
                        "product": it['product_name'],
                        "qty": it['qty'],
                        "buy_czk": cost_czk * it['qty'],
                        "transp_czk": transp_czk_final
                    }
                    
                    if oid in db_orders:
                        # Přidání finančního záznamu k objednávce
                        db_orders[oid]['finance'].append(finance_entry)
                        db_orders[oid]['status'] = "Zpracováno"
                
                # Bezpečné uložení obou databází do cloudu / na disk
                save_db(INVOICES_DB_FILE, db_invoices)
                save_db(ORDERS_DB_FILE, db_orders)
                
                # Úplné vyčištění paměti a úspěšný návrat zpět
                st.session_state['extra_invoice_fc'] = 0.0
                del st.session_state['active_invoice']
                st.success(f"🎉 Faktura {inv['inv_id']} i s dodatečnými rošty byla bezpečně uložena a zbytek přesunut na Sklad!")
                time.sleep(2)
                st.rerun()

        # --- TLAČÍTKO PRO RUČNÍ VYVOLÁNÍ POP-UPU PŘI PŘEBYTKU ---
        if adjusted_remaining_goods < -0.1:
            if st.button("➕ Připojit k postelím další fakturu (např. za rošty)", type="secondary", use_container_width=True):
                st.session_state['show_extra_invoice_modal'] = True

        # --- DIALOGOVÉ POP-UP OKNO PRO DRUHOU FAKTURU ---
        if st.session_state.get('show_extra_invoice_modal', False):
            @st.dialog("➕ Připojení dodatečné faktury (např. za rošty)")
            def extra_invoice_dialog():
                st.markdown("Zadejte podklady pro navýšení uznatelných nákladů na zboží:")
                
                inv_total = st.number_input(f"Celková částka na této druhé faktuře ({inv['currency']}):", min_value=0.0, step=100.0)
                inv_allocated = st.number_input(f"🎯 Částka z této faktury použitá k postelím v tomto rozvozu ({inv['currency']}):", min_value=0.0, step=100.0, help="Zadejte přesnou hodnotu roštů, které k těmto postelím reálně přiřazujete.")
                
                st.markdown("<br>", unsafe_allow_html=True)
                c_pop_cancel, c_pop_ok = st.columns(2)
                
                if c_pop_cancel.button("Zrušit", use_container_width=True):
                    st.session_state['show_extra_invoice_modal'] = False
                    st.rerun()
                    
                if c_pop_ok.button("💾 Sloučit hodnoty faktur", type="primary", use_container_width=True):
                    if inv_allocated > 0:
                        allocated_clean = inv_allocated / 1.21 if inv['is_incl_vat'] else inv_allocated
                        st.session_state['extra_invoice_fc'] = allocated_clean
                        st.session_state['show_extra_invoice_modal'] = False
                        st.success("Náklady úspěšně spojeny! Nyní můžete fakturu bezpečně uložit.")
                        time.sleep(1.2)
                        st.rerun()
            extra_invoice_dialog()

# =======================================================
# TAB 2: HISTORIE FAKTUR A SKLAD
# =======================================================
with tab_history:
    if st.session_state.get('reopen_msg'):
        st.success(f"✅ Faktura {st.session_state['reopen_msg']} byla vyjmuta ze skladu! Přepněte se do 1. záložky (Zpracování přijaté faktury) a můžete pokračovat v přidávání.")
        st.session_state['reopen_msg'] = ""

    st.markdown("### 📦 Historie zpracovaných dokladů a Zůstatky")
    if not db_invoices:
        st.info("Zatím nebyly zpracovány žádné faktury.")
    else:
        # Třídění faktur do 3 nezávislých skupin
        inv_zbozi = {}
        inv_doprava = {}
        inv_rozvozy = {}
        
        for i_id, i_data in db_invoices.items():
            if i_data.get('supplier') == "Dopravce (Import CSV/Excel)":
                inv_doprava[i_id] = i_data
            elif i_data.get('supplier') == "Vlastní rozvoz (Dispečink)":
                inv_rozvozy[i_id] = i_data
            else:
                inv_zbozi[i_id] = i_data

        t_zbozi, t_doprava, t_rozvozy = st.tabs([
            f"📦 Dodavatelé zboží ({len(inv_zbozi)})", 
            f"🚚 Přepravci z CSV ({len(inv_doprava)})", 
            f"🚐 Vlastní rozvozy ({len(inv_rozvozy)})"
        ])

        # --- Vykreslení 1: FAKTURY ZA ZBOŽÍ (Původní detailní karta) ---
        with t_zbozi:
            if not inv_zbozi:
                st.info("Zatím žádné faktury za zboží.")
                
            for i_id, i_data in reversed(list(inv_zbozi.items())):
                inv_revenue = 0.0
                inv_cost_goods = 0.0
                inv_cost_transp = 0.0
                
                for oid, o_data in db_orders.items():
                    if 'finance' in o_data:
                        for f in o_data['finance']:
                            if f['inv_id'] == i_id:
                                inv_cost_goods += f['buy_czk']
                                inv_cost_transp += f['transp_czk']
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

                str_celkem = f"{int(celkem_faktura_czk):,} Kč".replace(',', ' ')
                str_sklad_zbozi = f"{int(sklad_zbozi_czk):,} Kč".replace(',', ' ')
                str_sklad_doprava = f"{int(sklad_doprava_czk):,} Kč".replace(',', ' ')
                str_trzba = f"{int(inv_revenue):,} Kč".replace(',', ' ')
                str_naklad = f"{int(inv_cost_total):,} Kč".replace(',', ' ')
                str_marze = f"{int(inv_margin):,} Kč ({inv_margin_pct:.1f} %)".replace(',', ' ')

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
                st.markdown(html_obsah, unsafe_allow_html=True)
                
                with st.expander("⚙️ Nástroje a správa faktury"):
                    c_reopen, c_storno = st.columns(2)
                    
                    if c_reopen.button(f"✏️ Otevřít a doplnit fakturu", key=f"reopen_{i_id}", use_container_width=True):
                        for oid, o_data in db_orders.items():
                            if 'finance' in o_data:
                                o_data['finance'] = [f for f in o_data['finance'] if f['inv_id'] != i_id]
                                
                        # Ochrana pro starší faktury, které neměly zadanou měnu dopravy
                        t_curr = i_data.get('transp_currency', i_data.get('currency', 'CZK'))
                        t_rate = 1.0 if t_curr == 'CZK' else i_data.get('rate', 1.0)
                        
                        st.session_state['active_invoice'] = {
                            "inv_id": i_id,
                            "supplier": i_data['supplier'],
                            "currency": i_data['currency'],
                            "rate": i_data.get('rate', 1.0),
                            "total_goods_fc": i_data.get('raw_total_goods_fc', i_data['total_goods_czk'] / i_data.get('rate', 1.0)),
                            "total_transp_fc": i_data.get('raw_total_transp_fc', i_data['total_transp_czk'] / t_rate),
                            "transp_currency": t_curr,
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

                    # --- NOVINKA: Formulář pro dodatečnou úpravu faktury ---
                    st.markdown("#### 📝 Dodatečná úprava hlavičky faktury")
                    with st.form(f"edit_form_{i_id}"):
                        e_supp = st.text_input("Dodavatel (Název):", value=i_data.get('supplier', ''))
                        
                        ec1, ec2, ec3 = st.columns([1.5, 1.5, 1])
                        e_goods = ec1.number_input(f"Cena zboží ({i_data.get('currency', 'CZK')}):", value=float(i_data.get('raw_total_goods_fc', i_data.get('total_goods_czk', 0))), step=100.0)
                        
                        # Zjištění aktuální měny dopravy a příprava roletky
                        inv_c = i_data.get('currency', 'CZK')
                        def_t_curr = i_data.get('transp_currency', inv_c)
                        c_options = [inv_c, "CZK"] if inv_c != "CZK" else ["CZK"]
                        c_options = list(dict.fromkeys(c_options)) # Odstranění duplicit pro jistotu
                        
                        e_transp = ec2.number_input(f"Doprava dodavatele:", value=float(i_data.get('raw_total_transp_fc', i_data.get('total_transp_czk', 0))), step=10.0)
                        e_t_curr = ec3.selectbox("Měna dopravy:", c_options, index=c_options.index(def_t_curr) if def_t_curr in c_options else 0)
                        
                        e_vat = st.checkbox("Zadané částky JSOU VČETNĚ DPH (Systém je očistí o 21 %)", value=i_data.get('raw_is_incl_vat', False))
                        
                        if st.form_submit_button("💾 Uložit nové údaje", type="primary"):
                            actual_g = e_goods / 1.21 if e_vat else e_goods
                            actual_t = e_transp / 1.21 if e_vat else e_transp
                            rate = i_data.get('rate', 1.0)
                            t_rate = 1.0 if e_t_curr == 'CZK' else rate
                            
                            i_data['supplier'] = e_supp
                            i_data['raw_total_goods_fc'] = e_goods
                            i_data['raw_total_transp_fc'] = e_transp
                            i_data['transp_currency'] = e_t_curr
                            i_data['raw_is_incl_vat'] = e_vat
                            
                            i_data['total_goods_czk'] = actual_g * rate
                            i_data['total_transp_czk'] = actual_t * t_rate
                            
                            # Chytrý přepočet skladového zůstatku podle nové celkové ceny
                            i_data['stock_remainder_czk'] = i_data['total_goods_czk'] - i_data.get('assigned_goods_czk', 0)
                            
                            # --- NOVINKA: Zpětný přepočet dopravy do přiřazených objednávek ---
                            for it in i_data.get('items', []):
                                # Vypočteme nový poměr pro tento konkrétní produkt a vynásobíme novou celkovou dopravou
                                ratio = it['price_fc_clean'] / actual_g if actual_g > 0 else 0
                                new_transp_czk = (actual_t * t_rate) * ratio * it['qty']
                                
                                oid = it['oid']
                                if oid in db_orders and 'finance' in db_orders[oid]:
                                    for f in db_orders[oid]['finance']:
                                        if f['inv_id'] == i_id and f['product'] == it['product_name']:
                                            f['transp_czk'] = new_transp_czk
                            # ------------------------------------------------------------------
                            
                            db_invoices[i_id] = i_data
                            save_db(INVOICES_DB_FILE, db_invoices)
                            save_db(ORDERS_DB_FILE, db_orders) # Nyní se uloží i přepsané objednávky!
                            st.success("Údaje faktury byly úspěšně upraveny a doprava v objednávkách automaticky přepočítána!")
                            time.sleep(1.5)
                            st.rerun()
                # --- NOVINKA: Export rozúčtování faktury do Excelu a PDF ---
                    st.markdown("#### 📊 Export přiřazených položek")
                    export_rows = []
                    for export_oid, export_odata in db_orders.items():
                        if 'finance' in export_odata:
                            for f in export_odata['finance']:
                                if f['inv_id'] == i_id:
                                    sell_total = 0.0
                                    for p in export_odata.get('products', []):
                                        if p['name'] == f['product']:
                                            sell_total = p.get('unit_sell_price_czk', 0.0) * f['qty']
                                            break
                                    
                                    cost_zbozi = f['buy_czk']
                                    cost_doprava = f['transp_czk']
                                    cost_celkem = cost_zbozi + cost_doprava
                                    marze = sell_total - cost_celkem
                                    marze_pct = (marze / sell_total * 100) if sell_total > 0 else 0
                                    
                                    export_rows.append({
                                        "Faktura": i_id,
                                        "Objednávka": export_oid,
                                        "Produkt": f['product'],
                                        "Množství": f['qty'],
                                        "Nákup Zboží (CZK)": int(cost_zbozi),
                                        "Nákup Doprava (CZK)": int(cost_doprava),
                                        "Celkový náklad (CZK)": int(cost_celkem),
                                        "Prodej e-shop (CZK bez DPH)": int(sell_total),
                                        "Marže (CZK)": int(marze),
                                        "Marže (%)": round(marze_pct, 1)
                                    })
                    
                    if export_rows:
                        df_exp = pd.DataFrame(export_rows)
                        
                        # Vypočteme i celkové součty
                        totals = {
                            "Faktura": "CELKEM",
                            "Objednávka": "-",
                            "Produkt": "-",
                            "Množství": df_exp["Množství"].sum(),
                            "Nákup Zboží (CZK)": df_exp["Nákup Zboží (CZK)"].sum(),
                            "Nákup Doprava (CZK)": df_exp["Nákup Doprava (CZK)"].sum(),
                            "Celkový náklad (CZK)": df_exp["Celkový náklad (CZK)"].sum(),
                            "Prodej e-shop (CZK bez DPH)": df_exp["Prodej e-shop (CZK bez DPH)"].sum(),
                            "Marže (CZK)": df_exp["Marže (CZK)"].sum(),
                            "Marže (%)": round((df_exp["Marže (CZK)"].sum() / df_exp["Prodej e-shop (CZK bez DPH)"].sum() * 100), 1) if df_exp["Prodej e-shop (CZK bez DPH)"].sum() > 0 else 0
                        }
                        df_exp = pd.concat([df_exp, pd.DataFrame([totals])], ignore_index=True)
                        
                        # --- Přidání metadat do Excelu (Dodavatel, Kurz, Sklad) ---
                        df_exp.loc[len(df_exp)] = [""] * 10
                        df_exp.loc[len(df_exp)] = ["DODAVATEL:", i_data.get('supplier'), "", "", "", "", "", "", "", ""]
                        if i_data.get('currency', 'CZK') != 'CZK':
                            df_exp.loc[len(df_exp)] = ["KURZ:", f"{i_data.get('rate')} CZK/{i_data.get('currency')}", "", "", "", "", "", "", "", ""]
                        df_exp.loc[len(df_exp)] = ["ZŮSTATEK SKLAD (Kč):", int(sklad_zbozi_czk), "", "", "", "", "", "", "", ""]

                        # 1. EXCEL GENERACE
                        buf_exp = io.BytesIO()
                        with pd.ExcelWriter(buf_exp, engine='openpyxl') as writer:
                            df_exp.to_excel(writer, index=False, sheet_name='Rozpad_faktury')
                            
                        # 2. PDF GENERACE (Krásně naformátovaná tabulka)
                        from reportlab.lib import colors
                        from reportlab.lib.pagesizes import A4, landscape
                        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
                        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                        from reportlab.pdfbase import pdfmetrics
                        from reportlab.pdfbase.ttfonts import TTFont

                        pdf_buf = io.BytesIO()
                        doc = SimpleDocTemplate(pdf_buf, pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
                        elements = []

                        # Načtení Windows fontů pro českou diakritiku
                        font_reg = "Helvetica"
                        font_bold = "Helvetica-Bold"
                        paths_to_try = [("arial.ttf", "arialbd.ttf"), ("ARIAL.TTF", "ARIALBD.TTF"), ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf")]
                        for r_path, b_path in paths_to_try:
                            if os.path.exists(r_path) and os.path.exists(b_path):
                                try:
                                    pdfmetrics.registerFont(TTFont('ArialCustom', r_path))
                                    pdfmetrics.registerFont(TTFont('ArialCustom-Bold', b_path))
                                    font_reg = 'ArialCustom'
                                    font_bold = 'ArialCustom-Bold'
                                    break
                                except: pass

                        styles = getSampleStyleSheet()
                        title_style = styles['Heading1']
                        title_style.fontName = font_bold
                        title_style.textColor = colors.HexColor('#2c3e50')
                        elements.append(Paragraph(f"Rozpad přiřazených nákladů a marže - Faktura: {i_id}", title_style))
                        
                        # --- NOVINKA: Zobrazení metadat (Dodavatel, Kurz, Sklad) pod nadpisem v PDF ---
                        meta_style = ParagraphStyle('Meta', parent=styles['Normal'], fontName=font_reg, fontSize=11, leading=16)
                        kurz_str = f" | <b>Kurz:</b> {i_data.get('rate')} CZK/{i_data.get('currency')}" if i_data.get('currency', 'CZK') != 'CZK' else ""
                        sklad_str = f"{int(sklad_zbozi_czk):,} Kč".replace(',', ' ')
                        
                        meta_text = f"<b>Dodavatel:</b> {i_data.get('supplier')}{kurz_str}<br/>"
                        meta_text += f"<b>Zboží zbývající na skladě (nákupní cena):</b> <font color='#d35400'><b>{sklad_str}</b></font>"
                        
                        elements.append(Paragraph(meta_text, meta_style))
                        elements.append(Spacer(1, 15))
                        # ------------------------------------------------------------------------------

                        # Příprava dat pro PDF tabulku
                        table_data = [["Objednávka", "Produkt", "Ks", "Zboží (Kč)", "Doprava (Kč)", "Tržba bez DPH", "Marže (Kč)", "Marže (%)"]]

                        for row in export_rows:
                            prod_name = str(row['Produkt'])
                            if len(prod_name) > 40: prod_name = prod_name[:38] + "..." # Ořez dlouhých názvů, aby se vešly na A4
                            
                            table_data.append([
                                str(row['Objednávka']),
                                prod_name,
                                str(row['Množství']),
                                f"{row['Nákup Zboží (CZK)']:,}".replace(',', ' '),
                                f"{row['Nákup Doprava (CZK)']:,}".replace(',', ' '),
                                f"{row['Prodej e-shop (CZK bez DPH)']:,}".replace(',', ' '),
                                f"{row['Marže (CZK)']:,}".replace(',', ' '),
                                f"{row['Marže (%)']} %"
                            ])

                        # Přidání součtového řádku
                        table_data.append([
                            "CELKEM", "", str(totals['Množství']),
                            f"{int(totals['Nákup Zboží (CZK)']):,}".replace(',', ' '),
                            f"{int(totals['Nákup Doprava (CZK)']):,}".replace(',', ' '),
                            f"{int(totals['Prodej e-shop (CZK bez DPH)']):,}".replace(',', ' '),
                            f"{int(totals['Marže (CZK)']):,}".replace(',', ' '),
                            f"{totals['Marže (%)']} %"
                        ])

                        # Nastavení stylů PDF tabulky (sloupec Objednávka rozšířen na 115)
                        t = Table(table_data, colWidths=[115, 230, 30, 75, 80, 85, 75, 65])
                        t.setStyle(TableStyle([
                            ('FONTNAME', (0,0), (-1,-1), font_reg),
                            ('FONTNAME', (0,0), (-1,0), font_bold), # Hlavička tučně
                            ('FONTNAME', (0,-1), (-1,-1), font_bold), # Spodek tučně
                            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2c3e50')), # Tmavě modrá hlavička
                            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), # Bílý text v hlavičce
                            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#ecf0f1')), # Šedý podkres pro součty
                            ('ALIGN', (2,0), (-1,-1), 'RIGHT'), # Čísla zarovnaná doprava
                            ('ALIGN', (0,0), (1,-1), 'LEFT'), # Text doleva
                            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), # Vertikální zarovnání
                            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#bdc3c7')), # Jemná mřížka
                            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f9fbfc')]), # Zebrování řádků
                            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                            ('TOPPADDING', (0,0), (-1,-1), 6),
                        ]))
                        elements.append(t)
                        doc.build(elements)
                        
                        # --- VYKRESLENÍ OBOU TLAČÍTEK VEDLE SEBE ---
                        c_dl1, c_dl2 = st.columns(2)
                        
                        c_dl1.download_button(
                            label="📊 Stáhnout Excel data",
                            data=buf_exp.getvalue(),
                            file_name=f"Rozpad_faktury_{i_id}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_inv_xls_{i_id}",
                            type="secondary",
                            use_container_width=True
                        )
                        
                        c_dl2.download_button(
                            label="📥 Stáhnout hezké PDF k tisku",
                            data=pdf_buf.getvalue(),
                            file_name=f"Rozpad_faktury_{i_id}.pdf",
                            mime="application/pdf",
                            key=f"dl_inv_pdf_{i_id}",
                            type="primary",
                            use_container_width=True
                        )
                    else:
                        st.info("Na této faktuře zatím nejsou přiřazeny žádné produkty.")
                # -----------------------------------------------------------
                st.write("")

        # --- FUNKCE: Vykreslení DOPRAVY A ROZVOZŮ (Zjednodušená karta) ---
        def render_transport_invoices(inv_dict, icon, color_hex):
            if not inv_dict:
                st.info("Zatím žádné záznamy v této kategorii.")
                return
                
            for i_id, i_data in reversed(list(inv_dict.items())):
                # Zjistíme, na kolik objednávek se tento záznam uplatnil
                assigned_count = 0
                for oid, o_data in db_orders.items():
                    if 'finance' in o_data:
                        for f in o_data['finance']:
                            if f['inv_id'] == i_id:
                                assigned_count += 1
                                break
                                
                str_celkem = f"{int(i_data['total_transp_czk']):,} Kč".replace(',', ' ')
                
                # --- NOVINKA: Zobrazení hezkého názvu rozvozu místo strojového ID ---
                zobrazene_jmeno = i_data.get('route_name', i_data['supplier'])
                if i_data['supplier'] == "Dopravce (Import CSV/Excel)":
                    zobrazene_jmeno = f"Sběrná faktura: {i_id}"
                
                html_obsah = f"""<div style='border: 1px solid {color_hex}; border-radius: 8px; padding: 15px; margin-bottom: 5px; background-color: #ffffff;'>
<h4 style='margin-top: 0; color: {color_hex};'>{icon} {zobrazene_jmeno}</h4>
<div style='font-size: 0.8em; color: #bdc3c7; margin-top: -10px; margin-bottom: 10px;'>Interní ID záznamu: {i_id}</div>
<div style='display:flex; justify-content:space-between; border-top: 1px solid #eee; padding-top: 10px; margin-top: 10px;'>
    <div>
        <span style='font-size: 0.85em; color: #7f8c8d;'>CELKOVÝ NÁKLAD TRASY</span><br>
        <b style='font-size: 1.2em;'>{str_celkem}</b>
    </div>
    <div style='text-align: right;'>
        <span style='font-size: 0.85em; color: #7f8c8d;'>ÚSPĚŠNĚ ROZPOČÍTÁNO NA</span><br>
        <b style='font-size: 1.1em; color:#2c3e50;'>{assigned_count} objednávek</b>
    </div>
</div>
</div>"""
                st.markdown(html_obsah, unsafe_allow_html=True)
                
                with st.expander("⚙️ Správa (Úprava ceny / Storno)"):
                    if st.button(f"🗑️ Stornovat a smazat zúčtování", key=f"storno_{i_id}", use_container_width=True):
                        del db_invoices[i_id]
                        for oid, o_data in db_orders.items():
                            if 'finance' in o_data:
                                o_data['finance'] = [f for f in o_data['finance'] if f['inv_id'] != i_id]
                        save_db(INVOICES_DB_FILE, db_invoices)
                        save_db(ORDERS_DB_FILE, db_orders)
                        st.success("Záznam byl úspěšně stornován a veškeré náklady vymazány z dotčených objednávek!")
                        time.sleep(1.5)
                        st.rerun()

                    # --- NOVINKA: Úprava ceny dopravy / rozvozu zpětně ---
                    st.markdown("#### 📝 Dodatečná úprava nákladů")
                    with st.form(f"edit_transp_{i_id}"):
                        new_transp_cost = st.number_input("Celkový náklad (CZK):", value=float(i_data.get('total_transp_czk', 0)), step=100.0)
                        
                        if st.form_submit_button("💾 Uložit a přepočítat do objednávek", type="primary"):
                            i_data['total_transp_czk'] = new_transp_cost
                            i_data['raw_total_transp_fc'] = new_transp_cost
                            db_invoices[i_id] = i_data
                            
                            # Pokud byla trasa úspěšně rozpočítána na nějaké objednávky
                            if assigned_count > 0:
                                cost_per_order = new_transp_cost / assigned_count
                                for oid, o_data in db_orders.items():
                                    if 'finance' in o_data:
                                        for f in o_data['finance']:
                                            if f['inv_id'] == i_id:
                                                f['transp_czk'] = cost_per_order
                                                
                                                # Aktualizace textu u rozvozů (aby seděla matematika v závorce)
                                                if "Vlastní rozvoz" in f['product'] and "[" in f['product']:
                                                    base_name = f['product'].split('[')[0].strip()
                                                    f['product'] = f"{base_name} [1/{assigned_count} z nákladů {int(new_transp_cost)} Kč]"
                            
                            save_db(INVOICES_DB_FILE, db_invoices)
                            save_db(ORDERS_DB_FILE, db_orders)
                            st.success("Náklady na dopravu/rozvoz byly upraveny a automaticky přepočítány!")
                            time.sleep(1.5)
                            st.rerun()
                st.write("")

        # Vykreslení zbylých dvou záložek
        with t_doprava:
            render_transport_invoices(inv_doprava, "🚚", "#e67e22")
            
        with t_rozvozy:
            render_transport_invoices(inv_rozvozy, "🚐", "#8e44ad")

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
        # --- NOVINKA: Ignorování starých objednávek (aby doživotně zmizely z přehledů) ---
        if d.get('ignore_accounting', False): 
            continue
            
        if q_ord and q_ord not in oid.lower(): continue
        if q_stat != "Všechny" and d['status'] != q_stat: continue
        
        # --- KONTROLA KOMPLETNOSTI (E-shop vs. Faktury) ---
        is_canceled = any(x in d['status'].lower() for x in ['zrušen', 'stornov', 'vrácen'])
        
        req_qty = {}
        for p in d.get('products', []):
            req_qty[p['name']] = req_qty.get(p['name'], 0) + p['qty']
            
        ass_qty = {}
        has_outbound = False
        for f in d.get('finance', []):
            ass_qty[f['product']] = ass_qty.get(f['product'], 0) + f['qty']
            # Detekce, zda už má objednávka naúčtovanou dopravu ven
            if str(f.get('product', '')).startswith("🚚 Doprava") or str(f.get('product', '')).startswith("🚐 Vlastní rozvoz"):
                has_outbound = True
            
        tot_req = sum(req_qty.values())
        tot_ass = sum(min(ass_qty.get(p, 0), req_qty[p]) for p in req_qty)
        
        if is_canceled:
            stav_uctovani = "⚪ Zrušeno"
        elif tot_req == 0:
            stav_uctovani = "✅ Kompletní"
        elif tot_ass >= tot_req:
            # Máme přiřazené veškeré zboží. Máme už i dopravu ven?
            if has_outbound:
                stav_uctovani = "✅ Kompletní"
            else:
                stav_uctovani = "📦 Zboží OK, chybí doprava"
        elif tot_ass > 0 or has_outbound:
            stav_uctovani = "⚠️ Chybí část"
        else:
            stav_uctovani = "❌ Chybí vše"
            
        # --- VÝPOČTY ZISKU (ROZDĚLENÍ INBOUND / OUTBOUND DOPRAVY) ---
        prodej = d.get('sell_price_czk', 0)
        
        naklad_zbozi = 0.0
        doprava_dodavatele = 0.0
        doprava_zakaznik = 0.0
        
        for f in d.get('finance', []):
            naklad_zbozi += f['buy_czk']
            p_name = str(f.get('product', ''))
            
            # Pokud položka začíná ikonou dopravy k zákazníkovi, oddělíme ji
            if p_name.startswith("🚚 Doprava") or p_name.startswith("🚐 Vlastní rozvoz"):
                doprava_zakaznik += f['transp_czk']
            else:
                doprava_dodavatele += f['transp_czk']
                
        celk_naklad = naklad_zbozi + doprava_dodavatele + doprava_zakaznik
        
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
            marze_str = "Čeká se na náklady..."
            marze_pct_str = "---"
            
        table_data.append({
            "Objednávka": oid,
            "Stav (E-shop)": d['status'],
            "Stav účtování": stav_uctovani,
            "Tržba (CZK)": f"{int(prodej):,} Kč".replace(',', ' '),
            "Náklad zboží": f"{int(naklad_zbozi):,} Kč".replace(',', ' ') if naklad_zbozi > 0 else "0 Kč",
            "Doprava dodavatele": f"{int(doprava_dodavatele):,} Kč".replace(',', ' ') if doprava_dodavatele > 0 else "0 Kč",
            "Doprava k zákazníkovi": f"{int(doprava_zakaznik):,} Kč".replace(',', ' ') if doprava_zakaznik > 0 else "0 Kč",
            "Hrubá marže": marze_str,
            "Marže (%)": marze_pct_str,
            "Přiřazené faktury": inv_str,
            "V_importu": d.get('in_last_import', True) # Skrytý stav pro hlídače
        })
        
    if not table_data:
        st.warning("Žádná objednávka nevyhovuje filtrům.")
    else:
        df_profit = pd.DataFrame(table_data)
        
        # --- ROZDĚLENÍ DAT DO KATEGORIÍ ---
        df_comp = df_profit[df_profit['Stav účtování'] == '✅ Kompletní'].drop(columns=['V_importu'])
        df_nosh = df_profit[df_profit['Stav účtování'] == '📦 Zboží OK, chybí doprava'].drop(columns=['V_importu'])
        df_part = df_profit[df_profit['Stav účtování'] == '⚠️ Chybí část'].drop(columns=['V_importu'])
        df_miss = df_profit[df_profit['Stav účtování'] == '❌ Chybí vše'].drop(columns=['V_importu'])
        df_canc = df_profit[df_profit['Stav účtování'] == '⚪ Zrušeno'].drop(columns=['V_importu'])
        
        # --- HLÍDAČ NEDODĚLKŮ ---
        df_ghost = df_profit[(df_profit['V_importu'] == False) & (df_profit['Stav účtování'].isin(['⚠️ Chybí část', '❌ Chybí vše', '📦 Zboží OK, chybí doprava']))].drop(columns=['V_importu'])

        if not df_ghost.empty:
            st.markdown(f"<div style='background-color: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; margin-bottom: 15px; border-left: 5px solid #f5c6cb;'><b>🚨 HLÍDAČ NEDODĚLKŮ:</b> Nalezli jsme <b>{len(df_ghost)} objednávek</b>, které už e-shop neeviduje v aktivním exportu (jsou zřejmě vyřízené), ale <b>stále u nich nemáte přiřazené všechny náklady!</b> Najdete je v první záložce níže.</div>", unsafe_allow_html=True)
        
        # --- PODZÁLOŽKY S POČÍTADLEM ---
        t_ghost, t_comp, t_nosh, t_part, t_miss, t_canc = st.tabs([
            f"🚨 HLÍDAČ ({len(df_ghost)})", 
            f"✅ Kompletní marže ({len(df_comp)})", 
            f"📦 Zboží OK, chybí doprava ({len(df_nosh)})",
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
            elif row['Stav účtování'] == '📦 Zboží OK, chybí doprava':
                return ['background-color: #e8f8f5; color: #117a65; font-weight: bold'] * len(row)
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
        with t_ghost:
            if not df_ghost.empty:
                with st.expander("🧹 HROMADNÝ ÚKLID STARÝCH OBJEDNÁVEK", expanded=True):
                    st.info("Vyberte objednávky z minulosti, ke kterým už nechcete dohledávat faktury. Systém jim dá skrytou nálepku a už nikdy je nebude v účetnictví vyžadovat.")
                    ghost_oids = df_ghost['Objednávka'].tolist()
                    
                    c_sel, c_btn = st.columns([4, 1])
                    to_ignore = c_sel.multiselect("Vyberte objednávky k trvalému ignorování:", ghost_oids)
                    
                    c_btn.markdown("<br>", unsafe_allow_html=True)
                    if c_btn.button("🚫 Skrýt vybrané", type="primary", use_container_width=True):
                        if to_ignore:
                            for o in to_ignore:
                                db_orders[o]['ignore_accounting'] = True
                            save_db(ORDERS_DB_FILE, db_orders)
                            st.success(f"✅ {len(to_ignore)} objednávek bylo trvale vyřazeno z evidence!")
                            time.sleep(1.5)
                            st.rerun()
                        else:
                            st.error("Nevybrali jste žádnou objednávku.")
            render_table(df_ghost)
            
        with t_comp: render_table(df_comp)
        with t_nosh: render_table(df_nosh)
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
            
            # --- OPRAVA RENTGENU: Rozdělení dopravy na Inbound a Outbound ---
            celkovy_naklad_z = 0.0
            doprava_dodavatele = 0.0
            doprava_zakaznik = 0.0
            
            for f in d.get('finance', []):
                celkovy_naklad_z += f['buy_czk']
                p_name = str(f.get('product', ''))
                if p_name.startswith("🚚 Doprava") or p_name.startswith("🚐 Vlastní rozvoz"):
                    doprava_zakaznik += f['transp_czk']
                else:
                    doprava_dodavatele += f['transp_czk']
                    
            celkovy_naklad = celkovy_naklad_z + doprava_dodavatele + doprava_zakaznik
            celk_marze = celkovy_prodej - celkovy_naklad
            celk_marze_pct = (celk_marze / celkovy_prodej * 100) if celkovy_prodej > 0 else 0
            # -----------------------------------------------------------------
            
            if is_order_complete:
                barva_top = "#c0392b" if celk_marze < 0 else "#27ae60"
                marze_top_html = f"<span style='color:{barva_top}; font-size:1.1em;'><b>{int(celk_marze)} Kč ({celk_marze_pct:.1f} %)</b></span>"
            else:
                marze_top_html = "<span style='color:#e67e22; font-size:1.1em;'><b>Zatím neznámá (Čeká se na přiřazení všech faktur)</b></span>"
            
            st.markdown(f"""
            <div style='background-color: #f8f9f9; padding: 15px; border-radius: 8px; border-left: 5px solid #2980b9; margin-bottom: 20px;'>
                <h4 style='margin:0; color:#2c3e50;'>Detail objednávky: {detail_oid} | Stav: {d['status']}</h4>
                <b>Celková čistá tržba:</b> {int(celkovy_prodej)} Kč <span style='font-size:0.9em; color:#7f8c8d;'>(Z toho za zboží: {int(celkovy_prodej_zbozi)} Kč, Za dopravu od zák.: {int(prijem_doprava)} Kč)</span><br>
                <b>Zatím zadané náklady:</b> {int(celkovy_naklad)} Kč <span style='font-size:0.9em; color:#7f8c8d;'>(Z toho nákup: {int(celkovy_naklad_z)} Kč, Doprava dodavatele: {int(doprava_dodavatele)} Kč, Doprava k zákazníkovi: {int(doprava_zakaznik)} Kč)</span><br>
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

    # =======================================================
# TAB 4: IMPORT DOPRAVY (TOPTRANS / PPL / DPD)
# =======================================================
with tab_transport:
    st.markdown("### 🚚 Hromadný import dopravy (Toptrans / PPL / DPD)")
    st.info("Nahrajte soubor. Systém najde sloupeček s číslem objednávky (u Toptransu typicky 'Označení'), sloupeček s cenou a bleskově vše spáruje. Pokud se stane chyba, můžete import smazat v historii faktur.")
    tt_file = st.file_uploader("Vyberte soubor od dopravce", type=['csv', 'xls', 'xlsx'])

    if tt_file is not None:
        try:
            if tt_file.name.lower().endswith('.csv'):
                try: df_tt = pd.read_csv(tt_file, sep=';', encoding='utf-8')
                except:
                    tt_file.seek(0)
                    try: df_tt = pd.read_csv(tt_file, sep=',', encoding='utf-8')
                    except:
                        tt_file.seek(0)
                        df_tt = pd.read_csv(tt_file, sep=';', encoding='cp1250')
            else:
                df_tt = pd.read_excel(tt_file)

            st.success("Soubor úspěšně načten! Zkontrolujte náhled a přiřaďte správné sloupce.")
            st.dataframe(df_tt.head(3), use_container_width=True)

            c_inv, c_ref, c_price = st.columns(3)
            tt_inv_id = c_inv.text_input("Číslo sběrné faktury (např. TT-2024-12):")
            
            options = ["-- Vyberte sloupec --"] + list(df_tt.columns)
            
            # Chytrá detekce sloupců na základě Toptrans formátu
            default_ref = "Označení" if "Označení" in options else "-- Vyberte sloupec --"
            default_price = "Celková cena faktura" if "Celková cena faktura" in options else ("Cena přepravy" if "Cena přepravy" in options else "-- Vyberte sloupec --")
            
            idx_ref = options.index(default_ref) if default_ref in options else 0
            idx_price = options.index(default_price) if default_price in options else 0

            ref_col = c_ref.selectbox("Kde je ČÍSLO OBJEDNÁVKY / VS?", options, index=idx_ref)
            price_col = c_price.selectbox("Kde je CENA (bez DPH)?", options, index=idx_price)

            if st.button("🔄 Spárovat a zapsat dopravní náklady", type="primary", use_container_width=True):
                if not tt_inv_id:
                    st.error("Musíte zadat evidenční číslo této sběrné faktury!")
                elif tt_inv_id in db_invoices:
                    st.error("Faktura s tímto číslem už v historii existuje! Zvolte jiné.")
                elif ref_col == "-- Vyberte sloupec --" or price_col == "-- Vyberte sloupec --":
                    st.error("Musíte vybrat správné sloupce pro číslo objednávky a cenu!")
                else:
                    matched = 0
                    total_transp_sum = 0.0
                    not_found = []

                    for _, row in df_tt.iterrows():
                        # Ošetření prázdných řádků
                        raw_ref_val = row[ref_col]
                        if pd.isna(raw_ref_val):
                            continue
                            
                        # Toptrans občas dělá z čísel v Excelu desetinná čísla (92601168.0), tohle to ořeže
                        raw_ref_str = str(raw_ref_val).split('.')[0].strip()
                        if not raw_ref_str or raw_ref_str.lower() == 'nan': continue
                        
                        found_oid = None
                        clean_ref = ''.join(filter(str.isdigit, raw_ref_str))
                        
                        if clean_ref and len(clean_ref) > 3:
                            # BEZPEČNÉ PÁROVÁNÍ: Musí to být naprostá shoda (nebo přesný konec čísla), aby se nespárovalo něco náhodně
                            for oid in db_orders.keys():
                                clean_oid = ''.join(filter(str.isdigit, oid))
                                if clean_ref == clean_oid or clean_oid.endswith(clean_ref):
                                    found_oid = oid
                                    break

                        if found_oid:
                            try:
                                raw_price = str(row[price_col]).replace(' ', '').replace(',', '.').replace('Kč', '').replace('CZK', '').strip()
                                clean_price = float(raw_price)
                                
                                if clean_price > 0:
                                    finance_entry = {
                                        "inv_id": tt_inv_id,
                                        "product": "🚚 Doprava (Import z CSV)",
                                        "qty": 1,
                                        "buy_czk": 0.0,
                                        "transp_czk": clean_price
                                    }
                                    if 'finance' not in db_orders[found_oid]:
                                        db_orders[found_oid]['finance'] = []
                                    db_orders[found_oid]['finance'].append(finance_entry)
                                    total_transp_sum += clean_price
                                    matched += 1
                            except:
                                not_found.append(f"{raw_ref_str} (Chyba formátu ceny)")
                        else:
                            not_found.append(raw_ref_str)

                    if matched > 0:
                        # Vytvoření záznamu v historii faktur pro budoucí kontrolu či smazání
                        new_inv = {
                            "supplier": "Dopravce (Import CSV/Excel)",
                            "currency": "CZK",
                            "rate": 1.0,
                            "total_goods_czk": 0.0,
                            "total_transp_czk": total_transp_sum,
                            "assigned_goods_czk": 0.0,
                            "stock_remainder_czk": 0.0, 
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "raw_total_goods_fc": 0.0,
                            "raw_total_transp_fc": total_transp_sum,
                            "raw_is_incl_vat": False,
                            "items": [] 
                        }
                        db_invoices[tt_inv_id] = new_inv
                        
                        save_db(INVOICES_DB_FILE, db_invoices)
                        save_db(ORDERS_DB_FILE, db_orders)

                        st.success(f"✅ Úspěšně spárováno {matched} zásilek! Celková naúčtovaná doprava: {total_transp_sum} Kč.")
                        if not_found:
                            st.warning(f"Nenalezeno v databázi u {len(not_found)} záznamů: {', '.join(not_found[:15])}")
                    else:
                        st.error("Nepodařilo se spárovat ani jednu objednávku. Zkontrolujte, zda jste v Shoptetu tyto objednávky už stáhli.")

        except Exception as e:
            st.error(f"Nepodařilo se zpracovat soubor: {e}")

            st.markdown("---")
    st.markdown("### 🚐 Náklady z vlastních rozvozů (Z Plánovače tras)")
    st.info("Systém se podívá do historie dokončených rozvozů. Níže se zobrazí ty, u kterých máte vyplněné náklady, ale ještě nejsou zaúčtované. Můžete u nich odškrtnout objednávky, které se reálně nedoručily (zákazník nebyl doma), a systém rozpočítá náklady jen mezi ty úspěšné.")

    routes = load_routes_db()
    
    # Filtrace jen těch rozvozů, které jsou odjeté a ještě nebyly naúčtovány
    unprocessed_routes = [r for r in routes if r.get('status') == 'completed' and f"ROZVOZ-{r.get('id')}" not in db_invoices]

    if not unprocessed_routes:
        st.success("✅ Všechny odjeté rozvozy z Plánovače už mají náklady rozpočítané a zapsané!")
    else:
        for r in unprocessed_routes:
            r_id = r.get('id')
            inv_id = f"ROZVOZ-{r_id}"
            costs = r.get('costs', {})
            total_cost = float(costs.get('fuel', 0)) + float(costs.get('driver', 0)) + float(costs.get('accommodation', 0)) + float(costs.get('other', 0))
            
            with st.expander(f"🚐 Zpracovat rozvoz: {r.get('name').split('|')[0].strip()} | Náklady: {int(total_cost)} Kč", expanded=True):
                if total_cost <= 0:
                    st.warning("⚠️ U tohoto rozvozu nejsou v Plánovači zadány žádné náklady. Přejděte do Plánovače (záložka Historie -> Vyúčtování) a doplňte je.")
                    continue
                    
                st.write("Vyberte objednávky, které se **reálně doručily** (odškrtněte ty, které se vrátily zpět na sklad):")
                
                delivered_oids = []
                for row in r.get('itinerary_data', []):
                    oid = row.get('Číslo objednávky')
                    if oid in ['START', 'CÍL']: continue
                    
                    status = r.get('details', {}).get(oid, {}).get('dispatch_status', '')
                    # Pokud jsi už v dispečinku dal "Zrušeno", odškrtne se to rovnou samo
                    def_val = (status != 'Zrušeno')
                    
                    # Vizuální checklist
                    is_delivered = st.checkbox(f"📦 {oid} - {row.get('Příjemce')}", value=def_val, key=f"chk_del_{r_id}_{oid}")
                    if is_delivered:
                        delivered_oids.append(oid)
                        
                if st.button(f"💾 Rozpočítat {int(total_cost)} Kč na {len(delivered_oids)} doručených objednávek", key=f"btn_proc_{r_id}", type="primary"):
                    if not delivered_oids:
                        st.error("Musíte vybrat alespoň jednu doručenou objednávku!")
                    else:
                        pocet_dorucenych = len(delivered_oids)
                        cost_per_order = total_cost / pocet_dorucenych
                        
                        added_to_some = False
                        not_found_list = []
                        
                        for oid in delivered_oids:
                            clean_oid_route = ''.join(filter(str.isdigit, str(oid)))
                            found_oid = None
                            
                            # Bezpečné párování přes čistá čísla
                            if clean_oid_route and len(clean_oid_route) > 3:
                                for db_oid in db_orders.keys():
                                    clean_oid_db = ''.join(filter(str.isdigit, db_oid))
                                    if clean_oid_route == clean_oid_db or clean_oid_db.endswith(clean_oid_route):
                                        found_oid = db_oid
                                        break
                                        
                            if found_oid:
                                nazev_rozvozu = r.get('name', 'Neznámý').split('|')[0].strip()
                                matematika_str = f" [1/{pocet_dorucenych} z nákladů {int(total_cost)} Kč]"
                                
                                finance_entry = {
                                    "inv_id": inv_id,
                                    "product": f"🚐 Vlastní rozvoz ({nazev_rozvozu}){matematika_str}",
                                    "qty": 1,
                                    "buy_czk": 0.0,
                                    "transp_czk": cost_per_order
                                }
                                if 'finance' not in db_orders[found_oid]:
                                    db_orders[found_oid]['finance'] = []
                                db_orders[found_oid]['finance'].append(finance_entry)
                                added_to_some = True
                            else:
                                not_found_list.append(oid)
                                
                        # --- ÚPRAVA: Uložíme fakturu vždy, i když jsou všechny objednávky externí ---
                        db_invoices[inv_id] = {
                            "supplier": f"Vlastní rozvoz (Dispečink)",
                            "route_name": r.get('name', 'Neznámý rozvoz'), # <-- NOVINKA: Uložení lidského názvu
                            "currency": "CZK",
                            "rate": 1.0,
                            "total_goods_czk": 0.0,
                            "total_transp_czk": total_cost,
                            "assigned_goods_czk": 0.0,
                            "stock_remainder_czk": 0.0, 
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "raw_total_goods_fc": 0.0,
                            "raw_total_transp_fc": total_cost,
                            "raw_is_incl_vat": False,
                            "items": []
                        }
                        
                        if added_to_some:
                            save_db(INVOICES_DB_FILE, db_invoices)
                            save_db(ORDERS_DB_FILE, db_orders)
                            
                            if not_found_list:
                                st.warning(f"Rozvoz zpracován, ale některé objednávky (externí) se v účetnictví nenašly: {', '.join(not_found_list)}")
                            else:
                                st.success("✅ Rozvoz úspěšně zpracován a náklady exaktně rozpočítány!")
                        else:
                            save_db(INVOICES_DB_FILE, db_invoices)
                            st.warning(f"Rozvoz zpracován (čistě externí jízda). Náklady {int(total_cost)} Kč byly uloženy do historie jako nezařazená doprava.")
                            
                        time.sleep(2.5)
                        st.rerun()
                        # ----------------------------------------------------------------------------