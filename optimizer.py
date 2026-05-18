"""
Motor ILP — Programación Lineal Entera Mixta con PuLP + CBC.

Las variables de decisión son DINÁMICAS: se construyen desde las prendas
del colegio seleccionado en uniforme_insumos. No hay lista fija.

  Maximizar Z = Σ(utilidad_i · x_i)  para cada prenda del colegio
  Sujeto a:
    Σ(coef_ij · x_i) ≤ stock_j   para cada insumo j  (stock global compartido)
    x_i ≤ demanda_i               si incluir_demanda=True
    x_i ≥ 0, enteras
"""

import logging
import re
import unicodedata
from typing import NamedTuple

from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, lpSum, value
from pulp import PULP_CBC_CMD

from config import settings
from database import obtener_coeficientes, obtener_demanda_uniforme, obtener_stocks_insumos, obtener_uniformes_sin_receta
from schemas import (
    DemandaInfo,
    DetallePrenda,
    ParametrosOptimizacion,
    RecursoInfo,
    ResultadoOptimizacion,
)


class OptimizadorOutput(NamedTuple):
    resultado: ResultadoOptimizacion
    coef_matrix: dict    # {var_key: {ins_key: coef}}
    stocks: dict         # {ins_key: stock_disponible}
    var_labels: dict     # {var_key: "Pantalón Diario"} — etiquetas para gráficas
    insumo_labels: dict  # {ins_key: "Tela Lacoste Blanca"} — etiquetas para gráficas


logger = logging.getLogger(__name__)

# Utilidades por defecto en COP. Se busca por slug, luego por clave canónica.
UTILIDADES_DEFAULT: dict[str, float] = {
    "pantalon_diario":            20_000,
    "camisa_diario":              15_000,
    "sueter_diario":              13_000,
    "pantalon_ef":                12_000,
    "sueter_ef":                  11_000,
    "pantalon_educacion_fisica":  12_000,
    "sueter_educacion_fisica":    11_000,
    "camisa_educacion_fisica":    13_000,
    "blusa_diario":               15_000,
    "bata_diario":                14_000,
    "falda_diario":               13_000,
}

COEF_FALLBACK: dict[str, dict[str, float]] = {
    "pantalon_diario": {"drill": 1.5,    "hilo": 2.0, "botones": 1.0},
    "camisa_diario":   {"popelina": 1.2, "hilo": 1.5, "botones": 5.0},
    "sueter_diario":   {"drill": 1.0,    "hilo": 3.0, "botones": 3.0},
    "pantalon_ef":     {"licra": 0.8,    "hilo": 1.0},
    "sueter_ef":       {"licra": 0.6,    "hilo": 0.5},
}


def _slugify(text: str) -> str:
    """'Pantalón Educación Física' → 'pantalon_educacion_fisica'"""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", "_", text.strip())


def _make_var_key(prenda: str, tipo: str, genero: str | None = None) -> str:
    base = f"{_slugify(prenda)}_{_slugify(tipo)}"
    g = _slugify(genero or "")
    return f"{base}_{g}" if g and g != "unisex" else base


def _make_label(prenda: str, tipo: str, genero: str | None = None) -> str:
    base = f"{prenda.strip()} {tipo.strip()}"
    g = (genero or "").strip()
    return f"{base} — {g.title()}" if g and g.lower() != "unisex" else base


def _identificar_variable_canonica(prenda: str, tipo: str) -> str | None:
    """Mapea (prenda, tipo) a clave canónica legacy para buscar en UTILIDADES_DEFAULT."""
    p = prenda.lower()
    t = (tipo or "").lower()
    es_pantalon = any(w in p for w in ["pantalon", "pantalón", "pant"])
    es_camisa   = any(w in p for w in ["camisa", "blusa", "camiseta"])
    es_sueter   = any(w in p for w in ["sueter", "suéter", "sweater", "buzo", "sudadera"])
    es_ef       = any(w in t for w in ["educacion", "educación", "fisica", "física", "ef"])
    if es_pantalon:
        return "pantalon_ef" if es_ef else "pantalon_diario"
    if es_camisa:
        return None if es_ef else "camisa_diario"
    if es_sueter:
        return "sueter_ef" if es_ef else "sueter_diario"
    return None


def _get_utilidad(prenda: str, tipo: str, utilidades: dict[str, float]) -> float:
    """Busca utilidad por slug → canónica → 10 000 COP default."""
    slug = _make_var_key(prenda, tipo)
    if slug in utilidades:
        return utilidades[slug]
    canon = _identificar_variable_canonica(prenda, tipo)
    if canon and canon in utilidades:
        return utilidades[canon]
    return 10_000


