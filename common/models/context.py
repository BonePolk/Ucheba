from pydantic import BaseModel, ConfigDict, Field


class ContextTaskUpdate(BaseModel):
    """Только разрешённое переименование. None оставляет название владельца."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    title: str | None = Field(min_length=1, max_length=300)
