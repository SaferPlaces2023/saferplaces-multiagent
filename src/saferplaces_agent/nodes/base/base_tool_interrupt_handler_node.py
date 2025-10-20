import types
from typing_extensions import Literal

from langgraph.graph import END
from langgraph.types import Command, interrupt
from langchain_core.messages import SystemMessage, RemoveMessage

from ... import utils
from ... import names as N
from . import BaseToolInterrupt


class BaseToolInterruptHandler:
    
    def __init__(self):
        self.tool = None
        self.interrupt_data = None
        self.tool_message = None
        self.tool_interrupt = None
        self.tool_handler_node = None
        self.tool_name = None
        
    def handle(self, tool, interupt_data):
        self.tool = tool
        self.interrupt_data = interupt_data
        self.tool_message = self.interrupt_data['tool_message']
        self.tool_interrupt = self.interrupt_data['tool_interrupt']
        self.tool_handler_node = self.interrupt_data['tool_handler_node']
        self.tool_name = self.interrupt_data['tool_interrupt']['tool']
        

class BaseToolInterruptProvideArgsHandler(BaseToolInterruptHandler):
        
    def _generate_interrupt_message(self):
        args_description = '\n'.join([
            f'- {field} : {self.tool_interrupt["data"]["args_schema"][field].description}'
            for field in self.tool_interrupt['data']['args_schema'].keys()
            if field in self.tool_interrupt['data']['missing_args']
        ])        
        interrupt_message = utils.ask_llm(
            role = 'system',
            message = f"""The tool execution can't be completed for this reason:
            {self.tool_interrupt['reason']}
            Below there is a description of the required arguments:
            {args_description}
            Ask the user to provide the missing arguments for the tool execution."""
        )
        return interrupt_message
        
    def _generate_provided_args(self, response):
        provided_args = utils.ask_llm(
            role = 'system',
            message = f"""The tool execution could not be completed for this reason:
            {self.tool_interrupt['reason']}
            The user was asked to provide the missing arguments for the tool execution.
            The user replied: "{response}".
            If the user has provided valid arguments please reply with a dictionary with key the argument names and value what was specified by the user.
            If a value for an argument was not provided, then the value should be None.
            User can provide only some of the arguments.
            Reply with only the dictionary and nothing else.
            If the user asked to interrupt the tool process and exit, return None and nothing else.
            """,
            eval_output = True
        ) 
        return provided_args
              
    def handle(self, tool, interupt_data):
        super().handle(tool, interupt_data)
        
        interrupt_message = self._generate_interrupt_message()
        interruption = interrupt({
            "content": interrupt_message,
            "interrupt_type": BaseToolInterrupt.BaseToolInterruptType.PROVIDE_ARGS
        })
        response = interruption.get('response', 'User did not provide any response.')
        provided_args = self._generate_provided_args(response)
        
        if provided_args is None:
            remove_tool_message = RemoveMessage(self.tool_message.id)
            system_message = SystemMessage(content=f"User choose to exit the tool process with this response: {response}")            
            return {
                'goto': END,
                'update': { 
                    "messages": [remove_tool_message, system_message],
                    "node_params": { N.CHATBOT_UPDATE_MESSAGES: { "update_messages": [remove_tool_message, system_message] } }
                }
            }
        
        else:
            self.tool_message.tool_calls[-1]["args"].update(provided_args if provided_args is not None else dict())           
            return {
                'goto': self.tool_handler_node,
                'update': { 
                    "messages": [self.tool_message]
                }
            }      
        
        
        
