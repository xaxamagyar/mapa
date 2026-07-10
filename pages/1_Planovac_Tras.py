import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import BeautifyIcon, Draw
import requests
import io
import time
import os
import json
import re
import math
import base64
import random
import copy
from PIL import Image
from urllib.parse import quote
from datetime import datetime, timedelta, time as datetime_time
from streamlit_sortables import sort_items
from fpdf import FPDF
import matplotlib.pyplot as plt
from geopy.distance import geodesic

# Importy pro generátor štítků
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

st.set_page_config(page_title="Plánovač tras pro řidiče", layout="wide")

# ==============================================================================
# --- VIZUÁLNÍ VYLEPŠENÍ: ANTI-RAGE-CLICK (ANIMOVANÝ LOADER) ---
# ==============================================================================

# --- NOVINKA: NEVIDITELNÝ TEP SRDCE PROTI ODPOJOVÁNÍ ---
st.html("<script>window.parent.document.getElementById('top_target').scrollIntoView({behavior: 'smooth', block: 'start'});</script>")
# -------------------------------------------------------

st.markdown("""
<style>
    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }
    
    [data-testid="stStatusWidget"] {
        position: fixed !important;
        top: 50% !important;
        left: 50% !important;
        transform: translate(-50%, -50%) !important;
        background-color: #2c3e50 !important;
        padding: 25px 40px !important;
        border-radius: 15px !important;
        z-index: 999999 !important;
        box-shadow: 0px 10px 40px rgba(0,0,0,0.6) !important;
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        justify-content: center !important;
        pointer-events: auto !important;
    }
    
    [data-testid="stStatusWidget"] label { display: none !important; }
    [data-testid="stStatusWidget"] button { display: none !important; }

    [data-testid="stStatusWidget"]::before {
        content: "";
        box-sizing: border-box;
        width: 35px;
        height: 35px;
        border: 4px solid rgba(255, 255, 255, 0.3);
        border-top: 4px solid #3498db;
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin-right: 20px;
    }

    [data-testid="stStatusWidget"]::after {
        content: "Pracuji, momentíček prosím...";
        color: #ffffff !important;
        font-size: 22px !important;
        font-weight: bold !important;
        letter-spacing: 0.5px !important;
    }
</style>
""", unsafe_allow_html=True)


# --- FUNKCE PRO DATABÁZI A ZÁMKY ---
ROUTES_FILE = "saved_routes.json"
GEO_FILE = "geocode_cache.json"

try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
    GITHUB_REPO = st.secrets["GITHUB_REPO"]  
except:
    GITHUB_TOKEN = ""
    GITHUB_REPO = ""

def get_github_headers(): 
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_json_from_github_or_local(file_path, default_type):
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        resp = requests.get(url, headers=get_github_headers())
        if resp.status_code == 200:
            try: return json.loads(base64.b64decode(resp.json()['content']).decode('utf-8'))
            except: return default_type()
        return default_type()
    else:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                try: return json.load(f)
                except: return default_type()
        return default_type()

