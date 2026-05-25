import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import BeautifyIcon
import requests
import io
import time
import os
import json
import re
import math
import base64
from PIL import Image
from urllib.parse import quote
from datetime import datetime, timedelta, time as datetime_time
from streamlit_sortables import sort_items
from fpdf import FPDF
import matplotlib.pyplot as plt
from geopy.distance import geodesic

st.set_page_config(page_title="Plánovač tras pro řidiče", layout="wide")
st.title("🚚 Inteligentní plánovač tras (Interaktivní Mapa + Tisk PDF)")
st.write("Aplikace automaticky načítá data ze Shoptetů. Najetím myši na bod uvidíte detaily i s produkty.")

# --- PAMĚŤ PRO ULOŽENÉ ROZVOZY A GEOKÓD (GITHUB API NEBO LOKÁLNÍ) ---
ROUTES_FILE = "saved_routes.json"
GEO_FILE = "geocode_cache.json"

try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
    GITHUB_REPO = st.secrets["GITHUB_REPO"]  
except:
    GITHUB_TOKEN = ""
    GITHUB_REPO = ""

def get_github_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def load_json_from_github_or_local(file_path, default_type):
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        resp = requests.get(url, headers=get_github_headers())
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            try: return json.loads(content)
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

def load_routes(): return load_json_from_github_or_local(ROUTES_FILE, list)
def save_routes(routes): save_json_to_github_or_local(ROUTES_FILE, routes[-20:], f"Rozvozy {datetime.now().strftime('%H:%M:%S')}")
def load_geo_cache(): return load_json_from_github_or_local(GEO_FILE, dict)
def save_geo_cache(cache): save_json_to_github_or_local(GEO_FILE, cache, f"GeoCache {datetime.now().strftime('%H:%M:%S')}")

saved_routes = load_routes()
saved_routes_ids = set()
for r in saved_routes:
    saved_routes_ids.update(r.get('orders', []))

if 'geo_cache' not in st.session_state:
    st.session_state['geo_cache'] = load_geo_cache()

# --- SIDEBAR: NASTAVENÍ ČASŮ A API ---
st.sidebar.header("⚙️ Nastavení výpočtu")
mapy_api_key = st.sidebar.text_input("Mapy.cz REST API klíč", value="3FDgcWrx0FfOCW9IxM7-g1VJYCV-h8Dqv4vkV7wPrD8", type="password")
start_time = st.sidebar.time_input("Čas výjezdu řidiče ze skladu", datetime_time(6, 0))
unload_time_min = st.sidebar.slider("Doba zdržení na zastávce (vykládka v min)", 0, 60, 15)

st.sidebar.markdown("---")
st.sidebar.header("📍 Adresy Startu a Cíle")
st.sidebar.info("Zadejte přesné adresy, kde trasa začíná a končí.")
start_address = st.sidebar.text_input("Adresa startu (Sklad)", value="Karlovy Vary")
end_address = st.sidebar.text_input("Adresa konce (Návrat)", value="Karlovy Vary")
start_point_name = st.sidebar.text_input("Název výchozího bodu", value="SKLAD (Výjezd)")
end_point_name = st.sidebar.text_input("Název cílového bodu", value="SKLAD (Návrat)")

st.sidebar.markdown("---")
st.sidebar.header("💰 Pokladna / Finance")
kasac_value = st.sidebar.number_input("Částka do kasáče (Kč)", min_value=0, value=2000, step=100)

# --- SIDEBAR: LIMITY PRO MAGICKÝ NÁVRH ---
st.sidebar.markdown("---")
st.sidebar.header("🪄 Limity pro Magický návrh")
st.sidebar.info("Nastavte mantinely. Jakmile jich algoritmus i po optimalizaci dosáhne, přestane nabírat body.")
auto_min_orders = st.sidebar.number_input("Minimální počet objednávek", min_value=1, value=10, step=1)
auto_max_km = st.sidebar.number_input("Maximální trasa (km)", min_value=10, value=700, step=50)
auto_max_time_h = st.sidebar.number_input("Maximální čas jízdy (hodiny)", min_value=1.0, value=9.5, step=0.5)

# --- SIDEBAR: HISTORIE ULOŽENÝCH ROZVOZŮ ---
st.sidebar.markdown("---")
st.sidebar.header("📁 Uložené rozvozy (Historie)")
if not saved_routes:
    st.sidebar.info("Zatím nemáte žádné uložené rozvozy.")
else:
    for r in reversed(saved_routes):
        with st.sidebar.expander(f"🗓️ {r['name']} ({len(r['orders'])} obj.)"):
            col1, col2 = st.columns(2)
            
            if col1.button("Otevřít", key=f"open_{r['id']}"):
                st.session_state['selected_orders'] = r['orders'].copy()
                if 'details' in r:
                    for o_id, det in r['details'].items():
                        st.session_state[f"note_{o_id}"] = det.get("note", "")
                        if det.get("addr"): 
                            st.session_state[f"addr_{o_id}"] = det.get("addr", "")
                st.rerun()
                
            if col2.button("Smazat", key=f"del_{r['id']}"):
                saved_routes.remove(r)
                save_routes(saved_routes)
                st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Vynutit aktualizaci dat ze Shoptetu", type="secondary"):
    st.cache_data.clear()
    st.rerun()

# --- INICIALIZACE STAVŮ ---
if 'selected_orders' not in st.session_state: st.session_state['selected_orders'] = []  
if 'last_clicked_tooltip' not in st.session_state: st.session_state['last_clicked_tooltip'] = None
if 'map_center' not in st.session_state: st.session_state['map_center'] = [49.8, 15.5]
if 'map_zoom' not in st.session_state: st.session_state['map_zoom'] = 7

# --- FUNKCE PRO VÝPOČTY A GEOKÓD ---
def round_up_to_15_minutes(dt):
    minutes_to_add = (15 - dt.minute % 15) % 15
    if minutes_to_add == 0 and dt.second == 0: return dt
    if minutes_to_add == 0: minutes_to_add = 15
    return dt + timedelta(minutes=minutes_to_add) - timedelta(seconds=dt.second, microseconds=dt.microsecond)

def geocode_address_api(adresa, api_key):
    if not adresa or pd.isna(adresa) or not str(adresa).strip(): return None, None
    url = f"https://api.mapy.cz/v1/geocode?query={quote(adresa)}&limit=1&apikey={api_key}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        if "items" in data and len(data["items"]) > 0:
            pos = data["items"][0]["position"]
            time.sleep(0.1) 
            return float(pos["lat"]), float(pos["lon"])
    except: pass
    time.sleep(0.1)
    return None, None

def get_driving_data(lat1, lon1, lat2, lon2, api_key):
    url = f"https://api.mapy.cz/v1/routing/route?start={lon1},{lat1}&end={lon2},{lat2}&routeType=car_fast&apikey={api_key}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        if "length" in data and "duration" in data:
            return float(data["length"] / 1000.0), float(data["duration"] / 60.0)
    except: pass 
    fallback_dist = geodesic((lat1, lon1), (lat2, lon2)).kilometers * 1.3
    return float(fallback_dist), float((fallback_dist / 50.0) * 60)