class BaseToolInterruptInvalidArgsHandler(BaseToolInterruptHandler):
        
    def _generate_interrupt_message(self):
        args_description = '\n'.join([
            f"""- {field} : {self.tool_interrupt["data"]["args_schema"][field].description}
                    Invalid beacuse: {self.tool_interrupt["data"]["invalid_args"][field]}
            """
            for field in self.tool_interrupt['data']['args_schema'].keys()
            if field in self.tool_interrupt['data']['invalid_args']
        ])          
        interrupt_message = utils.ask_llm(
            role = 'system',
            message = f"""The tool execution can't be completed for this reason:
            {self.tool_interrupt['reason']}
            Below there is a description of the invalid arguments:
            {args_description}
            Ask the user to provide the valid arguments for the tool execution."""
        )
        return interrupt_message
           
    def _generate_provided_args(self, response):
        args_description = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool_message.tool_calls[-1]["args"].items() ])
        provided_args = utils.ask_llm(
            role = 'system',
            message = f"""The tool execution could not be completed for this reason:
            {self.tool_interrupt['reason']}
            Some of these arguments are invalid:
            {args_description}
            The user was asked to provide other valid arguments for the tool execution.
            The user replied: "{response}".
            If the user provided valid arguments, respond with a complete dictionary string keyed with the all the arguments they provided updated with what the user provided as a value, if any.
            Reply with only the dictionary string and nothing else.
            If the user asked to interrupt the tool process and exit, return None and nothing else.
            """,
            eval_output = True
        ) 
        return provided_args
            
    def handle(self, tool, interupt_data):
        super().handle(tool, interupt_data)
        
        interrupt_message = self._generate_interrupt_message()
        interruption = interrupt({
            "content": interrupt_message,
            "interrupt_type": BaseToolInterrupt.BaseToolInterruptType.INVALID_ARGS
        })
        response = interruption.get('response', 'User did not provide any response.')
        provided_args = self._generate_provided_args(response)
        
        if provided_args is None:
            remove_tool_message = RemoveMessage(self.tool_message.id)
            system_message = SystemMessage(content=f"User choose to exit the tool process with this response: {response}")            
            return {
                'goto': END,
                'update': { 
                    "messages": [remove_tool_message, system_message],
                    "node_params": { N.CHATBOT_UPDATE_MESSAGES: { "update_messages": [remove_tool_message, system_message] } }
                }
            }
            
        else:
            self.tool_message.tool_calls[-1]["args"].update(provided_args)  
            return {
                'goto': self.tool_handler_node,
                'update': { 
                    "messages": [self.tool_message]
                }
            }
        
                