def save_json_to_github_or_local(file_path, data_obj, commit_message):
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        headers = get_github_headers()
        resp = requests.get(url, headers=headers)
        sha = resp.json().get('sha') if resp.status_code == 200 else None
        content_b64 = base64.b64encode(json.dumps(data_obj, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
        payload = {"message": commit_message, "content": content_b64}
        if sha: payload["sha"] = sha
        requests.put(url, headers=headers, json=payload)
    else:
            with open(file_path, "w", encoding="utf-8") as f: 
                json.dump(data_obj, f, ensure_ascii=False, indent=2)

# ==============================================================================
# --- DATABÁZE TOPTRANS (EXCEL NA GITHUB) ---
# ==============================================================================
try: 
    TOPTRANS_REPO = st.secrets["TOPTRANS_REPO"]
except: 
    TOPTRANS_REPO = "xaxamagyar/toptrans-prevodnik"

@st.cache_data(show_spinner=False, ttl=3600)
def load_toptrans_db():
    """Stáhne Excel z GitHubu a sečte váhy a objemy balíků pro každý produkt."""
    if not GITHUB_TOKEN: 
        st.error("❌ Systém nemá k dispozici GITHUB_TOKEN ze Secrets!")
        return {}
        
    url = f"https://api.github.com/repos/{TOPTRANS_REPO}/contents/products.xlsx"
    resp = requests.get(url, headers=get_github_headers())
    
    if resp.status_code == 200:
        try:
            content = base64.b64decode(resp.json()['content'])
            df = pd.read_excel(io.BytesIO(content))
            
            db = {}
            if 'ZBOZI_2' in df.columns:
                for _, row in df.iterrows():
                    nazev = str(row['ZBOZI_2']).strip()
                    if nazev not in db:
                        db[nazev] = {'Vaha': 0.0, 'Objem': 0.0}
                    
                    v = float(row['ZBOZI_HMOTNOST']) if pd.notna(row.get('ZBOZI_HMOTNOST')) else 0.0
                    dl = float(row['ZBOZI_DELKA']) if pd.notna(row.get('ZBOZI_DELKA')) else 0.0
                    si = float(row['ZBOZI_SIRKA']) if pd.notna(row.get('ZBOZI_SIRKA')) else 0.0
                    vy = float(row['ZBOZI_VYSKA']) if pd.notna(row.get('ZBOZI_VYSKA')) else 0.0
                    
                    db[nazev]['Vaha'] += v
                    db[nazev]['Objem'] += (dl * si * vy)
            return db
        except Exception as e:
            st.error(f"❌ Staženo z GitHubu, ale selhalo čtení Excelu: {e}")
    else:
        st.error(f"❌ GitHub API vrátilo chybu {resp.status_code}: {resp.json().get('message', 'Neznámý důvod')}")
        
    return {}

def save_toptrans_product(shoptet_nazev, baliky_data, kuryr_nazev):
    """Nahraje balíky do Excelu ve stejném formátu jako webový převodník."""
    url = f"https://api.github.com/repos/{TOPTRANS_REPO}/contents/products.xlsx"
    headers = get_github_headers()
    resp = requests.get(url, headers=headers)
    
    df = pd.DataFrame(columns=['ZBOZI_2', 'ZBOZI_NAZEV', 'ZBOZI_HMOTNOST', 'ZBOZI_DELKA', 'ZBOZI_SIRKA', 'ZBOZI_VYSKA'])
    sha = None
    if resp.status_code == 200:
        try:
            sha = resp.json().get('sha')
            content = base64.b64decode(resp.json()['content'])
            df = pd.read_excel(io.BytesIO(content))
        except: pass
        
    nove_radky = []
    pocet = len(baliky_data)
    for i, b in enumerate(baliky_data):
        oznaceni = f" {i+1}/{pocet}" if pocet > 1 else ""
        nove_radky.append({
            'ZBOZI_2': shoptet_nazev,
            'ZBOZI_NAZEV': f"{kuryr_nazev}{oznaceni}",
            'ZBOZI_HMOTNOST': b['vaha'],
            'ZBOZI_DELKA': b['delka'],
            'ZBOZI_SIRKA': b['sirka'],
            'ZBOZI_VYSKA': b['vyska']
        })
        
    df = pd.concat([df, pd.DataFrame(nove_radky)], ignore_index=True)
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
        
    content_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    payload = {"message": f"Doplněn produkt do Toptrans databáze: {shoptet_nazev}", "content": content_b64}
    if sha: payload["sha"] = sha
    
    r_put = requests.put(url, headers=headers, json=payload)
    if r_put.status_code in [200, 201]:
        st.cache_data.clear() 
        return True
    else:
        st.error(f"❌ Chyba při ukládání na GitHub: {r_put.status_code} - {r_put.text}")
        time.sleep(4)
        return False

def calculate_toptrans_price(zip_from, zip_to, weight, volume, cod_value=0, fallback_address=""):
    """Odešle dotaz na API Toptrans a správně přečte zanořenou odpověď."""
    try:
        user = st.secrets["TOPTRANS_USER"]
        password = st.secrets["TOPTRANS_PASS"]
    except:
        return None, "Chybí přihlašovací údaje k Toptrans v nastavení (secrets)."

    import re

    def clean_psc(val):
        c = re.sub(r'\D', '', str(val))
        if len(c) == 5: return c
        return None

    clean_zip_to = clean_psc(zip_to)
    
    if not clean_zip_to and fallback_address:
        matches = re.findall(r'\b\d{3}\s?\d{2}\b', str(fallback_address))
        if matches:
            clean_zip_to = re.sub(r'\D', '', matches[-1])

    if not clean_zip_to:
        return None, f"Zákazníkovi chybí platné 5místné PSČ. Adresa: '{fallback_address}'"

    clean_zip_from = clean_psc(zip_from)
    
    country_code = "CZ"
    if clean_zip_to.startswith('0') or clean_zip_to.startswith('8') or clean_zip_to.startswith('9'):
        country_code = "SK"

    payload = {
        "term_id": 1,
        "loading": {
            "address": {
                "country": "CZ",
                "zip": clean_zip_from
            }
        },
        "discharge": {
            "address": {
                "country": country_code,
                "zip": clean_zip_to
            }
        },
        "kg": int(weight) if weight > 0 else 1,
        "m3": round(float(volume), 4) if volume > 0 else 0.01
    }
    
    if cod_value and float(cod_value) > 0:
        payload["cash_on_delivery"] = {
            "type": 1,
            "price": int(float(cod_value))
        }
    
    try:
        url = "https://zp.toptrans.cz/api/json/order/price/"
        resp = requests.post(url, json=payload, auth=(user, password), timeout=5)
        
        if resp.status_code == 200:
            try:
                result_json = resp.json()
                
                # --- SPRÁVNÉ ROZBALENÍ TOPTRANS OBÁLKY ---
                import json
                
                if result_json.get("status") == "ok" and "data" in result_json:
                    # Zkusíme najít cenu pod různými možnými názvy
                    data_obj = result_json["data"]
                    if "price" in data_obj and data_obj["price"] is not None:
                        return data_obj["price"], None
                    elif "PRICE" in data_obj and data_obj["PRICE"] is not None:
                        return data_obj["PRICE"], None
                    elif "total_price" in data_obj and data_obj["total_price"] is not None:
                        return data_obj["total_price"], None
                    else:
                        # Pokud se to jmenuje ještě jinak, vypíšeme úplně všechno, co v obálce je!
                        return None, f"Toptrans poslal data, ale nevíme jak se jmenuje cena. Obsah: {json.dumps(data_obj, ensure_ascii=False)}"
                
                if "errors" in result_json and result_json["errors"]:
                    return None, f"Toptrans odmítl nacenit: {json.dumps(result_json['errors'], ensure_ascii=False)}"
                
                return None, f"Nepodařilo se přečíst odpověď: {json.dumps(result_json, ensure_ascii=False)}"
                # ------------------------------------------
                
            except ValueError:
                return None, f"Toptrans nevrátil JSON. Tajná odpověď: {resp.text[:100]}"
        else:
            return None, f"Chyba serveru {resp.status_code}: {resp.text[:50]}"
    except Exception as e:
        return None, f"Kritická chyba spojení: {e}"

@st.dialog("📏 Neznámý produkt - Doplnění rozměrů", width="large")
def toptrans_product_dialog(product_name, zip_to, cod_val, order_id, psc_skladu="36236"):
    st.warning(f"Produkt **{product_name}** nemá v databázi nastavené rozměry.")
    
    kuryr_nazev = st.text_input("Základní označení pro kurýra:", value=product_name[:30])
    pocet_baliku = st.number_input("Počet různých balíků (krabic) pro tento produkt:", min_value=1, value=1, step=1)
    
    baliky_data = []
    for i in range(pocet_baliku):
        with st.container(border=True):
            st.markdown(f"**📦 Balík {i+1}**")
            c1, c2, c3, c4 = st.columns(4)
            v = c1.number_input("Hmotnost (kg)", min_value=0.1, value=15.0, step=0.5, key=f"v_{i}")
            d = c2.number_input("Délka (m)", min_value=0.01, value=1.2, step=0.05, key=f"d_{i}")
            s = c3.number_input("Šířka (m)", min_value=0.01, value=0.8, step=0.05, key=f"s_{i}")
            h = c4.number_input("Výška (m)", min_value=0.01, value=0.5, step=0.05, key=f"h_{i}")
            baliky_data.append({"vaha": v, "delka": d, "sirka": s, "vyska": h})
    
    c_save, c_skip = st.columns(2)
    if c_save.button("💾 Uložit do databáze a vypočítat", type="primary", use_container_width=True):
        if not kuryr_nazev.strip():
            st.error("Označení pro kurýra musí být vyplněné!")
        else:
            with st.spinner("Ukládám do Excelu na GitHubu..."):
                success = save_toptrans_product(product_name, baliky_data, kuryr_nazev)
            
            if success:
                with st.spinner("Počítám cenu u Toptransu..."):
                    tot_w = sum(b['vaha'] for b in baliky_data)
                    tot_v = sum(b['delka'] * b['sirka'] * b['vyska'] for b in baliky_data)
                    price, err = calculate_toptrans_price(psc_skladu, zip_to, tot_w, tot_v, cod_val)
                    
                    if price is not None:
                        st.session_state[f"tt_price_{order_id}"] = price
                        
                    # --- NOVINKA: ODMÁZNUTÍ Z FRONTY ---
                    if 'missing_queue' in st.session_state and st.session_state['missing_queue']:
                        st.session_state['missing_queue'] = [q for q in st.session_state['missing_queue'] if q[0] != product_name]
                    st.rerun()
        
    if c_skip.button("⏭️ Přeskočit (nepočítat)", use_container_width=True):
        # --- NOVINKA: ODMÁZNUTÍ Z FRONTY ---
        if 'missing_queue' in st.session_state and st.session_state['missing_queue']:
            st.session_state['missing_queue'] = [q for q in st.session_state['missing_queue'] if q[0] != product_name]
        st.rerun()
# ==============================================================================

def load_routes(): 
    return load_json_from_github_or_local(ROUTES_FILE, list)

def save_routes(routes): 
    # Zvýšeno z 10 na 200 rozvozů. Historie už nebude mizet a aplikace zůstane bleskově rychlá.
    save_json_to_github_or_local(ROUTES_FILE, routes[-200:], f"Rozvozy {datetime.now().strftime('%H:%M:%S')}")

def load_geo_cache(): 
    return load_json_from_github_or_local(GEO_FILE, dict)

def save_geo_cache(cache): 
    save_json_to_github_or_local(GEO_FILE, cache, f"GeoCache {datetime.now().strftime('%H:%M:%S')}")

# --- NOVINKA: HLÍDÁNÍ AKTIVNÍCH PŘIHLÁŠENÍ ---
ACTIVE_USERS_FILE = "active_users.json"
def load_active_users(): 
    return load_json_from_github_or_local(ACTIVE_USERS_FILE, dict)
def save_active_users(data): 
    save_json_to_github_or_local(ACTIVE_USERS_FILE, data, f"Login update {datetime.now().strftime('%H:%M:%S')}")

# --- NOVINKA: ZÁLOHY ROZPRACOVANÉ PRÁCE ---
DRAFTS_FILE = "user_drafts.json"
def load_drafts(): return load_json_from_github_or_local(DRAFTS_FILE, dict)
def save_drafts(data): save_json_to_github_or_local(DRAFTS_FILE, data, f"Draft {datetime.now().strftime('%H:%M')}")

# --- NOVINKA: AUDIT LOG (DENÍK AKCÍ) ---
AUDIT_LOG_FILE = "audit_log.json"
def load_audit_log(): 
    return load_json_from_github_or_local(AUDIT_LOG_FILE, list)
def save_audit_log(data): 
    save_json_to_github_or_local(AUDIT_LOG_FILE, data, f"AuditLog {datetime.now().strftime('%H:%M:%S')}")

def log_action(kategorie, popis, rozvoz="", objednavka=""):
    """Univerzální funkce, která zapíše jakoukoliv akci do deníku."""
    if 'st_user_name' not in st.session_state or not st.session_state['st_user_name']: return
    log_data = load_audit_log()
    if not isinstance(log_data, list): log_data = []
    
    cas = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    log_data.insert(0, {
        'Čas': cas, 
        'Uživatel': st.session_state['st_user_name'], 
        'Akce': kategorie, 
        'Rozvoz': rozvoz,
        'Objednávka': objednavka,
        'Detail': popis
    })
    
    save_audit_log(log_data[:1000]) # Držíme v paměti posledních 1000 akcí

def update_route_lock(r_id, lock=True, force_user=None):
    latest = load_routes()
    for r in latest:
        if r.get('id') == r_id:
            if lock:
                r['locked_by'] = force_user if force_user else st.session_state.get('st_user_name', 'Dispečer')
                r['locked_at'] = time.time()
            else:
                r['locked_by'] = ""
                r['locked_at'] = 0
    save_routes(latest)

def safe_save_route(new_route_data, delete_id=None):
    latest_routes = load_routes()
    if delete_id:
        latest_routes = [r for r in latest_routes if r['id'] != delete_id]
    if new_route_data:
        latest_routes.append(new_route_data)
    save_routes(latest_routes)

# ==============================================================================
# --- 1. BEZPEČNÉ SPOUŠTĚČE A INICIALIZACE VŠECH PROMĚNNÝCH ---
# ==============================================================================
if 'selected_orders' not in st.session_state: st.session_state['selected_orders'] = []  
if 'frozen_orders' not in st.session_state: st.session_state['frozen_orders'] = []
if 'loaded_route_orders' not in st.session_state: st.session_state['loaded_route_orders'] = []
if 'map_center' not in st.session_state: st.session_state['map_center'] = [49.8, 15.5]
if 'map_zoom' not in st.session_state: st.session_state['map_zoom'] = 7
if 'calc_main' not in st.session_state: st.session_state['calc_main'] = False
if 'last_processed_drawing' not in st.session_state: st.session_state['last_processed_drawing'] = ""
if 'last_clicked_tooltip' not in st.session_state: st.session_state['last_clicked_tooltip'] = None
if 'loaded_statuses' not in st.session_state: st.session_state['loaded_statuses'] = {}
if 'active_labels' not in st.session_state: st.session_state['active_labels'] = None
if 'active_costs' not in st.session_state: st.session_state['active_costs'] = None
if 'active_suggestion' not in st.session_state: st.session_state['active_suggestion'] = None
if 'sleep_after_oid' not in st.session_state: st.session_state['sleep_after_oid'] = None
if 'day2_start_time' not in st.session_state: st.session_state['day2_start_time'] = datetime_time(8, 0)
if 'split_marked_orders' not in st.session_state: st.session_state['split_marked_orders'] = []
if 'locked_pos_orders' not in st.session_state: st.session_state['locked_pos_orders'] = []

if st.session_state.get('trigger_clear'):
    if st.session_state.get('editing_route_id'): 
        update_route_lock(st.session_state['editing_route_id'], lock=False)
        
    st.session_state['selected_orders'] = []
    st.session_state['loaded_route_orders'] = []
    st.session_state['last_processed_drawing'] = ""
    st.session_state['last_clicked_tooltip'] = None
    st.session_state['loaded_statuses'] = {}
    st.session_state['calc_main'] = False
    st.session_state['active_labels'] = None
    st.session_state['active_dispatch'] = None
    st.session_state['active_costs'] = None
    st.session_state['active_suggestion'] = None
    if 'print_main' in st.session_state: del st.session_state['print_main']
    if 'editing_route_id' in st.session_state: del st.session_state['editing_route_id']
    
    st.session_state['st_route_name'] = ""
    st.session_state['st_driver_name'] = ""
    st.session_state['split_marked_orders'] = []
    st.session_state['locked_pos_orders'] = []
    st.session_state['trigger_clear'] = False

if st.session_state.get('trigger_load'):
    r_data = st.session_state['trigger_load']
    st.session_state['selected_orders'] = r_data.get('orders', []).copy()
    st.session_state['loaded_route_orders'] = r_data.get('orders', []).copy()
    st.session_state['last_processed_drawing'] = ""
    st.session_state['last_clicked_tooltip'] = None
    st.session_state['loaded_statuses'] = {}
    
    if 'details' in r_data:
        for o_id, det in r_data['details'].items():
            st.session_state[f"note_{o_id}"] = det.get("note", "")
            st.session_state['loaded_statuses'][o_id] = det.get("dispatch_status", "")
            if det.get("addr"): st.session_state[f"addr_{o_id}"] = det.get("addr", "")
            # --- NOVINKA: Načtení Toptrans ceny ---
            if det.get("tt_price"): st.session_state[f"tt_price_{o_id}"] = det.get("tt_price", 0)
                
    st.session_state['editing_route_id'] = r_data.get('id', '')
    st.session_state['st_start_address'] = r_data.get('start_address', 'Karlovy Vary')
    st.session_state['st_end_address'] = r_data.get('end_address', 'Karlovy Vary')
    st.session_state['st_start_point_name'] = r_data.get('start_point_name', 'SKLAD (Výjezd)')
    st.session_state['st_end_point_name'] = r_data.get('end_point_name', 'SKLAD (Návrat)')
    st.session_state['st_kasac_value'] = r_data.get('kasac_value', 2000)
    st.session_state['st_unload_time_min'] = r_data.get('unload_time_min', 15)
    
    if 'start_time_str' in r_data:
        try:
            h, m = map(int, r_data['start_time_str'].split(':'))
            st.session_state['st_start_time'] = datetime_time(h, m)
        except: pass
        
    # ZPĚTNÁ KOMPATIBILITA: Pokud načítáme velmi starý rozvoz, zkusíme data rozšifrovat z hlavního názvu
    raw_name = r_data.get('raw_route_name', '')
    driver = r_data.get('driver_name', '')
    if not raw_name and 'name' in r_data:
        parts = r_data['name'].split('|')
        if len(parts) > 0: raw_name = parts[0].strip()
        if len(parts) > 2: driver = parts[2].replace('Řidič:', '').strip()
        
    st.session_state['st_route_name'] = raw_name
    st.session_state['st_driver_name'] = driver
    
    if 'route_date' in r_data:
        if r_data['route_date'] == 'Neurčeno':
            st.session_state['st_route_date_unknown'] = True
            st.session_state['st_route_date'] = datetime.today()
        else:
            try: 
                st.session_state['st_route_date'] = datetime.strptime(r_data['route_date'], '%Y-%m-%d').date()
                st.session_state['st_route_date_unknown'] = False
            except: pass

    st.session_state['sleep_after_oid'] = r_data.get('sleep_after_oid')
    if 'day2_start_time_str' in r_data:
        try:
            dh, dm = map(int, r_data['day2_start_time_str'].split(':'))
            st.session_state['day2_start_time'] = datetime_time(dh, dm)
        except: pass
        
    st.session_state['scroll_to_editor'] = True  # <--- NOVINKA: Zapne skrolování
    st.session_state['trigger_load'] = None

# --- NOVINKA: PŘIHLAŠOVACÍ SYSTÉM (AUTENTIZACE S OCHRANOU DUPLICIT) ---
VALID_USERS = {
    "Velkej Luky": "1234",
    "Malej Luky": "1234",
    "Páťa": "1234",
    "Anuš": "1234",
    "Ruda": "1234",
    "Host": "1234"
}

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'session_token' not in st.session_state:
    st.session_state['session_token'] = ""

# Průběžná kontrola: Není uživatel přihlášen jinde?
if st.session_state['authenticated']:
    active_u = load_active_users()
    current_u = st.session_state.get('st_user_name', '')
    # Pokud v databázi existuje jiný klíč, než má tento prohlížeč, vykopneme ho
    if active_u.get(current_u) != st.session_state['session_token']:
        st.session_state['authenticated'] = False
        st.session_state['st_user_name'] = ""
        st.session_state['session_token'] = ""
        st.session_state['kicked_out'] = True

# Pokud uživatel není přihlášen, ukážeme mu jen přihlašovací formulář
if not st.session_state['authenticated']:
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center; color: #2c3e50;'>🔒 Přihlášení do dispečinku</h2>", unsafe_allow_html=True)
    
    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    with col_l2:
        if st.session_state.get('kicked_out'):
            st.error("⚠️ Byli jste odhlášeni! Někdo jiný se k tomuto účtu právě přihlásil na jiném zařízení.")
            st.session_state['kicked_out'] = False
        else:
            st.info("Pro přístup k plánovači rozvozů se prosím přihlaste.")
            
        with st.form("login_form"):
            username = st.selectbox("👤 Vaše jméno:", ["-- Vyberte uživatele --"] + list(VALID_USERS.keys()))
            password = st.text_input("🔑 Heslo:", type="password")
            submitted = st.form_submit_button("Přihlásit se", type="primary", use_container_width=True)
            
            if submitted:
                if username in VALID_USERS and VALID_USERS[username] == password:
                    import uuid
                    new_token = str(uuid.uuid4()) # Vytvoříme unikátní razítko (token)
                    
                    # Zapíšeme razítko do globálního souboru
                    active_u = load_active_users()
                    active_u[username] = new_token
                    save_active_users(active_u)
                    
                    st.session_state['authenticated'] = True
                    st.session_state['st_user_name'] = username
                    st.session_state['session_token'] = new_token
                    st.session_state['check_draft'] = True
                    log_action("Přihlášení", "Úspěšné přihlášení do dispečinku.")
                    st.rerun()
                else:
                    st.error("❌ Zvolte správné jméno ze seznamu a zadejte platné heslo!")
    # Tímto příkazem odstřihneme načítání zbytku aplikace
    st.stop() 

# --- NOVINKA: DIALOG PRO OBNOVU ZÁLOHY ---
@st.dialog("💾 Obnova rozpracované práce", width="large")
def draft_recovery_dialog(draft_data):
    st.warning(f"Našli jsme rozpracovaný rozvoz z vašeho předchozího sezení (počet objednávek: {len(draft_data.get('selected_orders', []))}). Chcete se k němu vrátit, nebo začít s čistým stolem?")
    c1, c2 = st.columns(2)
    if c1.button("✅ Ano, obnovit práci", type="primary", use_container_width=True):
        st.session_state['selected_orders'] = draft_data.get('selected_orders', [])
        st.session_state['editing_route_id'] = draft_data.get('editing_route_id')
        st.session_state['manual_orders'] = draft_data.get('manual_orders', [])
        st.session_state['manual_products'] = draft_data.get('manual_products', {})
        if 'st_route_name' in draft_data: st.session_state['st_route_name'] = draft_data['st_route_name']
        
        drafts = load_drafts()
        if st.session_state['st_user_name'] in drafts:
            del drafts[st.session_state['st_user_name']]
            save_drafts(drafts)
        st.session_state['check_draft'] = False
        st.rerun()
        
    if c2.button("🗑️ Ne, zahodit a začít čistě", use_container_width=True):
        drafts = load_drafts()
        if st.session_state['st_user_name'] in drafts:
            del drafts[st.session_state['st_user_name']]
            save_drafts(drafts)
        st.session_state['check_draft'] = False
        st.rerun()

if st.session_state.get('check_draft'):
    drafts = load_drafts()
    my_draft = drafts.get(st.session_state['st_user_name'])
    if my_draft and len(my_draft.get('selected_orders', [])) > 0:
        draft_recovery_dialog(my_draft)
    else:
        st.session_state['check_draft'] = False
# ----------------------------------------

# --- INICIALIZACE FORMULÁŘŮ ---

if 'st_start_address' not in st.session_state: st.session_state['st_start_address'] = "Karlovy Vary"
if 'st_end_address' not in st.session_state: st.session_state['st_end_address'] = "Karlovy Vary"
if 'st_start_point_name' not in st.session_state: st.session_state['st_start_point_name'] = "SKLAD (Výjezd)"
if 'st_end_point_name' not in st.session_state: st.session_state['st_end_point_name'] = "SKLAD (Návrat)"
if 'st_kasac_value' not in st.session_state: st.session_state['st_kasac_value'] = 2000
if 'st_start_time' not in st.session_state: st.session_state['st_start_time'] = datetime_time(6, 0)
if 'st_unload_time_min' not in st.session_state: st.session_state['st_unload_time_min'] = 10
if 'st_route_name' not in st.session_state: st.session_state['st_route_name'] = ""
if 'st_route_date' not in st.session_state: st.session_state['st_route_date'] = datetime.today()
if 'st_route_date_unknown' not in st.session_state: st.session_state['st_route_date_unknown'] = False
if 'st_driver_name' not in st.session_state: st.session_state['st_driver_name'] = ""

st.title("🚚 Inteligentní plánovač tras (Hromadný výběr + PDF)")

# --- NOVINKA: Automatický sjezd NAHORU po uložení ---
st.markdown("<div id='top_target'></div>", unsafe_allow_html=True)
if st.session_state.get('scroll_to_top'):
    st.html("<script>window.parent.document.getElementById('top_target').scrollIntoView({behavior: 'smooth', block: 'start'});</script>")
    st.session_state['scroll_to_top'] = False
# ----------------------------------------------------

# --- NOVINKA: PLOVOUCÍ TLAČÍTKO PRO AKTUALIZACI (Pravý dolní roh) ---
st.markdown("""
<style>
    /* Zajištění kompatibility napříč verzemi Streamlitu pro fixní pozici */
    div[data-testid="stElementContainer"]:has(.sticky-anchor) + div[data-testid="stElementContainer"],
    div[data-testid="stMarkdown"]:has(.sticky-anchor) + div[data-testid="stButton"] {
        position: fixed;
        bottom: 30px;
        right: 30px;
        z-index: 999999 !important;
    }
    
    /* Vizuální styl plovoucího tlačítka */
    div:has(.sticky-anchor) + div button {
        background-color: #2980b9 !important;
        color: white !important;
        border-radius: 50px !important;
        padding: 15px 30px !important;
        font-size: 16px !important;
        font-weight: bold !important;
        box-shadow: 0px 8px 25px rgba(0,0,0,0.5) !important;
        border: 2px solid white !important;
        transition: all 0.3s ease !important;
    }
    
    div:has(.sticky-anchor) + div button:hover {
        background-color: #3498db !important;
        transform: scale(1.05) !important;
        box-shadow: 0px 12px 30px rgba(0,0,0,0.6) !important;
    }
</style>
<div class="sticky-anchor"></div>
""", unsafe_allow_html=True)

if st.button("⚡ BLESKOVÁ AKTUALIZACE DAT", key="sticky_refresh_btn"):
    log_action("Aktualizace", "Vynucena blesková aktualizace dat ze Shoptetu.")
    st.cache_data.clear()
    st.rerun()
# --- KONEC PLOVOUCÍHO TLAČÍTKA ---

if st.session_state.get('show_success_msg'):
    st.success(st.session_state['show_success_msg'])
    st.session_state['show_success_msg'] = ""

if 'dispatch_warnings' not in st.session_state: st.session_state['dispatch_warnings'] = []

st.write("Aplikace automaticky načítá data ze Shoptetů. Využijte nástroje vpravo nahoře v mapě pro hromadný výběr (obdélník/tvar), nebo klikejte na jednotlivé body.")

saved_routes_main = load_routes()
saved_routes_ids = set()
editing_id = st.session_state.get('editing_route_id')
for r in saved_routes_main: 
    if editing_id and r.get('id') == editing_id:
        continue
    # NOVINKA: Ignorujeme zrušené objednávky, aby se vrátily na mapu
    for oid in r.get('orders', []):
        if r.get('details', {}).get(oid, {}).get('dispatch_status') != 'Zrušeno':
            saved_routes_ids.add(oid)
    
if 'geo_cache' not in st.session_state: st.session_state['geo_cache'] = load_geo_cache()
if 'active_dispatch' not in st.session_state: st.session_state['active_dispatch'] = None

# --- SIDEBAR: KDO U TOHO SEDÍ A NASTAVENÍ ---
st.sidebar.header("👤 Uživatel systému")
st.sidebar.success(f"Přihlášen jako: **{st.session_state['st_user_name']}**")

@st.dialog("📜 Deník dispečinku (Historie akcí)", width="large")
def show_audit_log():
    logs = load_audit_log()
    if not logs: st.info("Deník je zatím prázdný."); return
    df_logs = pd.DataFrame(logs)
    
    # Seřadíme sloupce hezky za sebe, pokud už existují
    cols = ['Čas', 'Uživatel', 'Akce', 'Rozvoz', 'Objednávka', 'Detail']
    cols = [c for c in cols if c in df_logs.columns]
    df_logs = df_logs[cols]
    
    col1, col2, col3 = st.columns(3)
    sel_user = col1.selectbox("Filtr podle uživatele:", ["Všichni"] + list(df_logs['Uživatel'].unique()))
    sel_akce = col2.selectbox("Filtr podle akce:", ["Všechny"] + list(df_logs['Akce'].unique()))
    search_txt = col3.text_input("🔍 Hledat (číslo obj. nebo text):")
    
    if sel_user != "Všichni": df_logs = df_logs[df_logs['Uživatel'] == sel_user]
    if sel_akce != "Všechny": df_logs = df_logs[df_logs['Akce'] == sel_akce]
    if search_txt:
        # Hledáme ve všech relevantních sloupcích naráz
        df_logs = df_logs[
            df_logs['Objednávka'].astype(str).str.contains(search_txt, case=False, na=False) |
            df_logs['Rozvoz'].astype(str).str.contains(search_txt, case=False, na=False) |
            df_logs['Detail'].astype(str).str.contains(search_txt, case=False, na=False)
        ]
    
    st.dataframe(df_logs, use_container_width=True, hide_index=True)

if st.sidebar.button("📜 Zobrazit Deník akcí", use_container_width=True):
    show_audit_log()

st.sidebar.markdown("---")
st.sidebar.header("💾 Záloha práce (Proti výpadku)")
if st.sidebar.button("💾 Uložit rozpracovaný stav do cloudu", use_container_width=True, type="primary"):
    with st.spinner("Zálohuji..."):
        drafts = load_drafts()
        drafts[st.session_state['st_user_name']] = {
            'selected_orders': st.session_state.get('selected_orders', []),
            'editing_route_id': st.session_state.get('editing_route_id'),
            'manual_orders': st.session_state.get('manual_orders', []),
            'manual_products': st.session_state.get('manual_products', {}),
            'st_route_name': st.session_state.get('st_route_name', '')
        }
        save_drafts(drafts)
    st.sidebar.success("✅ Úspěšně zálohováno proti výpadku!")
st.sidebar.info("💡 Klikněte sem kdykoliv během práce, abyste nepřišli o načtenou trasu při případném odhlášení.")

if st.sidebar.button("🚪 Odhlásit se", use_container_width=True):
    log_action("Odhlášení", "Uživatel se ručně odhlásil ze systému.")
    active_u = load_active_users()
    if st.session_state['st_user_name'] in active_u:
        del active_u[st.session_state['st_user_name']]
        save_active_users(active_u)
        
    st.session_state['authenticated'] = False
    st.session_state['st_user_name'] = ""
    st.session_state['session_token'] = ""
    st.rerun()

    # --- NOVINKA: GLOBÁLNÍ EXPORT VŠECH PŘIŘAZENÝCH OBJEDNÁVEK ---
st.sidebar.markdown("---")
st.sidebar.header("📥 Exporty dat")

all_r_for_export = load_routes()
export_rows = []
for r_exp in all_r_for_export:
    if r_exp.get('status') != 'trashed': # Ignorujeme smazané rozvozy v koši
        
        # 1. Získání čistého názvu rozvozu bez data a řidiče
        r_name_clean = r_exp.get('raw_route_name')
        if not r_name_clean:
            # Fallback pro starší rozvozy: ustřihne vše za znakem '|'
            r_name_clean = r_exp.get('name', 'Neznámý rozvoz').split('|')[0].strip()

        if 'itinerary_data' in r_exp:
            for row_exp in r_exp['itinerary_data']:
                oid = str(row_exp.get('Číslo objednávky', ''))
                # Vyfiltrujeme umělé body START a CÍL a případně Zrušené objednávky
                if oid not in ['START', 'CÍL'] and r_exp.get('details', {}).get(oid, {}).get('dispatch_status') != 'Zrušeno':
                    # 2. Odstranění předpony e-shopu (např. MAX-12345 -> 12345)
                    oid_clean = oid.split('-', 1)[1] if '-' in oid else oid
                    export_rows.append({"Číslo objednávky": oid_clean, "Název rozvozu": r_name_clean})
        else:
            # Fallback pro velmi staré uložené rozvozy (bez itinerary_data)
            for oid in r_exp.get('orders', []):
                oid_str = str(oid)
                oid_clean = oid_str.split('-', 1)[1] if '-' in oid_str else oid_str
                export_rows.append({"Číslo objednávky": oid_clean, "Název rozvozu": r_name_clean})

if export_rows:
    df_exp = pd.DataFrame(export_rows)
    buf_exp = io.BytesIO()
    with pd.ExcelWriter(buf_exp, engine='openpyxl') as writer:
        df_exp.to_excel(writer, index=False, sheet_name='Prirazene_objednavky')
    
    st.sidebar.download_button(
        label="📊 Export přiřazených obj. (XLSX)",
        data=buf_exp.getvalue(),
        file_name=f"Vsechny_prirazene_objednavky_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
else:
    st.sidebar.button("📊 Export přiřazených obj. (XLSX)", disabled=True, use_container_width=True, help="Zatím nemáte žádné aktivní ani historické rozvozy k exportu.")
# -------------------------------------------------------------

def update_disp_note(r_id_val, o_id_val, key_val):
    latest = load_routes()
    for route in latest:
        if route['id'] == r_id_val:
            new_note = st.session_state[key_val]
            route['details'][o_id_val]['note'] = new_note
            
            # NOVINKA: Okamžité propsání poznámky i do dat pro PDF řidiče
            if 'itinerary_data' in route:
                for row in route['itinerary_data']:
                    if row['Číslo objednávky'] == o_id_val:
                        row['Poznámka'] = new_note
                        break
                        
            save_routes(latest)
            
            # CHYTRÁ POJISTKA: Smažeme stará vygenerovaná PDF, aby si je někdo omylem nestáhl
            if f"ready_pdfs_{r_id_val}" in st.session_state:
                del st.session_state[f"ready_pdfs_{r_id_val}"]
                
            break

st.sidebar.markdown("---")
st.sidebar.header("⚙️ Nastavení výpočtu")
mapy_api_key = st.sidebar.text_input("Mapy.cz REST API klíč", value="3FDgcWrx0FfOCW9IxM7-g1VJYCV-h8Dqv4vkV7wPrD8", type="password")
st.sidebar.time_input("Čas výjezdu řidiče ze skladu", key="st_start_time")
st.sidebar.slider("Doba zdržení na zastávce (vykládka v min)", 0, 60, key="st_unload_time_min")
st.sidebar.slider("Max. rádius pro návrh doplnění (km)", 1, 50, 10, key="st_upsell_radius")

st.sidebar.markdown("---")
st.sidebar.header("📍 Adresy Startu a Cíle")
st.sidebar.text_input("Adresa startu (Sklad)", key="st_start_address")
st.sidebar.text_input("Adresa konce (Návrat)", key="st_end_address")
st.sidebar.text_input("Název výchozího bodu", key="st_start_point_name")
st.sidebar.text_input("Název cílového bodu", key="st_end_point_name")

st.sidebar.markdown("---")
st.sidebar.header("💰 Pokladna / Finance")
st.sidebar.number_input("Částka do kasáče (Kč)", min_value=0, step=100, key="st_kasac_value")

st.sidebar.markdown("---")
st.sidebar.header("🌌 Limity a Směr (Magický návrh)")
target_direction_city = st.sidebar.text_input("📍 Zacílit rozvoz (Město/Kraj - volitelné)", value="")
target_tolerance = st.sidebar.slider("Šířka koridoru po cestě", 1.05, 3.0, 1.4, 0.05)

auto_order_range = st.sidebar.slider("Počet objednávek (Min - Max)", min_value=1, max_value=80, value=(10, 25), step=1)
auto_min_orders, auto_max_orders = auto_order_range

auto_max_km = st.sidebar.number_input("Maximální trasa celkem (km)", min_value=10, value=700, step=50)
auto_max_time_h = st.sidebar.number_input("Maximální čas jízdy (hodiny)", min_value=1.0, value=9.5, step=0.5)

def round_up_to_15_minutes(dt):
    minutes_to_add = (15 - dt.minute % 15) % 15
    if minutes_to_add == 0 and dt.second == 0: return dt
    if minutes_to_add == 0: minutes_to_add = 15
    return dt + timedelta(minutes=minutes_to_add) - timedelta(seconds=dt.second, microseconds=dt.microsecond)

def geocode_address_api(adresa, api_key):
    if not adresa or pd.isna(adresa) or not str(adresa).strip(): return None, None
    url = f"https://api.mapy.cz/v1/geocode?query={quote(adresa)}&limit=1&apikey={api_key}"
    try:
        r = requests.get(url, timeout=5); data = r.json()
        if "items" in data and len(data["items"]) > 0:
            pos = data["items"][0]["position"]; time.sleep(0.1); return float(pos["lat"]), float(pos["lon"])
    except: pass
    time.sleep(0.1); return None, None

def get_driving_data(lat1, lon1, lat2, lon2, api_key):
    # --- OCHRANA: Pokud chybí GPS souřadnice (NaN), vrať vzdálenost 0 a nehaž chybu ---
    if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2):
        return 0.0, 0.0
    # ---------------------------------------------------------------------------------
        
    url = f"https://api.mapy.cz/v1/routing/route?start={lon1},{lat1}&end={lon2},{lat2}&routeType=car_fast&apikey={api_key}"
    try:
        time.sleep(0.2) # 🛑 NOVINKA: Zpomalovač proti zablokování Mapy.cz API
        r = requests.get(url, timeout=7); data = r.json()
        if "length" in data and "duration" in data: return float(data["length"] / 1000.0), float(data["duration"] / 60.0)
    except: pass 
    fallback_dist = geodesic((lat1, lon1), (lat2, lon2)).kilometers * 1.3
    return float(fallback_dist), float((fallback_dist / 50.0) * 60)

def parse_cod(val):
    try: return float(str(val).replace(' ', '').replace('Kč', '').replace(',', '.'))
    except: return 0.0

def optimize_route_2opt(route_nodes, dist_matrix):
    route_indices = list(range(len(route_nodes))); improvement = True
    while improvement:
        improvement = False
        for i in range(1, len(route_indices) - 2):
            for j in range(i + 1, len(route_indices) - 1):
                n_i_m1, n_i = route_nodes[route_indices[i-1]], route_nodes[route_indices[i]]
                n_j, n_j_p1 = route_nodes[route_indices[j]], route_nodes[route_indices[j+1]]
                current_dist = dist_matrix[n_i_m1][n_i] + dist_matrix[n_j][n_j_p1]
                new_dist = dist_matrix[n_i_m1][n_j] + dist_matrix[n_i][n_j_p1]
                if new_dist < current_dist - 0.0001: route_indices[i:j+1] = list(reversed(route_indices[i:j+1])); improvement = True
    return [route_nodes[i] for i in route_indices]

def calc_route_metrics(route_nodes, dist_matrix):
    dist = sum(dist_matrix[route_nodes[i]][route_nodes[i+1]] for i in range(len(route_nodes)-1))
    return dist, (dist / 50.0) * 60

# =========================================================================================
# CENTRÁLNÍ FUNKCE PRO VÝROBU PDF A UNICODE OCHRANU
# =========================================================================================
def generate_all_pdfs(route_name, df_itinerary, total_km, total_hours, total_cod, kasac_val, start_time_str, mapy_api_key, sleep_oid=None, day2_start_time_str="08:00"):
    use_custom_font = False; font_family_name = "Helvetica"; local_font_reg = ""; local_font_bold = ""
    paths_to_try = [("arial.ttf", "arialbd.ttf"), ("ARIAL.TTF", "ARIALBD.TTF"), ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf")]
    for r_path, b_path in paths_to_try:
        if os.path.exists(r_path) and os.path.exists(b_path): local_font_reg = r_path; local_font_bold = b_path; font_family_name = "ArialCustom"; use_custom_font = True; break

    def clean_str(s): 
        s = str(s)
        if not use_custom_font:
            import unicodedata
            s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
            return ''.join(c for c in s if ord(c) < 256)
        else:
            return ''.join(c for c in s if ord(c) < 65535)

    # --- NOVINKA: CHYTRÁ DETEKCE DATA A PŘESPÁNÍ (PŘÍMO Z PARAMETRŮ) ---
    import re
    from datetime import datetime, timedelta
    
    has_sleep = sleep_oid is not None

    d1_str = datetime.now().strftime('%d.%m.%Y')
    d2_str = (datetime.now() + timedelta(days=1)).strftime('%d.%m.%Y')
    route_name_display = route_name
    
    # Extrakce data z názvu rozvozu
    match = re.search(r'\b(\d{2}\.\d{2}\.\d{4})\b', route_name)
    if match:
        d1_str = match.group(1)
        try:
            d1 = datetime.strptime(d1_str, '%d.%m.%Y')
            d2_str = (d1 + timedelta(days=1)).strftime('%d.%m.%Y')
            if has_sleep and " - " not in route_name:
                route_name_display = route_name.replace(d1_str, f"{d1_str} - {d2_str}")
        except: pass

    # --- ROZPOČÍTÁNÍ METRIK NA 1. A 2. DEN ---
    d1_km = 0; d2_km = 0
    d1_min = 0; d2_min = 0
    d1_cod = 0; d2_cod = 0
    d1_stops = 0; d2_stops = 0
    total_stops_driver = sum(1 for _, row in df_itinerary.iterrows() if row['Číslo objednávky'] not in ['START', 'CÍL'])
    
    if has_sleep:
        is_day2_calc = False
        for _, r_row in df_itinerary.iterrows():
            oid = r_row['Číslo objednávky']
            if oid not in ['START', 'CÍL']:
                cod_val = parse_cod(r_row.get('Dobírka (Kč)', 0))
                if is_day2_calc: 
                    d2_cod += cod_val
                    d2_stops += 1
                else: 
                    d1_cod += cod_val
                    d1_stops += 1
                
            km_val = int(r_row.get('Vzdálen k další (km)', 0))
            min_val = int(float(r_row.get('Čas k další (min)', 0)))
            
            if is_day2_calc:
                d2_km += km_val; d2_min += min_val
            else:
                d1_km += km_val; d1_min += min_val
                
            if oid == sleep_oid:
                is_day2_calc = True
                
    d1_hours = f"{d1_min//60}h {d1_min%60}min"
    d2_hours = f"{d2_min//60}h {d2_min%60}min"
    # -----------------------------------------------

    def generate_map_image(itinerary_df):
        lats = itinerary_df['lat'].tolist(); lons = itinerary_df['lon'].tolist()
        if not lats: return None
        
        min_lat, max_lat = min(lats), max(lats); min_lon, max_lon = min(lons), max(lons)
        pad_lat = max(0.02, (max_lat - min_lat) * 0.15); pad_lon = max(0.02, (max_lon - min_lon) * 0.15)
        min_lat -= pad_lat; max_lat += pad_lat; min_lon -= pad_lon; max_lon += pad_lon
        
        def latlon_to_xy(lat, lon, z):
            lat_rad = math.radians(lat); n = 2.0 ** z
            return ((lon + 180.0) / 360.0 * n), ((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
            
        zoom = 12
        for z in range(14, 4, -1):
            x0, y0 = latlon_to_xy(max_lat, min_lon, z); x1, y1 = latlon_to_xy(min_lat, max_lon, z)
            if (x1 - x0) <= 5 and (y1 - y0) <= 5: zoom = z; break
                
        x0, y0 = latlon_to_xy(max_lat, min_lon, zoom); x1, y1 = latlon_to_xy(min_lat, max_lon, zoom)
        tile_x0, tile_y0 = int(x0), int(y0); tile_x1, tile_y1 = int(x1), int(y1)
        map_img = Image.new('RGB', ((tile_x1 - tile_x0 + 1) * 256, (tile_y1 - tile_y0 + 1) * 256), color='#eef2f3')
        
        for tx in range(tile_x0, tile_x1 + 1):
            for ty in range(tile_y0, tile_y1 + 1):
                url = f"https://api.mapy.cz/v1/maptiles/basic/256/{zoom}/{tx}/{ty}?apikey={mapy_api_key}"
                try:
                    r = requests.get(url, timeout=3)
                    if r.status_code == 200: tile = Image.open(io.BytesIO(r.content)).convert('RGB'); map_img.paste(tile, ((tx - tile_x0) * 256, (ty - tile_y0) * 256))
                except: pass
                    
        fig, ax = plt.subplots(figsize=(10, 7.5), dpi=150)
        def coord_to_px(lat, lon):
            x, y = latlon_to_xy(lat, lon, zoom); return (x - tile_x0) * 256, (y - tile_y0) * 256
            
        ax.imshow(map_img); pxs, pys = [], []
        for lat, lon in zip(lats, lons): px, py = coord_to_px(lat, lon); pxs.append(px); pys.append(py)
        ax.plot(pxs, pys, color='#2980b9', linewidth=4.5, zorder=2); ax.scatter(pxs, pys, color='#e74c3c', s=140, zorder=5, edgecolors='white', linewidths=2)
        
        for i, row in itinerary_df.iterrows():
            px, py = coord_to_px(row['lat'], row['lon'])
            label = "S" if i == 0 else ("C" if i == len(itinerary_df)-1 else str(i))
            is_hotel = (row['Číslo objednávky'] == sleep_oid)
            bg_col = "#8e44ad" if is_hotel else "white"
            txt_col = "white" if is_hotel else "black"
            lbl_text = f"🏨 {label}" if is_hotel else label
            
            ax.annotate(lbl_text, (px, py), textcoords="offset points", xytext=(0,10), ha='center', fontsize=11, fontweight='bold', color=txt_col, bbox=dict(boxstyle="round,pad=0.3", fc=bg_col, ec="#7f8c8d", alpha=0.9), zorder=6)
            
        px_min_x, px_min_y = coord_to_px(max_lat, min_lon); px_max_x, px_max_y = coord_to_px(min_lat, max_lon)
        ax.set_xlim(px_min_x, px_max_x); ax.set_ylim(px_max_y, px_min_y); ax.axis('off')
        plt.tight_layout(pad=0); img_buf = io.BytesIO(); plt.savefig(img_buf, format='png', bbox_inches='tight', pad_inches=0.1)
        img_buf.seek(0); plt.close(fig); return img_buf

    df_for_map = df_itinerary.dropna(subset=['lat', 'lon']).reset_index(drop=True)
    map_temp_img = generate_map_image(df_for_map) if not df_for_map.empty else None

    class DriverPDF(FPDF):
        def header(self): pass 

    def build_page_one(pdf_obj, title_txt):
        pdf_obj.add_page(); pdf_obj.set_font(font_family_name, "B", 14)
        pdf_obj.cell(0, 8, clean_str(title_txt), ln=True, align="C"); pdf_obj.set_font(font_family_name, "", 9); pdf_obj.set_text_color(100, 100, 100)
        pdf_obj.cell(0, 5, clean_str(f"Vygenerováno: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Výjezd: {start_time_str}"), ln=True, align="C")
        pdf_obj.ln(2); pdf_obj.set_font(font_family_name, "B", 16); pdf_obj.set_text_color(44, 62, 80)
        pdf_obj.cell(0, 10, clean_str(route_name_display), ln=True, align="C"); pdf_obj.ln(2)
        
        box_h = 24 if has_sleep else 16
        pdf_obj.set_font(font_family_name, "B", 10.5); pdf_obj.set_text_color(50, 50, 50); pdf_obj.set_fill_color(245, 246, 250); pdf_obj.set_draw_color(200, 200, 200)
        pdf_obj.rect(10, pdf_obj.get_y(), 190, box_h, style="DF"); pdf_obj.set_y(pdf_obj.get_y() + 2)
        pdf_obj.cell(95, 6, clean_str(f"  Celková vzdálenost: {int(total_km)} km"), ln=False); pdf_obj.cell(95, 6, clean_str(f"Čistý čas jízdy: {total_hours}"), ln=True)
        pdf_obj.cell(95, 6, clean_str(f"  Celkové dobírky: {int(total_cod)} Kč"), ln=False); pdf_obj.cell(95, 6, clean_str(f"Základní pokladna (Kasáč): {int(kasac_val)} Kč"), ln=True)
        
        if has_sleep:
            pdf_obj.set_font(font_family_name, "B", 9); pdf_obj.set_text_color(142, 68, 173)
            pdf_obj.cell(95, 6, clean_str(f"  ► 1. DEN: {int(d1_km)} km | {d1_hours} | Dobírky: {int(d1_cod)} Kč"), ln=False)
            pdf_obj.cell(95, 6, clean_str(f"► 2. DEN: {int(d2_km)} km | {d2_hours} | Dobírky: {int(d2_cod)} Kč"), ln=True)
            
        pdf_obj.ln(6)
        if map_temp_img:
            t_path = f"temp_m_{time.time()}.png"
            with open(t_path, "wb") as f: f.write(map_temp_img.getbuffer())
            pdf_obj.image(t_path, x=10, y=pdf_obj.get_y(), w=190)
            if os.path.exists(t_path): os.remove(t_path)

    # 1. PDF ŘIDIČ
    pdf_driver = DriverPDF(orientation="P", unit="mm", format="A4")
    if use_custom_font: 
        pdf_driver.add_font("ArialCustom", "", local_font_reg, uni=True); pdf_driver.add_font("ArialCustom", "B", local_font_bold, uni=True)
    build_page_one(pdf_driver, "TRASOVÝ SOUPIS ŘIDIČE (A4)")
    pdf_driver.add_page(); pdf_driver.set_font(font_family_name, "B", 14); pdf_driver.set_text_color(44, 62, 80)
    
    title_suffix = " (DVOUDENNÍ ROZVOZ 🏨)" if has_sleep else ""
    pdf_driver.cell(0, 8, clean_str(f"ITINERÁŘ TRASY{title_suffix} - {route_name_display}"), ln=True); pdf_driver.ln(2)
    
    is_pdf_day2 = False
    current_day_driver = 1
    for idx, row in df_itinerary.iterrows():
        is_start = row['Číslo objednávky'] == 'START'; is_end = row['Číslo objednávky'] == 'CÍL'; is_not_end = idx < len(df_itinerary) - 1
        
        # NOVINKA: Skok na novou stránku pro 2. den (ignoruje umělé body START/CÍL)
        if is_pdf_day2 and current_day_driver == 1 and not is_start and not is_end:
            pdf_driver.add_page()
            pdf_driver.set_font(font_family_name, "B", 15)
            pdf_driver.set_text_color(255, 255, 255)
            pdf_driver.set_fill_color(142, 68, 173)
            pdf_driver.cell(0, 10, clean_str(f"--- 2. DEN: VÝJEZD {d2_str} ---"), ln=True, align="C", fill=True)
            pdf_driver.ln(4)
            current_day_driver = 2
            
        lbl = "S" if is_start else "C" if is_end else str(idx); orig_prijemce = clean_str(row['Příjemce'])
        
        # --- NOVINKA: Přeformátování jména pro řidiče (Jan Novák -> Novák J.) ---
        if not is_start and not is_end:
            name_parts = orig_prijemce.split()
            # Pokud jméno obsahuje více slov (křestní a příjmení)
            if len(name_parts) >= 2:
                last_name = name_parts[-1] # Poslední slovo bereme jako příjmení
                first_initials = " ".join([f"{n[0]}." for n in name_parts[:-1]]) # Z předchozích uděláme iniciály
                orig_prijemce = f"{last_name} {first_initials}"
        # ------------------------------------------------------------------------
        
        addr = clean_str(row['Tisk_Adresa']).replace('nan','').replace('NaN','').replace('None','').strip()
        if row['Chyba'] and not (is_start or is_end): addr = f"({row['Chyba']}) {addr}"
        
        phone_raw = str(row['Telefon']).strip() if row['Telefon'] and str(row['Telefon']).lower() not in ['none', 'nan', ''] else ""
        prefix, main_num = "", ""
        if phone_raw:
            if phone_raw.startswith("+420") or phone_raw.startswith("+421"): 
                prefix = phone_raw[:4]; main_num = phone_raw[4:].strip()
            else: main_num = phone_raw
            m_c = main_num.replace(" ", "")
            main_num = f"{m_c[:3]} {m_c[3:6]} {m_c[6:]}" if len(m_c)==9 else " ".join([m_c[i:i+3] for i in range(0, len(m_c), 3)])
        
        cas_str = f"{row['Čas příjezdu']}" if (is_start or is_end) else f"Cca: {row['Čas příjezdu']}"
        okno_str = "" if (is_start or is_end) else f"{row['Okno příjezdu (2h)']}"
        
        pdf_driver.set_font(font_family_name, "B", 10)
        if is_start or is_end:
            p_name = orig_prijemce
            while len(p_name) > 0 and pdf_driver.get_string_width(p_name) > 58: p_name = p_name[:-1]
            name_and_id = p_name
            pkg_c = 1
        else:
            pkg_c = row.get('Počet balíků', 1)
            try: pkg_c = int(pkg_c)
            except: pkg_c = 1
            
            # Balíky už necpeme ke jménu, takže jméno má zpět svůj plný prostor!
            id_str = f" [{row['Číslo objednávky']}]"
            id_w = pdf_driver.get_string_width(id_str); p_name = orig_prijemce
            while len(p_name) > 0 and pdf_driver.get_string_width(p_name) > (65 - id_w): p_name = p_name[:-1]
            name_and_id = p_name + id_str
        
        cod_val = parse_cod(row['Dobírka (Kč)']); dobirka_str = f"{int(cod_val)} Kč" if cod_val > 0 else ""
        note_raw = str(row.get('Poznámka', '')).strip()
        
        if row['Číslo objednávky'] == sleep_oid:
            add_note = f"🏨 PŘESPÁNÍ PO TÉTO ZASTÁVCE! (Výjezd zítra v {day2_start_time_str})"
            note_raw = f"{add_note}\n{note_raw}" if note_raw else add_note
            
        has_note = bool(note_raw) and note_raw.lower() not in ['none', 'nan', '']; note_clean = clean_str(note_raw)
        
        box_h = 15 if has_note else 10; total_h = box_h + (5 if is_not_end else 2)
        if pdf_driver.get_y() + total_h > 280: pdf_driver.add_page()
        
        start_y = pdf_driver.get_y()
        pdf_driver.set_fill_color(248, 249, 250) if idx % 2 == 0 else pdf_driver.set_fill_color(255, 255, 255)
        pdf_driver.set_draw_color(160, 160, 160); pdf_driver.rect(10, start_y, 190, box_h, "DF")
        pdf_driver.set_draw_color(100, 100, 100); pdf_driver.rect(12, start_y + 1.5, 4, 4)
        
        pdf_driver.set_xy(18, start_y + 1); pdf_driver.set_text_color(30, 30, 30); pdf_driver.set_font(font_family_name, "B", 11); pdf_driver.cell(8, 5, lbl, align="L")
        pdf_driver.set_xy(26, start_y + 1); pdf_driver.set_font(font_family_name, "B", 10); pdf_driver.cell(60, 5, clean_str(name_and_id))
        
        pdf_driver.set_xy(88, start_y + 1)
        if phone_raw:
            pdf_driver.set_font(font_family_name, "", 7); pdf_driver.cell(pdf_driver.get_string_width(prefix + " "), 5, prefix + " ")
            pdf_driver.set_font(font_family_name, "B", 10); pdf_driver.cell(20, 5, main_num)
        else: 
            pdf_driver.set_font(font_family_name, "", 9); pdf_driver.cell(20, 5, "-")
        
        pdf_driver.set_xy(120, start_y + 1)
        if is_start or is_end: 
            pdf_driver.set_font(font_family_name, "B", 10); pdf_driver.cell(40, 5, clean_str(cas_str), align="C")
        else:
            pdf_driver.set_font(font_family_name, "", 7); pdf_driver.set_text_color(120, 120, 120); pdf_driver.cell(15, 5, clean_str(cas_str))
            pdf_driver.set_font(font_family_name, "B", 11); pdf_driver.set_text_color(30, 30, 30); pdf_driver.cell(25, 5, clean_str(okno_str))
        
        # --- ZDE PŘESOUVÁME BALÍKY POD DOBÍRKU ---
        pdf_driver.set_xy(160, start_y + 1)
        if dobirka_str: 
            pdf_driver.set_font(font_family_name, "B", 11)
            pdf_driver.set_fill_color(40, 40, 40)
            pdf_driver.set_text_color(255, 255, 255)
            pdf_driver.cell(30, 5, clean_str(dobirka_str), align="C", fill=True)
        elif not is_start and not is_end: 
            pdf_driver.set_font(font_family_name, "B", 9)
            pdf_driver.set_text_color(46, 204, 113)
            pdf_driver.cell(30, 5, clean_str("PLACENO"), align="C")
            
        if not is_start and not is_end and pkg_c > 1:
            pdf_driver.set_xy(160, start_y + 6.5)
            pdf_driver.set_font(font_family_name, "", 7.5)
            pdf_driver.set_text_color(100, 100, 100)
            b_txt = f"({pkg_c} balíků)" if pkg_c > 4 else f"({pkg_c} balíky)"
            pdf_driver.cell(30, 4, clean_str(b_txt), align="C")
        
        pdf_driver.set_text_color(30, 30, 30)
        curr_y = start_y + 6
        
        # Zkrácení pole pro adresu a poznámku, aby se fyzicky nepřekrývaly s textem o balících
        if has_note:
            pdf_driver.set_fill_color(40, 40, 40)
            pdf_driver.rect(26, curr_y, 134, 4.5, "F") # Zkráceno ze 162 na 134, končí tak přesně u hranice dobírky
            pdf_driver.set_xy(27, curr_y)
            pdf_driver.set_font(font_family_name, "B", 8)
            pdf_driver.set_text_color(255, 255, 255)
            
            note_str = f"(!) VZKAZ ŘIDIČI: {note_clean}"
            while len(note_str) > 0 and pdf_driver.get_string_width(note_str) > 132: note_str = note_str[:-1]
            pdf_driver.cell(132, 4.5, clean_str(note_str))
            curr_y += 4.5
            
        pdf_driver.set_text_color(50, 50, 50); pdf_driver.set_xy(26, curr_y); pdf_driver.set_font(font_family_name, "", 8)
        addr_str = addr
        while len(addr_str) > 0 and pdf_driver.get_string_width(addr_str) > 132: addr_str = addr_str[:-1]
        pdf_driver.cell(132, 4, clean_str(addr_str))
        
        if is_not_end:
            pdf_driver.set_xy(10, start_y + box_h); pdf_driver.set_font(font_family_name, "B", 9.5); pdf_driver.set_text_color(0, 0, 0)
            try: dm = int(float(row['Čas k další (min)'])); d_s = f"{dm//60}:{dm%60:02d} h" if dm >= 60 else f"{dm} min"
            except: d_s = f"{row['Čas k další (min)']} min"
            pdf_driver.cell(190, 5, clean_str(f"--- Přejezd k další zastávce: {int(row['Vzdálen k další (km)'])} km ({d_s}) ---"), align="C")
            
        pdf_driver.set_y(start_y + total_h)
        
        if row['Číslo objednávky'] == sleep_oid:
            pdf_driver.ln(3)
            pdf_driver.set_font(font_family_name, "B", 11)
            pdf_driver.set_fill_color(240, 240, 240)
            pdf_driver.set_text_color(30, 30, 30)
            pdf_driver.cell(190, 8, clean_str(f"📊 SOUHRN 1. DNE: Zastávek: {d1_stops} | Vybrané dobírky: {int(d1_cod)} Kč | V kase celkem (s kasáčem): {int(d1_cod + kasac_val)} Kč"), ln=True, align="C", fill=True)
            pdf_driver.ln(2)
            
            pdf_driver.set_font(font_family_name, "B", 12)
            pdf_driver.set_fill_color(142, 68, 173)
            pdf_driver.set_text_color(255, 255, 255)
            pdf_driver.cell(190, 8, clean_str(f"🏨 --- KONEC 1. DNE: PŘESPÁNÍ PO TÉTO ZASTÁVCE --- 🏨"), ln=True, align="C", fill=True)
            pdf_driver.ln(3)
            is_pdf_day2 = True

    # --- NOVINKA: FINÁLNÍ SOUHRN NA KONCI SOUPISU PRO ŘIDIČE ---
    pdf_driver.ln(5)
    pdf_driver.set_font(font_family_name, "B", 13)
    pdf_driver.set_fill_color(44, 62, 80)
    pdf_driver.set_text_color(255, 255, 255)
    
    if has_sleep:
        pdf_driver.cell(190, 8, clean_str(f"📊 SOUHRN 2. DNE: Zastávek: {d2_stops} | Vybrané dobírky: {int(d2_cod)} Kč"), ln=True, align="C", fill=True)
        pdf_driver.ln(3)
        pdf_driver.set_fill_color(39, 174, 96)
        
    pdf_driver.cell(190, 10, clean_str(f"🏆 CELKOVÝ SOUHRN TRASY (K ODEVZDÁNÍ) 🏆"), ln=True, align="C", fill=True)
    pdf_driver.set_font(font_family_name, "B", 11)
    pdf_driver.set_text_color(30, 30, 30)
    pdf_driver.set_fill_color(245, 246, 250)
    pdf_driver.cell(190, 8, clean_str(f"Celkový počet zastávek: {total_stops_driver} | Základní kasáč: {int(kasac_val)} Kč"), ln=True, align="C", fill=True)
    pdf_driver.cell(190, 8, clean_str(f"Celkem vybráno na dobírkách: {int(total_cod)} Kč"), ln=True, align="C", fill=True)
    
    pdf_driver.set_font(font_family_name, "B", 12)
    pdf_driver.set_text_color(192, 57, 43)
    pdf_driver.cell(190, 10, clean_str(f"💰 ŘIDIČ ODEVZDÁVÁ (DOBÍRKY + KASÁČ): {int(total_cod + kasac_val)} Kč 💰"), ln=True, align="C", fill=True)
    # -------------------------------------------------------------

    # 2. PDF DISPEČER
    pdf_disp = DriverPDF(orientation="P", unit="mm", format="A4")
    if use_custom_font: 
        pdf_disp.add_font("ArialCustom", "", local_font_reg, uni=True); pdf_disp.add_font("ArialCustom", "B", local_font_bold, uni=True)
    build_page_one(pdf_disp, "DISPEČERSKÝ SOUPIS A PŘEHLED TRASY")
    pdf_disp.add_page(); pdf_disp.set_font(font_family_name, "B", 13); pdf_disp.set_text_color(44, 62, 80)
    
    title_suffix = " (DVOUDENNÍ ROZVOZ 🏨)" if has_sleep else ""
    pdf_disp.cell(0, 8, clean_str(f"ADMINISTRATIVNÍ PŘEHLED ZÁSILEK{title_suffix} - {route_name_display}"), ln=True); pdf_disp.ln(2)
    
    is_disp_pdf_day2 = False
    disp_current_day = 1
    for idx, row in df_itinerary.iterrows():
        if row['Číslo objednávky'] in ['START', 'CÍL']: continue
        
        # NOVINKA: Výrazný předěl pro dispečera
        is_start = row['Číslo objednávky'] == 'START'; is_end = row['Číslo objednávky'] == 'CÍL'
        
        # NOVINKA: Skok na novou stránku pro 2. den v Dispečinku
        if is_disp_pdf_day2 and disp_current_day == 1 and not is_start and not is_end:
            pdf_disp.add_page()
            pdf_disp.set_font(font_family_name, "B", 14)
            pdf_disp.set_fill_color(142, 68, 173)
            pdf_disp.set_text_color(255, 255, 255)
            pdf_disp.cell(190, 10, clean_str(f"--- 2. DEN (Následující den: {d2_str}) ---"), ln=True, align="C", fill=True)
            pdf_disp.ln(4)
            disp_current_day = 2
            
        orig_prijemce = clean_str(row['Příjemce']); order_id = row['Číslo objednávky']
        addr = clean_str(row['Tisk_Adresa']).replace('nan','').replace('NaN','').replace('None','').strip()
        phone_raw = str(row['Telefon']).strip() if row['Telefon'] and str(row['Telefon']).lower() not in ['none', 'nan', ''] else "-"
        prefix, main_num = "", ""
        if phone_raw != "-":
            if phone_raw.startswith("+420") or phone_raw.startswith("+421"): prefix = phone_raw[:4]; main_num = phone_raw[4:].strip()
            else: main_num = phone_raw
            m_c = main_num.replace(" ", "")
            main_num = f"{m_c[:3]} {m_c[3:6]} {m_c[6:]}" if len(m_c)==9 else " ".join([m_c[i:i+3] for i in range(0, len(m_c), 3)])

        cod_val = parse_cod(row['Dobírka (Kč)']); dobirka_str = f"{int(cod_val)} Kč" if cod_val > 0 else "PLACENO (0 Kč)"
        p_html = row.get('Produkty', '')
        p_plain = p_html.replace('<br>- ', '\n- ').replace('<br>', '\n').replace('<i>', '').replace('</i>', '').strip()

        if "Žádné produkty" in p_plain or not p_plain: p_plain = "- Žádné specifické produkty v exportu"
        if not p_plain.startswith('-'): p_plain = '- ' + p_plain
            
        prod_lines_count = p_plain.count('\n') + 1; box_h = 24 + (prod_lines_count * 4)
        if pdf_disp.get_y() + box_h > 280: pdf_disp.add_page()
        start_y = pdf_disp.get_y()
        pdf_disp.set_fill_color(252, 253, 254) if idx % 2 == 0 else pdf_disp.set_fill_color(255, 255, 255)
        pdf_disp.set_draw_color(140, 145, 155); pdf_disp.rect(10, start_y, 190, box_h, "DF")
        pdf_disp.set_draw_color(100, 100, 100); pdf_disp.rect(13, start_y + 3, 4, 4); pdf_disp.set_xy(18, start_y + 2.5); pdf_disp.set_font(font_family_name, "B", 9); pdf_disp.cell(20, 5, clean_str("POTVRZENO"))
        pdf_disp.rect(13, start_y + 9, 4, 4); pdf_disp.set_xy(18, start_y + 8.5); pdf_disp.set_font(font_family_name, "B", 9); pdf_disp.cell(20, 5, clean_str("SMS"))
        
        pdf_disp.set_font(font_family_name, "B", 11); id_str = f"{idx}. [{order_id}] "; id_w = pdf_disp.get_string_width(id_str); p_name = orig_prijemce
        while len(p_name) > 0 and pdf_disp.get_string_width(p_name) > (100 - id_w): p_name = p_name[:-1]
        name_and_id = id_str + p_name
        
        pdf_disp.set_xy(42, start_y + 2); pdf_disp.set_text_color(44, 62, 80); pdf_disp.cell(100, 5, clean_str(name_and_id))
        pdf_disp.set_xy(150, start_y + 2); pdf_disp.set_font(font_family_name, "B", 11)
        if cod_val > 0: pdf_disp.set_text_color(231, 76, 60); pdf_disp.cell(40, 5, clean_str(dobirka_str), align="R")
        else: pdf_disp.set_text_color(46, 204, 113); pdf_disp.cell(40, 5, clean_str("PLACENO"), align="R")
        
        pdf_disp.set_text_color(30, 30, 30); pdf_disp.set_xy(42, start_y + 8)
        if phone_raw != "-":
            pdf_disp.set_font(font_family_name, "", 8); pdf_disp.cell(pdf_disp.get_string_width(prefix + " "), 5, prefix + " ")
            pdf_disp.set_font(font_family_name, "B", 12); pdf_disp.cell(30, 5, main_num)
        else: pdf_disp.set_font(font_family_name, "", 10); pdf_disp.cell(30, 5, "-")
        
        okno_disp_str = f"{row['Okno příjezdu (2h)']}"

        pdf_disp.set_xy(110, start_y + 8); pdf_disp.set_font(font_family_name, "", 8); pdf_disp.set_text_color(120, 120, 120); pdf_disp.cell(15, 5, clean_str(f"Cca: {row['Čas příjezdu']}"))
        pdf_disp.set_font(font_family_name, "B", 12); pdf_disp.set_text_color(30, 30, 30); pdf_disp.cell(65, 5, clean_str(okno_disp_str), align="R")
        
        pdf_disp.set_xy(42, start_y + 14); pdf_disp.set_font(font_family_name, "", 8.5); pdf_disp.set_text_color(60, 60, 60); pdf_disp.cell(148, 4, clean_str(addr))
        pdf_disp.set_xy(13, start_y + 20); pdf_disp.set_font(font_family_name, "B", 8); pdf_disp.set_text_color(50, 50, 50); pdf_disp.cell(30, 4, clean_str("PRODUKTY:"))
        pdf_disp.set_xy(35, start_y + 20); pdf_disp.set_font(font_family_name, "", 8); pdf_disp.set_text_color(70, 70, 70); pdf_disp.multi_cell(155, 4, clean_str(p_plain), border=0)
        pdf_disp.set_y(start_y + box_h + 2)
        
        if row['Číslo objednávky'] == sleep_oid:
            pdf_disp.ln(2)
            pdf_disp.set_font(font_family_name, "B", 12)
            pdf_disp.set_fill_color(142, 68, 173)
            pdf_disp.set_text_color(255, 255, 255)
            pdf_disp.cell(190, 8, clean_str(f"🏨 --- KONEC 1. DNE: PŘESPÁNÍ PO TÉTO ZASTÁVCE --- 🏨"), ln=True, align="C", fill=True)
            pdf_disp.ln(3)
            is_disp_pdf_day2 = True

    # 3. PDF SKLADNÍK
    pdf_ware = DriverPDF(orientation="P", unit="mm", format="A4")
    if use_custom_font: 
        pdf_ware.add_font("ArialCustom", "", local_font_reg, uni=True); pdf_ware.add_font("ArialCustom", "B", local_font_bold, uni=True)
    build_page_one(pdf_ware, "NÁKLADOVÝ LIST PRO SKLAD")
    pdf_ware.add_page(); pdf_ware.set_font(font_family_name, "B", 13); pdf_ware.set_text_color(44, 62, 80)
    pdf_ware.cell(0, 8, clean_str(f"POŘADÍ NAKLÁDKY A ZBOŽÍ - {route_name_display}"), ln=True); pdf_ware.ln(2)
    
    is_ware_pdf_day2 = False
    ware_current_day = 1
    
    for idx, row in df_itinerary.iterrows():
        is_start = row['Číslo objednávky'] == 'START'; is_end = row['Číslo objednávky'] == 'CÍL'
        if row['Číslo objednávky'] in ['START', 'CÍL']: continue
        
        # Skok na novou stránku pro skladníka
        if is_ware_pdf_day2 and ware_current_day == 1 and not is_start and not is_end:
            pdf_ware.add_page()
            pdf_ware.set_font(font_family_name, "B", 14)
            pdf_ware.set_fill_color(142, 68, 173)
            pdf_ware.set_text_color(255, 255, 255)
            pdf_ware.cell(190, 10, clean_str(f"--- 2. DEN (Následující den: {d2_str}) ---"), ln=True, align="C", fill=True)
            pdf_ware.ln(4)
            ware_current_day = 2
            
        orig_prijemce = clean_str(row['Příjemce']); order_id = row['Číslo objednávky']
        p_html = row.get('Produkty', '')
        p_plain = p_html.replace('<br>- ', '\n- ').replace('<br>', '\n').replace('<i>', '').replace('</i>', '').strip()

        if "Žádné produkty" in p_plain or not p_plain: p_plain = "- Žádné specifické produkty v exportu"
        if not p_plain.startswith('-'): p_plain = '- ' + p_plain
        prod_lines_count = p_plain.count('\n') + 1; box_h = 11 + (prod_lines_count * 4.5)
        if pdf_ware.get_y() + box_h > 280: pdf_ware.add_page()
        start_y = pdf_ware.get_y(); pdf_ware.set_fill_color(252, 253, 254) if idx % 2 == 0 else pdf_ware.set_fill_color(255, 255, 255)
        pdf_ware.set_draw_color(140, 145, 155); pdf_ware.rect(10, start_y, 190, box_h, "DF")
        pdf_ware.set_draw_color(100, 100, 100); pdf_ware.rect(13, start_y + 3, 5, 5) 
        pdf_ware.set_font(font_family_name, "B", 11)
        prefix_str = f"Zastávka č. {idx}   |   Objednávka: {order_id}   |   "
        pref_w = pdf_ware.get_string_width(clean_str(prefix_str)); p_name = orig_prijemce
        while len(p_name) > 0 and pdf_ware.get_string_width(p_name) > (165 - pref_w): p_name = p_name[:-1]
        name_and_id = clean_str(prefix_str) + p_name
        pdf_ware.set_xy(22, start_y + 2.5); pdf_ware.set_text_color(44, 62, 80); pdf_ware.cell(160, 6, clean_str(name_and_id))
        pdf_ware.set_xy(22, start_y + 9); pdf_ware.set_font(font_family_name, "", 9.5); pdf_ware.set_text_color(20, 20, 20)
        pdf_ware.multi_cell(175, 4.5, clean_str(p_plain), border=0); pdf_ware.set_y(start_y + box_h + 2)
        
        if row['Číslo objednávky'] == sleep_oid:
            pdf_ware.ln(2)
            pdf_ware.set_font(font_family_name, "B", 12)
            pdf_ware.set_fill_color(142, 68, 173)
            pdf_ware.set_text_color(255, 255, 255)
            pdf_ware.cell(190, 8, clean_str(f"🏨 --- KONEC 1. DNE: PŘESPÁNÍ PO TÉTO ZASTÁVCE --- 🏨"), ln=True, align="C", fill=True)
            pdf_ware.ln(3)
            is_ware_pdf_day2 = True

    return {
        'pdf_dr': pdf_driver.output(dest='S').encode('latin1') if isinstance(pdf_driver.output(dest='S'), str) else bytes(pdf_driver.output(dest='S')),
        'pdf_di': pdf_disp.output(dest='S').encode('latin1') if isinstance(pdf_disp.output(dest='S'), str) else bytes(pdf_disp.output(dest='S')),
        'pdf_wa': pdf_ware.output(dest='S').encode('latin1') if isinstance(pdf_ware.output(dest='S'), str) else bytes(pdf_ware.output(dest='S'))
    }

# =========================================================================================
# FUNKCE PRO GENEROVÁNÍ ŠTÍTKŮ NA BALÍKY (ReportLab) S CHYTROU HIERARCHIÍ A TUBUSY
# =========================================================================================
def generate_labels_pdf(route_dict, pkg_counts):
    FONT_REGULAR = 'Helvetica'
    FONT_BOLD = 'Helvetica-Bold'
    paths_to_try = [("arial.ttf", "arialbd.ttf"), ("ARIAL.TTF", "ARIALBD.TTF"), ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf")]
    for r_path, b_path in paths_to_try:
        if os.path.exists(r_path) and os.path.exists(b_path):
            try:
                pdfmetrics.registerFont(TTFont('ArialCustom', r_path))
                pdfmetrics.registerFont(TTFont('ArialCustom-Bold', b_path))
                FONT_REGULAR = 'ArialCustom'
                FONT_BOLD = 'ArialCustom-Bold'
                break
            except: pass

    def clean_str_rl(s):
        s = str(s)
        import unicodedata
        if FONT_REGULAR == 'Helvetica':
            s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
            s = ''.join(c for c in s if ord(c) < 256)
        else:
            s = ''.join(c for c in s if ord(c) < 65535)
        s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return s

    pdf_buffer = io.BytesIO()
    PAGE_MARGIN = 0  
    COL_WIDTH = 595.27 / 2  
    
    # Bezpečná výška, která spolehlivě pojme i čáry mřížky (7 x 118.5 = 829.5 bodů < 841.89 A4)
    ROW_HEIGHT = 118.5  
    
    # Čistě definovaný papír (odstraněn duplikát z minula)
    doc = SimpleDocTemplate(pdf_buffer, pagesize=(595.27, 841.89), 
                            leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN, 
                            topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN)
    styles = getSampleStyleSheet()
    
    style_main = ParagraphStyle('Main', parent=styles['Normal'], fontName=FONT_REGULAR, leading=14)
    style_btm = ParagraphStyle('Btm', parent=styles['Normal'], alignment=2, fontName=FONT_REGULAR, leading=26)
    
    story = []
    grid_data = []
    current_row = []

    active_rows = [r for r in route_dict.get('itinerary_data', []) if r['Číslo objednávky'] not in ['START', 'CÍL'] and route_dict['details'].get(r['Číslo objednávky'], {}).get('dispatch_status', '') != "Zrušeno"]
    total_stops = len(active_rows)
    
    route_meta = clean_str_rl(route_dict.get('name', 'Neznámý rozvoz'))

    stop_idx = 1
    for row in active_rows:
        oid = row['Číslo objednávky']
        count = pkg_counts.get(oid, 1)
        prijemce = clean_str_rl(row['Příjemce'])
        order_num = clean_str_rl(oid)
        poznamka = clean_str_rl(route_dict['details'].get(oid, {}).get('note', ''))
        
        nakladka_idx = total_stops - stop_idx + 1
        
        cod_val = parse_cod(row.get('Dobírka (Kč)', 0))
        
        # NOVINKA: Širší sloupec pro objednávku, upravené fonty a fixní výška [22]
        if cod_val > 0:
            db_content = Paragraph(f"<b>DOBÍRKA: {int(cod_val)} Kč</b>", ParagraphStyle('db', parent=styles['Normal'], fontName=FONT_BOLD, fontSize=10, textColor=colors.white, alignment=1))
            bg_col = colors.black
        else:
            db_content = ""
            bg_col = colors.white

        order_element = Table([
            [
                Paragraph(f"<font size=8 color='#7f8c8d'>Objednávka:</font> <font size=13><b>{order_num[:22]}</b></font>", style_main),
                db_content
            ]
        ], colWidths=[165, 95], rowHeights=[22]) 
        
        order_element.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ('BACKGROUND', (1,0), (1,0), bg_col),
            ('TOPPADDING', (1,0), (1,0), 4),
            ('BOTTOMPADDING', (1,0), (1,0), 5),
        ]))
        
        # Vytvoříme hlavičku štítku jako miniaturní tabulku, aby byla poznámka fixně vpravo nahoře
        header_table = Table([
            [
                Paragraph(f"<font size=7 color='#95a5a6'>Na auto: <b>{nakladka_idx}. v pořadí</b> &nbsp;|&nbsp; {route_meta[:45]}</font>", style_main),
                Paragraph("<font backColor='black' color='white' size=9><b>&nbsp;!POZNÁMKA&nbsp;</b></font>", ParagraphStyle('badge', parent=styles['Normal'], alignment=2, fontName=FONT_BOLD)) if poznamka else ""
            ]
        ], colWidths=[195, 65]) # Přesně definované šířky zajistí absolutní stabilitu layoutu
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))

        for i in range(1, count + 1):
            label_content = [
                header_table, 
                Spacer(1, 1), # Ubráno místo nahoře
                # O chloupek menší jméno (z 20 na 18), aby se to na 100% vešlo i u dlouhých jmen
                # Snížili jsme font z 18 na 16, aby nevznikalo přetékání
                Paragraph(f"<font size=8 color='#7f8c8d'>Příjemce:</font><br/><font size=16><b>{prijemce}</b></font>", style_main),
                Spacer(1, 0), # Tímto natlačíme dobírku a číslo objednávky úplně nahoru k příjemci
                order_element,
                Spacer(1, 2), # Extrémní zmenšení původní obří mezery (z 8 na 2) pod dobírkou
                # O chloupek menší číslo balíku (z 28 na 26) jako absolutní pojistka proti přetečení
                Paragraph(f"<font size=10 color='#7f8c8d'>Zastávka </font><font size=16><b>{stop_idx}</b></font> &nbsp;&nbsp;&nbsp; <font size=12 color='#7f8c8d'>Balík </font><font size=26><b>{i}/{count}</b></font>", style_btm)
            ]
            current_row.append(label_content)
            
            if len(current_row) == 2:
                grid_data.append(current_row)
                current_row = []
        stop_idx += 1
        
    if current_row:
        current_row.append("")
        grid_data.append(current_row)
        
    if grid_data:
        row_heights = [ROW_HEIGHT] * len(grid_data)
        t = Table(grid_data, colWidths=[COL_WIDTH, COL_WIDTH], rowHeights=row_heights)
        t.setStyle(TableStyle([
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CCCCCC')), 
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (0,-1), 18),
            ('RIGHTPADDING', (0,0), (0,-1), 10),
            ('LEFTPADDING', (1,0), (1,-1), 10),
            ('RIGHTPADDING', (1,0), (1,-1), 18),
        ]))
        story.append(t)
        doc.build(story)
        
    return pdf_buffer.getvalue()

