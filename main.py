import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import APIKeyHeader

from charts import generar_grafica_optimizacion, generar_grafica_region_factible_html
from config import settings
from database import (
    guardar_historial,
    init_db,
    obtener_historial,
    obtener_historial_por_id,
)
from optimizer import OptimizadorOutput, resolver_optimizacion
from pdf_export import generar_pdf_optimizacion
from schemas import HistorialResponse, ParametrosOptimizacion, ResultadoOptimizacion

logging.basicConfig(
    level=logging.INFO if settings.ENVIRONMENT == "production" else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Optimizacion Service listo en puerto %d", settings.PORT)
    yield


app = FastAPI(
    title="Optimizacion Service",
    description=(
        "Motor ILP (Programación Lineal Entera) para optimización semanal de producción. "
        "Resuelve: ¿cuántas prendas producir para maximizar utilidad sin exceder stocks ni demanda? "
        "Stack: FastAPI + PuLP + CBC + Plotly."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Autenticación por token (idéntica al prediccion-service) ─────────────
_api_key_header = APIKeyHeader(name="X-API-Token", auto_error=False)


async def verificar_token(token: str = Depends(_api_key_header)) -> str:
    if token != settings.API_SECRET_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token inválido o ausente",
        )
    return token


# ── Endpoints ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
def health() -> dict:
    return {"status": "ok", "service": "optimizacion-service", "version": "1.0.0"}


@app.post("/optimizar", response_model=ResultadoOptimizacion, tags=["Optimizacion"])
def optimizar(
    params: ParametrosOptimizacion,
    _: str = Depends(verificar_token),
) -> ResultadoOptimizacion:
    """
    Ejecuta el modelo ILP y retorna el plan óptimo de producción.

    - Lee coeficientes de insumo desde `uniforme_insumos` (dinámico).
    - Lee stocks desde `insumos`.
    - Lee demanda desde pedidos activos en `pedidos`/`detalle_pedidos`.
    - Resuelve con PuLP + CBC (COIN-BC).
    - Genera gráfica Plotly interactiva embebible en el dashboard.
    - Persiste el resultado en `historial_optimizacion`.
    """
    logger.info("Ejecutando optimización — incluir_demanda=%s, ejecutado_por=%s", params.incluir_demanda, params.ejecutado_por)

    output: OptimizadorOutput = resolver_optimizacion(params)
    resultado = output.resultado

    if resultado.estado == "OPTIMAL":
        resultado.grafica_html = generar_grafica_optimizacion(resultado.model_dump())
        resultado.grafica_region_html = generar_grafica_region_factible_html(
            coef_matrix=output.coef_matrix,
            stocks=output.stocks,
            plan=resultado.plan,
        )

    datos_historial = resultado.model_dump()
    datos_historial["parametros"] = params.model_dump()
    datos_historial["ejecutado_por"] = params.ejecutado_por
    id_guardado = guardar_historial(datos_historial)
    resultado.id = id_guardado

    return resultado


@app.get("/historial", response_model=HistorialResponse, tags=["Historial"])
def historial(
    limit: int = 20,
    _: str = Depends(verificar_token),
) -> HistorialResponse:
    """Retorna las últimas N ejecuciones del optimizador."""
    df = obtener_historial(limit)
    items = df.to_dict("records") if not df.empty else []
    for item in items:
        for k, v in item.items():
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
    return HistorialResponse(total=len(items), items=items)


@app.get("/historial/{record_id}", tags=["Historial"])
def historial_por_id(
    record_id: int,
    _: str = Depends(verificar_token),
) -> dict:
    """Retorna una ejecución específica incluyendo grafica_html."""
    item = obtener_historial_por_id(record_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"No existe historial con id={record_id}")
    for k, v in item.items():
        if hasattr(v, "isoformat"):
            item[k] = v.isoformat()
    return item


@app.get("/historial/{record_id}/pdf", tags=["Historial"])
def descargar_pdf(
    record_id: int,
    _: str = Depends(verificar_token),
) -> Response:
    """Genera y descarga el PDF del resultado de optimización."""
    item = obtener_historial_por_id(record_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"No existe historial con id={record_id}")
    pdf_bytes = generar_pdf_optimizacion(item)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="optimizacion-{record_id}.pdf"'},
    )
