from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ParametrosOptimizacion(BaseModel):
    colegio_id: int = Field(description="ID del colegio a optimizar")
    incluir_demanda: bool = Field(default=True, description="Aplica restricciones de demanda desde pedidos activos")
    utilidades: Optional[dict[str, float]] = Field(
        default=None,
        description="Override de utilidades por prenda en COP. Claves: slug de prenda_tipo o nombre canónico."
    )
    ejecutado_por: Optional[str] = Field(default=None)


class RecursoInfo(BaseModel):
    usado: float
    disponible: float
    holgura: float
    utilizacion_pct: float
    unidad_medida: str = ""


class DemandaInfo(BaseModel):
    solicitado: int
    demanda_maxima: int


class DetallePrenda(BaseModel):
    cantidad: int
    max_producible: int
    genero: Optional[str] = None
    insumo_limitante_key: Optional[str] = None
    insumo_limitante_nombre: Optional[str] = None
    coef_insumo_limitante: Optional[float] = None
    eficiencia_cop_por_unidad: Optional[float] = None
    motivo: str


class ResultadoOptimizacion(BaseModel):
    id: Optional[int] = None
    colegio_id: Optional[int] = None
    nombre_colegio: Optional[str] = None
    estado: str  # OPTIMAL | INFEASIBLE | UNBOUNDED | ERROR
    utilidad_total: float
    plan: dict[str, int]                       # {var_key: cantidad} — dinámico por colegio
    recursos: dict[str, RecursoInfo]
    demanda: dict[str, DemandaInfo]
    insumo_labels: dict[str, str] = {}         # {ins_key: "Tela Lacoste Blanca"}
    detalle_plan: dict[str, DetallePrenda] = {} # {var_key: DetallePrenda}
    mensaje: str
    grafica_html: Optional[str] = None
    grafica_region_html: Optional[str] = None
    fecha_ejecucion: Optional[datetime] = None


class HistorialResponse(BaseModel):
    total: int
    items: list[dict]
