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

# --- PAMĚŤ PRO ULOŽENÉ ROZVOZY A GEOKÓD ---
ROUTES_FILE = "saved_routes.json"
GEO_FILE = "geocode_cache.json"

try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
    GITHUB_REPO = st.secrets["GITHUB_REPO"]  
except:
    GITHUB_TOKEN = ""
    GITHUB_REPO = ""

def get_github_headers(): return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

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
        headers = get_github_headers(); resp = requests.get(url, headers=headers)
        sha = resp.json().get('sha') if resp.status_code == 200 else None
        content_b64 = base64.b64encode(json.dumps(data_obj, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
        payload = {"message": commit_message, "content": content_b64}
        if sha: payload["sha"] = sha
        requests.put(url, headers=headers, json=payload)
    else:
        with open(file_path, "w", encoding="utf-8") as f: json.dump(data_obj, f, ensure_ascii=False, indent=2)

def load_routes(): return load_json_from_github_or_local(ROUTES_FILE, list)
def save_routes(routes): save_json_to_github_or_local(ROUTES_FILE, routes[-20:], f"Rozvozy {datetime.now().strftime('%H:%M:%S')}")
def load_geo_cache(): return load_json_from_github_or_local(GEO_FILE, dict)
def save_geo_cache(cache): save_json_to_github_or_local(GEO_FILE, cache, f"GeoCache {datetime.now().strftime('%H:%M:%S')}")

saved_routes = load_routes()
saved_routes_ids = set()
for r in saved_routes: saved_routes_ids.update(r.get('orders', []))
if 'geo_cache' not in st.session_state: st.session_state['geo_cache'] = load_geo_cache()

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

st.sidebar.markdown("---")
st.sidebar.header("🪄 Limity a Směr (Magický návrh)")
st.sidebar.info("Nastavte mantinely a případný směr rozvozu. Algoritmus vybere body cestou.")
target_direction_city = st.sidebar.text_input("📍 Zacílit rozvoz (Město/Kraj - volitelné)", value="", help="Např. 'Plzeň'. Algoritmus vezme jen objednávky po cestě tam a zpět.")
target_tolerance = st.sidebar.slider("Šířka koridoru po cestě", 1.05, 3.0, 1.4, 0.05, help="1.05 = úzká cesta přímo k cíli. 2.0+ = vezme i velmi široké okolí.")
auto_min_orders = st.sidebar.number_input("Minimální počet objednávek", min_value=1, value=10, step=1)
auto_max_km = st.sidebar.number_input("Maximální trasa celkem (km)", min_value=10, value=700, step=50)
auto_max_time_h = st.sidebar.number_input("Maximální čas jízdy (hodiny)", min_value=1.0, value=9.5, step=0.5)

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Vynutit aktualizaci dat ze Shoptetu", type="secondary"): st.cache_data.clear(); st.rerun()

if 'selected_orders' not in st.session_state: st.session_state['selected_orders'] = []  
if 'last_clicked_tooltip' not in st.session_state: st.session_state['last_clicked_tooltip'] = None
if 'map_center' not in st.session_state: st.session_state['map_center'] = [49.8, 15.5]
if 'map_zoom' not in st.session_state: st.session_state['map_zoom'] = 7

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
    url = f"https://api.mapy.cz/v1/routing/route?start={lon1},{lat1}&end={lon2},{lat2}&routeType=car_fast&apikey={api_key}"
    try:
        r = requests.get(url, timeout=5); data = r.json()
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

# ==============================================================================
# --- CENTRÁLNÍ FUNKCE PRO VÝPOČET A GENEROVÁNÍ VŠECH PDF ---
# ==============================================================================
def create_route_pdfs(route_name, final_df, start_address, end_address, start_point_name, end_point_name, mapy_api_key, start_time, unload_time_min, slow_mode, kasac_value):
    with st.spinner("Geokóduji zadané adresy startu a cíle..."):
        start_lat, start_lon = geocode_address_api(start_address, mapy_api_key)
        end_lat, end_lon = geocode_address_api(end_address, mapy_api_key)
        if start_lat is None or end_lat is None: return None
    
    itinerary = []
    itinerary.append({'Číslo objednávky': 'START', 'Příjemce': start_point_name, 'Tisk_Adresa': start_address, 'Město': '', 'PSČ': '', 'Chyba': '', 'Telefon': '', 'Dobírka (Kč)': 0, 'Poznámka': '', 'Poznámka_Zakaznika_Shoptet': '', 'Poznámka_Eshopu_Shoptet': '', 'lat': start_lat, 'lon': start_lon, 'E-shop': '', 'Produkty': ''})
    for _, row in final_df.iterrows(): itinerary.append(row.to_dict())
    itinerary.append({'Číslo objednávky': 'CÍL', 'Příjemce': end_point_name, 'Tisk_Adresa': end_address, 'Město': '', 'PSČ': '', 'Chyba': '', 'Telefon': '', 'Dobírka (Kč)': 0, 'Poznámka': '', 'Poznámka_Zakaznika_Shoptet': '', 'Poznámka_Eshopu_Shoptet': '', 'lat': end_lat, 'lon': end_lon, 'E-shop': '', 'Produkty': ''})
    
    df_itinerary = pd.DataFrame(itinerary); segments_data = []
    with st.spinner("Počítám časy přejezdů přes Mapy.cz..."):
        for i in range(len(df_itinerary) - 1):
            res_drive = get_driving_data(df_itinerary.loc[i, 'lat'], df_itinerary.loc[i, 'lon'], df_itinerary.loc[i+1, 'lat'], df_itinerary.loc[i+1, 'lon'], mapy_api_key)
            segments_data.append(res_drive)
    
    current_dt = datetime.combine(datetime.today(), start_time)
    arrival_times, arrival_windows, distances_to_next, times_to_next = [current_dt.strftime('%H:%M')], ['-'], [], []
    
    for i in range(len(df_itinerary) - 1):
        dist, dur = segments_data[i]
        if slow_mode: dur = dur * 1.1
        distances_to_next.append(round(dist, 1)); times_to_next.append(int(dur)); arrival_dt = current_dt + timedelta(minutes=int(dur))
        if i + 1 == len(df_itinerary) - 1: arrival_times.append(arrival_dt.strftime('%H:%M')); arrival_windows.append('-')
        else:
            arrival_times.append(arrival_dt.strftime('%H:%M')); win_start = round_up_to_15_minutes(arrival_dt)
            arrival_windows.append(f"{win_start.strftime('%H:%M')} - {(win_start + timedelta(hours=2)).strftime('%H:%M')}")
            current_dt = arrival_dt + timedelta(minutes=unload_time_min)
            
    distances_to_next.append(0.0); times_to_next.append(0)
    df_itinerary['Čas příjezdu'] = arrival_times; df_itinerary['Okno příjezdu (2h)'] = arrival_windows
    df_itinerary['Vzdálen k další (km)'] = distances_to_next; df_itinerary['Čas k další (min)'] = times_to_next
    
    total_km = round(df_itinerary['Vzdálen k další (km)'].sum(), 1)
    pure_drive_min = int(df_itinerary['Čas k další (min)'].sum())
    total_hours = f"{pure_drive_min // 60}h {pure_drive_min % 60}min"
    total_cod = sum(parse_cod(x) for x in df_itinerary['Dobírka (Kč)'])
    
    def format_drive_time(m):
        try: m = int(float(m)); return f"{m//60}:{m%60:02d} h" if m >= 60 else f"{m} min"
        except: return ""

    df_web_display = df_itinerary.copy().astype(str); df_web_display['Čas přejezdu'] = df_itinerary['Čas k další (min)'].apply(format_drive_time)
    for bad_val in ['none', 'nan', '<na>', 'none.', 'nan.']:
        df_web_display.replace(bad_val, "", inplace=True); df_web_display.replace(bad_val.upper(), "", inplace=True); df_web_display.replace(bad_val.capitalize(), "", inplace=True)
    
    df_final_display = df_web_display[['Číslo objednávky', 'E-shop', 'Příjemce', 'Tisk_Adresa', 'Telefon', 'Dobírka (Kč)', 'Čas příjezdu', 'Okno příjezdu (2h)', 'Vzdálen k další (km)', 'Čas přejezdu', 'Poznámka']]

    def generate_map_image(itinerary_df):
        lats = itinerary_df['lat'].tolist(); lons = itinerary_df['lon'].tolist()
        if not lats: return None
        min_lat, max_lat = min(lats), max(lats); min_lon, max_lon = min(lons), max(lons)
        pad_lat = max(0.02, (max_lat - min_lat) * 0.15); pad_lon = max(0.02, (max_lon - min_lon) * 0.15)
        min_lat -= pad_lat; max_lat += pad_lat; min_lon -= pad_lon; max_lon += pad_lon
        def latlon_to_xy(lat, lon, z):
            lat_rad = math.radians(lat); n = 2.0 ** z; return ((lon + 180.0) / 360.0 * n), ((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
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
            px, py = coord_to_px(row['lat'], row['lon']); label = "S" if i == 0 else ("C" if i == len(itinerary_df)-1 else str(i))
            ax.annotate(label, (px, py), textcoords="offset points", xytext=(0,10), ha='center', fontsize=11, fontweight='bold', color='black', bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#7f8c8d", alpha=0.9), zorder=6)
        px_min_x, px_min_y = coord_to_px(max_lat, min_lon); px_max_x, px_max_y = coord_to_px(min_lat, max_lon)
        ax.set_xlim(px_min_x, px_max_x); ax.set_ylim(px_max_y, px_min_y); ax.axis('off'); plt.tight_layout(pad=0)
        img_buf = io.BytesIO(); plt.savefig(img_buf, format='png', bbox_inches='tight', pad_inches=0.1); img_buf.seek(0); plt.close(fig)
        return img_buf

    use_custom_font = False; font_family_name = "Helvetica"; local_font_reg, local_font_bold = "", ""
    paths_to_try = [("arial.ttf", "arialbd.ttf"), ("ARIAL.TTF", "ARIALBD.TTF"), ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf")]
    for r_path, b_path in paths_to_try:
        if os.path.exists(r_path) and os.path.exists(b_path): local_font_reg, local_font_bold = r_path, b_path; font_family_name = "ArialCustom"; use_custom_font = True; break

    if not use_custom_font:
        import unicodedata
        def clean_str(s): return ''.join(c for c in unicodedata.normalize('NFD', str(s)) if unicodedata.category(c) != 'Mn')
    else:
        def clean_str(s): return str(s)

    df_for_map = df_itinerary.dropna(subset=['lat', 'lon']).reset_index(drop=True)
    map_temp_img = generate_map_image(df_for_map) if not df_for_map.empty else None
    
    def build_page_one(pdf_obj, title_txt):
        pdf_obj.add_page(); pdf_obj.set_font(font_family_name, "B", 14)
        pdf_obj.cell(0, 8, clean_str(title_txt), ln=True, align="C"); pdf_obj.set_font(font_family_name, "", 9); pdf_obj.set_text_color(100, 100, 100)
        pdf_obj.cell(0, 5, clean_str(f"Vygenerováno: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Výjezd: {start_time.strftime('%H:%M')}"), ln=True, align="C"); pdf_obj.ln(2)
        pdf_obj.set_font(font_family_name, "B", 16); pdf_obj.set_text_color(44, 62, 80); pdf_obj.cell(0, 10, clean_str(route_name), ln=True, align="C"); pdf_obj.ln(2)
        pdf_obj.set_font(font_family_name, "B", 10.5); pdf_obj.set_text_color(50, 50, 50); pdf_obj.set_fill_color(245, 246, 250); pdf_obj.set_draw_color(200, 200, 200)
        pdf_obj.rect(10, pdf_obj.get_y(), 190, 16, style="DF"); pdf_obj.set_y(pdf_obj.get_y() + 2)
        pdf_obj.cell(95, 6, clean_str(f"  Celková vzdálenost: {total_km} km"), ln=False); pdf_obj.cell(95, 6, clean_str(f"Čistý čas jízdy: {total_hours}"), ln=True)
        pdf_obj.cell(95, 6, clean_str(f"  Celkové dobírky: {int(total_cod)} Kč"), ln=False); pdf_obj.cell(95, 6, clean_str(f"Základní pokladna (Kasáč): {int(kasac_value)} Kč"), ln=True); pdf_obj.ln(6)
        if map_temp_img:
            t_path = f"temp_m_{time.time()}.png"
            with open(t_path, "wb") as f: f.write(map_temp_img.getbuffer())
            pdf_obj.image(t_path, x=10, y=pdf_obj.get_y(), w=190); os.remove(t_path)

    # 1. PDF: ŘIDIČ
    pdf_driver = FPDF(); pdf_driver.header = lambda: None
    if use_custom_font: pdf_driver.add_font("ArialCustom", "", local_font_reg, uni=True); pdf_driver.add_font("ArialCustom", "B", local_font_bold, uni=True)
    build_page_one(pdf_driver, "TRASOVÝ SOUPIS ŘIDIČE (A4)")
    pdf_driver.add_page(); pdf_driver.set_font(font_family_name, "B", 14); pdf_driver.set_text_color(44, 62, 80)
    pdf_driver.cell(0, 8, clean_str(f"ITINERÁŘ TRASY - {route_name}"), ln=True); pdf_driver.ln(2)
    for idx, row in df_itinerary.iterrows():
        is_start = row['Číslo objednávky'] == 'START'; is_end = row['Číslo objednávky'] == 'CÍL'; is_not_end = idx < len(df_itinerary) - 1
        lbl = "S" if is_start else "C" if is_end else str(idx); prijemce = clean_str(row['Příjemce'])
        addr = clean_str(row['Tisk_Adresa']).replace('nan','').replace('NaN','').replace('None','').strip()
        if row['Chyba'] and not (is_start or is_end): addr = f"({row['Chyba']}) {addr}"
        phone_raw = str(row['Telefon']).strip() if row['Telefon'] and str(row['Telefon']).lower() not in ['none', 'nan', ''] else ""
        prefix, main_num = "", ""
        if phone_raw:
            if phone_raw.startswith("+420") or phone_raw.startswith("+421"): prefix = phone_raw[:4]; main_num = phone_raw[4:].strip()
            else: main_num = phone_raw
            m_c = main_num.replace(" ", "")
            main_num = f"{m_c[:3]} {m_c[3:6]} {m_c[6:]}" if len(m_c)==9 else " ".join([m_c[i:i+3] for i in range(0, len(m_c), 3)])
        cas_str = f"{row['Čas příjezdu']}" if (is_start or is_end) else f"Cca: {row['Čas příjezdu']}"
        okno_str = "" if (is_start or is_end) else f"{row['Okno příjezdu (2h)']}"
        name_and_id = prijemce[:40] if (is_start or is_end) else f"{prijemce[:22]} [{row['Číslo objednávky']}]"
        cod_val = parse_cod(row['Dobírka (Kč)']); dobirka_str = f"{int(cod_val)} Kč" if cod_val > 0 else ""
        note_raw = str(row.get('Poznámka', '')).strip(); has_note = bool(note_raw) and note_raw.lower() not in ['none', 'nan', '']; note_clean = clean_str(note_raw)
        box_h = 15 if has_note else 10; total_h = box_h + (5 if is_not_end else 2)
        if pdf_driver.get_y() + total_h > 280: pdf_driver.add_page()
        start_y = pdf_driver.get_y(); pdf_driver.set_fill_color(248, 249, 250) if idx % 2 == 0 else pdf_driver.set_fill_color(255, 255, 255)
        pdf_driver.set_draw_color(160, 160, 160); pdf_driver.rect(10, start_y, 190, box_h, "DF")
        pdf_driver.set_draw_color(100, 100, 100); pdf_driver.rect(12, start_y + 1.5, 4, 4)
        pdf_driver.set_xy(18, start_y + 1); pdf_driver.set_text_color(30, 30, 30); pdf_driver.set_font(font_family_name, "B", 11); pdf_driver.cell(8, 5, lbl, align="L")
        pdf_driver.set_xy(26, start_y + 1); pdf_driver.set_font(font_family_name, "B", 10); pdf_driver.cell(60, 5, clean_str(name_and_id))
        pdf_driver.set_xy(88, start_y + 1)
        if phone_raw:
            pdf_driver.set_font(font_family_name, "", 7); pdf_driver.cell(pdf_driver.get_string_width(prefix + " "), 5, prefix + " ")
            pdf_driver.set_font(font_family_name, "B", 10); pdf_driver.cell(20, 5, main_num)
        else: pdf_driver.set_font(font_family_name, "", 9); pdf_driver.cell(20, 5, "-")
        pdf_driver.set_xy(120, start_y + 1)
        if is_start or is_end: pdf_driver.set_font(font_family_name, "B", 10); pdf_driver.cell(40, 5, clean_str(cas_str), align="C")
        else:
            pdf_driver.set_font(font_family_name, "", 7); pdf_driver.set_text_color(120, 120, 120); pdf_driver.cell(15, 5, clean_str(cas_str))
            pdf_driver.set_font(font_family_name, "B", 11); pdf_driver.set_text_color(30, 30, 30); pdf_driver.cell(25, 5, clean_str(okno_str))
        pdf_driver.set_xy(160, start_y + 1)
        if dobirka_str: pdf_driver.set_font(font_family_name, "B", 11); pdf_driver.set_text_color(231, 76, 60); pdf_driver.cell(30, 5, clean_str(dobirka_str), align="R")
        elif not is_start and not is_end: pdf_driver.set_font(font_family_name, "B", 9); pdf_driver.set_text_color(46, 204, 113); pdf_driver.cell(30, 5, clean_str("PLACENO"), align="R")
        pdf_driver.set_text_color(30, 30, 30); curr_y = start_y + 6
        if has_note:
            pdf_driver.set_fill_color(255, 242, 204); pdf_driver.rect(26, curr_y, 162, 4.5, "F"); pdf_driver.set_xy(27, curr_y)
            pdf_driver.set_font(font_family_name, "B", 8); pdf_driver.set_text_color(211, 84, 0); pdf_driver.cell(160, 4.5, clean_str(f"⚠️ VZKAZ ŘIDIČI: {note_clean[:120]}")); curr_y += 4.5
        pdf_driver.set_text_color(50, 50, 50); pdf_driver.set_xy(26, curr_y); pdf_driver.set_font(font_family_name, "", 8); pdf_driver.cell(164, 4, clean_str(addr))
        if is_not_end:
            pdf_driver.set_xy(10, start_y + box_h); pdf_driver.set_font(font_family_name, "", 7); pdf_driver.set_text_color(160, 160, 160)
            try: dm = int(float(row['Čas k další (min)'])); d_s = f"{dm//60}:{dm%60:02d} h" if dm >= 60 else f"{dm} min"
            except: d_s = f"{row['Čas k další (min)']} min"
            pdf_driver.cell(190, 5, clean_str(f"↓ Přejezd: {row['Vzdálen k další (km)']} km ({d_s}) ↓"), align="C")
        pdf_driver.set_y(start_y + total_h)

    # 2. PDF: DISPEČER
    pdf_disp = FPDF(); pdf_disp.header = lambda: None
    if use_custom_font: pdf_disp.add_font("ArialCustom", "", local_font_reg, uni=True); pdf_disp.add_font("ArialCustom", "B", local_font_bold, uni=True)
    build_page_one(pdf_disp, "DISPEČERSKÝ SOUPIS A PŘEHLED TRASY")
    pdf_disp.add_page(); pdf_disp.set_font(font_family_name, "B", 13); pdf_disp.set_text_color(44, 62, 80)
    pdf_disp.cell(0, 8, clean_str(f"ADMINISTRATIVNÍ PŘEHLED ZÁSILEK - {route_name}"), ln=True); pdf_disp.ln(2)
    for idx, row in df_itinerary.iterrows():
        if row['Číslo objednávky'] in ['START', 'CÍL']: continue
        prijemce = clean_str(row['Příjemce']); order_id = row['Číslo objednávky']
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
        start_y = pdf_disp.get_y(); pdf_disp.set_fill_color(252, 253, 254) if idx % 2 == 0 else pdf_disp.set_fill_color(255, 255, 255)
        pdf_disp.set_draw_color(140, 145, 155); pdf_disp.rect(10, start_y, 190, box_h, "DF")
        pdf_disp.set_draw_color(100, 100, 100); pdf_disp.rect(13, start_y + 3, 4, 4); pdf_disp.set_xy(18, start_y + 2.5); pdf_disp.set_font(font_family_name, "B", 9); pdf_disp.cell(20, 5, clean_str("POTVRZENO"))
        pdf_disp.rect(13, start_y + 9, 4, 4); pdf_disp.set_xy(18, start_y + 8.5); pdf_disp.set_font(font_family_name, "B", 9); pdf_disp.cell(20, 5, clean_str("SMS"))
        pdf_disp.set_xy(42, start_y + 2); pdf_disp.set_font(font_family_name, "B", 11); pdf_disp.set_text_color(44, 62, 80); pdf_disp.cell(100, 5, clean_str(f"[{order_id}] {prijemce[:35]}"))
        pdf_disp.set_xy(150, start_y + 2); pdf_disp.set_font(font_family_name, "B", 11)
        if cod_val > 0: pdf_disp.set_text_color(231, 76, 60); pdf_disp.cell(40, 5, clean_str(dobirka_str), align="R")
        else: pdf_disp.set_text_color(46, 204, 113); pdf_disp.cell(40, 5, clean_str("PLACENO"), align="R")
        pdf_disp.set_text_color(30, 30, 30); pdf_disp.set_xy(42, start_y + 8)
        if phone_raw != "-":
            pdf_disp.set_font(font_family_name, "", 8); pdf_disp.cell(pdf_disp.get_string_width(prefix + " "), 5, prefix + " ")
            pdf_disp.set_font(font_family_name, "B", 12); pdf_disp.cell(30, 5, main_num)
        else: pdf_disp.set_font(font_family_name, "", 10); pdf_disp.cell(30, 5, "-")
        pdf_disp.set_xy(110, start_y + 8); pdf_disp.set_font(font_family_name, "", 8); pdf_disp.set_text_color(120, 120, 120); pdf_disp.cell(15, 5, clean_str(f"Cca: {row['Čas příjezdu']}"))
        pdf_disp.set_font(font_family_name, "B", 12); pdf_disp.set_text_color(30, 30, 30); pdf_disp.cell(65, 5, clean_str(f"{row['Okno příjezdu (2h)']}"), align="R")
        pdf_disp.set_xy(42, start_y + 14); pdf_disp.set_font(font_family_name, "", 8.5); pdf_disp.set_text_color(60, 60, 60); pdf_disp.cell(148, 4, clean_str(addr))
        pdf_disp.set_xy(13, start_y + 20); pdf_disp.set_font(font_family_name, "B", 8); pdf_disp.set_text_color(50, 50, 50); pdf_disp.cell(30, 4, clean_str("📦 PRODUKTY:"))
        pdf_disp.set_xy(35, start_y + 20); pdf_disp.set_font(font_family_name, "", 8); pdf_disp.set_text_color(70, 70, 70); pdf_disp.multi_cell(155, 4, clean_str(p_plain), border=0)
        pdf_disp.set_y(start_y + box_h + 2)

    # 3. PDF: SKLADNÍK
    pdf_ware = FPDF(); pdf_ware.header = lambda: None
    if use_custom_font: pdf_ware.add_font("ArialCustom", "", local_font_reg, uni=True); pdf_ware.add_font("ArialCustom", "B", local_font_bold, uni=True)
    build_page_one(pdf_ware, "NÁKLADOVÝ LIST PRO SKLAD")
    pdf_ware.add_page(); pdf_ware.set_font(font_family_name, "B", 13); pdf_ware.set_text_color(44, 62, 80)
    pdf_ware.cell(0, 8, clean_str(f"POŘADÍ NAKLÁDKY A ZBOŽÍ - {route_name}"), ln=True); pdf_ware.ln(2)
    for idx, row in df_itinerary.iterrows():
        if row['Číslo objednávky'] in ['START', 'CÍL']: continue
        prijemce = clean_str(row['Příjemce']); order_id = row['Číslo objednávky']
        p_html = row.get('Produkty', '')
        p_plain = p_html.replace('<br>- ', '\n- ').replace('<br>', '\n').replace('<i>', '').replace('</i>', '').strip()
        if "Žádné produkty" in p_plain or not p_plain: p_plain = "- Žádné specifické produkty v exportu"
        if not p_plain.startswith('-'): p_plain = '- ' + p_plain
        prod_lines_count = p_plain.count('\n') + 1; box_h = 11 + (prod_lines_count * 4.5)
        if pdf_ware.get_y() + box_h > 280: pdf_ware.add_page()
        start_y = pdf_ware.get_y(); pdf_ware.set_fill_color(252, 253, 254) if idx % 2 == 0 else pdf_ware.set_fill_color(255, 255, 255)
        pdf_ware.set_draw_color(140, 145, 155); pdf_ware.rect(10, start_y, 190, box_h, "DF")
        pdf_ware.set_draw_color(100, 100, 100); pdf_ware.rect(13, start_y + 3, 5, 5) # Checkbox NALOŽENO
        pdf_ware.set_xy(22, start_y + 2.5); pdf_ware.set_font(font_family_name, "B", 11); pdf_ware.set_text_color(44, 62, 80)
        pdf_ware.cell(160, 6, clean_str(f"Zastávka č. {idx}   |   Objednávka: {order_id}   |   {prijemce}"))
        pdf_ware.set_xy(22, start_y + 9); pdf_ware.set_font(font_family_name, "", 9.5); pdf_ware.set_text_color(20, 20, 20)
        pdf_ware.multi_cell(175, 4.5, clean_str(p_plain), border=0); pdf_ware.set_y(start_y + box_h + 2)

    raw_dr = pdf_driver.output(dest='S'); pdf_bytes_dr = raw_dr.encode('latin1') if isinstance(raw_dr, str) else bytes(raw_dr)
    raw_di = pdf_disp.output(dest='S'); pdf_bytes_di = raw_di.encode('latin1') if isinstance(raw_di, str) else bytes(raw_di)
    raw_wa = pdf_ware.output(dest='S'); pdf_bytes_wa = raw_wa.encode('latin1') if isinstance(raw_wa, str) else bytes(raw_wa)
    
    buffer_xls = io.BytesIO()
    with pd.ExcelWriter(buffer_xls, engine='openpyxl') as writer: df_final_display.to_excel(writer, index=False, sheet_name='Trasový soupis')

    return {
        'km': total_km, 'hours': total_hours, 'cod': int(total_cod), 'df': df_final_display,
        'pdf_dr': pdf_bytes_dr, 'pdf_di': pdf_bytes_di, 'pdf_wa': pdf_bytes_wa, 'xls': buffer_xls.getvalue()
    }

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
            try: df_temp = pd.read_csv(io.BytesIO(content), sep=sep, dtype=str, encoding=enc); df = df_temp if len(df_temp.columns) > 2 else None
            except: pass
    if df is None:
        try: df = pd.read_excel(io.BytesIO(content), dtype=str)
        except: pass
    if df is None:
        try: dfs = pd.read_html(io.BytesIO(content), dtype=str); df = dfs[0] if dfs else None
        except: pass
    if df is None: raise ValueError("Nepodařilo se rozpoznat formát souboru.")
    return df.loc[:, ~df.columns.duplicated()]

@st.cache_data(show_spinner=False, ttl=300)
def prepare_shop_data(url, prefix, eshop_name, exclude_wholesale=False):
    df_raw = fetch_data_from_url(url)
    prod_col, amount_col, item_type_col = None, None, None
    for col in ['itemName', 'Název položky', 'productName', 'name', 'Název produktu', 'title', 'Položka', 'Produkt', 'Zboží']:
        if col in df_raw.columns: prod_col = col; break
    for col in ['itemAmount', 'amount', 'Množství', 'množství', 'count', 'itemCount', 'Počet', 'ks', 'Ks']:
        if col in df_raw.columns: amount_col = col; break
    for col in ['orderItemType', 'itemType', 'type', 'Typ položky']:
        if col in df_raw.columns: item_type_col = col; break

    p_dict = {}
    skip_keywords = ['doprava', 'platba', 'dobírka', 'ppl', 'dpd', 'zásilkovna', 'gls', 'česká pošta', 'osobní odběr', 'kurýr', 'balíkovna', 'převodem', 'hotově', 'karta', 'kartou', 'gopay', 'comgate', 'dobirka', 'shoptet pay', 'twisto', 'payu']

    if 'code' in df_raw.columns and prod_col:
        for code, group in df_raw.groupby('code'):
            prods = []
            for _, r in group.iterrows():
                p_name = str(r[prod_col])
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
                        else: p_name_clean = p_name
                        prods.append(p_name_clean)
            p_dict[prefix + str(code)] = "<br>- " + "<br>- ".join(prods) if prods else "<br><i>Neznámé produkty</i>"

    if 'code' in df_raw.columns: df = df_raw.drop_duplicates(subset=['code']).copy(); df.rename(columns={'code': 'id'}, inplace=True)
    else: df = df_raw.copy(); df['id'] = [f"Neznamé-{i}" for i in range(len(df))] if 'id' not in df.columns else df['id']

    df['id'] = prefix + df['id'].astype(str); df['eshop'] = eshop_name
    if exclude_wholesale:
        mask_vw = pd.Series([False] * len(df), index=df.index)
        for col in df.columns:
            if df[col].dtype == object and any(x in str(col).lower() for x in ['group', 'skupin', 'customergroupname']): mask_vw = mask_vw | df[col].astype(str).str.lower().str.contains('velkoodběratel|velkoobchod', na=False, regex=True)
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

# --- PŘEDNÍ PANEL (HISTORIE ULOŽENÝCH ROZVOZŮ) ---
st.markdown("---")
st.subheader("📁 Uložené rozvozy (Historie)")
if not saved_routes:
    st.info("Zatím nemáte žádné uložené rozvozy.")
else:
    for r in reversed(saved_routes):
        with st.container():
            col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
            col1.markdown(f"**🗓️ {r['name']}**<br>📦 {len(r['orders'])} obj.", unsafe_allow_html=True)
            
            if col2.button("🖨️ Vygenerovat PDF", key=f"gen_{r['id']}", use_container_width=True):
                if 'full_data' in r:
                    with st.spinner("Rychle generuji PDF z archivu..."):
                        f_df = pd.DataFrame(r['full_data'])
                        res = create_route_pdfs(r['name'], f_df, start_address, end_address, start_point_name, end_point_name, mapy_api_key, start_time, unload_time_min, False, kasac_value)
                        if res: st.session_state[f"print_ready_{r['id']}"] = res
                else: st.error("Tento rozvoz je ve starém formátu. Zkuste jej prosím otevřít do mapy a znovu Uložit do historie.")
                    
            if col3.button("✏️ Otevřít (Upravit)", key=f"open_{r['id']}", use_container_width=True):
                st.session_state['selected_orders'] = r['orders'].copy()
                if 'details' in r:
                    for o_id, det in r['details'].items():
                        st.session_state[f"note_{o_id}"] = det.get("note", "")
                        if det.get("addr"): st.session_state[f"addr_{o_id}"] = det.get("addr", "")
                st.rerun()
                
            if col4.button("🗑️ Smazat", key=f"del_{r['id']}", use_container_width=True):
                saved_routes.remove(r); save_routes(saved_routes); st.rerun()

        if f"print_ready_{r['id']}" in st.session_state:
            res = st.session_state[f"print_ready_{r['id']}"]
            st.success(f"Archivní data připravena: {res['km']} km | {res['hours']} | Dobírky: {res['cod']} Kč")
            dl1, dl2, dl3, dl4 = st.columns(4)
            dl1.download_button("📥 PDF ŘIDIČ", data=res['pdf_dr'], file_name=f"{r['name']}_ridic.pdf", mime="application/pdf", key=f"dl_dr_{r['id']}", type="primary")
            dl2.download_button("📥 PDF DISPEČER", data=res['pdf_di'], file_name=f"{r['name']}_dispecer.pdf", mime="application/pdf", key=f"dl_di_{r['id']}", type="primary")
            dl3.download_button("📥 PDF SKLAD", data=res['pdf_wa'], file_name=f"{r['name']}_sklad.pdf", mime="application/pdf", key=f"dl_wa_{r['id']}", type="primary")
            dl4.download_button("📥 XLSX EXCEL", data=res['xls'], file_name=f"{r['name']}.xlsx", key=f"dl_xl_{r['id']}", type="secondary")
        st.markdown("---")

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
        st.caption("*(Skryto: velkoobchod)*")
    else: selected_vomaks = []; st.info("Žádná data pro výběr.")

with col_sh3:
    st.markdown("### 🛒 Slevadoma.cz")
    if not df_sleva.empty and 'statusName' in df_sleva.columns:
        statuses3 = sorted(df_sleva['statusName'].dropna().unique().tolist())
        selected_sleva = st.multiselect("Zobrazit na mapě (Slevadoma):", options=statuses3, default=[s for s in st.session_state['sleva_st_saved'] if s in statuses3], key='sleva_st', on_change=update_sleva)
    else: selected_sleva = []; st.info("Žádná data pro výběr.")

mask_maxi = (df_shop['eshop'] == 'Max-i.cz') & df_shop['statusName'].isin(selected_maxi)
mask_vomaks = (df_shop['eshop'] == 'Vomaks.cz') & df_shop['statusName'].isin(selected_vomaks)
mask_sleva = (df_shop['eshop'] == 'Slevadoma.cz') & df_shop['statusName'].isin(selected_sleva)
mask_selected = df_shop['id'].isin(st.session_state['selected_orders'])
mask_saved = df_shop['id'].isin(saved_routes_ids)

df_to_process = df_shop[mask_selected | ((mask_maxi | mask_vomaks | mask_sleva) & ~mask_saved)].copy()

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
            
            parts = [ulice, cp, mesto, psc]
            adresa_casti = [str(x).strip() for x in parts if pd.notna(x) and str(x).strip().lower() not in ['nan', 'none', '<na>', '']]
            cela_adresa = " ".join(adresa_casti).strip()

            if cela_adresa in st.session_state['geo_cache']: lat, lon = st.session_state['geo_cache'][cela_adresa]
            else:
                lat, lon = geocode_address_api(cela_adresa, mapy_api_key)
                if lat is not None and lon is not None: st.session_state['geo_cache'][cela_adresa] = [lat, lon]; new_geo_added = True

            jmeno = row.get('deliveryFullName'); jmeno = row.get('billFullName', 'Neznámý příjemce') if pd.isna(jmeno) or str(jmeno).strip() in ['', 'nan', 'None'] else jmeno
            orders.append({
                'Číslo objednávky': order_id, 'E-shop': row.get('eshop', ''), 'Příjemce': str(jmeno), 'Status': str(row.get('statusName', '')),
                'Celá_adresa': cela_adresa, 'Ulice': f"{ulice} {cp}".strip(), 'Město': mesto, 'PSČ': psc, 'Chyba': "(!)" if lat is None else "",
                'Telefon': str(row.get('phone', '')), 'Dobírka (Kč)': str(row.get('geisDeliveryPriceToPay', row.get('priceToPay', '0'))),
                'Produkty': products_dict.get(order_id, "<br><i>Žádné produkty v exportu</i>"), 'lat': lat, 'lon': lon
            })
        if new_geo_added: save_geo_cache(st.session_state['geo_cache'])

if orders: df_orders = pd.DataFrame(orders)
else:
    df_orders = pd.DataFrame(columns=['Číslo objednávky', 'E-shop', 'Příjemce', 'Status', 'Celá_adresa', 'Ulice', 'Město', 'PSČ', 'Chyba', 'Telefon', 'Dobírka (Kč)', 'Produkty', 'lat', 'lon'])
    st.info("Zvolte stavy v checklistech nahoře pro zobrazení objednávek na mapě.")

st.markdown("---")
col_metric1, col_metric2, col_metric3, col_metric4 = st.columns([1, 1, 1.5, 0.8])
pocet_placeholder = col_metric1.empty()
dobirka_placeholder = col_metric2.empty()

with col_metric4:
    st.write("") 
    if st.button("🗑️ Vymazat trasu", use_container_width=True, type="secondary"): st.session_state['selected_orders'] = []; st.rerun()

with col_metric3:
    if st.button("🤖 Magický návrh rozvozu", use_container_width=True, type="primary"):
        if len(df_orders) < auto_min_orders: st.error(f"Na mapě je pouze {len(df_orders)} volných objednávek. Limit je minimálně {auto_min_orders}.")
        else:
            with st.spinner("Počítám nejlepší možnou trasu..."):
                s_lat, s_lon = geocode_address_api(start_address, mapy_api_key)
                e_lat, e_lon = geocode_address_api(end_address, mapy_api_key)
                dir_lat, dir_lon = None, None
                if target_direction_city.strip(): dir_lat, dir_lon = geocode_address_api(target_direction_city, mapy_api_key)
                
                auto_max_time_min_val = auto_max_time_h * 60
                if s_lat and s_lon and e_lat and e_lon:
                    points_dict = {'START': {'lat': s_lat, 'lon': s_lon}, 'END': {'lat': e_lat, 'lon': e_lon}}
                    available_orders = []
                    base_dist_dir = geodesic((s_lat, s_lon), (dir_lat, dir_lon)).kilometers * 1.3 if dir_lat and dir_lon else 0
                    
                    for _, r in df_orders.iterrows():
                        o_id = r['Číslo objednávky']
                        if o_id not in st.session_state['selected_orders']:
                            if dir_lat and dir_lon:
                                dist_to_p = geodesic((s_lat, s_lon), (r['lat'], r['lon'])).kilometers * 1.3
                                dist_from_p_to_dir = geodesic((r['lat'], r['lon']), (dir_lat, dir_lon)).kilometers * 1.3
                                if (dist_to_p + dist_from_p_to_dir) > (base_dist_dir * target_tolerance): continue
                            points_dict[o_id] = {'lat': r['lat'], 'lon': r['lon']}; available_orders.append(o_id)
                            
                    if len(available_orders) < auto_min_orders: st.error(f"Ve vybraném směru je pouze {len(available_orders)} objednávek. Zvětšete koridor.")
                    else:
                        dist_matrix = {}
                        for p1_id, p1_coords in points_dict.items():
                            dist_matrix[p1_id] = {}
                            for p2_id, p2_coords in points_dict.items():
                                dist_matrix[p1_id][p2_id] = 0.0 if p1_id == p2_id else geodesic((p1_coords['lat'], p1_coords['lon']), (p2_coords['lat'], p2_coords['lon'])).kilometers * 1.3

                        best_route_ids = []
                        starting_points = sorted(available_orders, key=lambda o: geodesic((points_dict[o]['lat'], points_dict[o]['lon']), (dir_lat, dir_lon)).kilometers)[:3] if dir_lat and dir_lon else available_orders
                        
                        for first_stop in starting_points:
                            unvisited = set(available_orders); unvisited.remove(first_stop); route_nodes = ['START', first_stop, 'END']
                            while unvisited:
                                best_candidate, best_insert_idx, best_added_dist = None, -1, float('inf')
                                for candidate in unvisited:
                                    for i in range(1, len(route_nodes)):
                                        prev_node, next_node = route_nodes[i-1], route_nodes[i]
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
                        else: st.error(f"Do nastavených limitů se nevešlo víc než {len(best_route_ids)} zastávek.")
                else: st.error("Nemohu najít souřadnice skladu.")

st.markdown("---")
mapa_cr = folium.Map(location=st.session_state['map_center'], zoom_start=st.session_state['map_zoom'], tiles=f"https://api.mapy.cz/v1/maptiles/basic/256/{{z}}/{{x}}/{{y}}?apikey={mapy_api_key}", attr="Mapy.cz")
if not df_orders.empty:
    for idx, row in df_orders.dropna(subset=['lat', 'lon']).iterrows():
        order_id = row['Číslo objednávky']; is_selected = order_id in st.session_state['selected_orders']
        cod_val = parse_cod(row['Dobírka (Kč)']); eshop_name = row.get('E-shop', '')
        marker_text = 'M' if eshop_name == 'Max-i.cz' else 'V' if eshop_name == 'Vomaks.cz' else 'S' if eshop_name == 'Slevadoma.cz' else '?'
        if is_selected:
            poradi = st.session_state['selected_orders'].index(order_id) + 1
            oznaceni = f"<b>{poradi}. zastávka</b><br>"
            ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color='#2ecc71', border_color='#27ae60', inner_iconStyle='margin-top:2px; font-weight:bold; font-size:14px;', number=str(poradi))
        else:
            oznaceni = ""; bg_col = '#e74c3c' if cod_val > 0 else '#3498db'; bd_col = '#c0392b' if cod_val > 0 else '#2980b9'
            ikona = BeautifyIcon(icon_shape='marker', text_color='white', background_color=bg_col, border_color=bd_col, inner_iconStyle='margin-top:2px; font-weight:bold; font-size:14px;', number=marker_text)
        vzhled_bubliny = f"<span style='display:none;'>[ID:{order_id}]</span><div style='min-width: 250px; font-family: sans-serif; font-size: 13px;'>{oznaceni}<b>{order_id}</b> ({row['E-shop']})<br>{row['Příjemce']}<br><i>Stav: {row['Status']}</i><br><b>Dobírka: {row['Dobírka (Kč)']} Kč</b><br>{row['Celá_adresa']}<hr style='margin: 5px 0;'><b>Produkty:</b>{row['Produkty']}</div>"
        folium.Marker(location=[row['lat'], row['lon']], tooltip=folium.Tooltip(vzhled_bubliny), icon=ikona).add_to(mapa_cr)
    
map_data = st_folium(mapa_cr, height=600, use_container_width=True, returned_objects=["last_object_clicked_tooltip"])
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
                    if clicked_id in r.get('orders', []): r['orders'].remove(clicked_id); routes_modified = True
                if routes_modified: saved_routes = [r for r in saved_routes if len(r.get('orders', [])) > 0]; save_routes(saved_routes)
            else: st.session_state['selected_orders'].append(clicked_id)
            st.rerun()

if st.session_state['selected_orders'] and not df_orders.empty:
    platne_ids = [o_id for o_id in st.session_state['selected_orders'] if o_id in df_orders['Číslo objednávky'].values]
    df_selected = df_orders.set_index('Číslo objednávky').loc[platne_ids].reset_index() if platne_ids else pd.DataFrame()
else: df_selected = pd.DataFrame()

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
            with st.spinner("Počítám nejkratší logistickou smyčku..."):
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
                            dist_matrix[points[i]['id']][points[j]['id']] = 0.0 if i == j else geodesic((points[i]['lat'], points[i]['lon']), (points[j]['lat'], points[j]['lon'])).kilometers
                    route_nodes = [p['id'] for p in points]; optimized_route_nodes = optimize_route_2opt(route_nodes, dist_matrix)
                    st.session_state['selected_orders'] = [n for n in optimized_route_nodes if n not in ['START', 'END']]; st.rerun()
                else: st.error("Nepodařilo se zjistit souřadnice skladu.")
        
        items_list = []
        mapping_dict = {}
        for _, row in df_selected.iterrows():
            item_str = f"{row['Číslo objednávky']} | {row['Příjemce']} | {row['Chyba']} {row['Celá_adresa']} | {row['Dobírka (Kč)']} Kč"
            items_list.append(item_str); mapping_dict[item_str] = row.to_dict()
        sorted_strings = sort_items(items_list, direction='vertical') or items_list

    with tab_notes:
        st.info("Zde můžete k seřazeným objednávkám dopsat vzkaz řidiči.")
        order_notes, order_addresses = {}, {}
        for s in sorted_strings:
            order_data = mapping_dict[s]; order_id = order_data['Číslo objednávky']
            st.markdown(f"**{order_id} ({order_data['E-shop']}) | 👤 {order_data['Příjemce']}**")
            col_note, col_addr = st.columns(2)
            with col_note:
                default_note = st.session_state.get(f"note_{order_id}", "")
                order_notes[order_id] = st.text_input("Poznámka pro řidiče:", value=default_note, key=f"note_input_{order_id}")
            with col_addr:
                original_full_address = f"{order_data['Ulice']}, {order_data['Město']} {order_data['PSČ']}".strip(', ')
                default_addr = st.session_state.get(f"addr_{order_id}", original_full_address)
                order_addresses[order_id] = st.text_input("Upravená adresa pro tisk:", value=default_addr, key=f"addr_input_{order_id}")
            st.write("") 

    # --- KROK 3: TISK (V HLAVNÍM PROSTORU) ---
    st.markdown("---")
    st.subheader("Krok 3: Tisk a časy")
    route_name_input = st.text_input("📝 Název rozvozu / trasy (pro tisk i historii)", value=f"Rozvoz {datetime.now().strftime('%d.%m. %H:%M')}")
    slow_mode = st.checkbox("🐌 Režim 'Šnek' (Automaticky natáhne čistý čas jízdy o 10 %)")
    
    if 'calc_main' not in st.session_state: st.session_state['calc_main'] = False
    
    if st.button("🚀 Vypočítat časy a generovat všechna PDF", type="primary"):
        final_rows = [mapping_dict[s] for s in sorted_strings]; final_df = pd.DataFrame(final_rows)
        final_df['Poznámka'] = final_df['Číslo objednávky'].map(order_notes)
        final_df['Tisk_Adresa'] = final_df['Číslo objednávky'].map(order_addresses)
        res = create_route_pdfs(route_name_input, final_df, start_address, end_address, start_point_name, end_point_name, mapy_api_key, start_time, unload_time_min, slow_mode, kasac_value)
        if res:
            st.session_state['print_main'] = res
            st.session_state['calc_main'] = True
            st.rerun()

    if st.session_state['calc_main'] and 'print_main' in st.session_state:
        res = st.session_state['print_main']
        st.success("Výpočet všech tras úspěšně dokončen!")
        col_res1, col_res2, col_res3 = st.columns(3)
        col_res1.metric(label="🗺️ Celková délka trasy", value=f"{res['km']} km")
        col_res2.metric(label="⏱️ Čistý čas jízdy", value=res['hours'])
        col_res3.metric(label="💰 Celková hotovost z dobírek", value=f"{res['cod']} Kč")
        st.write("")
        st.dataframe(res['df'], use_container_width=True)

        col_dl1, col_dl2, col_dl3, col_dl4 = st.columns(4)
        col_dl1.download_button("📥 PDF ŘIDIČ", data=res['pdf_dr'], file_name=f"{route_name_input}_ridic.pdf", mime="application/pdf", key="m_dr", type="primary")
        col_dl2.download_button("📥 PDF DISPEČER", data=res['pdf_di'], file_name=f"{route_name_input}_dispecer.pdf", mime="application/pdf", key="m_di", type="primary")
        col_dl3.download_button("📥 PDF SKLAD", data=res['pdf_wa'], file_name=f"{route_name_input}_sklad.pdf", mime="application/pdf", key="m_wa", type="primary")
        col_dl4.download_button("📥 XLSX EXCEL", data=res['xls'], file_name=f"{route_name_input}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="m_xl", type="secondary")
            
    # --- ULOŽENÍ ROZVOZU DO HISTORIE ---
    st.markdown("---")
    st.info(f"Tímto přesunete aktuální objednávky do historie nahoře a ty zmizí z hlavní mapy.")
    if st.button("💾 Uložit do historie a vyčistit mapu", type="primary", use_container_width=True):
        final_rows = [mapping_dict[s] for s in sorted_strings]
        f_df = pd.DataFrame(final_rows)
        f_df['Poznámka'] = f_df['Číslo objednávky'].map(order_notes)
        f_df['Tisk_Adresa'] = f_df['Číslo objednávky'].map(order_addresses)
        
        sorted_ids = f_df['Číslo objednávky'].tolist()
        route_details = {o_id: {"note": order_notes.get(o_id, ""), "addr": order_addresses.get(o_id, "")} for o_id in sorted_ids}
        new_route = {"id": str(time.time()), "name": route_name_input, "orders": sorted_ids, "details": route_details, "full_data": f_df.to_dict('records')}
        
        saved_routes.append(new_route); save_routes(saved_routes)
        st.session_state['selected_orders'] = []; st.session_state['calc_main'] = False 
        st.success("Rozvoz byl bezpečně uložen do horního panelu!")
        time.sleep(1.5); st.rerun()