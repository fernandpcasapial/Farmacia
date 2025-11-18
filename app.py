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
# User-Agent actualizado para mayor compatibilidad (Chrome 142)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/142.0.7444.60 Safari/537.36")
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
            # Selectores específicos para Angular/React
            "fp-price", "[class*='fp-price']", "[class*='ng-price']",
            # Selectores genéricos
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".amount", ".cost", ".valor", ".precio-actual",
            "[class*='amount']", "[class*='cost']", "[class*='valor']",
            ".price-current", ".current-price", ".price-value"
        ],
        "product_selectors": [
            # Selectores específicos para Angular/React
            "fp-product", "fp-item", "fp-card", "[class*='fp-']", "[class*='ng-']",
            # Selectores genéricos
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']",
            ".search-result", ".result-item", ".product-card",
            ".product-list-item", ".product-grid-item", ".product-tile",
            ".product-box", ".product-container", ".product-wrapper",
            "div[class*='card']", "div[class*='tile']", "div[class*='box']"
        ],
        "use_text_extraction": False,  # Intentar primero con selectores, luego texto como fallback
        "use_selenium": True  # Usar Selenium para renderizar JS
    },
    {
        "name": "Inkafarma", 
        "base_url": "https://inkafarma.pe",
        "search_url": "https://inkafarma.pe/buscador?keyword={query}",
        "price_selectors": [
            # Selectores específicos para Angular/React
            "fp-price", "[class*='fp-price']", "[class*='ng-price']",
            # Selectores genéricos
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".amount", ".cost", ".valor", ".precio-actual",
            "[class*='amount']", "[class*='cost']", "[class*='valor']",
            ".price-current", ".current-price", ".price-value"
        ],
        "product_selectors": [
            # Selectores específicos para Angular/React
            "fp-product", "fp-item", "fp-card", "[class*='fp-']", "[class*='ng-']",
            # Selectores genéricos
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']",
            ".search-result", ".result-item", ".product-card",
            ".product-list-item", ".product-grid-item", ".product-tile",
            ".product-box", ".product-container", ".product-wrapper",
            "div[class*='card']", "div[class*='tile']", "div[class*='box']"
        ],
        "use_selenium": True  # Requiere renderizar JS y luego extracción por texto
    },
    {
        "name": "Boticas y Salud",
        "base_url": "https://www.boticasysalud.com",
        "search_url": "https://www.boticasysalud.com/tienda/busqueda?q={query}",
        "price_selectors": [
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".amount", ".cost", ".valor", ".precio-actual",
            "[class*='amount']", "[class*='cost']", "[class*='valor']",
            ".price-current", ".current-price", "span.price", "span.precio",
            "[data-price]", "[itemprop='price']", ".product-price", ".price-wrapper"
        ],
        "product_selectors": [
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']",
            ".search-result", ".result-item", ".product-card",
            ".product-box", ".product-wrapper", ".product-container",
            "article.product", "li.product", "div[class*='product']"
        ],
        "use_selenium": False,  # Intentar primero sin JS
        "fallback_to_text": True  # Usar extracción de texto si fallan selectores
    },
    {
        "name": "Boticas Perú",
        "base_url": "https://boticasperu.pe",
        "search_url": "https://boticasperu.pe/catalogsearch/result/?q={query}",
        "price_selectors": [
            # Selectores específicos para Magento
            "span.price", ".price", "[data-price-type]", "[class*='price']",
            ".price-wrapper .price", ".price-box .price", ".price-final",
            "[class*='price-box']", "[class*='price-wrapper']",
            # Selectores genéricos
            ".precio", "[class*='precio']", ".amount", ".cost"
        ],
        "product_selectors": [
            # Selectores específicos para Magento
            ".products-grid .product-item", ".product-item", 
            ".products-list .product-item", "li.product-item",
            # Selectores genéricos
            ".product", ".item", ".producto", "[class*='product-item']",
            "article.product", "div[class*='product']"
        ],
        "use_selenium": False
    },
    {
        "name": "Hogar y Salud",
        "base_url": "https://www.hogarysalud.com.pe",
        "search_url": "https://www.hogarysalud.com.pe/?s={query}&post_type=product",
        "price_selectors": [
            # Selectores específicos para WooCommerce
            ".woocommerce-Price-amount", "span.woocommerce-Price-amount",
            ".price", ".amount", "span.price", "span.amount",
            "[class*='woocommerce-Price']", "[class*='woocommerce-price']",
            ".product-price", ".price-wrapper", ".price-box",
            # Selectores genéricos
            ".precio", "[class*='price']", "[class*='precio']", "[data-price]"
        ],
        "product_selectors": [
            # Selectores específicos para WooCommerce
            ".woocommerce ul.products li.product",
            "li.product", "article.product", ".product",
            ".woocommerce-loop-product__link", ".product-item",
            # Selectores genéricos
            "[class*='product']", ".product-wrapper", ".product-box",
            "div[class*='product']", "article[class*='product']"
        ],
        "use_selenium": False,
        "fallback_to_text": True
    },
    {
        "name": "Farmacia Universal",
        "base_url": "https://www.farmaciauniversal.com",
        "search_url": "https://www.farmaciauniversal.com/{query}?_q={query}&map=ft",
        "price_selectors": [
            ".price", ".vtex-product-price", "[class*='price']", "[class*='precio']",
            ".vtex-store-components-3-x-sellingPrice", ".vtex-product-price-1-x-sellingPrice",
            "[class*='vtex-price']", ".product-price", "span[class*='price']"
        ],
        "product_selectors": [
            ".vtex-product-summary-2-x-container", ".vtex-search-result-3-x-galleryItem",
            "[class*='product-summary']", "[class*='galleryItem']",
            ".product-item", ".product", "[class*='product']"
        ],
        "use_selenium": True,  # VTEX requiere JS
        "custom_search_url": True  # Requiere formato especial
    },
    {
        "name": "Farmauna",
        "base_url": "https://www.farmauna.com",
        "search_url": "https://www.farmauna.com/search?q={query}",
        "price_selectors": [
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".product-price", ".price-value", "span.price", ".amount",
            "[data-price]", "[class*='product-price']", "[data-price-value]",
            ".selling-price", ".current-price", ".price-current"
        ],
        "product_selectors": [
            ".product", ".product-item", ".product-card", "[class*='product']",
            ".search-result-item", ".product-wrapper", ".item-product",
            "[class*='search-result']", "[class*='product-card']",
            "div[class*='product']", "article[class*='product']",
            ".product-tile", ".product-box", ".product-container"
        ],
        "use_selenium": True,  # Sitio React, requiere renderizado
        "fallback_to_text": True
    },
    {
        "name": "Farmacias Lider",
        "base_url": "https://www.farmaciaslider.pe",
        "search_url": "https://www.farmaciaslider.pe/category_product_search?product_name={query}",
        "price_selectors": [
            ".price", ".precio", "[class*='price']", "[class*='precio']",
            ".product-price", ".price-value", "span.price", ".amount",
            "[data-price]", "[class*='product-price']", ".selling-price",
            ".current-price", ".price-current", "[itemprop='price']",
            ".price-wrapper", ".price-box"
        ],
        "product_selectors": [
            ".product", ".product-item", ".product-card", "[class*='product']",
            ".search-result", ".product-wrapper", ".item-product",
            "[class*='search-result']", "[class*='product-card']", ".product-list-item",
            "div[class*='product']", "article[class*='product']",
            ".product-tile", ".product-box", ".product-container"
        ],
        "use_selenium": False,
        "fallback_to_text": True
    },
    {
        "name": "Farmacenter",
        "base_url": "https://farmacenter.com.pe",
        "search_url": "https://farmacenter.com.pe/?s={query}&post_type=product",
        "price_selectors": [
            # Selectores específicos para WooCommerce
            ".woocommerce-Price-amount", "span.woocommerce-Price-amount",
            ".price", ".amount", "span.price", "span.amount",
            "[class*='woocommerce-Price']", "[class*='woocommerce-price']",
            ".product-price", ".price-wrapper", ".price-box",
            # Selectores genéricos
            ".precio", "[class*='price']", "[class*='precio']", "[data-price]"
        ],
        "product_selectors": [
            # Selectores específicos para WooCommerce
            ".woocommerce ul.products li.product",
            "li.product", "article.product", ".product",
            ".woocommerce-loop-product__link", ".product-item",
            # Selectores genéricos
            "[class*='product']", ".product-wrapper", ".product-box",
            "div[class*='product']", "article[class*='product']"
        ],
        "use_selenium": False,
        "fallback_to_text": True
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
        
        # Check if this pharmacy uses ONLY text extraction (skip selectors)
        use_text_extraction_only = pharmacy_info.get("use_text_extraction", False)
        
        if use_text_extraction_only:
            print(f"    [TEXT] Using text extraction only for {pharmacy_info['name']} (Angular/React app)")
            # Obtener query del contexto si está disponible
            query = pharmacy_info.get("_current_query", "")
            products = extract_products_from_text(soup.get_text(), base_url, pharmacy_info, query=query)
            return products
        
        # Use pharmacy-specific product selectors
        product_selectors = pharmacy_info.get("product_selectors", [
            ".product-item", ".product", ".item", ".producto",
            "[class*='product']", "[class*='item']", "[class*='resultado']"
        ])
        
        product_containers = []
        for selector in product_selectors:
            containers = soup.select(selector)
            if containers:
                product_containers.extend(containers)
                print(f"    Found {len(containers)} containers with selector: {selector}")
        # De-dup containers by id/memory
        try:
            seen_ids = set()
            uniq = []
            for c in product_containers:
                cid = id(c)
                if cid in seen_ids: 
                    continue
                seen_ids.add(cid)
                uniq.append(c)
            product_containers = uniq
        except Exception:
            pass
        
        # If no specific product containers found, look for price elements
        if not product_containers:
            # Look for any element containing a price
            price_elements = []
            price_selectors = pharmacy_info.get("price_selectors", [".price", ".precio"])
            for selector in price_selectors:
                try:
                    elements = soup.select(selector)
                    if elements:
                        price_elements.extend(elements)
                        print(f"    Found {len(elements)} price elements with selector: {selector}")
                except Exception as sel_error:
                    print(f"    [WARN] Error with selector {selector}: {sel_error}")
                    continue
            
            if price_elements:
                # Remove duplicates by text content
                seen_prices = set()
                unique_price_elements = []
                for pe in price_elements:
                    price_text = normalize_price(pe.get_text().strip())
                    if price_text and price_text not in seen_prices:
                        seen_prices.add(price_text)
                        unique_price_elements.append(pe)
                
                # Group nearby elements as products
                for price_elem in unique_price_elements[:50]:  # Aumentado límite
                    product_info = extract_single_product_from_element(price_elem, base_url)
                    if product_info:
                        products.append(product_info)
        else:
            # Extract from product containers
            # Remove duplicates by checking if we've seen similar products
            seen_products = set()
            for container in product_containers[:50]:  # Aumentado límite
                try:
                    product_info = extract_single_product_from_container(container, base_url, pharmacy_info)
                    if product_info and product_info.get("price"):
                        # Create a key to avoid duplicates
                        product_key = (product_info.get("name", "").upper()[:50], product_info.get("price"))
                        if product_key not in seen_products:
                            seen_products.add(product_key)
                            products.append(product_info)
                except Exception as cont_error:
                    print(f"    [WARN] Error extracting from container: {cont_error}")
                    continue
        
        # If still no products found, try to extract from text patterns
        if not products:
            print(f"    [TEXT] No products found with selectors, trying text extraction...")
            query = pharmacy_info.get("_current_query", "")
            products = extract_products_from_text(soup.get_text(), base_url, pharmacy_info, query=query)
        
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

def extract_products_from_text(text: str, base_url: str, pharmacy_info: dict, query: str = "") -> list:
    """Extract products from text patterns when selectors fail"""
    products = []
    try:
        import re
        
        print(f"    [TEXT] Analyzing text for {pharmacy_info['name']}...")
        print(f"    [TEXT] Text length: {len(text)} characters")
        
        # Look for price patterns in the text
        price_patterns = [
            r"S/\.?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)",
            r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*S/\.?",
            r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*soles?",
            r"PEN\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)",
            r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*PEN"
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
                        price_str = f"S/ {match}"
                        if price_str not in found_prices:
                            found_prices.append(price_str)
                except ValueError:
                    continue
        
        print(f"    [TEXT] Found {len(found_prices)} unique prices")
        
        # Buscar nombres de productos cerca de los precios
        lines = text.split('\n')
        query_lower = query.lower() if query else ""
        
        # Buscar líneas que contengan el query y un precio cerca
        for i, line in enumerate(lines):
            line_lower = line.lower()
            # Buscar si la línea contiene el query o un precio
            has_query = query_lower and query_lower in line_lower
            has_price = any(price.replace("S/ ", "").replace(".", "").replace(",", "") in line.replace("S/", "").replace(".", "").replace(",", "") for price in found_prices)
            
            if has_price or has_query:
                # Buscar precio en esta línea o líneas cercanas
                price_found = None
                for price in found_prices:
                    price_clean = price.replace("S/ ", "").replace(".", "").replace(",", "")
                    line_clean = line.replace("S/", "").replace(".", "").replace(",", "")
                    if price_clean in line_clean:
                        price_found = price
                        break
                
                # Si no hay precio en esta línea, buscar en líneas cercanas
                if not price_found:
                    for j in range(max(0, i-2), min(len(lines), i+3)):
                        nearby_line = lines[j]
                        for price in found_prices:
                            price_clean = price.replace("S/ ", "").replace(".", "").replace(",", "")
                            nearby_clean = nearby_line.replace("S/", "").replace(".", "").replace(",", "")
                            if price_clean in nearby_clean:
                                price_found = price
                                break
                        if price_found:
                            break
                
                if price_found:
                    # Buscar nombre del producto
                    product_name = ""
                    
                    # Primero intentar en la misma línea
                    line_clean = re.sub(r"S/\.?\s*\d+[.,]?\d*", "", line).strip()
                    if len(line_clean) > 5 and len(line_clean) < 150:
                        if not any(skip in line_clean.lower() for skip in 
                                 ['agregar', 'comprar', 'ver', 'más', 'menos', 'stock', 'disponible', 'precio', 'soles', 'click', 'button', 'cantidad', 'añadir']):
                            product_name = line_clean
                    
                    # Si no, buscar en líneas cercanas
                    if not product_name:
                        for j in range(max(0, i-3), min(len(lines), i+4)):
                            nearby_line = lines[j].strip()
                            nearby_clean = re.sub(r"S/\.?\s*\d+[.,]?\d*", "", nearby_line).strip()
                            if (len(nearby_clean) > 8 and len(nearby_clean) < 150 and 
                                not nearby_clean.isdigit() and
                                not any(skip in nearby_clean.lower() for skip in 
                                       ['agregar', 'comprar', 'ver', 'más', 'menos', 'stock', 'disponible', 'precio', 'soles', 'click', 'button', 'cantidad', 'añadir', 'carrito'])):
                                # Preferir líneas que contengan el query
                                if query_lower and query_lower in nearby_clean.lower():
                                    product_name = nearby_clean
                                    break
                                elif not product_name:
                                    product_name = nearby_clean
                    
                    # Si aún no hay nombre, usar el query
                    if not product_name and query:
                        product_name = query.upper()
                    
                    if product_name and price_found:
                        # Evitar duplicados
                        combo_key = (product_name[:50].upper(), price_found)
                        if not any(p.get("name", "").upper() == product_name.upper() and p.get("price") == price_found for p in products):
                            products.append({
                                "name": product_name,
                                "price": price_found,
                                "url": base_url
                            })
                            print(f"    [TEXT] OK Extracted: {product_name[:50]} - {price_found}")
                            if len(products) >= 50:  # Aumentado el límite
                                break
        
        # Si aún no hay productos, crear con precios encontrados
        if not products and found_prices:
            print(f"    [TEXT] Creating products with found prices...")
            for i, price in enumerate(found_prices[:20]):  # Aumentado a 20
                product_name = query.upper() if query else f"Producto {pharmacy_info['name']}"
                products.append({
                    "name": product_name,
                    "price": price,
                    "url": base_url
                })
                if len(products) >= 20:
                    break
        
        print(f"    [TEXT] Total products extracted: {len(products)}")
        return products[:50]  # Aumentado el límite a 50
        
    except Exception as e:
        print(f"    [TEXT] Error extracting from text: {e}")
        import traceback
        print(traceback.format_exc())
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
        # Try title/alt attributes if still no name
        if not product_name:
            try:
                link = container.select_one("a[href]")
                if link:
                    for attr in ("title", "aria-label", "alt"):
                        t = (link.get(attr) or "").strip()
                        if 5 < len(t) < 150 and not any(w in t.lower() for w in ['agregar','comprar','carrito']):
                            product_name = t
                            break
            except Exception:
                pass
        
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
        # Manejar URLs especiales que requieren formato diferente
        search_url_template = pharmacy_info["search_url"]
        if pharmacy_info.get("custom_search_url", False):
            # Para Farmacia Universal: usar formato especial
            if pharmacy_info["name"] == "Farmacia Universal":
                # URLencode el query para la URL
                from urllib.parse import quote
                query_encoded = quote(query)
                search_url = f"https://www.farmaciauniversal.com/{query_encoded}?_q={query_encoded}&map=ft"
            else:
                search_url = search_url_template.replace("{query}", query)
        elif "{query}" in search_url_template:
            # Si hay múltiples {query}, reemplazar todos
            search_url = search_url_template.replace("{query}", query)
        else:
            search_url = search_url_template.format(query=query)
        
        print(f"    [SEARCH] {pharmacy_info['name']}: {search_url}")
        
        # Guardar query en pharmacy_info para uso en extracción
        pharmacy_info["_current_query"] = query
        
        try:
            r = requests.get(search_url, headers=HDRS, timeout=timeout)
        except requests.exceptions.Timeout:
            print(f"    [ERROR] {pharmacy_info['name']}: Timeout")
            return results
        except requests.exceptions.ConnectionError as ce:
            print(f"    [ERROR] {pharmacy_info['name']}: Connection error - {ce}")
            return results
        except Exception as req_error:
            print(f"    [ERROR] {pharmacy_info['name']}: Request error - {req_error}")
            return results
        
        if r.status_code == 200:
            # Sitios con JS: intentar renderizar con Selenium si está configurado
            use_selenium = pharmacy_info.get("use_selenium", False) or pharmacy_info.get("use_text_extraction", False)
            if use_selenium:
                rendered_html = ""
                rendered_text = ""
                try:
                    from selenium import webdriver
                    from selenium.webdriver.chrome.options import Options
                    from selenium.webdriver.common.by import By
                    from selenium.webdriver.support.ui import WebDriverWait
                    from selenium.webdriver.support import expected_conditions as EC
                    # Driver manager para asegurar ChromeDriver disponible
                    try:
                        from selenium.webdriver.chrome.service import Service
                        from webdriver_manager.chrome import ChromeDriverManager
                        _service = Service(ChromeDriverManager().install())
                    except Exception:
                        _service = None

                    chrome_options = Options()
                    chrome_options.add_argument("--headless=new")
                    chrome_options.add_argument("--no-sandbox")
                    chrome_options.add_argument("--disable-dev-shm-usage")
                    chrome_options.add_argument("--disable-gpu")
                    chrome_options.add_argument("--window-size=1366,768")
                    chrome_options.add_argument(f"--user-agent={UA}")
                    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"]) 
                    chrome_options.add_experimental_option('useAutomationExtension', False)

                    driver = None
                    try:
                        driver = webdriver.Chrome(service=_service, options=chrome_options) if _service else webdriver.Chrome(options=chrome_options)
                        driver.get(search_url)
                        # Esperar contenido dinámico razonable
                        try:
                            WebDriverWait(driver, 12).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, "a, div, span"))
                            )
                        except Exception:
                            pass
                        # Intentar cerrar banners de consentimiento/cookies
                        try:
                            for sel in [
                                "#consent-banner button",
                                "#onetrust-accept-btn-handler",
                                "button[aria-label='Aceptar']",
                                "button[aria-label='ACEPTAR']",
                                "button.cookie-accept",
                                "button:contains('Aceptar')"
                            ]:
                                btns = driver.find_elements(By.CSS_SELECTOR, sel) if ":contains(" not in sel else []
                                if btns:
                                    try:
                                        btns[0].click()
                                        break
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        # Desplazar para activar carga de listados y esperar a que aparezcan precios/texto
                        import time as _t
                        for _ in range(6):
                            driver.execute_script("window.scrollBy(0, document.body.scrollHeight/2);")
                            _t.sleep(1.0)
                        # Intentar presionar "ver más" / "cargar más"
                        try:
                            for btn_sel in [
                                "button[aria-label*='ver más']",
                                "button[aria-label*='Ver más']",
                                "button:contains('ver más')",
                                "button:contains('Ver más')",
                                "button.load-more", "button.more", "button[ng-click*='more']"
                            ]:
                                btns = driver.find_elements(By.CSS_SELECTOR, btn_sel) if ":contains(" not in btn_sel else []
                                if btns:
                                    try:
                                        btns[0].click(); _t.sleep(1.0)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        # Espera adicional a que aparezca patrón de precio en el HTML
                        tries = 0
                        while tries < 5:
                            html_tmp = driver.page_source or ""
                            if ("S/" in html_tmp) or (query.lower() in html_tmp.lower()):
                                break
                            _t.sleep(1.0)
                            tries += 1
                        rendered_html = driver.page_source or ""
                        # Extraer texto renderizado del body (mejor para buscar por regex)
                        def _capture_body_text():
                            try:
                                return driver.execute_script("return document.body.innerText || ''") or ""
                            except Exception:
                                return ""
                        rendered_text = _capture_body_text()
                        if len(rendered_text) < 500:
                            # Dar un tiempo extra para que aparezcan precios cargados por JS
                            _t.sleep(2.0)
                            rendered_text = _capture_body_text()
                        if len(rendered_text) < 500:
                            try:
                                driver.execute_script("window.scrollTo(0, 0);")
                                _t.sleep(1.0)
                            except Exception:
                                pass
                            rendered_text = _capture_body_text()
                    finally:
                        if driver:
                            try:
                                driver.quit()
                            except Exception as qe:
                                print(f"    [WARN] Error closing driver: {qe}")
                                try:
                                    driver.close()
                                except:
                                    pass
                except Exception as se:
                    print(f"    [WARN] Selenium no disponible/funcionó: {se}")
                    rendered_html = ""
                    rendered_text = ""

                used_js = bool(rendered_html) and (len(rendered_html) >= len(r.text))
                html_to_use = rendered_html if used_js else r.text
                products = extract_multiple_products(html_to_use, search_url, pharmacy_info)
                # Si con selectores no se obtuvo nada, usar el texto plano renderizado
                if not products and (rendered_text and len(rendered_text) > 200):
                    print("    [TEXT] No products via selectors. Trying rendered text extraction…")
                    pharmacy_info["_current_query"] = query
                    products = extract_products_from_text(rendered_text, search_url, pharmacy_info, query=query)
            else:
                # Sitios sin JS: extracción normal
                used_js = False
                pharmacy_info["_current_query"] = query
                products = extract_multiple_products(r.text, search_url, pharmacy_info)
                # Si no se encontraron productos y está habilitado fallback_to_text, intentar extracción de texto
                if not products and pharmacy_info.get("fallback_to_text", False):
                    print("    [TEXT] No products found with selectors, trying text extraction as fallback...")
                    try:
                        from bs4 import BeautifulSoup
                        soup_fallback = BeautifulSoup(r.text, "lxml")
                        text_fallback = soup_fallback.get_text()
                    except Exception:
                        text_fallback = r.text
                    products = extract_products_from_text(text_fallback, search_url, pharmacy_info, query=query)
            # Procesar productos encontrados (tanto con JS como sin JS)
            for product in products:
                try:
                    if product and isinstance(product, dict) and product.get("price"):
                        results.append({
                            "Producto (Marca comercial)": product.get("name", query.upper()),
                            "Precio": product["price"],
                            "Farmacia / Fuente": pharmacy_info["name"],
                            "Enlace": product.get("url", search_url),
                            "_ORIGEN": ("WEB_JS" if (use_selenium and used_js) else "WEB")
                        })
                except Exception as pe:
                    print(f"    [WARN] Error processing product: {pe}")
                    import traceback
                    print(traceback.format_exc())
                    continue
        else:
            print(f"    [ERROR] {pharmacy_info['name']}: HTTP {r.status_code}")
    except Exception as e:
        print(f"    [ERROR] {pharmacy_info['name']}: {e}")
    
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
            try:
                df_new = pd.DataFrame(new_rows)
                df_main = pd.concat([df_main, df_new], ignore_index=True)
                
                # Remove duplicates based on product name, price, and pharmacy
                df_main = df_main.drop_duplicates(
                    subset=["Producto (Marca comercial)", "Precio", "Farmacia / Fuente"], 
                    keep="first"
                )
                
                # Save back to CSV
                df_main.to_excel(EXCEL_PATH, index=False, engine='openpyxl')
                print(f"[CACHE] Saved {len(new_rows)} web results to main CSV")
            except Exception as save_ex:
                print(f"[WARN] Error in DataFrame operations: {save_ex}")
                # Intentar guardar solo los nuevos resultados si falla la concatenación
                try:
                    df_new_only = pd.DataFrame(new_rows)
                    df_new_only.to_excel(EXCEL_PATH, index=False, engine='openpyxl')
                    print(f"[CACHE] Saved {len(new_rows)} new results (replaced file)")
                except Exception as save_ex2:
                    print(f"[WARN] Could not save results: {save_ex2}")
    
    except Exception as e:
        print(f"[WARN] Error saving web results to CSV: {e}")
        import traceback
        print(traceback.format_exc())

