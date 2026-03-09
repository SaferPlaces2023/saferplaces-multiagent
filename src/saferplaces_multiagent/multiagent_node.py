import pandas as pd
from .common.states import MABaseGraphState

class MultiAgentNode():

    def __init__(self, name: str, log_state: bool = True):
        self.name = name
        self.log_state = log_state

    def _pre_run(self, state: MABaseGraphState) -> MABaseGraphState:
        if self.log_state:
            self._compile_log_state_filename(state)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        self._pre_run(state)
        state = self.run(state)
        self._post_run(state)
        return state
    
    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        raise NotImplementedError("Subclasses should implement this method.")

    def _post_run(self, state: MABaseGraphState) -> MABaseGraphState:
        if self.log_state:
            self._write_log_state(state.copy())

    def _compile_log_state_filename(self, state: MABaseGraphState) -> str:
        self._log_state_filename = f'__state_log__user_id={state["user_id"]}__project_id={state["project_id"]}.json'

    def _write_log_state(self, state: MABaseGraphState):
        
        def is_empty(value):
            if value is None:
                return True
            if isinstance(value, (list, dict, str)) and len(value) == 0:
                return True
            return False

        state['messages'] = [
            {
                mk: getattr(m, mk)
                for mk in ['id', 'content', 'type', 'tool_calls']
                if hasattr(m, mk) and not is_empty(getattr(m, mk))
            }
            for m in state.get('messages', []) 
        ]
        state = {
            '__node_name__': self.name,
            
            ** {
                sk:sv for sk, sv in state.items()
                if not is_empty(sv)
            } 
        }
        state_record = pd.DataFrame([state])
        state_record['__node_name__'] = [self.name]
        state_record.to_json(self._log_state_filename, orient='records', lines=True, mode='a')