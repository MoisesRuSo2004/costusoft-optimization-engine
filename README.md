# costusoft-optimization-engine

Microservicio de optimización semanal de producción para CostuSoft. Resuelve el plan óptimo de prendas a fabricar usando Programación Lineal Entera (ILP) con PuLP + CBC, maximizando utilidad sin exceder stocks de insumos ni demanda de pedidos activos.

## Stack

- **Python 3.11** + **FastAPI**
- **PuLP + CBC** — motor ILP (COIN-BC solver)
- **Plotly** — gráficas interactivas embebibles en el dashboard
- **SQLAlchemy + psycopg2** — conexión a Supabase (PostgreSQL)
- **fpdf2** — exportación de resultados a PDF
- **Docker** — despliegue en Render

## Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/health` | Estado del servicio |
| `POST` | `/optimizar` | Ejecuta el modelo ILP y retorna el plan óptimo |
| `GET` | `/historial` | Últimas N ejecuciones del optimizador |
| `GET` | `/historial/{id}` | Detalle de una ejecución específica |
| `GET` | `/historial/{id}/pdf` | Descarga el PDF del resultado |

Todos los endpoints (excepto `/health`) requieren el header `X-API-Token`.

## Variables de entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `DATABASE_URL` | URL de conexión a PostgreSQL (Supabase) | — |
| `API_SECRET_TOKEN` | Token interno compartido con el backend | — |
| `ENVIRONMENT` | `production` / `development` | `development` |
| `PORT` | Puerto del servidor (Render lo inyecta automáticamente) | `8002` |

## Desarrollo local

```bash
# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables
cp .env.example .env  # editar con tus valores

# Ejecutar
uvicorn main:app --reload --port 8002
```

## Despliegue (Render)

1. Conectar repo en Render como **Web Service**
2. Seleccionar **Docker** como runtime
3. Configurar las variables de entorno
4. Deploy — el `Dockerfile` maneja el resto