def fetch_prices_online(query: str, selected_pharmacies: list = None, max_sites: int = 30, timeout=8):
    """Enhanced web scraping for Peruvian pharmacies - Comprehensive version"""
    print(f"[INFO] Searching for: {query}")
    out, seen = [], set()
    
    # Filtrar farmacias según selección del usuario
    pharmacies_to_search = PERUVIAN_PHARMACIES
    if selected_pharmacies and len(selected_pharmacies) > 0:
        pharmacies_to_search = [p for p in PERUVIAN_PHARMACIES if p["name"] in selected_pharmacies]
        print(f"[INFO] Searching in {len(pharmacies_to_search)} selected pharmacies: {', '.join([p['name'] for p in pharmacies_to_search])}")
    else:
        print(f"[INFO] No pharmacies selected, searching in all {len(PERUVIAN_PHARMACIES)} pharmacies")
        # Si no hay selección y está en modo WEB/AMBOS, buscar en todas por defecto
        pharmacies_to_search = PERUVIAN_PHARMACIES
    
    # 1. Search directly on selected Peruvian pharmacy websites
    print("[INFO] Searching Peruvian pharmacies directly...")
    for i, pharmacy_info in enumerate(pharmacies_to_search, 1):
        print(f"  [{i}/{len(pharmacies_to_search)}] Searching {pharmacy_info['name']}...")
        try:
            results = search_pharmacy_direct(query, pharmacy_info, timeout=timeout)
            for result in results:
                try:
                    key = (result.get("Farmacia / Fuente", ""), result.get("Precio", ""), result.get("Enlace", ""))
                    if key not in seen and result.get("Precio"):
                        seen.add(key)
                        out.append(result)
                        print(f"    [OK] Found: {result.get('Precio', 'N/A')} - {result.get('Producto (Marca comercial)', 'N/A')} at {result.get('Farmacia / Fuente', 'N/A')}")
                        if len(out) >= max_sites:  # Stop if we have enough results
                            break
                except Exception as result_error:
                    print(f"    [WARN] Error processing result from {pharmacy_info['name']}: {result_error}")
                    continue
            if len(out) >= max_sites:
                break
        except KeyboardInterrupt:
            print(f"    [WARN] Interrupted while searching {pharmacy_info['name']}")
            raise
        except Exception as e:
            print(f"    [ERROR] Error with {pharmacy_info['name']}: {e}")
            import traceback
            print(traceback.format_exc())
            # Continuar con la siguiente farmacia en lugar de detener todo
            continue
    
    # 2. Search using DuckDuckGo if we need more results
    if len(out) < 10:
        print("[INFO] Searching with DuckDuckGo...")
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
                                print(f"    [OK] Found: {info['price']} at {dom}")
                except Exception as e:
                    print(f"    [ERROR] Error with {url}: {e}")
                    continue
        except Exception as e:
            print(f"[ERROR] DuckDuckGo error: {e}")
    
    # 3. Save results to main CSV for future searches
    if out:
        try:
            save_web_results_to_csv(out)
        except Exception as save_error:
            print(f"[WARN] Error saving results to CSV: {save_error}")
            # No detener el proceso si falla el guardado
    
    print(f"[INFO] Total results found: {len(out)}")
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
@app.route("/api/pharmacies")
def api_pharmacies():
    """Obtiene la lista de farmacias disponibles"""
    if "user" not in session: 
        return jsonify({"error":"unauth"}), 401
    
    pharmacies = [{"name": p["name"], "base_url": p["base_url"]} for p in PERUVIAN_PHARMACIES]
    return jsonify({"pharmacies": pharmacies})

