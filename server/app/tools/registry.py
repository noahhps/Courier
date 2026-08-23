from .tool import Tool;


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {};
        
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
        
        
    def register(self, tool: Tool) -> str:
        if tool.name in self._tools:
            return f"failed to add {tool.name} to registry";
        else:
            self._tools[tool.name] = tool;
            return f"successfully added {tool.name} to registry";
        
        
    def delete(self, name: str) -> str:
        if name not in self._tools:
            return f"Failed: {name} is not registered."

        del self._tools[name]
        return f"Successfully deleted {name}."