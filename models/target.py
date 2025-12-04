from pydantic import BaseModel, Field

class Target(BaseModel):
    key: str = Field(..., description="Unique identifier for the target site")
    url: str = Field(..., description="URL to scrape")
    base_url: str = Field(..., description="Base URL for resolving relative links")
    list_selector: str = Field(..., description="CSS selector for the list of notices")
    title_selector: str = Field(..., description="CSS selector for the title within a list item")
    link_selector: str = Field(..., description="CSS selector for the link within a list item")
    content_selector: str = Field(..., description="CSS selector for the content area in detail page")