# --- FUNKCE PRO ZRUŠENÍ A PŘEPOČET TRASY ---
def recalc_dispatch_route(r_dict, mapy_api_key):
    old_times = {}
    for row in r_dict['itinerary_data']:
        if row['Číslo objednávky'] not in ['START', 'CÍL']: 
            old_times[row['Číslo objednávky']] = row.get('Čas příjezdu', '')
            
    slow = r_dict.get('slow_mode', False); unload = r_dict.get('unload_time_min', 15); start_time_str = r_dict.get('start_time_str', '06:00')
    active_itin = []
    for row in r_dict['itinerary_data']:
        oid = row['Číslo objednávky']
        if oid in ['START', 'CÍL'] or r_dict['details'].get(oid, {}).get('dispatch_status') != 'Zrušeno': active_itin.append(row)
            
    segments_data = []
    for i in range(len(active_itin) - 1):
        dist, dur = get_driving_data(active_itin[i]['lat'], active_itin[i]['lon'], active_itin[i+1]['lat'], active_itin[i+1]['lon'], mapy_api_key)
        segments_data.append((dist, dur))
        
    start_h, start_m = map(int, start_time_str.split(':')); current_dt = datetime.today().replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    arrival_times = [current_dt.strftime('%H:%M')]; arrival_windows = ['-']; distances_to_next = []; times_to_next = []
    
    # NOVINKA: Načtení dat o přespání
    sleep_oid = r_dict.get('sleep_after_oid')
    day2_time_str = r_dict.get('day2_start_time_str', '08:00')
    dh, dm = map(int, day2_time_str.split(':'))
    
    for i in range(len(active_itin) - 1):
        curr_node_oid = active_itin[i]['Číslo objednávky']
        
        # POKUD ŘIDIČ PO TÉTO ZASTÁVCE SPÍ, DALŠÍ VÝJEZD JE AŽ DRUHÝ DEN
        if curr_node_oid == sleep_oid:
            current_dt = datetime.combine(current_dt.date() + timedelta(days=1), datetime_time(dh, dm))
            
        dist, dur = segments_data[i]
        if slow: dur = dur * 1.1
        distances_to_next.append(int(round(dist))); times_to_next.append(int(dur)); arrival_dt = current_dt + timedelta(minutes=int(dur))
        if i + 1 == len(active_itin) - 1: arrival_times.append(arrival_dt.strftime('%H:%M')); arrival_windows.append('-')
        else:
            arrival_times.append(arrival_dt.strftime('%H:%M')); win_start = round_up_to_15_minutes(arrival_dt)
            arrival_windows.append(f"{win_start.strftime('%H:%M')} - {(win_start + timedelta(hours=2)).strftime('%H:%M')}")
            current_dt = arrival_dt + timedelta(minutes=unload)
            
    distances_to_next.append(0); times_to_next.append(0)
    
    active_idx = 0
    for i, row in enumerate(r_dict['itinerary_data']):
        oid = row['Číslo objednávky']
        if oid in ['START', 'CÍL'] or r_dict['details'].get(oid, {}).get('dispatch_status') != 'Zrušeno':
            row['Čas příjezdu'] = arrival_times[active_idx]; row['Okno příjezdu (2h)'] = arrival_windows[active_idx]
            row['Vzdálen k další (km)'] = int(round(distances_to_next[active_idx])); row['Čas k další (min)'] = times_to_next[active_idx]
            try: m = int(float(times_to_next[active_idx])); row['Čas přejezdu'] = f"{m//60}:{m%60:02d} h" if m>=60 else f"{m} min"
            except: row['Čas přejezdu'] = ""
            active_idx += 1
        else:
            row['Čas příjezdu'] = "ZRUŠENO"; row['Okno příjezdu (2h)'] = "-"; row['Vzdálen k další (km)'] = 0; row['Čas k další (min)'] = 0; row['Čas přejezdu'] = "-"

    r_dict['total_km'] = int(sum(distances_to_next)); tot_m = sum(times_to_next); r_dict['total_hours'] = f"{tot_m//60}h {tot_m%60}min"
    r_dict['total_cod'] = sum(parse_cod(x['Dobírka (Kč)']) for x in active_itin if x['Číslo objednávky'] not in ['START', 'CÍL'])
    # --- NOVINKA: Dynamický přepočet celkové Toptrans ceny ---
    r_dict['total_tt_price'] = sum(float(r_dict.get('details', {}).get(x['Číslo objednávky'], {}).get('tt_price', 0)) for x in active_itin if x['Číslo objednávky'] not in ['START', 'CÍL'])
    
    r_id = r_dict['id']
    if f"ready_pdfs_{r_id}" in st.session_state: del st.session_state[f"ready_pdfs_{r_id}"]

    warnings = []
    for row in active_itin:
        oid = row['Číslo objednávky']
        if oid in ['START', 'CÍL']: continue
        
        status = r_dict.get('details', {}).get(oid, {}).get('dispatch_status', '')
        
        confirmed_t = r_dict.get('details', {}).get(oid, {}).get('confirmed_time', '')
        confirmed_w = r_dict.get('details', {}).get(oid, {}).get('confirmed_window', 'Neznámé okno')
        old_t = confirmed_t if confirmed_t else old_times.get(oid)
        new_t = row.get('Čas příjezdu', '')
        new_w = row.get('Okno příjezdu (2h)', '')
        
        if old_t and new_t and old_t != "ZRUŠENO" and new_t != "ZRUŠENO" and status == "Potvrzeno":
            try:
                oh, om = map(int, old_t.split(':')); nh, nm = map(int, new_t.split(':'))
                diff = (oh*60 + om) - (nh*60 + nm)
                if diff > 60: 
                    warnings.append(f"⏱️ **{row['Příjemce']}** (Obj: {oid}) - Přijede o **{diff} min dříve**! (Původní okno: {confirmed_w} ➔ Nově: **{new_w}**)")
                elif diff < -60:
                    warnings.append(f"🐌 **{row['Příjemce']}** (Obj: {oid}) - Zpoždění **{abs(diff)} min**! (Původní okno: {confirmed_w} ➔ Nově: **{new_w}**)")
            except: pass
                
    return warnings