class BaseToolInterruptArgsConfirmationHandler(BaseToolInterruptHandler):
    
    def _generate_interrupt_message(self):
        # args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool_interrupt["data"]["args"].items() ])  # DOC: OLD manner, but args are the one in function call, not all the tool args_schema
        
        # args_value = f"{self.tool_message.tool_calls[-1]['args']}"
        args_value = self.tool.args_valued #{argk: argv for argk,argv in self.tool.args_value.model_dump().items() if argv != self.tool.args_schema.model_fields[argk].default}

        interrupt_message = utils.ask_llm(
            role = 'system',
            message = f"""The tool execution can't be completed for this reason:
            {self.tool_interrupt['reason']}
            Below there is a description of the provided arguments:
            {args_value}
            Ask the user to confirm if the arguments are correct or if want to provide some updates."""
        )
        return interrupt_message
    
    def _generate_provided_args(self, response):
        # args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool_interrupt["data"]["args"].items() ])  # DOC: OLD manner, but args are the one in function call, not all the tool args_schema
        
        # args_value = f"{self.tool_message.tool_calls[-1]['args']}"
        args_value = self.tool.args_valued #{argk: argv for argk,argv in self.tool.args_value.model_dump().items() if argv != self.tool.args_schema.model_fields[argk].default}
        
        provided_args = utils.ask_llm(
            role = 'system',
            message = f"""The tool execution could not be completed for this reason:
            {self.tool_interrupt['reason']}
            The user was asked to confirm if arguments are correct or if he wanted to provide some modification.
            Below there is a list with provided arguments and their values:
            {args_value}
            The user replied: "{response}".
            If the user provided some updates respond with a complete dictionary string keyed with all the arguments they provided updated with what the user requested or provided as a value, if any.
            Reply with only the dictionary string and nothing else.
            If the user asked to interrupt the tool process and exit, return None and nothing else.
            """,
            eval_output = True
        )  
        return provided_args
    
    def _classify_args_confirmation(self, response):
        # args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool_interrupt["data"]["args"].items() ])  # DOC: OLD manner, but args are the one in function call, not all the tool args_schema
        
        # args_value = f"{self.tool_message.tool_calls[-1]['args']}"
        args_value = self.tool.args_valued #{argk: argv for argk,argv in self.tool.args_value.model_dump().items() if argv != self.tool.args_schema.model_fields[argk].default}
        
        # output_description = '\n'.join([ f'- {out_name}: {out_value}' for out_name,out_value in self.tool_interrupt["data"]["output"].items() ])
        provided_output = utils.ask_llm(
            role = 'system',
            message = f"""The tool execution could not be completed for this reason:
            {self.tool_interrupt['reason']}
            
            The tool was called with this input:
            {args_value}
            
            The user was asked to confirm if arguments are correct or if he wanted to provide some modification.
            
            The user replied: "{response}".
            
            If the user has answered affirmatively to the input arguments it respond True and nothing else.
            If the user has added details or specified changes in the input parameters, respond False and nothing else.
            If the user asked to interrupt the tool process and exit, return None and nothing else.
            """,
            eval_output = True
        )   
        return provided_output
    
    
    
    def handle(self, tool, interupt_data):
        super().handle(tool, interupt_data)
        
        interrupt_message = self._generate_interrupt_message()
        
        interruption = interrupt({
            "content": interrupt_message,
            "interrupt_type": BaseToolInterrupt.BaseToolInterruptType.CONFIRM_ARGS
        })
        response = interruption.get('response', 'User did not provide any response.')
        
        # DOC: OLD WAY
        # provided_args = self._generate_provided_args(response) 
        # if provided_args is None:
        #     remove_tool_message = RemoveMessage(self.tool_message.id)
        #     system_message = SystemMessage(content=f"User choose to exit the tool process with this response: {response}")            
        #     return {
        #         'goto': END,
        #         'update': { 
        #             "messages": [remove_tool_message, system_message],
        #             "node_params": { N.CHATBOT_UPDATE_MESSAGES: { "update_messages": [remove_tool_message, system_message] } }
        #         }
        #     }
        # else:
        #     self.tool_message.tool_calls[-1]["args"].update(provided_args)
        #     self.tool.execution_confirmed = True
        #     return {
        #         'goto': self.tool_handler_node,
        #         'update': { 
        #             "messages": [self.tool_message]
        #         }
        #     }
        # DOC: NEW WAY
        provided_args = self._classify_args_confirmation(response)
        if provided_args is True:
            self.tool.execution_confirmed = True
            return {
                'goto': self.tool_handler_node,
                'update': { "messages": [self.tool_message] }
            }
        elif provided_args is False:
            remove_tool_message = RemoveMessage(self.tool_message.id)
            system_message = SystemMessage(
                content=f"""The tool {self.tool.name} was called with this input arguments:
                {self.tool_message.tool_calls[-1]["args"]}
                User choose to update it's original request with this additional informations: {response}.
                Update the argument set by combining the existing arguments with the updates provided by the user. Do not add or remove any other information."""
            )
            return {
                'goto': END,
                'update': { 
                    "messages": [remove_tool_message, system_message],
                    "node_params": { N.CHATBOT_UPDATE_MESSAGES: { "update_messages": [remove_tool_message, system_message] } },
                }
            }
        else:
            remove_tool_message = RemoveMessage(self.tool_message.id)
            system_message = SystemMessage(content=f"User choose to exit the tool process with this response: {response}")            
            return {
                'goto': END,
                'update': { 
                    "messages": [remove_tool_message, system_message],
                    "node_params": { N.CHATBOT_UPDATE_MESSAGES: { "update_messages": [remove_tool_message, system_message] } }
                }
            }

            
