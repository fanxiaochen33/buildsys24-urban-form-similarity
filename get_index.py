import os
import warnings
import json

import numpy as np
import geopandas as gpd
from pyproj import Geod
from shapely.geometry import LineString
import pandas as pd
from rasterio.features import geometry_mask

warnings.filterwarnings("ignore")
geod = Geod(ellps="WGS84")



def indecators(geometry):
    area, perimeter = geod.geometry_area_perimeter(geometry)
    area = abs(area)
    perimeter = abs(perimeter)
    cplx = perimeter / np.sqrt(np.sqrt(area))
    compactness = 4 * np.pi * area / (perimeter ** 2)

    if geometry.geom_type == 'Polygon':
        vertices = len(geometry.exterior.coords)
    elif geometry.geom_type == 'MultiPolygon':
        vertices = sum(len(p.exterior.coords) for p in geometry)
    
    bbox = geometry.minimum_rotated_rectangle
    bbox_width, bbox_length = sorted([
        geod.geometry_length(LineString([bbox.exterior.coords[0], bbox.exterior.coords[1]])),
        geod.geometry_length(LineString([bbox.exterior.coords[1], bbox.exterior.coords[2]]))
    ])
    eri = np.sqrt(geometry.area / bbox.area) * bbox.length / geometry.length

    ri = np.mean(
            np.linalg.norm(
                np.array(geometry.exterior.coords) - np.array([geometry.centroid.x, geometry.centroid.y]), 
                axis=1)
        ) ** 2 / (geometry.area + geometry.length ** 2) * 42.62

    return area, perimeter, cplx, compactness, vertices, bbox_width, bbox_length, eri, ri

def region_index(gdf):
    try:
        if not os.path.exists(worldpop_file):
            left, bottom, right, top = bounds_gdf.total_bounds
            url = base_url + f"bbox={left},{bottom},{right},{top}"
            response = requests.get(url, stream=True, timeout=100)
            response.raise_for_status()
            if save_tif:
                # with open(
                #     worldpop_file,
                # ) as f:
                #     for chunk in response.iter_content(chunk_size=chunk_size):
                #         f.write(chunk)
                pass
            tiff_data = BytesIO(response.content)
            raster = rasterio.open(tiff_data)
        else:
            print("worldpop file exists:", worldpop_file)
            raster = rasterio.open(worldpop_file)

        height, width = raster.height, raster.width
        raster_transform = raster.transform
        buildings_meta = np.zeros((height, width, buildings_index.shape[1]-1), dtype=np.float32)
        buildings_gdf["area"] = buildings_gdf["geometry"].apply(
            lambda x: abs(geod.geometry_area_perimeter(x)[0])
        )
        buildings_gdf = buildings_gdf.to_crs(raster.crs)
        buildings_meta = np.zeros((height, width))
        for _, building in tqdm(buildings_gdf.iterrows(), total=buildings_gdf.shape[0]):
            geom = [building.geometry]
            mask_ = geometry_mask(
                geom,
                transform=raster_transform,
                invert=True,
                out_shape=(height, width),
            )
            buildings_meta[mask_] += building.values[1:]   

    except:
        pass   


def main():
    cities = json.load(open("./data/bldg/cities.json"))

    for key, cities_list in cities.items():
        for i, city in enumerate(cities_list):
            folder = f"./data/bldg/{key}/"
            buildings_file = folder + f"buildings_{city}.geojson"

            if os.path.exists(buildings_file):
                print(f"{key}({i+1}/{len(cities_list)}):{city}")
                buildings_gdf = gpd.read_file(buildings_file)

            buildings_gdf[['area', 'perimeter', 'cplx', 'compactness', 'vertices', 'bbox_width', 'bbox_length', 'eri', 'ri']] = buildings_gdf['geometry'].apply(indecators).apply(pd.Series)

            buildings_index = buildings_gdf[["osmid", "area", "perimeter", "cplx", "compactness", "vertices", "bbox_width", "bbox_length", "eri", "ri"]]
            buildings_index.to_csv(folder + f"buildings_index_{city}.csv", index=False)                

if __name__ == '__main__':
    main()