# --- FUNKCE PRO NÁHLED PŘIDÁNÍ ZASTÁVEK ---
def build_preview(r_dict, selected_cands, mapy_api_key):
    new_r = copy.deepcopy(r_dict)
    for c in selected_cands:
        oid = c['id']
        new_r['orders'].append(oid)
        new_r['details'][oid] = {
            "note": "",
            "addr": c['adresa'],
            "dispatch_status": "",
            "pkg_count": 1
        }
        new_r['itinerary_data'].append({
            'Číslo objednávky': oid, 'E-shop': c['eshop'], 'Příjemce': c['prijemce'],
            'Status': c['status'], 'Celá_adresa': c['adresa'], 'Tisk_Adresa': c['adresa'],
            'Město': '', 'PSČ': '', 'Chyba': '', 'Telefon': '', 'Dobírka (Kč)': str(c['cod']),
            'Produkty': c['produkty'], 'lat': c['lat'], 'lon': c['lon'], 'Poznámka': '',
            'Čas příjezdu': '', 'Okno příjezdu (2h)': '', 'Vzdálen k další (km)': 0,
            'Čas k další (min)': 0, 'Čas přejezdu': ''
        })
        
    pts = {row['Číslo objednávky']: row for row in new_r['itinerary_data']}
    active_oids = [oid for oid in pts if oid not in ['START', 'CÍL'] and new_r['details'].get(oid, {}).get('dispatch_status') != 'Zrušeno']
    
    dist_matrix = {}
    all_opt = ['START'] + active_oids + ['CÍL']
    for p1 in all_opt:
        dist_matrix[p1] = {}
        for p2 in all_opt:
            if p1 == p2: dist_matrix[p1][p2] = 0.0
            else: dist_matrix[p1][p2] = geodesic((pts[p1]['lat'], pts[p1]['lon']), (pts[p2]['lat'], pts[p2]['lon'])).kilometers * 1.3
            
    opt_ids = optimize_route_2opt(all_opt, dist_matrix)
    
    ordered_itin = []
    ordered_itin.append(pts['START'])
    for oid in opt_ids:
        if oid not in ['START', 'CÍL']: ordered_itin.append(pts[oid])
            
    cancelled_oids = [oid for oid in pts if oid not in ['START', 'CÍL'] and new_r['details'].get(oid, {}).get('dispatch_status') == 'Zrušeno']
    for oid in cancelled_oids: ordered_itin.append(pts[oid])
        
    ordered_itin.append(pts['CÍL'])
    new_r['itinerary_data'] = ordered_itin
    
    recalc_dispatch_route(new_r, mapy_api_key)
    return new_r

# --- NAČÍTÁNÍ DAT Z E-SHOPŮ ---
SHOP1_URL = "https://www.max-i.cz/export/orders.xls?patternId=139&partnerId=13&hash=79a9b4e46276c62e22d481c04662fe1dd9300b5d7133595d95c293f92e65e498"
SHOP2_URL = "https://www.vomaks.cz/export/orders.xls?patternId=113&partnerId=8&hash=21597d7e45b81f24edaee6acb923968470a428c8bd78aafc1b63c33eeb3d5b3f"
SHOP3_URL = "https://www.slevadoma.cz/export/orders.xls?patternId=16&partnerId=6&hash=a2dfdd4edfc9711e993f72ed428c8499530d324952cd6c82bde49171df279e2f"

@st.cache_data(show_spinner=False, ttl=300) 
def fetch_data_from_url(url):
    df = None; headers = {'User-Agent': 'Mozilla/5.0'}
    try: response = requests.get(url, headers=headers, timeout=15); response.raise_for_status(); content = response.content
    except Exception as e: raise ValueError(f"Chyba sítě: {e}")
        
    for sep, enc in [(';', 'utf-8'), (';', 'cp1250'), (',', 'utf-8')]:
        if df is None:
            try: 
                df_temp = pd.read_csv(io.BytesIO(content), sep=sep, dtype=str, encoding=enc)
                if len(df_temp.columns) > 2: df = df_temp
            except: pass
                
    if df is None:
        try: df = pd.read_excel(io.BytesIO(content), dtype=str)
        except: pass
            
    if df is None:
        try: 
            dfs = pd.read_html(io.BytesIO(content), dtype=str)
            if dfs: df = dfs[0]
        except: pass
            
    if df is None: raise ValueError("Nepodařilo se rozpoznat formát souboru.")
    return df.loc[:, ~df.columns.duplicated()]

@st.cache_data(show_spinner=False, ttl=300)
def prepare_shop_data(url, prefix, eshop_name):
    df_raw = fetch_data_from_url(url)
    prod_col, amount_col, item_type_col, item_status_col = None, None, None, None
    for col in ['itemName', 'Název položky', 'productName', 'name', 'Název produktu', 'title', 'Položka', 'Produkt', 'Zboží']:
        if col in df_raw.columns: prod_col = col; break
    for col in ['itemAmount', 'amount', 'Množství', 'množství', 'count', 'itemCount', 'Počet', 'ks', 'Ks']:
        if col in df_raw.columns: amount_col = col; break
    for col in ['orderItemType', 'itemType', 'type', 'Typ položky']:
        if col in df_raw.columns: item_type_col = col; break
    for col in ['itemStatusName', 'Stav položky', 'itemStatus']:
        if col in df_raw.columns: item_status_col = col; break

    # NOVINKA: Hledání sloupečku s variantou (Ignoruje velikost písmen a mezery)
    variant_col = None
    for col in df_raw.columns:
        if str(col).strip().lower() in ['itemvariantname', 'variantname', 'varianta']:
            variant_col = col; break

    p_dict = {}
    v_dict = {} 
    skip_keywords = ['doprava', 'platba', 'dobírka', 'ppl', 'dpd', 'zásilkovna', 'gls', 'česká pošta', 'osobní odběr', 'kurýr', 'balíkovna', 'převodem', 'hotově', 'karta', 'kartou', 'gopay', 'comgate', 'dobirka', 'shoptet pay', 'twisto', 'payu']

    if 'code' in df_raw.columns and prod_col:
        for code, group in df_raw.groupby('code'):
            prods = []
            var_map = {} 
            for _, r in group.iterrows():
                # --- NUKLEÁRNÍ FILTR PRODUKTŮ ---
                if item_status_col and pd.notna(r[item_status_col]):
                    s_val = str(r[item_status_col]).strip().lower()
                    if any(x in s_val for x in ['stornov', 'vyřízen', 'vyrizen']):
                        if 'nevyřízen' not in s_val and 'nevyrizen' not in s_val:
                            continue
                        
                p_name = str(r[prod_col])
                
                # NOVINKA: Získání čisté varianty bez závorek
                v_name = ""
                if variant_col and pd.notna(r[variant_col]):
                    v_str = str(r[variant_col]).strip()
                    if v_str and v_str.lower() not in ['nan', 'none', '']:
                        v_name = v_str
                        
                if p_name and p_name.lower() not in ['nan', 'none']:
                    is_skip = False
                    if item_type_col and pd.notna(r[item_type_col]):
                        if str(r[item_type_col]).strip().lower() in ['shipping', 'billing', 'doprava', 'platba', 'discount', 'slevový kupón']: is_skip = True
                    else:
                        if any(kw in p_name.lower() for kw in skip_keywords): is_skip = True
                            
                    if not is_skip:
                        if amount_col and pd.notna(r[amount_col]):
                            try: amt = int(float(r[amount_col]))
                            except: amt = r[amount_col]
                            p_name_clean = f"{amt}x {p_name}"
                        else: 
                            p_name_clean = p_name
                        
                        # NOVINKA: Varianta se bezpečně a natvrdo propojí rovnou s produktem!
                        if v_name: 
                            p_name_clean = f"{p_name_clean} [Varianta: {v_name}]"
                        
                        prods.append(p_name_clean)
            p_dict[prefix + str(code)] = "<br>- " + "<br>- ".join(prods) if prods else "<br><i>Neznámé produkty</i>"
            v_dict[prefix + str(code)] = var_map # Přidání do skrytého slovníku

    if 'code' in df_raw.columns: 
        df = df_raw.drop_duplicates(subset=['code']).copy(); df.rename(columns={'code': 'id'}, inplace=True)
    else: 
        df = df_raw.copy()
        if 'id' not in df.columns: df['id'] = [f"Neznamé-{i}" for i in range(len(df))]

    df['id'] = prefix + df['id'].astype(str); df['eshop'] = eshop_name
    
    # --- NUKLEÁRNÍ FILTR VELKOODBĚRATELŮ (VŠE) ---
    mask_vw = pd.Series([False] * len(df), index=df.index)
    for col in df.columns:
        mask_vw = mask_vw | df[col].astype(str).str.lower().str.contains('velkoodběratel|velkoobchod', na=False, regex=True)
    df = df[~mask_vw].copy()
    
    return df, p_dict, v_dict

df_maxi, dict_maxi, v_maxi = pd.DataFrame(), {}, {}
df_vomaks, dict_vomaks, v_vomaks = pd.DataFrame(), {}, {}
df_sleva, dict_sleva, v_sleva = pd.DataFrame(), {}, {}
df_shop = pd.DataFrame(); products_dict = {}

with st.spinner("Stahuji a zpracovávám data ze všech e-shopů..."):
    try: df_maxi, dict_maxi, v_maxi = prepare_shop_data(SHOP1_URL, "MAX-", "Max-i.cz")
    except Exception as e: st.error(f"⚠️ Nelze načíst Max-i.cz: {e}")
    try: df_vomaks, dict_vomaks, v_vomaks = prepare_shop_data(SHOP2_URL, "VOM-", "Vomaks.cz")
    except Exception as e: st.error(f"⚠️ Nelze načíst Vomaks.cz: {e}")
    try: df_sleva, dict_sleva, v_sleva = prepare_shop_data(SHOP3_URL, "SLE-", "Slevadoma.cz")
    except Exception as e: st.error(f"⚠️ Nelze načíst Slevadoma.cz: {e}")

    if df_maxi.empty and df_vomaks.empty and df_sleva.empty: 
        st.warning("Nepodařilo se stáhnout žádná data ze Shoptetu."); st.stop()
        
    df_shop = pd.concat([df_maxi, df_vomaks, df_sleva], ignore_index=True)
    products_dict = {**dict_maxi, **dict_vomaks, **dict_sleva}
    st.session_state['variants_dict'] = {**v_maxi, **v_vomaks, **v_sleva} # <--- Uložení do paměti aplikace

# --- FRAGMENT PRO ŽIVÉ NAČÍTÁNÍ HISTORIE ---
try: fragment_decorator = st.fragment(run_every=1800) # Změněno z 10 sekund na 1800 sekund (30 minut)
except AttributeError: 
    def fragment_decorator(func): return func

# --- VYSKAKOVACÍ OKNO PRO UPOZORNĚNÍ ---
@st.dialog("🚨 UPOZORNĚNÍ: Posun času u POTVRZENÝCH objednávek!")
def show_warning_popup():
    st.info("Vlivem úprav trasy se zásadně změnil čas dojezdu k těmto zákazníkům. Zvažte jejich informování:")
    for w in st.session_state['dispatch_warnings']:
        st.markdown(f"{w}")
    st.write("")
    if st.button("✅ Rozumím, varování skrýt", type="primary", use_container_width=True):
        st.session_state['dispatch_warnings'] = []
        st.rerun()
# ----------------------------------------

