from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ParametrosOptimizacion(BaseModel):
    talla: str = Field(default="M", description="Talla de uniforme a optimizar")
    incluir_demanda: bool = Field(default=True, description="Aplica restricciones de demanda desde pedidos activos")
    utilidades: Optional[dict[str, float]] = Field(
        default=None,
        description="Override de utilidades por prenda en COP. Defaults: pantalon_diario=20000, camisa_diario=15000, pantalon_ef=12000, sueter_ef=11000"
    )
    ejecutado_por: Optional[str] = Field(default=None)


class PlanProduccion(BaseModel):
    pantalon_diario: int = 0
    camisa_diario: int = 0
    pantalon_ef: int = 0
    sueter_ef: int = 0


class RecursoInfo(BaseModel):
    usado: float
    disponible: float
    holgura: float
    utilizacion_pct: float


class DemandaInfo(BaseModel):
    solicitado: int
    demanda_maxima: int


class ResultadoOptimizacion(BaseModel):
    id: Optional[int] = None
    estado: str  # OPTIMAL | INFEASIBLE | UNBOUNDED | ERROR
    utilidad_total: float
    plan: PlanProduccion
    recursos: dict[str, RecursoInfo]
    demanda: dict[str, DemandaInfo]
    talla: str = "M"
    mensaje: str
    grafica_html: Optional[str] = None
    grafica_region_html: Optional[str] = None
    fecha_ejecucion: Optional[datetime] = None


class HistorialResponse(BaseModel):
    total: int
    items: list[dict]