@app.route("/api/search")
def api_search():
    """Busca en BASE/EXTRA y/o en WEB, opcionalmente combinados."""
    if "user" not in session: 
        return jsonify({"error":"unauth"}), 401
    
    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "PRODUCTO").upper()  # PRODUCTO | PRINCIPIO ACTIVO | AMBOS
    mode  = (request.args.get("mode") or "base").lower()       # base | web | both
    selected_pharmacies = request.args.getlist("pharmacy")  # Lista de farmacias seleccionadas
    
    print(f"Search query: '{q}', scope: {scope}, mode: {mode}, pharmacies: {selected_pharmacies}")  # Debug
    
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
            # Si no hay farmacias seleccionadas y está en modo WEB/AMBOS, usar todas
            web_rows = fetch_prices_online(q, selected_pharmacies=selected_pharmacies if selected_pharmacies else None, max_sites=40)
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
        if not data:
            return jsonify({"error":"no_data"}), 400
        
        # Load current data
        df_main = load_normalized(EXCEL_PATH, "main")
        
        if df_main.empty:
            return jsonify({"error":"no_data_in_file"}), 400
        
        # Buscar la fila usando identificadores únicos
        original_codigo = data.get("original_codigo", "").strip()
        original_producto = data.get("original_producto", "").strip().upper()
        original_digemid = data.get("original_digemid", "").strip()
        
        # Buscar la fila en el DataFrame
        mask = pd.Series([False] * len(df_main))
        
        if original_codigo:
            mask |= (df_main["CÓDIGO PRODUCTO"].astype(str).str.strip() == original_codigo)
        if original_digemid:
            mask |= (df_main["N° DIGEMID"].astype(str).str.strip() == original_digemid)
        if original_producto:
            mask |= (df_main["Producto (Marca comercial)"].astype(str).str.strip().str.upper() == original_producto)
        
        matching_rows = df_main[mask]
        
        if len(matching_rows) == 0:
            return jsonify({"error":"row_not_found"}), 404
        
        if len(matching_rows) > 1:
            # Si hay múltiples coincidencias, usar la primera
            print(f"Warning: Multiple rows found, using first match")
        
        # Obtener el índice real en el DataFrame
        real_index = matching_rows.index[0]
        
        # Update row
        for col in BASE_COLUMNS_STD + EXTRA_COLUMNS:
            if col in data and col not in ("original_codigo", "original_producto", "original_digemid"):
                value = data[col]
                if col in _TEXT_COLS:
                    value = str(value).upper()
                df_main.at[real_index, col] = value
        
        # Handle CÓDIGO PRODUCTO and N° DIGEMID
        if not str(df_main.at[real_index, "CÓDIGO PRODUCTO"]).strip() and str(df_main.at[real_index, "N° DIGEMID"]).strip():
            df_main.at[real_index, "CÓDIGO PRODUCTO"] = df_main.at[real_index, "N° DIGEMID"]
        df_main.at[real_index, "N° DIGEMID"] = df_main.at[real_index, "CÓDIGO PRODUCTO"]
        
        df_main.to_excel(EXCEL_PATH, index=False, engine='openpyxl')
        
        return jsonify({"ok":True, "message":"Registro editado correctamente"})
    except Exception as e:
        import traceback
        print(f"Error editing row: {e}")
        print(traceback.format_exc())
        return jsonify({"error":str(e)}), 500

