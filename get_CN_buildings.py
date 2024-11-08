import os
import argparse
import subprocess
import warnings
import json

import numpy as np
import pandas as pd
import geopandas as gpd
import folium
import osmnx as ox
import rasterio
from rasterio.mask import mask
from shapely.geometry import Polygon
from pyproj import Geod
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
scaler = StandardScaler()
geod = Geod(ellps="WGS84")


def get_gdf_region(city):
    """
    Get Regional GeoDataFrame
    """
    os.makedirs(f"./data/data_{city}", exist_ok=True)

    assert os.path.exists(
        f"./data/data_{city}/region.geojson"
    ), "Request data from the author"

    gdf_region = gpd.read_file(f"./data/data_{city}/region.geojson")
    print("gdf_region nums:", gdf_region.shape)

    return gdf_region


def get_footprint_from_osmnx(gdf_region):
    """

    Input:
        gdf_region

    Output:
        gdf_building
    """
    tags = {"building": True}
    bounds = gdf_region.total_bounds
    bbox = [bounds[3], bounds[1], bounds[2], bounds[0]]
    gdf_building = ox.features.features_from_bbox(bbox=bbox, tags=tags)
    gdf_building = gdf_building[gdf_building["building"].notnull()]
    print("gdf_building nums:", gdf_building.shape)

    gdf_building = gdf_building[["building", "geometry"]]
    gdf_building = gdf_building.to_crs("EPSG:4326")

    def get_building_type(x):
        if x.geom_type in {"Polygon", "MultiPolygon"}:
            return 1
        return 0

    gdf_building["type"] = gdf_building["geometry"].apply(get_building_type)
    gdf_building = gdf_building[gdf_building["type"] == 1]
    gdf_building = gdf_building.drop("type", axis=1)
    gdf_building = gdf_building.reset_index()

    gdf_building.to_file(f"./data/data_{args.city}/buildings.geojson", driver="GeoJSON")

    return gdf_building


def download_height_tifs(regions):
    """
    CNBH10m tifs

    Input:
        regions

    Output:
        tifs

    File:
        CNBH10m_X{X}Y{Y}.tif
    """
    regions_bounds = regions.total_bounds
    regions_bounds = [
        regions_bounds[0] - 0.5,
        regions_bounds[1] - 0.5,
        regions_bounds[2] + 0.5,
        regions_bounds[3] + 0.5,
    ]
    bounds_int = [int(x) + 1 if int(x) % 2 == 0 else int(x) for x in regions_bounds]
    tifs = np.meshgrid(
        np.arange(bounds_int[0], bounds_int[2] + 1, 2),
        np.arange(bounds_int[1], bounds_int[3] + 1, 2),
    )
    for X, Y in zip(tifs[0].flatten(), tifs[1].flatten()):
        url = (
            f"https://zenodo.org/records/7923866/files/CNBH10m_X{X}Y{Y}.tif?download=1"
        )
        file = f"./data/data_CNBH/CNBH10m_X{X}Y{Y}.tif"
        if not os.path.exists(file):
            subprocess.run(["wget", url, "-O", file], check=True)
    print(f"Downloaded all {len(tifs[0].flatten())} tifs!")

    return tifs


def visualize_region(gdf_region, result_gdf):
    """
    Visualization

    Input:
        gdf_region
        result_gdf
    """
    m = folium.Map(
        location=[
            gdf_region["geometry"][0].centroid.y,
            gdf_region["geometry"][0].centroid.x,
        ],
        zoom_start=10,
    )
    folium.GeoJson(gdf_region, name="geojson").add_to(m)
    folium.GeoJson(
        result_gdf,
        name="geojson",
        style_function=lambda x: {
            "fillColor": "red",
            "color": "red",
            "weight": 2,
        },
    ).add_to(m)
    m.save(f"./data/data_{args.city}/visual.html")


def get_CN_building(gdf_region):
    """
    Regional Buildings Features
    """
    gdf_building = get_footprint_from_osmnx(gdf_region)
    tifs = download_height_tifs(gdf_region)
    gdfs = []
    for X, Y in zip(tifs[0].flatten(), tifs[1].flatten()):
        print(f"loading CNBH10m_X{X}Y{Y}.tif")
        chbn = rasterio.open(f"./data/data_CNBH/CNBH10m_X{X}Y{Y}.tif")
        chbn_polygon = Polygon.from_bounds(*(chbn.bounds))
        footprints = gdf_building.to_crs(chbn.crs)
        gdf = footprints[footprints["geometry"].intersects(chbn_polygon)]
        gdf["height"] = gdf["geometry"].apply(
            lambda x: np.max(np.nan_to_num(mask(chbn, [x], crop=True)[0]))
        )
        gdf["height"] = gdf["height"].astype(float)
        gdfs.append(gdf)
    gdf = pd.concat(gdfs)
    gdf = gdf[gdf["height"] > 0]
    gdf = gdf.to_crs("EPSG:4326")
    gdf = gpd.sjoin(
        gdf, gdf_region[["GEOID", "geometry"]], predicate="within", how="inner"
    )

    visualize_region(gdf_region, gdf)

    result_gdf = gdf[["height", "GEOID", "geometry"]]

    print("building nums =", result_gdf.shape[0])

    return result_gdf


