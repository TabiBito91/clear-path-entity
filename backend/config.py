from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str
    database_url: str = "sqlite+aiosqlite:///./clearpath.db"
    frontend_url: str = "http://localhost:3000"
    ca_sos_api_key: str = ""

    model_config = {"env_file": "../.env"}


settings = Settings()
