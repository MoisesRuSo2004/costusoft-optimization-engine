from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/inventario_db"
    PORT: int = 8002
    API_SECRET_TOKEN: str = "mi-token-secreto-interno-cambiar-en-produccion"
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