def parse_cod(val):
    try: return float(str(val).replace(' ', '').replace('Kč', '').replace(',', '.'))
    except: return 0.0

def optimize_route_2opt(route_nodes, dist_matrix):
    route_indices = list(range(len(route_nodes)))
    improvement = True
    while improvement:
        improvement = False
        for i in range(1, len(route_indices) - 2):
            for j in range(i + 1, len(route_indices) - 1):
                n_i_m1, n_i = route_nodes[route_indices[i-1]], route_nodes[route_indices[i]]
                n_j, n_j_p1 = route_nodes[route_indices[j]], route_nodes[route_indices[j+1]]
                
                current_dist = dist_matrix[n_i_m1][n_i] + dist_matrix[n_j][n_j_p1]
                new_dist = dist_matrix[n_i_m1][n_j] + dist_matrix[n_i][n_j_p1]
                
                if new_dist < current_dist - 0.0001: 
                    route_indices[i:j+1] = list(reversed(route_indices[i:j+1]))
                    improvement = True
    return [route_nodes[i] for i in route_indices]

def calc_route_metrics(route_nodes, dist_matrix):
    dist = sum(dist_matrix[route_nodes[i]][route_nodes[i+1]] for i in range(len(route_nodes)-1))
    time_min = (dist / 50.0) * 60
    return dist, time_min

# --- NAČÍTÁNÍ DAT Z E-SHOPŮ ---
SHOP1_URL = "https://www.max-i.cz/export/orders.xls?patternId=139&partnerId=13&hash=79a9b4e46276c62e22d481c04662fe1dd9300b5d7133595d95c293f92e65e498"
SHOP2_URL = "https://www.vomaks.cz/export/orders.xls?patternId=113&partnerId=8&hash=21597d7e45b81f24edaee6acb923968470a428c8bd78aafc1b63c33eeb3d5b3f"
SHOP3_URL = "https://www.slevadoma.cz/export/orders.xls?patternId=16&partnerId=6&hash=a2dfdd4edfc9711e993f72ed428c8499530d324952cd6c82bde49171df279e2f"

@st.cache_data(show_spinner=False, ttl=300) 
def fetch_data_from_url(url):
    df = None
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status() 
        content = response.content
    except Exception as e:
        raise ValueError(f"Chyba sítě: {e}")

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
    if df is None:
        raise ValueError("Nepodařilo se rozpoznat formát souboru.")
    return df.loc[:, ~df.columns.duplicated()]

@st.cache_data(show_spinner=False, ttl=300)
def prepare_shop_data(url, prefix, eshop_name, exclude_wholesale=False):
    df_raw = fetch_data_from_url(url)
    
    prod_col, amount_col = None, None
    for col in ['itemName', 'Název položky', 'productName', 'name', 'Název produktu', 'title', 'Položka', 'Produkt', 'Zboží']:
        if col in df_raw.columns: prod_col = col; break
    for col in ['itemAmount', 'amount', 'Množství', 'množství', 'count', 'itemCount', 'Počet', 'ks', 'Ks']:
        if col in df_raw.columns: amount_col = col; break

    p_dict = {}
    if 'code' in df_raw.columns and prod_col:
        for code, group in df_raw.groupby('code'):
            prods = []
            for _, r in group.iterrows():
                p_name = str(r[prod_col])
                if p_name and p_name.lower() not in ['nan', 'none']:
                    if amount_col and pd.notna(r[amount_col]):
                        try:
                            amt = float(r[amount_col])
                            if amt.is_integer(): amt = int(amt)
                        except: amt = r[amount_col]
                        prods.append(f"{amt}x {p_name}")
                    else: prods.append(p_name)
            p_dict[prefix + str(code)] = "<br>- " + "<br>- ".join(prods) if prods else "<br><i>Neznámé produkty</i>"

    if 'code' in df_raw.columns:
        df = df_raw.drop_duplicates(subset=['code']).copy()
        df.rename(columns={'code': 'id'}, inplace=True)
    else:
        df = df_raw.copy()
        if 'id' not in df.columns: df['id'] = [f"Neznamé-{i}" for i in range(len(df))]

    df['id'] = prefix + df['id'].astype(str)
    df['eshop'] = eshop_name

    if exclude_wholesale:
        if 'customerGroupName' in df.columns:
            mask_vw = df['customerGroupName'].astype(str).str.lower().str.contains('velkoodběratel|velkoobchod', na=False, regex=True)
            df = df[~mask_vw].copy()
        else:
            mask_vw = pd.Series([False] * len(df), index=df.index)
            for col in df.columns:
                if df[col].dtype == object and any(x in str(col).lower() for x in ['group', 'skupin', 'customergroupname']):
                    mask_vw = mask_vw | df[col].astype(str).str.lower().str.contains('velkoodběratel|velkoobchod', na=False, regex=True)
            df = df[~mask_vw].copy()

    return df, p_dict

with st.spinner("Stahuji a zpracovávám data ze všech e-shopů..."):
    try: df_maxi, dict_maxi = prepare_shop_data(SHOP1_URL, "MAX-", "Max-i.cz", exclude_wholesale=False)
    except Exception as e: st.error(f"⚠️ Nelze načíst Max-i.cz: {e}"); df_maxi, dict_maxi = pd.DataFrame(), {}
    
    try: df_vomaks, dict_vomaks = prepare_shop_data(SHOP2_URL, "VOM-", "Vomaks.cz", exclude_wholesale=True)
    except Exception as e: st.error(f"⚠️ Nelze načíst Vomaks.cz: {e}"); df_vomaks, dict_vomaks = pd.DataFrame(), {}

    try: df_sleva, dict_sleva = prepare_shop_data(SHOP3_URL, "SLE-", "Slevadoma.cz", exclude_wholesale=False)
    except Exception as e: st.error(f"⚠️ Nelze načíst Slevadoma.cz: {e}"); df_sleva, dict_sleva = pd.DataFrame(), {}

    if df_maxi.empty and df_vomaks.empty and df_sleva.empty: st.stop()
    
    df_shop = pd.concat([df_maxi, df_vomaks, df_sleva], ignore_index=True)
    products_dict = {**dict_maxi, **dict_vomaks, **dict_sleva}

# --- KROK 1: VÝBĚR STATUSŮ PER E-SHOP ---
st.subheader("Krok 1: Výběr objednávek z e-shopů")
col_sh1, col_sh2, col_sh3 = st.columns(3)

if 'maxi_st_saved' not in st.session_state: st.session_state['maxi_st_saved'] = []
if 'vomaks_st_saved' not in st.session_state: st.session_state['vomaks_st_saved'] = []
if 'sleva_st_saved' not in st.session_state: st.session_state['sleva_st_saved'] = []

def update_maxi(): st.session_state['maxi_st_saved'] = st.session_state['maxi_st']
def update_vomaks(): st.session_state['vomaks_st_saved'] = st.session_state['vomaks_st']
def update_sleva(): st.session_state['sleva_st_saved'] = st.session_state['sleva_st']

