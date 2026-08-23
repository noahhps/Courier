import os
import pathlib
import datetime
import requests
from zoneinfo import ZoneInfo
from abc import ABC, abstractmethod


class Tool(ABC): 
    ToolResult = None | None;
    
    def __init__(self, *, name: str, description: str ):
        self.name = name;
        self.description = description;

    # each tool requires a use or else it's completely useless
    @abstractmethod
    def use(self, *args, **kwargs) -> ToolResult:
        pass
    

    