@fragment_decorator
def render_history_and_dispatch():
    st.markdown("---")
    col_hdr1, col_hdr2 = st.columns([5, 1])
    col_hdr1.subheader("📁 Správa rozvozů a Digitální dispečink")
    if col_hdr2.button("🔄 Obnovit stavy", use_container_width=True): pass
    
    fresh_routes = load_routes()
    
    # --- PŘÍPRAVA PRO UPSELLING ---
    saved_routes_ids_global = set()
    for gr in fresh_routes: 
        if gr.get('status') != 'trashed':
            # NOVINKA: Ignorujeme zrušené objednávky pro upsell
            for oid in gr.get('orders', []):
                if gr.get('details', {}).get(oid, {}).get('dispatch_status') != 'Zrušeno':
                    saved_routes_ids_global.add(oid)
    
    global_unassigned = df_shop[~df_shop['id'].isin(saved_routes_ids_global)]
    target_statuses = ['skladem', 'naskladněno', 'naskladneno']
    # NOVINKA: Změněno z částečné shody na naprosto přesnou shodu (s ošetřením mezer)
    mask_s = global_unassigned['statusName'].astype(str).str.lower().apply(lambda x: x.strip() in target_statuses)
    potentials = global_unassigned[mask_s].copy()
    
    geo_cache = load_geo_cache()
    geocoded = 0
    potentials_with_geo = []
    
    for idx, s_row in potentials.iterrows():
        addr_parts = [s_row.get('deliveryStreet', s_row.get('billStreet', '')),
                      s_row.get('deliveryHouseNumber', s_row.get('billHouseNumber', '')),
                      s_row.get('deliveryCity', s_row.get('billCity', '')),
                      s_row.get('deliveryZip', s_row.get('billZip', ''))]
        addr_clean = " ".join([str(x).strip() for x in addr_parts if pd.notna(x) and str(x).strip().lower() not in ['nan', 'none', '<na>', '']]).strip()
        
        c_lat, c_lon = None, None
        if addr_clean in geo_cache:
            c_lat, c_lon = geo_cache[addr_clean][:2]
        else:
            if geocoded < 5:
                c_lat, c_lon = geocode_address_api(addr_clean, mapy_api_key)
                if c_lat and c_lon:
                    geo_cache[addr_clean] = [c_lat, c_lon]
                    geocoded += 1
        
        if c_lat and c_lon:
            potentials_with_geo.append({
                'id': s_row['id'],
                'prijemce': str(s_row.get('deliveryFullName', s_row.get('billFullName', ''))),
                'adresa': addr_clean,
                'lat': c_lat,
                'lon': c_lon,
                'eshop': s_row.get('eshop', ''),
                'cod': parse_cod(s_row.get('geisDeliveryPriceToPay', s_row.get('priceToPay', '0'))),
                'produkty': products_dict.get(s_row['id'], ''),
                'status': s_row.get('statusName', '')
            })
            
    if geocoded > 0: save_geo_cache(geo_cache)
    
    if not fresh_routes:
        st.info("Zatím nemáte žádné uložené rozvozy. Začněte výběrem e-shopů níže.")
        return

    tab_active, tab_history, tab_search = st.tabs(["🟢 Aktivní rozvozy & Dispečink", "🏁 Historie (Odjeto)", "🔍 Hledat zásilku"])

    active_routes = [r for r in fresh_routes if r.get('status', 'active') != 'completed']
    completed_routes = [r for r in fresh_routes if r.get('status', 'active') == 'completed']

    if 'active_costs' not in st.session_state: st.session_state['active_costs'] = None

    with tab_active:
        if not active_routes:
            st.success("Aktuálně nemáte žádné aktivní rozvozy. Vše je odjeto!")
        else:
            # --- NOVINKA: ŘAZENÍ OD NEJDŘÍVĚJŠÍHO (Neurčeno padá nakonec) ---
            def get_sort_key(route):
                d = route.get('route_date', '')
                if not d or d == 'Neurčeno': return '9999-12-31'
                return d
            
            sorted_active = sorted(active_routes, key=get_sort_key)
            
            for r in sorted_active:
                with st.container():
                    r_id = r.get('id', '')
                    is_trashed = (r.get('status') == 'trashed')
                    
                    if is_trashed:
                        if 'itinerary_data' in r:
                            orders_only = [row['Číslo objednávky'] for row in r['itinerary_data'] if row['Číslo objednávky'] not in ['START', 'CÍL']]
                            stats_str = f"📦 {len(orders_only)} obj."
                        else: stats_str = f"📦 {len(r.get('orders', []))} obj."
                        
                        st.markdown(f"<div style='padding: 15px; background-color: #f2f4f4; border-radius: 8px; border: 2px dashed #bdc3c7; opacity: 0.8; margin-bottom: 10px;'><b>🗑️ PŘIPRAVENO KE SMAZÁNÍ: {r['name']}</b><br><span style='font-size: 0.9em; color: #7f8c8d;'>{stats_str} (Objednávky z této trasy byly uvolněny zpět na mapu)</span></div>", unsafe_allow_html=True)
                        
                        c_res, c_del_perm, c_empty = st.columns([1.5, 1.5, 5])
                        if c_res.button("♻️ Obnovit rozvoz", key=f"res_{r_id}", type="primary"):
                            all_r = load_routes()
                            other_active = [x for x in all_r if x['id'] != r_id and x.get('status', 'active') == 'active']
                            conflicts = []
                            for oid in r.get('orders', []):
                                for other in other_active:
                                    if oid in other.get('orders', []):
                                        conflicts.append({'oid': oid, 'other_id': other['id'], 'other_name': other['name']})
                            if conflicts:
                                st.session_state[f'restore_conflicts_{r_id}'] = conflicts
                            else:
                                for rdb in all_r:
                                    if rdb['id'] == r_id: rdb['status'] = 'active'
                                save_routes(all_r)
                            st.rerun()
                            
                        if c_del_perm.button("❌ Trvale smazat", key=f"perm_{r_id}"):
                            safe_save_route(None, delete_id=r_id)
                            if f'restore_conflicts_{r_id}' in st.session_state: del st.session_state[f'restore_conflicts_{r_id}']
                            st.rerun()
                            
                        if f'restore_conflicts_{r_id}' in st.session_state:
                            conf = st.session_state[f'restore_conflicts_{r_id}']
                            st.warning("⚠️ **Některé objednávky už jsou v jiné trase!** Vyberte, jak to máme vyřešit:")
                            with st.form(key=f"resolve_{r_id}"):
                                choices = {}
                                for c in conf:
                                    choices[c['oid']] = st.radio(f"Objednávka [{c['oid']}] je nyní v trase '{c['other_name']}':", ["Vrátit sem (Vymazat z nového)", "Nechat tam (Vyřadit z tohoto)"], key=f"rad_{r_id}_{c['oid']}")
                                
                                if st.form_submit_button("Potvrdit a obnovit rozvoz", type="primary"):
                                    all_r = load_routes()
                                    target = next(x for x in all_r if x['id'] == r_id)
                                    for c in conf:
                                        if "Vrátit sem" in choices[c['oid']]:
                                            other = next((x for x in all_r if x['id'] == c['other_id']), None)
                                            if other and c['oid'] in other.get('orders', []):
                                                other['orders'].remove(c['oid'])
                                                if 'itinerary_data' in other:
                                                    other['itinerary_data'] = [row for row in other['itinerary_data'] if row['Číslo objednávky'] != c['oid']]
                                        else:
                                            if c['oid'] in target.get('orders', []):
                                                target['orders'].remove(c['oid'])
                                            if 'itinerary_data' in target:
                                                target['itinerary_data'] = [row for row in target['itinerary_data'] if row['Číslo objednávky'] != c['oid']]
                                    target['status'] = 'active'
                                    save_routes(all_r)
                                    del st.session_state[f'restore_conflicts_{r_id}']
                                    st.rerun()
                        st.markdown("---")
                        continue # Přeskočí normální vykreslení pro smazaný rozvoz
                        
                    locked_by = r.get('locked_by', '')
                    lock_age = time.time() - r.get('locked_at', 0)
                    is_locked = bool(locked_by and locked_by != st.session_state.get('st_user_name', 'Dispečer') and lock_age < 7200)
                    
                    # Detekce návrhů pro upsell
                    candidates = []
                    if 'itinerary_data' in r and not is_locked:
                        route_pts = [(row['lat'], row['lon']) for row in r['itinerary_data'] if pd.notna(row.get('lat')) and row['Číslo objednávky'] not in ['START', 'CÍL']]
                        if route_pts:
                            for p_geo in potentials_with_geo:
                                min_dist = min([geodesic((p_geo['lat'], p_geo['lon']), pt).kilometers for pt in route_pts])
                                if min_dist <= st.session_state.get('st_upsell_radius', 10):
                                    p_geo_copy = p_geo.copy()
                                    p_geo_copy['dist'] = min_dist
                                    candidates.append(p_geo_copy)
                    
                    col_title, col_gen, col_up, col_disp, col_lbl, col_sugg, col_fin, col_del = st.columns([2.0, 1.2, 1.0, 1.3, 1.0, 1.0, 1.2, 1.0])
                    
                    if 'itinerary_data' in r:
                        orders_only = [row['Číslo objednávky'] for row in r['itinerary_data'] if row['Číslo objednávky'] not in ['START', 'CÍL']]
                        total_orders = len(orders_only)
                        c_pot = sum(1 for oid in orders_only if r.get('details', {}).get(oid, {}).get('dispatch_status') == 'Potvrzeno')
                        c_sms = sum(1 for oid in orders_only if r.get('details', {}).get(oid, {}).get('dispatch_status') == 'SMS')
                        c_zru = sum(1 for oid in orders_only if r.get('details', {}).get(oid, {}).get('dispatch_status') == 'Zrušeno')
                        stats_str = f"📦 {total_orders} obj. (✅ {c_pot} | 💬 {c_sms} | ❌ {c_zru})"
                    else: stats_str = f"📦 {len(r.get('orders', []))} obj."
                    
                    # --- NOVINKA: Zobrazení dne a BAREVNÉ PODBARVENÍ ---
                    r_date_val = r.get('route_date', '')
                    is_unknown = (r_date_val == 'Neurčeno' or not r_date_val)
                    
                    if is_unknown:
                        day_name_str = "❓ Neurčeno | "
                        bg_style = "background-color: #fdf2e9; border-left: 6px solid #e67e22; padding: 12px; border-radius: 6px;"
                    else:
                        cz_days = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
                        try: day_name_str = f"{cz_days[datetime.strptime(r_date_val, '%Y-%m-%d').weekday()]} | "
                        except: day_name_str = ""
                        bg_style = "background-color: #eafaf1; border-left: 6px solid #2ecc71; padding: 12px; border-radius: 6px;"
                    # ----------------------------------------------------------
                    
                    lock_str = f"<br><span style='color:#e74c3c; font-weight:bold;'>🔒 Právě upravuje: {locked_by}</span>" if is_locked else ""
                    col_title.markdown(f"<div style='{bg_style}'>**🗓️ {day_name_str}{r['name']}**<br><span style='font-size: 0.95em; color: #555;'>{stats_str} &nbsp;|&nbsp; 🛣️ {int(r.get('total_km', 0))} km &nbsp;|&nbsp; 💰 {int(r.get('total_cod', 0))} Kč &nbsp;|&nbsp; 🚚 Toptrans: {int(r.get('total_tt_price', 0))} Kč</span>{lock_str}</div>", unsafe_allow_html=True)
                    
                    if is_locked:
                        if col_title.button("🔓 Vynutit odemčení", key=f"force_unlock_{r_id}"):
                            update_route_lock(r_id, lock=False); st.rerun()

                    if col_gen.button("🖨️ Připravit PDF k tisku", key=f"prep_{r_id}", use_container_width=True, disabled=is_locked):
                        if 'itinerary_data' in r:
                            with st.spinner("Bleskově generuji čisté PDF ze zálohy..."):
                                active_rows = [row for row in r['itinerary_data'] if row['Číslo objednávky'] in ['START', 'CÍL'] or r['details'].get(row['Číslo objednávky'], {}).get('dispatch_status') != 'Zrušeno']
                                # --- NOVINKA: PŘIDÁNÍ POČTU BALÍKŮ PRO ŘIDIČE ---
                                for row_dict in active_rows:
                                    oid = row_dict['Číslo objednávky']
                                    if oid not in ['START', 'CÍL']:
                                        row_dict['Počet balíků'] = r.get('details', {}).get(oid, {}).get('pkg_count', 1)
                                # ------------------------------------------------
                                df_itin = pd.DataFrame(active_rows)
                                pdf_dict = generate_all_pdfs(
                                    r['name'], df_itin, r.get('total_km', 0), r.get('total_hours', ''), 
                                    r.get('total_cod', 0), r.get('kasac_value', 2000), r.get('start_time_str', '06:00'), mapy_api_key,
                                    r.get('sleep_after_oid'), r.get('day2_start_time_str', '08:00')
                                )
                                buffer_xls = io.BytesIO()
                                with pd.ExcelWriter(buffer_xls, engine='openpyxl') as writer: df_itin.to_excel(writer, index=False, sheet_name='Trasový soupis')
                                pdf_dict['xls'] = buffer_xls.getvalue()
                                st.session_state[f"ready_pdfs_{r_id}"] = pdf_dict
                                st.rerun()
                        else: st.error("Starý formát rozvozu. Otevřete a uložte jej znovu.")
                            
                    if col_up.button("✏️ Otevřít na mapě", key=f"open_{r_id}", use_container_width=True, disabled=is_locked):
                        with st.spinner("Otevírám rozvoz na mapě..."):
                            update_route_lock(r_id, lock=True)
                            st.session_state['trigger_load'] = r
                            st.rerun()
                        
                    if col_disp.button("🖥️ Digitální dispečink", key=f"disp_{r_id}", use_container_width=True, type="secondary", disabled=is_locked):
                        if st.session_state.get('active_dispatch') == r_id:
                            st.session_state['active_dispatch'] = None
                            update_route_lock(r_id, lock=False)
                        else:
                            st.session_state['active_dispatch'] = r_id
                            st.session_state['active_labels'] = None
                            st.session_state['active_suggestion'] = None
                            update_route_lock(r_id, lock=True)
                        st.rerun()

                    if col_lbl.button("🏷️ Štítky", key=f"lbl_btn_{r_id}", use_container_width=True, type="secondary", disabled=is_locked):
                        if st.session_state.get('active_labels') == r_id:
                            st.session_state['active_labels'] = None
                            update_route_lock(r_id, lock=False)
                        else:
                            st.session_state['active_labels'] = r_id
                            st.session_state['active_dispatch'] = None
                            st.session_state['active_suggestion'] = None
                            update_route_lock(r_id, lock=True)
                        st.rerun()
                        
                    if candidates:
                        if col_sugg.button(f"💡 Návrhy (+{len(candidates)})", key=f"sugg_{r_id}", use_container_width=True, disabled=is_locked):
                            if st.session_state.get('active_suggestion') == r_id:
                                st.session_state['active_suggestion'] = None
                            else:
                                st.session_state['active_suggestion'] = r_id
                                st.session_state['active_dispatch'] = None
                                st.session_state['active_labels'] = None
                            st.rerun()
                    else:
                        col_sugg.write("")

                    if col_fin.button("🚚 Odjel", key=f"fin_{r_id}", use_container_width=True, disabled=is_locked, type="primary"):
                        log_action("Auto odjelo", "Auto naložené a vychstané opustilo sklad", rozvoz=r['name'])
                        all_r = load_routes()
                        for route_in_db in all_r:
                            if route_in_db['id'] == r_id:
                                route_in_db['status'] = 'completed'
                        save_routes(all_r)
                        if st.session_state.get('active_dispatch') == r_id: st.session_state['active_dispatch'] = None
                        if st.session_state.get('active_labels') == r_id: st.session_state['active_labels'] = None
                        if st.session_state.get('active_suggestion') == r_id: st.session_state['active_suggestion'] = None
                        st.rerun()
                        
                    if col_del.button("🗑️ Smazat", key=f"del_{r_id}", use_container_width=True, disabled=is_locked):
                        log_action("Smazání", "Rozvoz přesunut do koše", rozvoz=r['name'])
                        all_r = load_routes()
                        for rdb in all_r:
                            if rdb['id'] == r_id: rdb['status'] = 'trashed'
                        save_routes(all_r)
                        if st.session_state.get('active_dispatch') == r_id: st.session_state['active_dispatch'] = None
                        if st.session_state.get('active_labels') == r_id: st.session_state['active_labels'] = None
                        if st.session_state.get('active_suggestion') == r_id: st.session_state['active_suggestion'] = None
                        st.rerun()
                        
                    if f"ready_pdfs_{r_id}" in st.session_state:
                        pdf_dict = st.session_state[f"ready_pdfs_{r_id}"]
                        st.success("✅ Soubory jsou čisté a připravené ke stažení!")
                        dl1, dl2, dl3, dl4 = st.columns(4)
                        dl1.download_button("📥 PDF Řidič", data=pdf_dict['pdf_dr'], file_name=f"{r['name']}_ridic.pdf", mime="application/pdf", key=f"dl_dr_{r_id}", type="primary", use_container_width=True)
                        dl2.download_button("📥 PDF Dispečer", data=pdf_dict['pdf_di'], file_name=f"{r['name']}_dispecer.pdf", mime="application/pdf", key=f"dl_di_{r_id}", type="primary", use_container_width=True)
                        dl3.download_button("📥 PDF Sklad", data=pdf_dict['pdf_wa'], file_name=f"{r['name']}_sklad.pdf", mime="application/pdf", key=f"dl_wa_{r_id}", type="primary", use_container_width=True)
                        dl4.download_button("📊 Excel", data=pdf_dict['xls'], file_name=f"{r['name']}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"dl_xl_{r_id}", type="secondary", use_container_width=True)

                    # --- ZOBRAZENÍ NÁVRHŮ K DOPLNĚNÍ ---
                    if st.session_state.get('active_suggestion') == r_id and 'itinerary_data' in r:
                        st.markdown(f"### 💡 Návrh na doplnění rozvozu: {r['name']}")
                        if st.button("🔒 ZAVŘÍT NÁVRHY", type="primary", key=f"close_sugg_{r_id}"):
                            st.session_state['active_suggestion'] = None
                            if f'preview_{r_id}' in st.session_state: del st.session_state[f'preview_{r_id}']
                            st.rerun()
                            
                        st.info(f"Systém našel následující nevyřízené objednávky (Skladem / Naskladněno) v okruhu {st.session_state.get('st_upsell_radius', 10)} km od trasy.")
                        
                        selected_cands = []
                        for c in candidates:
                            p_html = c['produkty'].replace('<br>- ', ', ').replace('<i>', '').replace('</i>', '').strip(' ,')
                            chk = st.checkbox(f"➕ {c['prijemce']} | {c['adresa']} (Vzdálenost od trasy: {c['dist']:.1f} km) | 💰 {c['cod']} Kč", key=f"chk_sugg_{r_id}_{c['id']}")
                            st.caption(f"📦 Zboží: {p_html}")
                            if chk:
                                selected_cands.append(c)
                                
                        if selected_cands:
                            if st.button("🛠️ Náhled a přepočítat trasu", type="primary", key=f"prev_sugg_{r_id}"):
                                with st.spinner("Přepočítávám logistickou smyčku a časy..."):
                                    preview_r = build_preview(r, selected_cands, mapy_api_key)
                                    st.session_state[f'preview_{r_id}'] = preview_r
                                    
                        if f'preview_{r_id}' in st.session_state:
                            pr = st.session_state[f'preview_{r_id}']
                            st.markdown("---")
                            st.markdown("#### 🗺️ Náhled nové trasy")
                            
                            # Funkce pro výpočet rozdílu časů
                            def parse_time_str(t_str):
                                try:
                                    h = int(t_str.split('h')[0].strip())
                                    m = int(t_str.split('h')[1].replace('min', '').strip())
                                    return h * 60 + m
                                except: return 0
                                
                            old_m = parse_time_str(r.get('total_hours', '0h 0min'))
                            new_m = parse_time_str(pr.get('total_hours', '0h 0min'))
                            diff_m = new_m - old_m
                            
                            sign = "+" if diff_m >= 0 else "-"
                            abs_m = abs(diff_m)
                            diff_str = f"{sign} {abs_m//60}h {abs_m%60}min" if abs_m >= 60 else f"{sign} {abs_m} min"
                            
                            # Nové a mnohem čistší zobrazení metrik
                            c_km, c_time = st.columns(2)
                            km_diff = int(pr['total_km']) - int(r.get('total_km', 0))
                            
                            c_km.metric("Nová délka trasy", f"{int(pr['total_km'])} km", f"{km_diff:+} km (původně {int(r.get('total_km', 0))} km)", delta_color="inverse")
                            c_time.metric("Nový čas jízdy", f"{pr['total_hours']}", f"{diff_str} (původně {r.get('total_hours', '0h 0min')})", delta_color="inverse")
                            
                            m_prev = folium.Map(location=[pr['itinerary_data'][0]['lat'], pr['itinerary_data'][0]['lon']], zoom_start=8)
                            pts_prev = []
                            for idx_p, row_p in enumerate(pr['itinerary_data']):
                                if pd.notna(row_p['lat']):
                                    pts_prev.append((row_p['lat'], row_p['lon']))
                                    lbl = "S" if row_p['Číslo objednávky'] == "START" else ("C" if row_p['Číslo objednávky'] == "CÍL" else str(idx_p))
                                    folium.Marker([row_p['lat'], row_p['lon']], icon=BeautifyIcon(icon_shape='marker', text_color='white', background_color='#3498db', number=lbl)).add_to(m_prev)
                            folium.PolyLine(pts_prev, color="blue", weight=3, opacity=0.8).add_to(m_prev)
                            st_folium(m_prev, height=400, use_container_width=True, key=f"map_prev_{r_id}")
                            
                            c_ok, c_no = st.columns(2)
                            if c_ok.button("✅ Líbí se mi to, PŘEPSAT TRASU A OTEVŘÍT", type="primary", use_container_width=True, key=f"save_sugg_{r_id}"):
                                all_r = load_routes()
                                for i_r, rdb in enumerate(all_r):
                                    if rdb['id'] == r_id:
                                        all_r[i_r] = pr
                                save_routes(all_r)
                                del st.session_state[f'preview_{r_id}']
                                st.session_state['active_suggestion'] = None
                                
                                # Rovnou otevřeme trasu do pracovního prostoru
                                st.session_state['trigger_load'] = pr
                                
                                st.success("Trasa úspěšně aktualizována a načítá se do editoru!")
                                time.sleep(1.0)
                                st.rerun()
                                
                            if c_no.button("❌ Nelíbí, VZÍT ZPĚT", use_container_width=True, key=f"cancel_sugg_{r_id}"):
                                del st.session_state[f'preview_{r_id}']
                                st.rerun()

                    # ROZBALOVACÍ DIGITÁLNÍ DISPEČINK
                    if st.session_state.get('active_dispatch') == r_id and 'itinerary_data' in r:
                        st.markdown(f"### 📡 Aktivní dispečink: {r['name']}")
                        
                        # --- NOVINKA: Sticky (plovoucí) tlačítko pro zavření ---
                        st.markdown("""
                        <style>
                            div[data-testid="stElementContainer"]:has(.disp-anchor) + div[data-testid="stElementContainer"] {
                                position: -webkit-sticky !important;
                                position: sticky !important;
                                top: 55px !important; /* Zarovnání pod horní lištu Streamlitu */
                                z-index: 9999 !important;
                                background-color: var(--background-color, white) !important;
                                padding-top: 10px !important;
                                padding-bottom: 15px !important;
                                border-bottom: 2px solid #f0f2f6 !important;
                                box-shadow: 0px 15px 15px -15px rgba(0,0,0,0.15) !important;
                            }
                        </style>
                        <div class="disp-anchor"></div>
                        """, unsafe_allow_html=True)
                        # -------------------------------------------------------
                        
                        if st.button("🔒 ZAVŘÍT DISPEČINK A ODEMKNOUT TRASU", type="primary", key=f"close_disp_{r_id}", use_container_width=True):
                            st.session_state['active_dispatch'] = None
                            update_route_lock(r_id, lock=False)
                            st.rerun()
                            
                        # --- NOVINKA: Zobrazení varování jako POP-UP ---
                        if st.session_state.get('dispatch_warnings'):
                            show_warning_popup()
                        # --- KONEC VAROVÁNÍ ---
                        
                        # Extrakt data rozvozu pro UI
                        r_date_str = r.get('route_date', datetime.today().strftime('%Y-%m-%d'))
                        try:
                            rd_obj = datetime.strptime(r_date_str, '%Y-%m-%d')
                            d2_ui_str = (rd_obj + timedelta(days=1)).strftime('%d.%m.%Y')
                        except:
                            d2_ui_str = "Další den"
                            
                        stop_idx_disp = 1
                        is_currently_day2 = False # Sledování přelomu dnů
                        for r_idx, row in enumerate(r['itinerary_data']):
                            oid = row['Číslo objednávky']
                            if oid in ['START', 'CÍL']: continue
                                
                            status = r['details'].get(oid, {}).get('dispatch_status', '')
                            current_note = r['details'].get(oid, {}).get('note', '')
                            
                            if status == "Zrušeno":
                                stop_display = "❌ ZRUŠENO"
                            else:
                                day_lbl = f" (☀️ 2. DEN: {d2_ui_str})" if is_currently_day2 else " (1. DEN)"
                                stop_display = f"📍 {stop_idx_disp}. zastávka{day_lbl}"
                                stop_idx_disp += 1
                            
                            p_html = str(row.get('Produkty', ''))
                            raw_prods = [p.strip() for p in p_html.replace('<i>', '').replace('</i>', '').split('<br>') if p.strip() and p.strip() != '-']
                            prods_clean = []
                            for p in raw_prods:
                                if p.startswith('- '): p = p[2:]
                                if p.startswith('• '): p = p[2:]
                                if p: prods_clean.append(p)
                            
                            phone_raw = str(row.get('Telefon', '')).strip()
                            if phone_raw.lower() in ['none', 'nan', '', '-']: phone_display = "-"
                            else:
                                prefix, main_num = "", phone_raw
                                if phone_raw.startswith("+420") or phone_raw.startswith("+421"): 
                                    prefix = phone_raw[:4]; main_num = phone_raw[4:].strip()
                                m_c = main_num.replace(" ", "")
                                main_num = f"{m_c[:3]} {m_c[3:6]} {m_c[6:]}" if len(m_c)==9 else " ".join([m_c[i:i+3] for i in range(0, len(m_c), 3)])
                                if prefix: phone_display = f"<span style='font-size:0.75em; color:#7f8c8d;'>{prefix}</span> <span style='font-size:1.2em;'>{main_num}</span>"
                                else: phone_display = f"<span style='font-size:1.2em;'>{main_num}</span>"

                            if status == "Zrušeno": time_display = "<span style='font-size:1.3em; color:#7f8c8d;'><b>ZRUŠENO</b></span>"
                            else: 
                                day_suffix = f" <b style='color:#8e44ad;'>(Další den: {d2_ui_str})</b>" if is_currently_day2 else ""
                                time_display = f"<span style='font-size:1.3em; color:#e74c3c;'><b>{row.get('Okno příjezdu (2h)', '-')}</b></span>{day_suffix} <span style='font-size:0.85em; color:#7f8c8d;'>(Cca: {row.get('Čas příjezdu', '-')})</span>"
                                
                            is_focused = (st.session_state.get('focus_oid') == oid)
                            
                            if status == "Potvrzeno": border_col = "#2ecc71"; bg_col = "#eafaf1"; text_col = "#2c3e50"; opacity = "1.0"; badge = ""
                            elif status == "SMS": border_col = "#f39c12"; bg_col = "#fef5e7"; text_col = "#2c3e50"; opacity = "1.0"; badge = ""
                            elif status == "Zrušeno": border_col = "#bdc3c7"; bg_col = "#f2f4f4"; text_col = "#95a5a6"; opacity = "0.6"; badge = ""
                            else: border_col = "#bdc3c7"; bg_col = "#f8f9f9"; text_col = "#2c3e50"; opacity = "1.0"; badge = ""
                                
                            if is_focused:
                                border_col = "#e74c3c"
                                bg_col = "#fdf2e9"
                                badge = " <span style='background-color:#e74c3c; color:white; padding:3px 8px; border-radius:5px; font-size:0.7em;'>🎯 HLEDANÁ OBJEDNÁVKA</span>"
                                
                            st.markdown(f"""
                            <div id="target_{oid}" style="border: {'3px' if is_focused else '2px'} solid {border_col}; background-color: {bg_col}; padding: 15px; border-radius: 8px; margin-bottom: 5px; color: {text_col}; opacity: {opacity}; transition: all 0.5s ease;">
                                <h4 style="margin-top:0; color: {text_col};">{stop_display} | {row['Příjemce']} (Obj: {oid}) {badge}</h4>
                                <div style="margin-bottom: 5px;">
                                    <b>Čas:</b> {time_display} &nbsp;|&nbsp; <b>Tel:</b> {phone_display} &nbsp;|&nbsp; <b>Dobírka:</b> <span style="font-size:1.2em; font-weight:bold;">{row.get('Dobírka (Kč)', '0')} Kč</span>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            if is_focused:
                                st.html("<script>window.parent.document.getElementById('top_target').scrollIntoView({behavior: 'smooth', block: 'start'});</script>")
                                st.session_state['focus_oid'] = None
                            
                            with st.container():
                                st.markdown(f"<div style='font-size: 0.95em; color: {text_col}; opacity: {opacity}; margin-bottom: 5px;'><b>📦 Položky v objednávce:</b></div>", unsafe_allow_html=True)
                                if not prods_clean or "Neznámé" in "".join(prods_clean):
                                    st.markdown(f"<div style='font-size: 0.9em; padding-left: 10px; color: {text_col}; opacity: {opacity};'><i>Žádné specifické produkty v exportu</i></div>", unsafe_allow_html=True)
                                else:
                                    for p_idx, prod_name in enumerate(prods_clean):
                                        col_p, col_x = st.columns([15, 1])
                                        col_p.markdown(f"<div style='font-size: 0.9em; padding-top: 5px; color: {text_col}; opacity: {opacity};'>• {prod_name}</div>", unsafe_allow_html=True)
                                        
                                        if status != "Zrušeno":
                                            if col_x.button("❌", key=f"del_p_{r_id}_{oid}_{p_idx}", help="Odebrat tento produkt z objednávky"):
                                                st.session_state[f"ask_del_{r_id}_{oid}_{p_idx}"] = True
                                                st.rerun()

                                        if st.session_state.get(f"ask_del_{r_id}_{oid}_{p_idx}", False):
                                            with st.container():
                                                m_qty = re.match(r'^(\d+)[xX]\s+(.*)', prod_name)
                                                if m_qty:
                                                    max_qty = int(m_qty.group(1))
                                                    clean_name = m_qty.group(2)
                                                else:
                                                    max_qty = 1
                                                    clean_name = prod_name

                                                st.markdown(f"<div style='background-color:#fff3cd; padding:15px; border-radius:8px; margin-top:5px;'>", unsafe_allow_html=True)
                                                st.markdown(f"**Úprava položky:** {clean_name} (v objednávce: {max_qty} ks)")
                                                
                                                if max_qty > 1:
                                                    remove_qty = st.number_input("Kolik kusů odebíráte?", min_value=1, max_value=max_qty, value=1, step=1, key=f"rm_qty_{r_id}_{oid}_{p_idx}")
                                                else:
                                                    remove_qty = 1
                                                    st.info("Odebíráte 1 ks (celou položku).")

                                                cod_val = parse_cod(row.get('Dobírka (Kč)', '0'))
                                                deduct_val = 0.0
                                                if cod_val > 0:
                                                    st.write(f"Původní dobírka: **{cod_val} Kč**")
                                                    deduct_opt = st.radio("Co s dobírkou?", ["Ponechat původní dobírku", "Odečíst z dobírky chybějící kusy"], key=f"deduct_opt_{r_id}_{oid}_{p_idx}")
                                                    if deduct_opt == "Odečíst z dobírky chybějící kusy":
                                                        deduct_val = st.number_input("O kolik Kč ponížit dobírku?", min_value=0.0, max_value=float(cod_val), value=0.0, step=10.0, key=f"deduct_val_{r_id}_{oid}_{p_idx}")
                                                else:
                                                    st.info("Objednávka je již zaplacena (Dobírka 0 Kč).")
                                                
                                                c_y, c_n = st.columns(2)
                                                if c_y.button("✅ Potvrdit úpravu", key=f"conf_del_{r_id}_{oid}_{p_idx}", type="primary", use_container_width=True):
                                                    if remove_qty == max_qty:
                                                        prods_clean.pop(p_idx)
                                                    else:
                                                        prods_clean[p_idx] = f"{max_qty - remove_qty}x {clean_name}"
                                                        
                                                    new_prods_str = "<br>- " + "<br>- ".join(prods_clean) if prods_clean else "<i>Žádné produkty</i>"
                                                    new_cod = cod_val - deduct_val
                                                    
                                                    all_r = load_routes()
                                                    for rdb in all_r:
                                                        if rdb['id'] == r_id:
                                                            for row_rdb in rdb['itinerary_data']:
                                                                if row_rdb['Číslo objednávky'] == oid:
                                                                    row_rdb['Produkty'] = new_prods_str
                                                                    row_rdb['Dobírka (Kč)'] = str(new_cod)
                                                            rdb['total_cod'] = sum(parse_cod(x['Dobírka (Kč)']) for x in rdb['itinerary_data'] if x['Číslo objednávky'] not in ['START', 'CÍL'] and rdb['details'].get(x['Číslo objednávky'], {}).get('dispatch_status') != 'Zrušeno')
                                                    save_routes(all_r)
                                                    st.session_state[f"ask_del_{r_id}_{oid}_{p_idx}"] = False
                                                    st.success("Objednávka byla aktualizována!")
                                                    time.sleep(1)
                                                    st.rerun()
                                                    
                                                if c_n.button("❌ Zrušit akci", key=f"canc_del_{r_id}_{oid}_{p_idx}", use_container_width=True):
                                                    st.session_state[f"ask_del_{r_id}_{oid}_{p_idx}"] = False
                                                    st.rerun()
                                                st.markdown("</div>", unsafe_allow_html=True)
                                                
                                if st.button("🔄 Vrátit původní zboží a dobírku", key=f"reset_prod_{r_id}_{oid}", help="Vrátí objednávce původní stav podle živých dat z e-shopu."):
                                    orig_row = df_shop[df_shop['id'] == oid]
                                    if not orig_row.empty:
                                        orig_cod = str(orig_row.iloc[0].get('geisDeliveryPriceToPay', orig_row.iloc[0].get('priceToPay', '0')))
                                        orig_prods = products_dict.get(oid, "<br><i>Žádné produkty v exportu</i>")
                                        
                                        all_r = load_routes()
                                        for rdb in all_r:
                                            if rdb['id'] == r_id:
                                                for row_rdb in rdb['itinerary_data']:
                                                    if row_rdb['Číslo objednávky'] == oid:
                                                        row_rdb['Produkty'] = orig_prods
                                                        row_rdb['Dobírka (Kč)'] = orig_cod
                                                rdb['total_cod'] = sum(parse_cod(x['Dobírka (Kč)']) for x in rdb['itinerary_data'] if x['Číslo objednávky'] not in ['START', 'CÍL'] and rdb['details'].get(x['Číslo objednávky'], {}).get('dispatch_status') != 'Zrušeno')
                                        save_routes(all_r)
                                        st.success("Původní stav objednávky byl obnoven!")
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        st.error("Tato objednávka už není v aktivních datech e-shopu.")
                            
                            st.write("")
                            
                            # --- NOVINKA: Úprava poznámky a dobírky vedle sebe ---
                            col_n1, col_n2 = st.columns([3, 2])
                            with col_n1:
                                note_key = f"disp_note_{r_id}_{oid}"
                                st.text_input("📝 Poznámka (vzkaz řidiči):", value=current_note, key=note_key, on_change=update_disp_note, args=(r_id, oid, note_key))
                                
                            with col_n2:
                                curr_cod = float(parse_cod(row.get('Dobírka (Kč)', 0)))
                                new_cod = st.number_input("💰 Upravit dobírku (Kč):", value=curr_cod, step=50.0, key=f"disp_cod_{r_id}_{oid}")
                                
                                if new_cod != curr_cod:
                                    if st.button("💾 Uložit novou částku", key=f"save_cod_{r_id}_{oid}", type="primary", use_container_width=True):
                                        all_r = load_routes()
                                        for rdb in all_r:
                                            if rdb['id'] == r_id:
                                                for row_rdb in rdb['itinerary_data']:
                                                    if row_rdb['Číslo objednávky'] == oid:
                                                        row_rdb['Dobírka (Kč)'] = str(new_cod)
                                                rdb['total_cod'] = sum(parse_cod(x['Dobírka (Kč)']) for x in rdb['itinerary_data'] if x['Číslo objednávky'] not in ['START', 'CÍL'] and rdb['details'].get(x['Číslo objednávky'], {}).get('dispatch_status') != 'Zrušeno')
                                        save_routes(all_r)
                                        # Smazání starého PDF, aby se nepřetisklo se starou cenou
                                        if f"ready_pdfs_{r_id}" in st.session_state: del st.session_state[f"ready_pdfs_{r_id}"]
                                        st.success("Dobírka úspěšně změněna!")
                                        time.sleep(1)
                                        st.rerun()
                            # -----------------------------------------------------
                            
                            if status == "Zrušeno":
                                if st.button(f"🔄 Obnovit objednávku (Vrátit do trasy)", key=f"restore_{r_id}_{oid}", use_container_width=True):
                                    r['details'][oid]['dispatch_status'] = ""
                                    if st.session_state.get('editing_route_id') == r_id:
                                        st.session_state['loaded_statuses'][oid] = ""
                                    with st.spinner("Obnovuji balík..."):
                                        warns = recalc_dispatch_route(r, mapy_api_key)
                                        safe_save_route(r, delete_id=r_id)
                                        if warns: st.session_state['dispatch_warnings'].extend(warns)
                                    st.rerun()
                            else:
                                b1, b2, b3 = st.columns(3)
                                if b1.button(f"✅ Potvrzené převzetí", key=f"ok_{r_id}_{oid}", use_container_width=True):
                                    log_action("Stav: Potvrzení rozvozu", "Zákazník může objednávku převzít", rozvoz=r['name'], objednavka=oid)
                                    r['details'][oid]['dispatch_status'] = "Potvrzeno"
                                    r['details'][oid]['confirmed_time'] = row.get('Čas příjezdu', '')
                                    r['details'][oid]['confirmed_window'] = row.get('Okno příjezdu (2h)', '')
                                    if st.session_state.get('editing_route_id') == r_id:
                                        st.session_state['loaded_statuses'][oid] = "Potvrzeno"
                                    safe_save_route(r, delete_id=r_id); st.rerun()
                                    
                                if b2.button(f"💬 Odeslána SMS", key=f"sms_{r_id}_{oid}", use_container_width=True):
                                    r['details'][oid]['dispatch_status'] = "SMS"
                                    if st.session_state.get('editing_route_id') == r_id:
                                        st.session_state['loaded_statuses'][oid] = "SMS"
                                    safe_save_route(r, delete_id=r_id); st.rerun()
                                    
                                if b3.button(f"❌ Nemůže převzít (Zešednout)", key=f"cancel_{r_id}_{oid}", use_container_width=True):
                                    r['details'][oid]['dispatch_status'] = "Zrušeno"
                                    if st.session_state.get('editing_route_id') == r_id:
                                        st.session_state['loaded_statuses'][oid] = "Zrušeno"
                                    with st.spinner("Uspávám balík..."):
                                        warns = recalc_dispatch_route(r, mapy_api_key)
                                        safe_save_route(r, delete_id=r_id)
                                        if warns: st.session_state['dispatch_warnings'].extend(warns)
                                    st.rerun()
                                    
                            # --- 🌟 VIZUÁLNÍ PŘEDĚL PRO DRUHÝ DEN V DISPEČINKU ---
                            if oid == r.get('sleep_after_oid'):
                                is_currently_day2 = True
                                st.markdown("""
                                <div style="text-align:center; margin: 25px 0; padding: 15px; background-color: #8e44ad; color: white; border-radius: 8px; font-weight: bold; font-size: 1.15em; box-shadow: 0px 4px 12px rgba(0,0,0,0.15);">
                                    🏨 ——— ŘIDIČ ZDE KONČÍ 1. DEN A JDE SPÁT ——— 🏨<br>
                                    <span style="font-size:0.85em; font-weight:normal; color:#f5eef8;">⚠️ Následující zákazníky obvolávejte s informací, že přijedeme až ZÍTRA!</span>
                                </div>
                                """, unsafe_allow_html=True)
                                
                    # ROZBALOVACÍ MENU PRO ŠTÍTKY
                    if st.session_state.get('active_labels') == r_id and 'itinerary_data' in r:
                        st.markdown(f"### 🏷️ Tisk štítků pro: {r['name']}")
                        if st.button("🔒 ZAVŘÍT ŠTÍTKY A ODEMKNOUT TRASU", key=f"close_lbl_{r_id}", type="primary"):
                            st.session_state['active_labels'] = None
                            update_route_lock(r_id, lock=False)
                            st.rerun()

                        st.info("Zadejte počet balíků (štítků) pro každou zastávku. Políčka jsou záměrně prázdná, abyste na žádné nezapomněli.")

                        pkg_counts = {}
                        stop_idx = 1
                        for row in r['itinerary_data']:
                            oid = row['Číslo objednávky']
                            if oid in ['START', 'CÍL']: continue
                            status = r['details'].get(oid, {}).get('dispatch_status', '')
                            if status == "Zrušeno": continue

                            def_count = r['details'].get(oid, {}).get('pkg_count', None)

                            p_html = row.get('Produkty', '')
                            p_plain = p_html.replace('<br>- ', ', ').replace('<br>• ', ', ').replace('<br>', ', ').replace('<i>', '').replace('</i>', '').strip(' ,')
                            if "Žádné produkty" in p_plain or not p_plain: p_plain = "<i>Žádné specifické produkty v exportu</i>"

                            col_a, col_b = st.columns([3, 1])
                            col_a.markdown(f"<div style='padding-top:10px;'><b>📍 Zastávka {stop_idx}. | {row['Příjemce']}</b> (Obj: {oid})<br><span style='font-size:0.85em; color:#7f8c8d;'>📦 {p_plain}</span></div>", unsafe_allow_html=True)
                            
                            count = col_b.number_input("Počet balíků:", min_value=1, value=def_count, step=1, key=f"pkg_{r_id}_{oid}")
                            pkg_counts[oid] = count
                            stop_idx += 1

                        all_filled = all(c is not None for c in pkg_counts.values())

                        if st.button("🖨️ Vygenerovat PDF se štítky", key=f"gen_lbl_{r_id}", type="primary"):
                            if not all_filled:
                                st.error("⚠️ Prosím, vyplňte počet balíků u VŠECH zastávek před vygenerováním štítků.")
                            else:
                                for oid, c in pkg_counts.items():
                                    if 'pkg_count' not in r['details'][oid] or r['details'][oid]['pkg_count'] != c:
                                        r['details'][oid]['pkg_count'] = c
                                safe_save_route(r, delete_id=r_id)

                                with st.spinner("Generuji štítky..."):
                                    pdf_bytes = generate_labels_pdf(r, pkg_counts)
                                    st.session_state[f"ready_labels_{r_id}"] = pdf_bytes
                                st.rerun()

                        if f"ready_labels_{r_id}" in st.session_state:
                            st.success("✅ Štítky jsou připravené k tisku! (Po stažení se rozvoz automaticky zavře a odemkne)")
                            
                            # Pokud uživatel klikne na stažení, provede se tento kód
                            if st.download_button("📥 Stáhnout PDF Štítky (A4 - 2x7)", data=st.session_state[f"ready_labels_{r_id}"], file_name=f"Stitky_{r['name']}.pdf", mime="application/pdf", key=f"dl_lbl_{r_id}", type="primary", use_container_width=True):
                                time.sleep(1)  # Malá pojistka, aby prohlížeč v klidu zachytil stahovaný soubor
                                st.session_state['active_labels'] = None
                                del st.session_state[f"ready_labels_{r_id}"]
                                update_route_lock(r_id, lock=False)
                                st.rerun()
                                
                st.markdown("---")

    with tab_history:
        if not completed_routes:
            st.info("Zatím zde nejsou žádné dokončené rozvozy.")
        else:
            for r in reversed(completed_routes):
                with st.container():
                    r_id = r.get('id', '')
                    costs = r.get('costs', {'fuel': 0.0, 'driver': 0.0, 'accommodation': 0.0, 'other': 0.0})
                    total_costs = sum(float(v) for v in costs.values())

                    if 'itinerary_data' in r:
                        orders_only = [row['Číslo objednávky'] for row in r['itinerary_data'] if row['Číslo objednávky'] not in ['START', 'CÍL']]
                        total_orders = len(orders_only)
                        stats_str = f"📦 {total_orders} obj. &nbsp;|&nbsp; 🛣️ {int(r.get('total_km', 0))} km &nbsp;|&nbsp; 💰 Dobírky: {int(r.get('total_cod', 0))} Kč &nbsp;|&nbsp; 🚚 Toptrans: {int(r.get('total_tt_price', 0))} Kč &nbsp;|&nbsp; 💸 Náklady: {int(total_costs)} Kč"
                    else: stats_str = f"📦 {len(r.get('orders', []))} obj."
                    
                    # --- NOVINKA: Zobrazení dne v týdnu pro historii ---
                    r_date_val = r.get('route_date', '')
                    if r_date_val == 'Neurčeno' or not r_date_val:
                        day_name_str = "❓ Neurčeno | "
                    else:
                        cz_days = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
                        try: day_name_str = f"{cz_days[datetime.strptime(r_date_val, '%Y-%m-%d').weekday()]} | "
                        except: day_name_str = ""
                    # ---------------------------------------------------
                    
                    col_title, col_gen, col_costs_btn, col_del = st.columns([4, 2, 2, 1])
                    col_title.markdown(f"**🏁 {day_name_str}{r['name']}**<br><span style='font-size: 0.95em; color: #555;'>{stats_str}</span>", unsafe_allow_html=True)
                    
                    if col_gen.button("🖨️ PDF Záloha", key=f"hist_prep_{r_id}", use_container_width=True):
                        with st.spinner("Generuji retrospektivní PDF..."):
                            active_rows = [row for row in r['itinerary_data'] if row['Číslo objednávky'] in ['START', 'CÍL'] or r['details'].get(row['Číslo objednávky'], {}).get('dispatch_status') != 'Zrušeno']
                            # --- NOVINKA: PŘIDÁNÍ POČTU BALÍKŮ PRO ŘIDIČE ---
                            for row_dict in active_rows:
                                oid = row_dict['Číslo objednávky']
                                if oid not in ['START', 'CÍL']:
                                    row_dict['Počet balíků'] = r.get('details', {}).get(oid, {}).get('pkg_count', 1)
                            # ------------------------------------------------
                            df_itin = pd.DataFrame(active_rows)
                            pdf_dict = generate_all_pdfs(
                                r['name'], df_itin, r.get('total_km', 0), r.get('total_hours', ''), 
                                r.get('total_cod', 0), r.get('kasac_value', 2000), r.get('start_time_str', '06:00'), mapy_api_key,
                                r.get('sleep_after_oid'), r.get('day2_start_time_str', '08:00')
                            )
                            buffer_xls = io.BytesIO()
                            with pd.ExcelWriter(buffer_xls, engine='openpyxl') as writer: df_itin.to_excel(writer, index=False, sheet_name='Trasový soupis')
                            pdf_dict['xls'] = buffer_xls.getvalue()
                            st.session_state[f"ready_pdfs_hist_{r_id}"] = pdf_dict
                            st.rerun()

                    if col_costs_btn.button("💰 Vyúčtování", key=f"hist_cost_btn_{r_id}", use_container_width=True):
                        if st.session_state.get('active_costs') == r_id:
                            st.session_state['active_costs'] = None
                        else:
                            st.session_state['active_costs'] = r_id
                        st.rerun()

                    if col_del.button("🗑️ Smazat", key=f"hist_del_{r_id}", use_container_width=True):
                        safe_save_route(None, delete_id=r_id)
                        if st.session_state.get('active_costs') == r_id: st.session_state['active_costs'] = None
                        st.rerun()

                    if f"ready_pdfs_hist_{r_id}" in st.session_state:
                        pdf_dict = st.session_state[f"ready_pdfs_hist_{r_id}"]
                        st.success("✅ Historické soubory jsou připravené ke stažení!")
                        dl1, dl2, dl3, dl4 = st.columns(4)
                        dl1.download_button("📥 PDF Řidič", data=pdf_dict['pdf_dr'], file_name=f"{r['name']}_ridic.pdf", mime="application/pdf", key=f"dl_dr_h_{r_id}", type="primary", use_container_width=True)
                        log_action("Tisk PDF", "Soupis pro řidiče", rozvoz=r['name'])
                        dl2.download_button("📥 PDF Dispečer", data=pdf_dict['pdf_di'], file_name=f"{r['name']}_dispecer.pdf", mime="application/pdf", key=f"dl_di_h_{r_id}", type="primary", use_container_width=True)
                        log_action("Tisk PDF", "Soupis pro dispečera", rozvoz=r['name'])
                        dl3.download_button("📥 PDF Sklad", data=pdf_dict['pdf_wa'], file_name=f"{r['name']}_sklad.pdf", mime="application/pdf", key=f"dl_wa_h_{r_id}", type="primary", use_container_width=True)
                        log_action("Tisk PDF", "Soupis pro skladníka", rozvoz=r['name'])
                        dl4.download_button("📊 Excel", data=pdf_dict['xls'], file_name=f"{r['name']}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"dl_xl_h_{r_id}", type="secondary", use_container_width=True)

                    if st.session_state.get('active_costs') == r_id:
                        st.markdown("#### 💸 Vyúčtování nákladů rozvozu")
                        with st.form(key=f"form_costs_{r_id}"):
                            c1, c2, c3, c4 = st.columns(4)
                            f_fuel = c1.number_input("⛽ Pohonné hmoty (Kč)", value=float(costs.get('fuel', 0)), step=100.0)
                            f_driver = c2.number_input("🧑‍✈️ Řidič (Kč)", value=float(costs.get('driver', 0)), step=100.0)
                            f_acc = c3.number_input("🏨 Ubytování (Kč)", value=float(costs.get('accommodation', 0)), step=100.0)
                            f_other = c4.number_input("🛠️ Ostatní (Kč)", value=float(costs.get('other', 0)), step=100.0)
                            
                            if st.form_submit_button("💾 Uložit náklady", type="primary"):
                                all_r = load_routes()
                                for route_in_db in all_r:
                                    if route_in_db['id'] == r_id:
                                        route_in_db['costs'] = {'fuel': f_fuel, 'driver': f_driver, 'accommodation': f_acc, 'other': f_other}
                                save_routes(all_r)
                                st.session_state['active_costs'] = None
                                st.rerun()
                st.markdown("---")

    with tab_search:
        st.markdown("### 🔍 Rychlé hledání objednávky a rychlý dispečink")
        search_q = st.text_input("Zadejte číslo objednávky nebo jméno zákazníka (stačí část):", key="search_orders")
        
        if search_q:
            sq = search_q.lower().strip()
            found_orders = []
            
            for r in fresh_routes:
                if 'itinerary_data' in r:
                    for row in r['itinerary_data']:
                        oid = str(row.get('Číslo objednávky', ''))
                        if oid in ['START', 'CÍL']: continue
                        
                        prijemce = str(row.get('Příjemce', ''))
                        if sq in oid.lower() or sq in prijemce.lower():
                            status = r.get('details', {}).get(oid, {}).get('dispatch_status', 'Zatím neřešeno')
                            if not status: status = "Zatím neřešeno"
                            note = r.get('details', {}).get(oid, {}).get('note', '')
                            
                            found_orders.append({
                                'route_name': r.get('name', 'Neznámý rozvoz'),
                                'route_status': r.get('status', 'active'),
                                'driver': r.get('driver_name', 'Nepřiřazen'),
                                'date': r.get('route_date', 'Neznámé datum'),
                                'oid': oid,
                                'prijemce': prijemce,
                                'time': row.get('Okno příjezdu (2h)', '-'),
                                'time_exact': row.get('Čas příjezdu', '-'),
                                'cod': row.get('Dobírka (Kč)', '0'),
                                'products': row.get('Produkty', ''),
                                'status': status,
                                'r_id': r.get('id'),
                                'note': note
                            })
                            
            if not found_orders:
                st.warning(f"Objednávka pro hledaný výraz '{search_q}' nebyla nalezena v žádném uloženém rozvozu.")
            else:
                st.success(f"Nalezeno {len(found_orders)} shod:")
                for fo in found_orders:
                    p_plain = fo['products'].replace('<br>- ', '<br>• ').replace('<br>', '<br>').replace('<i>', '').replace('</i>', '').strip()
                    if "Žádné produkty" in p_plain or not p_plain: p_plain = "<i>Žádné specifické produkty v exportu</i>"
                    if not p_plain.startswith('•') and not p_plain.startswith('<br>'): p_plain = '• ' + p_plain
                    
                    bg_color = "#fef9e7" if fo['route_status'] == 'completed' else "#eaf2f8"
                    badge_status = "🟢 AKTIVNÍ TRASA" if fo['route_status'] == 'active' else "🏁 ODJETO (HISTORIE)"
                    
                    st.markdown(f"""
                    <div style="border: 1px solid #ccc; background-color: {bg_color}; padding: 15px; border-radius: 8px; margin-bottom: 5px;">
                        <h4 style="margin-top:0;">{fo['prijemce']} (Obj: {fo['oid']})</h4>
                        <div style="margin-bottom: 5px;"><b>Trasa:</b> {fo['route_name']} ({badge_status})</div>
                        <div style="margin-bottom: 5px;"><b>Řidič:</b> {fo['driver']} &nbsp;|&nbsp; <b>Datum:</b> {fo['date']}</div>
                        <div style="margin-bottom: 5px;"><b>Očekávaný příjezd:</b> <span style='color:#e74c3c; font-weight:bold;'>{fo['time']}</span> (cca {fo['time_exact']})</div>
                        <div style="margin-bottom: 5px;"><b>Dobírka:</b> {fo['cod']} Kč &nbsp;|&nbsp; <b>Aktuální stav:</b> <b>{fo['status']}</b></div>
                        <div style="font-size: 0.9em; margin-top: 10px; padding-top: 10px; border-top: 1px solid #ccc;">
                            <b>📦 Položky:</b><br>{p_plain}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # --- INTERAKTIVNÍ MINI-DISPEČINK (Zobrazí se jen u aktivních tras) ---
                    if fo['route_status'] == 'active':
                        with st.container():
                            r_id = fo['r_id']
                            oid = fo['oid']
                            
                            # --- NOVINKA: Úprava poznámky a dobírky ve vyhledávání ---
                            col_sn1, col_sn2 = st.columns([3, 2])
                            with col_sn1:
                                search_note_key = f"search_note_{r_id}_{oid}"
                                st.text_input("📝 Poznámka (vzkaz řidiči):", value=fo['note'], key=search_note_key, on_change=update_disp_note, args=(r_id, oid, search_note_key))
                                
                            with col_sn2:
                                s_curr_cod = float(parse_cod(fo['cod']))
                                s_new_cod = st.number_input("💰 Upravit dobírku (Kč):", value=s_curr_cod, step=50.0, key=f"search_cod_{r_id}_{oid}")
                                
                                if s_new_cod != s_curr_cod:
                                    if st.button("💾 Uložit novou částku", key=f"s_save_cod_{r_id}_{oid}", type="primary", use_container_width=True):
                                        all_r = load_routes()
                                        for rdb in all_r:
                                            if rdb['id'] == r_id:
                                                for row_rdb in rdb['itinerary_data']:
                                                    if row_rdb['Číslo objednávky'] == oid:
                                                        row_rdb['Dobírka (Kč)'] = str(s_new_cod)
                                                rdb['total_cod'] = sum(parse_cod(x['Dobírka (Kč)']) for x in rdb['itinerary_data'] if x['Číslo objednávky'] not in ['START', 'CÍL'] and rdb['details'].get(x['Číslo objednávky'], {}).get('dispatch_status') != 'Zrušeno')
                                        save_routes(all_r)
                                        if f"ready_pdfs_{r_id}" in st.session_state: del st.session_state[f"ready_pdfs_{r_id}"]
                                        st.success("Dobírka úspěšně změněna!")
                                        time.sleep(1)
                                        st.rerun()
                            # ---------------------------------------------------------
                            
                            # Ovládací tlačítka
                            if fo['status'] == "Zrušeno":
                                if st.button(f"🔄 Obnovit objednávku do trasy", key=f"s_res_{r_id}_{oid}", use_container_width=True):
                                    all_r = load_routes()
                                    for rdb in all_r:
                                        if rdb['id'] == r_id:
                                            rdb['details'][oid]['dispatch_status'] = ""
                                            warns = recalc_dispatch_route(rdb, mapy_api_key)
                                            save_routes(all_r)
                                            if warns: st.session_state['dispatch_warnings'].extend(warns)
                                            break
                                    st.rerun()
                            else:
                                b1, b2, b3 = st.columns(3)
                                if b1.button(f"✅ Potvrzeno", key=f"s_ok_{r_id}_{oid}", use_container_width=True):
                                    all_r = load_routes()
                                    for rdb in all_r:
                                        if rdb['id'] == r_id:
                                            rdb['details'][oid]['dispatch_status'] = "Potvrzeno"
                                            rdb['details'][oid]['confirmed_time'] = fo['time_exact'] # <--- Uložení slíbeného času
                                            rdb['details'][oid]['confirmed_window'] = fo['time'] # <--- Uložení slíbeného okna
                                            save_routes(all_r)
                                            break
                                    st.rerun()
                                    
                                if b2.button(f"💬 Odeslána SMS", key=f"s_sms_{r_id}_{oid}", use_container_width=True):
                                    all_r = load_routes()
                                    for rdb in all_r:
                                        if rdb['id'] == r_id:
                                            rdb['details'][oid]['dispatch_status'] = "SMS"
                                            save_routes(all_r)
                                            break
                                    st.rerun()
                                    
                                if b3.button(f"❌ Vyřadit (Zrušit)", key=f"s_canc_{r_id}_{oid}", use_container_width=True):
                                    all_r = load_routes()
                                    for rdb in all_r:
                                        if rdb['id'] == r_id:
                                            rdb['details'][oid]['dispatch_status'] = "Zrušeno"
                                            warns = recalc_dispatch_route(rdb, mapy_api_key)
                                            save_routes(all_r)
                                            if warns: st.session_state['dispatch_warnings'].extend(warns)
                                            break
                                    st.rerun()
                                    
                            # --- NOVINKA: Přesměrování do plného dispečinku pro editaci produktů ---
                            if st.button(f"✏️ Přejít do rozvozu a editovat produkty", key=f"s_edit_full_{r_id}_{oid}", use_container_width=True, type="secondary"):
                                st.session_state['active_dispatch'] = r_id
                                st.session_state['active_labels'] = None
                                st.session_state['active_suggestion'] = None
                                st.session_state['focus_oid'] = oid # <--- Zapamatujeme si cíl
                                update_route_lock(r_id, lock=True)
                                st.session_state['show_success_msg'] = f"✅ Rozvoz '{fo['route_name']}' byl otevřen a hledaná objednávka je zvýrazněna."
                                st.rerun()

                            # --- NOVINKA: ZÁCHRANNÉ TLAČÍTKO PRO ODJETÉ TRASY ---
                    elif fo['route_status'] == 'completed':
                        with st.container():
                            r_id = fo['r_id']
                            oid = fo['oid']
                            
                            if fo['status'] != "Zrušeno":
                                st.markdown("<br>", unsafe_allow_html=True)
                                if st.button(f"🔙 Zákazník nepřevzal - Vyřadit z historie a vrátit na mapu", key=f"s_ret_{r_id}_{oid}", type="primary", use_container_width=True):
                                    all_r = load_routes()
                                    for rdb in all_r:
                                        if rdb['id'] == r_id:
                                            rdb['details'][oid]['dispatch_status'] = "Zrušeno"
                                            save_routes(all_r)
                                            break
                                    st.success("Objednávka byla z historického rozvozu vyřazena a je zpět na mapě!")
                                    time.sleep(1.5)
                                    st.rerun()
                            else:
                                st.info("Tato objednávka už byla z historického rozvozu vyřazena a vrácena na mapu.")
                                
                    st.write("") # Odřádkování mezi výsledky

render_history_and_dispatch()

# --- KROK 1: VÝBĚR STATUSŮ PER E-SHOP ---
st.subheader("Krok 1: Výběr objednávek z e-shopů")
col_sh1, col_sh2, col_sh3 = st.columns(3)

if 'maxi_st_saved' not in st.session_state: st.session_state['maxi_st_saved'] = []
if 'vomaks_st_saved' not in st.session_state: st.session_state['vomaks_st_saved'] = []
if 'sleva_st_saved' not in st.session_state: st.session_state['sleva_st_saved'] = []

def update_maxi(): st.session_state['maxi_st_saved'] = st.session_state['maxi_st']
def update_vomaks(): st.session_state['vomaks_st_saved'] = st.session_state['vomaks_st']
def update_sleva(): st.session_state['sleva_st_saved'] = st.session_state['sleva_st']

with col_sh1:
    st.markdown("### 🛒 Max-i.cz")
    if not df_maxi.empty and 'statusName' in df_maxi.columns:
        statuses1 = sorted(df_maxi['statusName'].dropna().unique().tolist())
        selected_maxi = st.multiselect("Zobrazit na mapě (Max-i):", options=statuses1, default=[s for s in st.session_state['maxi_st_saved'] if s in statuses1], key='maxi_st', on_change=update_maxi)
    else: selected_maxi = []; st.info("Žádná data pro výběr.")

with col_sh2:
    st.markdown("### 🛒 Vomaks.cz")
    if not df_vomaks.empty and 'statusName' in df_vomaks.columns:
        statuses2 = sorted(df_vomaks['statusName'].dropna().unique().tolist())
        selected_vomaks = st.multiselect("Zobrazit na mapě (Vomaks):", options=statuses2, default=[s for s in st.session_state['vomaks_st_saved'] if s in statuses2], key='vomaks_st', on_change=update_vomaks)
    else: selected_vomaks = []; st.info("Žádná data pro výběr.")

with col_sh3:
    st.markdown("### 🛒 Slevadoma.cz")
    if not df_sleva.empty and 'statusName' in df_sleva.columns:
        statuses3 = sorted(df_sleva['statusName'].dropna().unique().tolist())
        selected_sleva = st.multiselect("Zobrazit na mapě (Slevadoma):", options=statuses3, default=[s for s in st.session_state['sleva_st_saved'] if s in statuses3], key='sleva_st', on_change=update_sleva)
    else: selected_sleva = []; st.info("Žádná data pro výběr.")

# --- NOVINKA: TRVALÁ DATABÁZE RUČNÍCH OBJEDNÁVEK ---
MANUAL_ORDERS_FILE = "manual_orders.json"
def load_manual_db(): return load_json_from_github_or_local(MANUAL_ORDERS_FILE, dict)
def save_manual_db(data): save_json_to_github_or_local(MANUAL_ORDERS_FILE, data, f"ManualOrder {datetime.now().strftime('%H:%M:%S')}")

man_db = load_manual_db()

# Přidání trvalých ručních objednávek do globálních dat, aby je mapa vždy viděla
if man_db.get('orders'):
    df_man = pd.DataFrame(man_db['orders'])
    df_shop = pd.concat([df_shop, df_man], ignore_index=True)
    products_dict.update(man_db.get('products', {}))

st.markdown("---")
with st.expander("➕ Vytvořit novou objednávku ručně (Mimo e-shopy)"):
    st.info("Tato objednávka se přidá rovnou na mapu a do vaší aktuální trasy. Bude se tvářit jako běžná objednávka.")
    with st.form(key="manual_order_form"):
        col_f1, col_f2 = st.columns(2)
        man_id = col_f1.text_input("Číslo objednávky (např. RUC-001):")
        man_name = col_f2.text_input("Jméno zákazníka (Příjemce):")
        
        man_addr = st.text_input("Celá adresa (Ulice a č.p., Město, PSČ - pro přesnou mapu):")
        
        col_f3, col_f4 = st.columns(2)
        man_phone = col_f3.text_input("Telefon:")
        man_cod = col_f4.number_input("Dobírka (Kč):", min_value=0.0, step=10.0)
        
        man_prods = st.text_area("Seznam produktů (co kus, to nový řádek - např. '2x Židle'):")
        
        submit_man = st.form_submit_button("💾 Vytvořit a přidat rovnou na mapu", type="primary")
        
        if submit_man:
            if not man_id or not man_addr:
                st.error("Číslo objednávky a adresa jsou povinné údaje!")
            elif man_id.strip() in df_shop['id'].values:
                st.error("Toto číslo objednávky už existuje! Zvolte jiné (např. RUC-001).")
            else:
                new_order = {
                    'id': man_id.strip(),
                    'eshop': 'Ruční zadání',
                    'deliveryFullName': man_name.strip(),
                    'deliveryStreet': man_addr.strip(),
                    'deliveryCity': '',
                    'deliveryZip': '',
                    'phone': man_phone.strip(),
                    'geisDeliveryPriceToPay': str(man_cod),
                    'statusName': 'Ruční'
                }
                
                formatted_prods = "<br>- " + "<br>- ".join([p.strip() for p in man_prods.split('\n') if p.strip()]) if man_prods.strip() else "<br><i>Žádné produkty</i>"
                
                # --- TRVALÉ ULOŽENÍ DO DATABÁZE ---
                curr_db = load_manual_db()
                if 'orders' not in curr_db: curr_db['orders'] = []
                if 'products' not in curr_db: curr_db['products'] = {}
                
                curr_db['orders'].append(new_order)
                curr_db['products'][man_id.strip()] = formatted_prods
                save_manual_db(curr_db)
                # ----------------------------------
                
                # Okamžité zařazení do trasy
                if man_id.strip() not in st.session_state['selected_orders']:
                    st.session_state['selected_orders'].append(man_id.strip())
                    
                st.success(f"Objednávka {man_id} vytvořena a trvale uložena do databáze!")
                time.sleep(1)
                st.rerun()
# --- KONEC NOVINKY ---

mask_maxi = (df_shop['eshop'] == 'Max-i.cz') & df_shop['statusName'].isin(selected_maxi)
mask_vomaks = (df_shop['eshop'] == 'Vomaks.cz') & df_shop['statusName'].isin(selected_vomaks)
mask_sleva = (df_shop['eshop'] == 'Slevadoma.cz') & df_shop['statusName'].isin(selected_sleva)
mask_selected = df_shop['id'].isin(st.session_state['selected_orders'])
mask_loaded = df_shop['id'].isin(st.session_state.get('loaded_route_orders', []))

saved_routes_main = load_routes()
saved_routes_ids = set()
editing_id = st.session_state.get('editing_route_id')
for r in saved_routes_main: 
    if r.get('status') == 'trashed':
        continue
    if editing_id and r.get('id') == editing_id:
        continue
    
    # NOVINKA: Zrušené objednávky propustíme zpět na mapu i z historie
    for oid in r.get('orders', []):
        if r.get('details', {}).get(oid, {}).get('dispatch_status') != 'Zrušeno':
            saved_routes_ids.add(oid)
            
mask_saved = df_shop['id'].isin(saved_routes_ids)

df_to_process = df_shop[mask_selected | mask_loaded | ((mask_maxi | mask_vomaks | mask_sleva) & ~mask_saved)].copy()

# --- NOVINKA: PŘÍPRAVA ULOŽENÝCH CUSTOM HODNOT (Aby se nepřemazaly úpravy produktů a dobírek) ---
custom_products = {}
custom_cods = {}
if st.session_state.get('editing_route_id'):
    for r_db in load_routes():
        if r_db['id'] == st.session_state['editing_route_id'] and 'itinerary_data' in r_db:
            for itin_row in r_db['itinerary_data']:
                custom_products[itin_row['Číslo objednávky']] = itin_row.get('Produkty')
                custom_cods[itin_row['Číslo objednávky']] = itin_row.get('Dobírka (Kč)')

# --- NOVINKA: PAMĚŤ PRO RUČNĚ OPRAVENÉ ADRESY ---
if 'address_overrides' not in st.session_state: 
    st.session_state['address_overrides'] = {}

orders = []
if not df_to_process.empty:
    with st.spinner("Připravuji mapu a souřadnice..."):
        new_geo_added = False
        for idx, row in df_to_process.iterrows():
            order_id = row['id']
            
            # 1. Zjistíme, jestli uživatel už adresu ručně neopravil
            if order_id in st.session_state['address_overrides']:
                cela_adresa = st.session_state['address_overrides'][order_id]
            else:
                # Jinak ji složíme standardně ze Shoptetu
                ulice = row.get('deliveryStreet', row.get('billStreet', ''))
                cp = row.get('deliveryHouseNumber', row.get('billHouseNumber', ''))
                mesto = row.get('deliveryCity', row.get('billCity', ''))
                psc = row.get('deliveryZip', row.get('billZip', ''))
                
                parts = [ulice, cp, mesto, psc]
                adresa_casti = [str(x).strip() for x in parts if pd.notna(x) and str(x).strip().lower() not in ['nan', 'none', '<na>', '']]
                cela_adresa = " ".join(adresa_casti).strip()

            # 2. Hledáme v GPS mezipaměti nebo voláme API
            if cela_adresa in st.session_state['geo_cache']: 
                lat, lon = st.session_state['geo_cache'][cela_adresa]
            else:
                lat, lon = geocode_address_api(cela_adresa, mapy_api_key)
                if lat is not None and lon is not None: 
                    st.session_state['geo_cache'][cela_adresa] = [lat, lon]
                    new_geo_added = True

            jmeno = row.get('deliveryFullName')
            if pd.isna(jmeno) or str(jmeno).strip() in ['', 'nan', 'None']: jmeno = row.get('billFullName', 'Neznámý příjemce')
                
            final_cod = custom_cods.get(order_id) if order_id in custom_cods else str(row.get('geisDeliveryPriceToPay', row.get('priceToPay', '0')))
            final_prods = custom_products.get(order_id) if order_id in custom_products else products_dict.get(order_id, "<br><i>Žádné produkty v exportu</i>")
            
            orders.append({
                'Číslo objednávky': order_id, 'E-shop': row.get('eshop', ''), 'Příjemce': str(jmeno), 'Status': str(row.get('statusName', '')),
                'Celá_adresa': cela_adresa, 'Ulice': f"{row.get('deliveryStreet', '')} {row.get('deliveryHouseNumber', '')}".strip(), 
                'Město': row.get('deliveryCity', ''), 'PSČ': row.get('deliveryZip', ''), 'Chyba': "(!)" if lat is None else "",
                'Telefon': str(row.get('phone', '')), 'Dobírka (Kč)': final_cod,
                'Produkty': final_prods, 'lat': lat, 'lon': lon
            })
            
        if new_geo_added: save_geo_cache(st.session_state['geo_cache'])

if orders: df_orders = pd.DataFrame(orders)
else: df_orders = pd.DataFrame(columns=['Číslo objednávky', 'E-shop', 'Příjemce', 'Status', 'Celá_adresa', 'Ulice', 'Město', 'PSČ', 'Chyba', 'Telefon', 'Dobírka (Kč)', 'Produkty', 'lat', 'lon'])

st.markdown("---")

# --- NOVINKA: DETEKTOR A OPRAVÁŘ ŠPATNÝCH ADRES PŘED VYKRESLENÍM MAPY ---
unmapped_orders = df_orders[df_orders['lat'].isna()]
if not unmapped_orders.empty:
    st.markdown(f"<div style='background-color: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; border-left: 5px solid #f5c6cb; margin-bottom: 15px;'><b>🚨 NENALEZENÉ ADRESY ({len(unmapped_orders)}):</b> Systém nedokázal na Mapy.cz najít níže uvedené adresy (zákazník pravděpodobně zadal překlep, zbytečná písmena u č.p., nebo chybí mezera). <b>Bez GPS souřadnic nelze vypočítat kilometry.</b> Zjednodušte adresu (např. odmažte lomítka nebo písmena z č.p.) a zkuste to znovu.</div>", unsafe_allow_html=True)
    with st.expander("🛠️ OPRAVIT ADRESY PRO VÝPOČET TRASY", expanded=True):
        for i, r_err in unmapped_orders.iterrows():
            o_id_err = r_err['Číslo objednávky']
            c_err1, c_err2 = st.columns([4, 1])
            
            # Textové políčko předvyplněné špatnou adresou
            new_a = c_err1.text_input(f"Objednávka: {o_id_err} | 👤 {r_err['Příjemce']}", value=r_err['Celá_adresa'], key=f"fix_addr_{o_id_err}")
            
            # Tlačítko pro nový pokus
            if c_err2.button("Zkusit znovu", key=f"btn_retry_{o_id_err}", use_container_width=True):
                st.session_state['address_overrides'][o_id_err] = new_a
                st.rerun()
    st.markdown("---")
# -------------------------------------------------------------------------

# --- NOVINKA: Automatický sjezd dolů ---
st.markdown("<div id='editor_target'></div>", unsafe_allow_html=True)
if st.session_state.get('scroll_to_editor'):
    st.html("<script>window.parent.document.getElementById('top_target').scrollIntoView({behavior: 'smooth', block: 'start'});</script>")
    st.session_state['scroll_to_editor'] = False
# ---------------------------------------

col_m1, col_m2, col_m3, col_m4 = st.columns(4)
pocet_placeholder = col_m1.empty()
dobirka_placeholder = col_m2.empty()
km_placeholder = col_m3.empty()
cas_placeholder = col_m4.empty()

st.write("")
col_b1, col_b2 = st.columns([4, 1])

with col_b2:
    if st.button("🗑️ Vymazat trasu z mapy", use_container_width=True, type="secondary"): 
        st.session_state['trigger_clear'] = True; st.rerun()

with col_b1:
    if st.button("🌌 Magický návrh rozvozu", use_container_width=True, type="primary"):
        if len(df_orders) < auto_min_orders: st.error(f"Na mapě je pouze {len(df_orders)} volných objednávek. Minimální limit na posuvníku je {auto_min_orders}.")
        else:
            with st.spinner("Počítám nejlepší možnou trasu (aplikuji striktní tubus)..."):
                s_lat, s_lon = geocode_address_api(st.session_state['st_start_address'], mapy_api_key)
                e_lat, e_lon = geocode_address_api(st.session_state['st_end_address'], mapy_api_key)
                
                dir_lat, dir_lon = None, None
                if target_direction_city.strip(): dir_lat, dir_lon = geocode_address_api(target_direction_city, mapy_api_key)
                
                auto_max_time_min_val = auto_max_time_h * 60
                
                if s_lat and s_lon and e_lat and e_lon:
                    points_dict = {'START': {'lat': s_lat, 'lon': s_lon}, 'END': {'lat': e_lat, 'lon': e_lon}}
                    available_orders = []
                    base_dist_dir = geodesic((s_lat, s_lon), (dir_lat, dir_lon)).kilometers * 1.3 if dir_lat and dir_lon else 0
                    
                    if dir_lat and dir_lon:
                        lat_km = 111.32
                        lon_km = 111.32 * math.cos(math.radians((s_lat + dir_lat) / 2))
                        x1, y1 = s_lon * lon_km, s_lat * lat_km
                        x2, y2 = dir_lon * lon_km, dir_lat * lat_km
                        segment_len_sq = (x2 - x1)**2 + (y2 - y1)**2
                        max_dist_to_line = max(2.0, base_dist_dir * (target_tolerance - 1) * 0.5)

                    for _, r in df_orders.dropna(subset=['lat', 'lon']).iterrows():
                        o_id = r['Číslo objednávky']
                        if o_id not in st.session_state['selected_orders'] and o_id not in st.session_state.get('frozen_orders', []):
                            if dir_lat and dir_lon:
                                x0, y0 = r['lon'] * lon_km, r['lat'] * lat_km
                                if segment_len_sq == 0:
                                    t_proj = 0
                                    proj_x, proj_y = x1, y1
                                else:
                                    t_proj = ((x0 - x1) * (x2 - x1) + (y0 - y1) * (y2 - y1)) / segment_len_sq
                                    proj_x = x1 + t_proj * (x2 - x1)
                                    proj_y = y1 + t_proj * (y2 - y1)
                                    
                                dist_to_line = math.sqrt((x0 - proj_x)**2 + (y0 - proj_y)**2)
                                
                                if t_proj < -0.05 or t_proj > 1.2 or dist_to_line > max_dist_to_line:
                                    continue
                                    
                            points_dict[o_id] = {'lat': r['lat'], 'lon': r['lon']}; available_orders.append(o_id)
                            
                    if len(available_orders) < auto_min_orders: st.error(f"Ve vybraném směru je pouze {len(available_orders)} objednávek. Zvětšete koridor nebo snižte minimum.")
                    else:
                        dist_matrix = {}
                        for p1_id, p1_coords in points_dict.items():
                            dist_matrix[p1_id] = {}
                            for p2_id, p2_coords in points_dict.items():
                                if p1_id == p2_id: dist_matrix[p1_id][p2_id] = 0.0
                                else: dist_matrix[p1_id][p2_id] = geodesic((p1_coords['lat'], p1_coords['lon']), (p2_coords['lat'], p2_coords['lon'])).kilometers * 1.3

                        best_route_ids = []
                        if dir_lat and dir_lon:
                            sorted_by_target = sorted(available_orders, key=lambda o: geodesic((points_dict[o]['lat'], points_dict[o]['lon']), (dir_lat, dir_lon)).kilometers)
                            starting_points = sorted_by_target[:3] 
                        else: starting_points = available_orders
                        
                        for first_stop in starting_points:
                            unvisited = set(available_orders); unvisited.remove(first_stop); route_nodes = ['START', first_stop, 'END']
                            while unvisited:
                                if len(route_nodes) - 2 >= auto_max_orders:
                                    break
                                
                                best_candidate = None; best_insert_idx = -1; best_added_dist = float('inf')
                                for candidate in unvisited:
                                    for i in range(1, len(route_nodes)):
                                        prev_node = route_nodes[i-1]; next_node = route_nodes[i]
                                        added_dist = dist_matrix[prev_node][candidate] + dist_matrix[candidate][next_node] - dist_matrix[prev_node][next_node]
                                        if added_dist < best_added_dist: best_added_dist = added_dist; best_candidate = candidate; best_insert_idx = i
                                            
                                if not best_candidate: break
                                    
                                test_route = route_nodes[:best_insert_idx] + [best_candidate] + route_nodes[best_insert_idx:]
                                opt_dist, opt_time = calc_route_metrics(test_route, dist_matrix)
                                
                                if opt_dist <= auto_max_km and opt_time <= auto_max_time_min_val: route_nodes = test_route; unvisited.remove(best_candidate)
                                else:
                                    opt_test_route = optimize_route_2opt(test_route, dist_matrix)
                                    opt_dist2, opt_time2 = calc_route_metrics(opt_test_route, dist_matrix)
                                    if opt_dist2 <= auto_max_km and opt_time2 <= auto_max_time_min_val: route_nodes = opt_test_route; unvisited.remove(best_candidate)
                                    else: unvisited.remove(best_candidate)
                                        
                            final_ids = [n for n in route_nodes if n not in ['START', 'END']]
                            if len(final_ids) > len(best_route_ids): best_route_ids = final_ids

                        if len(best_route_ids) >= auto_min_orders:
                            st.session_state['selected_orders'].extend(best_route_ids); st.success(f"Systém naplánoval {len(best_route_ids)} objednávek."); time.sleep(2.5); st.rerun()
                        else: st.error(f"Do limitů se vešlo pouze {len(best_route_ids)} zastávek (požadované minimum: {auto_min_orders}). Zkuste rozšířit koridor nebo zvednout limit kilometrů.")
                else: st.error("Nemohu najít souřadnice skladu.")

st.markdown("---")

# --- EARLY EVALUATION OF SELECTED DF ---
if st.session_state['selected_orders'] and not df_orders.empty:
    # --- NOVINKA: Absolutní pojistka proti duplikátům (Brání pádu aplikace) ---
    unique_selected = list(dict.fromkeys(st.session_state['selected_orders']))
    st.session_state['selected_orders'] = unique_selected
    
    platne_ids = [o_id for o_id in unique_selected if o_id in df_orders['Číslo objednávky'].values]
    if platne_ids: 
        # Z tabulky před načtením vymažeme i případné duplicitní řádky
        df_safe = df_orders.drop_duplicates(subset=['Číslo objednávky'])
        df_selected = df_safe.set_index('Číslo objednávky').loc[platne_ids].reset_index()
    else: 
        df_selected = pd.DataFrame()
else: 
    df_selected = pd.DataFrame()

# --- VÝPOČET A ZOBRAZENÍ ŽIVÉHO TACHOMETRU (Odhad km a času) ---
approx_km = 0.0

if not df_selected.empty:
    active_oids = [oid for oid in st.session_state['selected_orders'] if st.session_state.get('loaded_statuses', {}).get(oid, '') != 'Zrušeno']
    
    if active_oids:
        active_rows = df_selected[df_selected['Číslo objednávky'].isin(active_oids)]
        celkova_vybrana_dobirka = sum(parse_cod(x) for x in active_rows['Dobírka (Kč)'])
        pocet_placeholder.metric(label="📦 Počet aktivních obj.", value=f"{len(active_oids)}")
        dobirka_placeholder.metric(label="💰 Aktivní dobírky", value=f"{int(celkova_vybrana_dobirka)} Kč")
        
        start_addr = st.session_state['st_start_address']
        if start_addr in st.session_state['geo_cache']:
            cached_start = st.session_state['geo_cache'][start_addr]
            s_lat, s_lon = cached_start[0], cached_start[1]
        else:
            s_lat, s_lon = geocode_address_api(start_addr, mapy_api_key)
            if s_lat is not None:
                st.session_state['geo_cache'][start_addr] = [s_lat, s_lon]
                save_geo_cache(st.session_state['geo_cache'])
                
        end_addr = st.session_state['st_end_address']
        if end_addr in st.session_state['geo_cache']:
            cached_end = st.session_state['geo_cache'][end_addr]
            e_lat, e_lon = cached_end[0], cached_end[1]
        else:
            e_lat, e_lon = geocode_address_api(end_addr, mapy_api_key)
            if e_lat is not None:
                st.session_state['geo_cache'][end_addr] = [e_lat, e_lon]
                save_geo_cache(st.session_state['geo_cache'])

        unvisited_pts = []
        for oid in active_oids:
            row = df_orders[df_orders['Číslo objednávky'] == oid]
            if not row.empty:
                r_lat, r_lon = row.iloc[0]['lat'], row.iloc[0]['lon']
                if pd.notna(r_lat) and pd.notna(r_lon):
                    unvisited_pts.append((r_lat, r_lon))
                    
        if s_lat and s_lon and unvisited_pts:
            curr_pt = (s_lat, s_lon)
            while unvisited_pts:
                closest_pt = min(unvisited_pts, key=lambda pt: geodesic(curr_pt, pt).kilometers)
                approx_km += geodesic(curr_pt, closest_pt).kilometers * 1.3
                curr_pt = closest_pt
                unvisited_pts.remove(closest_pt)
            if e_lat and e_lon:
                approx_km += geodesic(curr_pt, (e_lat, e_lon)).kilometers * 1.3
        elif len(unvisited_pts) > 1:
            for i in range(len(unvisited_pts)-1):
                approx_km += geodesic(unvisited_pts[i], unvisited_pts[i+1]).kilometers * 1.3

        driving_time = (approx_km / 65.0) * 60.0
        
        km_placeholder.metric(label="🛣️ Odhad trasy (+30%)", value=f"~ {int(round(approx_km))} km")
        cas_placeholder.metric(label="⏱️ Čistý čas jízdy", value=f"~ {int(driving_time//60)}h {int(driving_time%60):02d}m")
    else:
        pocet_placeholder.metric(label="📦 Počet aktivních obj.", value="0")
        dobirka_placeholder.metric(label="💰 Aktivní dobírky", value="0 Kč")
        km_placeholder.metric(label="🛣️ Odhad trasy", value="0 km")
        cas_placeholder.metric(label="⏱️ Čistý čas jízdy", value="0h 00m")
else:
    pocet_placeholder.metric(label="📦 Počet aktivních obj.", value="0")
    dobirka_placeholder.metric(label="💰 Aktivní dobírky", value="0 Kč")
    km_placeholder.metric(label="🛣️ Odhad trasy", value="0 km")
    cas_placeholder.metric(label="⏱️ Čistý čas jízdy", value="0h 00m")

st.markdown("---")

# --- SPLIT SCREEN LAYOUT ---
if not df_selected.empty:
    col_map, col_step2 = st.columns([1.2, 1.0], gap="large")
else:
    col_map = st.container()
    col_step2 = None

with col_map:
    # ----------------- MAPA FOLIUM S INTEGROVANÝM KRESLENÍM (LASSO) -----------------
    st.write("Využijte nástroje vpravo nahoře v mapě pro hromadný výběr (nakreslení obdélníku/tvaru), nebo pro přidání/odebrání klikejte na jednotlivé body.")
    
    # --- NOVINKA: PŘEPÍNAČE MÓDŮ (MRAŽENÍ, PŘESPÁNÍ, ROZDĚLENÍ, ZÁMEK POZICE) ---
    st.markdown("<br>", unsafe_allow_html=True)
    col_t1, col_t2 = st.columns(2)
    freeze_mode = col_t1.toggle("🧊 Mrazící mód (Kliknutím vyřadíte z trasy)")
    st.session_state['freeze_mode'] = freeze_mode
    sleep_mode = col_t2.toggle("🏨 Mód přespání (Kliknutím určíte konec 1. dne)")
    st.session_state['sleep_mode'] = sleep_mode
    
    col_t3, col_t4 = st.columns(2)
    split_mode = col_t3.toggle("✂️ Mód rozdělení (Kliknutím určíte body pro novou část)")
    st.session_state['split_mode'] = split_mode
    lock_pos_mode = col_t4.toggle("📌 Zámek pozice (Kliknutím zafixujete bod pevně na jeho místě)")
    st.session_state['lock_pos_mode'] = lock_pos_mode

    # Pokud je zapnutý split mód a máme označené objednávky, zobrazíme tlačítko pro rozdělení
    if st.session_state.get('split_mode') and st.session_state.get('split_marked_orders'):
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("✂️ ROZDĚLIT TUTO TRASU NA 2 SAMOSTATNÉ ROZVOZY", type="primary", use_container_width=True):
            with st.spinner("Rozděluji trasu a přepočítávám obě části přes Mapy.cz..."):
                orders_part2 = st.session_state['split_marked_orders'].copy()
                orders_part1 = [o for o in st.session_state['selected_orders'] if o not in orders_part2]
                
                # Vnitřní funkce pro kompletní sestavení a přepočet rozdělené trasy
                def build_and_recalc_split_route(order_list, suffix):
                    base_name = st.session_state.get('st_route_name', 'Rozvoz') if st.session_state.get('st_route_name') else 'Rozvoz'
                    raw_name = f"{base_name} - {suffix}"
                    r_date = st.session_state.get('st_route_date', datetime.today())
                    r_driver = st.session_state.get('st_driver_name', '')
                    
                    full_name = " | ".join([raw_name, r_date.strftime('%d.%m.%Y')])
                    if r_driver: full_name += f" | Řidič: {r_driver}"
                    
                    s_lat, s_lon = geocode_address_api(st.session_state['st_start_address'], mapy_api_key)
                    e_lat, e_lon = geocode_address_api(st.session_state['st_end_address'], mapy_api_key)
                    
                    itin = [{
                        'Číslo objednávky': 'START', 'Příjemce': st.session_state['st_start_point_name'], 
                        'Tisk_Adresa': st.session_state['st_start_address'], 'Město': '', 'PSČ': '', 'Chyba': '', 'Telefon': '', 'Dobírka (Kč)': 0, 
                        'Poznámka': '', 'lat': s_lat, 'lon': s_lon, 'E-shop': '', 'Produkty': ''
                    }]
                    
                    details = {}
                    for oid in order_list:
                        matching = df_orders[df_orders['Číslo objednávky'] == oid]
                        if not matching.empty:
                            r_data = matching.iloc[0].to_dict()
                            r_data['Poznámka'] = st.session_state.get(f"note_input_{oid}", st.session_state.get(f"note_{oid}", ""))
                            r_data['Tisk_Adresa'] = st.session_state.get(f"addr_input_{oid}", r_data['Celá_adresa'])
                            itin.append(r_data)
                            
                            details[oid] = {
                                "note": r_data['Poznámka'],
                                "addr": r_data['Tisk_Adresa'],
                                "dispatch_status": st.session_state.get('loaded_statuses', {}).get(oid, ""),
                                "pkg_count": 1,
                                "tt_price": float(st.session_state.get(f"tt_price_{oid}", 0))
                            }
                            
                    itin.append({
                        'Číslo objednávky': 'CÍL', 'Příjemce': st.session_state['st_end_point_name'], 
                        'Tisk_Adresa': st.session_state['st_end_address'], 'Město': '', 'PSČ': '', 'Chyba': '', 'Telefon': '', 'Dobírka (Kč)': 0, 
                        'Poznámka': '', 'lat': e_lat, 'lon': e_lon, 'E-shop': '', 'Produkty': ''
                    })
                    
                    r_dict = {
                        "id": str(time.time() + random.random()), "name": full_name, "raw_route_name": raw_name,
                        "route_date": r_date.strftime('%Y-%m-%d'), "driver_name": r_driver,
                        "start_address": st.session_state['st_start_address'], "end_address": st.session_state['st_end_address'],
                        "start_point_name": st.session_state['st_start_point_name'], "end_point_name": st.session_state['st_end_point_name'],
                        "orders": order_list, "details": details, "itinerary_data": itin,
                        "total_km": 0, "total_hours": "0h 0min", "total_cod": 0,
                        "kasac_value": st.session_state['st_kasac_value'], "start_time_str": st.session_state['st_start_time'].strftime('%H:%M'),
                        "slow_mode": False, "unload_time_min": st.session_state['st_unload_time_min'],
                        "status": "active", "costs": {"fuel": 0.0, "driver": 0.0, "accommodation": 0.0, "other": 0.0},
                        "total_tt_price": sum(float(details[o]['tt_price']) for o in order_list if details[o]['dispatch_status'] != 'Zrušeno')
                    }
                    recalc_dispatch_route(r_dict, mapy_api_key)
                    return r_dict

                if orders_part1:
                    p1 = build_and_recalc_split_route(orders_part1, "Část 1")
                    safe_save_route(p1)
                if orders_part2:
                    p2 = build_and_recalc_split_route(orders_part2, "Část 2")
                    safe_save_route(p2)
                    
                # Pokud jsme původně upravovali již existující trasu z historie, smažeme její starou celistvou verzi
                editing_id = st.session_state.get('editing_route_id')
                if editing_id:
                    safe_save_route(None, delete_id=editing_id)
                    
                log_action("Rozdělení trasy", f"Trasa rozdělena na 2 části. Část 1: {len(orders_part1)} obj, Část 2: {len(orders_part2)} obj.")
                st.session_state['trigger_clear'] = True
                st.session_state['show_success_msg'] = f"✂️ Trasa byla úspěšně rozdělena na dvě samostatné části a uložena!"
                st.rerun()

    mapa_cr = folium.Map(location=st.session_state['map_center'], zoom_start=st.session_state['map_zoom'], tiles=f"https://api.mapy.cz/v1/maptiles/basic/256/{{z}}/{{x}}/{{y}}?apikey={mapy_api_key}", attr="Mapy.cz")

    if target_direction_city.strip():
        start_addr = st.session_state['st_start_address']
        s_lat_map, s_lon_map = None, None
        if start_addr in st.session_state['geo_cache']:
            s_lat_map, s_lon_map = st.session_state['geo_cache'][start_addr][:2]
        else:
            s_lat_map, s_lon_map = geocode_address_api(start_addr, mapy_api_key)
            
        t_lat_map, t_lon_map = None, None
        if target_direction_city in st.session_state['geo_cache']:
            t_lat_map, t_lon_map = st.session_state['geo_cache'][target_direction_city][:2]
        else:
            t_lat_map, t_lon_map = geocode_address_api(target_direction_city, mapy_api_key)
            if t_lat_map and t_lon_map:
                st.session_state['geo_cache'][target_direction_city] = [t_lat_map, t_lon_map]
                save_geo_cache(st.session_state['geo_cache'])
                
        if s_lat_map and s_lon_map and t_lat_map and t_lon_map:
            width_weight = max(10, (target_tolerance - 1) * 150)
            folium.PolyLine(
                locations=[(s_lat_map, s_lon_map), (t_lat_map, t_lon_map)],
                color="#9b59b6",
                weight=width_weight,
                opacity=0.15,
                tooltip="Oblast hledání (Koridor)"
            ).add_to(mapa_cr)
            
            folium.PolyLine(
                locations=[(s_lat_map, s_lon_map), (t_lat_map, t_lon_map)],
                color="#8e44ad",
                weight=3,
                dash_array="10, 10",
                opacity=0.8
            ).add_to(mapa_cr)
            
            folium.Marker(
                location=[t_lat_map, t_lon_map],
                icon=folium.Icon(color="purple", icon="flag"),
                tooltip=f"Cílový směr: {target_direction_city}"
            ).add_to(mapa_cr)

    Draw(export=False, position='topright', draw_options={'polyline':False, 'polygon':True, 'circle':False, 'marker':False, 'circlemarker':False, 'rectangle':True}).add_to(mapa_cr)

    if not df_orders.empty:
        grouped = df_orders.dropna(subset=['lat', 'lon']).groupby(['lat', 'lon'])
        for (lat, lon), group in grouped:
            orders_here = group.to_dict('records')
            
            selected_here = [o for o in orders_here if o['Číslo objednávky'] in st.session_state['selected_orders']]
            frozen_here = [o for o in orders_here if o['Číslo objednávky'] in st.session_state.get('frozen_orders', [])]
            unselected_here = [o for o in orders_here if o['Číslo objednávky'] not in st.session_state['selected_orders'] and o['Číslo objednávky'] not in st.session_state.get('frozen_orders', [])]
            
            tooltip_parts = []
            for row in orders_here:
                order_id = row['Číslo objednávky']
                if order_id == st.session_state.get('sleep_after_oid'):
                    oznaceni = "<b>🏨 PŘESPÁNÍ PO TÉTO ZASTÁVCE</b><br>"
                elif order_id in st.session_state.get('split_marked_orders', []):
                    oznaceni = "<b>✂️ OZNAČENO PRO ROZDĚLENÍ (ČÁST 2)</b><br>"
                elif order_id in st.session_state.get('frozen_orders', []):
                    oznaceni = "<b>🧊 ZMRAZENO (Ignorováno)</b><br>"
                elif order_id in st.session_state.get('locked_pos_orders', []) and order_id in st.session_state['selected_orders']:
                    poradi = st.session_state['selected_orders'].index(order_id) + 1
                    oznaceni = f"<b>📌 ZAMKNUTÁ POZICE ({poradi}. zastávka)</b><br>"
                elif order_id in st.session_state['selected_orders']:
                    poradi = st.session_state['selected_orders'].index(order_id) + 1
                    oznaceni = f"<b>{poradi}. zastávka</b><br>"
                else:
                    oznaceni = ""
                    
                # --- NOVINKA PRO TOPTRANS CENU V MAPĚ ---
                tt_price_str = ""
                if f"tt_price_{order_id}" in st.session_state:
                    tt_price_str = f"<br><b style='color:#27ae60; font-size:14px;'>🚚 Toptrans: {st.session_state[f'tt_price_{order_id}']} Kč</b>"
                # ----------------------------------------
                    
                bublina = f"<span style='display:none;'>[ID:{order_id}]</span><div style='min-width: 250px; font-family: sans-serif; font-size: 13px; margin-bottom:5px; padding-bottom:5px; border-bottom: 1px solid #ddd;'>{oznaceni}<b>{order_id}</b> ({row.get('E-shop','')})<br>{row['Příjemce']}<br><i>Stav: {row['Status']}</i><br><b>Dobírka: {row['Dobírka (Kč)']} Kč</b>{tt_price_str}<br>{row['Celá_adresa']}<hr style='margin: 5px 0;'><b>Produkty:</b>{row['Produkty']}</div>"
                tooltip_parts.append(bublina)
                
            vzhled_bubliny = "".join(tooltip_parts)
            
            if selected_here:
                is_split_node = any(o['Číslo objednávky'] in st.session_state.get('split_marked_orders', []) for o in selected_here)
                is_sleep_node = any(o['Číslo objednávky'] == st.session_state.get('sleep_after_oid') for o in selected_here)
                is_locked_node = any(o['Číslo objednávky'] in st.session_state.get('locked_pos_orders', []) for o in selected_here)
                
                indices = sorted([st.session_state['selected_orders'].index(o['Číslo objednávky']) + 1 for o in selected_here])
                if len(indices) == 1: marker_text = str(indices[0])
                elif len(indices) == 2: marker_text = f"{indices[0]}/{indices[1]}"
                else: marker_text = f"{indices[0]}-{indices[-1]}"
                    
                f_size = "14px" if len(marker_text) <= 2 else ("11px" if len(marker_text) <= 4 else "9px")
                
                if is_sleep_node:
                    ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color='#8e44ad', border_color='#732d91', inner_iconStyle=f'margin-top:2px; font-weight:bold; font-size:{f_size};', number=marker_text)
                elif is_split_node:
                    ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color='#e67e22', border_color='#d35400', inner_iconStyle=f'margin-top:2px; font-weight:bold; font-size:{f_size};', number=marker_text)
                elif is_locked_node:
                    # TMAVĚ MODRÁ PRO ZÁMEK POZICE
                    ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color='#2980b9', border_color='#1c5980', inner_iconStyle=f'margin-top:2px; font-weight:bold; font-size:{f_size};', number=marker_text)
                else:
                    ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color='#2ecc71', border_color='#27ae60', inner_iconStyle=f'margin-top:2px; font-weight:bold; font-size:{f_size};', number=marker_text)
            elif unselected_here:
                first_unsel = unselected_here[0]
                cod_val = parse_cod(first_unsel['Dobírka (Kč)'])
                eshop_name = first_unsel.get('E-shop', '')
                if eshop_name == 'Max-i.cz': m_txt = 'M'
                elif eshop_name == 'Vomaks.cz': m_txt = 'V'
                elif eshop_name == 'Slevadoma.cz': m_txt = 'S'
                else: m_txt = '?'
                if len(unselected_here) > 1: m_txt += "+"
                
                bg_col = '#e74c3c' if cod_val > 0 else '#3498db'
                bd_col = '#c0392b' if cod_val > 0 else '#2980b9'
                ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color=bg_col, border_color=bd_col, inner_iconStyle='margin-top:2px; font-weight:bold; font-size:14px;', number=m_txt)
            else:
                # Zůstaly jen zmrazené
                ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color='#95a5a6', border_color='#7f8c8d', inner_iconStyle='margin-top:2px; font-weight:bold; font-size:14px;', number='🔒')
                
            folium.Marker(location=[lat, lon], tooltip=folium.Tooltip(vzhled_bubliny), icon=ikona).add_to(mapa_cr)
        
    map_data = st_folium(mapa_cr, height=600, use_container_width=True, returned_objects=["last_object_clicked_tooltip", "last_active_drawing"])

    if map_data and map_data.get("last_object_clicked_tooltip"):
        clicked_tooltip = map_data["last_object_clicked_tooltip"]
        matches = re.findall(r"\[ID:(.*?)\]", clicked_tooltip)
        if matches:
            if clicked_tooltip != st.session_state['last_clicked_tooltip']:
                with st.spinner("🔄 Upravuji bod(y) na mapě..."):
                    st.session_state['last_clicked_tooltip'] = clicked_tooltip
                    routes_modified = False

                    if st.session_state.get('split_mode', False):
                        # --- MÓD ROZDĚLENÍ ---
                        for clicked_id in matches:
                            clicked_id = clicked_id.strip()
                            if clicked_id in st.session_state.get('split_marked_orders', []):
                                st.session_state['split_marked_orders'].remove(clicked_id)
                            else:
                                if clicked_id in st.session_state['selected_orders']:
                                    st.session_state['split_marked_orders'].append(clicked_id)
                        st.rerun()
                        
                    elif st.session_state.get('lock_pos_mode', False):
                        # --- MÓD ZÁMKU POZICE ---
                        for clicked_id in matches:
                            clicked_id = clicked_id.strip()
                            if clicked_id in st.session_state.get('locked_pos_orders', []):
                                st.session_state['locked_pos_orders'].remove(clicked_id)
                            else:
                                if clicked_id in st.session_state['selected_orders']:
                                    st.session_state['locked_pos_orders'].append(clicked_id)
                        st.rerun()
                        
                    elif st.session_state.get('sleep_mode', False):
                        # --- MÓD PŘESPÁNÍ ---
                        for clicked_id in matches:
                            clicked_id = clicked_id.strip()
                            if st.session_state.get('sleep_after_oid') == clicked_id:
                                st.session_state['sleep_after_oid'] = None
                            else:
                                st.session_state['sleep_after_oid'] = clicked_id
                        st.rerun()
                        
                    elif st.session_state.get('freeze_mode', False):
                        # --- MRAZÍCÍ MÓD AKTIVNÍ ---
                        for clicked_id in matches:
                            clicked_id = clicked_id.strip()
                            if clicked_id in st.session_state.get('frozen_orders', []):
                                st.session_state['frozen_orders'].remove(clicked_id)
                            else:
                                if 'frozen_orders' not in st.session_state: st.session_state['frozen_orders'] = []
                                st.session_state['frozen_orders'].append(clicked_id)
                                if clicked_id in st.session_state['selected_orders']:
                                    st.session_state['selected_orders'].remove(clicked_id)
                                    for r in load_routes():
                                        if clicked_id in r.get('orders', []):
                                            r['orders'].remove(clicked_id); routes_modified = True
                                            
                    else:
                        # --- NORMÁLNÍ MÓD (PŘIDÁVÁNÍ DO TRASY) ---
                        all_selected = all(m.strip() in st.session_state['selected_orders'] for m in matches if m.strip() not in st.session_state.get('frozen_orders', []))
                        for clicked_id in matches:
                            clicked_id = clicked_id.strip()
                            if clicked_id in st.session_state.get('frozen_orders', []):
                                continue
                                
                            if all_selected:
                                if clicked_id in st.session_state['selected_orders']:
                                    st.session_state['selected_orders'].remove(clicked_id)
                                    for r in load_routes():
                                        if clicked_id in r.get('orders', []): 
                                            r['orders'].remove(clicked_id); routes_modified = True
                            else:
                                if clicked_id not in st.session_state['selected_orders']:
                                    st.session_state['selected_orders'].append(clicked_id)
                                
                    if routes_modified: 
                        saved_routes = [r for r in load_routes() if len(r.get('orders', [])) > 0]
                        save_routes(saved_routes)
                    st.rerun()

    if map_data and map_data.get("last_active_drawing"):
        drawing = map_data["last_active_drawing"]
        geom_str = str(drawing['geometry']['coordinates'])
        if geom_str != st.session_state.get('last_processed_drawing', ''):
            st.session_state['last_processed_drawing'] = geom_str
            if drawing['geometry']['type'] == 'Polygon':
                poly_coords = drawing['geometry']['coordinates'][0]
                
                def point_in_polygon(lon, lat, poly):
                    x, y = lon, lat; inside = False; n = len(poly); p1x, p1y = poly[0]
                    for i in range(1, n + 1):
                        p2x, p2y = poly[i % n]
                        if y > min(p1y, p2y):
                            if y <= max(p1y, p2y):
                                if x <= max(p1x, p2x):
                                    if p1y != p2y: xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                                    if p1x == p2x or x <= xinters: inside = not inside
                        p1x, p1y = p2x, p2y
                    return inside

                changes = False
                for idx, row in df_orders.dropna(subset=['lat', 'lon']).iterrows():
                    if point_in_polygon(row['lon'], row['lat'], poly_coords):
                        oid = row['Číslo objednávky']
                        
                        if st.session_state.get('split_mode', False):
                            # --- LASO PRO ROZDĚLENÍ ---
                            if oid in st.session_state['selected_orders'] and oid not in st.session_state.get('split_marked_orders', []):
                                st.session_state['split_marked_orders'].append(oid)
                                changes = True
                        elif st.session_state.get('freeze_mode', False):
                            # --- LASO PRO ZMRAZENÍ ---
                            if oid not in st.session_state.get('frozen_orders', []):
                                if 'frozen_orders' not in st.session_state: st.session_state['frozen_orders'] = []
                                st.session_state['frozen_orders'].append(oid)
                                if oid in st.session_state['selected_orders']:
                                    st.session_state['selected_orders'].remove(oid)
                                changes = True
                        elif st.session_state.get('sleep_mode', False):
                            pass
                        elif st.session_state.get('lock_pos_mode', False):
                            # --- LASO PRO ZÁMEK POZICE ---
                            if oid in st.session_state['selected_orders'] and oid not in st.session_state.get('locked_pos_orders', []):
                                st.session_state['locked_pos_orders'].append(oid)
                                changes = True
                        else:
                            # --- KLASICKÉ PŘIDÁVÁNÍ DO TRASY ---
                            if oid not in st.session_state['selected_orders'] and oid not in st.session_state.get('frozen_orders', []):
                                st.session_state['selected_orders'].append(oid)
                                changes = True
                
                if changes:
                    st.rerun()