selected_maxi, selected_vomaks, selected_sleva = [], [], []
with col_sh1:
    st.markdown("### 🛒 Max-i.cz")
    if not df_maxi.empty and 'statusName' in df_maxi.columns:
        statuses1 = sorted(df_maxi['statusName'].dropna().unique().tolist())
        default_maxi = [s for s in st.session_state['maxi_st_saved'] if s in statuses1]
        selected_maxi = st.multiselect("Zobrazit na mapě (Max-i):", options=statuses1, default=default_maxi, key='maxi_st', on_change=update_maxi)
    else: st.info("Žádná data pro výběr.")

with col_sh2:
    st.markdown("### 🛒 Vomaks.cz")
    if not df_vomaks.empty and 'statusName' in df_vomaks.columns:
        statuses2 = sorted(df_vomaks['statusName'].dropna().unique().tolist())
        default_vomaks = [s for s in st.session_state['vomaks_st_saved'] if s in statuses2]
        selected_vomaks = st.multiselect("Zobrazit na mapě (Vomaks):", options=statuses2, default=default_vomaks, key='vomaks_st', on_change=update_vomaks)
        st.caption("*(Skryto: velkoobchod)*")
    else: st.info("Žádná data pro výběr.")

with col_sh3:
    st.markdown("### 🛒 Slevadoma.cz")
    if not df_sleva.empty and 'statusName' in df_sleva.columns:
        statuses3 = sorted(df_sleva['statusName'].dropna().unique().tolist())
        default_sleva = [s for s in st.session_state['sleva_st_saved'] if s in statuses3]
        selected_sleva = st.multiselect("Zobrazit na mapě (Slevadoma):", options=statuses3, default=default_sleva, key='sleva_st', on_change=update_sleva)
    else: st.info("Žádná data pro výběr.")

mask_maxi = (df_shop['eshop'] == 'Max-i.cz') & df_shop['statusName'].isin(selected_maxi)
mask_vomaks = (df_shop['eshop'] == 'Vomaks.cz') & df_shop['statusName'].isin(selected_vomaks)
mask_sleva = (df_shop['eshop'] == 'Slevadoma.cz') & df_shop['statusName'].isin(selected_sleva)
mask_status = mask_maxi | mask_vomaks | mask_sleva

mask_selected = df_shop['id'].isin(st.session_state['selected_orders'])
mask_saved = df_shop['id'].isin(saved_routes_ids)

df_to_process = df_shop[mask_selected | (mask_status & ~mask_saved)].copy()

# --- ZPRACOVÁNÍ A GEOKÓDOVÁNÍ (Z MEZIPAMĚTI) ---
orders = []
if not df_to_process.empty:
    with st.spinner("Připravuji mapu a souřadnice..."):
        new_geo_added = False
        for idx, row in df_to_process.iterrows():
            order_id = row['id']
            ulice = row.get('deliveryStreet', row.get('billStreet', ''))
            cp = row.get('deliveryHouseNumber', row.get('billHouseNumber', ''))
            mesto = row.get('deliveryCity', row.get('billCity', ''))
            psc = row.get('deliveryZip', row.get('billZip', ''))
            
            adresa_casti = [str(x) for x in [ulice, cp, mesto, psc] if pd.notna(x) and str(x) != 'nan']
            cela_adresa = " ".join(adresa_casti).strip()

            if cela_adresa in st.session_state['geo_cache']:
                lat, lon = st.session_state['geo_cache'][cela_adresa]
            else:
                lat, lon = geocode_address_api(cela_adresa, mapy_api_key)
                if lat is not None and lon is not None:
                    st.session_state['geo_cache'][cela_adresa] = [lat, lon]
                    new_geo_added = True

            jmeno = row.get('deliveryFullName')
            if pd.isna(jmeno) or str(jmeno).strip() in ['', 'nan', 'None']:
                jmeno = row.get('billFullName', 'Neznámý příjemce')

            prods_html = products_dict.get(order_id, "<br><i>Žádné produkty v exportu</i>")

            orders.append({
                'Číslo objednávky': order_id,
                'E-shop': row.get('eshop', ''),
                'Příjemce': str(jmeno),
                'Status': str(row.get('statusName', '')),
                'Celá_adresa': cela_adresa,
                'Ulice': f"{ulice} {cp}".strip(),
                'Město': mesto,
                'PSČ': psc,
                'Chyba': "(!)" if lat is None else "",
                'Telefon': str(row.get('phone', '')),
                'Dobírka (Kč)': str(row.get('geisDeliveryPriceToPay', row.get('priceToPay', '0'))),
                'Produkty': prods_html,
                'lat': lat,
                'lon': lon
            })
            
        if new_geo_added:
            save_geo_cache(st.session_state['geo_cache'])

if orders:
    df_orders = pd.DataFrame(orders)
else:
    df_orders = pd.DataFrame(columns=['Číslo objednávky', 'E-shop', 'Příjemce', 'Status', 'Celá_adresa', 'Ulice', 'Město', 'PSČ', 'Chyba', 'Telefon', 'Dobírka (Kč)', 'Produkty', 'lat', 'lon'])
    if selected_maxi or selected_vomaks or selected_sleva:
        st.info("Vybrané stavy neobsahují žádné volné objednávky (možná už jsou v uložených rozvozech).")
    elif not st.session_state['selected_orders']:
        st.info("Zvolte stavy v checklistech nahoře pro zobrazení objednávek na mapě.")

# --- DASHBOARD A POČÍTADLA NAD MAPOU ---
st.markdown("---")
col_metric1, col_metric2, col_metric3 = st.columns([1, 1, 1.5])
pocet_placeholder = col_metric1.empty()
dobirka_placeholder = col_metric2.empty()