class BaseToolInterruptOutputConfirmationHandler(BaseToolInterruptHandler):
    
    def _generate_interrupt_message(self):
        output_description = '\n'.join([ f'- {out_name}: {out_value}' for out_name,out_value in self.tool_interrupt["data"]["output"].items() ])
        interrupt_message = utils.ask_llm(
            role = 'system',
            message = f"""Before the completion of the tool execution, some output needs to be confirmed. In particular:
            {self.tool_interrupt['reason']}
            Below there is the provided outputs:
            {output_description}
            Show output to the user and ask him if he wants to confirm it or if he wants to modify some values."""
        )
        return interrupt_message
    
    def _classify_output_confirmation(self, response):
        # args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool_interrupt["data"]["args"].items() ])
        # args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool.args_value.items() ])
        args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool.args_valued.items()])

        output_description = '\n'.join([ f'- {out_name}: {out_value}' for out_name,out_value in self.tool_interrupt["data"]["output"].items() ])
        provided_output = utils.ask_llm(
            role = 'system',
            message = f"""Before the completion of the tool execution, some output needs to be confirmed.
            The tool was called with this input:
            {args_value}
            
            Below there is a description of the provided outputs:
            {self.tool_interrupt['reason']}
            
            The user was asked to confirm if the output or if he want to modify some values.
            Below there is the provided outputs:
            {output_description}
            
            The user replied: "{response}".
            
            If the user has answered affirmatively to the outputs produced it respond True and nothing else.
            If the user has added details or specified changes in the input parameters, respond False and nothing else.
            If the user asked to interrupt the tool process and exit, return None and nothing else.
            """,
            eval_output = True
        )   
        return provided_output
    
    def _generate_provided_output(self, response):
        # args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool_interrupt["data"]["args"].items() ])
        # args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool.args_value.items() ])
        args_value = '\n'.join([ f'- {arg}: {val}' for arg,val in self.tool.args_valued.items() ])

        update_inputs = utils.ask_llm(
            role = 'system',
            message = f"""Tool was called with this input arguments:
            {args_value}
            
            Output was:
            {self.tool_interrupt['reason']}
            
            But user provided this additional information for the execution:
            {response}
            
            Return a dictionary string with the input arguments valued with the update provide by user and nothing else.
            """,
            eval_output = True
        )
        return update_inputs
    
    def handle(self, tool, interupt_data):
        super().handle(tool, interupt_data)
        
        interrupt_message = self._generate_interrupt_message()
        interruption = interrupt({
            "content": interrupt_message,
            "interrupt_type": BaseToolInterrupt.BaseToolInterruptType.CONFIRM_OUTPUT
        })
        response = interruption.get('response', 'User did not provide any response.')
        provided_output = self._classify_output_confirmation(response)
        
        if provided_output is True:
            self.tool.output_confirmed = True
            return {
                'goto': self.tool_handler_node,
                'update': { "messages": [self.tool_message] }
            }
        
        elif provided_output is False:
            # DOC: ↓↓↓ OLD WAY: but maybe we need to get back to chatbot with the original request + updates (see [NEW WAY] below)
            # update_inputs = self._generate_provided_output(response)
            # self.tool_message.tool_calls[-1]["args"].update(update_inputs)
            # self.tool.output_confirmed = False
            # return {
            #     'goto': self.tool_handler_node,
            #     'update': { "messages": [self.tool_message] } 
            # }
            # DOC: [NEW WAY] — we need to return to the chatbot with the original request + updates
            # update_inputs = self._generate_provided_output(response) # ???: maybe non serve nemmeno
            remove_tool_message = RemoveMessage(self.tool_message.id)
            system_message = SystemMessage(content=f"User choose to update it's original request with this additional informations: {response}")
            return {
                'goto': END,
                'update': { 
                    "messages": [remove_tool_message, system_message],
                    "node_params": { N.CHATBOT_UPDATE_MESSAGES: { "update_messages": [remove_tool_message, system_message] } },
                }
            }

        else:
            remove_tool_message = RemoveMessage(self.tool_message.id)
            system_message = SystemMessage(content=f"User choose to exit the tool process with this response: {response}")            
            return {
                'goto': END,
                'update': { 
                    "messages": [remove_tool_message, system_message],
                    "node_params": { N.CHATBOT_UPDATE_MESSAGES: { "update_messages": [remove_tool_message, system_message] } }
                }
            }