# Pokud je Krok 2 aktivní, zobrazíme ho ve vedlejším sloupci
if col_step2 is not None:
    with col_step2:
        st.subheader("Krok 2: Seřazení trasy a poznámky")
        tab_sort, tab_notes = st.tabs(["🗺️ Seřadit trasu (Myší)", "📝 Dopsat poznámky a adresy"])
        
        with tab_sort:
            with st.container(height=650):
                st.info("Pokud vybíráte přes Lasso hromadně, doporučujeme následně kliknout na Automatickou optimalizaci pro ideální seřazení cesty.")
                
                col_opt1, col_opt2 = st.columns(2)
                with col_opt1:
                    btn_opt = st.button("🎰 Automaticky optimalizovat pořadí (Od skladu do cíle)", use_container_width=True)
                with col_opt2:
                    btn_rev = st.button("🔄 Otočit směr trasy (Od konce na začátek)", use_container_width=True)
                    
                if btn_opt:
                    with st.spinner("Počítám nejkratší logistickou smyčku..."):
                        start_lat, start_lon = geocode_address_api(st.session_state['st_start_address'], mapy_api_key)
                        end_lat, end_lon = geocode_address_api(st.session_state['st_end_address'], mapy_api_key)
                        if start_lat is not None and start_lon is not None and end_lat is not None and end_lon is not None:
                            points = [{'id': 'START', 'lat': start_lat, 'lon': start_lon}]
                            for oid in st.session_state['selected_orders']:
                                row = df_orders[df_orders['Číslo objednávky'] == oid].iloc[0]
                                if pd.notna(row['lat']) and pd.notna(row['lon']):
                                    points.append({'id': oid, 'lat': row['lat'], 'lon': row['lon']})
                            points.append({'id': 'END', 'lat': end_lat, 'lon': end_lon})
                            
                            dist_matrix = {}
                            for i in range(len(points)):
                                dist_matrix[points[i]['id']] = {}
                                for j in range(len(points)):
                                    if i == j: dist_matrix[points[i]['id']][points[j]['id']] = 0.0
                                    else: dist_matrix[points[i]['id']][points[j]['id']] = geodesic((points[i]['lat'], points[i]['lon']), (points[j]['lat'], points[j]['lon'])).kilometers
                                        
                            route_nodes = [p['id'] for p in points]
                            
                            # --- Optimalizace trasy po blocích (Respektování zafixovaných bodů) ---
                            locked_oids = st.session_state.get('locked_pos_orders', [])
                            if locked_oids:
                                chunks = []
                                current_chunk = []
                                for node in route_nodes:
                                    current_chunk.append(node)
                                    # Pokud narazíme na zamknutý bod (nebo cíl), uzavřeme blok
                                    if node in locked_oids or node == 'END':
                                        chunks.append(current_chunk)
                                        current_chunk = [node] 
                                        
                                optimized_route_nodes = []
                                for i, chunk in enumerate(chunks):
                                    if len(chunk) <= 2:
                                        opt_chunk = chunk 
                                    else:
                                        opt_chunk = optimize_route_2opt(chunk, dist_matrix)
                                        
                                    if i == 0: optimized_route_nodes.extend(opt_chunk)
                                    else: optimized_route_nodes.extend(opt_chunk[1:]) 
                            else:
                                # Standardní optimalizace bez zámků
                                optimized_route_nodes = optimize_route_2opt(route_nodes, dist_matrix)
                            
                            st.session_state['selected_orders'] = [n for n in optimized_route_nodes if n not in ['START', 'END']]
                            
                            # --- NOVINKA: Tiché Auto-Uložení do cloudu ---
                            drafts = load_drafts()
                            drafts[st.session_state['st_user_name']] = {
                                'selected_orders': st.session_state['selected_orders'],
                                'editing_route_id': st.session_state.get('editing_route_id'),
                                'manual_orders': st.session_state.get('manual_orders', []),
                                'manual_products': st.session_state.get('manual_products', {}),
                                'st_route_name': st.session_state.get('st_route_name', '')
                            }
                            save_drafts(drafts)
                            # ---------------------------------------------
                            
                            st.rerun()
                        else: st.error("Nepodařilo se zjistit souřadnice skladu.")
                        
                if btn_rev:
                    st.session_state['selected_orders'].reverse()
                    st.rerun()
                
                items_list = []
                mapping_dict = {}
                for i, row in df_selected.iterrows():
                    p_html = str(row.get('Produkty', ''))
                    if "Neznámé" in p_html or "Žádné" in p_html or not p_html.strip():
                        p_text = "Neznámé položky"
                    else:
                        items_count = p_html.count('<br>-')
                        if items_count == 1: p_text = "1 položka"
                        elif 1 < items_count < 5: p_text = f"{items_count} položky"
                        else: p_text = f"{items_count} položek"
                    
                    zastavka_num = i + 1
                    item_str = f"📍 {zastavka_num}. zastávka | [{row['Číslo objednávky']}] 👤 {row['Příjemce']} | 📦 {p_text} | 📍 {row['Celá_adresa']} | 💰 {row['Dobírka (Kč)']} Kč"
                    items_list.append(item_str)
                    mapping_dict[item_str] = row.to_dict()
                    
                sortable_data = [
                    {"header": "🗺️ Vaše aktuální trasa", "items": items_list},
                    {"header": "🗑️ Odebrat z trasy (Vrátí se zpět na mapu)", "items": []}
                ]
                
                sorted_res = sort_items(sortable_data, multi_containers=True)
                
                if sorted_res:
                    active_items = sorted_res[0]['items']
                    new_active_oids = [mapping_dict[item]['Číslo objednávky'] for item in active_items]
                    
                    if new_active_oids != st.session_state['selected_orders']:
                        st.session_state['selected_orders'] = new_active_oids
                        st.rerun()
                        
                sorted_strings = sorted_res[0]['items'] if sorted_res else items_list

        with tab_notes:
            with st.container(height=650):
                st.info("Zde můžete k seřazeným objednávkám dopsat vzkaz řidiči. Zde také vidíte detailní rozpis produktů pro plánování nákladu.")
                order_notes = {}
                order_addresses = {}
                for s in sorted_strings:
                    order_data = mapping_dict[s]
                    order_id = order_data['Číslo objednávky']
                    
                    p_html = str(order_data.get('Produkty', ''))
                    p_plain = p_html.replace('<br>- ', '<br>• ').replace('<br>', '<br>').replace('<i>', '').replace('</i>', '').strip()
                    if "Žádné" in p_plain or not p_plain: p_plain = "<i>Neznámé nebo žádné produkty</i>"
                    
                    c_title, c_toptrans = st.columns([3, 1])
                    c_title.markdown(f"**{order_id} ({order_data['E-shop']}) | 👤 {order_data['Příjemce']}**")
                    c_title.markdown(f"<div style='font-size: 0.85em; color: #7f8c8d; margin-top: -10px; margin-bottom: 10px;'>📦 {p_plain}</div>", unsafe_allow_html=True)
                    
                    # --- TLACITKO TOPTRANS ---
                    if c_toptrans.button("🧮 Zjistit cenu Toptrans", key=f"tt_btn_{order_id}", use_container_width=True):
                        tt_db = load_toptrans_db()
                        missing_product = None
                        total_weight = 0.0
                        total_volume = 0.0
                        
                        raw_prods = [p.strip() for p in p_html.replace('<i>', '').replace('</i>', '').split('<br>') if p.strip() and p.strip() != '-']
                        
                        # Rozebrání produktů a hledání v databázi
                        for prod in raw_prods:
                            if prod.startswith('- '): prod = prod[2:]
                            
                            # Detekce kusů (např. "2x Postel")
                            qty = 1
                            m_qty = re.match(r'^(\d+)[xX]\s+(.*)', prod)
                            if m_qty:
                                qty = int(m_qty.group(1))
                                clean_name = m_qty.group(2).strip()
                            else:
                                clean_name = prod.strip()
                                
                            if clean_name not in tt_db and "Neznámé" not in clean_name and "Žádné" not in clean_name:
                                missing_product = clean_name
                                break
                            elif clean_name in tt_db:
                                props = tt_db[clean_name]
                                # Databáze už v sobě má sečtené všechny balíky, takže jen vynásobíme kusy
                                total_weight += props['Vaha'] * qty
                                total_volume += props['Objem'] * qty
                                
                        if missing_product:
                            toptrans_product_dialog(missing_product, order_data['PSČ'], parse_cod(order_data.get('Dobírka (Kč)', 0)), order_id)
                        else:
                            with st.spinner("Počítám cenu u Toptransu (z Perninku)..."):
                                price, err = calculate_toptrans_price("36236", order_data['PSČ'], total_weight, total_volume, parse_cod(order_data.get('Dobírka (Kč)', 0)), order_data.get('Celá_adresa', ''))
                                if price is not None:
                                    st.session_state[f"tt_price_{order_id}"] = price
                                else:
                                    st.error(f"Nepodařilo se spočítat: {err}")
                                    
                    if f"tt_price_{order_id}" in st.session_state:
                        c_toptrans.success(f"Cena: **{st.session_state[f'tt_price_{order_id}']} Kč**")
                    # -------------------------
                    
                    col_note, col_addr = st.columns(2)
                    with col_note:
                        default_note = st.session_state.get(f"note_{order_id}", "")
                        order_notes[order_id] = st.text_input("Poznámka pro řidiče:", value=default_note, key=f"note_input_{order_id}")
                    with col_addr:
                        original_full_address = f"{order_data['Ulice']}, {order_data['Město']} {order_data['PSČ']}".strip(', ')
                        default_addr = st.session_state.get(f"addr_{order_id}", original_full_address)
                        order_addresses[order_id] = st.text_input("Upravená adresa pro tisk:", value=default_addr, key=f"addr_input_{order_id}")
                    st.markdown("<hr style='margin: 10px 0; border-top: 1px dashed #ddd;'>", unsafe_allow_html=True)