def _identificar_insumo(nombre: str) -> str | None:
    n = nombre.lower()
    if "drill" in n:                                         return "drill"
    if "popelina" in n or "popeline" in n:                  return "popelina"
    if "licra" in n or "lycra" in n or "spandex" in n:      return "licra"
    if "entretela" in n or "termofusible" in n:              return "entretela"
    if "tela" in n and "lana" in n:                         return "lana"
    if "hilo" in n or "thread" in n:                        return "hilo"
    if "boton" in n or "botón" in n or "button" in n:       return "botones"
    if "cierre" in n or "cremallera" in n or "zipper" in n: return "cierres"
    if "elástic" in n or "elastic" in n:                    return "elastico"
    if "cinta" in n or "ribbon" in n:                       return "cinta"
    if "etiqueta" in n or "label" in n:                     return "etiquetas"
    return None


def _abreviar_unidad(u: str) -> str:
    u = u.strip().lower()
    if u in ("metros", "metro", "m", "mts"):          return "mtr"
    if u in ("unidades", "unidad", "und", "u", "un"): return "un"
    if u in ("kilogramos", "kilogramo", "kg"):         return "kg"
    if u in ("gramos", "gramo", "gr", "g"):            return "gr"
    if u in ("litros", "litro", "lt", "l"):            return "ltr"
    if u in ("rollos", "rollo"):                       return "rol"
    if u in ("yardas", "yarda", "yrd", "yd"):          return "yrd"
    return u[:4] if u else "un"