@app.route("/api/admin/delete_row", methods=["POST"])
def api_admin_delete_row():
    if "user" not in session: return jsonify({"error":"unauth"}), 401
    if session["user"].get("role") != "admin": return jsonify({"error":"forbidden"}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error":"no_data"}), 400
        
        # Load current data
        df_main = load_normalized(EXCEL_PATH, "main")
        
        if df_main.empty:
            return jsonify({"error":"no_data_in_file"}), 400
        
        # Buscar la fila usando identificadores únicos
        codigo = data.get("codigo", "").strip()
        producto = data.get("producto", "").strip().upper()
        digemid = data.get("digemid", "").strip()
        
        # Buscar la fila en el DataFrame
        mask = pd.Series([False] * len(df_main))
        
        if codigo:
            mask |= (df_main["CÓDIGO PRODUCTO"].astype(str).str.strip() == codigo)
        if digemid:
            mask |= (df_main["N° DIGEMID"].astype(str).str.strip() == digemid)
        if producto:
            mask |= (df_main["Producto (Marca comercial)"].astype(str).str.strip().str.upper() == producto)
        
        matching_rows = df_main[mask]
        
        if len(matching_rows) == 0:
            return jsonify({"error":"row_not_found"}), 404
        
        if len(matching_rows) > 1:
            # Si hay múltiples coincidencias, usar la primera
            print(f"Warning: Multiple rows found, deleting first match")
        
        # Obtener el índice real en el DataFrame
        real_index = matching_rows.index[0]
        
        # Delete row
        df_main = df_main.drop(index=real_index).reset_index(drop=True)
        df_main.to_excel(EXCEL_PATH, index=False, engine='openpyxl')
        
        return jsonify({"ok":True, "message":"Registro eliminado correctamente"})
    except Exception as e:
        import traceback
        print(f"Error deleting row: {e}")
        print(traceback.format_exc())
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
      <label style="color:#c7d6ea; font-size:13px; white-space:nowrap;">Farmacias a buscar:</label>
      <div id="pharmacySelectors" class="pharmacy-checkboxes"></div>
    </div>
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
    # Para desarrollo local - desactivar reloader si causa problemas
    use_reloader = os.environ.get("FLASK_RELOAD", "true").lower() == "true"
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), use_reloader=use_reloader)
else:
    # Para producción (cuando se ejecuta con gunicorn)
    ensure_all_files()
