import requests
import os
import logging
import config
from urllib.parse import urlencode

def create_route_map(points, style="default", map_type="map", size="650,450", zoom=11):
    if len(points) < 2:
        raise ValueError("Для построения маршрута нужно как минимум 2 точки")
    
    styles = {
        "default": "pm2rdl",  
        "blue": "pm2blm",    
        "green": "pm2grl",   
        "orange": "pm2orgl",  
        "yellow": "pm2ywl"   
    }
    
    marker_style = styles.get(style, "pm2rdl")
    
    markers = []
    for i, point in enumerate(points):
        lat, lon = point[0], point[1]
        if i == 0:
            point_style = "pm2gnl" 
        elif i == len(points) - 1:
            point_style = "pm2rdl"  
        else:
            point_style = marker_style
        markers.append(f"{lon},{lat},{point_style}")
    
    markers_str = "~".join(markers)
    
    route_points = []
    for point in points:
        lat, lon = point[0], point[1]
        route_points.append(f"{lon},{lat}")
    
    route_str = ",".join(route_points)
    
    center_lon = sum(point[1] for point in points) / len(points)
    center_lat = sum(point[0] for point in points) / len(points)
    
    params = {
        "ll": f"{center_lon},{center_lat}",
        "z": str(zoom),
        "size": size,
        "l": map_type,
        "pt": markers_str,
        "pl": route_str,  
        "lang": "ru_RU"
    }
    
    url = f"https://static-maps.yandex.ru/1.x/?{urlencode(params)}"
    
    return url

def create_route_with_custom_style(places, start_location, route_color="0000FF", route_width=5, markers_style="default"):
    points = []
    if isinstance(start_location, dict):
        start_lat, start_lon = start_location['lat'], start_location['lon']
    else:
        if(geocode_to_coordinates(start_location)):
            start_location = geocode_to_coordinates(start_location)
            start_lat, start_lon = start_location['lat'], start_location['lon']
        else:
            start_lat, start_lon = 56.3287, 44.0020
    
    points.append([float(start_lat), float(start_lon)])
    
    for place in places:
        if 'lat' in place and 'lon' in place:
            points.append([float(place['lat']), float(place['lon'])])
    
    if len(points) < 2:
        raise ValueError("Для построения маршрута нужно как минимум 2 точки")
    
    styles = {
        "default": "pm2rdl",
        "blue": "pm2blm",
        "green": "pm2grl",
        "orange": "pm2orgl",
        "yellow": "pm2ywl"
    }
    
    marker_style = styles.get(markers_style, "pm2rdl")
    
    markers = []
    for i, point in enumerate(points):
        lat, lon = point[0], point[1]
        if i == 0:
            point_style = "pm2gnl"  
        elif i == len(points) - 1:
            point_style = "pm2rdl"  
        else:
            point_style = marker_style
        markers.append(f"{lon},{lat},{point_style}")
    
    markers_str = "~".join(markers)
    
    route_points = ",".join([f"{point[1]},{point[0]}" for point in points])
    route_style = f"c:{route_color},w:{route_width}"
    route_param = f"{route_style},{route_points}"
    
    min_lat = min(p[0] for p in points)
    max_lat = max(p[0] for p in points)
    min_lon = min(p[1] for p in points)
    max_lon = max(p[1] for p in points)
    pad_lat = max(0.002, (max_lat - min_lat) * 0.08)
    pad_lon = max(0.002, (max_lon - min_lon) * 0.08)
    bbox_left = min_lon - pad_lon
    bbox_bottom = min_lat - pad_lat
    bbox_right = max_lon + pad_lon
    bbox_top = max_lat + pad_lat

    params = {
        "bbox": f"{bbox_left},{bbox_bottom}~{bbox_right},{bbox_top}",
        "size": "650,450",
        "l": "map",
        "pt": markers_str,
        "pl": route_param,
        "lang": "ru_RU"
    }
    
    url = f"https://static-maps.yandex.ru/1.x/?{urlencode(params)}"
    
    return url

    
def geocode_location(location_name, api_key=None, lang="ru_RU"):    
    if not api_key:
        api_key = (getattr(config, 'YANDEX_GEOCODER_API_KEY', '').strip())
    if not api_key:
        return {"error": "API ключ обязателен для геокодирования"}
    
    base_url = "https://geocode-maps.yandex.ru/1.x/"
    
    params = {
        "apikey": api_key,
        "geocode": location_name,
        "format": "json",
        "lang": lang
    }

    text_lower = (location_name or "").lower()
    mentions_city = any(k in text_lower for k in ["нижний новгород", "нижегород", "nizhny novgorod", "nn "])
    coords_like = False
    try:
        import re as _re
        coords_like = bool(_re.search(r"[-\d\.]+\s*,\s*[-\d\.]+", location_name))
    except Exception:
        pass

    if not mentions_city and not coords_like:
        nn_bbox = {
            "left": 43.6,
            "bottom": 56.10,
            "right": 44.20,
            "top": 56.45,
        }
        params.update({
            "bbox": f"{nn_bbox['left']},{nn_bbox['bottom']}~{nn_bbox['right']},{nn_bbox['top']}",
            "rspn": 1  
        })
    
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        
        data = response.json()
        
        features = data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember", [])
        
        if not features:
            return {"error": "Место не найдено"}
        
        first_feature = features[0]
        geo_object = first_feature.get("GeoObject", {})
        point = geo_object.get("Point", {})
        pos = point.get("pos", "")
        
        if not pos:
            return {"error": "Координаты не найдены"}
        
        lon, lat = map(float, pos.split())
        
        address = geo_object.get("metaDataProperty", {}).get("GeocoderMetaData", {}).get("text", "")
        
        return {
            "lat": lat,
            "lon": lon,
            "address": address,
            "full_info": geo_object  
        }
        
    except requests.exceptions.RequestException as e:
        return {"error": f"Ошибка сети: {e}"}
    except Exception as e:
        return {"error": f"Ошибка обработки: {e}"}

def geocode_to_coordinates(location_name, api_key=None):
    result = geocode_location(location_name, api_key)
    if "error" in result:
        return None
    return {'lat': result["lat"], "lon": result["lon"]}
