import os
import datetime
from dateutil import relativedelta
from enum import Enum

from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)

from ...common import utils
from ...common import names as N
from ...nodes.base import BaseAgentTool



# DOC: This is a demo tool to retrieve weather data.
class DemoWeatherTool(BaseAgentTool):
    
    
    # DOC: Tool input schema
    class InputSchema(BaseModel):
        
        area: None | str | list[float] = Field(
            title = "Area",
            description = """The area of interest for the weather data. If not specified use None as default.
            It could be a bouning-box defined by [min_x, min_y, max_x, max_y] coordinates provided in EPSG:4326 Coordinate Reference System.
            Otherwise it can be the name of a country, continent, or specific geographic area.""",
            examples=[
                None,
                "Italy",
                "Paris",
                "Continental Spain",
                "Alps",
                [12, 52, 14, 53],
                [-5.5, 35.2, 5.58, 45.10],
            ]
        )
        date: None | str = Field(
            title = "Date",
            description = f"The date of the meteo data provided in UTC-0 YYYY-MM-DD. If not specified use {(datetime.datetime.now() + relativedelta.relativedelta(days=1)).strftime('%Y-%m-%d')} as default.",
            examples = [
                None,
                "2025-01-01",
                "2025-02-01",
                "2025-03-10",
            ],
            default = None
        )
        
    
    # DOC: Initialize the tool with a name, description and args_schema
    def __init__(self, **kwargs):
        super().__init__(
            name = N.DEMO_WEATHER_TOOL,
            description = """Useful when user want to get meteo information for a specific area and time period.""",
            args_schema = DemoWeatherTool.InputSchema,
            **kwargs
        )
        self.output_confirmed = True    # INFO: There is already the execution_confirmed:True
        
    
    # DOC: Validation rules ( i.e.: valid init and lead time ... ) 
    def _set_args_validation_rules(self) -> dict:
        
        return {
            'area': [
                lambda **ka: f"Invalid area coordinates: {ka['area']}. It should be a list of 4 float values representing the bounding box [min_x, min_y, max_x, max_y]." 
                    if isinstance(ka['area'], list) and len(ka['area']) != 4 else None  
            ],
            'date': [
                lambda **ka: f"Invalid initialization time: {ka['date']}. It should be in the format YYYY-MM-DD."
                    if ka['date'] is not None and utils.try_default(lambda: datetime.datetime.strptime(ka['date'], "%Y-%m-%d"), None) is None else None,
                lambda **ka: f"Invalid initialization time: {ka['date']}. It should be in the future."
                    if ka['date'] is not None and datetime.datetime.strptime(ka['date'], '%Y-%m-%d') < datetime.datetime.now() else None
            ]
        }
        
    
    # DOC: Inference rules ( i.e.: from location name to bbox ... )
    def _set_args_inference_rules(self) -> dict:
        
        def infer_area(**ka):
            def bounding_box_from_location_name(area):
                if type(area) is str:
                    area = utils.ask_llm(
                        role = 'system',
                        message = f"""Please provide the bounding box coordinates for the area: {area} with format [min_x, min_y, max_x, max_y] in EPSG:4326 Coordinate Reference System. 
                        Provide only the coordinates list without any additional text or explanation.""",
                        eval_output = True
                    )
                    self.execution_confirmed = False
                return area
            def round_bounding_box(area):
                if type(area) is list:
                    precision = 1
                    area = [
                        utils.floor_decimals(area[0], precision),
                        utils.floor_decimals(area[1], precision),
                        utils.ceil_decimals(area[2], precision),
                        utils.ceil_decimals(area[3], precision)
                    ]
                return area
            area = bounding_box_from_location_name(ka['area'])
            area = round_bounding_box(area)
            return area
        
        def infer_date(**ka):
            if ka['date'] is None:
                return (datetime.datetime.now() + relativedelta.relativedelta(days=1)).strftime('%Y-%m-%d')
            return ka['date']
        
        
        return {
            'area': infer_area,
            'date': infer_date,
        }
        
    
    # DOC: Execute the tool → Build notebook, write it to a file and return the path to the notebook and the zarr output file
    def _execute(
        self,
        area: str | list[float],
        date: str,
    ): 
        # DOC: this is a dummy execution and provided output
        weather_descr = f'It will rain {sum(area) / 4} mm in {area} on {date}'
        
        return {
            "weather_description": weather_descr,
        }
        
    
    # DOC: Back to a consisent state
    def _on_tool_end(self):
        self.execution_confirmed = False
        self.output_confirmed = True
        
    
    # DOC: Try running AgentTool → Will check required, validity and inference over arguments thatn call and return _execute()
    def _run(
        self, 
        area: str | list[float],
        date: str = None,
        run_manager: None | Optional[CallbackManagerForToolRun] = None
    ) -> dict:
        
        return super()._run(
            tool_args = {
                "area": area,
                "date": date,
            },
            run_manager=run_manager,
        )