def resolver_optimizacion(params: ParametrosOptimizacion) -> OptimizadorOutput:
    utilidades_map = {**UTILIDADES_DEFAULT, **(params.utilidades or {})}

    coef_matrix: dict[str, dict[str, float]] = {}
    stocks: dict[str, float] = {}
    units:  dict[str, str]   = {}
    var_labels:    dict[str, str]   = {}   # var_key  → "Pantalón Diario — Masculino"
    var_utilidad:  dict[str, float] = {}   # var_key  → COP
    var_generos:   dict[str, str]   = {}   # var_key  → "Masculino"
    insumo_labels: dict[str, str]   = {}   # ins_key  → "Tela Lacoste Blanca"

    def _infeasible(msg: str) -> OptimizadorOutput:
        return OptimizadorOutput(
            resultado=ResultadoOptimizacion(
                estado="INFEASIBLE",
                utilidad_total=0,
                plan={},
                recursos={},
                demanda={},
                mensaje=msg,
                colegio_id=params.colegio_id,
            ),
            coef_matrix={},
            stocks={},
            var_labels={},
            insumo_labels={},
        )

    try:
        # ── 1. Coeficientes del colegio ───────────────────────────────────────
        df_coef = obtener_coeficientes(params.colegio_id)

        coef_acum: dict[str, dict[str, list[float]]] = {}

        for _, row in df_coef.iterrows():
            prenda = str(row["prenda"])
            tipo   = str(row["tipo"])
            genero = str(row["genero"]) if row.get("genero") else ""
            var    = _make_var_key(prenda, tipo, genero)

            # Clave única del insumo: "ins_{id}" — nunca se descarta ningún insumo
            # sin importar cómo se llame en la base de datos.
            ins_id  = int(row["insumo_id"])
            ins_key = f"ins_{ins_id}"
            # Nombre legible para las gráficas
            nombre_raw = str(row["insumo_nombre"])   # viene en minúsculas de la query
            ins_label  = nombre_raw.title()           # "tela lacoste blanca" → "Tela Lacoste Blanca"

            coef_acum.setdefault(var, {}).setdefault(ins_key, []).append(float(row["cantidad_base"]))
            stocks[ins_key] = max(stocks.get(ins_key, 0), float(row["stock_actual"]))
            if ins_key not in units:
                units[ins_key]         = _abreviar_unidad(str(row.get("unidad_medida") or ""))
                insumo_labels[ins_key] = ins_label
            if var not in var_labels:
                var_labels[var]   = _make_label(prenda, tipo, genero)
                var_utilidad[var] = _get_utilidad(prenda, tipo, utilidades_map)
                var_generos[var]  = genero.strip().title() if genero.strip() else ""

        coef_matrix = {
            var: {ins: sum(vals) / len(vals) for ins, vals in insumos.items()}
            for var, insumos in coef_acum.items()
        }

        usando_fallback = False
        if not coef_matrix:
            if settings.ENVIRONMENT == "production":
                return _infeasible(
                    f"El colegio id={params.colegio_id} no tiene recetas de insumos configuradas. "
                    "Configure las recetas en Uniformes → Insumos antes de ejecutar la optimización."
                )
            logger.warning("Colegio %d sin coeficientes — usando COEF_FALLBACK", params.colegio_id)
            coef_matrix  = COEF_FALLBACK
            var_labels   = {k: k.replace("_", " ").title() for k in COEF_FALLBACK}
            var_utilidad = {k: utilidades_map.get(k, 10_000) for k in COEF_FALLBACK}
            usando_fallback = True
            df_stocks = obtener_stocks_insumos()
            for _, row in df_stocks.iterrows():
                ins = _identificar_insumo(str(row["nombre_lower"]))
                if ins:
                    stocks[ins] = max(stocks.get(ins, 0), float(row["stock"]))
                    if ins not in units:
                        units[ins]         = _abreviar_unidad(str(row.get("unidad_medida") or ""))
                        insumo_labels[ins] = str(row["nombre_lower"]).title()

        var_keys = list(coef_matrix.keys())

        # ── 2. Demanda del colegio ────────────────────────────────────────────
        demanda_db: dict[str, int] = {}
        if params.incluir_demanda:
            df_dem = obtener_demanda_uniforme(params.colegio_id)
            for _, row in df_dem.iterrows():
                var = _make_var_key(str(row["prenda"]), str(row["tipo"]), str(row.get("genero") or ""))
                if var in coef_matrix:
                    qty = int(row["cantidad_demandada"])
                    # Incluir también qty=0: la prenda existe en el catálogo pero sin pedidos activos
                    # → el modelo recibirá x[var] <= 0, bloqueando su producción
                    demanda_db[var] = demanda_db.get(var, 0) + qty

        # ── 3. Modelo ILP ─────────────────────────────────────────────────────
        prob = LpProblem("optimizacion_produccion_costusoft", LpMaximize)

        x: dict[str, LpVariable] = {
            v: LpVariable(v, lowBound=0, cat="Integer") for v in var_keys
        }

        # Función objetivo: Max Σ(utilidad_i · x_i)
        prob += lpSum(var_utilidad.get(v, 10_000) * x[v] for v in var_keys), "utilidad_total"

        # Restricciones de stock (insumos globales del taller)
        all_insumos = {ins for coefs in coef_matrix.values() for ins in coefs}
        restricciones_stock = []
        for ins in all_insumos:
            stock_disponible = stocks.get(ins, 0)
            if stock_disponible > 0:
                expr = lpSum(coef_matrix.get(v, {}).get(ins, 0) * x[v] for v in var_keys)
                prob += expr <= stock_disponible, f"stock_{ins}"
                restricciones_stock.append(ins)

        if not restricciones_stock:
            return _infeasible(
                "Ningún insumo tiene stock > 0. Registre insumos y configure las recetas."
            )

        # Restricciones de demanda del colegio (incluye dem=0 → bloquea producción sin pedido)
        for var, dem in demanda_db.items():
            if var in x:
                prob += x[var] <= dem, f"demanda_{var}"

        # ── 4. Resolver con CBC ───────────────────────────────────────────────
        solver = PULP_CBC_CMD(msg=0)
        prob.solve(solver)
        estado = LpStatus[prob.status]

        if estado == "Optimal":
            plan = {v: int(value(x[v]) or 0) for v in var_keys}
            utilidad_total = float(value(prob.objective) or 0)

            recursos: dict[str, RecursoInfo] = {}
            for ins in all_insumos:
                if ins in stocks:
                    usado = sum(coef_matrix.get(v, {}).get(ins, 0) * plan.get(v, 0) for v in var_keys)
                    disponible = stocks[ins]
                    recursos[ins] = RecursoInfo(
                        usado=round(usado, 3),
                        disponible=round(disponible, 3),
                        holgura=round(disponible - usado, 3),
                        utilizacion_pct=round(usado / disponible * 100, 1) if disponible > 0 else 0.0,
                        unidad_medida=units.get(ins, ""),
                    )

            # Detalle por prenda: binding insumo y motivo para las que quedan en 0
            detalle_plan: dict[str, DetallePrenda] = {}
            for v in var_keys:
                cant = plan.get(v, 0)
                min_add: float = float("inf")
                binding_ins: str | None = None
                binding_coef: float = 0.0
                for ins, coef in coef_matrix.get(v, {}).items():
                    if coef > 0 and ins in stocks:
                        usado_total = sum(
                            coef_matrix.get(w, {}).get(ins, 0) * plan.get(w, 0)
                            for w in var_keys
                        )
                        additional = (stocks[ins] - usado_total) / coef
                        if additional < min_add:
                            min_add = additional
                            binding_ins = ins
                            binding_coef = coef

                max_prod = int(max(0, min_add)) if min_add != float("inf") else 0
                ins_nombre = insumo_labels.get(binding_ins, binding_ins) if binding_ins else None
                utilidad_prenda = var_utilidad.get(v, 10_000)
                eficiencia = round(utilidad_prenda / binding_coef, 2) if binding_coef > 0 else None

                if cant > 0:
                    motivo = "Incluida en el plan óptimo"
                elif binding_ins is None:
                    motivo = "Sin insumos configurados"
                elif min_add < 1:
                    motivo = f"Stock agotado de: {ins_nombre}"
                else:
                    motivo = "Recursos asignados a prendas de mayor utilidad"

                detalle_plan[v] = DetallePrenda(
                    cantidad=cant,
                    max_producible=max_prod,
                    genero=var_generos.get(v) or None,
                    insumo_limitante_key=binding_ins,
                    insumo_limitante_nombre=ins_nombre,
                    coef_insumo_limitante=round(binding_coef, 4) if binding_coef > 0 else None,
                    eficiencia_cop_por_unidad=eficiencia,
                    motivo=motivo,
                )

            # Solo mostrar demanda donde hay pedidos activos reales
            demanda_resultado = {
                v: DemandaInfo(solicitado=plan.get(v, 0), demanda_maxima=dem)
                for v, dem in demanda_db.items()
                if dem > 0
            }

            # Advertencia si hay prendas del colegio sin receta configurada
            sin_receta = obtener_uniformes_sin_receta(params.colegio_id)
            nota_sin_receta = (
                f" ⚠ {len(sin_receta)} prenda(s) excluida(s) por falta de receta de insumos: "
                + ", ".join(sin_receta[:5])
                + ("…" if len(sin_receta) > 5 else "")
                + ". Configura sus insumos en el módulo Uniformes."
            ) if sin_receta else ""

            hay_demanda_activa = any(d > 0 for d in demanda_db.values())
            nota_dem = " Sin pedidos activos — producción bloqueada por demanda." if (demanda_db and not hay_demanda_activa) else ""
            nota_fb  = " (coeficientes de ejemplo)" if usando_fallback else ""

            return OptimizadorOutput(
                resultado=ResultadoOptimizacion(
                    estado="OPTIMAL",
                    utilidad_total=round(utilidad_total, 2),
                    plan=plan,
                    recursos=recursos,
                    demanda=demanda_resultado,
                    insumo_labels=insumo_labels,
                    detalle_plan=detalle_plan,
                    mensaje=(
                        f"Solución óptima{nota_fb}. "
                        f"Utilidad máxima: ${utilidad_total:,.0f} COP."
                        f"{nota_dem}{nota_sin_receta}"
                    ),
                    colegio_id=params.colegio_id,
                ),
                coef_matrix=coef_matrix,
                stocks=stocks,
                var_labels=var_labels,
                insumo_labels=insumo_labels,
            )

        return OptimizadorOutput(
            resultado=ResultadoOptimizacion(
                estado=estado.upper(),
                utilidad_total=0,
                plan={v: 0 for v in var_keys},
                recursos={},
                demanda={},
                mensaje=f"El solver no encontró solución óptima. Estado: {estado}.",
                colegio_id=params.colegio_id,
            ),
            coef_matrix=coef_matrix,
            stocks=stocks,
            var_labels=var_labels,
            insumo_labels=insumo_labels,
        )

    except Exception as exc:
        logger.error("Error en optimización ILP: %s", exc, exc_info=True)
        return OptimizadorOutput(
            resultado=ResultadoOptimizacion(
                estado="ERROR",
                utilidad_total=0,
                plan={},
                recursos={},
                demanda={},
                mensaje=f"Error interno al resolver el modelo: {exc}",
                colegio_id=params.colegio_id,
            ),
            coef_matrix={},
            stocks={},
            var_labels={},
            insumo_labels={},
        )
