import numpy as np

import geopandas as gpd

# import leafmap
# import leafmap.maplibregl as leafmap

from ..common import utils, s3_utils


class LeafmapProviders():
    
    MapLibreGL = 'MapLibreGL'
    MapLibreGL3D = 'MapLibreGL3D'
    
    __valid_providers__ = [
        MapLibreGL,
        MapLibreGL3D
    ]
    
    def init_map(provider: str = MapLibreGL):
        
        if provider not in LeafmapProviders.__valid_providers__:
            raise ValueError(f'Provider {provider} is not supported. Valid providers are {LeafmapProviders.__valid_providers__}')
        
        if provider == LeafmapProviders.MapLibreGL:
            import leafmap.maplibregl as leafmap
            m = leafmap.Map()
            return m
        
        elif provider == LeafmapProviders.MapLibreGL3D:
            import leafmap.maplibregl as leafmap
            m = leafmap.Map(
                style=leafmap.maptiler_3d_style(
                    style='dataviz', 
                    exaggeration=2, 
                    tile_size=256
                )
            )
            m.add_overture_3d_buildings()
            return m

class LeafmapInterface():
    
    def __init__(self, provider: str = LeafmapProviders.MapLibreGL):
        self.m = LeafmapProviders.init_map(provider)
        self.registred_layers = []  # DOC: Only src by noww ..
        
        
    def add_layer(self, src, layer_type, **kwargs):
        
        if src in self.registred_layers:
            return            
        
        if layer_type == 'vector':
            self.add_vector_layer(src, **kwargs)
        
        elif layer_type == 'raster':
            self.add_raster_layer(src, **kwargs)
        
        else:
            raise ValueError(f'Layer type {layer_type} is not supported. Valid layer types are ["vector", "raster"]')
        
        self.registred_layers.append(src)
        return True
        
        
    def add_vector_layer(self, src, **kwargs):
        """Add a vector layer to the map."""
        
        src = utils.s3uri_to_https(src)
        name = kwargs.pop('title', utils.juststem(src))
        
        # DOC: when using vector layers in MapLibreGL they needs to be in EPSG:4326
        gdf = gpd.read_file(src)
        if gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        
        self.m.add_gdf(
            gdf = gdf,
            name = name
        )
        
        
    def add_raster_layer(self, src, **kwargs):
        """Add a raster layer to the map."""
        
        src_cog = utils.tif_to_cog3857(src)
        
        src_cog = utils.s3uri_to_https(src_cog)
        name = kwargs.pop('title', utils.juststem(src_cog))
        colormap = kwargs.pop('colormap_name', 'blues')
        nodata = kwargs.pop('nodata', -9999)
        
        self.m.add_cog_layer(
            url = src_cog,
            name = name,
            colormap_name = colormap,
            nodata = nodata,
        )
        
    
    def add_3d_buildings(self):
        self.m.add_overture_3d_buildings()
        
        