# app.py — Sistema web unificado (Excel + Web Scraping + Logo + Filtros) en 1 archivo
# Ejecuta:  python app.py  ->  http://127.0.0.1:5000
#
# Requisitos:
#   pip install flask requests beautifulsoup4 pandas openpyxl lxml xlsxwriter
#
# Usuarios por defecto:
#   admin/admin  (rol: admin)
#   consulta/consulta  (rol: consulta)
#
# Qué hace (highlights):
# - Login y control de roles (admin puede subir BASE/EXTRA y cambiar LOGO)
# - Carga/normalización de Excel (BASE=fuente.xlsx, EXTRA=productos1.xlsx) en ~/.meds_app_data_web
# - Búsqueda por PRODUCTO / PRINCIPIO ACTIVO / AMBOS sobre la base local
# - Scraping en vivo (DuckDuckGo -> páginas -> regex de precio) y modo "AMBOS" (BASE+WEB)
# - Tabla con paginación + orden por columnas (precio “inteligente”) + filtro por Farmacia (hasta 4)
# - KPIs (MENOR/MAYOR) con botones para abrir el enlace
# - Exportar el filtrado actual a CSV/XLSX (con formato contable en XLSX)
# - Logo personalizado (persistente) mostrado en la UI
#
# Notas:
# - Varias funciones de normalización, columnas y UX se portaron/ajustaron desde MedsApp_v13 (Tkinter)
#   para este entorno web.
# - Si no tienes Excel aún, se generan archivos vacíos con encabezados estándar en la primera ejecución.

from flask import (
    Flask, request, session, redirect, url_for, jsonify, send_file, make_response
)
import os, sys, io, re, json, shutil, datetime, math
import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from werkzeug.utils import secure_filename

APP_TITLE   = "SISTEMA WEB – BÚSQUEDA DE MEDICAMENTOS"
APP_VERSION = "2025.10"

# --------- Flask ----------
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET_KEY", "dev-secret-change-me")

# --------- Paths & Archivos persistentes ----------
USER_DATA_DIR = os.path.join(os.path.expanduser("~"), ".meds_app_data_web")
os.makedirs(USER_DATA_DIR, exist_ok=True)

DATA_BASENAME  = "fuente.xlsx"      # Base editable
EXTRA_BASENAME = "productos1.xlsx"  # Base adicional (consulta)
USERS_BASENAME = "usuarios.json"    # Usuarios locales
LOGO_NAME      = "logo.jpg"             # Se guardará como logo.(png|jpg|...)

EXCEL_PATH       = os.path.join(USER_DATA_DIR, DATA_BASENAME)
EXCEL_EXTRA_PATH = os.path.join(USER_DATA_DIR, EXTRA_BASENAME)
USERS_PATH       = os.path.join(USER_DATA_DIR, USERS_BASENAME)

# --------- Columnas ----------
BASE_COLUMNS_STD = [
    "CÓDIGO PRODUCTO",
    "Producto (Marca comercial)",
    "Principio Activo",
    "N° DIGEMID",
    "Laboratorio / Fabricante",
    "Presentación",
    "Precio",
    "Farmacia / Fuente",
    "Enlace",
]
EXTRA_COLUMNS = [
    "GRUPO",
    "Laboratorio Abreviado",
    "LABORATORIO PRECIO",
]
DEFAULT_UI_ORDER = [
    "CÓDIGO PRODUCTO",
    "Producto (Marca comercial)",
    "Principio Activo",
    "Laboratorio / Fabricante",
    "Laboratorio Abreviado",
    "LABORATORIO PRECIO",
    "Presentación",
    "Precio",
    "Farmacia / Fuente",
    "GRUPO",
    "Enlace",
]
_TEXT_COLS = [c for c in BASE_COLUMNS_STD if c not in ("Precio", "Enlace")]

# --------- Usuarios ----------
def _ensure_users():
    if not os.path.exists(USERS_PATH):
        default_users = {"users":[
            {"username":"admin","password":"admin","role":"admin"},
            {"username":"consulta","password":"consulta","role":"consulta"},
        ]}
        with open(USERS_PATH,"w",encoding="utf-8") as f:
            json.dump(default_users, f, indent=2, ensure_ascii=False)

def load_users():
    _ensure_users()
    try:
        with open(USERS_PATH,"r",encoding="utf-8") as f:
            return json.load(f).get("users",[])
    except Exception:
        return [
            {"username":"admin","password":"admin","role":"admin"},
            {"username":"consulta","password":"consulta","role":"consulta"},
        ]

def check_credentials(u,p):
    for x in load_users():
        if x["username"]==u and x["password"]==p:
            return x["role"]
    return None

