import pdb
import numpy as np
import cv2
import json
from shapely.geometry import Polygon


def convert_to_wkt_coordinate(contour, slide_height, ratio):
    contour = contour * ratio
    contour[:, 1] = slide_height - contour[:, 1]
    return contour


def check_clockwise(coor_list):
    coor_list.append(coor_list[0])
    _area = 0
    for i in range(len(coor_list)-1):
        _area += (coor_list[i][0]*coor_list[i+1][1] -
                  coor_list[i+1][0]*coor_list[i][1])
    if _area > 0:
        return 1
    elif _area < 0:
        return -1
    else:
        return 0


def generate_wkt_from_openapi(openapi_output, slide_height):
    wkt_list = []
    min_area = 0
    if openapi_output["summary"]["score"] == "Benign":
        return wkt_list
    contour_list = json.loads(openapi_output["heatmap"]["contours"]) #DeepDx-HTTP-API 서비스에서 contour 객체의 contour field type = string
    
    for _contours in contour_list:
        contours = _contours['contour']
        pattern = _contours['label']
        rotate_list = []
        annotation = []
        for contour in contours:
            clock = check_clockwise(contour)
            if clock == 0:
                continue
            elif clock == 1:
                annotation.append(contour)
            elif clock == -1:
                annotation.insert(0, contour)
            else:
                print('Error')
            rotate_list.append(clock)
        
        if rotate_list.count(-1) > 1:
            print('Multipolygon Exists!')
            pdb.set_trace()
            continue
        elif rotate_list.count(-1) == 0:
            print('String Exists!')
            continue

        poly = Polygon(
            shell = convert_to_wkt_coordinate(np.array(annotation[0]), slide_height, 1),
            holes = [convert_to_wkt_coordinate(np.array(an), slide_height, 1) for an in annotation[1:]])
        
        wkt_list.append((poly, pattern))        
    return wkt_list