# --- VYLEPŠENÝ MAGICKÝ NÁVRH ---
with col_metric3:
    if st.button("🤖 Magický návrh rozvozu (Auto-výběr z volných bodů na mapě)", use_container_width=True, type="primary"):
        if len(df_orders) < auto_min_orders:
            st.error(f"Na mapě je pouze {len(df_orders)} volných objednávek. Nastavený limit je minimálně {auto_min_orders}.")
        else:
            with st.spinner("Provádím hloubkovou analýzu. Zkouším všechny možnosti..."):
                s_lat, s_lon = geocode_address_api(start_address, mapy_api_key)
                e_lat, e_lon = geocode_address_api(end_address, mapy_api_key)
                
                auto_max_time_min_val = auto_max_time_h * 60
                
                if s_lat and s_lon and e_lat and e_lon:
                    points_dict = {'START': {'lat': s_lat, 'lon': s_lon}, 'END': {'lat': e_lat, 'lon': e_lon}}
                    available_orders = []
                    
                    for _, r in df_orders.iterrows():
                        o_id = r['Číslo objednávky']
                        if o_id not in st.session_state['selected_orders']:
                            points_dict[o_id] = {'lat': r['lat'], 'lon': r['lon']}
                            available_orders.append(o_id)
                            
                    dist_matrix = {}
                    for p1_id, p1_coords in points_dict.items():
                        dist_matrix[p1_id] = {}
                        for p2_id, p2_coords in points_dict.items():
                            if p1_id == p2_id: 
                                dist_matrix[p1_id][p2_id] = 0.0
                            else:
                                dist_matrix[p1_id][p2_id] = geodesic((p1_coords['lat'], p1_coords['lon']), (p2_coords['lat'], p2_coords['lon'])).kilometers * 1.3

                    best_route_ids = []
                    
                    for first_stop in available_orders:
                        unvisited = set(available_orders)
                        unvisited.remove(first_stop)
                        route_nodes = ['START', first_stop]
                        
                        while unvisited:
                            curr_id = route_nodes[-1]
                            
                            # OPRAVA: Seřadíme všechny volné kandidáty podle vzdálenosti, zkusíme prvního, když ne, zkusíme druhého...
                            candidates_sorted = sorted(list(unvisited), key=lambda x: dist_matrix[curr_id][x])
                            added_candidate = False
                            
                            for candidate in candidates_sorted:
                                test_route = route_nodes + [candidate, 'END']
                                raw_dist, raw_time = calc_route_metrics(test_route, dist_matrix)
                                
                                if raw_dist <= auto_max_km and raw_time <= auto_max_time_min_val:
                                    route_nodes.append(candidate)
                                    unvisited.remove(candidate)
                                    added_candidate = True
                                    break
                                else:
                                    opt_test_route = optimize_route_2opt(test_route, dist_matrix)
                                    opt_dist, opt_time = calc_route_metrics(opt_test_route, dist_matrix)
                                    
                                    if opt_dist <= auto_max_km and opt_time <= auto_max_time_min_val:
                                        route_nodes = opt_test_route[:-1] 
                                        unvisited.remove(candidate)
                                        added_candidate = True
                                        break
                                        
                            if not added_candidate:
                                break # Žádný ze zbývajících bodů už se do mantinelu nevejde
                                    
                        final_ids = [n for n in route_nodes if n != 'START']
                        if len(final_ids) > len(best_route_ids):
                            best_route_ids = final_ids

                    if len(best_route_ids) >= auto_min_orders:
                        st.session_state['selected_orders'].extend(best_route_ids)
                        st.success(f"Geniální! Systém vyzkoušel všechny možnosti a dokázal naložit {len(best_route_ids)} objednávek.")
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error(f"I přes tvrdou optimalizaci se do limitu nevešlo víc než {len(best_route_ids)} zastávek. Zkuste přidat kilometry nebo hodiny.")
                else:
                    st.error("Nemohu najít souřadnice skladu (startu/cíle).")

st.markdown("---")

# --- 2. VYKRESLENÍ MAPY ---
st.info("💡 **Návod:** Najetím myši na špendlík uvidíte detaily. Kliknutím ho přidáte do trasy.")
st.markdown("**Legenda:** 🔴 Na dobírku | 🔵 Zaplaceno (0 Kč) | 🟢 Vybráno do trasy | **Značky v pinu:** **M** (Max-i), **V** (Vomaks), **S** (Slevadoma) nebo **číslice** (pořadí v trase)")

mapa_cr = folium.Map(
    location=st.session_state['map_center'], 
    zoom_start=st.session_state['map_zoom'], 
    tiles=f"https://api.mapy.cz/v1/maptiles/basic/256/{{z}}/{{x}}/{{y}}?apikey={mapy_api_key}", 
    attr="Mapy.cz"
)

if not df_orders.empty:
    for idx, row in df_orders.dropna(subset=['lat', 'lon']).iterrows():
        order_id = row['Číslo objednávky']
        is_selected = order_id in st.session_state['selected_orders']
        cod_val = parse_cod(row['Dobírka (Kč)'])
        eshop_name = row.get('E-shop', '')
        
        if eshop_name == 'Max-i.cz': marker_text = 'M'
        elif eshop_name == 'Vomaks.cz': marker_text = 'V'
        elif eshop_name == 'Slevadoma.cz': marker_text = 'S'
        else: marker_text = '?'
        
        if is_selected:
            poradi = st.session_state['selected_orders'].index(order_id) + 1
            oznaceni = f"<b>{poradi}. zastávka</b><br>"
            
            ikona = BeautifyIcon(
                icon_shape='marker',
                text_color='white',
                background_color='#2ecc71',
                border_color='#27ae60',
                inner_iconStyle='margin-top:2px; font-weight:bold; font-size:14px; font-family:sans-serif;',
                number=str(poradi) 
            )
        else:
            oznaceni = ""
            if cod_val > 0:
                bg_col = '#e74c3c' 
                bd_col = '#c0392b'
            else:
                bg_col = '#3498db' 
                bd_col = '#2980b9'
                
            ikona = BeautifyIcon(
                icon_shape='marker',
                text_color='white',
                background_color=bg_col,
                border_color=bd_col,
                inner_iconStyle='margin-top:2px; font-weight:bold; font-size:14px; font-family:sans-serif;',
                number=marker_text 
            )
            
        vzhled_bubliny = f"<span style='display:none;'>[ID:{order_id}]</span>" \
                         f"<div style='min-width: 250px; font-family: sans-serif; font-size: 13px;'>" \
                         f"{oznaceni}<b>{order_id}</b> ({row['E-shop']})<br>{row['Příjemce']}<br><i>Stav: {row['Status']}</i><br>" \
                         f"<b>Dobírka: {row['Dobírka (Kč)']} Kč</b><br>{row['Celá_adresa']}<hr style='margin: 5px 0;'>" \
                         f"<b>Produkty:</b>{row['Produkty']}</div>"

        folium.Marker(
            location=[row['lat'], row['lon']],
            tooltip=folium.Tooltip(vzhled_bubliny),
            icon=ikona
        ).add_to(mapa_cr)
    
map_data = st_folium(
    mapa_cr, 
    height=600, 
    use_container_width=True, 
    returned_objects=["last_object_clicked_tooltip"]
)

if map_data and map_data.get("last_object_clicked_tooltip"):
    clicked_tooltip = map_data["last_object_clicked_tooltip"]
    
    match = re.search(r"\[ID:(.*?)\]", clicked_tooltip)
    if match:
        clicked_id = match.group(1).strip()
        
        if clicked_tooltip != st.session_state['last_clicked_tooltip']:
            st.session_state['last_clicked_tooltip'] = clicked_tooltip
            
            if clicked_id in st.session_state['selected_orders']:
                st.session_state['selected_orders'].remove(clicked_id)
                
                routes_modified = False
                for r in saved_routes:
                    if clicked_id in r.get('orders', []):
                        r['orders'].remove(clicked_id)
                        routes_modified = True
                
                if routes_modified:
                    saved_routes = [r for r in saved_routes if len(r.get('orders', [])) > 0]
                    save_routes(saved_routes)
                
            else:
                st.session_state['selected_orders'].append(clicked_id)
            st.rerun()

