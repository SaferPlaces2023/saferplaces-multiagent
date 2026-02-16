import json

from ..common.states import MABaseGraphState
from ..common.utils import _base_llm


class Prompts:

    general_context = (
        "You are an AI assistant for SaferPlaces, "
        "specialized in geospatial analysis and risk management.\n"
        "You can interact with Saferplaces and Safercast APIs, perform operations on geospatial layers, "
        "manage files in S3 buckets, and assist users with project and layer management.\n"
        "You support tools for weather data, digital twins, and geospatial operations, "
        "and help users visualize, edit, and export geospatial data in a web application."
    )
    
    present_request = lambda parsed_request: (
        f"User request: {parsed_request['raw_text']}\n"
        f"Intent: {parsed_request['intent']}\n"
        f"Entities: {parsed_request['entities']}"
    )

    classify_next = (
        "You are an AI assistant for SaferPlaces, based on the user request and context. "
        "Your task is to classify the next action or intent of the user.\n"
        "Classify to this next intents: \n"
        "1. SaferplacesAgent\n"
        "2. SafercastAgent\n"
        "3. DirectResponse\n"
        "4. WeatherForecastAgent\n"
        "Return a JSON object with the following structure:\n"
        "{\n"
        "  \"next_intent\": <next_intent>,\n"
        "}\n"
        "Return only the JSON object without any additional text."
    )
    
    

class SupervisorAgent():
    
    def __init__(self):
        self.name = 'SupervisorAgent'
    
    
    @staticmethod
    def __call__(state: MABaseGraphState) -> MABaseGraphState:
        return SupervisorAgent.run(state)
    
    @staticmethod
    def run(state: MABaseGraphState) -> MABaseGraphState:
        
        parsed_request = state["parsed_request"]
        
        if parsed_request["intent"] == "unknown":
            state['intent_supervisor'] = parsed
            return state
        
        system_prompt = Prompts.general_context
        response = _base_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": Prompts.present_request(parsed_request)},
            {"role": "system", "content": Prompts.classify_next}
        ])

        try:
            parsed = json.loads(response.content)
            print(parsed)
        except json.JSONDecodeError:
            parsed = {
                "next_intent": "unknown",
                "entities": [],
                "raw_text": parsed_request["raw_text"]
            }
            state['intent_supervisor'] = parsed
            return state
            
        
        if parsed["next_intent"] == "unknown":
            state['intent_supervisor'] = parsed
            return state
        
        if parsed["next_intent"] not in (
            "SaferplacesAgent",
            "SafercastAgent",
            "DirectResponse",
            "WeatherForecastAgent"
        ):
            # Handle unknown intent > go to final_chat_agent but with different "intent updates"
            state['intent_supervisor'] = parsed
            return state
        
        if parsed["next_intent"] == "SaferplacesAgent":
            # Handle SaferplacesAgent intent
            state['intent_supervisor'] = parsed
            return state
        if parsed["next_intent"] == "SafercastAgent":
            # Handle SafercastAgent intent
            state['intent_supervisor'] = parsed
            return state
        if parsed["next_intent"] == "DirectResponse":
            # Handle DirectResponse intent
            state['intent_supervisor'] = parsed
            return state
        if parsed["next_intent"] == "WeatherForecastAgent":
            # Handle WeatherForecastAgent intent
            state['intent_supervisor'] = parsed
            return state
        
        return state