# --- KROK 3: TISK (V HLAVNÍM PROSTORU) ---
st.markdown("---")
st.subheader("Krok 3: Tisk a časy")

# NOVINKA: "Neprůstřelné" načtení hodnot nezávisle na záludnostech Streamlitu
v_name = st.session_state.get('st_route_name', '')
v_date = st.session_state.get('st_route_date', datetime.today())
v_date_unknown = st.session_state.get('st_route_date_unknown', False)
v_driver = st.session_state.get('st_driver_name', '')

col_rn1, col_rn2, col_rn3 = st.columns(3)
input_route_name = col_rn1.text_input("📝 Název trasy (např. Plzeň)", value=v_name)

# --- NOVINKA: Volba Neurčeno a výpočet dne v týdnu ---
is_date_unknown = col_rn2.checkbox("❓ Zatím neurčeno (Bez pevného data)", value=v_date_unknown)

if is_date_unknown:
    input_route_date = v_date # Schováme si do pozadí dnešek, aby to nepadalo
    col_rn2.info("Datum bude uloženo jako neurčené.")
    final_route_date_str = "Neurčeno"
else:
    cz_days = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
    try: den_v_tydnu = cz_days[v_date.weekday()]
    except: den_v_tydnu = ""
    input_route_date = col_rn2.date_input(f"📅 Datum rozvozu ({den_v_tydnu})", value=v_date, help=f"Tento rozvoz vychází na: {den_v_tydnu}")
    final_route_date_str = input_route_date.strftime('%Y-%m-%d')