# --- 3. SESTAVENÍ VYBRANÝCH ZASTÁVEK A DOPLNĚNÍ POČÍTADEL ---
if st.session_state['selected_orders'] and not df_orders.empty:
    platne_ids = [o_id for o_id in st.session_state['selected_orders'] if o_id in df_orders['Číslo objednávky'].values]
    if platne_ids:
        df_selected = df_orders.set_index('Číslo objednávky').loc[platne_ids].reset_index()
    else:
        df_selected = pd.DataFrame()
else:
    df_selected = pd.DataFrame()

if not df_selected.empty:
    celkova_vybrana_dobirka = sum(parse_cod(x) for x in df_selected['Dobírka (Kč)'])
    pocet_placeholder.metric(label="📦 Počet objednávek v trase", value=f"{len(df_selected)}")
    dobirka_placeholder.metric(label="💰 Vybrané dobírky do trasy", value=f"{int(celkova_vybrana_dobirka)} Kč")
else:
    pocet_placeholder.metric(label="📦 Počet objednávek v trase", value="0")
    dobirka_placeholder.metric(label="💰 Vybrané dobírky do trasy", value="0 Kč")

if not df_selected.empty:
    st.markdown("---")
    st.subheader("Krok 2: Seřazení trasy a poznámky")
    
    tab_sort, tab_notes = st.tabs(["🗺️ Seřadit trasu (Myší)", "📝 Dopsat poznámky a adresy"])
    
    with tab_sort:
        st.info("Trasa je seřazena podle toho, jak jste klikali do mapy. Pokud chcete pořadí změnit, chyťte řádek myší.")
        
        if st.button("🪄 Automaticky optimalizovat pořadí (Nejkratší trasa od skladu do cíle)", use_container_width=True):
            with st.spinner("Počítám nejkratší logistickou smyčku pomocí algoritmu 2-opt..."):
                start_lat, start_lon = geocode_address_api(start_address, mapy_api_key)
                end_lat, end_lon = geocode_address_api(end_address, mapy_api_key)
                
                if start_lat is not None and start_lon is not None and end_lat is not None and end_lon is not None:
                    points = [{'id': 'START', 'lat': start_lat, 'lon': start_lon}]
                    for oid in st.session_state['selected_orders']:
                        row = df_orders[df_orders['Číslo objednávky'] == oid].iloc[0]
                        points.append({'id': oid, 'lat': row['lat'], 'lon': row['lon']})
                    points.append({'id': 'END', 'lat': end_lat, 'lon': end_lon})

                    dist_matrix = {}
                    for i in range(len(points)):
                        dist_matrix[points[i]['id']] = {}
                        for j in range(len(points)):
                            if i == j: dist_matrix[points[i]['id']][points[j]['id']] = 0.0
                            else: dist_matrix[points[i]['id']][points[j]['id']] = geodesic((points[i]['lat'], points[i]['lon']), (points[j]['lat'], points[j]['lon'])).kilometers

                    route_nodes = [p['id'] for p in points]
                    optimized_route_nodes = optimize_route_2opt(route_nodes, dist_matrix)
                    
                    optimized_order = [n for n in optimized_route_nodes if n not in ['START', 'END']]
                    st.session_state['selected_orders'] = optimized_order
                    st.rerun()
                else:
                    st.error("Nepodařilo se zjistit souřadnice skladu pro start nebo cíl.")
        
        items_list = []
        mapping_dict = {}
        for _, row in df_selected.iterrows():
            item_str = f"{row['Číslo objednávky']} | {row['Příjemce']} | {row['Chyba']} {row['Celá_adresa']} | {row['Dobírka (Kč)']} Kč"
            items_list.append(item_str)
            mapping_dict[item_str] = row.to_dict()
            
        sorted_strings = sort_items(items_list, direction='vertical') or items_list

    with tab_notes:
        st.info("Zde můžete k seřazeným objednávkám dopsat vzkaz řidiči a ručně upravit adresu pro tisk (opravit překlep atd.).")
        order_notes = {}
        order_addresses = {}
        
        for s in sorted_strings:
            order_data = mapping_dict[s]
            order_id = order_data['Číslo objednávky']
            
            st.markdown(f"**{order_id} ({order_data['E-shop']}) | 👤 {order_data['Příjemce']}**")
            col_note, col_addr = st.columns(2)
            with col_note:
                default_note = st.session_state.get(f"note_{order_id}", "")
                order_notes[order_id] = st.text_input("Poznámka (vzkaz):", value=default_note, key=f"note_input_{order_id}")
            with col_addr:
                original_full_address = f"{order_data['Ulice']}, {order_data['Město']} {order_data['PSČ']}".strip(', ')
                default_addr = st.session_state.get(f"addr_{order_id}", original_full_address)
                order_addresses[order_id] = st.text_input("Upravená adresa pro tisk:", value=default_addr, key=f"addr_input_{order_id}")
            st.write("") 

    st.markdown("---")
    
    # --- 4. VÝPOČET A TISK PDF ---
    if st.button("🚀 Vypočítat časy a generovat PDF", type="primary"):
        final_rows = [mapping_dict[s] for s in sorted_strings]
        final_df = pd.DataFrame(final_rows)
        final_df['Poznámka'] = final_df['Číslo objednávky'].map(order_notes)
        final_df['Tisk_Adresa'] = final_df['Číslo objednávky'].map(order_addresses)
        
        with st.spinner("Geokóduji zadané adresy startu a cíle..."):
            start_lat, start_lon = geocode_address_api(start_address, mapy_api_key)
            end_lat, end_lon = geocode_address_api(end_address, mapy_api_key)
            if start_lat is None or end_lat is None:
                st.error("Nelze nalézt ručně zadanou adresu startu nebo cíle.")
                st.stop()
        
        itinerary = []
        itinerary.append({
            'Číslo objednávky': 'START', 'Příjemce': start_point_name, 
            'Tisk_Adresa': start_address, 'Město': '', 'PSČ': '', 'Chyba': '', 
            'Telefon': '', 'Dobírka (Kč)': 0, 'Poznámka': '', 'lat': start_lat, 'lon': start_lon, 'E-shop': ''
        })
        for _, row in final_df.iterrows(): 
            itinerary.append(row.to_dict())
        itinerary.append({
            'Číslo objednávky': 'CÍL', 'Příjemce': end_point_name, 
            'Tisk_Adresa': end_address, 'Město': '', 'PSČ': '', 'Chyba': '', 
            'Telefon': '', 'Dobírka (Kč)': 0, 'Poznámka': '', 'lat': end_lat, 'lon': end_lon, 'E-shop': ''
        })
        
        df_itinerary = pd.DataFrame(itinerary)
        segments_data = []
        
        with st.spinner("Počítám časy přejezdů přes Mapy.cz..."):
            for i in range(len(df_itinerary) - 1):
                res_drive = get_driving_data(df_itinerary.loc[i, 'lat'], df_itinerary.loc[i, 'lon'], df_itinerary.loc[i+1, 'lat'], df_itinerary.loc[i+1, 'lon'], mapy_api_key)
                segments_data.append(res_drive)
        
        current_dt = datetime.combine(datetime.today(), start_time)
        arrival_times, arrival_windows, distances_to_next, times_to_next = [current_dt.strftime('%H:%M')], ['-'], [], []
        
        for i in range(len(df_itinerary) - 1):
            dist, dur = segments_data[i]
            distances_to_next.append(round(dist, 1))
            times_to_next.append(int(dur))
            arrival_dt = current_dt + timedelta(minutes=int(dur))
            
            if i + 1 == len(df_itinerary) - 1:
                arrival_times.append(arrival_dt.strftime('%H:%M'))
                arrival_windows.append('-')
            else:
                arrival_times.append(arrival_dt.strftime('%H:%M'))
                win_start = round_up_to_15_minutes(arrival_dt)
                arrival_windows.append(f"{win_start.strftime('%H:%M')} - {(win_start + timedelta(hours=2)).strftime('%H:%M')}")
                current_dt = arrival_dt + timedelta(minutes=unload_time_min)
                
        distances_to_next.append(0.0)
        times_to_next.append(0)

        df_itinerary['Čas příjezdu'] = arrival_times
        df_itinerary['Okno příjezdu (2h)'] = arrival_windows
        df_itinerary['Vzdálen k další (km)'] = distances_to_next
        df_itinerary['Čas k další (min)'] = times_to_next
        
        st.success("Výpočet dokončen!")
        
        df_web_display = df_itinerary.copy().astype(str)
        for bad_val in ['none', 'nan', '<na>', 'none.', 'nan.']:
            df_web_display.replace(bad_val, "", inplace=True)
            df_web_display.replace(bad_val.upper(), "", inplace=True)
            df_web_display.replace(bad_val.capitalize(), "", inplace=True)
        
        df_final_display = df_web_display[[
            'Číslo objednávky', 'E-shop', 'Příjemce', 'Tisk_Adresa', 
            'Telefon', 'Dobírka (Kč)', 'Čas příjezdu', 'Okno příjezdu (2h)',
            'Vzdálen k další (km)', 'Čas k další (min)', 'Poznámka'
        ]]
        st.dataframe(df_final_display, use_container_width=True)

        # --- REÁLNÁ MAPA Z MAPY.CZ DO PDF ---
        def generate_map_image(itinerary_df):
            lats = itinerary_df['lat'].tolist()
            lons = itinerary_df['lon'].tolist()

            if not lats: return None

            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)

            pad_lat = max(0.02, (max_lat - min_lat) * 0.15)
            pad_lon = max(0.02, (max_lon - min_lon) * 0.15)
            min_lat -= pad_lat
            max_lat += pad_lat
            min_lon -= pad_lon
            max_lon += pad_lon

            def latlon_to_xy(lat, lon, z):
                lat_rad = math.radians(lat)
                n = 2.0 ** z
                x = ((lon + 180.0) / 360.0 * n)
                y = ((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
                return x, y

            zoom = 12
            for z in range(14, 4, -1):
                x0, y0 = latlon_to_xy(max_lat, min_lon, z) 
                x1, y1 = latlon_to_xy(min_lat, max_lon, z) 
                if (x1 - x0) <= 5 and (y1 - y0) <= 5:
                    zoom = z
                    break

            x0, y0 = latlon_to_xy(max_lat, min_lon, zoom)
            x1, y1 = latlon_to_xy(min_lat, max_lon, zoom)

            tile_x0, tile_y0 = int(x0), int(y0)
            tile_x1, tile_y1 = int(x1), int(y1)

            width_tiles = tile_x1 - tile_x0 + 1
            height_tiles = tile_y1 - tile_y0 + 1

            map_img = Image.new('RGB', (width_tiles * 256, height_tiles * 256), color='#eef2f3')

            for tx in range(tile_x0, tile_x1 + 1):
                for ty in range(tile_y0, tile_y1 + 1):
                    url = f"https://api.mapy.cz/v1/maptiles/basic/256/{zoom}/{tx}/{ty}?apikey={mapy_api_key}"
                    try:
                        r = requests.get(url, timeout=3)
                        if r.status_code == 200:
                            tile = Image.open(io.BytesIO(r.content)).convert('RGB')
                            map_img.paste(tile, ((tx - tile_x0) * 256, (ty - tile_y0) * 256))
                    except:
                        pass

            fig, ax = plt.subplots(figsize=(10, 8), dpi=150)
            
            def coord_to_px(lat, lon):
                x, y = latlon_to_xy(lat, lon, zoom)
                return (x - tile_x0) * 256, (y - tile_y0) * 256

            ax.imshow(map_img)

            pxs, pys = [], []
            for lat, lon in zip(lats, lons):
                px, py = coord_to_px(lat, lon)
                pxs.append(px)
                pys.append(py)

            ax.plot(pxs, pys, color='#2980b9', linewidth=4.5, label="Trasa", zorder=2)
            ax.scatter(pxs, pys, color='#e74c3c', s=140, zorder=5, edgecolors='white', linewidths=2)

            for i, row in itinerary_df.iterrows():
                px, py = coord_to_px(row['lat'], row['lon'])
                label = "S" if i == 0 else ("C" if i == len(itinerary_df)-1 else str(i))
                ax.annotate(label, (px, py), textcoords="offset points", xytext=(0,10),
                            ha='center', fontsize=11, fontweight='bold', color='black',
                            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#7f8c8d", alpha=0.9),
                            zorder=6)

            px_min_x, px_min_y = coord_to_px(max_lat, min_lon)
            px_max_x, px_max_y = coord_to_px(min_lat, max_lon)
            ax.set_xlim(px_min_x, px_max_x)
            ax.set_ylim(px_max_y, px_min_y)

            ax.axis('off')
            plt.tight_layout(pad=0)

            img_buf = io.BytesIO()
            plt.savefig(img_buf, format='png', bbox_inches='tight', pad_inches=0.1)
            img_buf.seek(0)
            plt.close(fig)
            return img_buf

        # --- GENERÁTOR PDF PŘES FPDF ---
        total_km = round(df_itinerary['Vzdálen k další (km)'].sum(), 1)
        pure_drive_min = int(df_itinerary['Čas k další (min)'].sum())
        total_hours = f"{pure_drive_min // 60}h {pure_drive_min % 60}min"
        
        def parse_cod(val):
            try: return float(str(val).replace(' ', '').replace('Kč', '').replace(',', '.'))
            except: return 0.0
        total_cod = sum(parse_cod(x) for x in df_itinerary['Dobírka (Kč)'])

        use_custom_font = False
        font_family_name = "Helvetica"
        local_font_reg = ""
        local_font_bold = ""
        
        paths_to_try = [
            ("arial.ttf", "arialbd.ttf"),
            ("ARIAL.TTF", "ARIALBD.TTF"),
            ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf")
        ]

        for r_path, b_path in paths_to_try:
            if os.path.exists(r_path) and os.path.exists(b_path):
                local_font_reg, local_font_bold = r_path, b_path
                font_family_name = "ArialCustom"
                use_custom_font = True
                break

        class DriverPDF(FPDF):
            def header(self):
                self.set_font(font_family_name, "B", 14)
                heading_text = "TRASOVÝ SOUPIS ŘIDIČE (A4)" if use_custom_font else "TRASOVY SOUPIS RIDICE (A4)"
                self.cell(0, 10, heading_text, ln=True, align="C")
                
                self.set_font(font_family_name, "", 9)
                self.set_text_color(100, 100, 100)
                self.cell(0, 5, f"Vygenerovano: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Start: {start_time.strftime('%H:%M')}", ln=True, align="C")
                self.ln(3)
                self.line(10, self.get_y(), 200, self.get_y())
                self.ln(6)

        pdf = DriverPDF(orientation="P", unit="mm", format="A4")
        if use_custom_font:
            pdf.add_font("ArialCustom", "", local_font_reg, uni=True)
            pdf.add_font("ArialCustom", "B", local_font_bold, uni=True)
            
        pdf.add_page()
        pdf.set_font(font_family_name, "B", 12)
        pdf.set_text_color(44, 62, 80)
        pdf.cell(0, 8, "MAPA TRASY - PŘEHLED", ln=True, align="C")
        pdf.ln(5)
        
        df_for_map = df_itinerary.dropna(subset=['lat', 'lon']).reset_index(drop=True)
        if not df_for_map.empty:
            map_img = generate_map_image(df_for_map)
            if map_img:
                temp_img_path = "temp_map_context.png"
                with open(temp_img_path, "wb") as f:
                    f.write(map_img.getbuffer())
                
                pdf.image(temp_img_path, x=15, y=pdf.get_y(), w=180)
                if os.path.exists(temp_img_path): os.remove(temp_img_path)

        pdf.add_page()

        for idx, row in df_itinerary.iterrows():
            has_note = bool(str(row.get('Poznámka', '')).strip()) and str(row.get('Poznámka', '')).lower() != 'none'
            is_start = row['Číslo objednávky'] == 'START'
            is_end = row['Číslo objednávky'] == 'CÍL'
            
            if is_start or is_end: addr = str(row['Tisk_Adresa'])
            else:
                err_prefix = f"({row['Chyba']}) " if row['Chyba'] else ""
                addr = f"{err_prefix}{row['Tisk_Adresa']}"
            
            if not use_custom_font:
                import unicodedata
                addr = ''.join(c for c in unicodedata.normalize('NFD', addr) if unicodedata.category(c) != 'Mn')
                prijemce_clean = ''.join(c for c in unicodedata.normalize('NFD', str(row['Příjemce'])) if unicodedata.category(c) != 'Mn')
                note_clean = ''.join(c for c in unicodedata.normalize('NFD', str(row.get('Poznámka', ''))) if unicodedata.category(c) != 'Mn')
            else:
                prijemce_clean = str(row['Příjemce'])
                note_clean = str(row.get('Poznámka', ''))

            pdf.set_font(font_family_name, "", 9.5)
            words = f"Adresa: {addr}".split(' ')
            lines_count = 1
            current_line_width = 0
            for word in words:
                word_w = pdf.get_string_width(word + " ")
                if current_line_width + word_w > 54:
                    lines_count += 1
                    current_line_width = word_w
                else: current_line_width += word_w
            
            content_height = (lines_count * 4.5) + 11
            if has_note: content_height += 8.5
            box_height = max(18, content_height)
                
            if (297 - pdf.get_y() - 20) < (box_height + 10):
                pdf.add_page()
            
            start_y = pdf.get_y()
            pdf.set_draw_color(180, 180, 180)
            pdf.set_fill_color(245, 246, 250) if (is_start or is_end) else pdf.set_fill_color(255, 255, 255)
            pdf.rect(10, start_y, 190, box_height, style="DF" if (is_start or is_end) else "D")
            
            pdf.set_y(start_y + 2)
            pdf.set_x(13)
            pdf.set_font(font_family_name, "B", 10.5)
            pdf.set_text_color(44, 62, 80)
            
            if is_start or is_end: 
                title = f"{prijemce_clean}"
            else:
                title = f"Zastávka č. {idx} - {prijemce_clean}" if use_custom_font else f"Zastavka c. {idx} - {prijemce_clean}"
                title += f"  [{row['Číslo objednávky']}]"
            pdf.cell(0, 5, title, ln=True)
            
            if has_note:
                pdf.ln(0.5)
                pdf.set_x(13)
                pdf.set_fill_color(255, 242, 204)
                pdf.set_draw_color(230, 126, 34)
                pdf.rect(13, pdf.get_y(), 184, 6, style="DF")
                pdf.set_x(15)
                pdf.set_font(font_family_name, "B", 9)
                pdf.set_text_color(211, 84, 0)
                pdf.cell(0, 6, f"POZOR VZKAZ: {note_clean}", ln=True) 
                pdf.ln(0.5)
            else: pdf.ln(0.5)

            current_y = pdf.get_y()

            pdf.set_y(current_y)
            pdf.set_x(13)
            pdf.set_font(font_family_name, "B", 7.5)
            pdf.set_text_color(120, 120, 120)
            pdf.cell(54, 3.5, "MÍSTO DORUČENÍ" if use_custom_font else "MISTO DORUCENI", ln=True)
            
            pdf.set_y(current_y + 3.5)
            pdf.set_x(13)
            pdf.set_font(font_family_name, "", 9)
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(54, 4.2, addr)
            
            pdf.set_y(current_y)
            pdf.set_x(70)
            pdf.set_font(font_family_name, "B", 7.5)
            pdf.set_text_color(120, 120, 120)
            pdf.cell(28, 3.5, "TELEFON", ln=True)
            
            pdf.set_y(current_y + 3.5)
            pdf.set_x(70)
            
            phone_raw = str(row['Telefon']).strip() if row['Telefon'] and str(row['Telefon']).lower() != 'none' else ""
            
            if not phone_raw:
                pdf.set_font(font_family_name, "", 9)
                pdf.set_text_color(30, 30, 30)
                pdf.cell(28, 4.5, "-", ln=True)
            else:
                prefix = ""
                main_num = phone_raw
                if phone_raw.startswith("+420") or phone_raw.startswith("+421"):
                    prefix = phone_raw[:4]
                    main_num = phone_raw[4:].strip()
                elif phone_raw.startswith("+"):
                    if " " in phone_raw:
                        prefix = phone_raw.split(" ")[0]
                        main_num = phone_raw.split(" ", 1)[1].strip()
                    else:
                        prefix = phone_raw[:4]
                        main_num = phone_raw[4:].strip()
                
                if prefix:
                    pdf.set_font(font_family_name, "", 8)
                    pdf.set_text_color(140, 140, 140)
                    w_pref = pdf.get_string_width(prefix + " ")
                    pdf.cell(w_pref, 4.5, prefix + " ", ln=False) 
                
                pdf.set_font(font_family_name, "B", 12)
                pdf.set_text_color(20, 20, 20)
                pdf.cell(28, 4.5, main_num, ln=True)
            
            pdf.set_y(current_y)
            pdf.set_x(101)
            pdf.set_font(font_family_name, "B", 7.5)
            pdf.set_text_color(120, 120, 120)
            pdf.cell(45, 3.5, "ČASOVÝ HARMONOGRAM" if use_custom_font else "CASOVY HARMONOGRAM", ln=True)
            
            pdf.set_y(current_y + 3.5)
            pdf.set_x(101)
            pdf.set_font(font_family_name, "", 9)
            pdf.set_text_color(30, 30, 30)
            if is_start or is_end:
                pdf.cell(45, 4.5, f"Čas: {row['Čas příjezdu']}" if use_custom_font else f"Cas: {row['Čas příjezdu']}", ln=True)
            else:
                pdf.cell(45, 4.5, f"Příjezd cca: {row['Čas příjezdu']}" if use_custom_font else f"Prijezd cca: {row['Čas příjezdu']}", ln=True)
                pdf.set_x(101)
                pdf.set_font(font_family_name, "B", 9)
                pdf.cell(45, 4.5, f"Okno: {row['Okno příjezdu (2h)']}", ln=True)
                
            pdf.set_y(current_y)
            pdf.set_x(148)
            pdf.set_font(font_family_name, "B", 7.5)
            pdf.set_text_color(120, 120, 120)
            pdf.cell(22, 3.5, "K VYBRÁNÍ" if use_custom_font else "K VYBRANI", ln=True)
            
            pdf.set_y(current_y + 3.5)
            pdf.set_x(148)
            cod_val = parse_cod(row['Dobírka (Kč)'])
            if is_start or is_end:
                pdf.cell(22, 4.5, "-", ln=True)
            elif cod_val == 0:
                pdf.set_font(font_family_name, "B", 9.5)
                pdf.set_text_color(46, 204, 113) 
                pdf.cell(22, 4.5, "PLACENO", ln=True)
            else:
                pdf.set_font(font_family_name, "B", 9.5)
                pdf.set_text_color(231, 76, 60) 
                pdf.cell(22, 4.5, f"{int(cod_val)} Kč" if use_custom_font else f"{int(cod_val)} Kc", ln=True)
                
            if not (is_start or is_end):
                pdf.set_draw_color(100, 100, 100)
                pdf.set_line_width(0.4)
                pdf.rect(174, current_y + 0.5, 6, 6)
                pdf.set_line_width(0.2)
                pdf.set_draw_color(180, 180, 180)
                pdf.set_fill_color(248, 249, 250)
                pdf.rect(171, current_y + 8, 26, 6, style="DF")
                pdf.set_y(current_y + 9)
                pdf.set_x(172)
                pdf.set_font(font_family_name, "", 7.5)
                pdf.set_text_color(110, 110, 110)
                pdf.cell(26, 4, "Čas: __ : __" if use_custom_font else "Cas: __ : __", ln=True)
                
            pdf.set_y(start_y + box_height + 2)
            
            if idx < len(df_itinerary) - 1:
                pdf.ln(2)
                pdf.set_font(font_family_name, "B", 8)
                pdf.set_text_color(150, 150, 151)
                segment_text = f"      |      Přejezd na další zastávku: {row['Vzdálen k další (km)']} km ({row['Čas k další (min)']} min)" if use_custom_font else f"      |      Prejezd na dalsi zastavku: {row['Vzdálen k další (km)']} km ({row['Čas k další (min)']} min)"
                pdf.cell(0, 4, segment_text, ln=True)
                pdf.ln(2)
                
        pdf.ln(4)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_font(font_family_name, "B", 11)
        pdf.set_text_color(44, 62, 80)
        pdf.cell(0, 6, "CELKOVÝ SOUHRN TRASY" if use_custom_font else "CELKOVY SOUHRN TRASY", ln=True)
        
        pdf.set_font(font_family_name, "", 10)
        pdf.cell(65, 5, f"Celková vzdálenost: {total_km} km" if use_custom_font else f"Celkova vzdalenost: {total_km} km", ln=False)
        pdf.cell(65, 5, f"Čistý čas jízdy: {total_hours}" if use_custom_font else f"Cisty cas jizdy: {total_hours}", ln=True)
        
        pdf.ln(1)
        pdf.set_font(font_family_name, "B", 10)
        pdf.set_text_color(231, 76, 60)
        pdf.cell(65, 5, f"Vybrat dobírky celkem: {int(total_cod)} Kč" if use_custom_font else f"Vybrat dobirky celkem: {int(total_cod)} Kc", ln=False)
        
        pdf.set_text_color(44, 62, 80)
        pdf.cell(65, 5, f"Kasáč (při odjezdu): {int(kasac_value)} Kč" if use_custom_font else f"Kasac (pri odjezdu): {int(kasac_value)} Kc", ln=True)

        raw_pdf_string = pdf.output(dest='S')
        if isinstance(raw_pdf_string, str): pdf_bytes = raw_pdf_string.encode('latin1')
        else: pdf_bytes = bytes(raw_pdf_string)
        
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button("📥 Stáhnout PDF k tisku (A4)", data=pdf_bytes, file_name="trasovy_soupis_tisk.pdf", mime="application/pdf", type="primary")
        with col_dl2:
            buffer_xls = io.BytesIO()
            with pd.ExcelWriter(buffer_xls, engine='openpyxl') as writer:
                df_final_display.to_excel(writer, index=False, sheet_name='Trasový soupis')
            st.download_button("📥 Stáhnout XLSX tabulku", data=buffer_xls.getvalue(), file_name="hotovy_trasovy_soupis.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            
    # --- ZDE PŘIDÁVÁME ULOŽENÍ ROZVOZU DO HISTORIE ---
    st.markdown("---")
    st.subheader("💾 Uložit rozvoz a vyčistit mapu")
    st.info("Tímto přesunete aktuální objednávky do historie vlevo a zmizí z hlavní mapy. (Uloží se v tomto přesném seřazení).")
    
    col_s1, col_s2 = st.columns([3, 1])
    with col_s1:
        route_name = st.text_input("Název rozvozu (např. Datum a Jméno řidiče)", value=f"Rozvoz {datetime.now().strftime('%d.%m. %H:%M')}")
    with col_s2:
        st.write("") 
        st.write("")
        if st.button("Uložit do historie", type="primary", use_container_width=True):
            sorted_ids = [mapping_dict[s]['Číslo objednávky'] for s in sorted_strings]
            
            route_details = {}
            for o_id in sorted_ids:
                route_details[o_id] = {
                    "note": order_notes.get(o_id, ""),
                    "addr": order_addresses.get(o_id, "")
                }
                
            new_route = {
                "id": str(time.time()),
                "name": route_name,
                "orders": sorted_ids,
                "details": route_details
            }
            saved_routes.append(new_route)
            save_routes(saved_routes)
            
            st.session_state['selected_orders'] = []
            st.success("Rozvoz byl bezpečně uložen!")
            time.sleep(1.5)
            st.rerun()