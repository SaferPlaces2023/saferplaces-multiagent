from enum import Enum    
from typing import Optional
    

# DOC: ToolInterrupt is a custom exception class that is used to handle interruptions in the tool execution process.
    
class BaseToolInterrupt(Exception):
    
    
    # DOC: BaseToolInterruptType is an enumeration that defines the types of interruptions that can occur during tool execution.
    class BaseToolInterruptType():
        PROVIDE_ARGS = "PROVIDE_ARGS"
        INVALID_ARGS = "INVALID_ARGS"
        CONFIRM_ARGS = "CONFIRM_ARGS"
        CONFIRM_OUTPUT = "CONFIRM_OUTPUT"
        
    # DOC: When an instance of ToolInterrupt is created, it needs the caller Tool object, interrupt-type, interrupt-reason and optional interrupt-data based on interrupt-type
    def __init__(self, interrupt_tool: str, interrupt_type: BaseToolInterruptType, interrupt_reason: str, interrupt_data: Optional[dict] = dict(), state_updates: Optional[dict] = dict()):
        super().__init__(interrupt_reason)
        self.tool = interrupt_tool
        self.type = interrupt_type
        self.reason = interrupt_reason
        self.data = interrupt_data
        self.state_updates = state_updates
    
    @property
    def message(self):
        return self.reason
    
    @property
    def as_dict(self):
        return {
            "tool": self.tool,
            "type": self.type,
            "reason": self.reason,
            "data": self.data,
            "state_updates": self.state_updates
        }