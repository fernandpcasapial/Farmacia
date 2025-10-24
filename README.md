# 🏥 Sistema Web de Búsqueda de Medicamentos

Sistema web completo para búsqueda de medicamentos en farmacias peruanas con funcionalidades de web scraping, gestión de usuarios y CRUD.

## 🚀 Características

- **🔍 Búsqueda Avanzada**: Por producto, principio activo o ambos
- **🌐 Web Scraping**: Extracción automática de precios de farmacias peruanas
- **👥 Gestión de Usuarios**: Roles de admin y consulta
- **📊 CRUD Completo**: Agregar, editar, eliminar productos
- **💾 Cache Inteligente**: Resultados guardados para búsquedas más rápidas
- **📱 Responsive**: Diseño moderno y adaptable

## 🏪 Farmacias Soportadas

- **Mifarma**: https://www.mifarma.com.pe
- **Inkafarma**: https://inkafarma.pe
- **Boticas y Salud**: https://www.boticasysalud.com
- **Boticas Perú**: https://boticasperu.pe

## 👤 Usuarios por Defecto

- **Admin**: `admin` / `admin`
- **Consulta**: `consulta` / `consulta`

## 🛠️ Instalación Local

```bash
# Clonar repositorio
git clone <tu-repositorio>
cd Proyecto-Farmacia

# Instalar dependencias
pip install -r requirements.txt

# Ejecutar aplicación
python app.py
```

## 🌐 Despliegue en la Web

### Opción 1: Railway.app (Recomendado)

1. **Crear cuenta** en [Railway.app](https://railway.app)
2. **Conectar GitHub** y seleccionar este repositorio
3. **Deploy automático** - ¡Listo!

### Opción 2: Render.com

1. **Crear cuenta** en [Render.com](https://render.com)
2. **Nuevo Web Service** desde GitHub
3. **Configurar**:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
4. **Deploy** - ¡Listo!

### Opción 3: Heroku

1. **Instalar Heroku CLI**
2. **Login**: `heroku login`
3. **Crear app**: `heroku create tu-app-farmacia`
4. **Deploy**: `git push heroku main`

## 📁 Estructura del Proyecto

```
Proyecto Farmacia/
├── app.py              # Aplicación principal Flask
├── static/
│   └── app.js          # JavaScript del frontend
├── requirements.txt    # Dependencias Python
├── Procfile           # Configuración para Heroku/Railway
├── runtime.txt        # Versión de Python
└── README.md          # Este archivo
```

## 🔧 Tecnologías

- **Backend**: Flask (Python)
- **Frontend**: HTML5, CSS3, JavaScript
- **Base de Datos**: Excel/CSV + JSON
- **Web Scraping**: BeautifulSoup + Requests
- **Deploy**: Gunicorn + Railway/Render

## 📝 Notas

- Los datos se almacenan en archivos Excel/CSV
- El web scraping funciona con las 4 farmacias principales
- Los resultados se cachean automáticamente
- Diseño responsive para móviles y desktop

## 🆘 Soporte

Para soporte técnico o consultas, contacta al desarrollador.

---

**Desarrollado con ❤️ para la comunidad farmacéutica peruana**
