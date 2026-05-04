"""
Motor ILP — Programación Lineal Entera Mixta con PuLP + CBC.

Variables de decisión (enteras ≥ 0):
  x1 = pantalon_diario   utilidad = 20 000 COP
  x2 = camisa_diario     utilidad = 15 000 COP
  x3 = pantalon_ef       utilidad = 12 000 COP
  x4 = sueter_ef         utilidad = 11 000 COP

Los coeficientes de insumo se leen dinámicamente desde uniforme_insumos.
Los stocks se leen desde insumos.
La demanda máxima se calcula desde pedidos activos.
"""

import logging
from typing import NamedTuple

from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, lpSum, value
from pulp import PULP_CBC_CMD

from database import obtener_coeficientes, obtener_demanda_uniforme, obtener_stocks_insumos
from schemas import (
    DemandaInfo,
    ParametrosOptimizacion,
    PlanProduccion,
    RecursoInfo,
    ResultadoOptimizacion,
)


class OptimizadorOutput(NamedTuple):
    resultado: ResultadoOptimizacion
    coef_matrix: dict   # {variable: {insumo: coef}}
    stocks: dict        # {insumo: stock_disponible}

logger = logging.getLogger(__name__)

# Utilidades por defecto en COP (del modelo matemático del spec)
UTILIDADES_DEFAULT: dict[str, float] = {
    "pantalon_diario": 20_000,
    "camisa_diario":   15_000,
    "pantalon_ef":     12_000,
    "sueter_ef":       11_000,
}

# Las cuatro variables del modelo ILP
VARIABLES_ILP = ["pantalon_diario", "camisa_diario", "pantalon_ef", "sueter_ef"]

# Coeficientes de respaldo cuando no hay datos en DB (valores del spec)
COEF_FALLBACK: dict[str, dict[str, float]] = {
    "pantalon_diario": {"drill": 1.5,  "hilo": 2.0,  "botones": 1.0},
    "camisa_diario":   {"popelina": 1.2, "hilo": 1.5, "botones": 5.0},
    "pantalon_ef":     {"licra": 0.8,  "hilo": 1.0},
    "sueter_ef":       {"licra": 0.6,  "hilo": 0.5},
}


def _identificar_variable(prenda: str, tipo: str) -> str | None:
    """Mapea (prenda, tipo) del DB a una variable del modelo ILP."""
    p = prenda.lower()
    t = (tipo or "").lower()

    es_pantalon = any(w in p for w in ["pantalon", "pantalón", "pant"])
    es_camisa   = any(w in p for w in ["camisa", "blusa", "camiseta"])
    es_sueter   = any(w in p for w in ["sueter", "suéter", "sweater", "buzo", "sudadera", "chompa"])
    es_diario   = "diario" in t or "uniforme" in t
    es_ef       = any(w in t for w in ["educacion", "educación", "fisica", "física", " ef"])

    if es_pantalon:
        if es_diario:
            return "pantalon_diario"
        if es_ef:
            return "pantalon_ef"
    if es_camisa:
        return "camisa_diario"
    if es_sueter:
        return "sueter_ef"
    return None


def _identificar_insumo(nombre: str) -> str | None:
    """Mapea el nombre del insumo a la clave del modelo ILP."""
    n = nombre.lower()
    if "drill" in n:
        return "drill"
    if "popelina" in n or "popeline" in n:
        return "popelina"
    if "licra" in n or "lycra" in n:
        return "licra"
    if "hilo" in n:
        return "hilo"
    if "boton" in n or "botón" in n:
        return "botones"
    return None