def save_users(users):
    """Save users to JSON file"""
    try:
        with open(USERS_PATH,"w",encoding="utf-8") as f:
            json.dump({"users":users}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving users: {e}")

# --------- Utilidades de logo ----------
def resource_path(rel: str) -> str:
    """Get resource path for bundled files (PyInstaller) or local files"""
    try:
        base = sys._MEIPASS  # PyInstaller
    except Exception:
        base = os.path.abspath(".")
    return os.path.join(base, rel)

def find_user_logo_path():
    """Find user's custom logo in USER_DATA_DIR"""
    for ext in (".png",".jpg",".jpeg",".gif",".bmp"):
        p = os.path.join(USER_DATA_DIR, "logo"+ext)
        if os.path.exists(p):
            return p
    return None

def current_logo_path():
    """Get the best available logo path (user custom > bundled > default)"""
    # First check user's custom logo
    p = find_user_logo_path()
    if p:
        return p
    
    # Then check bundled logo files
    for fn in ("logo.jpg", "logo.png", "logo.jpeg", "logo.gif", "logo.bmp"):
        cand = resource_path(fn)
        if os.path.exists(cand):
            return cand
    
    # Finally check current directory
    for fn in ("logo.jpg", "logo.png", "logo.jpeg", "logo.gif", "logo.bmp"):
        cand = os.path.join(os.path.abspath("."), fn)
        if os.path.exists(cand):
            return cand
    
    # Return default path (will be handled gracefully)
    return os.path.join(os.path.abspath("."), "logo.jpg")

def save_logo(file_storage):
    """Save uploaded logo file, removing any existing logos first"""
    filename = secure_filename(file_storage.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".png",".jpg",".jpeg",".gif",".bmp"):
        raise ValueError("Formato de logo no soportado.")
    
    # Remove any existing logo files
    for e in (".png",".jpg",".jpeg",".gif",".bmp"):
        p = os.path.join(USER_DATA_DIR, "logo"+e)
        if os.path.exists(p):
            try: 
                os.remove(p)
            except: 
                pass
    
    # Save new logo
    dst = os.path.join(USER_DATA_DIR, "logo"+ext)
    file_storage.save(dst)
    return dst

# --------- Carga/normalización de Excel ----------
def ensure_file_from_bundle_or_local(dst_path, basename, empty_df_columns=None):
    """Ensure file exists, copying from bundle or local directory if needed"""
    if os.path.exists(dst_path): 
        return
    
    # Try bundle first (PyInstaller)
    src1 = resource_path(basename)
    if os.path.exists(src1):
        shutil.copyfile(src1, dst_path)
        return
    
    # Try current directory
    src2 = os.path.join(os.path.abspath("."), basename)
    if os.path.exists(src2):
        shutil.copyfile(src2, dst_path)
        return
    
    # Create empty file with proper columns if none found
    if empty_df_columns is not None:
        pd.DataFrame(columns=empty_df_columns).to_excel(dst_path, index=False)

def ensure_all_files():
    ensure_file_from_bundle_or_local(EXCEL_PATH, DATA_BASENAME, empty_df_columns=BASE_COLUMNS_STD)
    ensure_file_from_bundle_or_local(EXCEL_EXTRA_PATH, EXTRA_BASENAME, empty_df_columns=BASE_COLUMNS_STD + EXTRA_COLUMNS)
    _ensure_users()

def load_file(path):
    try:
        if str(path).lower().endswith(".csv"):
            return pd.read_csv(path)
        return pd.read_excel(path)
    except Exception:
        return pd.DataFrame()

def df_to_upper(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        out[c] = out[c].astype(str).replace("nan", "")
    for c in _TEXT_COLS:
        if c in out.columns:
            out[c] = out[c].astype(str).str.upper()
    return out

def smart_abbrev(text: str, max_len: int = 18) -> str:
    """Create smart abbreviation of text, trying acronym first, then truncation"""
    t = (str(text) or "").strip()
    if len(t) <= max_len: 
        return t
    
    # Try to create acronym from uppercase words
    import re as _re
    words = _re.findall(r"[A-ZÁÉÍÓÚÜÑ]+", t.upper())
    acr = "".join(w[0] for w in words) if words else t[:3]
    
    if 3 <= len(acr) <= max_len:
        return acr
    else:
        return t[: max_len-1] + "…"

def normalize_from_main(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    lower = {str(c).strip().lower(): c for c in df.columns}
    def pick(*cands):
        for c in cands:
            if c in lower: return lower[c]
        return None
    codigo  = pick("código producto","codigo producto","cod","codigo","sku")
    prod    = pick("producto (marca comercial)","producto","marca comercial","nombre")
    prin    = pick("principio activo","p. activo","activo")
    digemid = pick("n° digemid","no digemid","numero digemid","registro digemid","n°  digemid")
    lab     = pick("laboratorio / fabricante","laboratorio","fabricante","proveedor","lab")
    pres    = pick("presentación","presentacion","contenido")
    precio  = pick("precio","precio (s/)","precio s/","precio s/.","monto")
    farma   = pick("farmacia / fuente","farmacia","fuente","botica","cadena","tienda")
    enlace  = pick("enlace","link","url")
    out = pd.DataFrame({
        "CÓDIGO PRODUCTO":           df[codigo]  if codigo  in df.columns else "",
        "Producto (Marca comercial)":df[prod]    if prod    in df.columns else "",
        "Principio Activo":          df[prin]    if prin    in df.columns else "",
        "N° DIGEMID":                df[digemid] if digemid in df.columns else "",
        "Laboratorio / Fabricante":  df[lab]     if lab     in df.columns else "",
        "Presentación":              df[pres]    if pres    in df.columns else "",
        "Precio":                    df[precio]  if precio  in df.columns else "",
        "Farmacia / Fuente":         df[farma]   if farma   in df.columns else "",
        "Enlace":                    df[enlace]  if enlace  in df.columns else "",
    })
    for ex in EXTRA_COLUMNS:
        out[ex] = df[lower[ex.lower()]] if ex.lower() in lower else ""
    for c in out.columns:
        out[c] = out[c].astype(str).replace("nan", "")
    out["CÓDIGO PRODUCTO"] = out["CÓDIGO PRODUCTO"].where(
        out["CÓDIGO PRODUCTO"].astype(str).str.strip() != "", out["N° DIGEMID"]
    )
    out["N° DIGEMID"] = out["CÓDIGO PRODUCTO"]
    return out

def normalize_from_extra(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    lower = {str(c).strip().lower(): c for c in df.columns}
    def find_key(*alts):
        for a in alts:
            if a in lower: return lower[a]
        for k in list(lower.keys()):
            if ("c" in k or "cod" in k) and "dig" in k: return lower[k]
        return None
    col_grupo = find_key("grupo")
    col_cdig  = find_key("códig","codig","còdigo","codigo","código","c¢dig")
    col_nom   = find_key("nombre del producto","nombre","producto")
    col_lab_abrev = find_key("laboratorio abreviado","lab. abreviado","laboratorio abrev")
    col_lab_full  = find_key("laboratorio precio","laboratorio","lab precio","lab completo")
    out = pd.DataFrame({
        "CÓDIGO PRODUCTO":           "",
        "Producto (Marca comercial)":df[col_nom] if col_nom else "",
        "Principio Activo":          "",
        "N° DIGEMID":                df[col_cdig] if col_cdig else "",
        "Laboratorio / Fabricante":  df[col_lab_abrev] if col_lab_abrev else "",
        "Presentación":              "",
        "Precio":                    df[find_key("precio")] if find_key("precio") else "",
        "Farmacia / Fuente":         "",
        "Enlace":                    "",
        "GRUPO":                     df[col_grupo] if col_grupo else "",
        "Laboratorio Abreviado":     df[col_lab_abrev] if col_lab_abrev else "",
        "LABORATORIO PRECIO":        df[col_lab_full] if col_lab_full else "",
    })
    for c in out.columns:
        out[c] = out[c].astype(str).replace("nan", "")
    out["CÓDIGO PRODUCTO"] = out["N° DIGEMID"]
    return out

def load_normalized(path, which="main") -> pd.DataFrame:
    raw = load_file(path)
    if raw.empty:
        return pd.DataFrame(columns=BASE_COLUMNS_STD + EXTRA_COLUMNS)
    df = normalize_from_main(raw) if which == "main" else normalize_from_extra(raw)
    df_up = df_to_upper(df)
    if "Enlace" in df.columns:
        df_up["Enlace"] = df["Enlace"].astype(str).replace("nan", "")
    return df_up

def combo_df():
    ensure_all_files()
    df_main  = load_normalized(EXCEL_PATH, "main")
    df_extra = load_normalized(EXCEL_EXTRA_PATH, "extra")

    for df in (df_main, df_extra):
        if "CÓDIGO PRODUCTO" not in df.columns: df["CÓDIGO PRODUCTO"] = ""
        if "N° DIGEMID" not in df.columns:      df["N° DIGEMID"] = ""
        df["CÓDIGO PRODUCTO"] = df["CÓDIGO PRODUCTO"].where(
            df["CÓDIGO PRODUCTO"].astype(str).str.strip() != "", df["N° DIGEMID"]
        )
        df["N° DIGEMID"] = df["CÓDIGO PRODUCTO"]

    for ex in EXTRA_COLUMNS:
        if ex not in df_main.columns:  df_main[ex]  = ""
        if ex not in df_extra.columns: df_extra[ex] = ""
    
    # Ensure LABORATORIO PRECIO column exists
    if "LABORATORIO PRECIO" not in df_main.columns:
        df_main["LABORATORIO PRECIO"] = df_main["Laboratorio / Fabricante"]
    if "LABORATORIO PRECIO" not in df_extra.columns:
        df_extra["LABORATORIO PRECIO"] = df_extra["Laboratorio / Fabricante"]
    
    # Ensure Laboratorio Abreviado column exists with smart abbreviations
    if "Laboratorio Abreviado" not in df_main.columns:
        df_main["Laboratorio Abreviado"] = df_main["Laboratorio / Fabricante"].apply(lambda x: smart_abbrev(x, 18))
    if "Laboratorio Abreviado" not in df_extra.columns:
        df_extra["Laboratorio Abreviado"] = df_extra["Laboratorio / Fabricante"].apply(lambda x: smart_abbrev(x, 18))

    a = df_main.copy();  a["_ORIGEN"]="BASE"
    b = df_extra.copy(); b["_ORIGEN"]="EXTRA"
    return pd.concat([a, b], ignore_index=True)

# --------- Scraping ----------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HDRS = {"User-Agent": UA, "Accept-Language":"es-PE,es;q=0.9,en;q=0.8"}

# Improved price regex patterns for Peruvian pharmacies
RE_PRICE_PATTERNS = [
    re.compile(r"S/\.?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)", re.IGNORECASE),
    re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*S/\.?", re.IGNORECASE),
    re.compile(r"Precio[:\s]*S/\.?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)", re.IGNORECASE),
    re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*soles?", re.IGNORECASE),
    re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*PEN", re.IGNORECASE),
    re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)", re.IGNORECASE),  # Generic number pattern
]

# Real Peruvian pharmacy websites with specific search URLs and selectors
PERUVIAN_PHARMACIES = [
    {
        "name": "Mifarma",
        "base_url": "https://www.mifarma.com.pe",
        "search_url": "https://www.mifarma.com.pe/buscador?keyword={query}",
        "price_selectors": [
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".amount", ".cost", ".valor", ".precio-actual",
            "[class*='amount']", "[class*='cost']", "[class*='valor']"
        ],
        "product_selectors": [
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']",
            ".search-result", ".result-item"
        ]
    },
    {
        "name": "Inkafarma", 
        "base_url": "https://inkafarma.pe",
        "search_url": "https://inkafarma.pe/buscador?keyword={query}",
        "price_selectors": [
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".amount", ".cost", ".valor", ".precio-actual",
            "[class*='amount']", "[class*='cost']", "[class*='valor']"
        ],
        "product_selectors": [
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']",
            ".search-result", ".result-item"
        ]
    },
    {
        "name": "Boticas y Salud",
        "base_url": "https://www.boticasysalud.com",
        "search_url": "https://www.boticasysalud.com/tienda/busqueda?q={query}",
        "price_selectors": [
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".amount", ".cost", ".valor", ".precio-actual",
            "[class*='amount']", "[class*='cost']", "[class*='valor']",
            ".price-current", ".current-price"
        ],
        "product_selectors": [
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']",
            ".search-result", ".result-item", ".product-card"
        ]
    },
    {
        "name": "Boticas Perú",
        "base_url": "https://boticasperu.pe",
        "search_url": "https://boticasperu.pe/catalogsearch/result/?q={query}",
        "price_selectors": [
            ".price", ".precio", "[class*='price']", "[class*='precio']"
        ],
        "product_selectors": [
            ".product-item", ".product", ".item", ".producto"
        ]
    }
]

def ddg_results(q, max_urls=15, timeout=10):
    """Get search results from DuckDuckGo"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    
    try:
        url = "https://duckduckgo.com/html/"
        params = {"q": f"{q} precio farmacia peru comprar"}
        r = requests.get(url, params=params, headers=HDRS, timeout=timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        out = []
        for a in soup.select("a.result__a[href]"):
            href = a.get("href")
            if href and href.startswith("http"):
                out.append(href)
            if len(out) >= max_urls:
                break
        return out
    except Exception as e:
        print(f"Error in ddg_results: {e}")
        return []

def google_results(q, max_urls=15, timeout=10):
    """Get search results from Google"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    
    try:
        url = "https://www.google.com/search"
        params = {
            "q": f"{q} precio farmacia peru",
            "num": max_urls,
            "hl": "es",
            "gl": "pe"
        }
        r = requests.get(url, params=params, headers=HDRS, timeout=timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        out = []
        
        # Try different selectors for Google results
        selectors = [
            "div.g a[href^='http']",
            "a[href^='http']:not([href*='google'])",
            ".yuRUbf a[href^='http']"
        ]
        
        for selector in selectors:
            for a in soup.select(selector):
                href = a.get("href")
                if href and href.startswith("http") and "google" not in href:
                    out.append(href)
                if len(out) >= max_urls:
                    break
            if len(out) >= max_urls:
                break
        return out
    except Exception as e:
        print(f"Error in google_results: {e}")
        return []

def normalize_price(s: str) -> str:
    """Extract and normalize price from text"""
    if not s:
        return ""
    
    s = s.replace("\xa0", " ").replace("\n", " ").strip()
    
    # Try different price patterns
    for pattern in RE_PRICE_PATTERNS:
        match = pattern.search(s)
        if match:
            price_str = match.group(1)
            # Clean up the price
            price_str = price_str.replace(",", ".")
            # Ensure it's a valid price
            try:
                price_num = float(price_str)
                if 0.01 <= price_num <= 10000:  # Reasonable price range
                    return f"S/ {price_str}"
            except ValueError:
                continue
    
    return ""

def extract_multiple_products(html: str, base_url: str, pharmacy_info: dict) -> list:
    """Extract multiple products from a search results page"""
    products = []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return products
    
    try:
        soup = BeautifulSoup(html, "lxml")
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Use pharmacy-specific product selectors
        product_selectors = pharmacy_info.get("product_selectors", [
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']"
        ])
        
        product_containers = []
        for selector in product_selectors:
            containers = soup.select(selector)
            if containers:
                product_containers = containers
                print(f"    Found {len(containers)} containers with selector: {selector}")
                break
        
        # If no specific product containers found, look for price elements
        if not product_containers:
            # Look for any element containing a price
            price_elements = []
            for selector in pharmacy_info.get("price_selectors", [".price", ".precio"]):
                elements = soup.select(selector)
                price_elements.extend(elements)
                if elements:
                    print(f"    Found {len(elements)} price elements with selector: {selector}")
            
            if price_elements:
                # Group nearby elements as products
                for price_elem in price_elements[:10]:  # Limit to first 10
                    product_info = extract_single_product_from_element(price_elem, base_url)
                    if product_info:
                        products.append(product_info)
        else:
            # Extract from product containers
            for container in product_containers[:10]:  # Limit to first 10
                product_info = extract_single_product_from_container(container, base_url, pharmacy_info)
                if product_info:
                    products.append(product_info)
        
        # If still no products found, try to extract from text patterns
        if not products:
            print(f"    No products found with selectors, trying text extraction...")
            products = extract_products_from_text(soup.get_text(), base_url, pharmacy_info)
        
        return products
    except Exception as e:
        print(f"Error extracting multiple products: {e}")
        return products

def extract_single_product_from_element(price_elem, base_url: str) -> dict:
    """Extract product info from a price element"""
    try:
        price_text = price_elem.get_text().strip()
        price = normalize_price(price_text)
        if not price:
            return {}
        
        # Try to find product name in nearby elements
        product_name = ""
        parent = price_elem.parent
        for _ in range(5):  # Go up 5 levels to find product name
            if parent:
                # Look for text elements, links, and headings
                text_elements = parent.find_all(text=True, recursive=False)
                for text in text_elements:
                    text = text.strip()
                    if len(text) > 5 and len(text) < 150 and text != price_text and not text.isdigit():
                        # Skip common non-product text
                        if not any(skip in text.lower() for skip in ['agregar', 'comprar', 'ver', 'más', 'menos', 'stock', 'disponible']):
                            product_name = text
                            break
                
                # Also look for links and headings
                if not product_name:
                    for tag in parent.find_all(['a', 'h1', 'h2', 'h3', 'h4', 'span', 'div']):
                        tag_text = tag.get_text().strip()
                        if len(tag_text) > 5 and len(tag_text) < 150 and tag_text != price_text:
                            if not any(skip in tag_text.lower() for skip in ['agregar', 'comprar', 'ver', 'más', 'menos', 'stock', 'disponible']):
                                product_name = tag_text
                                break
                
                if product_name:
                    break
                parent = parent.parent
        
        # Clean up product name
        if product_name:
            product_name = re.sub(r'\s+', ' ', product_name)  # Remove extra spaces
            product_name = product_name.strip()
        
        return {
            "name": product_name or "Producto",
            "price": price,
            "url": base_url
        }
    except Exception:
        return {}

def extract_products_from_text(text: str, base_url: str, pharmacy_info: dict) -> list:
    """Extract products from text patterns when selectors fail"""
    products = []
    try:
        import re
        
        # Look for price patterns in the text
        price_patterns = [
            r"S/\.?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)",
            r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*S/\.?",
            r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*soles?"
        ]
        
        found_prices = []
        for pattern in price_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                try:
                    price_num = float(match.replace(",", "."))
                    if 0.01 <= price_num <= 10000:  # Reasonable price range
                        found_prices.append(f"S/ {match}")
                except ValueError:
                    continue
        
        print(f"    Found {len(found_prices)} price patterns: {found_prices[:3]}")
        
        # Try to find product names near prices
        lines = text.split('\n')
        for i, line in enumerate(lines):
            for price in found_prices[:5]:  # Limit to first 5 prices
                if price.replace("S/ ", "") in line:
                    # Look for product name in nearby lines
                    product_name = ""
                    for j in range(max(0, i-3), min(len(lines), i+3)):
                        nearby_line = lines[j].strip()
                        if (len(nearby_line) > 5 and len(nearby_line) < 150 and 
                            nearby_line != price and not nearby_line.isdigit()):
                            if not any(skip in nearby_line.lower() for skip in 
                                     ['agregar', 'comprar', 'ver', 'más', 'menos', 'stock', 'disponible', 'precio', 'soles']):
                                product_name = nearby_line
                                break
                    
                    if product_name:
                        products.append({
                            "name": product_name,
                            "price": price,
                            "url": base_url
                        })
                        print(f"    ✓ Extracted: {product_name} - {price}")
                        break
        
        # If still no products, try to create generic products with found prices
        if not products and found_prices:
            print(f"    Creating generic products with found prices...")
            for i, price in enumerate(found_prices[:3]):
                products.append({
                    "name": f"Producto {pharmacy_info['name']} {i+1}",
                    "price": price,
                    "url": base_url
                })
                print(f"    ✓ Created: Producto {pharmacy_info['name']} {i+1} - {price}")
        
        return products[:5]  # Limit to 5 products
        
    except Exception as e:
        print(f"Error extracting from text: {e}")
        return products

def extract_single_product_from_container(container, base_url: str, pharmacy_info: dict = None) -> dict:
    """Extract product info from a product container"""
    try:
        # Look for price in the container using pharmacy-specific selectors
        price = ""
        price_selectors = pharmacy_info.get("price_selectors", [".price", ".precio", "[class*='price']", "[class*='precio']"]) if pharmacy_info else [".price", ".precio", "[class*='price']", "[class*='precio']"]
        for selector in price_selectors:
            price_elem = container.select_one(selector)
            if price_elem:
                price = normalize_price(price_elem.get_text().strip())
                if price:
                    break
        
        if not price:
            return {}
        
        # Look for product name with more comprehensive selectors
        product_name = ""
        name_selectors = [
            ".product-name", ".product-title", ".item-name", ".nombre", ".title",
            "h1", "h2", "h3", "h4", "h5", "h6",
            "a[href]", ".product-link", ".item-link",
            "[class*='product']", "[class*='item']", "[class*='nombre']",
            ".name", ".producto", ".medicamento"
        ]
        
        for selector in name_selectors:
            name_elem = container.select_one(selector)
            if name_elem:
                name_text = name_elem.get_text().strip()
                if len(name_text) > 5 and len(name_text) < 150:
                    # Skip common non-product text
                    if not any(skip in name_text.lower() for skip in ['agregar', 'comprar', 'ver', 'más', 'menos', 'stock', 'disponible', 'carrito']):
                        product_name = name_text
                        break
        
        # If still no name, try to extract from the container's text
        if not product_name:
            container_text = container.get_text().strip()
            lines = [line.strip() for line in container_text.split('\n') if line.strip()]
            for line in lines:
                if len(line) > 5 and len(line) < 150 and line != price:
                    if not any(skip in line.lower() for skip in ['agregar', 'comprar', 'ver', 'más', 'menos', 'stock', 'disponible', 'carrito']):
                        product_name = line
                        break
        
        # Clean up product name
        if product_name:
            product_name = re.sub(r'\s+', ' ', product_name)  # Remove extra spaces
            product_name = product_name.strip()
        
        # Look for product URL
        product_url = base_url
        link_elem = container.select_one("a[href]")
        if link_elem:
            href = link_elem.get("href")
            if href:
                if href.startswith("http"):
                    product_url = href
                elif href.startswith("/"):
                    from urllib.parse import urljoin
                    product_url = urljoin(base_url, href)
        
        return {
            "name": product_name or "Producto",
            "price": price,
            "url": product_url
        }
    except Exception:
        return {}

def extract_product_info(html: str, url: str) -> dict:
    """Extract product information from HTML"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}
    
    try:
        soup = BeautifulSoup(html, "lxml")
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        text = soup.get_text()
        
        # Look for price in the text
        price = normalize_price(text)
        if not price:
            # Try to find price in specific elements
            price_selectors = [
                ".price", ".precio", ".cost", ".valor",
                "[class*='price']", "[class*='precio']", "[class*='cost']",
                "[id*='price']", "[id*='precio']", "[id*='cost']"
            ]
            
            for selector in price_selectors:
                elements = soup.select(selector)
                for element in elements:
                    price_text = element.get_text().strip()
                    price = normalize_price(price_text)
                    if price:
                        break
                if price:
                    break
        
        if not price:
            return {}
        
        # Try to find product name in title or headings
        product_name = ""
        title = soup.find("title")
        if title:
            product_name = title.get_text().strip()
        
        # Look for product name in h1, h2 tags
        if not product_name:
            for tag in soup.find_all(["h1", "h2", "h3"]):
                text_content = tag.get_text().strip()
                if len(text_content) > 5 and len(text_content) < 100:
                    product_name = text_content
                    break
        
        # Look for product name in product-specific elements
        if not product_name:
            product_selectors = [
                ".product-name", ".product-title", ".item-name", ".producto-nombre",
                "[class*='product']", "[class*='item']", "[class*='nombre']"
            ]
            
            for selector in product_selectors:
                elements = soup.select(selector)
                for element in elements:
                    name_text = element.get_text().strip()
                    if len(name_text) > 5 and len(name_text) < 100:
                        product_name = name_text
                        break
                if product_name:
                    break
        
        # Clean up product name
        if product_name:
            product_name = re.sub(r'\s+', ' ', product_name)
            product_name = product_name[:100]  # Limit length
        
        return {
            "price": price,
            "product_name": product_name,
            "text": text[:500]  # First 500 chars for debugging
        }
    except Exception as e:
        print(f"Error extracting product info: {e}")
        return {}

def search_pharmacy_direct(query: str, pharmacy_info: dict, timeout=8) -> list:
    """Search directly on a pharmacy website using specific search URL"""
    results = []
    try:
        search_url = pharmacy_info["search_url"].format(query=query)
        print(f"    🔍 Searching: {pharmacy_info['name']} - {search_url}")
        
        r = requests.get(search_url, headers=HDRS, timeout=timeout)
        if r.status_code == 200:
            # Extract multiple products from the search results page
            products = extract_multiple_products(r.text, search_url, pharmacy_info)
            for product in products:
                if product.get("price"):
                    results.append({
                        "Producto (Marca comercial)": product.get("name", query.upper()),
                        "Precio": product["price"],
                        "Farmacia / Fuente": pharmacy_info["name"],
                        "Enlace": product.get("url", search_url),
                        "_ORIGEN": "WEB"
                    })
        else:
            print(f"    ✗ {pharmacy_info['name']}: HTTP {r.status_code}")
    except Exception as e:
        print(f"    ✗ {pharmacy_info['name']}: {e}")
    
    return results

def save_web_results_to_csv(web_results: list):
    """Save web scraping results to the main CSV for faster future searches"""
    if not web_results:
        return
    
    try:
        # Load current main CSV
        df_main = load_normalized(EXCEL_PATH, "main")
        
        # Convert web results to DataFrame format
        new_rows = []
        for result in web_results:
            new_row = {
                "CÓDIGO PRODUCTO": "",
                "Producto (Marca comercial)": result.get("Producto (Marca comercial)", ""),
                "Principio Activo": "",
                "N° DIGEMID": "",
                "Laboratorio / Fabricante": "",
                "Presentación": "",
                "Precio": result.get("Precio", ""),
                "Farmacia / Fuente": result.get("Farmacia / Fuente", ""),
                "Enlace": result.get("Enlace", ""),
                "GRUPO": "",
                "Laboratorio Abreviado": "",
                "LABORATORIO PRECIO": "",
                "_ORIGEN": "WEB_CACHED"
            }
            new_rows.append(new_row)
        
        # Add new rows to main DataFrame
        if new_rows:
            df_new = pd.DataFrame(new_rows)
            df_main = pd.concat([df_main, df_new], ignore_index=True)
            
            # Remove duplicates based on product name, price, and pharmacy
            df_main = df_main.drop_duplicates(
                subset=["Producto (Marca comercial)", "Precio", "Farmacia / Fuente"], 
                keep="first"
            )
            
            # Save back to CSV
            df_main.to_excel(EXCEL_PATH, index=False)
            print(f"💾 Saved {len(new_rows)} web results to main CSV")
    
    except Exception as e:
        print(f"Error saving web results to CSV: {e}")

def fetch_prices_online(query: str, max_sites: int = 30, timeout=8):
    """Enhanced web scraping for Peruvian pharmacies - Comprehensive version"""
    print(f"🔍 Searching for: {query}")
    out, seen = [], set()
    
    # 1. Search directly on ALL Peruvian pharmacy websites
    print("📱 Searching Peruvian pharmacies directly...")
    for i, pharmacy_info in enumerate(PERUVIAN_PHARMACIES, 1):
        print(f"  [{i}/{len(PERUVIAN_PHARMACIES)}] Searching {pharmacy_info['name']}...")
        try:
            results = search_pharmacy_direct(query, pharmacy_info, timeout=timeout)
            for result in results:
                key = (result["Farmacia / Fuente"], result["Precio"], result["Enlace"])
                if key not in seen:
                    seen.add(key)
                    out.append(result)
                    print(f"    ✓ Found: {result['Precio']} - {result['Producto (Marca comercial)']} at {result['Farmacia / Fuente']}")
                    if len(out) >= max_sites:  # Stop if we have enough results
                        break
            if len(out) >= max_sites:
                break
        except Exception as e:
            print(f"    ✗ Error with {pharmacy_info['name']}: {e}")
            continue
    
    # 2. Search using DuckDuckGo if we need more results
    if len(out) < 10:
        print("🦆 Searching with DuckDuckGo...")
        try:
            ddg_urls = ddg_results(query, max_urls=8, timeout=timeout)
            for i, url in enumerate(ddg_urls[:5], 1):  # Limit to first 5 URLs
                print(f"  [{i}/5] Checking DuckDuckGo result...")
                try:
                    r = requests.get(url, headers=HDRS, timeout=timeout)
                    if r.status_code == 200:
                        info = extract_product_info(r.text, url)
                        if info.get("price"):
                            dom = urlparse(url).netloc.replace("www.", "")
                            key = (dom, info["price"], url)
                            if key not in seen:
                                seen.add(key)
                                out.append({
                                    "Producto (Marca comercial)": query.upper(),
                                    "Precio": info["price"],
                                    "Farmacia / Fuente": dom,
                                    "Enlace": url,
                                    "_ORIGEN": "WEB"
                                })
                                print(f"    ✓ Found: {info['price']} at {dom}")
                except Exception as e:
                    print(f"    ✗ Error with {url}: {e}")
                    continue
        except Exception as e:
            print(f"  ✗ DuckDuckGo error: {e}")
    
    # 3. Save results to main CSV for future searches
    if out:
        save_web_results_to_csv(out)
    
    print(f"🎯 Total results found: {len(out)}")
    return out[:max_sites]  # Limit results

# --------- Helpers front ----------
def extract_price_number(txt):
    if not isinstance(txt, str): return None
    txt = txt.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d{2})?)", txt)
    return float(m.group(1)) if m else None

def sort_rows(rows, col, asc=True):
    if col == "Precio":
        return sorted(rows, key=lambda r: (extract_price_number(r.get("Precio","")) is None,
                                           extract_price_number(r.get("Precio",""))), reverse=not asc)
    return sorted(rows, key=lambda r: str(r.get(col,"")).upper(), reverse=not asc)

def last_modified_text(path):
    try:
        ts = os.path.getmtime(path)
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "—"

# --------- Estado por sesión ----------
def get_state():
    if "state" not in session:
        session["state"] = {
            "rows": [],
            "filters": {"pharmacies": []},
            "sort": {"col":"Precio","asc":True},
        }
    return session["state"]

# --------- Rutas Auth ----------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","").strip()
        role = check_credentials(u,p)
        if role:
            session["user"] = {"username":u, "role":role}
            return redirect(url_for("home"))
        return _html_login(error="Usuario o contraseña incorrectos.")
    return _html_login()

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# --------- Rutas UI ----------
@app.route("/")
def home():
    if "user" not in session:
        return redirect(url_for("login"))
    return _html_home()

@app.route("/static/logo")
def static_logo():
    """Serve the logo image with fallback to transparent 1x1 PNG"""
    p = current_logo_path()
    if p and os.path.exists(p):
        try:
            return send_file(p)
        except Exception:
            pass
    
    # Fallback: 1x1 transparent PNG if no logo found
    png1x1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
              b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc``\x00"
              b"\x00\x00\x02\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82")
    return send_file(io.BytesIO(png1x1), mimetype="image/png")

# --------- API: Búsqueda y Vista ----------
@app.route("/api/search")
def api_search():
    """Busca en BASE/EXTRA y/o en WEB, opcionalmente combinados."""
    if "user" not in session: 
        return jsonify({"error":"unauth"}), 401
    
    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "PRODUCTO").upper()  # PRODUCTO | PRINCIPIO ACTIVO | AMBOS
    mode  = (request.args.get("mode") or "base").lower()       # base | web | both
    
    print(f"Search query: '{q}', scope: {scope}, mode: {mode}")  # Debug
    
    if not q:
        st = get_state()
        st["rows"] = []
        session.modified = True
        return jsonify({
            "rows":[], 
            "pharmacies":[], 
            "base_last": last_modified_text(EXCEL_PATH),
            "extra_last": last_modified_text(EXCEL_EXTRA_PATH)
        })

    rows = []

    # Buscar en BASE/EXTRA
    if mode in ("base","both"):
        try:
            df = combo_df()
            print(f"Loaded {len(df)} rows from combo_df")  # Debug
            
            if not df.empty:
                qU = q.upper()
                if scope == "PRODUCTO":
                    mask = df["Producto (Marca comercial)"].astype(str).str.contains(qU, regex=False, na=False)
                elif scope == "PRINCIPIO ACTIVO":
                    mask = df["Principio Activo"].astype(str).str.contains(qU, regex=False, na=False)
                else: # AMBOS
                    mask = (df["Producto (Marca comercial)"].astype(str).str.contains(qU, regex=False, na=False) | 
                           df["Principio Activo"].astype(str).str.contains(qU, regex=False, na=False))
                
                df_filtered = df[mask]
                print(f"Found {len(df_filtered)} matching rows")  # Debug
                
                for _, r in df_filtered.iterrows():
                    rows.append({
                        "CÓDIGO PRODUCTO":        r.get("CÓDIGO PRODUCTO",""),
                        "Producto (Marca comercial)": r.get("Producto (Marca comercial)",""),
                        "Principio Activo":       r.get("Principio Activo",""),
                        "N° DIGEMID":             r.get("N° DIGEMID",""),
                        "Laboratorio / Fabricante": r.get("Laboratorio / Fabricante",""),
                        "Presentación":           r.get("Presentación",""),
                        "Precio":                 r.get("Precio",""),
                        "Farmacia / Fuente":      r.get("Farmacia / Fuente",""),
                        "Enlace":                 r.get("Enlace",""),
                        "GRUPO":                  r.get("GRUPO",""),
                        "Laboratorio Abreviado":  r.get("Laboratorio Abreviado",""),
                        "LABORATORIO PRECIO":     r.get("LABORATORIO PRECIO",""),
                        "_ORIGEN":                r.get("_ORIGEN","BASE"),
                    })
        except Exception as e:
            print(f"Error in base search: {e}")  # Debug

    # Buscar en WEB
    if mode in ("web","both"):
        try:
            web_rows = fetch_prices_online(q, max_sites=40)
            print(f"Found {len(web_rows)} web results")  # Debug
            
            # homogeniza columnas extra
            for r in web_rows:
                r.setdefault("CÓDIGO PRODUCTO","")
                r.setdefault("Principio Activo","")
                r.setdefault("N° DIGEMID","")
                r.setdefault("Laboratorio / Fabricante","")
                r.setdefault("Presentación","")
                r.setdefault("GRUPO","")
                r.setdefault("Laboratorio Abreviado","")
                r.setdefault("LABORATORIO PRECIO","")
            rows += web_rows
        except Exception as e:
            print(f"Error in web search: {e}")  # Debug

    # Guardar en sesión
    st = get_state()
    st["rows"] = rows
    st["filters"]["pharmacies"] = []  # limpiar
    st["sort"] = {"col":"Precio","asc":True}
    session.modified = True

    print(f"Total results: {len(rows)}")  # Debug

    return jsonify({
        "rows": rows,
        "pharmacies": sorted(list({r.get("Farmacia / Fuente","") for r in rows if r.get("Farmacia / Fuente")}))
                     [:200],
        "base_last":  last_modified_text(EXCEL_PATH),
        "extra_last": last_modified_text(EXCEL_EXTRA_PATH)
    })

@app.route("/api/view")
def api_view():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    st = get_state()
    rows = list(st["rows"])

    # filtros
    pharm_sel = request.args.getlist("pharmacy")
    if pharm_sel:
        rows = [r for r in rows if r.get("Farmacia / Fuente") in pharm_sel]

    # sort
    col = request.args.get("sort_col", st["sort"]["col"])
    asc = request.args.get("sort_asc","true").lower() == "true"
    rows = sort_rows(rows, col, asc)

    # paginación
    page = max(1, int(request.args.get("page", 1)))
    per  = max(5, min(100, int(request.args.get("per", 25))))
    total = len(rows)
    pages = max(1, (total + per - 1)//per)
    if page > pages: page = pages
    start = (page-1)*per
    end   = min(start+per, total)
    rows_page = rows[start:end]

    # min/max (en todo el set post-filtro farmacia)
    def minmax(all_rows):
        vals = [(extract_price_number(r.get("Precio","")), r) for r in all_rows]
        vals = [(v,r) for (v,r) in vals if v is not None]
        if not vals: return None, None
        rmin = min(vals, key=lambda x:x[0])[1]
        rmax = max(vals, key=lambda x:x[0])[1]
        return rmin, rmax

    rmin, rmax = minmax(rows)

    return jsonify({
        "rows": rows_page,
        "total": total,
        "page": page,
        "pages": pages,
        "per": per,
        "sort": {"col": col, "asc": asc},
        "selected_pharmacies": pharm_sel,
        "all_pharmacies": sorted(list({r.get("Farmacia / Fuente","") for r in st["rows"] if r.get("Farmacia / Fuente")})),
        "min_item": rmin,
        "max_item": rmax
    })

@app.route("/api/export")
def api_export():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    st = get_state()
    rows = list(st["rows"])
    pharm_sel = request.args.getlist("pharmacy")
    if pharm_sel:
        rows = [r for r in rows if r.get("Farmacia / Fuente") in pharm_sel]
    col = request.args.get("sort_col", st["sort"]["col"])
    asc = request.args.get("sort_asc","true").lower() == "true"
    rows = sort_rows(rows, col, asc)
    if not rows:
        return jsonify({"error":"no_data"}), 400

    # Orden de columnas “bonito”
    cols = [c for c in DEFAULT_UI_ORDER if any(c in r for r in rows)]
    df = pd.DataFrame(rows, columns=cols)

    fmt = request.args.get("fmt","csv").lower()
    if fmt == "xlsx":
        bio = io.BytesIO()
        with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Resultados")
            wb  = writer.book
            ws  = writer.sheets["Resultados"]
            header_fmt = wb.add_format({"bold": True, "bg_color": "#E6E6E6", "border":1})
            cell_fmt   = wb.add_format({"border":1})
            money_fmt  = wb.add_format({"border":1, "num_format": '"S/" #,##0.00'})
            for colx, colname in enumerate(df.columns):
                ws.write(0, colx, colname.upper(), header_fmt)
            for rowx in range(len(df)):
                for colx, colname in enumerate(df.columns):
                    val = df.iloc[rowx, colx]
                    fmt_cell = money_fmt if colname == "Precio" else cell_fmt
                    ws.write(rowx+1, colx, val, fmt_cell)
            # Anchos
            for colx, colname in enumerate(df.columns):
                width = max(12, min(45, int(df[colname].astype(str).str.len().clip(upper=80).max() + 4)))
                ws.set_column(colx, colx, width)
        bio.seek(0)
        fname = f"medicamentos_{datetime.datetime.now():%Y%m%d_%H%M%S}.xlsx"
        return send_file(bio, as_attachment=True, download_name=fname,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # CSV por defecto
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    bio = io.BytesIO(csv_bytes)
    fname = f"medicamentos_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv"
    return send_file(bio, as_attachment=True, download_name=fname, mimetype="text/csv; charset=utf-8")

# --------- Administración (solo admin) ----------
@app.route("/api/admin/upload_base", methods=["POST"])
def api_admin_upload_base():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    which = (request.args.get("which") or "main").lower() # main | extra
    f = request.files.get("file")
    if not f: return jsonify({"error":"no_file"}), 400
    tmp = io.BytesIO(f.read()); tmp.seek(0)
    try:
        # lee y normaliza
        if f.filename.lower().endswith(".csv"):
            raw = pd.read_csv(tmp)
        else:
            raw = pd.read_excel(tmp)
        df = normalize_from_main(raw) if which=="main" else normalize_from_extra(raw)
        dst = EXCEL_PATH if which=="main" else EXCEL_EXTRA_PATH
        df.to_excel(dst, index=False)
        return jsonify({"ok":True, "which":which})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/admin/upload_logo", methods=["POST"])
def api_admin_upload_logo():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    f = request.files.get("file")
    if not f: return jsonify({"error":"no_file"}), 400
    try:
        dst = save_logo(f)
        return jsonify({"ok":True, "path":dst})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

# --------- CRUD Operations (solo admin) ----------
@app.route("/api/admin/add_row", methods=["POST"])
def api_admin_add_row():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error":"no_data"}), 400
        
        # Load current data
        df_main = load_normalized(EXCEL_PATH, "main")
        
        # Create new row
        new_row = {}
        for col in BASE_COLUMNS_STD + EXTRA_COLUMNS:
            value = data.get(col, "")
            if col in _TEXT_COLS:
                value = str(value).upper()
            new_row[col] = value
        
        # Handle CÓDIGO PRODUCTO and N° DIGEMID
        if not str(new_row.get("CÓDIGO PRODUCTO","")).strip() and str(new_row.get("N° DIGEMID","")).strip():
            new_row["CÓDIGO PRODUCTO"] = new_row["N° DIGEMID"]
        new_row["N° DIGEMID"] = new_row.get("CÓDIGO PRODUCTO","")
        
        # Add to dataframe
        df_main = pd.concat([df_main, pd.DataFrame([new_row])], ignore_index=True)
        df_main.to_excel(EXCEL_PATH, index=False)
        
        return jsonify({"ok":True, "message":"Registro agregado correctamente"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/admin/edit_row", methods=["POST"])
def api_admin_edit_row():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        data = request.get_json()
        if not data or "index" not in data:
            return jsonify({"error":"no_data_or_index"}), 400
        
        index = int(data["index"])
        
        # Load current data
        df_main = load_normalized(EXCEL_PATH, "main")
        
        if index >= len(df_main):
            return jsonify({"error":"index_out_of_range"}), 400
        
        # Update row
        for col in BASE_COLUMNS_STD + EXTRA_COLUMNS:
            if col in data:
                value = data[col]
                if col in _TEXT_COLS:
                    value = str(value).upper()
                df_main.at[index, col] = value
        
        # Handle CÓDIGO PRODUCTO and N° DIGEMID
        if not str(df_main.at[index, "CÓDIGO PRODUCTO"]).strip() and str(df_main.at[index, "N° DIGEMID"]).strip():
            df_main.at[index, "CÓDIGO PRODUCTO"] = df_main.at[index, "N° DIGEMID"]
        df_main.at[index, "N° DIGEMID"] = df_main.at[index, "CÓDIGO PRODUCTO"]
        
        df_main.to_excel(EXCEL_PATH, index=False)
        
        return jsonify({"ok":True, "message":"Registro editado correctamente"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/admin/delete_row", methods=["POST"])
def api_admin_delete_row():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        data = request.get_json()
        if not data or "index" not in data:
            return jsonify({"error":"no_data_or_index"}), 400
        
        index = int(data["index"])
        
        # Load current data
        df_main = load_normalized(EXCEL_PATH, "main")
        
        if index >= len(df_main):
            return jsonify({"error":"index_out_of_range"}), 400
        
        # Delete row
        df_main = df_main.drop(index=index).reset_index(drop=True)
        df_main.to_excel(EXCEL_PATH, index=False)
        
        return jsonify({"ok":True, "message":"Registro eliminado correctamente"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

# --------- User Management (solo admin) ----------
@app.route("/api/admin/users", methods=["GET"])
def api_admin_get_users():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        users = load_users()
        # Remove passwords from response
        safe_users = [{"username": u["username"], "role": u["role"]} for u in users]
        return jsonify({"users": safe_users})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/admin/users", methods=["POST"])
def api_admin_add_user():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        data = request.get_json()
        if not data or not all(k in data for k in ["username", "password", "role"]):
            return jsonify({"error":"missing_fields"}), 400
        
        users = load_users()
        
        # Check if username already exists
        if any(u["username"] == data["username"] for u in users):
            return jsonify({"error":"username_exists"}), 400
        
        # Add new user
        users.append({
            "username": data["username"],
            "password": data["password"],
            "role": data["role"]
        })
        
        save_users(users)
        return jsonify({"ok":True, "message":"Usuario agregado correctamente"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/admin/users/<username>", methods=["PUT"])
def api_admin_edit_user(username):
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error":"no_data"}), 400
        
        users = load_users()
        user_found = False
        
        for u in users:
            if u["username"] == username:
                if "password" in data and data["password"]:
                    u["password"] = data["password"]
                if "role" in data:
                    u["role"] = data["role"]
                user_found = True
                break
        
        if not user_found:
            return jsonify({"error":"user_not_found"}), 404
        
        save_users(users)
        return jsonify({"ok":True, "message":"Usuario editado correctamente"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/admin/users/<username>", methods=["DELETE"])
def api_admin_delete_user(username):
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        if username == "admin":
            return jsonify({"error":"cannot_delete_admin"}), 400
        
        users = load_users()
        original_count = len(users)
        users = [u for u in users if u["username"] != username]
        
        if len(users) == original_count:
            return jsonify({"error":"user_not_found"}), 404
        
        save_users(users)
        return jsonify({"ok":True, "message":"Usuario eliminado correctamente"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

# --------- HTMLs ----------
def _html_login(error=None):
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Login · {APP_TITLE}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root {{
  --bg:#0e2a47; --card:#101826; --muted:#7e8ea0; --acc:#1dd1a1; --txt:#e8eef6; --danger:#ff6b6b;
}}
* {{ box-sizing: border-box; }}
html,body {{ margin:0; height:100%; font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Arial; color:var(--txt);
            background:linear-gradient(135deg,#0e2a47 0%, #102a44 60%, #0a1c2f 100%); }}
.wrapper {{ min-height:100%; display:grid; place-items:center; padding:24px; }}
.card {{
  width:min(940px, 96vw); display:grid; grid-template-columns: 1.1fr 0.9fr; gap:0;
  background:linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
  border:1px solid rgba(255,255,255,0.06); border-radius:18px; overflow:hidden; box-shadow:0 10px 40px rgba(0,0,0,.35);
}}
.left {{ padding:36px 32px; background:rgba(3,12,22,.55); }}
.brand {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
.brand h1 {{ margin:0; font-size:24px; letter-spacing:.5px; }}
.brand small {{ color:var(--muted); }}
h2 {{ margin:10px 0 18px; font-size:18px; color:var(--muted); }}
label {{ display:block; font-size:14px; margin:12px 0 6px; color:#c5d3e6; }}
input[type=text], input[type=password] {{
  width:100%; padding:12px 14px; border-radius:12px; border:1px solid rgba(255,255,255,0.12);
  background:#0b1726; color:var(--txt); outline:none;
}}
input:focus {{ border-color:var(--acc); box-shadow:0 0 0 3px rgba(29,209,161,.15); }}
.actions {{ display:flex; gap:10px; margin-top:16px; }}
button {{
  padding:10px 14px; border-radius:12px; border:1px solid rgba(255,255,255,0.12);
  background:var(--acc); color:#082019; font-weight:700; cursor:pointer;
}}
button.secondary {{ background:transparent; color:var(--txt); }}
a.link {{ color:#7fe9cf; text-decoration:none; font-size:13px }}
.right {{
  background: radial-gradient(1200px 600px at 80% -10%, rgba(29,209,161,.25), transparent 60%),
              linear-gradient(180deg, #0b2644, #0a1e37);
  display:grid; place-items:center; padding:20px;
}}
.logo-container {{
  display: flex; align-items: center; justify-content: center;
  width: min(420px, 36vw); height: min(420px, 36vw);
  background: radial-gradient(closest-side, rgba(255,255,255,.12), rgba(255,255,255,.04) 60%, transparent 62%),
              conic-gradient(from 0deg, rgba(127,233,207,.15), rgba(255,255,255,.04), rgba(127,233,207,.15));
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 20px;
  box-shadow: inset 0 0 40px rgba(255,255,255,.06), 0 10px 60px rgba(0,0,0,.35);
  padding: 20px;
}}
.login-logo {{
  max-width: 80%; max-height: 80%; 
  object-fit: contain; border-radius: 12px;
  background: rgba(255,255,255,.9); padding: 8px;
  box-shadow: 0 4px 20px rgba(0,0,0,.2);
}}
.error {{ color:var(--danger); margin-top:8px; font-size:14px }}
@media (max-width:840px) {{
  .card {{ grid-template-columns:1fr; }}
  .right {{ display:none; }}
}}
</style>
</head>
<body>
<div class="wrapper">
  <div class="card">
    <div class="left">
      <div class="brand"><h1>AVision</h1><small>v {APP_VERSION}</small></div>
      <h2>A&V para decisiones inteligentes, a tiempo</h2>
      <form method="post">
        <label>Usuario</label>
        <input name="username" type="text" placeholder="admin o consulta" required>
        <label>Contraseña</label>
        <input name="password" type="password" placeholder="••••••••" required>
        <div class="actions">
          <button type="submit">Ingresar</button>
          <a class="link" href="https://ayvservice.wixsite.com/my-site-1" target="_blank">ayvservice.wixsite.com</a>
        </div>
        {"<div class='error'>"+error+"</div>" if error else ""}
      </form>
    </div>
    <div class="right">
      <div class="logo-container">
        <img src="/static/logo" alt="Logo" class="login-logo">
      </div>
    </div>
  </div>
</div>
</body>
</html>"""

def _html_home():
    user = session.get("user",{})
    role = user.get("role","consulta")
    base_last  = last_modified_text(EXCEL_PATH)
    extra_last = last_modified_text(EXCEL_EXTRA_PATH)
    admin_panel = f"""
    <details class="admin">
      <summary>⚙️ Panel Admin</summary>
      <div class="admin-grid">
        <div>
          <h4>Subir BASE (fuente.xlsx)</h4>
          <form id="formBase" enctype="multipart/form-data">
            <input type="file" name="file" accept=".xlsx,.xls,.csv" required>
            <button type="submit">Subir BASE</button>
          </form>
        </div>
        <div>
          <h4>Subir EXTRA (productos1.xlsx)</h4>
          <form id="formExtra" enctype="multipart/form-data">
            <input type="file" name="file" accept=".xlsx,.xls,.csv" required>
            <button type="submit">Subir EXTRA</button>
          </form>
        </div>
        <div>
          <h4>Cambiar LOGO</h4>
          <img src="/static/logo" alt="Logo actual" style="max-width:180px; display:block; margin-bottom:8px; background:#fff;border-radius:8px;padding:6px;">
          <form id="formLogo" enctype="multipart/form-data">
            <input type="file" name="file" accept=".png,.jpg,.jpeg,.gif,.bmp" required>
            <button type="submit">Subir LOGO</button>
          </form>
        </div>
        <div>
          <h4>Gestión de Usuarios</h4>
          <button id="btnManageUsers">👥 Gestionar Usuarios</button>
        </div>
      </div>
      <small class="muted">Últimas modificaciones · BASE: {base_last} · EXTRA: {extra_last}</small>
    </details>
    """ if role=="admin" else ""

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{APP_TITLE}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root {{ --bg:#0e2a47; --panel:#0b1726; --muted:#8aa0b8; --acc:#1dd1a1; --txt:#eaf2fb; --line:#17263a; --chip:#102136; }}
html,body {{ margin:0; height:100%; background:linear-gradient(180deg, #0e2a47, #0d1f37); color:var(--txt); font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Arial; }}
.topbar {{
  display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid var(--line);
  background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02));
}}
.brand {{ font-weight:800; letter-spacing:.3px }}
.version {{ color:var(--muted); font-size:12px; margin-left:auto; }}
.topbar img {{ height:40px; border-radius:8px; background:#fff; padding:4px; }}
button, select {{ background:var(--acc); color:#082019; border:none; padding:8px 12px; border-radius:12px; font-weight:700; cursor:pointer; }}
select, input[type=number] {{ color:#0c2238; }}
input[type=text] {{
  width:36ch; max-width:70vw; padding:10px 12px; border-radius:12px; border:1px solid rgba(255,255,255,0.14);
  background:var(--panel); color:var(--txt);
}}
.controls {{ display:flex; flex-wrap:wrap; gap:8px; padding:10px 16px; align-items:center; border-bottom:1px solid var(--line); }}
.controls .pill {{ background:var(--chip); border:1px solid var(--line); padding:8px 10px; border-radius:12px; display:flex; gap:8px; align-items:center; }}
main {{ padding:12px 16px; }}
.grid {{ display:grid; grid-template-columns: 1fr; gap:12px; }}
.table-wrap {{ background:rgba(3,12,22,.55); border:1px solid var(--line); border-radius:14px; overflow:auto; }}
table {{ width:100%; border-collapse:collapse; min-width:960px; }}
th, td {{ padding:10px 12px; border-bottom:1px solid #12243a; text-align:left; }}
th {{ position:sticky; top:0; background:#0c1b2f; z-index:1; cursor:pointer; }}
td.price {{ text-align:right; white-space:nowrap; }}
.rowlink a {{ color:#9aeed8; text-decoration:none; }}
.meta {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
.kpi {{ padding:8px 10px; background:var(--chip); border:1px solid var(--line); border-radius:12px; }}
footer {{ color:var(--muted); font-size:12px; text-align:center; padding:18px; }}
.chips {{ display:flex; gap:6px; flex-wrap:wrap; }}
.chip {{ background:var(--chip); border:1px solid var(--line); padding:5px 9px; border-radius:999px; font-size:12px; cursor:pointer; }}
.chip.sel {{ outline:2px solid rgba(29,209,161,.4); }}
.muted {{ color:var(--muted); }}
details.admin {{ background:rgba(3,12,22,.4); border:1px solid var(--line); border-radius:12px; margin:10px 16px; padding:10px; }}
details.admin summary {{ cursor:pointer; font-weight:700; }}
.admin-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:14px; margin-top:8px; }}
.admin-grid h4 {{ margin:.4rem 0 .6rem; }}
.admin-grid input[type=file] {{ display:block; margin:.4rem 0; }}

/* Modal styles */
.modal-overlay {{
  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}}
.modal-content {{
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 20px; max-width: 90vw; max-height: 90vh; overflow-y: auto;
  box-shadow: 0 10px 40px rgba(0,0,0,0.5);
}}
.modal-content h3 {{ margin: 0 0 16px; color: var(--acc); }}
.form-grid {{
  display: grid; grid-template-columns: auto 1fr; gap: 8px 12px; align-items: center;
  margin-bottom: 16px;
}}
.form-grid label {{ color: #c7d6ea; font-size: 13px; }}
.form-grid input, .form-grid select {{
  padding: 8px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--txt);
}}
.modal-buttons {{ display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }}
.modal-buttons button {{ padding: 8px 16px; }}

/* User management styles */
.user-management {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
.user-list h4, .user-form h4 {{ margin: 0 0 12px; color: var(--acc); }}
.user-item {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px; background: var(--chip); border: 1px solid var(--line);
  border-radius: 6px; margin-bottom: 6px;
}}
.user-actions {{ display: flex; gap: 4px; }}
.user-actions button {{ padding: 4px 8px; font-size: 12px; }}
.user-form form {{ display: flex; flex-direction: column; gap: 8px; }}
.user-form input, .user-form select {{ padding: 8px; border: 1px solid var(--line); border-radius: 6px; }}

/* Row selection */
tr.selected {{ background: rgba(29,209,161,0.2) !important; }}
tr:hover {{ background: rgba(255,255,255,0.05); }}
</style>
</head>
<body>
  <div class="topbar">
    <img src="/static/logo" alt="Logo">
    <div class="brand">AVision</div>
    <div style="opacity:.7">|</div>
    <div>{APP_TITLE}</div>
    <div class="version">v {APP_VERSION}</div>
    <div style="margin-left:auto; display:flex; gap:10px; align-items:center;">
      <span style="color:var(--muted); font-size:13px;">{user.get("username")} · {user.get("role")}</span>
      <a href="{url_for('logout')}" style="color:#9aeed8; text-decoration:none;">Salir</a>
    </div>
  </div>

  {admin_panel}

  <div class="controls">
    <div class="pill">
      <span>🔎</span>
      <input id="q" type="text" placeholder="Ej: paracetamol 500 mg">
      <select id="scope">
        <option>PRODUCTO</option>
        <option>PRINCIPIO ACTIVO</option>
        <option>AMBOS</option>
      </select>
      <select id="mode">
        <option value="base">BASE</option>
        <option value="web">WEB</option>
        <option value="both" selected>AMBOS</option>
      </select>
      <button id="btnSearch">Buscar</button>
    </div>
    <div class="pill">
      <label for="per" style="color:#c7d6ea; font-size:13px;">Filas/pág</label>
      <select id="per">
        <option>10</option><option selected>25</option><option>50</option><option>100</option>
      </select>
    </div>
    <div class="pill">
      <label style="color:#c7d6ea; font-size:13px;">Farmacias (filtro)</label>
      <div id="pharmChips" class="chips"></div>
    </div>
    <div class="pill">
      <button id="btnCsv">Exportar CSV</button>
      <button id="btnXlsx">Exportar XLSX</button>
    </div>
    {f'''
    <div class="pill" id="crudButtons" style="display:none;">
      <button id="btnAdd">➕ Agregar</button>
      <button id="btnEdit">✏️ Editar</button>
      <button id="btnDelete">🗑️ Eliminar</button>
    </div>
    ''' if role=="admin" else ""}
  </div>

  <main class="grid">
    <div class="meta">
      <div class="kpi" id="kpiCount">0 resultado(s)</div>
      <div class="kpi" id="kpiMin">MENOR: —</div>
      <button id="btnOpenMin" style="display:none;">Abrir (MENOR)</button>
      <div class="kpi" id="kpiMax">MAYOR: —</div>
      <button id="btnOpenMax" style="display:none;">Abrir (MAYOR)</button>
      <div class="muted" id="lastMods" style="margin-left:auto;">BASE: {base_last} · EXTRA: {extra_last}</div>
      <div style="display:flex; gap:8px; align-items:center;">
        <button id="btnPrev">◀ Ant</button>
        <div class="kpi" id="kpiPage">Pág 0/0</div>
        <button id="btnNext">Sig ▶</button>
      </div>
    </div>

    <div class="table-wrap">
      <table id="tbl">
        <thead>
          <tr>
            <th data-col="Producto (Marca comercial)">Producto</th>
            <th data-col="Principio Activo">P. Activo</th>
            <th data-col="Presentación">Presentación</th>
            <th data-col="Precio">Precio</th>
            <th data-col="Laboratorio / Fabricante">Laboratorio</th>
            <th data-col="Farmacia / Fuente">Farmacia / Fuente</th>
            <th data-col="Enlace">Enlace</th>
            <th data-col="GRUPO">Grupo</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </main>

  <footer>Hecho con ♥ · A&V · Copyright ©Relexner v {APP_VERSION}</footer>

<script src="/static/app.js"></script>
</body>
</html>"""

# ---- Main
if __name__ == "__main__":
    ensure_all_files()
    # Para desarrollo local
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
else:
    # Para producción (cuando se ejecuta con gunicorn)
    ensure_all_files()