class BaseToolInterruptNode:
    
    def __new__(
        cls,
        state,
        tool_handler_node_name: str,
        tool_interrupt_node_name: str,
        tools: dict,
        custom_tool_interupt_handlers: dict = dict(),
    ):
        instance = super().__new__(cls) 
        instance.__init__(
            state,
            tool_handler_node_name,
            tool_interrupt_node_name,
            tools,
            custom_tool_interupt_handlers
        )
        return instance.setup()
    
    
    tool_interupt_handlers = {
        BaseToolInterrupt.BaseToolInterruptType.PROVIDE_ARGS: BaseToolInterruptProvideArgsHandler(),
        BaseToolInterrupt.BaseToolInterruptType.INVALID_ARGS: BaseToolInterruptInvalidArgsHandler(),
        BaseToolInterrupt.BaseToolInterruptType.CONFIRM_ARGS: BaseToolInterruptArgsConfirmationHandler(),
        BaseToolInterrupt.BaseToolInterruptType.CONFIRM_OUTPUT: BaseToolInterruptOutputConfirmationHandler(),
    }
    
    
    def __init__( 
            self,
            state,
            tool_handler_node_name: str,
            tool_interrupt_node_name: str,
            tools: dict,
            custom_tool_interupt_handlers: dict = dict(),
    ):
        self.state = state
        self.state_type = type(state)
        self.tool_handler_node_name = tool_handler_node_name
        self.tool_interrupt_node_name = tool_interrupt_node_name
        self.tools = tools
        self.tool_interupt_handlers.update(custom_tool_interupt_handlers)   # DOC: Dict Key is BaseToolInterruptType and Value is the handler class
        
    def setup(self):
        
        # DOC: This is a template function that will be used to create the tool interrupt node function.
        def tool_interrupt_node_template(state):            
            interrupt_data = state['node_params'][self.tool_interrupt_node_name]
            tool_interrupt = interrupt_data['tool_interrupt']
            tool_name = interrupt_data['tool_interrupt']['tool']
            
            tool = self.tools[tool_name]
            
            # DOC: OP.1 — i.e. BaseToolInterruptProvideArgsHandler.handle() -> _generate_interrupt_message > _generate_provided_args > _update_tool_message > return Command(goto=tool_handler_node, update={'messages' [tool_message]}
            # DOC: OP.2 — i.e. A generic class with handle method that return {'goto': node-name, 'update': state}
            
            command = self.tool_interupt_handlers[tool_interrupt['type']].handle(tool, interrupt_data)
            
            next_node = command['goto']
            update_state = command['update']
            
            return Command(goto=next_node, update=update_state)

        # DOC: Creating the tool interrupt node function using the template function.
        tool_interrupt = types.FunctionType(
            tool_interrupt_node_template.__code__,
            globals(),
            name = self.tool_interrupt_node_name,
            argdefs = tool_interrupt_node_template.__defaults__,
            closure = tool_interrupt_node_template.__closure__
        )
        
        tool_interrupt.__annotations__ = {
            'state': type(self.state),
            'return': Command[Literal[END, self.tool_handler_node_name]]
        }
        
        return tool_interrupt