input_driver_name = col_rn3.text_input("🧑‍✈️ Jméno řidiče", value=v_driver)

if st.session_state.get('sleep_after_oid'):
    st.warning(f"🏨 V trase je nastaveno přespání po objednávce: **{st.session_state['sleep_after_oid']}**.")
    st.session_state['day2_start_time'] = st.time_input("⏰ Čas výjezdu další den (po přespání):", value=st.session_state.get('day2_start_time', datetime_time(8, 0)))

# Trvalé zapsání zpět, aby se nám to neztratilo po kliknutí na jiná tlačítka
st.session_state['st_route_name'] = input_route_name
st.session_state['st_route_date'] = input_route_date
st.session_state['st_route_date_unknown'] = is_date_unknown
st.session_state['st_driver_name'] = input_driver_name
    
slow_mode = st.checkbox("🐌 Režim 'Šnek' (Automaticky natáhne čistý čas jízdy o 10 %)")

r_parts = []
if input_route_name: r_parts.append(input_route_name)

# NOVINKA: Chytřejší zápis data do hlavního názvu
if is_date_unknown:
    r_parts.append("Neurčeno")
elif st.session_state.get('sleep_after_oid'):
    d2_date = input_route_date + timedelta(days=1)
    r_parts.append(f"{input_route_date.strftime('%d.%m.%Y')} - {d2_date.strftime('%d.%m.%Y')}")
else:
    r_parts.append(input_route_date.strftime('%d.%m.%Y'))
    
if input_driver_name: r_parts.append(f"Řidič: {input_driver_name}")
route_name_input = " | ".join(r_parts)

current_orders = [mapping_dict[s]['Číslo objednávky'] for s in sorted_strings if s in mapping_dict] if not df_selected.empty else []
loaded_orders = st.session_state.get('loaded_route_orders', [])
route_changed = (current_orders != loaded_orders)

# --- NOVINKA: HROMADNÁ KALKULACE S POKLADNÍM PÁSEM ---
if current_orders:
    st.markdown("<br>", unsafe_allow_html=True)
    
    # 1. Zpracování fronty (pokud je na pásu nějaký neznámý produkt)
    if 'missing_queue' in st.session_state and st.session_state['missing_queue']:
        st.warning(f"📦 Hromadné doplňování: Ve frontě čeká {len(st.session_state['missing_queue'])} neznámých produktů.")
        first_missing = st.session_state['missing_queue'][0]
        # Vyvolání okna pro první produkt ve frontě
        toptrans_product_dialog(first_missing[0], first_missing[1], first_missing[2], first_missing[3], psc_skladu="36236")
        
    # 2. Automatické spuštění výpočtu po vyprázdnění fronty
    elif st.session_state.get('auto_bulk', False):
        st.session_state['auto_bulk'] = False
        tt_db = load_toptrans_db()
        total_tt_price = 0.0
        success_count = 0
        errors = []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_count = len(sorted_strings)
        
        for idx, s in enumerate(sorted_strings):
            order_data = mapping_dict[s]
            oid = order_data['Číslo objednávky']
            status_text.text(f"Počítám Toptrans pro: {oid} ({idx+1}/{total_count})...")
            
            p_html = str(order_data.get('Produkty', ''))
            raw_prods = [p.strip() for p in p_html.replace('<i>', '').replace('</i>', '').split('<br>') if p.strip() and p.strip() != '-']
            
            tot_w, tot_v = 0.0, 0.0
            missing_prod_name = None
            
            for prod in raw_prods:
                if prod.startswith('- '): prod = prod[2:]
                qty = 1
                m_qty = re.match(r'^(\d+)[xX]\s+(.*)', prod)
                if m_qty:
                    qty, clean_name = int(m_qty.group(1)), m_qty.group(2).strip()
                else: clean_name = prod.strip()
                
                if clean_name not in tt_db and "Neznámé" not in clean_name and "Žádné" not in clean_name:
                    missing_prod_name = clean_name
                    break
                elif clean_name in tt_db:
                    tot_w += tt_db[clean_name]['Vaha'] * qty
                    tot_v += tt_db[clean_name]['Objem'] * qty
                    
            if missing_prod_name:
                errors.append(f"[{oid}] Zákazník: {order_data['Příjemce']} - Uloženo přeskočení produktu '{missing_prod_name}'.")
            else:
                time.sleep(0.3) # Pauza proti zablokování
                price, err = calculate_toptrans_price("36236", order_data['PSČ'], tot_w, tot_v, parse_cod(order_data.get('Dobírka (Kč)', 0)), order_data.get('Celá_adresa', ''))
                if price is not None:
                    total_tt_price += float(price)
                    success_count += 1
                    st.session_state[f"tt_price_{oid}"] = price
                else:
                    errors.append(f"[{oid}] {err}")
                    
            progress_bar.progress((idx + 1) / total_count)
            
        status_text.empty()
        progress_bar.empty()
        cena_format = f"{int(total_tt_price):,} Kč".replace(',', ' ')
        
        if success_count == total_count:
            st.success(f"🎉 **Hromadná kalkulace úspěšná!** Poslat úplně celou tuto trasu přes Toptrans by stálo **{cena_format}** bez DPH.")
        else:
            st.warning(f"⚠️ **Kalkulace proběhla částečně:** Spočítáno {success_count} z {total_count} objednávek. Částečná cena: **{cena_format}** bez DPH.")
            if errors:
                with st.expander("Zobrazit důvody, proč se některé objednávky nespočítaly:"):
                    for e in errors: st.write(e)
                    
    # 3. Základní tlačítko (Když není fronta a neprobíhá auto-výpočet)
    elif not st.session_state.get('auto_bulk', False) and not st.session_state.get('missing_queue', []):
        if st.button("🚚 Zjistit celkovou cenu trasy přes Toptrans", type="secondary", use_container_width=True):
            tt_db = load_toptrans_db()
            missing_items = []
            seen_prods = set() # Pojistka proti duplikátům ve frontě
            
            for s in sorted_strings:
                order_data = mapping_dict[s]
                p_html = str(order_data.get('Produkty', ''))
                raw_prods = [p.strip() for p in p_html.replace('<i>', '').replace('</i>', '').split('<br>') if p.strip() and p.strip() != '-']
                
                for prod in raw_prods:
                    if prod.startswith('- '): prod = prod[2:]
                    m_qty = re.match(r'^(\d+)[xX]\s+(.*)', prod)
                    clean_name = m_qty.group(2).strip() if m_qty else prod.strip()
                    
                    if clean_name not in tt_db and "Neznámé" not in clean_name and "Žádné" not in clean_name:
                        if clean_name not in seen_prods:
                            seen_prods.add(clean_name)
                            # Přidání neznámého produktu na pás
                            missing_items.append((clean_name, order_data['PSČ'], parse_cod(order_data.get('Dobírka (Kč)', 0)), order_data['Číslo objednávky']))
                            
            if missing_items:
                st.session_state['missing_queue'] = missing_items
                st.session_state['auto_bulk'] = True # Zapne automatický výpočet hned po vyprázdnění fronty
                st.rerun()
            else:
                st.session_state['auto_bulk'] = True # Všechny známe, pustíme to hned!
                st.rerun()
                
    st.markdown("<br>", unsafe_allow_html=True)
# -------------------------------------------------------------

# --- CHYTRÁ POJISTKA PROTI ULOŽENÍ STARÝCH DAT ---
# Pokud se seznam objednávek na mapě liší od posledního výpočtu, skryjeme tlačítko Uložit
if 'print_main' in st.session_state:
    calc_orders = [row['Číslo objednávky'] for row in st.session_state['print_main'].get('itinerary_data', []) if row['Číslo objednávky'] not in ['START', 'CÍL']]
    if current_orders != calc_orders:
        st.session_state['calc_main'] = False
# --------------------------------------------------

is_editing = bool(st.session_state.get('editing_route_id'))

if is_editing and not route_changed and not df_selected.empty:
    col_b1, col_b2 = st.columns(2)
    btn_fast_save = col_b1.button("💾 Rychlé uložení (bez přepočtu trasy)", type="primary", use_container_width=True)
    btn_calc = col_b2.button("🚀 Vypočítat časy a generovat nové PDF", type="secondary", use_container_width=True)
else:
    btn_fast_save = False
    if is_editing and route_changed:
        st.warning("⚠️ Trasa nebo její pořadí se změnilo. Před uložením je nutný nový výpočet časů.")
    btn_calc = st.button("🚀 Vypočítat časy a vygenerovat všechny soubory", type="primary", use_container_width=True, disabled=df_selected.empty)


if btn_fast_save:
    editing_id = st.session_state.get('editing_route_id')
    latest_routes = load_routes()
    for r in latest_routes:
        if r['id'] == editing_id:
            r['raw_route_name'] = input_route_name
            r['route_date'] = final_route_date_str
            r['driver_name'] = input_driver_name
            r['name'] = route_name_input
            r['kasac_value'] = st.session_state['st_kasac_value']
            r['start_time_str'] = st.session_state['st_start_time'].strftime('%H:%M')
            r['unload_time_min'] = st.session_state['st_unload_time_min']
            r['slow_mode'] = slow_mode

            if 'details' not in r: r['details'] = {}
            for oid in current_orders:
                if oid not in r['details']: r['details'][oid] = {}
                r['details'][oid]['note'] = order_notes.get(oid, "")
                r['details'][oid]['addr'] = order_addresses.get(oid, "")
                r['details'][oid]['tt_price'] = float(st.session_state.get(f"tt_price_{oid}", 0)) # Zápis ceny

            # Okamžitý přepočet celkové Toptrans sumy při rychlém uložení
            r['total_tt_price'] = sum(float(r['details'].get(x['Číslo objednávky'], {}).get('tt_price', 0)) for x in r.get('itinerary_data', []) if x['Číslo objednávky'] not in ['START', 'CÍL'] and r['details'].get(x['Číslo objednávky'], {}).get('dispatch_status') != 'Zrušeno')

            if 'itinerary_data' in r:
                for itin_row in r['itinerary_data']:
                    oid = itin_row['Číslo objednávky']
                    if oid in order_notes: itin_row['Poznámka'] = order_notes[oid]
                    if oid in order_addresses: itin_row['Tisk_Adresa'] = order_addresses[oid]
            break
            
    save_routes(latest_routes)
    st.session_state['trigger_clear'] = True
    st.session_state['scroll_to_top'] = True  # <--- NOVINKA: Zapne skrolování nahoru
    st.session_state['show_success_msg'] = f"✅ Rozvoz '{route_name_input}' byl rychle uložen bez přepočtu!"
    st.rerun()


if btn_calc:
    sorted_ids_safe = [mapping_dict[s]['Číslo objednávky'] for s in sorted_strings if s in mapping_dict]
    final_rows = [mapping_dict[s] for s in sorted_strings if s in mapping_dict]
    final_df = pd.DataFrame(final_rows)
    
    final_df['Poznámka'] = final_df['Číslo objednávky'].map(order_notes)
    final_df['Tisk_Adresa'] = final_df['Číslo objednávky'].map(order_addresses)
    
    with st.spinner("Geokóduji zadané adresy startu a cíle..."):
        start_lat, start_lon = geocode_address_api(st.session_state['st_start_address'], mapy_api_key)
        end_lat, end_lon = geocode_address_api(st.session_state['st_end_address'], mapy_api_key)
        if start_lat is None or end_lat is None: 
            st.error("Nelze nalézt adresu startu nebo cíle."); st.stop()
            
    itinerary = []
    itinerary.append({
        'Číslo objednávky': 'START', 'Příjemce': st.session_state['st_start_point_name'], 
        'Tisk_Adresa': st.session_state['st_start_address'], 'Město': '', 'PSČ': '', 'Chyba': '', 'Telefon': '', 'Dobírka (Kč)': 0, 
        'Poznámka': '', 'lat': start_lat, 'lon': start_lon, 'E-shop': '', 'Produkty': ''
    })
    for _, row in final_df.iterrows(): itinerary.append(row.to_dict())
    itinerary.append({
        'Číslo objednávky': 'CÍL', 'Příjemce': st.session_state['st_end_point_name'], 
        'Tisk_Adresa': st.session_state['st_end_address'], 'Město': '', 'PSČ': '', 'Chyba': '', 'Telefon': '', 'Dobírka (Kč)': 0, 
        'Poznámka': '', 'lat': end_lat, 'lon': end_lon, 'E-shop': '', 'Produkty': ''
    })
    
    # --- NOVINKA: Načtení počtu balíků z historie, pokud už se štítky dělaly dříve ---
    latest_db_details = {}
    editing_id = st.session_state.get('editing_route_id')
    if editing_id:
        for r_db in load_routes():
            if r_db['id'] == editing_id:
                latest_db_details = r_db.get('details', {})
                break
                
    for row_dict in itinerary:
        oid = row_dict['Číslo objednávky']
        if oid not in ['START', 'CÍL']:
            row_dict['Počet balíků'] = latest_db_details.get(oid, {}).get('pkg_count', 1)
    # --------------------------------------------------------------------------------
    
    df_itinerary = pd.DataFrame(itinerary)
    
    active_itin = []
    for i, row in df_itinerary.iterrows():
        oid = row['Číslo objednávky']
        if oid in ['START', 'CÍL'] or st.session_state.get('loaded_statuses', {}).get(oid, '') != 'Zrušeno':
            active_itin.append(row)

    segments_data = []
    with st.spinner("Počítám časy přejezdů přes Mapy.cz..."):
        # --- OPRAVA: DOPLNĚNÍ CHYBĚJÍCÍHO STAŽENÍ DAT Z MAP ---
        for i in range(len(active_itin) - 1):
            dist, dur = get_driving_data(active_itin[i]['lat'], active_itin[i]['lon'], active_itin[i+1]['lat'], active_itin[i+1]['lon'], mapy_api_key)
            segments_data.append((dist, dur))
        # ------------------------------------------------------
        current_dt = datetime.combine(datetime.today(), st.session_state['st_start_time'])
        
    arrival_times, arrival_windows, distances_to_next, times_to_next = [current_dt.strftime('%H:%M')], ['-'], [], []
    
    for i in range(len(active_itin) - 1):
        curr_node_oid = active_itin[i]['Číslo objednávky']
        
        # KONTROLA PŘESPÁNÍ PRO ZMĚNU ČASU VÝJEZDU
        if curr_node_oid == st.session_state.get('sleep_after_oid'):
            current_dt = datetime.combine(current_dt.date() + timedelta(days=1), st.session_state.get('day2_start_time', datetime_time(8, 0)))

        dist, dur = segments_data[i]
        if slow_mode: dur = dur * 1.1
        distances_to_next.append(int(round(dist)))
        times_to_next.append(int(dur))
        arrival_dt = current_dt + timedelta(minutes=int(dur))
        
        if i + 1 == len(active_itin) - 1:
            arrival_times.append(arrival_dt.strftime('%H:%M')); arrival_windows.append('-')
        else:
            arrival_times.append(arrival_dt.strftime('%H:%M')); win_start = round_up_to_15_minutes(arrival_dt)
            arrival_windows.append(f"{win_start.strftime('%H:%M')} - {(win_start + timedelta(hours=2)).strftime('%H:%M')}")
            current_dt = arrival_dt + timedelta(minutes=st.session_state['st_unload_time_min'])
            
    distances_to_next.append(0)
    times_to_next.append(0)
    
    active_idx = 0
    cas_prijezdu_col = []
    okno_prijezdu_col = []
    vzdalen_col = []
    cas_k_dalsi_col = []

    for i, row in df_itinerary.iterrows():
        oid = row['Číslo objednávky']
        if oid in ['START', 'CÍL'] or st.session_state.get('loaded_statuses', {}).get(oid, '') != 'Zrušeno':
            cas_prijezdu_col.append(arrival_times[active_idx])
            okno_prijezdu_col.append(arrival_windows[active_idx])
            vzdalen_col.append(distances_to_next[active_idx])
            cas_k_dalsi_col.append(times_to_next[active_idx])
            active_idx += 1
        else:
            cas_prijezdu_col.append("ZRUŠENO")
            okno_prijezdu_col.append("-")
            vzdalen_col.append(0)
            cas_k_dalsi_col.append(0)

    df_itinerary['Čas příjezdu'] = cas_prijezdu_col
    df_itinerary['Okno příjezdu (2h)'] = okno_prijezdu_col
    df_itinerary['Vzdálen k další (km)'] = vzdalen_col
    df_itinerary['Čas k další (min)'] = cas_k_dalsi_col
    
    total_km = int(sum(distances_to_next))
    pure_drive_min = int(sum(times_to_next))
    total_hours = f"{pure_drive_min // 60}h {pure_drive_min % 60}min"
    
    total_cod = sum(parse_cod(row['Dobírka (Kč)']) for _, row in df_itinerary.iterrows() if row['Číslo objednávky'] not in ['START', 'CÍL'] and st.session_state.get('loaded_statuses', {}).get(row['Číslo objednávky'], '') != 'Zrušeno')
    
    def format_drive_time(m):
        try: m = int(float(m)); return f"{m//60}:{m%60:02d} h" if m >= 60 else f"{m} min"
        except: return ""

    df_web_display = df_itinerary.copy().astype(str)
    df_web_display['Čas přejezdu'] = df_itinerary['Čas k další (min)'].apply(format_drive_time)
    for bad_val in ['none', 'nan', '<na>', 'none.', 'nan.']:
        df_web_display.replace(bad_val, "", inplace=True)
        df_web_display.replace(bad_val.upper(), "", inplace=True)
        df_web_display.replace(bad_val.capitalize(), "", inplace=True)
    
    df_final_display = df_web_display[[
        'Číslo objednávky', 'E-shop', 'Příjemce', 'Tisk_Adresa', 
        'Telefon', 'Dobírka (Kč)', 'Čas příjezdu', 'Okno příjezdu (2h)', 
        'Vzdálen k další (km)', 'Čas přejezdu', 'Poznámka'
    ]]

    with st.spinner("Vytvářím náhledová data..."):
        pdf_dict = generate_all_pdfs(
            route_name_input, df_itinerary, total_km, total_hours, total_cod, 
            st.session_state['st_kasac_value'], st.session_state['st_start_time'].strftime('%H:%M'), mapy_api_key,
            st.session_state.get('sleep_after_oid'), st.session_state.get('day2_start_time', datetime_time(8,0)).strftime('%H:%M')
        )
        buffer_xls = io.BytesIO()
        with pd.ExcelWriter(buffer_xls, engine='openpyxl') as writer: df_final_display.to_excel(writer, index=False, sheet_name='Trasový soupis')
        pdf_dict['xls'] = buffer_xls.getvalue()

        st.session_state['print_main'] = {
            'km': total_km, 'hours': total_hours, 'cod': int(total_cod), 
            'df': df_final_display, 'itinerary_data': df_itinerary.to_dict('records'), 'pdf_dict': pdf_dict
        }
        
    st.session_state['calc_main'] = True
    st.session_state['scroll_to_summary'] = True  # <--- NOVINKA: Zapne skrolování k souhrnu
    st.rerun()

# --- FINÁLNÍ VÝSLEDEK ---
if st.session_state.get('calc_main') and 'print_main' in st.session_state:
    st.markdown("---")
    
    # --- NOVINKA: Automatický sjezd dolů na Souhrn ---
    st.markdown("<div id='summary_target'></div>", unsafe_allow_html=True)
    if st.session_state.get('scroll_to_summary'):
        st.html("<script>window.parent.document.getElementById('top_target').scrollIntoView({behavior: 'smooth', block: 'start'});</script>")
        st.session_state['scroll_to_summary'] = False
    # ------------------------------------------------
    
    st.subheader("📊 Souhrn a uložení trasy")
    res = st.session_state['print_main']
    st.success("✅ Výpočet dokončen! Zkontrolujte data níže a uložte rozvoz.")
    
    col_res1, col_res2, col_res3 = st.columns(3)
    col_res1.metric(label="🗺️ Celková délka trasy", value=f"{res['km']} km")
    col_res2.metric(label="⏱️ Čistý čas jízdy", value=res['hours'])
    col_res3.metric(label="💰 Celková hotovost z dobírek", value=f"{res['cod']} Kč")
    st.write("")
    st.dataframe(res['df'], use_container_width=True)
    st.info("💡 **Aby nedošlo ke ztrátě dat, uložte prosím rozvoz do historie kliknutím na tlačítko níže.**")

    st.markdown("---")
    if st.button("💾 ULOŽIT ROZVOZ DO HISTORIE (a vyčistit mapu)", type="primary", use_container_width=True):
        sorted_ids_safe = [mapping_dict[s]['Číslo objednávky'] for s in sorted_strings if s in mapping_dict]
        loaded_statuses = st.session_state.get('loaded_statuses', {})
        
        # --- POJISTKA: Přenesení čerstvých poznámek a adres těsně před uložením ---
        for row in res['itinerary_data']:
            oid = row['Číslo objednávky']
            if oid in order_notes: row['Poznámka'] = order_notes[oid]
            if oid in order_addresses: row['Tisk_Adresa'] = order_addresses[oid]
        # -------------------------------------------------------------------------
        
        old_status = "active"
        old_costs = {"fuel": 0.0, "driver": 0.0, "accommodation": 0.0, "other": 0.0}
        
        latest_db_details = {}
        editing_id = st.session_state.get('editing_route_id')
        if editing_id:
            for r_db in load_routes():
                if r_db['id'] == editing_id:
                    latest_db_details = r_db.get('details', {})
                    old_status = r_db.get('status', 'active')
                    old_costs = r_db.get('costs', old_costs)
                    break

        route_details = {}
        for o_id in sorted_ids_safe:
            final_status = loaded_statuses.get(o_id, "")
            if o_id in latest_db_details and latest_db_details[o_id].get("dispatch_status"):
                final_status = latest_db_details[o_id].get("dispatch_status")
                
            old_pkg_count = 1
            if o_id in latest_db_details and "pkg_count" in latest_db_details[o_id]:
                old_pkg_count = latest_db_details[o_id]["pkg_count"]

            route_details[o_id] = {
                "note": order_notes.get(o_id, ""),
                "addr": order_addresses.get(o_id, ""),
                "dispatch_status": final_status,
                "pkg_count": old_pkg_count,
                "tt_price": float(st.session_state.get(f"tt_price_{o_id}", 0)) # Zápis ceny
            }
            
        route_id = editing_id if editing_id else str(time.time())
        
        new_route = {
            "id": route_id, "name": route_name_input, "raw_route_name": input_route_name,
            "route_date": final_route_date_str, "driver_name": input_driver_name,
            "start_address": st.session_state['st_start_address'], "end_address": st.session_state['st_end_address'],
            "start_point_name": st.session_state['st_start_point_name'], "end_point_name": st.session_state['st_end_point_name'],
            "orders": sorted_ids_safe, "details": route_details, "itinerary_data": res['itinerary_data'],
            "total_km": res['km'], "total_hours": res['hours'], "total_cod": res['cod'],
            "kasac_value": st.session_state['st_kasac_value'], "start_time_str": st.session_state['st_start_time'].strftime('%H:%M'),
            "slow_mode": slow_mode, "unload_time_min": st.session_state['st_unload_time_min'],
            "status": old_status, "costs": old_costs,
            "total_tt_price": sum(float(st.session_state.get(f"tt_price_{o_id}", 0)) for o_id in sorted_ids_safe if loaded_statuses.get(o_id) != "Zrušeno"),
            "sleep_after_oid": st.session_state.get('sleep_after_oid'),
            "day2_start_time_str": st.session_state.get('day2_start_time', datetime_time(8,0)).strftime('%H:%M'),
        }
        
        safe_save_route(new_route, delete_id=editing_id)
        
        # --- NOVINKA: Vymazání zálohy po úspěšném uložení ---
        drafts_db = load_drafts()
        if st.session_state['st_user_name'] in drafts_db:
            del drafts_db[st.session_state['st_user_name']]
            save_drafts(drafts_db)
        # ----------------------------------------------------
        
        st.session_state['trigger_clear'] = True
        st.session_state['scroll_to_top'] = True  # <--- NOVINKA: Zapne skrolování nahoru
        st.session_state['show_success_msg'] = f"✅ Rozvoz '{route_name_input}' byl bezpečně uložen!"
        st.rerun()