def resolver_optimizacion(params: ParametrosOptimizacion) -> OptimizadorOutput:
    talla = params.talla
    utilidades = {**UTILIDADES_DEFAULT, **(params.utilidades or {})}
    coef_matrix: dict[str, dict[str, float]] = {}
    stocks: dict[str, float] = {}

    try:
        # ── 1. Cargar coeficientes y stocks desde DB ──────────────────────
        df_coef = obtener_coeficientes(talla)

        for _, row in df_coef.iterrows():
            var = _identificar_variable(str(row["prenda"]), str(row["tipo"]))
            ins = _identificar_insumo(str(row["insumo_nombre"]))
            if var and ins:
                coef_matrix.setdefault(var, {})[ins] = float(row["cantidad_base"])
                # Conserva el mayor stock si el mismo insumo aparece varias veces
                stocks[ins] = max(stocks.get(ins, 0), float(row["stock_actual"]))

        usando_fallback = False
        if not coef_matrix:
            logger.warning("Sin coeficientes en DB para talla=%s — usando valores del spec", talla)
            coef_matrix = COEF_FALLBACK
            usando_fallback = True
            # Leer stocks reales desde insumos aunque no haya uniforme_insumos
            df_stocks = obtener_stocks_insumos()
            for _, row in df_stocks.iterrows():
                ins = _identificar_insumo(str(row["nombre_lower"]))
                if ins:
                    stocks[ins] = max(stocks.get(ins, 0), float(row["stock"]))

        # ── 2. Demanda desde pedidos activos ──────────────────────────────
        demanda_db: dict[str, int] = {}
        if params.incluir_demanda:
            df_dem = obtener_demanda_uniforme(talla)
            for _, row in df_dem.iterrows():
                var = _identificar_variable(str(row["prenda"]), str(row["tipo"]))
                if var:
                    qty = int(row["cantidad_demandada"])
                    demanda_db[var] = demanda_db.get(var, 0) + qty

        # ── 3. Definir el modelo ILP ──────────────────────────────────────
        prob = LpProblem("optimizacion_produccion_costusoft", LpMaximize)

        x: dict[str, LpVariable] = {
            v: LpVariable(v, lowBound=0, cat="Integer") for v in VARIABLES_ILP
        }

        # Función objetivo: Max Z = sum(utilidad_i * x_i)
        prob += lpSum(utilidades.get(v, 0) * x[v] for v in VARIABLES_ILP), "utilidad_total"

        # Restricciones de insumos (R1-R5 del spec)
        all_insumos = {ins for coefs in coef_matrix.values() for ins in coefs}
        for ins in all_insumos:
            stock_disponible = stocks.get(ins, 0)
            if stock_disponible > 0:
                expr = lpSum(coef_matrix.get(v, {}).get(ins, 0) * x[v] for v in VARIABLES_ILP)
                prob += expr <= stock_disponible, f"stock_{ins}"

        # Restricciones de demanda (R6a-R6d del spec)
        for var, dem in demanda_db.items():
            if dem > 0 and var in x:
                prob += x[var] <= dem, f"demanda_{var}"

        # ── 4. Resolver con CBC ───────────────────────────────────────────
        solver = PULP_CBC_CMD(msg=0)
        prob.solve(solver)
        estado = LpStatus[prob.status]

        if estado == "Optimal":
            plan = {v: int(value(x[v]) or 0) for v in VARIABLES_ILP}
            utilidad_total = float(value(prob.objective) or 0)

            recursos: dict[str, RecursoInfo] = {}
            for ins in all_insumos:
                if ins in stocks:
                    usado = sum(
                        coef_matrix.get(v, {}).get(ins, 0) * plan.get(v, 0)
                        for v in VARIABLES_ILP
                    )
                    disponible = stocks[ins]
                    recursos[ins] = RecursoInfo(
                        usado=round(usado, 3),
                        disponible=round(disponible, 3),
                        holgura=round(disponible - usado, 3),
                        utilizacion_pct=round(usado / disponible * 100, 1) if disponible > 0 else 0.0,
                    )

            demanda_resultado = {
                v: DemandaInfo(solicitado=plan.get(v, 0), demanda_maxima=dem)
                for v, dem in demanda_db.items()
            }

            nota_fallback = " (coeficientes del spec — sin datos en DB)" if usando_fallback else ""
            resultado = ResultadoOptimizacion(
                estado="OPTIMAL",
                utilidad_total=round(utilidad_total, 2),
                plan=PlanProduccion(**plan),
                recursos=recursos,
                demanda=demanda_resultado,
                talla=talla,
                mensaje=f"Solución óptima encontrada{nota_fallback}. Utilidad máxima: ${utilidad_total:,.0f} COP",
            )
            return OptimizadorOutput(resultado=resultado, coef_matrix=coef_matrix, stocks=stocks)

        resultado = ResultadoOptimizacion(
            estado=estado.upper(),
            utilidad_total=0,
            plan=PlanProduccion(),
            recursos={},
            demanda={},
            talla=talla,
            mensaje=f"El solver no encontró solución óptima. Estado: {estado}. Verifique stocks y restricciones.",
        )
        return OptimizadorOutput(resultado=resultado, coef_matrix=coef_matrix, stocks=stocks)

    except Exception as exc:
        logger.error("Error en optimización ILP: %s", exc, exc_info=True)
        resultado = ResultadoOptimizacion(
            estado="ERROR",
            utilidad_total=0,
            plan=PlanProduccion(),
            recursos={},
            demanda={},
            talla=talla,
            mensaje=f"Error interno al resolver el modelo: {exc}",
        )
        return OptimizadorOutput(resultado=resultado, coef_matrix={}, stocks={})
