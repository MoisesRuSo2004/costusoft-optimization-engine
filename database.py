import json
import logging

import pandas as pd
from sqlalchemy import create_engine, text

from config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=settings.ENVIRONMENT == "development",
)


def init_db() -> None:
    """Crea la tabla historial_optimizacion si no existe."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS historial_optimizacion (
                id               BIGSERIAL PRIMARY KEY,
                fecha_ejecucion  TIMESTAMP NOT NULL DEFAULT NOW(),
                estado_solucion  VARCHAR(30) NOT NULL,
                utilidad_total   NUMERIC(15, 2),
                talla            VARCHAR(10) NOT NULL DEFAULT 'M',
                x1_pantalon_diario INTEGER DEFAULT 0,
                x2_camisa_diario   INTEGER DEFAULT 0,
                x3_pantalon_ef     INTEGER DEFAULT 0,
                x4_sueter_ef       INTEGER DEFAULT 0,
                stocks_usados      JSONB,
                parametros_entrada JSONB,
                grafica_html        TEXT,
                grafica_region_html TEXT,
                ejecutado_por       VARCHAR(50),
                mensaje             TEXT,
                created_at          TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        # Migraciones no destructivas: agrega columnas si la tabla ya existía sin ellas
        conn.execute(text("""
            ALTER TABLE historial_optimizacion
            ADD COLUMN IF NOT EXISTS grafica_region_html TEXT
        """))
        conn.execute(text("""
            ALTER TABLE historial_optimizacion
            ADD COLUMN IF NOT EXISTS x5_sueter_diario INTEGER DEFAULT 0
        """))
        conn.commit()
    logger.info("Tabla historial_optimizacion lista")


def obtener_coeficientes() -> pd.DataFrame:
    """
    Retorna los coeficientes de insumo por prenda desde uniforme_insumos.
    Agrega sobre TODAS las tallas usando el promedio de cantidad_base, de modo
    que el modelo ILP (que opera a nivel prenda, no talla) reciba una receta
    completa independientemente de qué tallas estén configuradas.
    Columnas: prenda, tipo, insumo_nombre, cantidad_base, stock_actual, insumo_id
    """
    query = text("""
        SELECT
            u.prenda,
            u.tipo,
            LOWER(i.nombre)       AS insumo_nombre,
            AVG(ui.cantidad_base) AS cantidad_base,
            i.stock               AS stock_actual,
            i.unidad_medida       AS unidad_medida,
            i.id                  AS insumo_id
        FROM uniforme_insumos ui
        JOIN uniformes u ON ui.uniforme_id = u.id
        JOIN insumos   i ON ui.insumo_id   = i.id
        GROUP BY u.prenda, u.tipo, i.nombre, i.stock, i.unidad_medida, i.id
        ORDER BY u.prenda, i.nombre
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def obtener_stocks_insumos() -> pd.DataFrame:
    """Lee todos los insumos con su stock actual para usar como fallback de stocks."""
    query = text("""
        SELECT id, nombre, LOWER(nombre) AS nombre_lower, stock, stock_minimo, unidad_medida
        FROM insumos
        ORDER BY nombre
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def obtener_demanda_uniforme() -> pd.DataFrame:
    """
    Suma la demanda de pedidos activos (BORRADOR → EN_PRODUCCION) por prenda/tipo,
    agregada sobre TODAS las tallas. El modelo ILP opera a nivel prenda (no talla),
    por lo que la restricción de demanda debe reflejar el total comprometido con clientes.
    """
    query = text("""
        SELECT
            u.prenda,
            u.tipo,
            COALESCE(SUM(dp.cantidad), 0) AS cantidad_demandada
        FROM uniformes u
        LEFT JOIN detalle_pedidos dp ON dp.uniforme_id = u.id
        LEFT JOIN pedidos p ON dp.pedido_id = p.id
            AND p.estado IN ('BORRADOR', 'CALCULADO', 'CONFIRMADO', 'EN_PRODUCCION')
        GROUP BY u.prenda, u.tipo
        ORDER BY u.prenda
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def guardar_historial(resultado: dict) -> int | None:
    """Persiste el resultado de una ejecución en historial_optimizacion."""
    plan = resultado.get("plan") or {}
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()

    query = text("""
        INSERT INTO historial_optimizacion (
            fecha_ejecucion, estado_solucion, utilidad_total, talla,
            x1_pantalon_diario, x2_camisa_diario, x3_pantalon_ef, x4_sueter_ef, x5_sueter_diario,
            stocks_usados, parametros_entrada, grafica_html, grafica_region_html, ejecutado_por, mensaje
        ) VALUES (
            NOW(), :estado, :utilidad, :talla,
            :x1, :x2, :x3, :x4, :x5,
            CAST(:stocks AS jsonb), CAST(:params AS jsonb), :grafica, :grafica_region, :usuario, :mensaje
        ) RETURNING id
    """)
    recursos = resultado.get("recursos") or {}
    recursos_serial = {
        k: (v.model_dump() if hasattr(v, "model_dump") else v)
        for k, v in recursos.items()
    }

    with engine.connect() as conn:
        row = conn.execute(query, {
            "estado":   resultado.get("estado", "ERROR"),
            "utilidad": resultado.get("utilidad_total"),
            "talla":    "ALL",   # el modelo agrega todas las tallas
            "x1": plan.get("pantalon_diario", 0),
            "x2": plan.get("camisa_diario", 0),
            "x3": plan.get("pantalon_ef", 0),
            "x4": plan.get("sueter_ef", 0),
            "x5": plan.get("sueter_diario", 0),
            "stocks":  json.dumps(recursos_serial),
            "params":  json.dumps(resultado.get("parametros", {})),
            "grafica": resultado.get("grafica_html"),
            "grafica_region": resultado.get("grafica_region_html"),
            "usuario": resultado.get("ejecutado_por"),
            "mensaje": resultado.get("mensaje"),
        }).fetchone()
        conn.commit()
        return row[0] if row else None


def obtener_historial(limit: int = 20) -> pd.DataFrame:
    query = text("""
        SELECT id, fecha_ejecucion, estado_solucion, utilidad_total, talla,
               x1_pantalon_diario, x2_camisa_diario, x3_pantalon_ef, x4_sueter_ef,
               COALESCE(x5_sueter_diario, 0) AS x5_sueter_diario,
               mensaje, created_at
        FROM historial_optimizacion
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"limit": limit})


def obtener_historial_por_id(record_id: int) -> dict | None:
    query = text("""
        SELECT id, fecha_ejecucion, estado_solucion, utilidad_total, talla,
               x1_pantalon_diario, x2_camisa_diario, x3_pantalon_ef, x4_sueter_ef,
               COALESCE(x5_sueter_diario, 0) AS x5_sueter_diario,
               stocks_usados, parametros_entrada, grafica_html, grafica_region_html, mensaje, created_at
        FROM historial_optimizacion
        WHERE id = :id
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"id": record_id}).fetchone()
        return dict(row._mapping) if row else None


def obtener_historial_por_id_para_pdf(record_id: int) -> dict | None:
    """
    Query mínima para generación de PDF: solo datos numéricos y texto.
    Excluye grafica_html y grafica_region_html (varios MB innecesarios)
    para no consumir RAM extra en Render free tier (512 MB).
    """
    query = text("""
        SELECT id, fecha_ejecucion, estado_solucion, utilidad_total,
               x1_pantalon_diario, x2_camisa_diario, x3_pantalon_ef, x4_sueter_ef,
               COALESCE(x5_sueter_diario, 0) AS x5_sueter_diario,
               stocks_usados, mensaje, created_at,
               grafica_region_html
        FROM historial_optimizacion
        WHERE id = :id
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"id": record_id}).fetchone()
        return dict(row._mapping) if row else None