def get_pop(gdf_region):
    """
    Get worldpop data
    """
    if not os.path.exists("./data/data_worldpop"):
        os.mkdir("./data/data_worldpop")
        world_pop_dir = "./data/data_worldpop/chn_ppp_2020_UNadj.tif"
        url = "https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/CHN/chn_ppp_2020_UNadj.tif"
        subprocess.run(["wget", url, "-O", world_pop_dir], check=True)
    world_pop = rasterio.open("./data/data_worldpop/chn_ppp_2020_UNadj.tif")

    def pop(x):
        """
        get regional population data
        """
        data = mask(world_pop, [x], crop=True)[0]
        return data[data > 0].sum()

    gdf_region["pop_overall"] = gdf_region["geometry"].apply(pop)
    return gdf_region


def get_building_feature(gdf_region, result_gdf):
    """
    regional feature to gdf_region
    """

    def calculate_ERI(polygon):
        polygon_area = polygon.area
        min_rect = polygon.minimum_rotated_rectangle
        rect_area = min_rect.area
        scale_factor = polygon_area / rect_area

        min_rect = polygon.minimum_rotated_rectangle
        ear_perimeter = scale_factor * min_rect.length
        polygon_perimeter = polygon.length

        ERI = ear_perimeter / polygon_perimeter
        return ERI

    result_gdf["complexity"] = result_gdf["geometry"].apply(calculate_ERI)
    result_gdf["area"] = result_gdf["geometry"].apply(
        lambda x: abs(geod.geometry_area_perimeter(x)[0])
    )
    result_gdf["volume"] = result_gdf["area"] * result_gdf["height"]
    result_gdf_agg = (
        result_gdf.groupby("GEOID")
        .agg(
            {
                "area": ["mean", "sum"],
                "height": "mean",
                "volume": "sum",
                "complexity": "mean",
            }
        )
        .reset_index()
    )
    result_gdf_agg.columns = ["_".join(col) for col in result_gdf_agg.columns.values]
    result_gdf_agg = result_gdf_agg.rename(columns={"GEOID_": "GEOID"})
    gdf_region = gdf_region.merge(result_gdf_agg, on="GEOID", how="left")
    gdf_region = gdf_region.fillna(0)
    gdf_region["building_density"] = gdf_region["area_sum"] / gdf_region["ALAND"]
    gdf_region["plot_ratio"] = gdf_region["volume_sum"] / gdf_region["ALAND"]
    return gdf_region


def dump_region2info(gdf_region):
    """
    save data
    """
    gdf_region_normal = gdf_region[
        [
            "GEOID",
            "ALAND",
            "pop_overall",
            "area_mean",
            "height_mean",
            "complexity_mean",
            "building_density",
            "plot_ratio",
        ]
    ]
    gdf_region_normal.iloc[:, 1:] = scaler.fit_transform(gdf_region_normal.iloc[:, 1:])

    region2info = {
        gdf_region["GEOID"].iloc[i]: {
            "ALAND": int(gdf_region["ALAND"].iloc[i]),
            "pop_overall": int(gdf_region["pop_overall"].iloc[i]),
            "area_mean": gdf_region["area_mean"].iloc[i],
            "height_mean": gdf_region["height_mean"].iloc[i],
            "complexity_mean": gdf_region["complexity_mean"].iloc[i],
            "building_density": gdf_region["building_density"].iloc[i],
            "plot_ratio": gdf_region["plot_ratio"].iloc[i],
            "feature": gdf_region_normal.iloc[i, 1:].values.tolist(),
        }
        for i in range(gdf_region_normal.shape[0])
    }

    with open(f"./data/data_{args.city}/region2info_building.json", "w") as f:
        json.dump(region2info, f)

    print("region2info_building.json saved!")


def main(city):
    # Read the GeoDataFrame of the region
    gdf_region = get_gdf_region(city)

    # Obtain the population data of the region
    gdf_region = get_pop(gdf_region)

    # Obtain the building data of the region
    result_gdf = get_CN_building(gdf_region)  # including visualization

    # Calculate the building density and floor area ratio of the region
    gdf_region = get_building_feature(gdf_region, result_gdf)

    # save data
    dump_region2info(gdf_region)

    return gdf_region


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", type=str, default="bj", choices=["bj", "jn", "sz"])
    args = parser.parse_args()

    gdf_region = main(args.city)
