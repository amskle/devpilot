from devpilot.api.schemas.common import ApiModel


class DirectoryEntry(ApiModel):
    name: str
    path: str


class RepositoryDirectoryResponse(ApiModel):
    path: str | None
    parent: str | None
    items: list[DirectoryEntry]
